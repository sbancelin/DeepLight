"""The polarisation chain: one plate per job, never both at once.

The optics' ellipticity is corrected by a fixed compensator outside DeepLight.
The half-wave plate carries the azimuth and goes to half of it; the quarter-wave
plate sets the state -- linear, right or left circular -- on measured positions,
and stands still while the azimuth is scanned.
"""

import pytest

from DeepLight.gui.managers.Positioner_Manager import MockPositionerManager
from DeepLight.gui.managers.Waveplate_Rotator import (
    QUARTER_WAVE_SETTING_KEYS,
    QUARTER_WAVE_STATES,
    azimuth_for_half_wave_angle,
    half_wave_angle_for_azimuth,
    validate_waveplate_rotator_contract,
)


@pytest.fixture
def positioner():
    manager = MockPositionerManager(["x", "y", "z", "p", "p4"])
    for axis in ("p", "p4"):
        manager.set_limits(axis, -360.0, 360.0, max_speed=430.0, tolerance=0.1)
    return manager


@pytest.mark.parametrize("azimuth, plate", [
    (0.0, 0.0),
    (45.0, 22.5),
    (90.0, 45.0),
    (157.5, 78.75),
    (180.0, 90.0),
])
def test_the_plate_turns_by_half_the_azimuth(azimuth, plate):
    """A half-wave plate reflects the polarisation about its own fast axis, so
    the polarisation turns by twice the plate."""
    assert half_wave_angle_for_azimuth(azimuth) == pytest.approx(plate)


def test_the_zero_is_the_angle_where_the_light_comes_out_horizontal():
    """One number describes the mount, and it shifts every azimuth alike."""
    assert half_wave_angle_for_azimuth(90.0, zero_deg=28.97) == pytest.approx(28.97 + 45.0)
    assert half_wave_angle_for_azimuth(0.0, zero_deg=28.97) == pytest.approx(28.97)


def test_the_azimuth_can_be_read_back_from_a_plate_angle():
    for azimuth in (0.0, 33.0, 90.0, 175.0):
        plate = half_wave_angle_for_azimuth(azimuth, zero_deg=12.5)
        assert azimuth_for_half_wave_angle(plate, zero_deg=12.5) == pytest.approx(azimuth)


def test_a_polarisation_scan_turns_only_the_half_wave_plate(positioner):
    positioner.set_quarter_wave_positions(linear=12.0, CD=53.48, CG=56.16)
    positioner.move_from_scan("Polarization", 90.0, 430.0, 0.0, "polar_step")

    assert positioner._state["p"].target_abs == pytest.approx(45.0)


def test_the_quarter_wave_plate_stands_still_through_an_azimuth_series(positioner):
    """Parked on linear at the first point, then never moved again: the two
    plates are not driven together any more."""
    positioner.set_quarter_wave_positions(linear=12.0, CD=53.48, CG=56.16)
    positioner.move_from_scan("Polarization", 0.0, 430.0, 0.0, "polar_step")
    positioner._state["p4"].target_abs = None             # forget the parking move

    for azimuth in (10.0, 45.0, 90.0, 135.0, 180.0):
        positioner.move_from_scan("Polarization", azimuth, 430.0, 0.0, "polar_step")
        assert positioner._state["p"].target_abs == pytest.approx(azimuth / 2.0)
        assert positioner._state["p4"].target_abs is None


def test_the_quarter_wave_plate_is_parked_on_linear_when_a_scan_starts(positioner):
    """A P-SHG series only means something in linear polarisation, so the
    software puts the plate there rather than trusting where it was left."""
    positioner.set_quarter_wave_positions(linear=12.0, CD=53.48, CG=56.16)
    positioner.move_from_scan("Polarization", 0.0, 430.0, 0.0, "polar_step")

    assert positioner._state["p4"].target_abs == pytest.approx(12.0)


def test_an_unconfigured_quarter_wave_position_is_refused(positioner):
    """Driving the plate somewhere arbitrary would silently change the
    polarisation reaching the sample; declining says so instead."""
    assert positioner.move_quarter_wave("linear") is False

    positioner.set_quarter_wave_positions(linear=12.0)
    assert positioner.move_quarter_wave("linear") is True
    assert positioner.move_quarter_wave("CD") is False       # never measured


def test_a_state_the_quarter_wave_plate_has_no_name_for_is_refused(positioner):
    positioner.set_quarter_wave_positions(linear=12.0)

    assert positioner.move_quarter_wave("elliptical") is False
    assert set(QUARTER_WAVE_STATES) == {"linear", "CD", "CG"}
    assert set(QUARTER_WAVE_SETTING_KEYS) == set(QUARTER_WAVE_STATES)


def test_the_quarter_wave_plate_reports_which_position_it_sits_on(positioner):
    positioner.set_quarter_wave_positions(linear=12.0, CD=53.48, CG=56.16)

    positioner.move_quarter_wave("CD")
    positioner._state["p4"].abs_pos = 53.48                 # arrived
    assert positioner.quarter_wave_state() == "CD"

    positioner._state["p4"].abs_pos = 30.0                  # somewhere else
    assert positioner.quarter_wave_state() is None


def test_the_plate_returns_where_it_started_after_a_scan(positioner):
    positioner.set_quarter_wave_positions(linear=12.0)
    positioner._state["p"].abs_pos = 7.5

    positioner.move_from_scan("Polarization", 90.0, 430.0, 0.0, "polar_step")
    positioner.move_from_scan("Polarization", 0.0, 430.0, 0.0, "polar_return_to_base")

    assert positioner._state["p"].target_abs == pytest.approx(7.5)


def test_a_new_scan_remembers_its_own_starting_angle(positioner):
    """The start of one run is not carried over into the next."""
    positioner._state["p"].abs_pos = 7.5
    positioner.move_from_scan("Polarization", 90.0, 430.0, 0.0, "polar_step")
    positioner.move_from_scan("Polarization", 0.0, 430.0, 0.0, "polar_return_to_base")

    positioner._state["p"].abs_pos = 20.0
    positioner.move_from_scan("Polarization", 90.0, 430.0, 0.0, "polar_step")
    positioner.move_from_scan("Polarization", 0.0, 430.0, 0.0, "polar_return_to_base")

    assert positioner._state["p"].target_abs == pytest.approx(20.0)


def test_a_rotator_satisfies_its_contract():
    from DeepLight.gui.managers.Waveplate_Rotator import create_waveplate_rotator

    validate_waveplate_rotator_contract(create_waveplate_rotator("mock"))


def test_the_simulated_rotator_goes_where_it_is_told():
    from DeepLight.gui.managers.Waveplate_Rotator import MockWaveplateRotator

    rotator = MockWaveplateRotator("lambda/2")
    with pytest.raises(RuntimeError, match="not connected"):
        rotator.set_angle_deg(45.0)

    rotator.connect()
    rotator.set_angle_deg(78.75)
    assert rotator.get_angle_deg() == pytest.approx(78.75)


def test_another_brand_of_mount_needs_one_small_class():
    """The extensibility claim, checked rather than asserted."""
    from DeepLight.gui.managers.Waveplate_Rotator import WaveplateRotatorBase

    class NewportRotator(WaveplateRotatorBase):
        kind = "Newport rotation stage"

        def __init__(self):
            super().__init__()
            self.angle = 0.0

        def connect(self):
            self.connected = True

        def disconnect(self):
            self.connected = False

        def set_angle_deg(self, angle_deg, blocking=True):
            self.angle = float(angle_deg)

        def get_angle_deg(self):
            return self.angle

    rotator = NewportRotator()
    validate_waveplate_rotator_contract(rotator)

    rotator.connect()
    rotator.set_angle_deg(half_wave_angle_for_azimuth(135.0))
    assert rotator.get_angle_deg() == pytest.approx(67.5)


def test_an_incomplete_rotator_is_rejected():
    with pytest.raises(TypeError, match="does not satisfy"):
        validate_waveplate_rotator_contract(object())


def test_there_is_no_calibration_table_any_more():
    """Both plates are never placed together from a table: the module and its
    CSV are gone, and nothing should quietly bring them back."""
    import importlib.util

    assert importlib.util.find_spec("DeepLight.gui.managers.Polarization_Table") is None
