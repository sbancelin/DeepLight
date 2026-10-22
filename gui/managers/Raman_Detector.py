"""The Raman detector: a camera read as a spectrometer.

A spectrograph spreads the spectrum along the sensor's columns, and the slit
image covers a band of rows. A spectrum is that band summed column by column.
Done on the chip -- vertical binning -- the band is read out as a single row,
with one read-noise contribution per column instead of one per pixel. So the
detector bins on the chip whenever the camera accepts it, and only falls back
to summing rows in software when it refuses.

RamanDetectorBase extends CameraBackendBase: the Raman detector is still a
DeepLight camera -- snap() gives the full frame, which is what one looks at to
find the slit image -- with a spectrum on top: configure_spectrum(),
acquire_spectrum(), and how many spectral pixels a spectrum has.

Backends
--------
"mock"   a simulated detector, so the whole Raman path runs without the bench;
"picam"  a Princeton Instruments camera through PICam -- the LANSIS is declared
         in Teledyne's picam.h (LansisA261BR, A261BRX, D424B, D424BX), and the
         Kuro already runs through the same library.

Another SDK (PVCam, say) is one more RamanDetectorBase subclass and one more
name in create_raman_detector(); nothing above this module changes.
"""

from __future__ import annotations

import time
from ctypes import POINTER, byref

import numpy as np

from ...config import CONFIG
from ..widgets.Log_Widget import logger
from .Camera_Manager import CameraBackendBase


class RamanDetectorBase(CameraBackendBase):
    """A camera that also delivers spectra.

    Rows are sensor rows (unbinned). row_count = 0 means the full height,
    which is the right default until the slit image has been located.
    """

    # ---- geometry ------------------------------------------------------
    @property
    def spectral_pixels(self) -> int:
        """Length of a spectrum: the sensor columns, along the dispersion."""
        raise NotImplementedError

    @property
    def sensor_rows(self) -> int:
        raise NotImplementedError

    # ---- spectra -------------------------------------------------------
    def configure_spectrum(self, exposure_ms: float, row_start: int = 0, row_count: int = 0) -> None:
        """Exposure and the band of rows summed into a spectrum."""
        raise NotImplementedError

    def acquire_spectrum(self) -> np.ndarray:
        """One exposure, as a 1D float32 array of spectral_pixels values."""
        raise NotImplementedError

    # ---- state ---------------------------------------------------------
    def sensor_temperature_c(self) -> float | None:
        """Sensor temperature, or None where the camera does not report one."""
        return None

    def describe(self) -> dict:
        """What the detector was doing, for the record beside every spectrum."""
        return {}


def row_window(row_start: int, row_count: int, sensor_rows: int) -> tuple[int, int]:
    """Clamp a (first row, row count) request to the sensor; 0 rows = full height."""
    sensor_rows = max(1, int(sensor_rows))
    start = min(max(0, int(row_start)), sensor_rows - 1)
    count = int(row_count)
    if count <= 0:
        return 0, sensor_rows
    return start, max(1, min(count, sensor_rows - start))


# ======================================================================
# Mock
# ======================================================================

class MockRamanDetector(RamanDetectorBase):
    """A simulated spectroscopy camera.

    Illustrative only. The scene is a few Raman bands of a sample excited at
    785 nm, placed in *wavelength* and mapped onto the columns with a
    dispersion that follows the mock spectrograph's grating and centre -- so
    turning the grating or moving the centre visibly moves the spectrum, which
    is what the window's controls need to be seen working. Counts grow with
    the exposure and carry shot noise and read noise; none of the numbers
    describes the real LANSIS.
    """

    #: Exposures longer than this are not waited in full in simulation.
    _MAX_SIMULATED_WAIT_S = 5.0

    #: Bands of the simulated sample: (Raman shift in cm-1, relative height, width in cm-1).
    _BANDS = ((1001.0, 1.00, 6.0), (1450.0, 0.55, 18.0), (1660.0, 0.70, 22.0))
    _EXCITATION_NM = 785.0

    def __init__(self, width: int = 1024, height: int = 128):
        super().__init__()
        self.width = int(width)
        self.height = int(height)
        self._exposure_ms = 100.0
        self._rows = (0, self.height)
        self._grooves_per_mm = 600.0
        self._center_nm = 870.0
        self._rng = np.random.default_rng()

    # ---- lifecycle -----------------------------------------------------
    def connect(self) -> None:
        self.connected = True
        logger.info("[RamanDetector] mock connected")

    def disconnect(self) -> None:
        self.connected = False

    # ---- geometry ------------------------------------------------------
    @property
    def spectral_pixels(self) -> int:
        return self.width

    @property
    def sensor_rows(self) -> int:
        return self.height

    def follow_spectrograph(self, grooves_per_mm: float | None, center_nm: float | None) -> None:
        """Simulation only: where the mock spectrograph sends the light."""
        if grooves_per_mm:
            self._grooves_per_mm = float(grooves_per_mm)
        if center_nm is not None:
            self._center_nm = float(center_nm)

    # ---- spectra -------------------------------------------------------
    def configure_spectrum(self, exposure_ms: float, row_start: int = 0, row_count: int = 0) -> None:
        self._exposure_ms = max(0.0, float(exposure_ms))
        self._rows = row_window(row_start, row_count, self.height)

    def _scene_counts_per_s(self) -> np.ndarray:
        """Signal per column per second, for the band of rows read."""
        nm_per_pixel = 81.0 / max(1.0, self._grooves_per_mm)
        wavelengths = self._center_nm + (np.arange(self.width) - (self.width - 1) / 2.0) * nm_per_pixel
        shift = 1.0e7 / self._EXCITATION_NM - 1.0e7 / wavelengths      # cm-1

        signal = 300.0 + 0.15 * np.clip(shift, 0.0, None)              # fluorescence-like background
        for centre, height, width in self._BANDS:
            signal = signal + 4000.0 * height * np.exp(-0.5 * ((shift - centre) / width) ** 2)

        # Fraction of the slit image falling in the rows read: a centred band.
        rows = np.arange(self.height)
        profile = np.exp(-0.5 * ((rows - self.height / 2.0) / (self.height / 8.0)) ** 2)
        start, count = self._rows
        fraction = profile[start:start + count].sum() / profile.sum()
        return signal * fraction

    def acquire_spectrum(self) -> np.ndarray:
        if not self.connected:
            self.connect()
        time.sleep(min(self._exposure_ms / 1000.0, self._MAX_SIMULATED_WAIT_S))

        expected = self._scene_counts_per_s() * (self._exposure_ms / 1000.0)
        shot = self._rng.normal(0.0, np.sqrt(np.clip(expected, 1.0, None)))
        read = self._rng.normal(0.0, 6.0, size=self.width)   # one read per column: binned on chip
        return np.clip(expected + shot + read, 0.0, None).astype(np.float32)

    # ---- the plain camera contract -------------------------------------
    def snap(self) -> np.ndarray:
        """Full frame: the spectrum spread over the slit image's rows."""
        spectrum = self._scene_counts_per_s() * (self._exposure_ms / 1000.0)
        rows = np.arange(self.height)
        profile = np.exp(-0.5 * ((rows - self.height / 2.0) / (self.height / 8.0)) ** 2)
        frame = np.outer(profile / profile.sum(), spectrum)
        frame += self._rng.normal(0.0, 6.0, size=frame.shape)
        return np.clip(frame, 0.0, None).astype(np.float32)

    def describe(self) -> dict:
        start, count = self._rows
        return {
            "backend": "mock",
            "spectral_pixels": self.width,
            "sensor_rows": self.height,
            "rows": [int(start), int(count)],
            "binning": "on-chip (simulated)",
            "exposure_ms": float(self._exposure_ms),
            "temperature_c": None,
        }


# ======================================================================
# PICam
# ======================================================================

def _picam_raman_class():
    """Build the PICam detector class on demand.

    PiCam_Manager locates Picam.dll when an instance is created, not at import,
    but importing it here lazily still keeps a machine without the Raman camera
    from paying for it.
    """
    from .PiCam_Manager import (
        LANSIS_MODEL_IDS,
        PiCamManager,
        PicamHandle,
        PicamRoi,
        PicamRois,
        piflt,
        piint,
    )

    #: picam.h: PicamParameter_SensorTemperatureReading = PI_V(FloatingPoint, None, 15)
    #: = (PicamConstraintType_None << 24) + (PicamValueType_FloatingPoint << 16) + 15.
    sensor_temperature_reading = (1 << 24) + (2 << 16) + 15
    #: picam.h: PicamParameter_Rois = PI_V(Rois, Rois, 37); the value already used by PiCamManager.
    from .PiCam_Manager import PicamParameter_Rois as parameter_rois

    class PiCamRamanDetector(PiCamManager, RamanDetectorBase):
        """A Princeton Instruments camera behind the spectrograph, through PICam.

        UNVERIFIED AGAINST HARDWARE. Built on the PICam calls DeepLight already
        uses for the Kuro, with one addition: the ROI spans the full width and
        the chosen band of rows, and bins those rows on the chip (y_binning =
        row count). Should the camera refuse that binning, the ROI is applied
        unbinned and the rows are summed here instead -- slower and noisier,
        but a spectrum either way, and the log says which.
        """

        def __init__(self, serial_number: str | None = None):
            PiCamManager.__init__(
                self,
                serial_number=serial_number,
                preferred_models=LANSIS_MODEL_IDS,
            )
            self._spectrum_params = None
            self._onchip_binning = True
            self._rows = (0, 0)

        # ---- geometry --------------------------------------------------
        def _sensor_shape(self) -> tuple[int, int]:
            self.connect()
            if self._cached_sensor_shape is None:
                self._cached_sensor_shape = self._get_sensor_shape()
            return self._cached_sensor_shape

        @property
        def spectral_pixels(self) -> int:
            return int(self._sensor_shape()[1])

        @property
        def sensor_rows(self) -> int:
            return int(self._sensor_shape()[0])

        # ---- spectra ---------------------------------------------------
        def configure_spectrum(self, exposure_ms: float, row_start: int = 0, row_count: int = 0) -> None:
            sensor_h, _ = self._sensor_shape()
            self._rows = row_window(row_start, row_count, sensor_h)
            params = {"exposure_ms": float(exposure_ms), "spectrum_rows": tuple(self._rows)}
            self.apply_parameters(params)
            self._spectrum_params = params

        def apply_parameters(self, params: dict | None):
            params = dict(params or {})
            try:
                super().apply_parameters(params)
            except RuntimeError as e:
                if "spectrum_rows" not in params or not self._onchip_binning:
                    raise
                logger.warning(
                    f"[RamanDetector] the camera refused on-chip vertical binning ({e}); "
                    "summing the rows in software instead."
                )
                self._onchip_binning = False
                super().apply_parameters(params)

        def _set_roi_and_binning(self, params: dict):
            if "spectrum_rows" not in params:
                # A plain snap of the full frame: the camera behaves as any other.
                return super()._set_roi_and_binning(params)

            sensor_h, sensor_w = self._sensor_shape()
            start, count = params["spectrum_rows"]

            roi = PicamRoi()
            roi.x = 0
            roi.width = int(sensor_w)
            roi.x_binning = 1
            roi.y = int(start)
            roi.height = int(count)
            roi.y_binning = int(count) if self._onchip_binning else 1

            roi_buf = (PicamRoi * 1)(roi)
            rois = PicamRois()
            rois.roi_array = roi_buf
            rois.roi_count = 1
            self._check(
                self.lib.Picam_SetParameterRoisValue(self.camera, piint(parameter_rois), byref(rois)),
                "Picam_SetParameterRoisValue(Rois, spectrum)",
            )
            del rois

            rows_out = 1 if self._onchip_binning else int(count)
            self._last_shape = (rows_out, int(sensor_w))
            self._last_binning = 1
            logger.debug(
                f"[RamanDetector] ROI rows {start}..{start + count - 1}, "
                f"{'on-chip' if self._onchip_binning else 'software'} binning "
                f"-> {rows_out}x{sensor_w}"
            )

        def acquire_spectrum(self) -> np.ndarray:
            if self._spectrum_params is None:
                raise RuntimeError("configure_spectrum() must be called before acquire_spectrum().")
            frame = np.asarray(self.snap(self._spectrum_params), dtype=np.float32)
            if frame.ndim == 2:
                frame = frame.sum(axis=0)
            return frame.astype(np.float32, copy=False)

        # ---- state -----------------------------------------------------
        def sensor_temperature_c(self) -> float | None:
            if not self.connected:
                return None
            try:
                getter = self.lib.Picam_GetParameterFloatingPointValue
                getter.argtypes = [PicamHandle, piint, POINTER(piflt)]
                getter.restype = piint
                value = piflt(0.0)
                self._check(
                    getter(self.camera, piint(sensor_temperature_reading), byref(value)),
                    "Picam_GetParameterFloatingPointValue(SensorTemperatureReading)",
                )
                return float(value.value)
            except Exception as e:
                logger.debug(f"[RamanDetector] temperature unavailable: {e}")
                return None

        def describe(self) -> dict:
            start, count = self._rows
            exposure = (self._spectrum_params or {}).get("exposure_ms")
            return {
                "backend": "picam",
                "camera": dict(getattr(self, "opened_camera", {}) or {}),
                "spectral_pixels": self.spectral_pixels if self.connected else None,
                "sensor_rows": self.sensor_rows if self.connected else None,
                "rows": [int(start), int(count)],
                "binning": "on-chip" if self._onchip_binning else "software",
                "exposure_ms": None if exposure is None else float(exposure),
                "temperature_c": self.sensor_temperature_c(),
            }

    return PiCamRamanDetector


# ======================================================================
# Factory and contract
# ======================================================================

def create_raman_detector(name: str | None = None) -> RamanDetectorBase:
    """Build the Raman detector named in the configuration (raman.camera)."""
    backend = str(name or CONFIG.raman.get("camera", "mock") or "mock").strip().lower()

    if backend in ("mock", "none", ""):
        return MockRamanDetector()

    if backend in ("picam", "lansis"):
        detector_class = _picam_raman_class()
        return detector_class(serial_number=CONFIG.raman.get("camera_serial", "") or None)

    raise ValueError(f"Unknown Raman camera '{backend}'. Expected one of: mock, picam.")


def validate_raman_detector_contract(detector) -> None:
    """Check at runtime that a Raman detector honours the expected contract."""
    missing = []
    if not isinstance(detector, CameraBackendBase):
        missing.append("base:CameraBackendBase")
    for name in ("connect", "disconnect", "snap", "configure_spectrum",
                 "acquire_spectrum", "sensor_temperature_c", "describe"):
        if not callable(getattr(detector, name, None)):
            missing.append(f"method:{name}")
    for name in ("spectral_pixels", "sensor_rows"):
        if not hasattr(type(detector), name):
            missing.append(f"attr:{name}")

    if missing:
        raise TypeError(
            f"Raman detector {detector.__class__.__name__} does not satisfy the "
            f"Raman detector contract. Missing: {', '.join(missing)}"
        )
