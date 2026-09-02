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
    Evénement discret planifié dans la timeline maître.

    IMPORTANT:
    - target_rel est exprimé dans l'unité native de l'axe
      (µm pour X/Y/Z, degrés pour Polarization, etc.)
    - velocity est exprimée dans l'unité native de l'axe, par seconde.

    Note:
    - acceleration_max et jerk ne font plus partie du modèle.
    - l'overscan raster est porté par overscan_fraction.
    - le retour de frame Y est porté par frame_flyback_time_s.
    """
    sample_index: int
    axis_name: str
    target_rel: float
    velocity: float
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
    leading_skip_px: int = 0
    trailing_skip_px: int = 0

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
        # Physically scans in the stage Y direction (fast mirror, up-down).
        "sample_direction": "Y",
        "conv_um_per_v": 77.0,
        "vmin": -10.0,
        "vmax": 10.0,
        "overscan_fraction": 0.10,
        "frame_flyback_time_s": 0.0,
        "vel_max": 1000.0,
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