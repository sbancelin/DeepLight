"""Spectrograph contract, a mock, and the Princeton Instruments IsoPlane 320.

A spectrograph is the half of the Raman chain that decides *which wavelength
lands on which camera column*: it owns the grating turret, the centre
wavelength and the slit, and acquires nothing. The detector is a separate
object (Raman_Detector), and RamanSpectrometer composes the two.

It does not label the spectral axis either. Which pixel sees which wavelength
is measured in LightField against its calibration lamps and loaded as a table
(Spectral_Calibration); a spectrograph only reports the setting a table
belongs to -- grating and centre -- so the table is applied where it is valid
and nowhere else.

Moves are blocking: set_grating() and set_center_wavelength_nm() return once
the turret has arrived. A turret takes seconds, so callers run them off the
GUI thread (SpectroManager does).
"""

from __future__ import annotations

import time

from ...config import CONFIG
from ..widgets.Log_Widget import logger

try:
    import serial
except Exception as _exc:  # pragma: no cover - pyserial is a hard dependency
    serial = None
    logger.debug(f"[Spectrograph] pyserial unavailable: {_exc}")


class SpectrographBackendBase:
    """Minimal contract common to the DeepLight spectrographs.

    Wavelengths in nm, slit widths in µm, groove densities in grooves/mm.
    Gratings are named by their turret position, as the instrument numbers
    them; the groove density is a property of the grating found there.
    """

    def __init__(self):
        self.connected = False
        self.model = "unknown"

    # ---------- lifecycle ----------
    def connect(self) -> None:
        raise NotImplementedError

    def disconnect(self) -> None:
        raise NotImplementedError

    # ---------- capabilities ----------
    def list_gratings(self) -> list[dict]:
        """Installed gratings: [{"index": 1, "grooves_per_mm": 600.0, "blaze": "500 nm"}].

        "blaze" is the instrument's own description, or None when it gives
        none -- never a guess.
        """
        return []

    # ---------- settings ----------
    def get_grating(self) -> int:
        raise NotImplementedError

    def set_grating(self, index: int) -> None:
        """Select a turret position. Blocking until the turret has arrived."""
        raise NotImplementedError

    def get_center_wavelength_nm(self) -> float:
        raise NotImplementedError

    def set_center_wavelength_nm(self, value_nm: float) -> None:
        """Rotate the grating to centre this wavelength. Blocking."""
        raise NotImplementedError

    def get_slit_width_um(self) -> float | None:
        """Entrance slit width, or None where the slit is manual or not reported."""
        return None

    def set_slit_width_um(self, value_um: float) -> None:
        """Optional: motorised slits are not present on every unit."""
        return

    # ---------- derived ----------
    def grooves_per_mm(self, index: int | None = None) -> float | None:
        """Groove density at a turret position (the current one by default)."""
        index = self.get_grating() if index is None else int(index)
        for grating in self.list_gratings():
            if int(grating.get("index", -1)) == index:
                value = grating.get("grooves_per_mm")
                return None if value is None else float(value)
        return None

    def describe(self) -> dict:
        """The setting, for the record beside every spectrum."""
        index = self.get_grating()
        blaze = None
        for grating in self.list_gratings():
            if int(grating.get("index", -1)) == int(index):
                blaze = grating.get("blaze")
                break
        return {
            "model": self.model,
            "grating": int(index),
            "grooves_per_mm": self.grooves_per_mm(index),
            "blaze": blaze,
            "center_nm": float(self.get_center_wavelength_nm()),
            "slit_um": self.get_slit_width_um(),
        }


class MockSpectrograph(SpectrographBackendBase):
    """Software spectrograph: keeps the settings, and takes a little time to move.

    The delays are there so the window's "moving" state can be seen and
    exercised without the bench; they make no claim about the real turret.
    The three gratings are the bench's 600, 1200 and 1800 grooves/mm.
    """

    _GRATING_MOVE_S = 0.4
    _WAVELENGTH_MOVE_S_PER_NM = 0.0005

    def __init__(self):
        super().__init__()
        self.model = "mock"
        self._grating = 1
        # Where the simulated sample's Raman bands are (MockRamanDetector).
        self._center_nm = 870.0
        self._slit_um = None

    def connect(self) -> None:
        self.connected = True
        logger.info("[Spectrograph] mock connected")

    def disconnect(self) -> None:
        self.connected = False

    def list_gratings(self) -> list[dict]:
        return [
            {"index": 1, "grooves_per_mm": 600.0, "blaze": None},
            {"index": 2, "grooves_per_mm": 1200.0, "blaze": None},
            {"index": 3, "grooves_per_mm": 1800.0, "blaze": None},
        ]

    def get_grating(self) -> int:
        return int(self._grating)

    def set_grating(self, index: int) -> None:
        index = int(index)
        if index not in {g["index"] for g in self.list_gratings()}:
            raise ValueError(f"No grating at turret position {index}.")
        if index != self._grating:
            time.sleep(self._GRATING_MOVE_S)
        self._grating = index

    def get_center_wavelength_nm(self) -> float:
        return float(self._center_nm)

    def set_center_wavelength_nm(self, value_nm: float) -> None:
        value_nm = float(value_nm)
        if value_nm < 0.0:
            raise ValueError("A centre wavelength cannot be negative.")
        distance = abs(value_nm - self._center_nm)
        time.sleep(min(0.3, distance * self._WAVELENGTH_MOVE_S_PER_NM))
        self._center_nm = value_nm


class IsoPlane320Spectrograph(SpectrographBackendBase):
    """Princeton Instruments IsoPlane 320, over the Acton ASCII serial protocol.

    UNVERIFIED AGAINST HARDWARE, AND PENDING THE MANUFACTURER'S SDK. The
    structure, units, timeouts and error handling are real, but every command
    string in _CMD below was written from the documented Acton/SpectraPro
    protocol and has never been sent to this unit. The IsoPlane's own SDK will
    replace or confirm it; whichever way it goes, only this class changes --
    nothing above it knows how a move is sent.

    The protocol is line based: a command is echoed back, then the unit answers
    and terminates with "ok". Moves are slow (a grating turret takes seconds),
    so the read timeout is deliberately generous.
    """

    _CMD = {
        "get_wavelength": "?NM",          # -> "<value> nm"
        "goto_wavelength": "{value:.3f} GOTO",
        "get_grating": "?GRATING",        # -> "<index>"
        "set_grating": "{index:d} GRATING",
        "list_gratings": "?GRATINGS",
    }

    def __init__(self, port: str | None = None, baudrate: int | None = None,
                 timeout_s: float | None = None):
        super().__init__()
        self.model = "IsoPlane 320"

        self.port = port or str(CONFIG.raman.get("spectrograph_port", ""))
        self.baudrate = int(baudrate or CONFIG.raman.get("spectrograph_baudrate", 9600))
        self.timeout_s = float(
            timeout_s if timeout_s is not None
            else CONFIG.raman.get("spectrograph_timeout_s", 20.0)
        )

        self._serial = None
        self._gratings_cache = None

    # ---------- transport ----------
    def _transceive(self, payload: str) -> str:
        if self._serial is None:
            raise RuntimeError("IsoPlane not connected.")

        self._serial.reset_input_buffer()
        self._serial.write((payload + "\r").encode("ascii"))
        self._serial.flush()

        # The unit echoes the command, then answers, then sends "ok".
        deadline = time.monotonic() + self.timeout_s
        chunks = []
        while time.monotonic() < deadline:
            line = self._serial.readline().decode("ascii", errors="replace").strip()
            if not line:
                continue
            chunks.append(line)
            if line.endswith("ok"):
                break
        else:
            raise TimeoutError(f"IsoPlane did not answer {payload!r} in {self.timeout_s}s")

        answer = "\n".join(chunks)
        # Strip the echo and the trailing ok, leaving the payload.
        if answer.startswith(payload):
            answer = answer[len(payload):]
        return answer.strip().removesuffix("ok").strip()

    # ---------- lifecycle ----------
    def connect(self) -> None:
        if self.connected:
            return
        if serial is None:
            raise RuntimeError("pyserial is not installed; cannot reach the IsoPlane.")
        if not self.port:
            raise RuntimeError("No serial port configured for the IsoPlane (raman.spectrograph_port).")

        self._serial = serial.Serial(self.port, self.baudrate, timeout=self.timeout_s)
        self.connected = True
        logger.info(f"[IsoPlane] connected on {self.port} @ {self.baudrate}")

    def disconnect(self) -> None:
        if self._serial is not None:
            try:
                self._serial.close()
            except Exception as e:
                logger.debug(f"[IsoPlane] closing the port failed: {e}")
        self._serial = None
        self._gratings_cache = None
        self.connected = False

    # ---------- settings ----------
    def get_center_wavelength_nm(self) -> float:
        answer = self._transceive(self._CMD["get_wavelength"])
        return float(answer.split()[0])

    def set_center_wavelength_nm(self, value_nm: float) -> None:
        self._transceive(self._CMD["goto_wavelength"].format(value=float(value_nm)))

    def get_grating(self) -> int:
        return int(self._transceive(self._CMD["get_grating"]).split()[0])

    def set_grating(self, index: int) -> None:
        self._transceive(self._CMD["set_grating"].format(index=int(index)))

    def list_gratings(self) -> list[dict]:
        """Parse the turret description the unit reports.

        Cached per connection: the turret does not change while connected, and
        describe() asks for it with every spectrum. The exact wording of
        ?GRATINGS is one of the things to confirm on the bench; anything
        unparseable is skipped rather than guessed at.
        """
        if self._gratings_cache is not None:
            return list(self._gratings_cache)

        gratings = []
        try:
            answer = self._transceive(self._CMD["list_gratings"])
        except Exception as e:
            logger.warning(f"[IsoPlane] could not list the gratings: {e}")
            return gratings

        for line in answer.splitlines():
            parts = line.replace(">", " ").split()
            if len(parts) < 2:
                continue
            try:
                index = int(parts[0].strip(":"))
                grooves = float(parts[1])
            except ValueError:
                continue
            rest = " ".join(parts[2:]).strip() or None
            gratings.append({"index": index, "grooves_per_mm": grooves, "blaze": rest})

        self._gratings_cache = list(gratings)
        return gratings


def create_spectrograph(name: str | None = None) -> SpectrographBackendBase:
    """Build the spectrograph named in the configuration ("mock" or "isoplane320")."""
    backend = str(name or CONFIG.raman.get("spectrograph", "mock") or "mock").strip().lower()

    if backend in ("mock", "none", ""):
        return MockSpectrograph()
    if backend in ("isoplane", "isoplane320", "isoplane_320"):
        return IsoPlane320Spectrograph()

    raise ValueError(
        f"Unknown spectrograph '{backend}'. Expected one of: mock, isoplane320."
    )


def validate_spectrograph_contract(spectrograph) -> None:
    """Check at runtime that a spectrograph honours the expected minimal contract."""
    required_methods = (
        "connect",
        "disconnect",
        "list_gratings",
        "get_grating",
        "set_grating",
        "get_center_wavelength_nm",
        "set_center_wavelength_nm",
        "describe",
    )
    required_attrs = ("connected", "model")

    missing = []
    for name in required_methods:
        if not callable(getattr(spectrograph, name, None)):
            missing.append(f"method:{name}")
    for name in required_attrs:
        if not hasattr(spectrograph, name):
            missing.append(f"attr:{name}")

    if missing:
        raise TypeError(
            f"Spectrograph {spectrograph.__class__.__name__} does not satisfy the "
            f"spectrograph contract. Missing: {', '.join(missing)}"
        )
