"""Raise the laser power with depth to offset attenuation in the sample.

Pure computation: no Qt, no hardware. The widget is a thin shell over this.

Convention, which matters
-------------------------
`mu` is the attenuation coefficient measured on the **signal** of an n-photon
z-stack, in µm^-1 -- the quantity you actually get by imaging a homogeneous
sample and fitting the decay.

Because an n-photon signal goes as the n-th power of the excitation intensity,
a signal decaying as exp(-mu*z) comes from an excitation decaying as
exp(-mu*z/n). Restoring a constant signal therefore needs

    P(z) = P(0) * exp(mu * z / n)

which is why the process order belongs in the formula. For n = 1 it collapses
back to plain Beer-Lambert on the beam.

If instead `mu` describes the excitation beam itself, pass
MU_ON_EXCITATION and the order drops out: P(z) = P(0) * exp(mu * z).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from .Hardware_Manager import (
    MIRA_POWER_MAX_PERCENT,
    MIRA_POWER_MIN_PERCENT,
    MIRA_ROTATOR_REL_MAX_DEG,
    MIRA_ROTATOR_REL_MIN_DEG,
)

#: `mu` was measured on the n-photon signal decay (default).
MU_ON_SIGNAL = "signal"
#: `mu` describes the excitation beam; the process order then plays no part.
MU_ON_EXCITATION = "excitation"

SUPPORTED_ORDERS = (1, 2, 3)


@dataclass
class DepthCompensation:
    """Settings of the depth ramp."""

    attenuation_um_inv: float = 0.0
    order: int = 2
    mu_refers_to: str = MU_ON_SIGNAL
    enabled: bool = False

    def effective_coefficient(self) -> float:
        """Coefficient actually applied to the depth, in µm^-1."""
        mu = float(self.attenuation_um_inv)
        if self.mu_refers_to == MU_ON_EXCITATION:
            return mu
        order = int(self.order)
        if order not in SUPPORTED_ORDERS:
            raise ValueError(f"Unsupported process order {self.order!r}; expected 1, 2 or 3.")
        return mu / order

    def power_gain(self, depth_um) -> np.ndarray | float:
        """Multiplier to apply to the surface power at the given depth(s)."""
        k = self.effective_coefficient()
        depth = np.asarray(depth_um, dtype=np.float64)
        gain = np.exp(k * depth)
        return float(gain) if gain.ndim == 0 else gain

    def attenuation(self, depth_um) -> np.ndarray | float:
        """Beer-Lambert transmission of the excitation at the given depth(s)."""
        k = self.effective_coefficient()
        depth = np.asarray(depth_um, dtype=np.float64)
        value = np.exp(-k * depth)
        return float(value) if value.ndim == 0 else value


@dataclass
class PowerRangeReport:
    """Whether a computed ramp stays inside what the hardware can deliver."""

    ok: bool
    percents: np.ndarray
    depths_um: np.ndarray
    min_percent: float
    max_percent: float
    max_angle_deg: float
    first_invalid_depth_um: float | None
    reason: str

    def summary(self) -> str:
        if self.ok:
            return (
                f"{self.min_percent:.1f} % to {self.max_percent:.1f} % "
                f"(waveplate up to {self.max_angle_deg:.1f} deg)"
            )
        return self.reason


def percent_to_waveplate_deg(percent: float) -> float:
    """Half-wave plate angle for a power percentage.

    Mirrors the 0-100 % branch of the rotation controller: the plate sweeps
    MIRA_ROTATOR_REL_MAX_DEG over the full range.
    """
    return (float(percent) / 100.0) * MIRA_ROTATOR_REL_MAX_DEG


def depth_axis_um(size_um: float, pixels: int) -> np.ndarray:
    """Depth of each plane of a stack, starting at the surface."""
    n = max(1, int(pixels))
    if n == 1:
        return np.zeros(1, dtype=np.float64)
    return np.linspace(0.0, float(size_um), n, dtype=np.float64)


def compute_power_profile(
    compensation: DepthCompensation,
    base_percent: float,
    depths_um,
) -> np.ndarray:
    """Power percentage requested at each depth."""
    return float(base_percent) * np.asarray(
        compensation.power_gain(depths_um), dtype=np.float64
    )


def validate_power_profile(
    percents,
    depths_um,
    min_percent: float = MIRA_POWER_MIN_PERCENT,
    max_percent: float = MIRA_POWER_MAX_PERCENT,
    max_angle_deg: float = MIRA_ROTATOR_REL_MAX_DEG,
    min_angle_deg: float = MIRA_ROTATOR_REL_MIN_DEG,
) -> PowerRangeReport:
    """Check the ramp against the laser range and the waveplate travel.

    Both are checked: a percentage inside 0-100 always maps inside the plate's
    travel, but the two limits are configured separately and a setup could make
    them disagree, and finding that out mid-stack is expensive.
    """
    percents = np.asarray(percents, dtype=np.float64)
    depths = np.asarray(depths_um, dtype=np.float64)

    if percents.size == 0:
        return PowerRangeReport(
            ok=False, percents=percents, depths_um=depths,
            min_percent=0.0, max_percent=0.0, max_angle_deg=0.0,
            first_invalid_depth_um=None, reason="No plane to compute.",
        )

    if not np.all(np.isfinite(percents)):
        return PowerRangeReport(
            ok=False, percents=percents, depths_um=depths,
            min_percent=0.0, max_percent=0.0, max_angle_deg=0.0,
            first_invalid_depth_um=None,
            reason="The ramp is not finite; check the attenuation coefficient.",
        )

    angles = np.array([percent_to_waveplate_deg(p) for p in percents])

    too_low = percents < min_percent
    too_high = percents > max_percent
    angle_out = (angles < min_angle_deg) | (angles > max_angle_deg)
    invalid = too_low | too_high | angle_out

    lo = float(np.min(percents))
    hi = float(np.max(percents))
    hi_angle = float(np.max(angles))

    if not np.any(invalid):
        return PowerRangeReport(
            ok=True, percents=percents, depths_um=depths,
            min_percent=lo, max_percent=hi, max_angle_deg=hi_angle,
            first_invalid_depth_um=None, reason="",
        )

    first = int(np.argmax(invalid))
    depth = float(depths[first]) if depths.size > first else float("nan")
    percent = float(percents[first])

    if too_high[first] or angle_out[first] and percent > max_percent:
        reason = (
            f"{percent:.1f} % needed at {depth:.1f} µm, above the {max_percent:.0f} % "
            "the laser can deliver. Lower the surface power, the depth or the "
            "attenuation."
        )
    elif too_low[first]:
        reason = f"{percent:.1f} % requested at {depth:.1f} µm, below {min_percent:.0f} %."
    else:
        reason = (
            f"Waveplate would need {angles[first]:.1f} deg at {depth:.1f} µm, "
            f"beyond its {max_angle_deg:.0f} deg travel."
        )

    return PowerRangeReport(
        ok=False, percents=percents, depths_um=depths,
        min_percent=lo, max_percent=hi, max_angle_deg=hi_angle,
        first_invalid_depth_um=depth, reason=reason,
    )


def max_reachable_depth_um(
    compensation: DepthCompensation,
    base_percent: float,
    max_percent: float = MIRA_POWER_MAX_PERCENT,
) -> float:
    """Depth at which the ramp would hit the ceiling, in µm.

    Useful to tell the user how deep the current settings can actually go
    rather than only that they cannot go as deep as asked.
    """
    k = compensation.effective_coefficient()
    base = float(base_percent)
    if k <= 0.0 or base <= 0.0:
        return float("inf")
    if base >= max_percent:
        return 0.0
    return math.log(max_percent / base) / k
