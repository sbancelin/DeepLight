from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional
import math
import time

from PySide6.QtCore import QObject, Signal, Slot, QTimer
from ..widgets.Log_Widget import logger


@dataclass
class AxisState:
    abs_pos: float = 0.0
    zero_offset: float = 0.0
    target_abs: Optional[float] = None
    speed: float = 0.0
    moving: bool = False

    # limites ABSOLUES device / sécurité
    min_pos: float = -1000.0
    max_pos: float = 1000.0

    max_speed: float = 1.0
    tolerance: float = 0.1

    # +1 = repère logique identique au hardware
    # -1 = repère logique inversé par rapport au hardware
    direction: float = 1.0


class PositionerManager(QObject):
    """Common API of the positioner managers."""
    absPositionChanged = Signal(str, float)   # axis, abs
    relPositionChanged = Signal(str, float)   # axis, rel
    movingChanged = Signal(str, bool)         # axis, moving

    SCAN_AXIS_MAP = {
        "X-Stage": "x",
        "Y-Stage": "y",
        "Z-Vcoil": "z",
        "Polarization": "p",
    }

    # Vitesse (fixe) des lames Elliptec ELL14 pour les moves de scan polar.
    _POLAR_SPEED_DEG_S = 430.0

    def __init__(self, axes: list[str], parent=None):
        super().__init__(parent)
        self._axes = axes
        self._state: Dict[str, AxisState] = {a: AxisState() for a in axes}
        # Positions relatives des lames avant un scan de polarisation, pour les
        # restaurer au retour à la base (cf. _handle_polarization_scan).
        self._polar_prescan: Optional[dict] = None

    def _handle_polarization_scan(self, target_rel: float, reason: str):
        """
        One 'Polarization' scan point = one AZIMUTH (°).

        The calibration table gives the positions of BOTH waveplates
        (λ/2 on axis 'p', λ/4 on axis 'p4') that produce this polarisation,
        and both are moved. The state of the waveplates before the scan is
        remembered so it can be restored on 'return_to_base'.
        """
        from .Polarization_Table import get_polarization_table

        r = str(reason or "")

        # Retour à la base : restaurer les positions pré-scan des deux lames.
        if r.endswith("return_to_base"):
            pre = self._polar_prescan
            self._polar_prescan = None
            if pre is not None:
                for ax in ("p", "p4"):
                    if ax in self._state and pre.get(ax) is not None:
                        self.move_to_rel(ax, float(pre[ax]), self._POLAR_SPEED_DEG_S)
            return

        # Première commande du run : mémoriser l'état courant des lames.
        if self._polar_prescan is None:
            self._polar_prescan = {}
            for ax in ("p", "p4"):
                try:
                    self._polar_prescan[ax] = (
                        float(self.get_rel_pos(ax)) if ax in self._state else None
                    )
                except Exception:
                    self._polar_prescan[ax] = None

        l2_deg, l4_deg = get_polarization_table().lookup(float(target_rel))
        if "p" in self._state:
            self.move_to_rel("p", float(l2_deg), self._POLAR_SPEED_DEG_S)
        if "p4" in self._state:
            self.move_to_rel("p4", float(l4_deg), self._POLAR_SPEED_DEG_S)

    def set_ums_scaling_factor(self, factor: float):
        """
        No-op default. Implementations that talk to a Scientifica XY controller
        override this to forward the new factor.
        """
        return
    
    def stop_all(self):
        """Stop the motion of every axis."""
        for axis in self._axes:
            self.stop(axis)
    
    @property
    def axes(self) -> list[str]:
        """The axes this positioner drives, in the order it was built with."""
        return list(self._axes)

    def axis_from_scan_name(self, axis_name: str) -> str | None:
        return self.SCAN_AXIS_MAP.get(axis_name)

    def has_axis(self, axis: str) -> bool:
        return axis in self._state

    def _require_axis(self, axis: str) -> None:
        if axis not in self._state:
            raise KeyError(f"Unknown axis={axis!r}. Known={list(self._state.keys())}")
    
    def get_zero_offset(self, axis: str) -> float:
        return float(self._state[axis].zero_offset)

    def set_zero_offset(self, axis: str, offset: float):
        """
        Set an axis's zero_offset (its relative frame) directly.

        Used for the mounting offset of the waveplates: the positioner's
        relative 0° corresponds to this physical angle. Unlike set_zero, which
        captures the current position, the value is imposed as given.
        """
        self._require_axis(axis)
        self._state[axis].zero_offset = float(offset)
        self._emit_positions(axis)

    def get_tolerance(self, axis: str) -> float:
        return float(self._state[axis].tolerance)

    def get_limits(self, axis: str) -> tuple[float, float]:
        st = self._state[axis]
        return float(st.min_pos), float(st.max_pos)

    def get_max_speed(self, axis: str) -> float:
        return float(self._state[axis].max_speed)

    def get_abs_pos(self, axis: str) -> float:
        return float(self._state[axis].abs_pos)

    def set_axis_direction(self, axis: str, direction: float):
        self._require_axis(axis)
        self._state[axis].direction = -1.0 if float(direction) < 0 else 1.0
        self._emit_positions(axis)

    def get_axis_direction(self, axis: str) -> float:
        self._require_axis(axis)
        return float(self._state[axis].direction)

    def rel_to_abs(self, axis: str, rel_target: float) -> float:
        self._require_axis(axis)
        st = self._state[axis]
        return float(st.zero_offset) + float(rel_target)

    def abs_to_rel(self, axis: str, abs_pos: float) -> float:
        self._require_axis(axis)
        st = self._state[axis]
        return float(abs_pos) - float(st.zero_offset)

    def rel_delta_to_abs_delta(self, axis: str, rel_delta: float) -> float:
        self._require_axis(axis)
        return float(rel_delta)
    
    def get_rel_pos(self, axis: str) -> float:
        return self.abs_to_rel(axis, self.get_abs_pos(axis))

    def _emit_positions(self, axis: str):
        self.absPositionChanged.emit(axis, self.get_abs_pos(axis))
        self.relPositionChanged.emit(axis, self.get_rel_pos(axis))
    
    def _validate_move(self, axis: str, target_abs: float, speed: float) -> bool:
        st = self._state[axis]

        if not math.isfinite(float(target_abs)):
            logger.warning(f"Invalid target position for axis {axis}: {target_abs}")
            return False

        if float(target_abs) < float(st.min_pos) or float(target_abs) > float(st.max_pos):
            logger.warning(
                f"Absolute position {target_abs} out of range for axis {axis} "
                f"(device range: {st.min_pos} .. {st.max_pos})."
            )
            return False

        if float(speed) < 0:
            logger.warning(f"Invalid negative speed for axis {axis}: {speed}")
            return False

        if float(speed) > float(st.max_speed):
            logger.warning(f"Speed {speed} too high for axis {axis}.")
            return False

        return True
    
    def set_limits(self, axis: str, min_pos: float, max_pos: float, max_speed: float, tolerance: float):
        """Update the limits of a given axis."""
        st = self._state[axis]
        st.min_pos = min_pos
        st.max_pos = max_pos
        st.max_speed = max_speed
        st.tolerance = tolerance

    def is_abs_target_allowed(self, axis: str, target_abs: float) -> bool:
        self._require_axis(axis)
        st = self._state[axis]
        return float(st.min_pos) <= float(target_abs) <= float(st.max_pos)

    def is_rel_target_allowed(self, axis: str, target_rel: float) -> bool:
        self._require_axis(axis)
        target_abs = self.rel_to_abs(axis, target_rel)
        return self.is_abs_target_allowed(axis, target_abs)

    def ensure_target_in_range(self, axis: str, target_abs: float):
        if not self.is_abs_target_allowed(axis, target_abs):
            st = self._state[axis]
            raise ValueError(
                f"Axis {axis}: target_abs={target_abs:.3f} outside device range "
                f"[{st.min_pos:.3f}, {st.max_pos:.3f}]"
            )

    @Slot(str, float, float)
    def move_relative(self, axis: str, delta: float, speed: float):
        self._require_axis(axis)
        st = self._state[axis]
        speed_um_s = max(0.0, float(speed) * 1000)  # mm/s → µm/s
        target_abs = st.abs_pos + self.rel_delta_to_abs_delta(axis, delta)
        self._start_move(axis, target_abs=target_abs, speed_um_s=speed_um_s)

class MockPositionerManager(PositionerManager):
    """
    Simple simulation:
    - move_relative => sets a target_abs and advances towards it
    - stop => immediate stop
    """
    def __init__(self, axes: list[str], parent=None, tick_ms: int = 30):
        super().__init__(axes, parent=parent)
        self._tick_s = tick_ms / 1000.0

        self._timer = QTimer(self)
        self._timer.setInterval(tick_ms)
        self._timer.timeout.connect(self._on_tick)
        self._timer.start()

    @Slot(str, float, float)
    def move_to_rel(self, axis: str, rel_target: float, speed: float):
        """
        Go to a RELATIVE position requested by the user.
        Converted to absolute through the axis's logical frame.
        """
        self._require_axis(axis)
        speed_um_s = max(0.0, float(speed)) * 1000.0   # mm/s -> µm/s
        target_abs = self.rel_to_abs(axis, rel_target)
        self._start_move(axis, target_abs=target_abs, speed_um_s=speed_um_s)
    
    @Slot(str, float, float, float, str)
    def move_from_scan(
        self,
        axis_name: str,
        target_rel: float,
        vel_um_s: float,
        t_sched_ms: float,
        reason: str
    ):
        """
        Command coming from the ScanManager.
        The scan speaks in UI/hardware names, the manager in x/y/z/p.
        """
        # Axe de scan Polarization : azimut -> table -> déplace λ/2 ET λ/4.
        if axis_name == "Polarization":
            self._handle_polarization_scan(target_rel, reason)
            return

        axis = self.axis_from_scan_name(axis_name)
        if axis is None:
            return
        if axis not in self._state:
            return

        st = self._state[axis]
        target_abs = self.rel_to_abs(axis, target_rel)

        # stop d'un éventuel move en cours
        st.target_abs = None
        if st.moving:
            st.moving = False
            self.movingChanged.emit(axis, False)

        # saut direct à la nouvelle position
        st.abs_pos = float(target_abs)

        # mise à jour immédiate GUI / visualizer
        self._emit_positions(axis)
    
    @Slot(str)
    def home(self, axis: str):
        # "Home => revenir à 0 relatif" => abs = zero_offset
        st = self._state[axis]
        speed_um_s = st.speed if st.speed > 0 else 1000.0
        self._start_move(axis, target_abs=st.zero_offset, speed_um_s=speed_um_s)

    @Slot(str)
    def stop(self, axis: str):
        st = self._state[axis]
        st.target_abs = None
        if st.moving:
            st.moving = False
            self.movingChanged.emit(axis, False)

    @Slot(str)
    def set_zero(self, axis: str):
        st = self._state[axis]
        st.zero_offset = st.abs_pos
        # rel devient 0, abs ne change pas
        self._emit_positions(axis)

    @Slot(str, float, float)
    def move_relative(self, axis: str, delta: float, speed: float):
        self._require_axis(axis)
        st = self._state[axis]
        speed_um_s = max(0.0, float(speed) * 1000)  # mm/s → µm/s
        target = st.abs_pos + self.rel_delta_to_abs_delta(axis, delta)
        self._start_move(axis, target_abs=target, speed_um_s=speed_um_s)

    @Slot(float, float, float, float)
    def move_xy_to_rel(self, x_rel: float, y_rel: float, speed_x: float, speed_y: float):
        speed = max(float(speed_x), float(speed_y), 0.001)
        if "x" in self._state:
            self.move_to_rel("x", float(x_rel), speed)
        if "y" in self._state:
            self.move_to_rel("y", float(y_rel), speed)

    def _start_move(self, axis: str, target_abs: float, speed_um_s: float):
        st = self._state[axis]
        st.target_abs = float(target_abs)
        st.speed = max(0.0, float(speed_um_s))
        if not st.moving:
            st.moving = True
            self.movingChanged.emit(axis, True)

    def _on_tick(self):
        # avance toutes les axes "moving"
        for axis, st in self._state.items():
            if st.target_abs is None:
                continue

            cur = st.abs_pos
            tgt = st.target_abs
            if st.speed <= 0.0:
                st.abs_pos = tgt
            else:
                step = st.speed * self._tick_s
                if tgt > cur:
                    st.abs_pos = min(tgt, cur + step)
                else:
                    st.abs_pos = max(tgt, cur - step)

            # arrivé ?
            if abs(st.abs_pos - tgt) < 1e-9:
                st.target_abs = None
                if st.moving:
                    st.moving = False
                    self.movingChanged.emit(axis, False)

            self._emit_positions(axis)

class HardwarePositionerManager(PositionerManager):
    """
    Transitional "hardware-like" implementation.

    - same API as the mock
    - no real hardware
    - carries out the moves immediately
    - logs everything
    """

    def __init__(self, axes: list[str], parent=None):
        super().__init__(axes, parent=parent)

    def _log(self, msg: str):
        logger.debug(f"[HardwarePositioner] {msg}")

    @Slot(str, float, float)
    def move_to_rel(self, axis: str, rel_target: float, speed: float):
        self._log(f"move_to_rel axis={axis} target={rel_target:.3f} speed={speed}")

        st = self._state[axis]
        st.abs_pos = self.rel_to_abs(axis, rel_target)

        self._emit_positions(axis)

    @Slot(str, float, float, float, str)
    def move_from_scan(
        self,
        axis_name: str,
        target_rel: float,
        vel_um_s: float,
        t_sched_ms: float,
        reason: str
    ):
        axis = self.axis_from_scan_name(axis_name)
        if axis is None:
            return
        if axis not in self._state:
            return

        self._log(
            f"[SCAN] axis={axis_name} target={target_rel:.3f} "
            f"vel={vel_um_s} t_sched={t_sched_ms}ms reason={reason}"
        )

        st = self._state[axis]

        if t_sched_ms > 0:
            time.sleep(t_sched_ms / 1000.0)

        st.abs_pos = self.rel_to_abs(axis, target_rel)
        self._emit_positions(axis)

    @Slot(str)
    def home(self, axis: str):
        self._log(f"home axis={axis}")

        st = self._state[axis]
        st.abs_pos = st.zero_offset

        self._emit_positions(axis)

    @Slot(str)
    def stop(self, axis: str):
        self._log(f"stop axis={axis}")
        # pas d'effet réel ici

    @Slot(str)
    def set_zero(self, axis: str):
        self._log(f"set_zero axis={axis}")

        st = self._state[axis]
        st.zero_offset = st.abs_pos

        self._emit_positions(axis)