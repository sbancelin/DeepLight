"""The Raman chain: a spectrograph decides the wavelengths, a camera reads them.

Neither half knows about the other. This class owns the only thing that needs
both: turning a 2D camera frame into (wavelengths, intensities) by binning the
rows that fall on the slit image.

Swapping the camera means writing one CameraBackendBase; swapping the
spectrograph means writing one SpectrographBackendBase. Nothing here changes.
"""

from __future__ import annotations

import numpy as np

from ...config import CONFIG
from ..widgets.Log_Widget import logger
from .Camera_Manager import CameraBackendBase, CameraParameters
from .Spectrograph_Manager import (
    create_spectrograph,
    validate_spectrograph_contract,
)


class MockRamanCamera(CameraBackendBase):
    """Stand-in detector: a few Gaussian lines on a sloping background.

    Enough to exercise the whole Raman path, the wavelength axis and the plot
    without the bench.
    """

    def __init__(self, width: int = 1340, height: int = 100):
        super().__init__()
        self.width = int(width)
        self.height = int(height)
        self._rng = np.random.default_rng(0)

    def connect(self) -> None:
        self.connected = True
        logger.info("[RamanCamera] mock connected")

    def disconnect(self) -> None:
        self.connected = False

    def snap(self) -> np.ndarray:
        x = np.arange(self.width, dtype=np.float64)
        spectrum = 200.0 + 0.05 * x
        for centre, amp, width in ((0.28, 3000.0, 6.0),
                                   (0.46, 1500.0, 9.0),
                                   (0.71, 2200.0, 4.0)):
            spectrum += amp * np.exp(-0.5 * ((x - centre * self.width) / width) ** 2)

        # The slit image only lights up the middle rows, as on a real sensor.
        rows = np.arange(self.height, dtype=np.float64)
        profile = np.exp(-0.5 * ((rows - self.height / 2.0) / (self.height / 8.0)) ** 2)

        frame = np.outer(profile, spectrum)
        frame += self._rng.normal(0.0, 15.0, size=frame.shape)
        return frame.astype(np.float32)


class RamanSpectrometer:
    """Spectrograph + camera, exposing one spectrum."""

    def __init__(self, spectrograph=None, camera=None):
        self.spectrograph = spectrograph or create_spectrograph()
        validate_spectrograph_contract(self.spectrograph)

        self.camera = camera if camera is not None else MockRamanCamera()
        self.pixel_size_um = float(CONFIG.raman.camera_pixel_size_um)

    # ---------- lifecycle ----------
    def connect(self) -> None:
        if not self.spectrograph.connected:
            self.spectrograph.connect()
        if not getattr(self.camera, "connected", False):
            self.camera.connect()

    def disconnect(self) -> None:
        for device in (self.camera, self.spectrograph):
            try:
                device.disconnect()
            except Exception as e:
                logger.debug(f"[Raman] disconnecting {type(device).__name__} failed: {e}")

    # ---------- settings ----------
    def set_center_wavelength_nm(self, value_nm: float) -> None:
        self.spectrograph.set_center_wavelength_nm(float(value_nm))

    def set_grating(self, index: int) -> None:
        self.spectrograph.set_grating(int(index))

    def set_exposure_ms(self, exposure_ms: float) -> None:
        params = self.camera.get_parameters()
        params.exposure_ms = float(exposure_ms)
        self.camera.set_parameters(params)

    # ---------- acquisition ----------
    def acquire_spectrum(self, exposure_ms: float | None = None):
        """Return (wavelengths_nm, intensities) for one exposure.

        The camera frame is binned along the slit, which is the sensor's *rows*:
        a spectrograph disperses across the columns, so summing rows is what
        turns an image into a spectrum. A 1D camera is taken as already binned.
        """
        self.connect()

        if exposure_ms is not None:
            self.set_exposure_ms(exposure_ms)

        frame = np.asarray(self.camera.snap(), dtype=np.float64)

        if frame.ndim == 1:
            intensities = frame
        elif frame.ndim == 2:
            intensities = self._bin_slit_rows(frame)
        else:
            raise ValueError(f"Unexpected camera frame with shape {frame.shape}")

        wavelengths = self.spectrograph.wavelength_axis_nm(
            n_pixels=intensities.size,
            pixel_size_um=self.pixel_size_um,
        )
        return wavelengths, intensities

    def _bin_slit_rows(self, frame: np.ndarray) -> np.ndarray:
        """Sum the sensor rows that carry signal.

        A window keeps the read noise of the unlit rows out of the spectrum;
        raman.bin_rows = 0 means "use the whole height".
        """
        height = frame.shape[0]
        bin_rows = int(CONFIG.raman.get("bin_rows", 0) or 0)

        if bin_rows <= 0 or bin_rows >= height:
            return frame.sum(axis=0)

        centre = height // 2
        half = max(1, bin_rows // 2)
        lo = max(0, centre - half)
        hi = min(height, centre + half)
        return frame[lo:hi, :].sum(axis=0)
