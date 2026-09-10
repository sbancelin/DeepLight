"""Shutter contract, a mock, and the Thorlabs KCube solenoid.

The shutter is the one device that decides whether light reaches the sample. It
is opened when an acquisition starts, shut when it stops, and shut again while a
dark frame is measured -- so its behaviour is worth being able to exercise
without a bench, which until now it was not: the simulated backend simply did
nothing when asked to open or close.

Adding another brand means writing one class against ShutterBase and returning
it from create_shutter(). Nothing else in DeepLight changes.

A note on naming. Everywhere else in Hardware_Manager, ``close()`` on a
controller means *disconnect the device*. Calling the shut action ``close()``
too would make ``shutter.close()`` ambiguous in exactly the situation where
being wrong is expensive, so the light is moved with ``set_open(bool)`` and the
connection is dropped with ``disconnect()``.
"""

from __future__ import annotations

from ...config import CONFIG
from ..widgets.Log_Widget import logger


class ShutterBase:
    """Minimal contract for a DeepLight shutter."""

    def __init__(self):
        self.connected = False

    # ---------- lifecycle ----------
    def connect(self) -> None:
        raise NotImplementedError

    def disconnect(self) -> None:
        raise NotImplementedError

    def close(self) -> None:
        """Drop the connection.

        An alias for disconnect(), kept because Hardware_Manager tears every
        controller down by calling close() on it. It does *not* shut the
        shutter -- use set_open(False) for that.
        """
        self.disconnect()

    # ---------- the light ----------
    def set_open(self, open_: bool) -> None:
        raise NotImplementedError

    def is_open(self) -> bool:
        raise NotImplementedError


class MockShutter(ShutterBase):
    """A shutter in software: remembers its state and says what it was told.

    Not a no-op on purpose. The acquisition opens and shuts the shutter at
    defined moments, and a dark frame is only a dark frame if it was actually
    shut; a mock that ignores the command would let that logic go untested.
    """

    def __init__(self):
        super().__init__()
        self._open = False

    def connect(self) -> None:
        self.connected = True
        self._open = False          # a shutter is found shut, never assumed open
        logger.debug("[MockShutter] connected")

    def disconnect(self) -> None:
        self._open = False
        self.connected = False
        logger.debug("[MockShutter] disconnected")

    def set_open(self, open_: bool) -> None:
        if not self.connected:
            raise RuntimeError("Shutter is not connected.")
        self._open = bool(open_)
        logger.debug(f"[MockShutter] {'open' if self._open else 'shut'}")

    def is_open(self) -> bool:
        return bool(self._open)


class ThorlabsSolenoidShutter(ShutterBase):
    """Thorlabs KCube solenoid, driven through Kinesis.

    Wraps the existing private controller rather than replacing it: the .NET
    handling is unchanged, and this class only gives it the shape the rest of
    DeepLight now expects. The hardware offers no read-back, so is_open()
    reports the last state commanded.
    """

    def __init__(self, serial: str):
        super().__init__()
        self.serial = str(serial)
        self._controller = None
        self._open = False

    def _ensure_controller(self):
        if self._controller is None:
            # Imported here: Hardware_Manager imports this module, so importing
            # it back at module level would close the loop.
            from .Hardware_Manager import _ThorlabsShutterController

            self._controller = _ThorlabsShutterController(self.serial)
        return self._controller

    def connect(self) -> None:
        controller = self._ensure_controller()
        controller.connect()
        self.connected = bool(controller.connected)
        self.set_open(False)        # known state before anything is acquired

    def disconnect(self) -> None:
        if self._controller is not None:
            self._controller.close()
        self.connected = False
        self._open = False

    def set_open(self, open_: bool) -> None:
        self._ensure_controller().set_open(bool(open_))
        self._open = bool(open_)

    def is_open(self) -> bool:
        return bool(self._open)


def create_shutter(name: str | None = None, serial: str | None = None) -> ShutterBase:
    """Build the shutter for a backend name ("mock" or "nidaq"/"thorlabs")."""
    backend = str(name or "mock").strip().lower()

    if backend in ("mock", "none", ""):
        return MockShutter()
    if backend in ("nidaq", "thorlabs", "kcube", "solenoid"):
        return ThorlabsSolenoidShutter(serial or CONFIG.thorlabs.shutter_serial)

    raise ValueError(
        f"Unknown shutter '{backend}'. Expected one of: mock, thorlabs."
    )


def validate_shutter_contract(shutter) -> None:
    """Check at runtime that a shutter honours the expected minimal contract."""
    required_methods = ("connect", "disconnect", "close", "set_open", "is_open")
    required_attrs = ("connected",)

    missing = []
    for name in required_methods:
        if not callable(getattr(shutter, name, None)):
            missing.append(f"method:{name}")
    for name in required_attrs:
        if not hasattr(shutter, name):
            missing.append(f"attr:{name}")

    if missing:
        raise TypeError(
            f"Shutter {shutter.__class__.__name__} does not satisfy the shutter "
            f"contract. Missing: {', '.join(missing)}"
        )
