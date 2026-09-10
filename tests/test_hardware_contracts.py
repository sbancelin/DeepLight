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
