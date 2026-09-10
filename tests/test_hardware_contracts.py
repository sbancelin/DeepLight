"""Every swappable device honours its contract.

This is what "you can put your own instrument behind it" means in practice:
each family has a base class stating what is required, a simulated
implementation, and a validator. Writing a new backend means satisfying the
validator -- and these tests are what a contributor runs to know they have.
"""

import pytest

from DeepLight.gui.managers.Camera_Manager import CameraBackendBase
from DeepLight.gui.managers.Detector_Manager_Base import validate_detector_contract
from DeepLight.gui.managers.Microscopes.Microscope_Backend_Base import (
    validate_backend_contract,
)
from DeepLight.gui.managers.Shutter_Manager import validate_shutter_contract
from DeepLight.gui.managers.Spectrograph_Manager import (
    create_spectrograph,
    validate_spectrograph_contract,
)


@pytest.mark.parametrize("backend_name", ["mock"])
def test_a_microscope_backend_satisfies_its_contract(qapp, backend_name):
    from DeepLight.gui.managers.Microscopes import create_microscope_backend

    backend = create_microscope_backend(backend_name)
    validate_backend_contract(backend)          # raises if anything is missing


def test_an_incomplete_microscope_backend_is_rejected():
    """The validator has to actually reject something, or it proves nothing."""
    class Halfway:
        def configure(self, scan_parameters): ...

    with pytest.raises((TypeError, AttributeError, ValueError)):
        validate_backend_contract(Halfway())


def test_a_camera_backend_satisfies_its_contract(qapp):
    from DeepLight.gui.managers.Motic_Camera_Manager import MockCameraBackend

    camera = MockCameraBackend()
    for method in ("connect", "disconnect", "set_parameters", "snap",
                   "start_live", "stop_live", "get_frame"):
        assert callable(getattr(camera, method)), method

    assert isinstance(camera, CameraBackendBase)

    camera.connect()
    frame = camera.snap()
    assert frame.ndim in (2, 3) and frame.size > 0


def test_the_raman_camera_is_the_same_kind_of_object_as_the_widefield_one(qapp):
    """One camera contract for the whole application: swapping the Raman
    detector is writing a CameraBackendBase, nothing more."""
    from DeepLight.gui.managers.Raman_Spectrometer import create_raman_camera

    assert isinstance(create_raman_camera("mock"), CameraBackendBase)


def test_a_detector_manager_satisfies_its_contract(qapp):
    from DeepLight.gui.managers.Detector_Manager import MockDetectorManager

    validate_detector_contract(MockDetectorManager())


def test_a_spectrograph_satisfies_its_contract():
    spectrograph = create_spectrograph("mock")
    validate_spectrograph_contract(spectrograph)


def test_a_shutter_satisfies_its_contract():
    from DeepLight.gui.managers.Shutter_Manager import create_shutter

    validate_shutter_contract(create_shutter("mock"))
    validate_shutter_contract(create_shutter("thorlabs", "68800404"))


def test_the_simulated_shutter_actually_moves():
    """A mock that ignored the command would leave the shutter logic untested,
    which is what it did before it existed: a dark frame is only dark if
    something shut."""
    from DeepLight.gui.managers.Shutter_Manager import create_shutter

    shutter = create_shutter("mock")
    shutter.connect()

    assert shutter.is_open() is False        # found shut, never assumed open
    shutter.set_open(True)
    assert shutter.is_open() is True
    shutter.set_open(False)
    assert shutter.is_open() is False


def test_a_shutter_refuses_to_move_before_it_is_connected():
    from DeepLight.gui.managers.Shutter_Manager import MockShutter

    with pytest.raises(RuntimeError, match="not connected"):
        MockShutter().set_open(True)


def test_close_disconnects_a_shutter_rather_than_shutting_it():
    """The one naming trap in this family, pinned so it cannot be reintroduced:
    close() means disconnect, the light is moved with set_open()."""
    from DeepLight.gui.managers.Shutter_Manager import MockShutter

    shutter = MockShutter()
    shutter.connect()
    shutter.set_open(True)
    shutter.close()

    assert shutter.connected is False


def test_the_power_actuators_satisfy_their_contract():
    from DeepLight.gui.managers.Power_Actuator import (
        DirectPowerActuator, MockPowerActuator, RotationMountActuator,
        WaveplateActuator, validate_power_actuator_contract,
    )

    for actuator in (MockPowerActuator("Mira 900"),
                     RotationMountActuator(None, speed=1, steps_per_degree=1.0, offset_deg=0.0),
                     WaveplateActuator(None, offset_deg=0.0),
                     DirectPowerActuator(None, "Alcor 920")):
        validate_power_actuator_contract(actuator)


def test_a_new_power_mechanism_needs_one_small_class():
    """The extensibility claim, checked rather than asserted: an actuator for a
    mechanism DeepLight has never seen satisfies the contract as written."""
    from DeepLight.gui.managers.Power_Actuator import (
        PowerActuatorBase, validate_power_actuator_contract,
    )

    class AomActuator(PowerActuatorBase):
        kind = "acousto-optic modulator"

        def __init__(self):
            self.volts = None

        def set_power_percent(self, percent):
            self.volts = self.clamp(percent) / 100.0

        def get_power_percent(self):
            return None                    # write-only, and says so

    actuator = AomActuator()
    validate_power_actuator_contract(actuator)

    actuator.set_power_percent(250.0)
    assert actuator.volts == 1.0           # clamped by the base class
    assert actuator.get_power_percent() is None


def test_an_incomplete_power_actuator_is_rejected():
    from DeepLight.gui.managers.Power_Actuator import validate_power_actuator_contract

    with pytest.raises(TypeError, match="does not satisfy"):
        validate_power_actuator_contract(object())


def test_the_simulated_microscope_needs_no_hardware_library(qapp):
    """The claim that the whole application runs without a bench: if any driver
    were imported eagerly, this would fail on a machine without the SDKs."""
    from DeepLight.gui.managers.Microscopes import create_microscope_backend

    backend = create_microscope_backend("mock")
    backend.configure({
        "rows": [{"axis": "X-Galvo", "pixels": 32, "size_um": 10.0},
                 {"axis": "Y-Galvo", "pixels": 32, "size_um": 10.0}],
        "pixel_values": [32, 32, 1, 1],
        "axis_order": ["X-Galvo", "Y-Galvo", "None", "None"],
        "active_axes": ["X-Galvo", "Y-Galvo"],
        "active_channels": ["PMT-Vis"],
        "dwell_time": 2e-6,
        "repetitions": 1,
    })
    assert backend.dim_image_x > 0 and backend.dim_image_y > 0


def test_an_unknown_backend_name_says_so(qapp):
    from DeepLight.gui.managers.Microscopes import create_microscope_backend

    with pytest.raises(ValueError, match="Unknown backend"):
        create_microscope_backend("telescope")
