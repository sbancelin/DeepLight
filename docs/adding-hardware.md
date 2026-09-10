# Adding your own hardware

DeepLight was written for one microscope, but the devices sit behind small
contracts so another bench can be supported by writing a class rather than by
editing the application. Each device family has the same three pieces:

- a **base class** saying what is required,
- a **simulated implementation**, so the path can be exercised with no bench,
- a **validator** that refuses an incomplete implementation, so a mistake shows
  up immediately instead of halfway through an acquisition.

| Family | Base class | Simulated | Validator |
|---|---|---|---|
| Microscope backend | `Microscope_Backend_Base` | `MockMicroscope` | `validate_backend_contract` |
| Camera | `CameraBackendBase` | `MockCameraBackend` | — |
| Detector | `DetectorManagerBase` | `MockDetectorManager` | `validate_detector_contract` |
| Spectrograph | `SpectrographBackendBase` | `MockSpectrograph` | `validate_spectrograph_contract` |
| Shutter | `ShutterBase` | `MockShutter` | `validate_shutter_contract` |
| Power actuator | `PowerActuatorBase` | `MockPowerActuator` | `validate_power_actuator_contract` |
| Waveplate rotator | `WaveplateRotatorBase` | `MockWaveplateRotator` | `validate_waveplate_rotator_contract` |

Two worked examples follow. They are short on purpose: if adding a device takes
more than a page, the contract is wrong.


## A shutter

The shutter decides whether light reaches the sample. DeepLight opens it when an
acquisition starts, shuts it when the run stops, and shuts it again while a dark
frame is measured.

Write one class in `gui/managers/Shutter_Manager.py`:

```python
class UniblitzShutter(ShutterBase):
    def __init__(self, port: str):
        super().__init__()
        self.port = port
        self._serial = None
        self._open = False

    def connect(self) -> None:
        self._serial = serial.Serial(self.port, 9600, timeout=1.0)
        self.connected = True
        self.set_open(False)          # start from a known state

    def disconnect(self) -> None:
        if self._serial is not None:
            self._serial.close()
        self._serial, self.connected, self._open = None, False, False

    def set_open(self, open_: bool) -> None:
        self._serial.write(b"@" if open_ else b"A")
        self._open = bool(open_)

    def is_open(self) -> bool:
        return self._open
```

then return it from `create_shutter()`:

```python
if backend in ("uniblitz",):
    return UniblitzShutter(CONFIG.uniblitz.port)
```

That is the whole change. Nothing else in DeepLight refers to a shutter brand.

**On naming.** The shut action is `set_open(False)`, not `close()`. Everywhere
else in `Hardware_Manager`, `close()` on a controller means *disconnect the
device*, and a shutter where `close()` might drop the connection is a trap in
exactly the situation where being wrong is expensive. `ShutterBase.close()`
exists, but it is an alias for `disconnect()`.

**Read-back.** If your shutter cannot report its state, keep the last commanded
value as the Thorlabs one does. `is_open()` must always answer.


## A power actuator

Setting "the power" means different things on different benches. DeepLight
already does it three ways — a half-wave plate on a Thorlabs rotation mount
before a polariser, the same idea on an Elliptec mount, and a direct command to
a laser that attenuates itself. An acousto-optic modulator or a motorised
neutral-density wheel is one more class:

```python
class AomActuator(PowerActuatorBase):
    kind = "acousto-optic modulator"

    def __init__(self, ni_channel: str, calibration):
        self.ni_channel = ni_channel
        self.calibration = calibration      # percent -> volts, measured once

    def set_power_percent(self, percent: float) -> None:
        write_analog(self.ni_channel, self.calibration(self.clamp(percent)))

    def get_power_percent(self) -> float | None:
        return None                          # write-only: say so, do not invent
```

then return it from `HardwareManager.power_actuator()` for the laser it drives.

**Percent of what.** Percent of the maximum this actuator can deliver, 0 to 100.
How many milliwatts that is depends on the laser and the alignment, and is
deliberately not modelled.

**Calibration belongs to the actuator.** Speed, steps per degree, mounting
offset: these describe the mount, not the request. Passing them at every call is
what the rotation-mount actuator used to do, and it leaked mount-specific
arguments into every caller up the chain.

**`get_power_percent()` may return `None`.** A stepper with no encoder knows
only what it was last told. `None` is a real answer; an invented number would
end up in the provenance beside the data.


## A waveplate rotator

Two plates sit before the objective and they do different jobs, which is worth
knowing before replacing either.

The **half-wave plate** carries the azimuth. Rotating it turns linear
polarisation by twice its own angle, so a scan to azimuth θ drives it to θ/2.
One number describes the mount — the plate angle at which the light comes out
horizontal — and it is entered as *Horizontal at* in the positioner settings.

The **quarter-wave plate** is a compensator. It corrects the ellipticity the
dichroics and scan mirrors introduce, and it does not scan: it is parked on one
of three measured positions (linear, CD, CG), entered in the same settings and
reachable from the *Lin* / *CD* / *CG* buttons.

A mount of another brand is one class:

```python
class NewportRotator(WaveplateRotatorBase):
    kind = "Newport rotation stage"

    def connect(self) -> None:
        self._stage = newport.open(self.port)
        self.connected = True

    def disconnect(self) -> None:
        self._stage.close()
        self.connected = False

    def set_angle_deg(self, angle_deg: float, blocking: bool = True) -> None:
        self._stage.move_absolute(angle_deg, wait=blocking)

    def get_angle_deg(self) -> float:
        return self._stage.position()
```

then return it from `create_waveplate_rotator()`.

**There is no calibration table.** An earlier version listed both plate
positions for every azimuth in ten-degree steps and interpolated between them,
because both plates turned together. With the compensator standing still the
azimuth is arithmetic, so there is no file to regenerate when a mount is
remounted and no interpolation to be wrong about. If you are porting a bench
that still works the old way, the thing to measure is the two numbers above,
not a table.


## Checking your work

The validator is the specification, so call it:

```python
from DeepLight.gui.managers.Shutter_Manager import validate_shutter_contract

validate_shutter_contract(UniblitzShutter("COM7"))   # raises if anything is missing
```

`tests/test_hardware_contracts.py` does this for every family. Adding your class
to it is one line, and it is what tells you the day a contract gains a method.


## Where settings go

Anything that differs between two installations — a COM port, a serial number, a
driver path, a calibration offset — belongs in the configuration file, described
in `config.py`. Constants that are properties of a hardware *model* rather than
of an installation stay in the code, where they cannot be adjusted into being
wrong.
