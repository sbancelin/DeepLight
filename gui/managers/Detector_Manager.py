from __future__ import annotations

import hashlib
import numpy as np
from PySide6.QtCore import QObject

MOCK_SIGNAL_MAX = 255.0

class MockDetectorManager(QObject):
    """
    Générateur mock de signaux détecteurs.

    Il génère une frame 2D synthétique complète, puis la sert
    comme un flux 1D raster compatible avec le pipeline d'acquisition.
    """
    def __init__(self, parent=None):
        super().__init__(parent)

        self.random_mode = False
        self.pattern_type = "mixed"   # "rings", "grid", "dots", "mixed"
        self.high_level = 200.0
        self.low_level = 10.0
        self.noise_on = 0.0
        self.noise_off = 0.0

        self.dim_image_x = 256
        self.dim_image_y = 256
        self.samples_per_pixel = 1

        self.dim_fast = self.dim_image_x
        self.dim_slow = self.dim_image_y
        self.fast_axis_is_image_x = True
        self.bidirectional = False
        self.turnback_offset_px = 0

        self._frame_signal_cache: dict[str, np.ndarray] = {}
        self._sample_cursor = 0

    # ------------------------------------------------------------------
    # API publique
    # ------------------------------------------------------------------

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
    
    def acquire_integrated_scalar(self, channel: str, dwell_time_s: float, source_kind: str = "analog_integrating") -> float:
        """Retourne un scalaire intégré mock pour un canal et un dwell donnés."""
        if source_kind != "analog_integrating":
            raise ValueError(f"Unsupported source_kind: {source_kind}")

        return self._mock_integrated_scalar(channel, dwell_time_s)
        
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
        turnback_offset_px: int = 0,
    ):
        """
        Configure le format technique attendu par l'acquisition.

        - dim_x / dim_y : dimensions image logiques
        - dim_fast / dim_slow : dimensions raster utiles acquises
        - fast_axis_is_image_x :
            True  -> fast -> X image
            False -> fast -> Y image
        """
        self.dim_image_x = int(dim_image_x)
        self.dim_image_y = int(dim_image_y)
        self.samples_per_pixel = max(1, int(samples_per_pixel))

        self.bidirectional = bool(bidirectional)
        self.turnback_offset_px = max(0, int(turnback_offset_px))

        self.dim_fast = int(dim_fast) if dim_fast is not None else self.dim_image_x
        self.dim_slow = int(dim_slow) if dim_slow is not None else self.dim_image_y
        self.fast_axis_is_image_x = bool(fast_axis_is_image_x)

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
        Configuration interne du mock uniquement.
        À appeler depuis le mock lui-même ou manuellement pendant les tests.
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
        Prépare une nouvelle frame complète et remet le curseur de lecture à zéro.
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
        Renvoie le prochain bloc de samples dans le flux raster de la frame.
        Si la frame est épuisée, le bloc est complété au niveau bas.
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
        Transforme une image logique frame[y, x] en flux 1D dans l'ordre
        temporel réellement acquis.

        - fast_axis_is_image_x = True  -> fast = X image
        - fast_axis_is_image_x = False -> fast = Y image
        - si bidirectional=True, une ligne slow sur deux est lue en sens inverse
        """
        flat = np.empty((self.dim_fast * self.dim_slow,), dtype=np.float64)

        k = 0
        for slow_idx in range(self.dim_slow):
            if self.bidirectional and (slow_idx % 2 == 1):
                fast_range = range(self.dim_fast - 1, -1, -1)
            else:
                fast_range = range(self.dim_fast)

            for fast_idx in fast_range:
                if self.fast_axis_is_image_x:
                    x_img = fast_idx
                    y_img = slow_idx
                else:
                    x_img = slow_idx
                    y_img = fast_idx

                flat[k] = frame[y_img, x_img]
                k += 1

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

        if self.random_mode:
            n_pixels_stream = self.dim_fast * self.dim_slow
            n_samples = n_pixels_stream * self.samples_per_pixel
            return rng.integers(0, int(MOCK_SIGNAL_MAX), size=n_samples).astype(np.float64)

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

        frame = np.clip(frame, 0.0, MOCK_SIGNAL_MAX)

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