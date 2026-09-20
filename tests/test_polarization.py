"""The polarisation chain, and the two ways it reaches an azimuth.

In compensator mode the half-wave plate carries the azimuth and goes to half of
it while the quarter-wave plate stands still on a measured position; in
calibration mode both plates are placed from a measured table. The same angle
therefore means a different pair of plate positions in each, which is what
these tests pin -- and why the mode has to travel with the data.
"""

import pytest

from DeepLight.gui.managers.Positioner_Manager import MockPositionerManager
from DeepLight.gui.managers.Waveplate_Rotator import (
    COMPENSATOR_SETTING_KEYS,
    DEFAULT_POLARIZATION_MODE,
    COMPENSATOR_STATES,
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
    """The compensator corrects the optics upstream, which does not depend on
    the azimuth -- turning it per point would be turning it for nothing."""
    positioner.set_compensator_positions(linear=12.0, CD=53.48, CG=56.16)
    positioner.move_from_scan("Polarization", 90.0, 430.0, 0.0, "polar_step")

    assert positioner._state["p"].target_abs == pytest.approx(45.0)


def test_the_compensator_is_parked_on_linear_when_a_scan_starts(positioner):
    """A P-SHG series only means something in linear polarisation, so the
    software puts the plate there rather than trusting where it was left."""
    positioner.set_compensator_positions(linear=12.0, CD=53.48, CG=56.16)
    positioner.move_from_scan("Polarization", 0.0, 430.0, 0.0, "polar_step")

    assert positioner._state["p4"].target_abs == pytest.approx(12.0)


def test_an_unconfigured_compensator_position_is_refused(positioner):
    """Driving the plate somewhere arbitrary would silently change the
    polarisation reaching the sample; declining says so instead."""
    assert positioner.move_compensator("linear") is False

    positioner.set_compensator_positions(linear=12.0)
    assert positioner.move_compensator("linear") is True
    assert positioner.move_compensator("CD") is False       # never measured


def test_a_state_the_compensator_has_no_name_for_is_refused(positioner):
    positioner.set_compensator_positions(linear=12.0)

    assert positioner.move_compensator("elliptical") is False
    assert set(COMPENSATOR_STATES) == {"linear", "CD", "CG"}
    assert set(COMPENSATOR_SETTING_KEYS) == set(COMPENSATOR_STATES)


def test_the_compensator_reports_which_position_it_sits_on(positioner):
    positioner.set_compensator_positions(linear=12.0, CD=53.48, CG=56.16)

    positioner.move_compensator("CD")
    positioner._state["p4"].abs_pos = 53.48                 # arrived
    assert positioner.compensator_state() == "CD"

    positioner._state["p4"].abs_pos = 30.0                  # somewhere else
    assert positioner.compensator_state() is None


def test_the_plate_returns_where_it_started_after_a_scan(positioner):
    positioner.set_compensator_positions(linear=12.0)
    positioner._state["p"].abs_pos = 7.5

    positioner.move_from_scan("Polarization", 90.0, 430.0, 0.0, "polar_step")
    positioner.move_from_scan("Polarization", 0.0, 430.0, 0.0, "polar_return_to_base")

    assert positioner._state["p"].target_abs == pytest.approx(7.5)


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


def test_the_calibration_table_is_available_again():
    """It was removed when the compensator became the only way; it is back as
    the second mode, for a chain the compensator description does not fit."""
    from DeepLight.gui.managers.Polarization_Table import get_polarization_table

    table = get_polarization_table()
    l2, l4 = table.lookup(0.0)

    assert (l2, l4) == pytest.approx((8.3, 94.56))


# ---- the two modes ---------------------------------------------------------

def test_compensator_is_the_default(positioner):
    assert positioner.polarization_mode() == DEFAULT_POLARIZATION_MODE == "compensator"


def test_an_unknown_mode_falls_back_rather_than_stopping_a_run(positioner):
    """Pushed from a settings field: a typo there should not stop an
    acquisition from starting, but it must not pass unnoticed either."""
    assert positioner.set_polarization_mode("magic") == "compensator"
    assert positioner.set_polarization_mode("") == "compensator"
    assert positioner.set_polarization_mode("CALIBRATION") == "calibration"


def test_calibration_mode_places_both_plates_from_the_table(positioner):
    positioner.set_compensator_positions(linear=12.0, CD=53.48, CG=56.16)
    positioner.set_polarization_mode("calibration")

    positioner.move_from_scan("Polarization", 90.0, 430.0, 0.0, "polar_step")

    # The 90 degree row of polarization_table.csv.
    assert positioner._state["p"].target_abs == pytest.approx(54.0)
    assert positioner._state["p4"].target_abs == pytest.approx(5.15)


def test_the_two_modes_disagree_on_purpose(positioner):
    """The same azimuth means a different pair of plate positions in each mode,
    which is why the provenance has to record which one was used."""
    positioner.set_compensator_positions(linear=12.0)

    positioner.set_polarization_mode("compensator")
    positioner.move_from_scan("Polarization", 90.0, 430.0, 0.0, "polar_step")
    compensator = positioner._state["p"].target_abs

    positioner._polar_prescan = None
    positioner.set_polarization_mode("calibration")
    positioner.move_from_scan("Polarization", 90.0, 430.0, 0.0, "polar_step")
    calibrated = positioner._state["p"].target_abs

    assert compensator == pytest.approx(45.0)
    assert calibrated == pytest.approx(54.0)
    assert compensator != pytest.approx(calibrated)


def test_calibration_mode_does_not_park_the_compensator(positioner):
    """The table already says where the quarter-wave plate goes; parking it on
    'linear' first would be a move the table immediately undoes."""
    positioner.set_compensator_positions(linear=12.0)
    positioner.set_polarization_mode("calibration")
    positioner._state["p4"].target_abs = None

    positioner.move_from_scan("Polarization", 0.0, 430.0, 0.0, "polar_step")

    assert positioner._state["p4"].target_abs == pytest.approx(94.56)   # the table


def test_calibration_mode_restores_both_plates_afterwards(positioner):
    """Both moved, so both have to be put back -- the compensator mode only
    has to restore the one it turned."""
    positioner.set_polarization_mode("calibration")
    positioner._state["p"].abs_pos = 7.5
    positioner._state["p4"].abs_pos = 3.25

    positioner.move_from_scan("Polarization", 90.0, 430.0, 0.0, "polar_step")
    positioner.move_from_scan("Polarization", 0.0, 430.0, 0.0, "polar_return_to_base")

    assert positioner._state["p"].target_abs == pytest.approx(7.5)
    assert positioner._state["p4"].target_abs == pytest.approx(3.25)
