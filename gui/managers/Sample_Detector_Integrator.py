from __future__ import annotations

import numpy as np
from typing import Dict, Iterable

from .Detector_Manager_Base import validate_detector_contract


class SampleDetectorIntegrator:
    """
    Minimal abstraction producing one scalar value per pixel.
    The default path is "analog_integrating".

    The detector it is given must satisfy DetectorManagerBase; this class is
    the only consumer of that contract, which is why the contract is scoped to
    exactly what is used here.
    """

    def __init__(self, detector_manager=None):
        if detector_manager is not None:
            validate_detector_contract(detector_manager)
        self.detector_manager = detector_manager
        self._frame_context: Dict = {}

    def start_frame(
        self,
        channels: Iterable[str],
        width: int,
        height: int,
        rep_index: int = 0,
        axis3_index: int = 0,
        axis4_index: int = 0,
        axis3_value=None,
        axis4_value=None,
    ):
        """Initialise the current frame context and prepare the detector if needed."""
        channels = list(channels)

        self._frame_context = {
            "channels": channels,
            "width": int(width),
            "height": int(height),
            "rep_index": int(rep_index),
            "axis3_index": int(axis3_index),
            "axis4_index": int(axis4_index),
            "axis3_value": axis3_value,
            "axis4_value": axis4_value,
        }

        dm = self.detector_manager
        if dm is not None and hasattr(dm, "start_frame"):
            dm.start_frame(
                channels=channels,
                rep_index=int(rep_index),
                axis3_index=int(axis3_index),
                axis4_index=int(axis4_index),
                axis3_value=axis3_value,
                axis4_value=axis4_value,
            )

    def acquire_scalar(
        self,
        channel: str,
        x_index: int,
        y_index: int,
        dwell_time_s: float,
        pixel_source_kind: str = "analog_integrating",
    ) -> float:
        """Return the scalar value of one pixel for a given channel."""
        if pixel_source_kind == "analog_integrating":
            return float(
                self._acquire_analog_integrated(
                    channel=channel,
                    x_index=int(x_index),
                    y_index=int(y_index),
                    dwell_time_s=float(dwell_time_s),
                )
            )

        raise ValueError(f"Unsupported pixel_source_kind: {pixel_source_kind}")

    def _pixel_to_stream_index(self, x_index: int, y_index: int) -> int:
        """Convert an image pixel (x, y) into an index in the 1D raster stream."""
        dm = self.detector_manager
        if dm is None:
            raise RuntimeError("No detector_manager attached")

        x = int(x_index)
        y = int(y_index)

        dim_fast = int(getattr(dm, "dim_fast", 1))
        dim_slow = int(getattr(dm, "dim_slow", 1))
        fast_axis_is_image_x = bool(getattr(dm, "fast_axis_is_image_x", True))
        bidirectional = bool(getattr(dm, "bidirectional", False))

        if fast_axis_is_image_x:
            fast_idx = x
            slow_idx = y
        else:
            fast_idx = y
            slow_idx = x

        if fast_idx < 0 or fast_idx >= dim_fast or slow_idx < 0 or slow_idx >= dim_slow:
            raise IndexError(
                f"Pixel out of bounds: x={x}, y={y}, dim_fast={dim_fast}, dim_slow={dim_slow}"
            )

        if bidirectional and (slow_idx % 2 == 1):
            fast_idx_stream = dim_fast - 1 - fast_idx
        else:
            fast_idx_stream = fast_idx

        return slow_idx * dim_fast + fast_idx_stream
    
    def _get_low_level(self) -> float:
        dm = self.detector_manager
        return float(getattr(dm, "low_level", 0.0)) if dm is not None else 0.0

    def _acquire_analog_integrated(self, channel: str, x_index: int, y_index: int, dwell_time_s: float) -> float:
        dm = self.detector_manager
        if dm is None:
            raise RuntimeError("No detector_manager attached")

        # Le détecteur a-t-il déjà préparé une frame complète (cf. start_frame) ?
        # Si oui on y lit directement le pixel ; get_frame_signal() renvoie None
        # pour un détecteur qui acquiert point par point.
        signal = dm.get_frame_signal(channel) if hasattr(dm, "get_frame_signal") else None
        if signal is not None:
            pixel_index = self._pixel_to_stream_index(x_index, y_index)

            spp = max(1, int(getattr(dm, "samples_per_pixel", 1)))
            start = pixel_index * spp
            stop = start + spp

            if start >= signal.size:
                return self._get_low_level()

            block = signal[start:stop]
            if block.size == 0:
                return self._get_low_level()

            return float(np.mean(block))

        # Fallback futur pour un vrai backend matériel
        return float(
            dm.acquire_integrated_scalar(
                channel=channel,
                dwell_time_s=dwell_time_s,
                source_kind="analog_integrating",
            )
        )