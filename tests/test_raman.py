"""A Raman spectrum is labelled truthfully.

Wavelengths are only ever claimed where a calibration made at that very
setting says so; everywhere else the axis is the detector pixel.
"""

import numpy as np
import pytest

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
