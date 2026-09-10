"""What it takes to read a saved acquisition back in six months.

Two jobs, kept apart from the writers so both formats record the same thing:

- physical_pixel_size_um() extracts the sampling interval from the scan
  parameters, so OME-TIFF can state PhysicalSize and OME-Zarr its NGFF scale.
  Without it a viewer opens the images in pixels and the micrometres are lost.
- acquisition_provenance() assembles the record that belongs beside the data:
  what was scanned, through which optics, with which laser, when, and by which
  build of DeepLight.

No Qt here: the caller passes plain values, so this is testable without a
window and neither writer depends on the widgets.
"""

from __future__ import annotations

import os
import platform
from datetime import datetime

from .Scan_Types import image_rows_by_axis

#: Bumped by hand, and not much: the git revision below is what actually
#: identifies a build in this project.
__version__ = "0.1.0"


def git_revision(start: str | None = None) -> str | None:
    """Current commit of the checkout DeepLight runs from, or None.

    Reads .git directly rather than shelling out to git: no dependency on git
    being installed or on PATH, nothing to wait for during a save, and it still
    works from a frozen build (where it simply finds nothing and says so).
    """
    path = os.path.abspath(start or os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

    while True:
        git_dir = os.path.join(path, ".git")
        if os.path.isdir(git_dir):
            break
        parent = os.path.dirname(path)
        if parent == path:
            return None
        path = parent

    try:
        with open(os.path.join(git_dir, "HEAD"), encoding="utf-8") as fh:
            head = fh.read().strip()
    except OSError:
        return None

    if not head.startswith("ref:"):
        return head or None          # detached HEAD already holds the sha

    ref = head[4:].strip()
    ref_path = os.path.join(git_dir, *ref.split("/"))
    try:
        with open(ref_path, encoding="utf-8") as fh:
            return fh.read().strip() or None
    except OSError:
        pass

    # Packed refs: the loose file is absent once git has packed the branch.
    try:
        with open(os.path.join(git_dir, "packed-refs"), encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith(("#", "^")):
                    continue
                sha, _, name = line.partition(" ")
                if name.strip() == ref:
                    return sha
    except OSError:
        pass

    return None


def _active_rows(scan_params: dict) -> list:
    return [r for r in (scan_params or {}).get("rows", []) if r.get("axis") != "None"]


def _row_for_axis(scan_params: dict, index: int) -> dict | None:
    rows = _active_rows(scan_params)
    return rows[index] if len(rows) > index else None


def _sampling_interval_um(row: dict | None) -> float | None:
    """Distance between two samples of an axis, in µm.

    Recomputed from size and pixel count rather than read from step_um: the
    widget displays the step rounded to three decimals, which for a small field
    is a visible error once a viewer scales an image by it. The stored step is
    the fallback for the rows that carry no size.
    """
    if not row:
        return None
    size = float(row.get("size_um", 0.0) or 0.0)
    pixels = int(row.get("pixels", 0) or 0)
    if size > 0.0 and pixels > 1:
        return size / (pixels - 1)
    step = float(row.get("step_um", 0.0) or 0.0)
    return step if step > 0.0 else None


def physical_pixel_size_um(scan_params: dict):
    """(x, y, z) sampling interval in µm; a component is None when unknown.

    x and y are the image's own axes, not the order the scan rows are listed
    in; z is the stack axis when there is one, so a Z stack carries its spacing
    and a polarisation stack does not pretend to have one.
    """
    row_x, row_y = image_rows_by_axis(scan_params)
    x = _sampling_interval_um(row_x)
    y = _sampling_interval_um(row_y)

    z = None
    third = _row_for_axis(scan_params, 2)
    if third and str(third.get("axis", "")).startswith("Z"):
        z = _sampling_interval_um(third)

    return x, y, z


def polarization_angles_deg(scan_params: dict) -> list[float]:
    """Azimuths visited by a polarisation stack, in degrees.

    Listed explicitly rather than left implicit in size/offset: a reader should
    not have to re-derive which angle each plane was taken at.
    """
    for index in range(4):
        row = _row_for_axis(scan_params, index)
        if not row or str(row.get("axis", "")) != "Polarization":
            continue
        pixels = int(row.get("pixels", 0) or 0)
        if pixels <= 0:
            return []
        start = float(row.get("offset_um", 0.0) or 0.0)
        step = _sampling_interval_um(row) or 0.0
        return [round(start + i * step, 6) for i in range(pixels)]
    return []


def acquisition_provenance(
    scan_params: dict,
    optics: dict | None = None,
    lasers: dict | None = None,
    comment: str = "",
    corrections: dict | None = None,
) -> dict:
    """The record that travels with the data.

    `optics` carries the objective, NA, refractive index and the wavelength the
    Nyquist panel is set to. `lasers` maps a laser name to its power in percent,
    holding only those actually on -- which is what identifies the excitation
    here, the wavelength being a property of whichever laser is running.
    """
    pixel_x, pixel_y, pixel_z = physical_pixel_size_um(scan_params)
    dwell_s = float((scan_params or {}).get("dwell_time", 0.0) or 0.0)

    row_fast = _row_for_axis(scan_params, 0)
    pixels_fast = int((row_fast or {}).get("pixels", 0) or 0)
    line_rate_hz = None
    if dwell_s > 0.0 and pixels_fast > 0:
        line_rate_hz = 1.0 / (dwell_s * pixels_fast)

    return {
        "software": {
            "name": "DeepLight",
            "version": __version__,
            "git_revision": git_revision(),
            "python": platform.python_version(),
            "host": platform.node(),
        },
        "timestamps": {
            "saved": datetime.now().astimezone().isoformat(timespec="seconds"),
        },
        "sampling": {
            "pixel_size_x_um": pixel_x,
            "pixel_size_y_um": pixel_y,
            "pixel_size_z_um": pixel_z,
            "dwell_time_s": dwell_s or None,
            "line_rate_hz": line_rate_hz,
            "samples_per_pixel": int((scan_params or {}).get("samples_per_pixel", 1) or 1),
            "bidirectional": bool((scan_params or {}).get("bidirectional_scan", False)),
        },
        "optics": dict(optics or {}),
        # Only the lasers that are on: the excitation wavelength follows from
        # which one is running, and DeepLight does not model it separately.
        "lasers": dict(lasers or {}),
        "polarization_angles_deg": polarization_angles_deg(scan_params),
        "detectors": list((scan_params or {}).get("detector_channels", []) or []),
        # Which dark/flat reference was subtracted, if any: without it a
        # corrected image and a raw one are indistinguishable after the fact.
        "corrections": dict(corrections or {}),
        # Detector gain is not set by the software and cannot be read back, so
        # it belongs here, written by whoever ran the experiment.
        "comment": str(comment or ""),
    }
