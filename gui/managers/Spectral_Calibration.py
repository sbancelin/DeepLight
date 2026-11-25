"""The Raman spectral axis: raw detector pixels, unless a calibration applies.

Wavelength calibration is done in LightField, against its IntelliCal lamps;
DeepLight only *applies* the result. By default a spectrum is labelled by
detector pixel -- the one axis that cannot be wrong. A calibration maps each
pixel to a wavelength, and it is only valid for the grating and the centre
wavelength it was measured at: change either and the spectrum moves on the
sensor. So a calibration carries the setting it belongs to, and is applied
only when the spectrograph is at that setting. Anywhere else the axis falls
back to pixels and says why, rather than labelling a spectrum with
wavelengths that belong to another setting.

Calibration file -- plain text, which a LightField or WinSpec export, or a
spreadsheet, can be turned into::

    # Lines starting with '#' are comments.
    # grating = 2            optional: the turret position it was measured on
    # center_nm = 785.0      optional: the centre wavelength it was measured at
    wavelength_nm            optional header row
    770.123
    770.145
    ...

Columns may be separated by commas, semicolons, tabs or spaces:

* one column: the wavelength of each pixel, in pixel order;
* several columns: the one whose header names it (wavelength, lambda, nm), or
  the second one when the first is the pixel index 0, 1, 2, ...

Anything else is refused with a message saying what to change, rather than
guessed at. Without the two optional lines, the calibration is taken to belong
to the setting the spectrograph is at when the file is loaded.

No Qt here: this is arithmetic and a file format, testable on its own.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from datetime import datetime

import numpy as np

#: Spectral axis units written beside every spectrum.
PIXEL = "pixel"
NANOMETRE = "nm"

#: How far the centre wavelength may be from the one a calibration was made at
#: and still be the same setting. The spectrograph reports its position to a
#: few hundredths of a nanometre; anything beyond this is a different setting.
CENTER_TOLERANCE_NM = 0.05

_WAVELENGTH_HEADERS = ("wavelength", "lambda", "nm")


def _split(line: str) -> list[str]:
    """Cells of one line, on the line's own separator.

    Semicolon, then tab, then comma, then any whitespace: a header such as
    "Wavelength (nm),Intensity" must stay two cells, which splitting on
    spaces as well would break. With a semicolon or a tab between cells, a
    comma inside one can only be a decimal comma -- what a spreadsheet in a
    French locale writes -- and is read as one.
    """
    for separator in (";", "\t"):
        if separator in line:
            return [c.strip().replace(",", ".") for c in line.split(separator) if c.strip()]
    if "," in line:
        return [c.strip() for c in line.split(",") if c.strip()]
    return [c for c in re.split(r"\s+", line) if c]


@dataclass
class SpectralAxis:
    """The axis a spectrum is labelled with, and how much to trust it."""

    values: np.ndarray
    unit: str                       # PIXEL or NANOMETRE
    calibrated: bool = False
    note: str = ""                  # why the axis is what it is, for the log and the file

    def to_metadata(self) -> dict:
        return {
            "unit": self.unit,
            "calibrated": bool(self.calibrated),
            "note": self.note,
            "n_pixels": int(self.values.size),
        }


def pixel_axis(n_pixels: int, note: str = "") -> SpectralAxis:
    return SpectralAxis(
        values=np.arange(max(0, int(n_pixels)), dtype=np.float64),
        unit=PIXEL,
        calibrated=False,
        note=note or "raw detector pixels (no calibration loaded)",
    )


def _parse_number(text: str):
    try:
        return float(text)
    except ValueError:
        return None


def read_calibration_file(path: str) -> tuple[np.ndarray, dict]:
    """Return (wavelength of each pixel, header keys) from a calibration file.

    Raises ValueError with a message meant for the person who wrote the file:
    a calibration applied to the wrong column would mislabel every spectrum
    acquired with it, and nothing downstream could tell.
    """
    header: dict = {}
    names: list[str] | None = None
    rows: list[list[float]] = []

    with open(path, encoding="utf-8-sig", errors="replace") as fh:
        for line_number, raw in enumerate(fh, start=1):
            line = raw.strip()
            if not line:
                continue
            if line.startswith("#"):
                key, sep, value = line.lstrip("#").partition("=")
                if sep:
                    header[key.strip().lower()] = value.strip()
                continue

            cells = _split(line)
            numbers = [_parse_number(c) for c in cells]

            if any(n is None for n in numbers):
                if rows or names is not None:
                    raise ValueError(
                        f"line {line_number} is not numeric: {line!r}. Only one "
                        "header row is allowed, before the values."
                    )
                names = [c.strip().lower() for c in cells]
                continue

            rows.append(numbers)

    if not rows:
        raise ValueError("the file holds no numeric values.")

    widths = {len(r) for r in rows}
    if len(widths) != 1:
        raise ValueError(f"rows do not all have the same number of columns ({sorted(widths)}).")
    table = np.asarray(rows, dtype=np.float64)

    if table.shape[1] == 1:
        wavelengths = table[:, 0]
    else:
        column = None
        if names is not None:
            for i, name in enumerate(names[:table.shape[1]]):
                if any(token in name for token in _WAVELENGTH_HEADERS):
                    column = i
                    break
        if column is None:
            first = table[:, 0]
            if np.array_equal(first, np.arange(first.size, dtype=np.float64)):
                column = 1
        if column is None:
            raise ValueError(
                f"{table.shape[1]} columns and none of them is identified as the "
                "wavelength. Add a header row naming it 'wavelength_nm', or keep "
                "a single column."
            )
        wavelengths = table[:, column]

    _validate_wavelengths(wavelengths)
    return wavelengths, header


def _validate_wavelengths(wavelengths: np.ndarray) -> None:
    if wavelengths.ndim != 1 or wavelengths.size < 2:
        raise ValueError("a calibration needs at least two pixels.")
    if not np.all(np.isfinite(wavelengths)):
        raise ValueError("the calibration contains non-numeric or infinite values.")
    steps = np.diff(wavelengths)
    if not (np.all(steps > 0) or np.all(steps < 0)):
        raise ValueError(
            "wavelengths are not strictly monotonic along the pixels: this is "
            "not a pixel-to-wavelength table."
        )


def _optional_float(value):
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _optional_int(value):
    number = _optional_float(value)
    return None if number is None else int(round(number))


@dataclass
class SpectralCalibration:
    """Wavelength of each detector pixel, and the setting it was measured at."""

    wavelengths_nm: np.ndarray
    grating: int | None = None              # turret position
    center_nm: float | None = None
    grooves_per_mm: float | None = None
    source: str = ""                        # file it came from, for the record
    loaded: str = field(default_factory=lambda: datetime.now().astimezone().isoformat(timespec="seconds"))

    def __post_init__(self):
        self.wavelengths_nm = np.asarray(self.wavelengths_nm, dtype=np.float64)
        _validate_wavelengths(self.wavelengths_nm)

    @property
    def n_pixels(self) -> int:
        return int(self.wavelengths_nm.size)

    # ---- construction --------------------------------------------------

    @classmethod
    def from_file(cls, path: str, grating: int | None = None,
                  center_nm: float | None = None, grooves_per_mm: float | None = None,
                  n_pixels: int | None = None) -> "SpectralCalibration":
        """Load a calibration file.

        The setting written in the file wins over the one passed in: the file
        knows where it was measured, the caller only knows where the
        spectrograph is now.
        """
        wavelengths, header = read_calibration_file(path)

        if n_pixels is not None and wavelengths.size != int(n_pixels):
            raise ValueError(
                f"the file maps {wavelengths.size} pixels, the detector reads "
                f"{int(n_pixels)}: it was made for another camera, binning or ROI."
            )

        file_grating = _optional_int(header.get("grating"))
        file_center = _optional_float(header.get("center_nm"))
        file_grooves = _optional_float(header.get("grooves_per_mm"))

        return cls(
            wavelengths_nm=wavelengths,
            grating=file_grating if file_grating is not None else grating,
            center_nm=file_center if file_center is not None else center_nm,
            grooves_per_mm=file_grooves if file_grooves is not None else grooves_per_mm,
            source=os.path.basename(path),
        )

    def to_dict(self) -> dict:
        """Plain data, to persist with the spectro settings."""
        return {
            "wavelengths_nm": [float(v) for v in self.wavelengths_nm],
            "grating": self.grating,
            "center_nm": self.center_nm,
            "grooves_per_mm": self.grooves_per_mm,
            "source": self.source,
            "loaded": self.loaded,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "SpectralCalibration":
        data = dict(data or {})
        return cls(
            wavelengths_nm=np.asarray(data.get("wavelengths_nm", []), dtype=np.float64),
            grating=_optional_int(data.get("grating")),
            center_nm=_optional_float(data.get("center_nm")),
            grooves_per_mm=_optional_float(data.get("grooves_per_mm")),
            source=str(data.get("source", "") or ""),
            loaded=str(data.get("loaded", "") or ""),
        )

    # ---- use -----------------------------------------------------------

    def mismatch(self, n_pixels: int, grating: int | None, center_nm: float | None) -> str | None:
        """Why this calibration does not apply to that setting, or None if it does."""
        if int(n_pixels) != self.n_pixels:
            return (f"calibration maps {self.n_pixels} pixels, the detector reads "
                    f"{int(n_pixels)}")
        if self.grating is None or self.center_nm is None:
            return "calibration does not say which grating and centre it belongs to"
        if grating is None or center_nm is None:
            return "the spectrograph setting is unknown"
        if int(grating) != int(self.grating):
            return (f"calibration made on grating {self.grating}, spectrograph on "
                    f"grating {int(grating)}")
        if abs(float(center_nm) - float(self.center_nm)) > CENTER_TOLERANCE_NM:
            return (f"calibration made at {self.center_nm:.3f} nm, spectrograph at "
                    f"{float(center_nm):.3f} nm")
        return None

    def describe(self) -> dict:
        """What was applied, for the record beside the data (no per-pixel array)."""
        return {
            "source": self.source,
            "loaded": self.loaded,
            "grating": self.grating,
            "grooves_per_mm": self.grooves_per_mm,
            "center_nm": self.center_nm,
            "n_pixels": self.n_pixels,
            "range_nm": [float(self.wavelengths_nm.min()), float(self.wavelengths_nm.max())],
        }


def spectral_axis(n_pixels: int, calibration: SpectralCalibration | None,
                  grating: int | None, center_nm: float | None) -> SpectralAxis:
    """The axis to label a spectrum with, at the spectrograph's current setting."""
    if calibration is None:
        return pixel_axis(n_pixels)

    reason = calibration.mismatch(n_pixels, grating, center_nm)
    if reason is not None:
        return pixel_axis(n_pixels, note=f"raw detector pixels: {reason}")

    return SpectralAxis(
        values=calibration.wavelengths_nm.copy(),
        unit=NANOMETRE,
        calibrated=True,
        note=f"calibrated from {calibration.source or 'a loaded table'}",
    )
