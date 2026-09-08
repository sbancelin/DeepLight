from __future__ import annotations

import hashlib
import numpy as np

from .Detector_Manager_Base import DetectorManagerBase

MOCK_ANALOG_MAX_V = 10.0
MOCK_DIGITAL_MAX_COUNTS = 65535.0

class MockDetectorManager(DetectorManagerBase):
    """
    Mock generator of detector signals.

    It builds a complete synthetic 2D frame, then serves it as a 1D raster
    stream compatible with the acquisition pipeline.
    """
    def __init__(self, parent=None):
        super().__init__(parent)

        self.random_mode = False
        self.pattern_type = "mixed"   # "rings", "grid", "dots", "mixed"
        self.high_level = 8.0
        self.low_level = 0.5
        self.noise_on = 0.0
        self.noise_off = 0.0

        self.dim_image_x = 256
        self.dim_image_y = 256
        self.samples_per_pixel = 1

        self.dim_fast = self.dim_image_x
        self.dim_slow = self.dim_image_y
        self.fast_axis_is_image_x = True
        self.bidirectional = False

        self.leading_skip_px = 0
        self.trailing_skip_px = 0

        self._frame_signal_cache: dict[str, np.ndarray] = {}
        self._sample_cursor = 0

    # ------------------------------------------------------------------
    # API publique
    # ------------------------------------------------------------------

    def get_frame_signal(self, channel: str) -> np.ndarray | None:
        """Return the prepared raster signal of `channel`, or None."""
        return self._frame_signal_cache.get(channel)

    def _mock_integrated_scalar(self, channel: str, dwell_time_s: float) -> float:
        if not self._frame_signal_cache or channel not in self._frame_signal_cache:
            self.start_frame(channels=[channel])

        signal = self._frame_signal_cache[channel]

        if self._sample_cursor >= signal.size:
            return float(self.low_level)

        i = int(self._sample_cursor)
        value = float(signal[i])

        self._sample_cursor += 1
        return value
    
    def _mock_digital_counts(self, channel: str, dwell_time_s: float) -> float:
        """
        Mock photon counting matching the H16721 PMT + C8855.
        Saturation is set by the linearity of the H16721 (~1.5 MHz).
        """

        rng = np.random.default_rng(
            self._seed_from_key(f"{channel}|{self._sample_cursor}")
        )

        # plafond réaliste du H16721
        max_counts = int(1.5e6 * max(dwell_time_s, 1e-9))

        counts = rng.integers(0, max_counts + 1)

        return float(counts)
        
    def _digital_max_counts_for_dwell(self, dwell_time_s: float) -> float:
        """
        Mock ceiling consistent with the PMT/counter in use.
        Saturation is kept realistic around ~16721 counts per pixel, with a
        16-bit safety bound where needed.
        """
        target_max = 16721.0

        # si on veut plus tard dépendre du dwell, on pourra le faire ici.
        # pour l'instant on garde le comportement demandé: microscope mock
        # plafonné autour de 16721 counts/pixel.
        return min(target_max, 65535.0)
    
    def acquire_integrated_scalar(self, channel: str, dwell_time_s: float, source_kind: str = "analog_integrating") -> float:
        """
        Return a mock integrated scalar for a given channel and dwell.
        """

        if source_kind == "analog_integrating":
            return self._mock_integrated_scalar(channel, dwell_time_s)

        if source_kind == "photon_counter":
            return self._mock_digital_counts(channel, dwell_time_s)

        raise ValueError(f"Unsupported source_kind: {source_kind}")
        
    def _reset_frame_cache(self):
        self._frame_signal_cache.clear()
        self._sample_cursor = 0
    
    def set_frame_shape(
        self,
        dim_image_x: int,
        dim_image_y: int,
        samples_per_pixel: int = 1,
        dim_fast: int | None = None,
        dim_slow: int | None = None,
        fast_axis_is_image_x: bool = True,
        bidirectional: bool = False,
        leading_skip_px: int = 0,
        trailing_skip_px: int = 0,
    ):
        """
        Configure the technical layout the acquisition expects.

        - dim_x / dim_y: logical image dimensions
        - dim_fast / dim_slow: useful raster dimensions actually acquired
        - fast_axis_is_image_x:
            True  -> fast -> image X
            False -> fast -> image Y
        """
        self.dim_image_x = int(dim_image_x)
        self.dim_image_y = int(dim_image_y)
        self.samples_per_pixel = max(1, int(samples_per_pixel))

        self.bidirectional = bool(bidirectional)

        self.dim_fast = int(dim_fast) if dim_fast is not None else self.dim_image_x
        self.dim_slow = int(dim_slow) if dim_slow is not None else self.dim_image_y
        self.fast_axis_is_image_x = bool(fast_axis_is_image_x)

        self.leading_skip_px = max(0, int(leading_skip_px))
        self.trailing_skip_px = max(0, int(trailing_skip_px))

        self._reset_frame_cache()

    def configure_mock(
        self,
        *,
        random_mode: bool | None = None,
        pattern_type: str | None = None,
        high_level: float | None = None,
        low_level: float | None = None,
        noise_on: float | None = None,
        noise_off: float | None = None,
    ):
        """
        Internal configuration of the mock only.
        To be called from the mock itself, or by hand during tests.
        """
        if random_mode is not None:
            self.random_mode = bool(random_mode)
        if pattern_type is not None:
            self.pattern_type = str(pattern_type)
        if high_level is not None:
            self.high_level = float(high_level)
        if low_level is not None:
            self.low_level = float(low_level)
        if noise_on is not None:
            self.noise_on = float(noise_on)
        if noise_off is not None:
            self.noise_off = float(noise_off)

        self._reset_frame_cache()

    def start_frame(
        self,
        channels: list[str],
        rep_index: int = 0,
        axis3_index: int = 0,
        axis4_index: int = 0,
        axis3_value: float | None = None,
        axis4_value: float | None = None,
    ):
        """
        Prepare a new complete frame and reset the read cursor to zero.
        """
        self._reset_frame_cache()

        for ch in channels:
            signal = self._build_frame_signal(
                channel=ch,
                rep_index=rep_index,
                axis3_index=axis3_index,
                axis4_index=axis4_index,
                axis3_value=axis3_value,
                axis4_value=axis4_value,
            )
            self._frame_signal_cache[ch] = signal

    def read_samples(
        self,
        n_samples: int,
        channels: list[str],
        rep_index: int = 0,
        axis3_index: int = 0,
        axis4_index: int = 0,
        axis3_value: float | None = None,
        axis4_value: float | None = None,
    ) -> dict[str, np.ndarray]:
        """
        Return the next block of samples in the frame's raster stream.
        Once the frame is exhausted, the block is padded at the low level.
        """
        n_samples = max(1, int(n_samples))

        if not self._frame_signal_cache:
            self.start_frame(
                channels=channels,
                rep_index=rep_index,
                axis3_index=axis3_index,
                axis4_index=axis4_index,
                axis3_value=axis3_value,
                axis4_value=axis4_value,
            )

        data: dict[str, np.ndarray] = {}

        for ch in channels:
            signal = self._frame_signal_cache[ch]
            start = self._sample_cursor
            stop = min(start + n_samples, signal.size)

            out = signal[start:stop]

            if out.size < n_samples:
                pad = np.full(n_samples - out.size, self.low_level, dtype=np.float64)
                out = np.concatenate([out, pad])

            data[ch] = out.astype(np.float64, copy=False)

        self._sample_cursor += n_samples
        return data

    # ------------------------------------------------------------------
    # Génération d'une frame
    # ------------------------------------------------------------------

    def _frame_to_raster_stream(self, frame: np.ndarray) -> np.ndarray:
        """
        Turn a logical image frame[y, x] into a 1D stream in the order in which
        it is really acquired in time.

        The raster stream can be wider than the logical image because of the
        overscan. In that case:
        - the useful pixels are inserted between leading_skip_px and trailing_skip_px
        - the overscan regions are filled at the low level
        - if bidirectional=True, every other slow line is read backwards over the
        full technical stream
        """
        flat = np.full((self.dim_fast * self.dim_slow,), self.low_level, dtype=np.float64)

        if self.fast_axis_is_image_x:
            useful_fast = int(self.dim_image_x)
            useful_slow = int(self.dim_image_y)
        else:
            useful_fast = int(self.dim_image_y)
            useful_slow = int(self.dim_image_x)

        if useful_slow != int(self.dim_slow):
            raise ValueError(
                f"Incompatible frame geometry: useful_slow={useful_slow}, dim_slow={self.dim_slow}"
            )

        expected_fast = self.leading_skip_px + useful_fast + self.trailing_skip_px
        if expected_fast != int(self.dim_fast):
            raise ValueError(
                "Incompatible raster geometry: "
                f"dim_fast={self.dim_fast}, "
                f"leading_skip_px={self.leading_skip_px}, "
                f"useful_fast={useful_fast}, "
                f"trailing_skip_px={self.trailing_skip_px}"
            )

        k = 0
        for slow_idx in range(self.dim_slow):
            line = np.full((self.dim_fast,), self.low_level, dtype=np.float64)

            if self.fast_axis_is_image_x:
                line[
                    self.leading_skip_px : self.leading_skip_px + self.dim_image_x
                ] = frame[slow_idx, :]
            else:
                line[
                    self.leading_skip_px : self.leading_skip_px + self.dim_image_y
                ] = frame[:, slow_idx]

            if self.bidirectional and (slow_idx % 2 == 1):
                line = line[::-1]

            flat[k : k + self.dim_fast] = line
            k += self.dim_fast

        return flat

    def _build_frame_signal(
        self,
        *,
        channel: str,
        rep_index: int,
        axis3_index: int,
        axis4_index: int,
        axis3_value: float | None,
        axis4_value: float | None,
    ) -> np.ndarray:
        rng = np.random.default_rng(
            self._seed_from_key(
                f"{channel}|{rep_index}|{axis3_index}|{axis4_index}|{axis3_value}|{axis4_value}|{self.pattern_type}|{self.random_mode}"
            )
        )

        is_digital = str(channel) in ("Ch 0", "Ch 1")

        if is_digital:
            max_counts = self._digital_max_counts_for_dwell(dwell_time_s=0.0)
            high_counts = 0.85 * max_counts
            low_counts = 0.02 * max_counts

            if self.random_mode:
                n_pixels_stream = self.dim_fast * self.dim_slow
                n_samples = n_pixels_stream * self.samples_per_pixel
                return rng.uniform(0.0, max_counts, size=n_samples).astype(np.float64)

            mask = self._build_pattern_mask(
                width=self.dim_image_x,
                height=self.dim_image_y,
                pattern_type=self.pattern_type,
                rng=rng,
            )

            frame = np.empty((self.dim_image_y, self.dim_image_x), dtype=np.float64)

            on_pixels = mask
            off_pixels = ~mask

            if np.any(on_pixels):
                sigma_on = max(5.0, 0.03 * high_counts)
                vals = rng.normal(loc=high_counts, scale=sigma_on, size=np.count_nonzero(on_pixels))
                frame[on_pixels] = vals

            if np.any(off_pixels):
                sigma_off = max(2.0, 0.01 * max_counts)
                vals = rng.normal(loc=low_counts, scale=sigma_off, size=np.count_nonzero(off_pixels))
                frame[off_pixels] = vals

            frame = np.clip(np.rint(frame), 0.0, max_counts)

            flat_pixels = self._frame_to_raster_stream(frame)

            if self.samples_per_pixel > 1:
                # en digital, chaque sous-sample représente un count-gate;
                # on répartit le pixel sur les sous-samples pour que la somme
                # au FrameBuilder redonne le count du pixel.
                repeated = np.repeat(flat_pixels[:, None] / float(self.samples_per_pixel), self.samples_per_pixel, axis=1)
                flat_pixels = repeated.reshape(-1)

            return flat_pixels.astype(np.float64, copy=False)

        # -------- analog channels --------
        if self.random_mode:
            n_pixels_stream = self.dim_fast * self.dim_slow
            n_samples = n_pixels_stream * self.samples_per_pixel
            return rng.uniform(0.0, MOCK_ANALOG_MAX_V, size=n_samples).astype(np.float64)

        mask = self._build_pattern_mask(
            width=self.dim_image_x,
            height=self.dim_image_y,
            pattern_type=self.pattern_type,
            rng=rng,
        )

        frame = np.empty((self.dim_image_y, self.dim_image_x), dtype=np.float64)

        on_pixels = mask
        off_pixels = ~mask

        if np.any(on_pixels):
            frame[on_pixels] = self.high_level + rng.uniform(
                -self.noise_on, self.noise_on, size=np.count_nonzero(on_pixels)
            )

        if np.any(off_pixels):
            frame[off_pixels] = self.low_level + rng.uniform(
                -self.noise_off, self.noise_off, size=np.count_nonzero(off_pixels)
            )

        frame = np.clip(frame, 0.0, MOCK_ANALOG_MAX_V)

        flat_pixels = self._frame_to_raster_stream(frame)

        if self.samples_per_pixel > 1:
            flat_pixels = np.repeat(flat_pixels, self.samples_per_pixel)

        return flat_pixels.astype(np.float64, copy=False)

    def _build_pattern_mask(
        self,
        *,
        width: int,
        height: int,
        pattern_type: str,
        rng: np.random.Generator,
    ) -> np.ndarray:
        if pattern_type == "rings":
            return self._make_rings(width, height)
        if pattern_type == "grid":
            return self._make_grid(width, height, rng)
        if pattern_type == "dots":
            return self._make_dots(width, height, rng)
        if pattern_type == "vertical_bars":
            return self._make_vertical_bars(width, height)
        if pattern_type == "mixed":
            return (
                self._make_rings(width, height)
                | self._make_grid(width, height, rng)
                | self._make_dots(width, height, rng)
            )
        return self._make_rings(width, height)

    # ------------------------------------------------------------------
    # Motifs
    # ------------------------------------------------------------------

    def _make_vertical_bars(self, width: int, height: int) -> np.ndarray:
        yy, xx = np.mgrid[0:height, 0:width]
        return (xx % 16) < 3

    def _make_rings(self, width: int, height: int) -> np.ndarray:
        yy, xx = np.mgrid[0:height, 0:width]
        cx = width / 2.0
        cy = height / 2.0
        r = np.sqrt((xx - cx) ** 2 + (yy - cy) ** 2)

        period = max(14.0, min(width, height) / 10.0)
        thickness = max(2.0, period / 5.0)

        return (r % period) < thickness

    def _make_grid(self, width: int, height: int, rng: np.random.Generator) -> np.ndarray:
        yy, xx = np.mgrid[0:height, 0:width]

        angle = rng.uniform(-0.35, 0.35)
        c = np.cos(angle)
        s = np.sin(angle)

        xr = c * xx - s * yy
        yr = s * xx + c * yy

        pitch_x = int(rng.integers(20, 40))
        pitch_y = int(rng.integers(20, 40))
        thick_x = int(rng.integers(2, 4))
        thick_y = int(rng.integers(2, 4))

        return (np.mod(xr, pitch_x) < thick_x) | (np.mod(yr, pitch_y) < thick_y)

    def _make_dots(self, width: int, height: int, rng: np.random.Generator) -> np.ndarray:
        yy, xx = np.mgrid[0:height, 0:width]
        mask = np.zeros((height, width), dtype=bool)

        n_dots = max(12, (width * height) // 8000)

        for _ in range(n_dots):
            cx = rng.uniform(0, width)
            cy = rng.uniform(0, height)
            radius = rng.uniform(3, 8)
            mask |= ((xx - cx) ** 2 + (yy - cy) ** 2) <= radius ** 2

        return mask

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _seed_from_key(key: str) -> int:
        digest = hashlib.sha256(key.encode("utf-8")).digest()
        return int.from_bytes(digest[:8], "little", signed=False)