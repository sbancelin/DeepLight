"""Contract of the detectors that the sample-scan path consumes.

Scope, deliberately narrow
--------------------------
This describes what a detector must offer so ``SampleDetectorIntegrator`` can
turn a prepared frame into one scalar per pixel: the geometry of the raster
stream, and a way to read the signal of a channel.

``PMTDigitalManager`` is *not* one of these and does not inherit it. It drives
the NI counters for the digital channels only -- one subsystem of the real
chain, whose analog half ``Nidaq_Microscope`` reads itself through its AI
tasks. Its ``read_pixel_chunk(n_samples)`` and the raster stream described here
are different operations on different layers, and forcing both behind a single
base would produce an abstraction every caller has to work around rather than
one they can rely on.

The seam that genuinely exists is this one: ``SampleDetectorIntegrator`` takes
any object satisfying the contract below.
"""

from __future__ import annotations

import numpy as np
from PySide6.QtCore import QObject


class DetectorManagerBase(QObject):
    """Common contract of the detector managers driven by the sample-scan path."""

    def __init__(self, parent=None):
        super().__init__(parent)

        # Geometry of the raster stream. The integrator needs these to map an
        # image pixel (x, y) onto an index in the 1D stream.
        self.dim_fast = 1
        self.dim_slow = 1
        self.fast_axis_is_image_x = True
        self.bidirectional = False
        self.samples_per_pixel = 1

        # Value returned when a pixel falls outside the acquired stream.
        self.low_level = 0.0

    def start_frame(
        self,
        channels,
        rep_index: int = 0,
        axis3_index: int = 0,
        axis4_index: int = 0,
        axis3_value=None,
        axis4_value=None,
    ):
        """Prepare a new frame for the given channels."""
        raise NotImplementedError

    def get_frame_signal(self, channel: str) -> np.ndarray | None:
        """Return the prepared 1D raster signal of ``channel``, or None.

        None means this detector does not precompute a whole frame, and the
        caller falls back to ``acquire_integrated_scalar()`` pixel by pixel.
        Exposing this replaces reaching into a private frame cache.
        """
        return None

    def acquire_integrated_scalar(
        self,
        channel: str,
        dwell_time_s: float,
        source_kind: str = "analog_integrating",
    ) -> float:
        """Return one integrated scalar for a channel, acquired on demand."""
        raise NotImplementedError


def validate_detector_contract(detector) -> None:
    """Check at runtime that a detector honours the expected minimal contract."""
    required_methods = (
        "start_frame",
        "get_frame_signal",
        "acquire_integrated_scalar",
    )
    required_attrs = (
        "dim_fast",
        "dim_slow",
        "fast_axis_is_image_x",
        "bidirectional",
        "samples_per_pixel",
        "low_level",
    )

    missing = []

    for name in required_methods:
        if not hasattr(detector, name) or not callable(getattr(detector, name)):
            missing.append(f"method:{name}")

    for name in required_attrs:
        if not hasattr(detector, name):
            missing.append(f"attr:{name}")

    if missing:
        raise TypeError(
            f"Detector {detector.__class__.__name__} does not satisfy detector contract. "
            f"Missing: {', '.join(missing)}"
        )
