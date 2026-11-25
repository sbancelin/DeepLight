"""Specto BIPD filter (81Watts): contract, simulation, and the libspecto driver.

The BIPD sits in the Brillouin arm and sets how well the spectrometer extinguishes
the light it should not pass. It is tuned with three things:

- a **liquid crystal**, whose voltage (mV) is the fine adjustment, and the one
  that drifts -- hence the automatic re-optimisation in Extinction.py;
- a **rotator** (degrees), the coarse adjustment;
- a **heater** that keeps the liquid crystal at a constant temperature, since
  its retardance depends on it.

The vendor ships a compiled Python module, ``libspecto`` (wheel kept in
Controle_BIPD/; built for Python 3.12 only). It is imported lazily, on connect,
so the rest of DeepLight -- and the simulated filter -- never needs it.

Another filter is one more class against BipdFilterBase, returned from
create_bipd_filter(). Nothing else in DeepLight refers to the vendor.

Thread safety. The filter is driven from two threads at once: the panel's
command thread (Bipd_Manager) and, during a map, the mapping thread when the
extinction has to be re-optimised. Every call to the device goes through one
lock, so two transactions never interleave on the serial port.
"""

from __future__ import annotations

import math
import threading

from ...config import CONFIG
from ..widgets.Log_Widget import logger


class BipdFilterBase:
    """Minimal contract for the extinction filter of the Brillouin arm."""

    kind = "BIPD filter"

    #: Hard limits of the liquid-crystal voltage. A property of the device, not
    #: of the installation: 4.5 V is the absolute safety limit stated for the
    #: liquid crystal, so it stays in the code where it cannot be configured
    #: into being wrong. Every voltage sent is clamped to it.
    LC_MIN_MV = 0.0
    LC_MAX_MV = 4500.0

    def __init__(self):
        self.connected = False
        self._lock = threading.RLock()

    # ---------- lifecycle ----------
    def connect(self) -> None:
        raise NotImplementedError

    def disconnect(self) -> None:
        raise NotImplementedError

    def close(self) -> None:
        """Drop the connection (alias of disconnect, as for every controller)."""
        self.disconnect()

    # ---------- liquid crystal ----------
    @classmethod
    def clamp_lc_mv(cls, level_mv: float) -> float:
        level = float(level_mv)
        if not math.isfinite(level):
            raise ValueError(f"Liquid-crystal level must be a number, got {level_mv!r}")
        return min(cls.LC_MAX_MV, max(cls.LC_MIN_MV, level))

    def set_lc_level_mv(self, level_mv: float) -> float:
        """Send a voltage, clamped to the safety limits; returns what was sent."""
        raise NotImplementedError

    def lc_level_mv(self) -> float:
        """The voltage last commanded (the setpoint)."""
        raise NotImplementedError

    def measured_lc_level_mv(self) -> float | None:
        """The voltage the device reads back, or None if it cannot say."""
        return None

    # ---------- rotator ----------
    def move_rotator_deg(self, angle_deg: float) -> None:
        """Start an absolute move; rotator_busy() says when it is over."""
        raise NotImplementedError

    def home_rotator(self) -> None:
        raise NotImplementedError

    def rotator_angle_deg(self) -> float:
        raise NotImplementedError

    def rotator_busy(self) -> bool:
        return False

    # ---------- heater ----------
    def set_heater_enabled(self, enabled: bool) -> None:
        raise NotImplementedError

    def heater_enabled(self) -> bool:
        raise NotImplementedError

    def heater_temperature_c(self) -> float | None:
        return None

    # ---------- record ----------
    def describe(self) -> dict:
        return {"kind": self.kind}

    def state(self) -> dict:
        """Everything worth showing or recording beside a dataset, in one read."""
        return {
            "lc_level_mv": float(self.lc_level_mv()),
            "lc_measured_mv": self.measured_lc_level_mv(),
            "rotator_deg": float(self.rotator_angle_deg()),
            "rotator_busy": bool(self.rotator_busy()),
            "heater_enabled": bool(self.heater_enabled()),
            "temperature_c": self.heater_temperature_c(),
        }

    def _require_connected(self) -> None:
        if not self.connected:
            raise RuntimeError("BIPD filter is not connected.")


class MockBipdFilter(BipdFilterBase):
    """The filter in software, with an extinction that actually depends on it.

    Not inert on purpose: the automatic re-optimisation is only exercised if
    turning the liquid crystal changes something. simulated_leak() is the
    fraction of unwanted light let through -- zero at one voltage and one
    rotator angle, growing on either side -- and the simulated Brillouin camera
    adds it to its frames (HardwareManager.simulated_extinction_leak).
    """

    kind = "simulated BIPD filter"

    _BEST_LC_MV = 2200.0
    _LC_WIDTH_MV = 120.0
    _BEST_ROTATOR_DEG = 0.0

    def __init__(self):
        super().__init__()
        self._lc_mv = 2000.0
        self._rotator_deg = 0.0
        self._heater = False

    def connect(self) -> None:
        self.connected = True
        logger.debug("[MockBipd] connected")

    def disconnect(self) -> None:
        self.connected = False
        logger.debug("[MockBipd] disconnected")

    def set_lc_level_mv(self, level_mv: float) -> float:
        self._require_connected()
        with self._lock:
            self._lc_mv = self.clamp_lc_mv(level_mv)
            return self._lc_mv

    def lc_level_mv(self) -> float:
        return float(self._lc_mv)

    def measured_lc_level_mv(self) -> float | None:
        return float(self._lc_mv) if self.connected else None

    def move_rotator_deg(self, angle_deg: float) -> None:
        self._require_connected()
        self._rotator_deg = float(angle_deg)

    def home_rotator(self) -> None:
        self._require_connected()
        self._rotator_deg = 0.0

    def rotator_angle_deg(self) -> float:
        return float(self._rotator_deg)

    def set_heater_enabled(self, enabled: bool) -> None:
        self._require_connected()
        self._heater = bool(enabled)

    def heater_enabled(self) -> bool:
        return bool(self._heater)

    def heater_temperature_c(self) -> float | None:
        if not self.connected:
            return None
        return 35.0 if self._heater else 22.0

    def simulated_leak(self) -> float:
        """Unwanted light let through, relative to a well-adjusted filter."""
        lc = (self._lc_mv - self._BEST_LC_MV) / self._LC_WIDTH_MV
        rot = math.sin(math.radians(self._rotator_deg - self._BEST_ROTATOR_DEG))
        return float(lc * lc + 4.0 * rot * rot)

    def describe(self) -> dict:
        return {"kind": self.kind}


class SpectoBipdFilter(BipdFilterBase):
    """The 81Watts Specto BIPD, through the vendor's libspecto module.

    ``port`` empty lets libspecto find the device itself. The rotator is not
    homed on connect: it keeps the angle it was left at, which is the one the
    extinction was last tuned for.
    """

    kind = "Specto BIPD (81Watts)"

    #: How the vendor module is installed, repeated in every error about it.
    INSTALL_HINT = (
        "install the vendor wheel kept in DeepLight/Controle_BIPD "
        "(pip install specto_bipd-0.1.1-cp312-cp312-win_amd64.whl -- "
        "built for Python 3.12 only)"
    )

    def __init__(self, port: str = ""):
        super().__init__()
        self.port = str(port or "").strip()
        self._device = None
        self._lc_mv = 0.0
        self._lc_enabled = False
        self._heater = False
        self._firmware = None

    def _import_driver(self):
        try:
            from libspecto import SpectoBIPD
        except ImportError as e:
            raise RuntimeError(f"libspecto is not installed ({e}); {self.INSTALL_HINT}") from e
        return SpectoBIPD

    def _open(self, SpectoBIPD):
        args = (self.port,) if self.port else ()
        try:
            return SpectoBIPD(*args, init_rotator_move_home=False)
        except TypeError:
            # A build without the keyword: it may home the rotator on opening.
            logger.warning("[BIPD] this libspecto cannot skip homing the rotator on connect")
            return SpectoBIPD(*args)

    def connect(self) -> None:
        if self.connected:
            return
        SpectoBIPD = self._import_driver()
        with self._lock:
            self._device = self._open(SpectoBIPD)
            dev = self._device
            # Start from what the device holds, not from an assumed zero: the
            # first step from the panel is then a step from where it really is.
            try:
                self._lc_mv = float(dev.lc_get_level_target())
            except Exception:
                self._lc_mv = float(dev.lc_get_level())
            try:
                self._lc_enabled = bool(dev.lc_get_enabled())
            except Exception:
                self._lc_enabled = False
            try:
                self._heater = bool(dev.heater_get_enabled())
            except Exception:
                self._heater = False
            try:
                self._firmware = str(dev.firmware_version)
            except Exception:
                self._firmware = None
            self.connected = True
        logger.info(
            f"[BIPD] connected{' on ' + self.port if self.port else ''}"
            f"{', firmware ' + self._firmware if self._firmware else ''}, "
            f"LC {self._lc_mv:.1f} mV"
        )

    def disconnect(self) -> None:
        with self._lock:
            dev, self._device = self._device, None
            self.connected = False
            if dev is not None:
                try:
                    dev.close()
                except Exception as e:
                    logger.debug(f"[BIPD] close failed: {e}")

    def _dev(self):
        self._require_connected()
        return self._device

    def set_lc_level_mv(self, level_mv: float) -> float:
        level = self.clamp_lc_mv(level_mv)
        with self._lock:
            dev = self._dev()
            if not self._lc_enabled:
                dev.lc_set_enabled(True)
                self._lc_enabled = True
            dev.lc_set_level(level)
            self._lc_mv = level
        return level

    def lc_level_mv(self) -> float:
        return float(self._lc_mv)

    def measured_lc_level_mv(self) -> float | None:
        if not self.connected:
            return None
        with self._lock:
            return float(self._dev().lc_get_level())

    def move_rotator_deg(self, angle_deg: float) -> None:
        with self._lock:
            self._dev().rotator_move_absolute(float(angle_deg))

    def home_rotator(self) -> None:
        with self._lock:
            self._dev().rotator_move_home()

    def rotator_angle_deg(self) -> float:
        with self._lock:
            return float(self._dev().rotator_get_position())

    def rotator_busy(self) -> bool:
        if not self.connected:
            return False
        with self._lock:
            return bool(self._dev().rotator_is_busy())

    def set_heater_enabled(self, enabled: bool) -> None:
        with self._lock:
            self._dev().heater_set_enabled(bool(enabled))
            self._heater = bool(enabled)

    def heater_enabled(self) -> bool:
        return bool(self._heater)

    def heater_temperature_c(self) -> float | None:
        if not self.connected:
            return None
        with self._lock:
            return float(self._dev().heater_get_actual_temperature())

    def describe(self) -> dict:
        return {"kind": self.kind, "port": self.port or "auto", "firmware": self._firmware}


def create_bipd_filter(name: str | None = None, port: str | None = None) -> BipdFilterBase:
    """Build the filter named in the configuration ("specto" or "mock")."""
    bipd_config = CONFIG.get("bipd", {}) or {}
    if name is None:
        name = bipd_config.get("device", "specto")
    backend = str(name or "mock").strip().lower()

    if backend in ("mock", "none", ""):
        return MockBipdFilter()
    if backend in ("specto", "specto_bipd", "bipd", "81watts", "libspecto"):
        return SpectoBipdFilter(port if port is not None else bipd_config.get("port", ""))

    raise ValueError(f"Unknown BIPD filter '{backend}'. Expected one of: mock, specto.")


def validate_bipd_filter_contract(bipd) -> None:
    """Check at runtime that a filter honours the expected minimal contract."""
    required_methods = (
        "connect", "disconnect", "close",
        "set_lc_level_mv", "lc_level_mv", "measured_lc_level_mv",
        "move_rotator_deg", "home_rotator", "rotator_angle_deg", "rotator_busy",
        "set_heater_enabled", "heater_enabled", "heater_temperature_c",
        "describe", "state",
    )
    required_attrs = ("connected", "kind", "LC_MIN_MV", "LC_MAX_MV")

    missing = []
    for name in required_methods:
        if not callable(getattr(bipd, name, None)):
            missing.append(f"method:{name}")
    for name in required_attrs:
        if not hasattr(bipd, name):
            missing.append(f"attr:{name}")

    if missing:
        raise TypeError(
            f"BIPD filter {bipd.__class__.__name__} does not satisfy the BIPD filter "
            f"contract. Missing: {', '.join(missing)}"
        )
