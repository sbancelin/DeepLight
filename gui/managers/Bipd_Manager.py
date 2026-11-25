"""The BIPD filter, driven without ever blocking the window.

Every exchange with the filter is a serial transaction, and an optimisation
waits on camera exposures and on the liquid crystal settling. All of it runs in
one background thread, in the order it was asked for: a burst of clicks on ">"
reaches the filter as that burst, never reordered, and the window keeps
answering. The state shown in the panel is read there too, every few seconds,
and only when nothing else is queued -- a status poll on the GUI thread is what
made the Elliptec positioners stutter.

During a map the filter is also driven from the mapping thread, through an
ExtinctionGuard built here; the filter's own lock keeps the two apart.
"""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor

from PySide6.QtCore import QObject, QTimer, Signal, Slot

from ..widgets.Log_Widget import logger
from .Extinction import (
    AutoLcSettings, ExtinctionGuard, border_intensity, interruptible_sleep, search_lc_minimum,
)


class BipdManager(QObject):
    sigConnectionChanged = Signal(bool, str)     # connected, what / why not
    sigState = Signal(dict)                      # BipdFilterBase.state()
    sigBusy = Signal(bool)                       # an optimisation is running
    sigOptimizationDone = Signal(dict)           # LcSearchResult.as_dict()
    sigFrame = Signal(object)                    # frames taken while optimising
    sigLevel = Signal(float)                     # their border level
    sigError = Signal(str)

    POLL_INTERVAL_MS = 2000
    ROTATOR_TIMEOUT_S = 60.0

    def __init__(self, hardware_manager, frame_source, parent=None):
        """``frame_source(params)`` returns one Brillouin frame (real or simulated)."""
        super().__init__(parent)
        self.hardware = hardware_manager
        self._frame_source = frame_source
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="bipd")
        self._pending = 0
        self._pending_lock = threading.Lock()
        self._cancel = threading.Event()
        self._optimizing = False
        self._closing = False
        #: Signed step the last successful search went in; tried first next time.
        self._direction_mv = 5.0

        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(self.POLL_INTERVAL_MS)
        self._poll_timer.timeout.connect(self._poll)
        # Emitted from the command thread, delivered here in the GUI thread,
        # where the timer lives and may be started or stopped.
        self.sigConnectionChanged.connect(self._on_connection_changed)

    # ------------------------------------------------------------------
    # plumbing
    # ------------------------------------------------------------------
    def _filter(self):
        getter = getattr(self.hardware, "bipd_filter", None)
        bipd = getter() if callable(getter) else None
        if bipd is None:
            reason_fn = getattr(self.hardware, "bipd_unavailable_reason", None)
            reason = reason_fn() if callable(reason_fn) else None
            raise RuntimeError("BIPD filter unavailable" + (f": {reason}" if reason else ""))
        return bipd

    def _connected_filter(self):
        bipd = self._filter()
        if not bipd.connected:
            raise RuntimeError("BIPD filter is not connected.")
        return bipd

    def is_connected(self) -> bool:
        try:
            return bool(self._filter().connected)
        except Exception:
            return False

    def is_optimizing(self) -> bool:
        return bool(self._optimizing)

    def _submit(self, label: str, fn, *args) -> bool:
        if self._closing:
            return False
        with self._pending_lock:
            self._pending += 1
        self._executor.submit(self._run, label, fn, *args)
        return True

    def _run(self, label: str, fn, *args):
        try:
            fn(*args)
        except Exception as e:
            logger.error(f"[BIPD] {label} failed: {e}")
            self.sigError.emit(f"{label} failed: {e}")
        finally:
            with self._pending_lock:
                self._pending -= 1

    def _emit_state(self, bipd=None):
        try:
            bipd = bipd or self._connected_filter()
            self.sigState.emit(bipd.state())
        except Exception as e:
            logger.debug(f"[BIPD] state not read: {e}")

    # ------------------------------------------------------------------
    # connection
    # ------------------------------------------------------------------
    @Slot(bool)
    def set_connected(self, connected: bool):
        if connected:
            self._submit("connect", self._connect_job)
        else:
            self.cancel_optimization()
            self._poll_timer.stop()
            self._submit("disconnect", self._disconnect_job)

    def _connect_job(self):
        try:
            bipd = self._filter()
            bipd.connect()
        except Exception as e:
            self.sigConnectionChanged.emit(False, str(e))
            raise
        description = dict(bipd.describe() or {})
        text = description.get("kind", "BIPD")
        if description.get("port"):
            text += f" ({description['port']})"
        self.sigConnectionChanged.emit(True, text)
        self._emit_state(bipd)

    def _disconnect_job(self):
        try:
            self._filter().disconnect()
        finally:
            self.sigConnectionChanged.emit(False, "disconnected")

    @Slot(bool, str)
    def _on_connection_changed(self, connected: bool, _text: str):
        if connected and not self._closing:
            self._poll_timer.start()
        else:
            self._poll_timer.stop()

    @Slot()
    def _poll(self):
        # Only when idle: a poll queued behind an optimisation would report a
        # state already stale by the time it runs.
        with self._pending_lock:
            idle = self._pending == 0
        if idle and self.is_connected():
            self._submit("status", self._emit_state)

    # ------------------------------------------------------------------
    # commands from the panel
    # ------------------------------------------------------------------
    @Slot(float)
    def set_lc_level(self, level_mv: float):
        self._submit("liquid crystal", self._lc_job, float(level_mv))

    def _lc_job(self, level_mv: float):
        bipd = self._connected_filter()
        bipd.set_lc_level_mv(level_mv)
        self._emit_state(bipd)

    @Slot(float)
    def move_rotator(self, angle_deg: float):
        self._submit("rotator", self._rotator_job, float(angle_deg), False)

    @Slot()
    def home_rotator(self):
        self._submit("rotator home", self._rotator_job, 0.0, True)

    def _rotator_job(self, angle_deg: float, home: bool):
        bipd = self._connected_filter()
        if home:
            bipd.home_rotator()
        else:
            bipd.move_rotator_deg(angle_deg)
        # The move returns before the rotator has arrived: wait here, in this
        # thread, so the angle shown afterwards is the one it reached.
        interruptible_sleep(0.05)
        waited = 0.0
        while bipd.rotator_busy() and waited < self.ROTATOR_TIMEOUT_S and not self._closing:
            interruptible_sleep(0.1)
            waited += 0.1
        self._emit_state(bipd)

    @Slot(bool)
    def set_heater(self, enabled: bool):
        self._submit("heater", self._heater_job, bool(enabled))

    def _heater_job(self, enabled: bool):
        bipd = self._connected_filter()
        bipd.set_heater_enabled(enabled)
        logger.info(f"[BIPD] heater {'on' if enabled else 'off'}")
        self._emit_state(bipd)

    # ------------------------------------------------------------------
    # optimisation on demand
    # ------------------------------------------------------------------
    def optimize(self, camera_params: dict, settings: dict) -> bool:
        """Search the liquid-crystal voltage for the lowest border level.

        Frames are taken with ``camera_params`` (those of the Spectro tab). The
        caller stops the live view first and restarts it on sigBusy(False).
        Returns False, and does nothing, if the search cannot start.
        """
        if self._optimizing or self._closing:
            return False
        if not self.is_connected():
            self.sigError.emit("Connect the BIPD filter first.")
            return False
        self._cancel.clear()
        self._optimizing = True
        self.sigBusy.emit(True)
        if not self._submit("optimisation", self._optimize_job, dict(camera_params or {}),
                            AutoLcSettings.from_dict(settings)):
            self._optimizing = False
            self.sigBusy.emit(False)
            return False
        return True

    @Slot()
    def cancel_optimization(self):
        self._cancel.set()

    def _optimize_job(self, camera_params: dict, settings: AutoLcSettings):
        try:
            bipd = self._connected_filter()

            def measure() -> float:
                frame = self._frame_source(camera_params)
                if frame is None:
                    raise RuntimeError("no Brillouin frame")
                self.sigFrame.emit(frame)
                level = border_intensity(frame, settings.margin_px)
                self.sigLevel.emit(level)
                return level

            def set_level(mv: float) -> float:
                sent = bipd.set_lc_level_mv(mv)
                self._emit_state(bipd)
                return sent

            step = abs(settings.step_mv) * (1.0 if self._direction_mv >= 0 else -1.0)
            start_level = measure()
            result = search_lc_minimum(
                set_level=set_level,
                settle=lambda: interruptible_sleep(settings.settle_s, self._cancel.is_set),
                measure=measure,
                start_mv=bipd.lc_level_mv(),
                start_level=start_level,
                step_mv=step,
                max_steps=settings.max_steps,
                lc_min_mv=bipd.LC_MIN_MV,
                lc_max_mv=bipd.LC_MAX_MV,
                should_stop=self._cancel.is_set,
            )
            self._direction_mv = result.direction_mv
            self._emit_state(bipd)

            outcome = "stopped" if result.stopped else ("done" if result.improved else "already at the minimum")
            logger.notice(
                f"[BIPD] LC optimisation {outcome}: {result.start_mv:.1f} -> {result.best_mv:.1f} mV, "
                f"border {result.start_level:.2f} -> {result.best_level:.2f} ({result.moves} moves)"
            )
            self.sigOptimizationDone.emit(result.as_dict())
        finally:
            self._optimizing = False
            self.sigBusy.emit(False)

    # ------------------------------------------------------------------
    # during a map
    # ------------------------------------------------------------------
    def make_map_guard(self, settings: dict, enabled: bool):
        """An ExtinctionGuard for the next map, or None if no filter is connected.

        Built whenever the filter is connected, even with the automatic
        correction off: the map then still records the filter's state.
        """
        if not self.is_connected():
            return None
        self.cancel_optimization()
        bipd = self._filter()

        def remember_direction(mv: float):
            self._direction_mv = float(mv)

        return ExtinctionGuard(
            bipd,
            AutoLcSettings.from_dict(settings),
            enabled=bool(enabled),
            direction_mv=self._direction_mv,
            on_direction=remember_direction,
            on_state=lambda: self._emit_state(bipd),
            on_log=logger.notice,
        )

    # ------------------------------------------------------------------
    # closing
    # ------------------------------------------------------------------
    def shutdown(self):
        """Stop polling, cut an optimisation short, and wait for the thread.

        The filter itself is closed by HardwareManager.close(), with the rest.
        """
        self._closing = True
        self._poll_timer.stop()
        self._cancel.set()
        self._executor.shutdown(wait=True, cancel_futures=True)
