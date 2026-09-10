"""Raise the laser power with depth to offset attenuation in the sample.

Pure computation: no Qt, no hardware. The widget is a thin shell over this.

What is being held constant
---------------------------
The intensity **in the focal volume**, at every depth.

`mu` is the attenuation coefficient of the excitation beam in the sample, in
µm^-1. Beer-Lambert gives the intensity reaching the focus as
I(z) = I(0) * exp(-mu*z), so keeping it constant means raising the incident
power by exactly the inverse:

    P(z) = P(0) * exp(mu * z)

There is deliberately no process order here, and none is needed. An n-photon
signal follows the n-th power of the focal intensity, so once that intensity is
held constant the signal is too, whatever n may be: writing the condition out,
(P * exp(-mu*z))^n = const gives P = const * exp(mu*z), and n cancels. A
1/2/3-photon selector on this ramp would change nothing, which is why there
isn't one.
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

#: How the power is decided at a given depth.
RAMP_MODES = ("exponential", "table")


def parse_power_table(text: str):
    """Read ``0:10, 40:25, 80:60`` into ((depth_um, percent), ...).

    A compact text field rather than a grid of cells: the panel is narrow, the
    points are few, and a line like this can be pasted straight out of the
    notebook where the powers were measured.
    """
    points = []
    for chunk in str(text or "").replace(";", ",").split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        depth, _, percent = chunk.partition(":")
        if not _:
            raise ValueError(f"{chunk!r} is not a depth:percent pair")
        try:
            points.append((float(depth.replace(",", ".")),
                           float(percent.replace(",", "."))))
        except ValueError:
            raise ValueError(f"{chunk!r} is not a depth:percent pair") from None

    points.sort(key=lambda point: point[0])

    depths = [d for d, _ in points]
    if len(set(depths)) != len(depths):
        raise ValueError("the same depth appears twice")

    return tuple(points)


def format_power_table(table) -> str:
    """The inverse of parse_power_table, for putting a table back in the field."""
    return ", ".join(f"{depth:g}:{percent:g}" for depth, percent in (table or ()))


@dataclass
class DepthCompensation:
    """Settings of the depth ramp."""

    attenuation_um_inv: float = 0.0
    enabled: bool = False
    #: "exponential" follows Beer-Lambert from the surface power; "table"
    #: reads measured powers off a list, for a sample that does not.
    mode: str = "exponential"
    #: ((depth_um, percent), ...) sorted by depth. Absolute percentages, not
    #: gains: these are the powers someone measured, and making them relative
    #: to a surface power captured later would change what they mean.
    table: tuple = ()

    def effective_coefficient(self) -> float:
        """Coefficient applied to the depth, in µm^-1."""
        mu = float(self.attenuation_um_inv)
        if mu < 0.0:
            raise ValueError(
                f"Attenuation must be positive, got {mu} µm^-1; a negative value "
                "would lower the power with depth."
            )
        return mu

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


def interpolate_power_table(table, depths_um) -> np.ndarray:
    """Power at each depth, read off a measured table.

    Held flat outside the measured range rather than extrapolated. Continuing
    an exponential-looking curve past the deepest point someone actually
    measured is how a sample gets cooked, and the flat hold is visible in the
    profile rather than silent.
    """
    points = tuple(table or ())
    if not points:
        raise ValueError("The power table is empty: add at least one depth:percent pair.")

    depths = np.asarray(depths_um, dtype=np.float64)
    known_depths = np.array([d for d, _ in points], dtype=np.float64)
    known_percents = np.array([p for _, p in points], dtype=np.float64)

    # np.interp already clamps to the end values outside the range.
    values = np.interp(depths, known_depths, known_percents)
    return float(values) if values.ndim == 0 else values


def compute_power_profile(
    compensation: DepthCompensation,
    base_percent: float,
    depths_um,
) -> np.ndarray:
    """Power percentage requested at each depth.

    In table mode the surface power is not used: the table already says what
    the power should be, and multiplying it by wherever the laser happened to
    be would silently rescale a measurement.
    """
    if str(compensation.mode) == "table":
        return np.asarray(
            interpolate_power_table(compensation.table, depths_um), dtype=np.float64
        )

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
