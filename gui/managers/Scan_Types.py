from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional
import numpy as np

# ---------------------------------------------------------
# Data Class
# ---------------------------------------------------------
@dataclass
class ScanParams:
    """
    Description déclarative d'un scan.
    C'est la version structurée des paramètres issus du ScanWidget/UI.
    """
    mode: str  # "preview_single", "preview_continuous", "acquisition"

    axis_order: List[str]
    active_axes: List[str]

    pixel_values: List[int]                 # [nx, ny, n3, n4]
    sizes: Dict[str, float]                 # µm ou ° selon l'axe
    step_sizes: Dict[str, float]            # taille pixel / pas par axe (µm ou °)
    offsets: Dict[str, float]

    dwell_time_s: float

    bidirectional_scan: bool = False
    bidirectional_shift_px: int = 0
    turnback_offset_px: int = 0

    conversion_factors: Dict[str, float] = field(default_factory=dict)
    min_voltages: Dict[str, float] = field(default_factory=dict)
    max_voltages: Dict[str, float] = field(default_factory=dict)

    velocity_max: Dict[str, float] = field(default_factory=dict)
    acceleration_max: Dict[str, float] = field(default_factory=dict)
    jerk: Dict[str, float] = field(default_factory=dict)

    repetitions: int = 1
    delay_between_rep_s: float = 0.0

    active_channels: List[str] = field(default_factory=list)

    initial_relative_positions: Dict[str, float] = field(default_factory=dict)

    # Sur-échantillonnage temporel éventuel
    samples_per_pixel: int = 1

    scan_kind: str = "laser"

    # prévu pour la suite :
    # - "analog_integrating" pour PMT / NI-DAQ
    # - plus tard : "camera_scalar", etc.
    pixel_source_kind: str = "analog_integrating"

    # Temps de stabilisation mécanique éventuel
    sample_settle_time_s: float = 0.0


@dataclass
class StepEvent:
    """
    Evénement discret planifié dans la timeline maître.
    """
    sample_index: int
    axis_name: str
    target_rel: float
    velocity_um_s: float
    acceleration_um_s2: float
    jerk_um_s3: float
    reason: str

@dataclass
class FrameSlice:
    """
    Portion de timeline correspondant à une image XY.
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
    Décrit une frame sample-scan 2D au sein d'un run multi-axes.
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
    Plan minimal nécessaire pour reconstruire une frame XY
    à partir d'un flux temporel détecteur.
    """
    dim_x: int                  # largeur image logique
    dim_y: int                  # hauteur image logique

    dim_fast: int               # nb pixels sur l'axe rapide (ordre matériel)
    dim_slow: int               # nb pixels sur l'axe lent   (ordre matériel)

    samples_per_pixel: int = 1

    bidirectional: bool = False
    bidirectional_shift_px: int = 0
    turnback_offset_px: int = 0

    fast_axis_is_image_x: bool = True

@dataclass
class ExecutionPlan:
    """
    Plan complet d'exécution d'un run.
    Toute la vérité temporelle du système doit pouvoir être dérivée
    de ce plan via sample_index / sample_rate_hz.
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
SCAN_AXIS_DEFAULTS = {
    "X-Galvo": {
        "conv_um_per_v": 100.0,
        "vmin": -10.0,
        "vmax": 10.0,
        "turnback_px": 0,
        "vel_max": 1000.0,
        "acc_max": 1000.0,
        "jerk": 1.0,
    },
    "Y-Galvo": {
        "conv_um_per_v": 100.0,
        "vmin": -10.0,
        "vmax": 10.0,
        "turnback_px": 0,
        "vel_max": 1000.0,
        "acc_max": 1000.0,
        "jerk": 1.0,
    },
}

STEPPER_AXIS_DEFAULTS = {
    "X-Stage": {
        "min_um": -10000,
        "max_um": 10000.0,
        "vel_max": 1.0,
        "tolerance": 0.1,
    },
    "Y-Stage": {
        "min_um": -10000.0,
        "max_um": 10000.0,
        "vel_max": 1.0,
        "tolerance": 0.1,
    },
    "Z-Vcoil": {
        "min_um": 0.0,
        "max_um": 7000.0,
        "vel_max": 1000.0,
        "tolerance": 0.1,
    },
    "Polarization": {
        "min_um": 0.0,
        "max_um": 180.0,
        "vel_max": 10.0,
        "tolerance": 0.1,
    },
}