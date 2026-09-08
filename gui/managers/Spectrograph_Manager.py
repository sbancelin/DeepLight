"""Spectrograph contract, a mock, and the Princeton Instruments IsoPlane 320.

A spectrograph is the half of the Raman chain that decides *which wavelength
lands on which camera column*. It owns the grating, the centre wavelength and
the slit; it does not acquire anything. The camera is a separate object behind
CameraBackendBase, and RamanSpectrometer composes the two.

Splitting it this way means the wavelength axis is computed in one place, from
the optics, rather than being guessed by whoever holds the camera frame.
"""

from __future__ import annotations

import time

import numpy as np

from ...config import CONFIG
from ..widgets.Log_Widget import logger

try:
    import serial
except Exception as _exc:  # pragma: no cover - pyserial is a hard dependency
    serial = None
    logger.debug(f"[Spectrograph] pyserial unavailable: {_exc}")


class SpectrographBackendBase:
    """Minimal contract common to the DeepLight spectrographs.

    Wavelengths are in nm, the slit width in µm, focal length in mm and the
    groove density in grooves/mm throughout.
    """

    def __init__(self):
        self.connected = False
        self.focal_length_mm = 320.0
        self.groove_density_per_mm = 600.0
        self.diffraction_order = 1

    # ---------- lifecycle ----------
    def connect(self) -> None:
        raise NotImplementedError

    def disconnect(self) -> None:
        raise NotImplementedError

    # ---------- capabilities ----------
    def list_gratings(self) -> list[dict]:
        """Installed gratings, as [{"index": 1, "grooves_per_mm": 600.0, ...}]."""
        return []

    # ---------- settings ----------
    def get_grating(self) -> int:
        raise NotImplementedError

    def set_grating(self, index: int) -> None:
        raise NotImplementedError

    def get_center_wavelength_nm(self) -> float:
        raise NotImplementedError

    def set_center_wavelength_nm(self, value_nm: float) -> None:
        raise NotImplementedError

    def get_slit_width_um(self) -> float:
        return 0.0

    def set_slit_width_um(self, value_um: float) -> None:
        """Optional: motorised slits are not present on every unit."""
        return

    # ---------- optics ----------
    def reciprocal_linear_dispersion_nm_per_mm(self) -> float:
        """First-order dispersion at the focal plane, in nm/mm.

        Czerny-Turner small-angle approximation: 1e6 / (m * G * f), with G the
        groove density in grooves/mm and f the focal length in mm. It is good
        enough to label an axis, but it is *not* a calibration: a real setup
        should override the axis with a measured polynomial (see
        wavelength_axis_nm and the raman.calibration_poly config key).
        """
        g = float(self.groove_density_per_mm)
        f = float(self.focal_length_mm)
        m = max(1, int(self.diffraction_order))
        if g <= 0.0 or f <= 0.0:
            return 0.0
        return 1.0e6 / (m * g * f)

    def wavelength_axis_nm(self, n_pixels: int, pixel_size_um: float) -> np.ndarray:
        """Wavelength of each camera column, centred on the current setting.

        A measured calibration polynomial always wins when one is configured:
        the optical model below ignores the grating angle and the camera's
        position on the focal plane, which a real bench does not.
        """
        n = max(1, int(n_pixels))
        centre = float(self.get_center_wavelength_nm())

        poly = list(CONFIG.raman.get("calibration_poly", []) or [])
        if poly:
            # numpy convention: highest order first, evaluated on the pixel index
            return np.polyval(poly, np.arange(n, dtype=np.float64))

        pixel_mm = float(pixel_size_um) / 1000.0
        nm_per_pixel = self.reciprocal_linear_dispersion_nm_per_mm() * pixel_mm
        offsets = (np.arange(n, dtype=np.float64) - (n - 1) / 2.0) * nm_per_pixel
        return centre + offsets


class MockSpectrograph(SpectrographBackendBase):
    """Software spectrograph: keeps the settings, moves instantly."""

    def __init__(self):
        super().__init__()
        self._grating = 1
        self._center_nm = 700.0
        self._slit_um = 50.0

    def connect(self) -> None:
        self.connected = True
        logger.info("[Spectrograph] mock connected")

    def disconnect(self) -> None:
        self.connected = False

    def list_gratings(self) -> list[dict]:
        return [
            {"index": 1, "grooves_per_mm": 600.0, "blaze_nm": 500.0},
            {"index": 2, "grooves_per_mm": 1200.0, "blaze_nm": 750.0},
            {"index": 3, "grooves_per_mm": 1800.0, "blaze_nm": 500.0},
        ]

    def get_grating(self) -> int:
        return int(self._grating)

    def set_grating(self, index: int) -> None:
        self._grating = int(index)
        for g in self.list_gratings():
            if g["index"] == self._grating:
                self.groove_density_per_mm = float(g["grooves_per_mm"])
                break

    def get_center_wavelength_nm(self) -> float:
        return float(self._center_nm)

    def set_center_wavelength_nm(self, value_nm: float) -> None:
        self._center_nm = float(value_nm)

    def get_slit_width_um(self) -> float:
        return float(self._slit_um)

    def set_slit_width_um(self, value_um: float) -> None:
        self._slit_um = float(value_um)


class IsoPlane320Spectrograph(SpectrographBackendBase):
    """Princeton Instruments IsoPlane 320, over the Acton ASCII serial protocol.

    UNVERIFIED AGAINST HARDWARE. The structure, units, timeouts and error
    handling are real, but every command string in _CMD below was written from
    the documented Acton/SP protocol and has never been sent to this unit.
    Check them against the IsoPlane manual before the first run; if the unit is
    driven through the ARC_Instrument SDK instead of raw serial, only this class
    needs replacing -- nothing above it knows how the moves are sent.

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

        self.port = port or str(CONFIG.raman.spectrograph_port)
        self.baudrate = int(baudrate or CONFIG.raman.spectrograph_baudrate)
        self.timeout_s = float(
            timeout_s if timeout_s is not None else CONFIG.raman.spectrograph_timeout_s
        )
        self.focal_length_mm = float(CONFIG.raman.focal_length_mm)
        self.groove_density_per_mm = float(CONFIG.raman.groove_density_per_mm)

        self._serial = None

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

        answer = " ".join(chunks)
        # Strip the echo and the trailing ok, leaving the payload.
        if answer.startswith(payload):
            answer = answer[len(payload):]
        return answer.removesuffix("ok").strip()

    # ---------- lifecycle ----------
    def connect(self) -> None:
        if self.connected:
            return
        if serial is None:
            raise RuntimeError("pyserial is not installed; cannot reach the IsoPlane.")

        self._serial = serial.Serial(
            self.port, self.baudrate, timeout=self.timeout_s
        )
        self.connected = True
        logger.info(f"[IsoPlane] connected on {self.port} @ {self.baudrate}")

    def disconnect(self) -> None:
        if self._serial is not None:
            try:
                self._serial.close()
            except Exception as e:
                logger.debug(f"[IsoPlane] closing the port failed: {e}")
        self._serial = None
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
        for g in self.list_gratings():
            if g["index"] == int(index):
                self.groove_density_per_mm = float(g["grooves_per_mm"])
                break

    def list_gratings(self) -> list[dict]:
        """Parse the turret description the unit reports.

        The exact wording of ?GRATINGS is one of the things to confirm on the
        bench; anything unparseable is skipped rather than guessed at.
        """
        gratings = []
        try:
            answer = self._transceive(self._CMD["list_gratings"])
        except Exception as e:
            logger.warning(f"[IsoPlane] could not list the gratings: {e}")
            return gratings

        for line in answer.splitlines():
            parts = line.split()
            if len(parts) < 2:
                continue
            try:
                index = int(parts[0].strip(":"))
                grooves = float(parts[1])
            except ValueError:
                continue
            gratings.append({"index": index, "grooves_per_mm": grooves})
        return gratings


def create_spectrograph(name: str | None = None) -> SpectrographBackendBase:
    """Build the spectrograph named in the configuration ("mock" or "isoplane320")."""
    backend = str(name or CONFIG.raman.spectrograph or "mock").strip().lower()

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
        "wavelength_axis_nm",
    )
    required_attrs = ("connected", "focal_length_mm", "groove_density_per_mm")

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
