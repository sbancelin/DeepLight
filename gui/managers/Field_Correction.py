"""Take the detector's own signature out of an image.

A camera frame is not just what the sample sent back. A dark frame -- the
sensor's read offset and its dark current, which on a cooled EMCCD grows with
exposure -- is added to every pixel whether or not any light arrived. A flat
field -- vignetting, uneven illumination, per-pixel gain -- multiplies it. Both
are properties of the instrument, both are measurable once, and neither belongs
in the data.

    corrected = (raw - dark) / (flat - dark) * mean(flat - dark)

with the last factor keeping the result in the original units rather than
around 1. With no flat, only the subtraction is applied.

Corrections are taken on the raw sensor frame, before any ROI or software
binning: one reference then serves every crop, instead of a new dark per
region.

No Qt here: this is arithmetic over arrays plus a file format, so it can be
tested and reused by whichever instrument needs it.
"""

from __future__ import annotations

from datetime import datetime

import numpy as np

#: Below this, a flat pixel carries no signal to divide by and would explode
#: the result; those pixels are left uncorrected for gain.
MIN_FLAT_LEVEL = 1e-6


def average_frames(frames) -> np.ndarray | None:
    """Mean of a series of frames, in float32.

    Averaging is the whole point of taking several: the read noise falls as
    1/sqrt(n) while the offset and dark current, which is what is being
    measured, stay exactly where they are.
    """
    stack = [np.asarray(f, dtype=np.float32) for f in (frames or [])]
    if not stack:
        return None

    shapes = {f.shape for f in stack}
    if len(shapes) != 1:
        raise ValueError(f"Frames of differing shapes cannot be averaged: {sorted(shapes)}")

    return np.mean(stack, axis=0, dtype=np.float32)


class FieldCorrection:
    """A dark frame, optionally a flat field, and what they were taken with.

    The metadata is not decoration: a dark is only valid for the exposure and
    binning it was taken at, so it travels with them and the caller can say so
    in the provenance.
    """

    def __init__(self, dark=None, flat=None, metadata: dict | None = None):
        self.dark = None if dark is None else np.asarray(dark, dtype=np.float32)
        self.flat = None if flat is None else np.asarray(flat, dtype=np.float32)
        self.metadata = dict(metadata or {})

        if self.dark is not None and self.flat is not None:
            if self.dark.shape != self.flat.shape:
                raise ValueError(
                    f"Dark {self.dark.shape} and flat {self.flat.shape} must have the same shape"
                )

        self.metadata.setdefault("created", datetime.now().astimezone().isoformat(timespec="seconds"))

    # ---- state --------------------------------------------------------

    @property
    def is_empty(self) -> bool:
        return self.dark is None and self.flat is None

    @property
    def shape(self):
        reference = self.dark if self.dark is not None else self.flat
        return None if reference is None else reference.shape

    def matches(self, frame) -> bool:
        """Whether this correction applies to a frame of that geometry."""
        if self.is_empty:
            return False
        return np.asarray(frame).shape == self.shape

    def describe(self) -> dict:
        """What was applied, for the record beside the data."""
        return {
            "dark": self.dark is not None,
            "flat": self.flat is not None,
            "shape": list(self.shape) if self.shape else None,
            **self.metadata,
        }

    # ---- use ----------------------------------------------------------

    def apply(self, frame) -> np.ndarray:
        """Correct one raw frame. A frame of the wrong shape is returned as is.

        Refusing loudly here would abort an acquisition over a changed binning;
        returning the frame untouched keeps the data coming, and the caller
        checks matches() when it wants to say something about it.
        """
        raw = np.asarray(frame, dtype=np.float32)
        if self.is_empty or raw.shape != self.shape:
            return raw

        out = raw if self.dark is None else raw - self.dark

        if self.flat is not None:
            gain = self.flat if self.dark is None else self.flat - self.dark
            level = float(np.mean(gain))
            # A flat that averages to nothing was taken in the dark; dividing by
            # it would be noise amplification, not a correction.
            if abs(level) > MIN_FLAT_LEVEL:
                safe = np.where(np.abs(gain) < MIN_FLAT_LEVEL, np.float32(np.nan), gain)
                with np.errstate(invalid="ignore", divide="ignore"):
                    corrected = out / safe * level
                out = np.where(np.isfinite(corrected), corrected, out)

        return np.asarray(out, dtype=np.float32)

    # ---- persistence --------------------------------------------------

    def save(self, path: str) -> str:
        """Write dark, flat and metadata to a single .npz.

        A dark is worth keeping between sessions -- it takes minutes to acquire
        and changes only with the exposure or the sensor temperature.
        """
        import json

        arrays = {"metadata": np.asarray(json.dumps(self.metadata, default=str))}
        if self.dark is not None:
            arrays["dark"] = self.dark
        if self.flat is not None:
            arrays["flat"] = self.flat

        np.savez_compressed(path, **arrays)
        return path

    @classmethod
    def load(cls, path: str) -> "FieldCorrection":
        import json

        with np.load(path, allow_pickle=False) as data:
            dark = data["dark"] if "dark" in data else None
            flat = data["flat"] if "flat" in data else None
            try:
                metadata = json.loads(str(data["metadata"]))
            except (KeyError, ValueError):
                metadata = {}

        return cls(dark=dark, flat=flat, metadata=metadata)
