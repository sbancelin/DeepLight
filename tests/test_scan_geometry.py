"""Scan geometry: which axis draws what, and at what spacing.

The heart of a scanning microscope, and the part where a mistake is invisible:
an image scaled along the wrong axis still looks like an image.
"""

import pytest

from DeepLight.gui.managers.Provenance import physical_pixel_size_um
from DeepLight.gui.managers.Scan_Types import (
    bidirectional_lag_us,
    bidirectional_shift_px,
    image_rows_by_axis,
    stack_move_time_s,
)

# 64 samples over 20 µm on the fast galvo, 48 over 15 µm on the slow one.
SCAN = {
    "rows": [
        {"axis": "X-Galvo", "pixels": 64, "size_um": 20.0, "step_um": 0.0},
        {"axis": "Y-Galvo", "pixels": 48, "size_um": 15.0, "step_um": 0.0},
    ]
}


def test_the_image_axes_are_not_the_scan_row_order():
    """X-Galvo sweeps the sample's y direction, so the frame comes out
    transposed with respect to the order the axes are listed in."""
    row_x, row_y = image_rows_by_axis(SCAN)

    assert row_x["axis"] == "Y-Galvo"
    assert row_y["axis"] == "X-Galvo"


def test_listing_the_axes_the_other_way_round_changes_nothing():
    swapped = {"rows": list(reversed(SCAN["rows"]))}

    assert image_rows_by_axis(swapped)[0]["axis"] == "Y-Galvo"
    assert image_rows_by_axis(swapped)[1]["axis"] == "X-Galvo"


def test_pixel_size_is_the_interval_between_samples():
    """size_um spans first sample to last, so the step is size/(n-1) -- and it
    follows the image's own axes, not the scan rows."""
    x, y, z = physical_pixel_size_um(SCAN)

    assert x == pytest.approx(15.0 / 47.0)
    assert y == pytest.approx(20.0 / 63.0)
    assert z is None                      # no stack axis in this scan


def test_a_z_stack_carries_its_spacing_and_a_polarisation_stack_does_not():
    def with_third(axis, pixels, size):
        return {"rows": SCAN["rows"] + [
            {"axis": axis, "pixels": pixels, "size_um": size, "step_um": 0.0}
        ]}

    assert physical_pixel_size_um(with_third("Z-Vcoil", 11, 50.0))[2] == pytest.approx(5.0)
    assert physical_pixel_size_um(with_third("Polarization", 8, 157.5))[2] is None


def test_an_unknown_field_size_yields_no_pixel_size():
    """Better to say nothing than to state a size a viewer would trust."""
    empty = {"rows": [{"axis": "X-Galvo", "pixels": 1, "size_um": 0.0, "step_um": 0.0}]}

    assert physical_pixel_size_um(empty) == (None, None, None)
    assert image_rows_by_axis({"rows": []}) == (None, None)


@pytest.mark.parametrize("lag_us, dwell_us, expected", [
    (120.0, 8.0, 15),      # 120 µs of round-trip lag at 8 µs per pixel
    (120.0, 4.0, 30),      # half the dwell, twice the shift
    (0.0, 8.0, 0),         # not calibrated: no shift invented
])
def test_the_bidirectional_shift_follows_the_dwell(lag_us, dwell_us, expected):
    """The galvo lag is a property of the mirror; the shift it causes is that
    lag divided by the time spent on a pixel."""
    assert bidirectional_shift_px(lag_us, dwell_us) == expected


def test_the_lag_can_be_recovered_from_a_shift_set_by_hand():
    """Calibration is the inverse operation: someone tunes the shift until the
    lines align, and that tells us the lag once and for all."""
    assert bidirectional_lag_us(15, 8.0) == pytest.approx(120.0)


def test_a_stack_step_reserves_the_time_the_axis_needs():
    """The plan has to wait for the axis to arrive; not waiting is what
    smeared a plane into the next one."""
    assert stack_move_time_s(50.0, 200.0, margin_s=0.0) == pytest.approx(0.25)
    assert stack_move_time_s(50.0, 200.0, margin_s=0.005) == pytest.approx(0.255)
    assert stack_move_time_s(0.0, 200.0, margin_s=0.0) == 0.0
    assert stack_move_time_s(50.0, 0.0, margin_s=0.0) == 0.0     # unknown speed
