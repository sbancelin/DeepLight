from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional
import time

from PySide6.QtCore import QObject, Signal, Slot, QTimer


@dataclass
class AxisState:
    abs_pos: float = 0.0
    zero_offset: float = 0.0
    target_abs: Optional[float] = None
    speed: float = 0.0
    moving: bool = False
    min_pos: float = -1000.0
    max_pos: float = 1000.0
    max_speed: float = 1.0
    tolerance: float = 0.1


class PositionerManager(QObject):
    """API commune des gestionnaires de positionneurs."""
    absPositionChanged = Signal(str, float)   # axis, abs
    relPositionChanged = Signal(str, float)   # axis, rel
    movingChanged = Signal(str, bool)         # axis, moving

    SCAN_AXIS_MAP = {
        "X-Stage": "x",
        "Y-Stage": "y",
        "Z-Vcoil": "z",
        "Polarization": "p",
    }

    def __init__(self, axes: list[str], parent=None):
        super().__init__(parent)
        self._axes = axes
        self._state: Dict[str, AxisState] = {a: AxisState() for a in axes}

    def stop_all(self):
        """Arrête le mouvement de tous les axes."""
        for axis in self._axes:
            self.stop(axis)
    
    def axis_from_scan_name(self, axis_name: str) -> str | None:
        return self.SCAN_AXIS_MAP.get(axis_name)
    
    def has_axis(self, axis: str) -> bool:
        return axis in self._state

    def _require_axis(self, axis: str) -> None:
        if axis not in self._state:
            raise KeyError(f"Unknown axis={axis!r}. Known={list(self._state.keys())}")
    
    def get_zero_offset(self, axis: str) -> float:
        return float(self._state[axis].zero_offset)

    def get_tolerance(self, axis: str) -> float:
        return float(self._state[axis].tolerance)

    def get_limits(self, axis: str) -> tuple[float, float]:
        st = self._state[axis]
        return float(st.min_pos), float(st.max_pos)

    def get_max_speed(self, axis: str) -> float:
        return float(self._state[axis].max_speed)

    def get_abs_pos(self, axis: str) -> float:
        return float(self._state[axis].abs_pos)

    def get_rel_pos(self, axis: str) -> float:
        st = self._state[axis]
        return float(st.abs_pos - st.zero_offset)

    def _emit_positions(self, axis: str):
        self.absPositionChanged.emit(axis, self.get_abs_pos(axis))
        self.relPositionChanged.emit(axis, self.get_rel_pos(axis))
    
    def _validate_move(self, axis: str, target_abs: float, speed: float) -> bool:
        """Valide si le mouvement est autorisé."""
        st = self._state[axis]
        if target_abs < st.min_pos or target_abs > st.max_pos:
            print(f"Position {target_abs} hors limites pour l'axe {axis}.")
            return False
        if speed > st.max_speed:
            print(f"Vitesse {speed} trop élevée pour l'axe {axis}.")
            return False
        return True
    
    def set_limits(self, axis: str, min_pos: float, max_pos: float, max_speed: float, tolerance: float):
        """Met à jour les limites pour un axe donné."""
        st = self._state[axis]
        st.min_pos = min_pos
        st.max_pos = max_pos
        st.max_speed = max_speed
        st.tolerance = tolerance

    @Slot(str, float, float)
    def move_relative(self, axis: str, delta: float, speed: float):
        self._require_axis(axis)
        st = self._state[axis]
        speed_um_s = max(0.0, float(speed) * 1000)  # mm/s → µm/s
        target_abs = st.abs_pos + delta

        self._start_move(axis, target_abs=target_abs, speed_um_s=speed_um_s)

class MockPositionerManager(PositionerManager):
    """
    Simulation simple :
    - move_relative => définit un target_abs et avance vers la cible
    - stop => stop immédiat
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
        Va à une position RELATIVE demandée (par l'utilisateur).
        Convertit en absolu via zero_offset.
        """
        st = self._state[axis]
        speed_um_s = max(0.0, float(speed))*1000      # Convertir de mm/s à µm/s
        target_abs = st.zero_offset + float(rel_target)
        self._start_move(axis, target_abs=target_abs, speed_um_s=speed_um_s)
    
    @Slot(str, float, float, float, float, float, str)
    def move_from_scan(
        self,
        axis_name: str,
        target_rel: float,
        vel_um_s: float,
        acc_um_s2: float,
        jerk_um_s3: float,
        t_sched_ms: float,
        reason: str
    ):
        """
        Commande issue du ScanManager.
        Le scan parle avec des noms UI/hardware, le manager avec x/y/z/p.
        """
        axis = self.axis_from_scan_name(axis_name)
        if axis is None:
            return
        if axis not in self._state:
            return

        st = self._state[axis]
        target_abs = st.zero_offset + float(target_rel)

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
        target = st.abs_pos + float(delta)
        self._start_move(axis, target_abs=target, speed_um_s=speed_um_s)

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
    Implémentation de transition "hardware-like".

    - même API que le mock
    - pas de vrai hardware
    - exécute les mouvements immédiatement
    - loggue tout
    """

    def __init__(self, axes: list[str], parent=None):
        super().__init__(axes, parent=parent)

    def _log(self, msg: str):
        print(f"[HardwarePositioner] {msg}")

    @Slot(str, float, float)
    def move_to_rel(self, axis: str, rel_target: float, speed: float):
        self._log(f"move_to_rel axis={axis} target={rel_target:.3f} speed={speed}")

        st = self._state[axis]
        st.abs_pos = rel_target + st.zero_offset

        self._emit_positions(axis)

    @Slot(str, float, float, float, float, float, str)
    def move_from_scan(
        self,
        axis_name: str,
        target_rel: float,
        vel_um_s: float,
        acc_um_s2: float,
        jerk_um_s3: float,
        t_sched_ms: float,
        reason: str
    ):
        self._log(
            f"[SCAN] axis={axis_name} target={target_rel:.3f} "
            f"vel={vel_um_s} t_sched={t_sched_ms}ms reason={reason}"
        )

        st = self._state[axis_name]

        # simulation simple : on attend le temps prévu
        if t_sched_ms > 0:
            time.sleep(t_sched_ms / 1000.0)

        st.abs_pos = target_rel + st.zero_offset
        self._emit_positions(axis_name)

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