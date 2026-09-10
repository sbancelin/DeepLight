"""How the power reaching the sample is set, whichever way a bench does it.

DeepLight already sets laser power in three unrelated ways: a Thorlabs rotation
mount turning a half-wave plate in front of a polariser, an Elliptec ELL14 doing
the same job for the Cobolt, and a direct command to a laser that can attenuate
itself. Which one applied was decided by matching the laser's *name*, and the
waveplate calibration -- speed, steps per degree, mounting offset -- travelled
down through every call as arguments.

A power actuator is the thing that answers "make it this many percent". It holds
its own calibration, because that is a property of the actuator and of nothing
else. An AOM, a motorised neutral-density wheel or a Pockels cell is one more
class here and nothing else in DeepLight changes.

Percent means percent of the maximum this actuator can deliver, 0 to 100. What
the maximum *is* in milliwatts is a property of the laser and its alignment, and
is deliberately not modelled here.
"""

from __future__ import annotations

from ..widgets.Log_Widget import logger


class PowerActuatorBase:
    """Minimal contract for anything that sets the power reaching the sample."""

    #: Shown in logs and in the saved provenance.
    kind = "power actuator"

    def set_power_percent(self, percent: float) -> None:
        raise NotImplementedError

    def get_power_percent(self) -> float | None:
        """Current setting, or None when the device cannot be read back.

        None is a real answer, not a failure: a stepper with no encoder knows
        only what it was last told, and pretending otherwise would put an
        invented number in the provenance.
        """
        return None

    @staticmethod
    def clamp(percent: float) -> float:
        return max(0.0, min(100.0, float(percent)))

    def describe(self) -> str:
        return self.kind


class MockPowerActuator(PowerActuatorBase):
    """Remembers what it was told, so the power path runs without a bench.

    Not a no-op: the depth-compensation ramp and the return to surface power
    both drive this during a simulated stack, and a mock that dropped the value
    would leave that logic unexercised.
    """

    kind = "simulated"

    def __init__(self, name: str = ""):
        self.name = str(name)
        self._percent = 0.0

    def set_power_percent(self, percent: float) -> None:
        self._percent = self.clamp(percent)
        logger.debug(f"[MockPower] {self.name or 'laser'} -> {self._percent:.3g} %")

    def get_power_percent(self) -> float | None:
        return self._percent


class RotationMountActuator(PowerActuatorBase):
    """A half-wave plate on a Thorlabs rotation mount, before a polariser.

    Turning the plate rotates the polarisation and the polariser converts that
    into a transmitted fraction. The calibration lives here rather than being
    passed in at every call: it belongs to this mount on this bench.
    """

    kind = "half-wave plate on a rotation mount"

    def __init__(self, controller, speed: int, steps_per_degree: float, offset_deg: float):
        self._controller = controller
        self.speed = int(speed)
        self.steps_per_degree = float(steps_per_degree)
        self.offset_deg = float(offset_deg)

    def set_power_percent(self, percent: float) -> None:
        self._controller.set_power_percent(
            self.clamp(percent),
            speed=self.speed,
            steps_per_degree=self.steps_per_degree,
            offset_deg=self.offset_deg,
        )

    def describe(self) -> str:
        return f"{self.kind} (offset {self.offset_deg:.3g} deg)"


class WaveplateActuator(PowerActuatorBase):
    """The same idea on an Elliptec ELL14, which needs only its mounting offset."""

    kind = "half-wave plate on an Elliptec mount"

    def __init__(self, controller, offset_deg: float):
        self._controller = controller
        self.offset_deg = float(offset_deg)

    def set_power_percent(self, percent: float) -> None:
        self._controller.set_power_percent(self.clamp(percent), offset_deg=self.offset_deg)

    def get_power_percent(self) -> float | None:
        try:
            return float(self._controller.get_power_percent(offset_deg=self.offset_deg))
        except Exception as e:
            logger.debug(f"[Power] ELL14 read-back failed: {e}")
            return None

    def describe(self) -> str:
        return f"{self.kind} (offset {self.offset_deg:.3g} deg)"


class DirectPowerActuator(PowerActuatorBase):
    """A laser that attenuates itself, told in percent over its own link."""

    kind = "laser's own power command"

    def __init__(self, laser_hardware, laser_name: str):
        self._laser = laser_hardware
        self.laser_name = str(laser_name)

    def set_power_percent(self, percent: float) -> None:
        self._laser.set_power_percent(self.laser_name, self.clamp(percent))

    def get_power_percent(self) -> float | None:
        try:
            return float(self._laser.get_power_percent(self.laser_name))
        except Exception as e:
            logger.debug(f"[Power] {self.laser_name} read-back failed: {e}")
            return None

    def describe(self) -> str:
        return f"{self.kind} ({self.laser_name})"


def validate_power_actuator_contract(actuator) -> None:
    """Check at runtime that an actuator honours the expected minimal contract."""
    missing = [
        f"method:{name}"
        for name in ("set_power_percent", "get_power_percent", "describe")
        if not callable(getattr(actuator, name, None))
    ]
    if not hasattr(actuator, "kind"):
        missing.append("attr:kind")

    if missing:
        raise TypeError(
            f"Power actuator {actuator.__class__.__name__} does not satisfy the "
            f"power actuator contract. Missing: {', '.join(missing)}"
        )
