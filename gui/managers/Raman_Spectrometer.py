"""The Raman chain: a spectrograph places the wavelengths, a detector reads them.

Neither half knows about the other. RamanSpectrometer owns the three things
that need both:

* one lock, so a live spectrum, a snap, a mapping point and a grating move
  never reach the instruments at the same time -- two overlapping
  acquisitions on one camera handle leave it armed and unusable;
* the spectrograph setting (grating, centre), cached here because DeepLight is
  the only program moving the spectrograph while it runs (LightField must be
  closed: both cannot hold the instruments), which spares two serial round
  trips per spectrum during a map;
* the spectral axis -- detector pixels by default, wavelengths when a loaded
  calibration belongs to the current setting (see Spectral_Calibration).

What comes out is a RamanSpectrum: counts per detector column, the axis they
are labelled with, and what was done to get them. No processing beyond the
average of repeated exposures: analysis happens elsewhere, on the raw data.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass

import numpy as np

from ..widgets.Log_Widget import logger
from .Raman_Detector import (
    AcquisitionAborted,
    RamanDetectorBase,
    validate_raman_detector_contract,
)
from .Spectral_Calibration import (
    SpectralAxis,
    SpectralCalibration,
    spectral_axis,
)
from .Spectrograph_Manager import validate_spectrograph_contract


@dataclass
class RamanSpectrum:
    """One spectrum, as acquired."""

    values: np.ndarray          # float32 counts per detector column, mean of `averages` exposures
    axis: SpectralAxis
    exposure_ms: float
    averages: int
    acquired_at: float          # time.time()


class RamanSpectrometer:
    """Spectrograph + detector + calibration, exposing one spectrum at a time."""

    def __init__(self, spectrograph, detector: RamanDetectorBase,
                 calibration: SpectralCalibration | None = None):
        validate_spectrograph_contract(spectrograph)
        validate_raman_detector_contract(detector)

        self.spectrograph = spectrograph
        self.detector = detector

        self._lock = threading.RLock()
        self._calibration = calibration
        self._rows = (0, 0)
        self._configured = None          # (exposure_ms, rows) last sent to the detector
        self._setting = None             # cached spectrograph setting

    # ---------- lifecycle ----------
    def connect(self) -> None:
        with self._lock:
            if not self.spectrograph.connected:
                self.spectrograph.connect()
                self._setting = None
            if not getattr(self.detector, "connected", False):
                self.detector.connect()
                self._configured = None
            if self._setting is None:
                self._refresh_setting()

    def disconnect(self) -> None:
        with self._lock:
            for device in (self.detector, self.spectrograph):
                try:
                    device.disconnect()
                except Exception as e:
                    logger.debug(f"[Raman] disconnecting {type(device).__name__} failed: {e}")
            self._configured = None
            self._setting = None

    # ---------- spectrograph ----------
    def _refresh_setting(self) -> dict:
        """Read the setting back from the instrument, the one time it is needed."""
        self._setting = dict(self.spectrograph.describe())
        follow = getattr(self.detector, "follow_spectrograph", None)
        if callable(follow):             # simulation only: the mock follows the mock
            follow(self._setting.get("grooves_per_mm"), self._setting.get("center_nm"))
        return dict(self._setting)

    def gratings(self) -> list[dict]:
        with self._lock:
            self.connect()
            return list(self.spectrograph.list_gratings())

    def setting(self) -> dict:
        """Grating, groove density, blaze, centre and slit, as last read."""
        with self._lock:
            self.connect()
            return dict(self._setting)

    def set_grating(self, index: int) -> dict:
        """Turn the turret. Blocking: a turret takes seconds."""
        with self._lock:
            self.connect()
            t0 = time.perf_counter()
            self.spectrograph.set_grating(int(index))
            setting = self._refresh_setting()
            logger.notice(
                f"[Raman] grating {setting.get('grating')} "
                f"({setting.get('grooves_per_mm')} l/mm) in {time.perf_counter() - t0:.1f} s"
            )
            return setting

    def set_center_wavelength_nm(self, value_nm: float) -> dict:
        """Rotate the grating to this centre wavelength. Blocking."""
        with self._lock:
            self.connect()
            self.spectrograph.set_center_wavelength_nm(float(value_nm))
            setting = self._refresh_setting()
            logger.notice(f"[Raman] centre wavelength {setting.get('center_nm'):.3f} nm")
            return setting

    # ---------- calibration ----------
    @property
    def calibration(self) -> SpectralCalibration | None:
        return self._calibration

    def set_calibration(self, calibration: SpectralCalibration | None) -> None:
        with self._lock:
            self._calibration = calibration

    def load_calibration(self, path: str) -> SpectralCalibration:
        """Read a calibration file for the current detector and setting.

        Checked against the detector's pixel count straight away: a table made
        for another camera or another binning is refused here, not discovered
        later as a silently wrong axis. Returned, not installed: the caller
        decides when it takes effect.
        """
        with self._lock:
            self.connect()
            setting = self._setting or {}
            return SpectralCalibration.from_file(
                path,
                grating=setting.get("grating"),
                center_nm=setting.get("center_nm"),
                grooves_per_mm=setting.get("grooves_per_mm"),
                n_pixels=int(self.detector.spectral_pixels),
            )

    def spectral_axis(self) -> SpectralAxis:
        """The axis spectra are labelled with at the current setting."""
        with self._lock:
            self.connect()
            setting = self._setting or {}
            return spectral_axis(
                int(self.detector.spectral_pixels),
                self._calibration,
                setting.get("grating"),
                setting.get("center_nm"),
            )

    # ---------- detector ----------
    def set_detector_rows(self, row_start: int, row_count: int) -> None:
        """The band of sensor rows summed into a spectrum; 0 rows = full height."""
        with self._lock:
            self._rows = (max(0, int(row_start)), max(0, int(row_count)))

    def configure(self, exposure_ms: float) -> None:
        """Send the exposure and the row band to the detector, if they changed.

        Called by acquire_spectrum(); called ahead of it when the state is about
        to be recorded, so the record describes the acquisition to come rather
        than whatever the live view last used.
        """
        exposure_ms = max(0.0, float(exposure_ms))
        with self._lock:
            self.connect()
            wanted = (exposure_ms, self._rows)
            if self._configured != wanted:
                self.detector.configure_spectrum(exposure_ms, *self._rows)
                self._configured = wanted

    def acquire_spectrum(self, exposure_ms: float, averages: int = 1, cancel=None) -> RamanSpectrum:
        """`averages` exposures, averaged: counts per exposure, comparable across settings.

        `cancel` (a threading.Event) cuts the acquisition short from any thread:
        AcquisitionAborted is raised and nothing is returned -- not even the
        exposures already averaged, which would be a spectrum of fewer
        exposures than asked for. Any other failure drops the detector's
        connection, so the next spectrum starts from a freshly opened camera
        rather than one left mid-acquisition.
        """
        exposure_ms = max(0.0, float(exposure_ms))
        averages = max(1, int(averages))

        with self._lock:
            self.configure(exposure_ms)

            total = None
            try:
                for _ in range(averages):
                    if cancel is not None and cancel.is_set():
                        raise AcquisitionAborted("stopped between two exposures")
                    frame = np.asarray(self.detector.acquire_spectrum(cancel=cancel), dtype=np.float64)
                    total = frame if total is None else total + frame
            except AcquisitionAborted:
                raise
            except Exception:
                try:
                    self.detector.disconnect()
                except Exception as e:
                    logger.debug(f"[Raman] detector would not close after a failure: {e}")
                self._configured = None
                logger.warning("[Raman] acquisition failed; the detector will be re-opened on the next one.")
                raise
            values = (total / averages).astype(np.float32)

            axis = self.spectral_axis()
            if axis.values.size != values.size:
                raise RuntimeError(
                    f"the detector returned {values.size} values for an axis of "
                    f"{axis.values.size}: the detector geometry changed under the axis"
                )

        return RamanSpectrum(
            values=values,
            axis=axis,
            exposure_ms=exposure_ms,
            averages=averages,
            acquired_at=time.time(),
        )

    # ---------- record ----------
    def state(self) -> dict:
        """Everything about how a spectrum was taken, for the file beside it."""
        with self._lock:
            self.connect()
            axis = self.spectral_axis()
            return {
                "spectrograph": dict(self._setting or {}),
                "detector": dict(self.detector.describe()),
                "calibration": None if self._calibration is None else self._calibration.describe(),
                "axis": axis.to_metadata(),
                "rows_requested": [int(self._rows[0]), int(self._rows[1])],
            }
