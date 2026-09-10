"""Describe an acquisition without a window.

The GUI's scan panel assembles a large dictionary from a dozen widgets, and
that dictionary is what the whole pipeline speaks. A script has no widgets, so
this module builds the same dictionary from a short description: the axes, the
dwell, the detectors, where to write. Everything the panel would have filled in
from its per-axis settings -- conversion factors, voltage limits, velocities,
overscan, flyback -- is taken from the same defaults the panel seeds itself
from, so a recipe and a click produce the same scan.

Nothing here imports Qt: a recipe can be built, checked and serialised without
a running application, which is what makes it worth writing to a file and
looping over.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Sequence

from .gui.managers.Scan_Types import SCAN_AXIS_DEFAULTS, STEPPER_AXIS_DEFAULTS

#: The four detector channels DeepLight V1 exposes, and what each one is.
#: Mirrors DetectorWidget.get_detector_specs(); the two must agree, and a test
#: checks that they do.
DETECTOR_CHANNELS = {
    "PMT-Vis": "analog",
    "PMT-IR": "analog",
    "Ch 0": "digital",
    "Ch 1": "digital",
}

#: Axes a scan can drive. The first two make the image; the others step
#: between frames.
SCAN_AXES = ("X-Galvo", "Y-Galvo", "X-Stage", "Y-Stage", "Z-Vcoil", "Polarization")

#: The pair that makes the image, per scan kind. Both axes have to come from
#: the same pair: the execution plan draws an image from two *continuously
#: swept* axes, and mixing in a stepped one -- an XZ slice, say -- would need a
#: slow-axis mode that does not exist yet. Asked for it anyway, the plan used
#: to emit no movement at all and record the same line N times.
IMAGE_AXES = {
    "laser": ("X-Galvo", "Y-Galvo"),
    "sample": ("X-Stage", "Y-Stage"),
}

#: Axes that step a stack, and to which the around/from mode applies. They can
#: only appear from the third row on.
STACK_AXES = ("Z-Vcoil", "Polarization")

#: How many axis rows the pipeline expects, padded with "None".
AXIS_SLOTS = 4


def axis_defaults(name: str) -> dict:
    """Settings the GUI would hold for an axis, before the user edits any.

    The scan panel seeds itself from SCAN_AXIS_DEFAULTS and the positioner
    panel from STEPPER_AXIS_DEFAULTS, so together they are what a freshly
    opened window would use.
    """
    merged = dict(STEPPER_AXIS_DEFAULTS.get(name, {}))
    merged.update(SCAN_AXIS_DEFAULTS.get(name, {}))
    return merged


@dataclass
class Axis:
    """One scanned dimension.

    `size_um` is the distance from the first sample to the last, so the step is
    size/(pixels-1) -- the same convention the panel displays. For Polarization
    the unit is degrees rather than µm, the field keeping its name because the
    pipeline treats every axis alike.
    """

    name: str
    pixels: int
    size_um: float
    offset_um: float = 0.0
    #: Stack axes only: "around" centres the range on the current position,
    #: "from" starts at it.
    mode: str = "around"

    def step_um(self) -> float:
        if self.pixels > 1:
            return float(self.size_um) / (self.pixels - 1)
        return float(self.size_um)


@dataclass
class Recipe:
    """An acquisition, as a script would write it.

    The defaults are a plain XY preview: two galvo axes and one detector. Add a
    third axis to get a stack -- Z-Vcoil for depth, Polarization for a P-SHG
    sweep -- and a folder to have it written.
    """

    axes: Sequence[Axis]
    dwell_us: float = 10.0
    detectors: Sequence[str] = ("PMT-Vis",)
    repetitions: int = 1
    delay_between_rep_s: float = 0.0
    laser_off_between_rep: bool = False

    bidirectional: bool = False
    bidirectional_shift_px: int = 0
    samples_per_pixel: int = 1
    settle_ms: float = 0.0

    #: "laser" moves the galvos, "sample" moves the stage under a fixed beam.
    scan_kind: str = "laser"

    #: Where the acquisition is written. No folder means nothing is saved and
    #: the frames only come back in memory.
    folder: str = ""
    filename: str = ""
    fmt: str = "OME-TIFF"
    comment: str = ""

    #: Recorded in the provenance beside the data, as the Nyquist panel would.
    optics: dict = field(default_factory=dict)

    #: Per-axis overrides of what axis_defaults() gives, by axis name.
    axis_settings: dict = field(default_factory=dict)

    # ---- construction -------------------------------------------------

    @classmethod
    def from_dict(cls, data: dict) -> "Recipe":
        """Build a recipe from plain data -- JSON, TOML, a config file."""
        data = dict(data or {})
        axes = [Axis(**dict(a)) for a in data.pop("axes", [])]
        return cls(axes=axes, **data)

    def to_dict(self) -> dict:
        """Plain data again, so a recipe can be written next to its results."""
        out = asdict(self)
        out["axes"] = [asdict(a) for a in self.axes]
        return out

    # ---- validation ---------------------------------------------------

    def validate(self) -> None:
        """Raise ValueError on a recipe the pipeline would refuse.

        The window says no in its log and greys a button; a script has neither,
        so the same rules are raised where the caller can see them.
        """
        active = [a for a in self.axes if a.name != "None"]

        if len(active) < 2:
            raise ValueError("An acquisition needs at least two axes: got "
                             f"{[a.name for a in active]}")
        if len(active) > AXIS_SLOTS:
            raise ValueError(f"At most {AXIS_SLOTS} axes, got {len(active)}")

        seen = set()
        for axis in active:
            if axis.name not in SCAN_AXES:
                raise ValueError(f"Unknown axis {axis.name!r}; expected one of {SCAN_AXES}")
            if axis.name in seen:
                raise ValueError(f"Axis {axis.name!r} appears twice")
            seen.add(axis.name)

            if axis.pixels < 1:
                raise ValueError(f"{axis.name}: pixels must be >= 1, got {axis.pixels}")
            if axis.size_um < 0:
                raise ValueError(f"{axis.name}: size_um must be >= 0, got {axis.size_um}")
            if axis.mode not in ("around", "from"):
                raise ValueError(f"{axis.name}: mode must be 'around' or 'from', got {axis.mode!r}")

        if self.scan_kind not in IMAGE_AXES:
            raise ValueError(
                f"scan_kind must be one of {sorted(IMAGE_AXES)}, got {self.scan_kind!r}"
            )

        image_pair = IMAGE_AXES[self.scan_kind]
        for index, axis in enumerate(active[:2]):
            if axis.name not in image_pair:
                raise ValueError(
                    f"{axis.name} cannot draw the image: a {self.scan_kind} scan is "
                    f"drawn by {image_pair[0]} and {image_pair[1]}. A slice through "
                    f"a stepped axis such as Z-Vcoil is not supported yet -- it would "
                    f"need a slow image axis that steps, and asking for one produces "
                    f"no movement at all."
                )
        for axis in active[2:]:
            if axis.name not in STACK_AXES:
                raise ValueError(
                    f"{axis.name} cannot be a stack axis: only {list(STACK_AXES)} "
                    f"step between frames."
                )

        if self.dwell_us <= 0:
            raise ValueError(f"dwell_us must be > 0, got {self.dwell_us}")
        if self.repetitions < 1:
            raise ValueError(f"repetitions must be >= 1, got {self.repetitions}")

        # Same rule the acquisition button enforces: a polarisation stack is
        # already a series, and repeating it would overwrite its own planes.
        if "Polarization" in seen and self.repetitions != 1:
            raise ValueError("A Polarization stack requires repetitions = 1")

        unknown = [d for d in self.detectors if d not in DETECTOR_CHANNELS]
        if unknown:
            raise ValueError(f"Unknown detectors {unknown}; expected any of {list(DETECTOR_CHANNELS)}")
        if not self.detectors:
            raise ValueError("At least one detector is needed")


    # ---- the dictionary the pipeline speaks ---------------------------

    def _settings_for(self, name: str) -> dict:
        merged = axis_defaults(name)
        merged.update(self.axis_settings.get(name, {}))
        return merged

    def detector_specs(self) -> list[dict]:
        """The four channels, with the ones this recipe asked for enabled."""
        wanted = set(self.detectors)
        specs = []
        for name, kind in DETECTOR_CHANNELS.items():
            specs.append({
                "name": name,
                "kind": kind,
                "enabled": name in wanted,
                "digital_source": name if kind == "digital" else None,
                "digital_mode": "counts",
            })
        return specs

    def to_scan_parameters(self, positions_um: dict | None = None) -> dict:
        """The dictionary the scan and acquisition managers consume.

        `positions_um` gives the current relative position of each stage axis,
        which the panel reads from the positioner; a scan is expressed relative
        to where the sample already is, so an offset means the same thing as it
        does in the window.
        """
        self.validate()

        positions_um = dict(positions_um or {})
        active = [a for a in self.axes if a.name != "None"]

        axis_order = [a.name for a in active] + ["None"] * (AXIS_SLOTS - len(active))
        pixel_values = [int(a.pixels) for a in active] + [1] * (AXIS_SLOTS - len(active))

        rows = []
        sizes, offsets, relative_offsets = {}, {}, {}
        current_positions, step_sizes, scan_modes = {}, {}, {}
        conversion_factors, min_voltages, max_voltages, velocity_max = {}, {}, {}, {}

        for index in range(AXIS_SLOTS):
            if index >= len(active):
                rows.append({"axis": "None", "pixels": 1, "size_um": 0.0,
                             "offset_um": 0.0, "relative_offset_um": 0.0,
                             "current_position_um": 0.0, "step_um": 0.0})
                continue

            axis = active[index]
            current = float(positions_um.get(axis.name, 0.0))
            settings = self._settings_for(axis.name)

            rows.append({
                "axis": axis.name,
                "pixels": int(axis.pixels),
                "size_um": float(axis.size_um),
                # Absolute base of the scan, as the panel computes it: where
                # the axis already is, plus the offset the user asked for.
                "offset_um": current + float(axis.offset_um),
                "relative_offset_um": float(axis.offset_um),
                "current_position_um": current,
                "step_um": axis.step_um(),
            })

            sizes[axis.name] = float(axis.size_um)
            offsets[axis.name] = current + float(axis.offset_um)
            relative_offsets[axis.name] = float(axis.offset_um)
            current_positions[axis.name] = current
            step_sizes[axis.name] = axis.step_um()
            scan_modes[axis.name] = axis.mode if axis.name in STACK_AXES else "around"

            conversion_factors[axis.name] = float(settings.get("conv_um_per_v", 20.0))
            min_voltages[axis.name] = float(settings.get("vmin", -10.0))
            max_voltages[axis.name] = float(settings.get("vmax", 10.0))
            velocity_max[axis.name] = float(settings.get("vel_max", 1.0))

        # Overscan belongs to the axis sweeping the line, flyback to the one
        # stepping between them -- the panel reads them from the same place.
        fast, slow = active[0].name, active[1].name
        overscan = float(self._settings_for(fast).get("overscan_fraction", 0.0))
        flyback = float(self._settings_for(slow).get("frame_flyback_time_s", 0.0))

        specs = self.detector_specs()
        total_pixels = 1
        for axis in active:
            total_pixels *= max(1, int(axis.pixels))

        return {
            "rows": rows,
            "pixel_values": pixel_values,
            "dwell_time": float(self.dwell_us) / 1e6,
            "samples_per_pixel": max(1, int(self.samples_per_pixel)),
            "scan_kind": str(self.scan_kind),
            "pixel_source_kind": "analog_integrating",
            "sample_settle_time_s": max(0.0, float(self.settle_ms) / 1e3),
            "active_axes": [a.name for a in active],
            "axis_order": axis_order,
            "conversion_factors": conversion_factors,
            "min_voltages": min_voltages,
            "max_voltages": max_voltages,
            "velocity_max": velocity_max,
            "overscan_fraction": overscan,
            "frame_flyback_time_s": flyback,
            "bidirectional_scan": bool(self.bidirectional),
            "bidirectional_shift_px": int(self.bidirectional_shift_px) if self.bidirectional else 0,
            "backlash_x_um": float(self._settings_for("X-Stage").get("backlash_um", 0.0)),
            "backlash_x_forward_um": float(self._settings_for("X-Stage").get("backlash_forward_um", 0.0)),
            "repetitions": max(1, int(self.repetitions)),
            "delay_between_rep": max(0.0, float(self.delay_between_rep_s)),
            "laser_off_between_rep": bool(self.laser_off_between_rep),
            "sizes": sizes,
            "offsets": offsets,
            "relative_offsets": relative_offsets,
            "current_positions": current_positions,
            "initial_relative_positions": dict(current_positions),
            "scan_modes": scan_modes,
            "step_sizes": step_sizes,
            "total_pixels": total_pixels,
            "detector_channels": specs,
            "active_channels": [s["name"] for s in specs if s["enabled"]],
        }


def polarization_sweep(
    angles_deg: Sequence[float] | None = None,
    count: int = 8,
    span_deg: float = 180.0,
    offset_deg: float = 0.0,
) -> Axis:
    """A Polarization axis for a P-SHG sweep.

    Given `count` and `span_deg`, the half-wave plate visits `count` evenly
    spaced angles covering the span; a P-SHG series is usually 8 or 18 steps
    over 180 degrees. Passing explicit `angles_deg` instead only works for an
    even spacing, the axis being stepped rather than listed -- an uneven series
    has to be run as several acquisitions.
    """
    if angles_deg is not None:
        angles = [float(a) for a in angles_deg]
        if len(angles) < 2:
            raise ValueError("A sweep needs at least two angles")
        steps = [round(b - a, 9) for a, b in zip(angles, angles[1:])]
        if len(set(steps)) != 1:
            raise ValueError(f"Angles must be evenly spaced, got steps {steps}")
        return Axis("Polarization", pixels=len(angles),
                    size_um=angles[-1] - angles[0], offset_um=angles[0], mode="from")

    if count < 2:
        raise ValueError("A sweep needs at least two angles")

    # size is first-to-last, so a full 180 degree turn in `count` steps stops
    # one step short of wrapping back onto the first angle.
    step = float(span_deg) / count
    return Axis("Polarization", pixels=count, size_um=step * (count - 1),
                offset_um=float(offset_deg), mode="from")
