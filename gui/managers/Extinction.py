"""Keeping the Brillouin extinction at its best: measure, search, watch.

Ported from the bench program that came with the BIPD filter
(Controle_BIPD/Interface.py), without Qt so it can be tested on its own:

- border_intensity(): the figure of merit -- the mean level of a band of
  ``margin`` pixels along the edges of the camera frame, where the light the
  filter should extinguish shows up. Lower is better.
- search_lc_minimum(): the local search over the liquid-crystal voltage. One
  step each way to find the downhill direction, then steps in that direction
  for as long as the level keeps falling, then back to the best voltage seen.
  The direction that worked is returned, and is tried first next time.
- ExtinctionGuard: the same search run during a map, between two points, when
  the level has grown past a multiple of the one measured at the first point.
  The point is then acquired again, at the corrected extinction.

Everything that touches hardware comes in as a callable, so the search runs as
well on a simulated filter and camera as on the real ones.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable

import numpy as np


def border_intensity(image, margin_px: int) -> float:
    """Mean level of the frame's border band, ``margin_px`` wide.

    The band is clipped to leave at least a pixel inside; with no band left
    (a margin of 0, or a frame too small) the mean of the whole frame is used.
    """
    img = np.asarray(image)
    if img.ndim != 2 or img.size == 0:
        raise ValueError(f"border_intensity needs a 2D image, got shape {img.shape}")

    h, w = img.shape
    margin = int(max(0, margin_px))
    margin_x = min(margin, w // 2 - 1)
    margin_y = min(margin, h // 2 - 1)
    if margin_x <= 0 or margin_y <= 0:
        return float(np.mean(img, dtype=np.float64))

    total = np.sum(img, dtype=np.float64)
    inner = np.sum(img[margin_y:h - margin_y, margin_x:w - margin_x], dtype=np.float64)
    inner_count = (h - 2 * margin_y) * (w - 2 * margin_x)
    border_count = img.size - inner_count
    if border_count <= 0:
        return float(np.mean(img, dtype=np.float64))
    return float((total - inner) / border_count)


@dataclass
class LcSearchResult:
    start_mv: float
    best_mv: float
    start_level: float
    best_level: float
    direction_mv: float          # signed step to try first next time
    moves: int                   # voltages tried
    stopped: bool = False        # interrupted (Stop, closing)
    trials: list = field(default_factory=list)   # [(mv, level), ...]

    @property
    def improved(self) -> bool:
        return self.best_level < self.start_level

    def as_dict(self) -> dict:
        return {
            "start_mv": float(self.start_mv),
            "best_mv": float(self.best_mv),
            "start_level": float(self.start_level),
            "best_level": float(self.best_level),
            "direction_mv": float(self.direction_mv),
            "moves": int(self.moves),
            "stopped": bool(self.stopped),
            "improved": bool(self.improved),
        }


def search_lc_minimum(
    set_level: Callable[[float], float],
    settle: Callable[[], None],
    measure: Callable[[], float],
    start_mv: float,
    start_level: float,
    step_mv: float = 5.0,
    max_steps: int = 30,
    lc_min_mv: float = 0.0,
    lc_max_mv: float = 4500.0,
    should_stop: Callable[[], bool] | None = None,
) -> LcSearchResult:
    """Walk the liquid-crystal voltage down to the nearest minimum of the level.

    ``set_level(mv)`` sends a voltage and returns the one actually sent (it may
    clamp). ``settle()`` waits for the liquid crystal to follow -- the vendor
    asks for 200 ms at least. ``measure()`` acquires a frame and returns its
    level. ``step_mv`` is signed: its sign is the direction tried first.

    On return the filter is always left at the best voltage seen, including
    when ``should_stop`` interrupted the search.
    """
    step = float(step_mv) or 5.0
    stop = should_stop or (lambda: False)

    best_mv = float(start_mv)
    best_level = float(start_level)
    current = best_mv
    moves = 0
    trials = [(best_mv, best_level)]

    def at_limit(direction: float, level_mv: float) -> bool:
        return (direction > 0 and level_mv >= lc_max_mv) or (direction < 0 and level_mv <= lc_min_mv)

    def try_level(target: float) -> float:
        nonlocal current, moves
        current = float(set_level(target))
        moves += 1
        settle()
        level = float(measure())
        trials.append((current, level))
        return level

    def finish(stopped: bool) -> LcSearchResult:
        if current != best_mv:
            set_level(best_mv)
            settle()
        return LcSearchResult(
            start_mv=float(start_mv), best_mv=best_mv,
            start_level=float(start_level), best_level=best_level,
            direction_mv=step, moves=moves, stopped=stopped, trials=trials,
        )

    # Which way is downhill? The direction that worked last time first.
    if stop():
        return finish(True)
    level = try_level(best_mv + step)
    if level < best_level:
        best_mv, best_level = current, level
    else:
        step = -step
        if stop():
            return finish(True)
        level = try_level(best_mv + step)
        if level < best_level:
            best_mv, best_level = current, level
        else:
            return finish(False)     # already at the minimum

    # Downhill for as long as it keeps going down.
    improvements = 0
    while improvements < int(max_steps):
        if stop():
            return finish(True)
        if at_limit(step, current):
            break
        level = try_level(current + step)
        if level < best_level:
            best_mv, best_level = current, level
            improvements += 1
        else:
            break

    return finish(False)


@dataclass
class AutoLcSettings:
    """How the extinction is watched and corrected. Defaults as on the bench."""
    margin_px: int = 20
    step_mv: float = 5.0
    settle_s: float = 0.5
    max_steps: int = 30
    threshold: float = 3.0          # correct when level > threshold x reference
    check_every: float = 60.0
    check_mode: str = "seconds"     # "seconds" or "points"

    @classmethod
    def from_dict(cls, values: dict | None) -> "AutoLcSettings":
        values = dict(values or {})
        out = cls()
        for name in ("margin_px", "max_steps"):
            if name in values:
                setattr(out, name, int(values[name]))
        for name in ("step_mv", "settle_s", "threshold", "check_every"):
            if name in values:
                setattr(out, name, float(values[name]))
        if values.get("check_mode") in ("seconds", "points"):
            out.check_mode = str(values["check_mode"])
        return out

    def as_dict(self) -> dict:
        return {
            "margin_px": int(self.margin_px),
            "step_mv": float(self.step_mv),
            "settle_s": float(self.settle_s),
            "max_steps": int(self.max_steps),
            "threshold": float(self.threshold),
            "check_every": float(self.check_every),
            "check_mode": str(self.check_mode),
        }


def interruptible_sleep(seconds: float, should_stop: Callable[[], bool] | None = None) -> None:
    end = time.perf_counter() + max(0.0, float(seconds))
    while time.perf_counter() < end:
        if should_stop is not None and should_stop():
            return
        time.sleep(min(0.02, max(0.0, end - time.perf_counter())))


class ExtinctionGuard:
    """Re-optimise the liquid crystal during a map when the extinction drifts.

    Called by the mapping worker after each Brillouin frame, in its thread. The
    first frame sets the reference level. Then, at the chosen period (seconds or
    points), the level is compared with ``threshold`` x reference; past it, the
    voltage is searched again and the point is acquired anew.

    ``enabled=False`` still records the filter state beside the map, and the
    level of the first point: the dataset then says how it was taken.
    """

    def __init__(
        self,
        bipd,
        settings: AutoLcSettings,
        enabled: bool = True,
        direction_mv: float | None = None,
        on_direction: Callable[[float], None] | None = None,
        on_state: Callable[[], None] | None = None,
        on_status: Callable[[str], None] | None = None,
        on_log: Callable[[str], None] | None = None,
        clock: Callable[[], float] = time.perf_counter,
        sleep: Callable[[float, Callable[[], bool] | None], None] = interruptible_sleep,
    ):
        self.bipd = bipd
        self.settings = settings
        self.enabled = bool(enabled)
        self._direction = float(direction_mv) if direction_mv else abs(float(settings.step_mv))
        self._on_direction = on_direction
        self._on_state = on_state
        #: Public: the mapping worker points it at its status line.
        self.on_status = on_status
        self._on_log = on_log
        self._clock = clock
        self._sleep = sleep

        self.reference_level = None
        self.points = 0
        self._last_check_t = None
        self.corrections = []
        self.correction_time_s = 0.0
        self._t0 = clock()
        self._state_at_start = self._safe_state()

    def _safe_state(self) -> dict:
        try:
            return dict(self.bipd.state())
        except Exception as e:
            return {"error": str(e)}

    def _due(self, now: float) -> bool:
        period = max(0.0, float(self.settings.check_every))
        if self.settings.check_mode == "points":
            n = max(1, int(round(period)))
            return self.points % n == 0
        return self._last_check_t is None or (now - self._last_check_t) >= period

    def check(self, image, reacquire: Callable[[], object],
              should_stop: Callable[[], bool] | None = None, point_index: int | None = None):
        """Return the frame to keep for this point: ``image``, or a new one."""
        level = border_intensity(image, self.settings.margin_px)
        now = self._clock()
        self.points += 1

        if self.reference_level is None:
            self.reference_level = level
            self._last_check_t = now
            self._log(f"[BIPD] map reference: border level {level:.2f} at the first point")
            return image

        if not self.enabled or not self._due(now):
            return image
        self._last_check_t = now

        limit = float(self.settings.threshold) * float(self.reference_level)
        if level <= limit:
            return image

        stop = should_stop or (lambda: False)
        if self.on_status is not None:
            self.on_status("Extinction drifted: re-optimising the liquid crystal...")
        t_start = self._clock()
        settings = self.settings

        def set_level(mv: float) -> float:
            sent = self.bipd.set_lc_level_mv(mv)
            if self._on_state is not None:
                self._on_state()
            return sent

        def measure() -> float:
            return border_intensity(reacquire(), settings.margin_px)

        result = search_lc_minimum(
            set_level=set_level,
            settle=lambda: self._sleep(settings.settle_s, stop),
            measure=measure,
            start_mv=self.bipd.lc_level_mv(),
            start_level=level,
            step_mv=self._direction,
            max_steps=settings.max_steps,
            lc_min_mv=self.bipd.LC_MIN_MV,
            lc_max_mv=self.bipd.LC_MAX_MV,
            should_stop=stop,
        )
        self._direction = result.direction_mv
        if self._on_direction is not None:
            self._on_direction(self._direction)

        new_image = reacquire()
        new_level = border_intensity(new_image, settings.margin_px)
        duration = self._clock() - t_start
        self.correction_time_s += duration
        self._last_check_t = self._clock()

        record = {
            "point": point_index,
            "time_s": round(t_start - self._t0, 3),
            "level_before": round(level, 3),
            "level_after": round(new_level, 3),
            "lc_before_mv": round(result.start_mv, 2),
            "lc_after_mv": round(result.best_mv, 2),
            "moves": result.moves,
            "duration_s": round(duration, 3),
        }
        self.corrections.append(record)
        self._log(
            f"[BIPD] extinction re-optimised at point {point_index}: border "
            f"{level:.2f} -> {new_level:.2f} (reference {self.reference_level:.2f}, "
            f"limit {limit:.2f}), LC {result.start_mv:.1f} -> {result.best_mv:.1f} mV "
            f"in {result.moves} moves, {duration:.1f} s"
        )
        return new_image

    def _log(self, message: str) -> None:
        if self._on_log is not None:
            self._on_log(message)

    def record(self) -> dict:
        """What goes beside the map: filter state, settings, corrections."""
        return {
            "filter": self._describe(),
            "state_at_start": self._state_at_start,
            "state_at_end": self._safe_state(),
            "auto_lc_enabled": bool(self.enabled),
            "auto_lc": self.settings.as_dict(),
            "reference_border_level": self.reference_level,
            "corrections": list(self.corrections),
            "correction_time_s": round(self.correction_time_s, 3),
        }

    def _describe(self) -> dict:
        try:
            return dict(self.bipd.describe())
        except Exception as e:
            return {"error": str(e)}
