from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Literal

import numpy as np

# ---------------------------------------------------------
# Data Class
# ---------------------------------------------------------
@dataclass
class DetectorChannelSpec:
    name: str
    kind: Literal["analog", "digital"] = "analog"
    enabled: bool = True

    # analog only
    ni_ai_channel: Optional[str] = None

    # digital only
    digital_source: Optional[str] = None
    digital_mode: Literal["counts", "count_rate"] = "counts"

@dataclass
class ScanParams:
    mode: str
    axis_order: List[str]
    active_axes: List[str]

    pixel_values: List[int]
    sizes: Dict[str, float]
    step_sizes: Dict[str, float]
    offsets: Dict[str, float]

    dwell_time_s: float

    bidirectional_scan: bool = False
    bidirectional_shift_px: int = 0
    overscan_fraction: float = 0.0
    frame_flyback_time_s: float = 0.0

    conversion_factors: Dict[str, float] = field(default_factory=dict)
    min_voltages: Dict[str, float] = field(default_factory=dict)
    max_voltages: Dict[str, float] = field(default_factory=dict)

    velocity_max: Dict[str, float] = field(default_factory=dict)

    repetitions: int = 1
    delay_between_rep_s: float = 0.0
    active_channels: List[str] = field(default_factory=list)
    detector_channels: List[DetectorChannelSpec] = field(default_factory=list)
    initial_relative_positions: Dict[str, float] = field(default_factory=dict)
    # Mode de balayage par axe pour les axes "stack" (Z-Vcoil, Polarization) :
    #   "around" -> ±size/2 autour de la position courante (défaut)
    #   "from"   -> taille complète À PARTIR de la position courante
    scan_modes: Dict[str, str] = field(default_factory=dict)
    samples_per_pixel: int = 1
    scan_kind: str = "laser"
    pixel_source_kind: str = "analog_integrating"
    sample_settle_time_s: float = 0.0


@dataclass
class StepEvent:
    """
    Discrete event scheduled on the master timeline.

    IMPORTANT:
    - target_rel is expressed in the axis's native unit
      (µm for X/Y/Z, degrees for Polarization, etc.)
    - velocity is expressed in the axis's native unit, per second.

    Note:
    - acceleration_max and jerk are no longer part of the model.
    - the raster overscan is carried by overscan_fraction.
    - the Y frame flyback is carried by frame_flyback_time_s.
    """
    sample_index: int
    axis_name: str
    target_rel: float
    velocity: float
    reason: str

@dataclass
class FrameSlice:
    """
    Portion of the timeline corresponding to one XY image.
    """
    sample_start: int
    sample_stop: int

    rep_index: int = 0
    axis3_index: int = 0
    axis4_index: int = 0

    axis3_value: Optional[float] = None
    axis4_value: Optional[float] = None

@dataclass
class SampleFramePlan:
    """
    Describes one 2D sample-scan frame within a multi-axis run.
    """
    axis3_index: int = 0
    axis4_index: int = 0
    axis3_name: Optional[str] = None
    axis4_name: Optional[str] = None
    axis3_value: Optional[float] = None
    axis4_value: Optional[float] = None

    def idx_tuple(self) -> tuple[int, ...]:
        if self.axis4_name is not None:
            return (int(self.axis3_index), int(self.axis4_index))
        if self.axis3_name is not None:
            return (int(self.axis3_index),)
        return tuple()

@dataclass
class FrameReconstructionPlan:
    """
    Minimal plan needed to reconstruct an XY frame
    from a detector time stream.
    """
    dim_x: int                  # largeur image logique
    dim_y: int                  # hauteur image logique

    dim_fast: int               # nb pixels sur l'axe rapide (ordre matériel)
    dim_slow: int               # nb pixels sur l'axe lent   (ordre matériel)

    samples_per_pixel: int = 1

    bidirectional: bool = False
    bidirectional_shift_px: int = 0
    leading_skip_px: int = 0
    trailing_skip_px: int = 0

    fast_axis_is_image_x: bool = True

@dataclass
class ExecutionPlan:
    """
    Complete execution plan for a run.
    All the timing truth of the system must be derivable from this
    plan through sample_index / sample_rate_hz.
    """
    mode: str

    dwell_time_s: float
    pixel_rate_hz: float
    samples_per_pixel: int
    sample_rate_hz: float

    total_samples: int

    ao_x: np.ndarray
    ao_y: np.ndarray

    step_events: List[StepEvent] = field(default_factory=list)
    frame_slices: List[FrameSlice] = field(default_factory=list)

    metadata: Dict[str, object] = field(default_factory=dict)

# ---------------------------------------------------------
# Machine axis default configuration
# ---------------------------------------------------------
#: Fixed margin added to every reserved stack move, in seconds.
#: Covers command latency and the tail of the mechanical settling, which the
#: distance/velocity model does not describe. Deliberately small: the point is
#: to reserve the move, not to wait on a round number.
STACK_SETTLE_MARGIN_S = 0.005


def stack_move_time_s(distance: float, velocity_per_s: float,
                      margin_s: float = STACK_SETTLE_MARGIN_S) -> float:
    """Time to reserve for one stack-axis move, in seconds.

    Open loop on purpose: the duration is computed from the distance and the
    axis velocity rather than measured by polling the device. Asking every
    controller where it is at every step costs a serial round trip per plane,
    which is far more than the move itself for a short step.

    The single source of truth for both the execution plan, which reserves this
    time, and the duration estimate, which counts it. If they disagreed, the
    displayed time would not be the time actually taken.
    """
    distance = abs(float(distance))
    if distance <= 0.0:
        return 0.0
    velocity = float(velocity_per_s)
    if velocity <= 0.0:
        return float(margin_s)
    return distance / velocity + float(margin_s)


def bidirectional_shift_px(lag_us: float, dwell_us: float) -> int:
    """Pixel shift to apply on reverse lines, from the calibrated galvo lag.

    A galvo follows its command with a fixed delay. On a forward line the image
    lands late by that delay, on a reverse line it lands late the other way, so
    the even-to-odd offset is the round-trip lag. In pixels:

        shift = lag / dwell

    The scan size and the pixel size cancel out, which is the useful part: they
    set the scan speed *and* the µm-per-pixel conversion, in opposite
    directions. Only the dwell time is left, so one calibration holds for every
    ROI and every sampling, and only has to be redone if the dwell changes --
    which is exactly what makes it automatable.

    `lag_us` is the round-trip (even-to-odd) lag, twice the galvo's one-way
    delay. It is stored that way because it is what directly divides by the
    dwell.
    """
    dwell_us = float(dwell_us)
    if dwell_us <= 0.0:
        return 0
    return int(round(float(lag_us) / dwell_us))


def bidirectional_lag_us(shift_px: float, dwell_us: float) -> float:
    """Inverse of bidirectional_shift_px(): calibrate the lag from one shift.

    Set the shift by hand once, at a known dwell, and the lag that explains it
    follows. From then on the shift is computed rather than adjusted.
    """
    return abs(float(shift_px)) * max(0.0, float(dwell_us))


SCAN_AXIS_DEFAULTS = {
    "X-Galvo": {
        # Physically scans in the stage Y direction (fast mirror, up-down).
        "sample_direction": "Y",
        "conv_um_per_v": 77.0,
        "vmin": -10.0,
        "vmax": 10.0,
        "overscan_fraction": 0.10,
        "frame_flyback_time_s": 0.0,
        "vel_max": 1000.0,
        # Round-trip galvo lag, in µs. 0 = not calibrated yet, so the
        # bidirectional shift stays whatever the user sets by hand.
        "bidir_lag_us": 0.0,
    },
    "Y-Galvo": {
        # Physically scans in the stage X direction (slow mirror, left-right).
        "sample_direction": "X",
        "conv_um_per_v": 70.0,
        "vmin": -10.0,
        "vmax": 10.0,
        "overscan_fraction": 0.10,
        "frame_flyback_time_s": 0.001,
        "vel_max": 1000.0,
    },
}


def infer_image_axes(fast_axis: str, slow_axis: str) -> tuple[str, str]:
    """
    Returns (image_x_axis, image_y_axis) from a fast/slow galvo pair.

    Uses sample_direction from SCAN_AXIS_DEFAULTS when available,
    falls back to name prefix (X-* → X direction, Y-* → Y direction).
    """
    axes = [fast_axis, slow_axis]

    def _sample_dir(ax: str) -> str:
        d = SCAN_AXIS_DEFAULTS.get(ax, {}).get("sample_direction")
        if d:
            return str(d).upper()
        return "X" if ax.startswith("X-") else "Y"

    dirs = {ax: _sample_dir(ax) for ax in axes}

    image_x_axis = next((ax for ax in axes if dirs[ax] == "X"), fast_axis)
    image_y_axis = next((ax for ax in axes if dirs[ax] == "Y"), slow_axis)

    return image_x_axis, image_y_axis

STEPPER_AXIS_DEFAULTS = {
    "X-Stage": {
        "min_um": -25000.0,
        "max_um": 25000.0,
        "vel_max": 4.0,
        "backlash_um": 1.2,
        "backlash_forward_um": 0.0,
        "ums_scaling": 0.635,
    },
    "Y-Stage": {
        "min_um": -25000.0,
        "max_um": 25000.0,
        "vel_max": 4.0,
        "backlash_um": 1.2,
        "backlash_forward_um": 0.0,
        "ums_scaling": 0.635,
    },
    "Z-Vcoil": {
        "min_um": 0.0,
        "max_um": 7000.0,
        "vel_max": 200.0,
        "tolerance": 0.1,
    },
    "Polarization": {
        "min_um": -360.0,   # rotation autorisée dans les deux sens
        "max_um": 360.0,
        "vel_max": 430.0,   # ELL14 : vitesse max fixe 430°/s (non modifiable)
        "tolerance": 0.1,
        # Offset de montage : le 0° du positioner (relatif) correspond à cet
        # angle physique de la lame. -> devient le zero_offset de l'axe.
        "offset_deg": 28.97,
        # Positions (relatives) des lames pour les polarisations circulaires.
        "cd_deg": 9.8,   # circulaire droite (CD)
        "cg_deg": 53.3,    # circulaire gauche (CG)
    },
    # P(λ/4) : lame quart d'onde (ELL14 adresse 2). Axe positioner uniquement,
    # jamais proposé comme axe de scan.
    "Polarization-L4": {
        "min_um": -360.0,   # rotation autorisée dans les deux sens
        "max_um": 360.0,
        "vel_max": 430.0,   # ELL14 : vitesse max fixe 430°/s (non modifiable)
        "tolerance": 0.1,
        "offset_deg": 26.49,
        "cd_deg": 53.48,     # circulaire droite (CD)
        "cg_deg": 56.16,    # circulaire gauche (CG)
    },
}