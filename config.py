"""Single configuration file for everything that changes between installations.

Design goals
------------
* **No command-line flags, no start-up dialog.** DeepLight launches exactly as
  before. If no configuration file exists, the built-in defaults below are used
  and behaviour is identical to the hard-coded values it replaces.
* **The file is optional.** It only *overrides* defaults, key by key. An empty
  file, or a file with a single line in it, is perfectly valid.
* **Written for you on first run.** If no file is found, the commented template
  below is written to the per-user location and its path is logged, so there is
  never a file to author from scratch — only one to edit.

What belongs here
-----------------
Anything that differs between two installations of DeepLight: COM ports, device
serial numbers, driver install paths, the data directory.

What does *not* belong here: constants that are properties of the hardware
model rather than of the installation (``COUNTS_PER_REV`` for an ELL14, the
Alcor repetition-rate limits, the half-wave-plate 45 degree span). Those stay in
the code, where they cannot be "adjusted" into being wrong.

Lookup order (first hit wins)
-----------------------------
1. ``$DEEPLIGHT_CONFIG`` — full path to a file; escape hatch, rarely needed.
2. ``deeplight.toml`` next to the package (git-ignored).
3. ``%APPDATA%\\DeepLight\\config.toml`` on Windows, else
   ``~/.config/deeplight/config.toml``. This is the recommended location: each
   acquisition PC keeps its own, and it never collides with the repository.
4. The built-in defaults.

Usage::

    from DeepLight.config import CONFIG
    port = CONFIG.cobolt.flamenco_port
"""

from __future__ import annotations

import datetime
import logging
import os
import sys
from pathlib import Path
from typing import Any, Dict

try:  # Python 3.11+
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - exercised on 3.9/3.10 only
    try:
        import tomli as tomllib  # type: ignore[no-redef]
    except ModuleNotFoundError as exc:  # pragma: no cover
        raise ModuleNotFoundError(
            "Reading the DeepLight configuration needs a TOML parser. "
            "On Python 3.11+ this is the standard-library 'tomllib'; on older "
            "versions install the backport with:  pip install tomli"
        ) from exc

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# The template below is the single source of truth: the built-in defaults are
# parsed from it, so the generated file and the defaults can never drift apart.
# TOML literal strings (single quotes) are used for Windows paths so that
# backslashes need no escaping.
# ---------------------------------------------------------------------------

TEMPLATE = r"""# DeepLight configuration
#
# Every key is optional: delete a line and DeepLight falls back to its built-in
# default. Every value below IS the built-in default, so this file as generated
# changes nothing -- edit only what differs on this machine.
#
# After editing, just restart DeepLight. There is nothing else to run.


# --- National Instruments acquisition card --------------------------------
[ni]
# Device name as it appears in NI MAX (e.g. "Dev1", "Dev2").
# The analog-out/in and counter lines are derived from it automatically.
device_name = "Dev1"
ai_min_v = -10.0
ai_max_v = 10.0
# "DIFF" (differential), "RSE" or "NRSE".
ai_terminal_mode = "DIFF"


# --- Thorlabs (Kinesis) ----------------------------------------------------
[thorlabs]
# Folder containing the Kinesis .NET DLLs.
kinesis_path = 'C:\Program Files\Thorlabs\Kinesis'
# Serial number printed on the KCube driving the shutter.
shutter_serial = "68800404"
# Controller family of the rotation mounts: "KCubeDCServo" or
# "KCubeStepperMotor".
rotator_controller_kind = "KCubeDCServo"

# Serial number of the rotation mount used for each laser.
[thorlabs.rotator_serials]
"Mira 900" = "27269600"
"Tumecs" = "27005331"


# --- Physik Instrumente Z stage -------------------------------------------
[pi]
v308_serial = "123041734"
z_axis_id = 1
z_default_vel_mm_s = 0.5


# --- Cobolt Flamenco laser + its Elliptec half-wave plate ------------------
[cobolt]
flamenco_port = "COM12"
flamenco_baudrate = 115200
flamenco_max_power_mw = 300.0

# Power half-wave plate, address 0 on the shared ELLB bus (see [elliptec]).
ell14_port = "COM15"
ell14_baudrate = 9600
ell14_address = "0"
ell14_timeout_s = 1.0


# --- Elliptec rotation mounts ---------------------------------------------
# Three ELL14 mounts share ONE ELLB bus, i.e. the same COM port as
# cobolt.ell14_port. They are told apart by their bus address:
#   address 0 -> Cobolt power half-wave plate  (see [cobolt])
#   address 1 -> half-wave plate, positioner "p"  = P(lambda/2), usable in scans
#   address 2 -> quarter-wave plate, positioner "p4" = P(lambda/4), not scanned
[elliptec]
lambda2_address = "1"
lambda4_address = "2"


# --- Spark Lasers ALCOR / XSight ------------------------------------------
[alcor]
port = "COM14"
baudrate = 115200
timeout_s = 0.7


# --- Scientifica Motion 8 XY stage (virtual serial port) -------------------
[scientifica]
port = "COM11"
baudrate = 9600
timeout_s = 1.0
device_id = 0
x_axis_id = 1
y_axis_id = 0


# --- Princeton Instruments camera (PICam) ----------------------------------
[picam]
# Leave empty to search the standard PICam install locations automatically.
# Set it to a full path to Picam.dll to override that search.
dll_path = ''


# --- Brillouin arm ---------------------------------------------------------
[spectro]
# Serial number of the Kuro. PICam drives every Princeton Instruments camera,
# so once the Raman LANSIS shares the bench the two must be told apart by
# serial -- otherwise whichever PICam discovers first is opened. Leave empty
# when the Kuro is the only PI camera plugged in.
brillouin_camera_serial = ""


# --- Raman spectrometer (Princeton Instruments IsoPlane 320 + camera) ------
[raman]
# "mock" runs the whole Raman path in software; "isoplane320" drives the real
# spectrograph over its serial port.
spectrograph = "mock"
spectrograph_port = "COM13"
spectrograph_baudrate = 9600
spectrograph_timeout_s = 20.0      # a grating turret takes seconds to move

# Detector: "mock" synthesises a spectrum, "picam" drives the real camera
# through PICam -- the same library already used for the Kuro.
camera = "mock"
# Serial number of the Teledyne Princeton Instruments LANSIS-261X. Required as
# soon as the Kuro is plugged in too (see spectro.brillouin_camera_serial).
camera_serial = ""

# Optics, used to label the wavelength axis.
focal_length_mm = 320.0            # IsoPlane 320
groove_density_per_mm = 600.0      # grating currently installed
camera_pixel_size_um = 20.0        # detector pixel pitch along the dispersion

# Rows of the sensor summed to form the spectrum, centred on the slit image.
# 0 = sum the full height.
bin_rows = 0

# Measured wavelength calibration, highest order first, evaluated on the pixel
# index: [a, b, c] means lambda = a*i^2 + b*i + c. Leave empty to fall back on
# the optical model, which labels the axis but is not a calibration.
calibration_poly = []


# --- Where acquisitions are written ---------------------------------------
[data]
# Root of the data tree. Dated sub-folders (year/month/day) are created below
# it. Leave empty to use <your home folder>/DeepLight_data instead.
root = 'C:\Data'
"""


DEFAULTS: Dict[str, Any] = tomllib.loads(TEMPLATE)


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------

def _user_config_path() -> Path:
    """Per-user config location: %APPDATA% on Windows, XDG elsewhere."""
    if sys.platform == "win32":
        base = os.environ.get("APPDATA")
        if base:
            return Path(base) / "DeepLight" / "config.toml"
        return Path.home() / "AppData" / "Roaming" / "DeepLight" / "config.toml"
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg) if xdg else Path.home() / ".config"
    return base / "deeplight" / "config.toml"


def candidate_paths() -> list[Path]:
    """Every location searched, in priority order."""
    paths = []
    env = os.environ.get("DEEPLIGHT_CONFIG")
    if env:
        paths.append(Path(env))
    paths.append(Path(__file__).parent / "deeplight.toml")
    paths.append(_user_config_path())
    return paths


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    """Recursively overlay `override` on `base`, returning a new dict."""
    out = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def _write_template(path: Path) -> bool:
    """Write the commented template to `path`. Never raises."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(TEMPLATE, encoding="utf-8")
        return True
    except OSError as exc:
        logger.warning("[config] could not create %s: %s", path, exc)
        return False


class Section:
    """Read-only attribute access over a config table."""

    def __init__(self, data: Dict[str, Any], _path: str = ""):
        self._data = data
        self._path = _path

    def __getattr__(self, name: str) -> Any:
        try:
            value = self._data[name]
        except KeyError:
            where = f"{self._path}.{name}" if self._path else name
            raise AttributeError(
                f"no configuration key {where!r}. Known keys here: "
                f"{sorted(self._data)}"
            ) from None
        if isinstance(value, dict):
            return Section(value, f"{self._path}.{name}" if self._path else name)
        return value

    def get(self, name: str, default: Any = None) -> Any:
        return self._data.get(name, default)

    def as_dict(self) -> Dict[str, Any]:
        return dict(self._data)

    def __contains__(self, name: str) -> bool:
        return name in self._data

    def __repr__(self) -> str:
        return f"Section({self._path or 'root'}: {sorted(self._data)})"


def load(create_if_missing: bool = True) -> Section:
    """Load the configuration, falling back to the built-in defaults."""
    for path in candidate_paths():
        try:
            if not path.is_file():
                continue
            with path.open("rb") as fh:
                user = tomllib.load(fh)
        except OSError as exc:
            logger.warning("[config] cannot read %s: %s", path, exc)
            continue
        except tomllib.TOMLDecodeError as exc:
            # A broken file must not prevent the microscope from starting.
            logger.error(
                "[config] %s is not valid TOML (%s). Using built-in defaults.",
                path, exc,
            )
            continue
        logger.info("[config] loaded %s", path)
        return Section(_deep_merge(DEFAULTS, user))

    target = _user_config_path()
    if create_if_missing and _write_template(target):
        logger.info(
            "[config] no configuration found; wrote a commented one to %s "
            "(built-in defaults, nothing changed)", target,
        )
    else:
        logger.info("[config] no configuration file; using built-in defaults")
    return Section(dict(DEFAULTS))


CONFIG: Section = load()


# ---------------------------------------------------------------------------
# Small helpers shared by the widgets
# ---------------------------------------------------------------------------

def data_root() -> Path:
    """Root of the data tree, with a cross-platform fallback."""
    root = str(CONFIG.data.get("root", "") or "").strip()
    if not root:
        return Path.home() / "DeepLight_data"
    return Path(root)


def log_folder() -> Path:
    """Where session logs are kept: ``<data root>/logs``.

    Under the data root rather than beside the program, so the logs follow the
    data onto whichever drive the experiments live on, and one configured path
    still decides everything.
    """
    return data_root() / "logs"


def dated_data_folder(year: str, month: str = "", day: str = "") -> str:
    """``<data root>/<year>[/<month>[/<day>]]`` as a string."""
    path = data_root() / year
    if month:
        path = path / month
    if day:
        path = path / day
    return str(path)


# Month names are spelled out rather than taken from the C library so the
# folder name cannot change with the machine's locale. This matches what
# QDate.toString("MMMM") produces, since DeepLight forces QLocale.c().
_MONTH_NAMES = (
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
)


def default_dated_folder(when: "datetime.date | None" = None) -> str:
    """Default save folder: ``<data root>/YYYY/Month/DD``.

    The single source of the default save location, so the Save panel and the
    Spectro panel cannot drift apart again.
    """
    day = when or datetime.date.today()
    return dated_data_folder(
        f"{day.year:04d}", _MONTH_NAMES[day.month - 1], f"{day.day:02d}"
    )
