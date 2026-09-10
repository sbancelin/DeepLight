"""The polarisation chain: a rotating half-wave plate and a fixed compensator.

Two plates sit before the objective. What each one is for:

* The **half-wave plate** sets the azimuth. Rotating it by an angle turns linear
  polarisation by twice that angle, so reaching an azimuth is one halving and
  nothing else. One number describes the mount: the plate angle at which the
  polarisation comes out horizontal.

* The **quarter-wave plate** is a compensator. It corrects the ellipticity the
  optics upstream introduce -- dichroics and the scan mirrors are not neutral --
  and it does not scan. It is parked on one of three measured positions:
  linear, right circular, left circular.

This replaced a calibration table that listed both plate positions for every
azimuth in ten-degree steps and interpolated between them. That table existed
because both plates were turned together; with the compensator standing still,
the azimuth is arithmetic and there is nothing left to tabulate -- no file to
regenerate when a mount is remounted, and no interpolation between measured
points to be wrong about.

Adding another brand of mount means writing one WaveplateRotatorBase.
"""

from __future__ import annotations

from ..widgets.Log_Widget import logger

#: The three positions a compensator is parked on.
COMPENSATOR_STATES = ("linear", "CD", "CG")

#: Settings key holding each one, on the quarter-wave plate's axis.
COMPENSATOR_SETTING_KEYS = {"linear": "linear_deg", "CD": "cd_deg", "CG": "cg_deg"}


def half_wave_angle_for_azimuth(azimuth_deg: float, zero_deg: float = 0.0) -> float:
    """Plate angle producing this polarisation azimuth, in degrees.

    A half-wave plate reflects the polarisation about its own fast axis, so the
    polarisation turns by twice the plate. Half the azimuth is the whole
    calculation; `zero_deg` is the plate angle at which the light comes out
    horizontal.
    """
    return float(zero_deg) + float(azimuth_deg) / 2.0


def azimuth_for_half_wave_angle(plate_deg: float, zero_deg: float = 0.0) -> float:
    """The azimuth a plate at this angle produces -- the inverse of the above."""
    return 2.0 * (float(plate_deg) - float(zero_deg))


class WaveplateRotatorBase:
    """Minimal contract for a rotation mount carrying a waveplate.

    Angles are in degrees throughout, in the mount's own frame; what a given
    angle does to the polarisation is the caller's business.
    """

    #: Shown in logs and in the saved provenance.
    kind = "waveplate rotator"

    def __init__(self):
        self.connected = False

    # ---------- lifecycle ----------
    def connect(self) -> None:
        raise NotImplementedError

    def disconnect(self) -> None:
        raise NotImplementedError

    def close(self) -> None:
        """Drop the connection. Alias for disconnect(), as elsewhere."""
        self.disconnect()

    # ---------- motion ----------
    def set_angle_deg(self, angle_deg: float, blocking: bool = True) -> None:
        raise NotImplementedError

    def get_angle_deg(self) -> float:
        raise NotImplementedError

    def describe(self) -> str:
        return self.kind


class MockWaveplateRotator(WaveplateRotatorBase):
    """A mount in software: goes where it is told, instantly.

    Real enough to exercise a polarisation sweep with no bench, which is what
    the acquisition tests rely on.
    """

    kind = "simulated rotation mount"

    def __init__(self, name: str = ""):
        super().__init__()
        self.name = str(name)
        self._angle = 0.0

    def connect(self) -> None:
        self.connected = True

    def disconnect(self) -> None:
        self.connected = False

    def set_angle_deg(self, angle_deg: float, blocking: bool = True) -> None:
        if not self.connected:
            raise RuntimeError("Waveplate rotator is not connected.")
        self._angle = float(angle_deg)
        logger.debug(f"[MockWaveplate] {self.name or 'plate'} -> {self._angle:.3f} deg")

    def get_angle_deg(self) -> float:
        return float(self._angle)


class ElliptecWaveplateRotator(WaveplateRotatorBase):
    """A Thorlabs Elliptec ELL14 on a shared bus.

    Wraps the existing controller rather than replacing it: the bus protocol is
    unchanged, and this only gives it the shape the rest of DeepLight expects.
    """

    kind = "Elliptec ELL14"

    def __init__(self, controller, address: str = ""):
        super().__init__()
        self._controller = controller
        self.address = str(address)

    def connect(self) -> None:
        self._controller.connect()
        self.connected = bool(getattr(self._controller, "connected", True))

    def disconnect(self) -> None:
        try:
            self._controller.close()
        finally:
            self.connected = False

    def set_angle_deg(self, angle_deg: float, blocking: bool = True) -> None:
        self._controller.move_to_angle_deg(float(angle_deg), blocking=blocking)

    def get_angle_deg(self) -> float:
        return float(self._controller.get_angle_deg())

    def describe(self) -> str:
        return f"{self.kind} (address {self.address})" if self.address else self.kind


def create_waveplate_rotator(name: str | None = None, controller=None,
                             address: str = "") -> WaveplateRotatorBase:
    """Build a rotator for a backend name ("mock" or "elliptec"/"nidaq")."""
    backend = str(name or "mock").strip().lower()

    if backend in ("mock", "none", ""):
        return MockWaveplateRotator(address)
    if backend in ("elliptec", "ell14", "nidaq", "thorlabs"):
        if controller is None:
            raise ValueError("An Elliptec rotator needs its bus controller.")
        return ElliptecWaveplateRotator(controller, address=address)

    raise ValueError(
        f"Unknown waveplate rotator '{backend}'. Expected one of: mock, elliptec."
    )


def validate_waveplate_rotator_contract(rotator) -> None:
    """Check at runtime that a rotator honours the expected minimal contract."""
    missing = [
        f"method:{name}"
        for name in ("connect", "disconnect", "close", "set_angle_deg",
                     "get_angle_deg", "describe")
        if not callable(getattr(rotator, name, None))
    ]
    for name in ("connected", "kind"):
        if not hasattr(rotator, name):
            missing.append(f"attr:{name}")

    if missing:
        raise TypeError(
            f"Waveplate rotator {rotator.__class__.__name__} does not satisfy the "
            f"waveplate rotator contract. Missing: {', '.join(missing)}"
        )
