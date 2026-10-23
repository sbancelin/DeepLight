"""A Raman spectrum is labelled truthfully, and a Raman map is saved as a cube.

Wavelengths are only ever claimed where a calibration made at that very
setting says so; everywhere else the axis is the detector pixel. And a map
comes back from disk as an image stack -- the scanned field in XY, one plane
per spectral pixel -- with each point's spectrum where the point was.
"""

import json
import os

import numpy as np
import pytest
import tifffile

from DeepLight.gui.managers.Save_Manager import SaveManager
from DeepLight.gui.managers.Spectral_Calibration import (
    NANOMETRE,
    PIXEL,
    SpectralCalibration,
    spectral_axis,
)


def _write(path, text):
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return str(path)


def test_a_calibration_file_is_read_by_its_named_column(tmp_path):
    table = "".join(f"{740 + 0.5 * i},{100 + i}\n" for i in range(8))
    path = _write(tmp_path / "lightfield.csv", "Wavelength (nm),Intensity\n" + table)

    calibration = SpectralCalibration.from_file(path, grating=2, center_nm=785.0)

    assert calibration.n_pixels == 8
    assert calibration.wavelengths_nm[0] == pytest.approx(740.0)
    assert calibration.wavelengths_nm[-1] == pytest.approx(743.5)


def test_a_calibration_file_that_could_be_misread_is_refused(tmp_path):
    """Two unnamed columns: wavelength first, or second? Guessing wrong would
    mislabel every spectrum taken with it, so the file is refused instead."""
    ambiguous = _write(tmp_path / "ambiguous.csv",
                       "".join(f"{740 + 0.5 * i},{100 + i}\n" for i in range(8)))
    not_a_table = _write(tmp_path / "shuffled.txt", "740.0\n741.0\n740.5\n742.0\n")

    for path in (ambiguous, not_a_table):
        with pytest.raises(ValueError):
            SpectralCalibration.from_file(path)


def test_wavelengths_are_claimed_only_at_the_setting_they_were_measured_at():
    calibration = SpectralCalibration(np.linspace(760.0, 810.0, 100), grating=2, center_nm=785.0)

    assert spectral_axis(100, None, 2, 785.0).unit == PIXEL             # nothing loaded
    assert spectral_axis(100, calibration, 2, 785.0).unit == NANOMETRE  # its own setting
    assert spectral_axis(100, calibration, 2, 800.0).unit == PIXEL      # centre moved
    assert spectral_axis(100, calibration, 1, 785.0).unit == PIXEL      # other grating
    assert spectral_axis(120, calibration, 2, 785.0).unit == PIXEL      # other binning


def test_a_raman_map_is_saved_as_an_xy_stack_along_the_spectrum(tmp_path):
    t, z, y, x, n = 1, 1, 3, 4, 50
    spectra = np.random.default_rng(0).random((t, z, y, x, n)).astype(np.float32)
    wavelengths = np.linspace(800.0, 850.0, n)
    dataset = {
        "raman_spectra": spectra,
        "raman_axis": wavelengths,
        "raman_axis_unit": "nm",
        "acquisition_order_indices": np.array(
            [[0, 0, j, i] for j in range(y) for i in range(x)], dtype=np.int32),
        "grid_shape": (x, y, z),
        "axis_order": ("x", "y", "z"),
        "serpentine": True,
        "metadata": {"acquisition_parameters": {"step_x_um": 2.0, "step_y_um": 3.0}},
    }

    root = SaveManager().save_spectro_dataset(str(tmp_path), "map", "", dataset, "OME-TIFF")

    with tifffile.TiffFile(os.path.join(root, "raman.ome.tif")) as tf:
        series = tf.series[0]
        cube = series.asarray()
    assert series.axes == "ZYX" and cube.shape == (n, y, x)     # one XY plane per spectral pixel
    assert np.array_equal(cube[:, 2, 1], spectra[0, 0, 2, 1])   # point (y=2, x=1) where it was

    # The axis file is itself a calibration table: it reads back as one.
    axis = SpectralCalibration.from_file(os.path.join(root, "raman_axis.csv"))
    assert np.allclose(axis.wavelengths_nm, wavelengths)

    with open(os.path.join(root, "metadata.json"), encoding="utf-8") as fh:
        assert json.load(fh)["raman_axis"]["unit"] == "nm"


def test_an_exposure_stopped_midway_ends_at_once_and_returns_nothing():
    """Stop cuts a long exposure short, and the partial spectrum is discarded:
    its duration is unknown, so its counts would compare to nothing."""
    import threading
    import time

    from DeepLight.gui.managers.Raman_Detector import AcquisitionAborted, MockRamanDetector
    from DeepLight.gui.managers.Raman_Spectrometer import RamanSpectrometer
    from DeepLight.gui.managers.Spectrograph_Manager import MockSpectrograph

    raman = RamanSpectrometer(MockSpectrograph(), MockRamanDetector())
    cancel = threading.Event()
    threading.Timer(0.2, cancel.set).start()

    started = time.monotonic()
    with pytest.raises(AcquisitionAborted):
        raman.acquire_spectrum(exposure_ms=4000.0, averages=3, cancel=cancel)
    assert time.monotonic() - started < 1.0
