"""Raising the power with depth, by Beer-Lambert or from a measured table."""

import numpy as np
import pytest

from DeepLight.gui.managers.Depth_Compensation import (
    DepthCompensation,
    compute_power_profile,
    depth_axis_um,
    format_power_table,
    interpolate_power_table,
    parse_power_table,
)


# ---- the exponential model -------------------------------------------------

def test_the_power_rises_to_hold_the_focal_intensity():
    """P(z) = P(0)·exp(µz), which cancels the exp(-µz) the focus receives."""
    ramp = DepthCompensation(attenuation_um_inv=0.01, enabled=True)

    assert ramp.power_gain(0.0) == pytest.approx(1.0)
    assert ramp.power_gain(100.0) == pytest.approx(np.exp(1.0))
    assert ramp.attenuation(100.0) * ramp.power_gain(100.0) == pytest.approx(1.0)


def test_a_negative_attenuation_is_refused():
    """It would lower the power with depth, which is never what was meant."""
    with pytest.raises(ValueError, match="must be positive"):
        DepthCompensation(attenuation_um_inv=-0.01).effective_coefficient()


def test_the_profile_starts_at_the_surface_power():
    ramp = DepthCompensation(attenuation_um_inv=0.005, enabled=True)
    depths = depth_axis_um(100.0, 5)
    percents = compute_power_profile(ramp, base_percent=10.0, depths_um=depths)

    assert percents[0] == pytest.approx(10.0)
    assert np.all(np.diff(percents) > 0)


# ---- the table -------------------------------------------------------------

def test_a_table_is_read_from_the_notebook_line_it_was_written_on():
    assert parse_power_table("0:10, 40:25, 80:60") == ((0.0, 10.0), (40.0, 25.0), (80.0, 60.0))
    assert parse_power_table("0:10; 40:25") == ((0.0, 10.0), (40.0, 25.0))
    assert parse_power_table("  80:60 ,, 0:10 ") == ((0.0, 10.0), (80.0, 60.0))  # sorted
    assert parse_power_table("") == ()


@pytest.mark.parametrize("text, message", [
    ("0:10, 40", "depth:percent"),
    ("0:10, abc:5", "depth:percent"),
    ("0:10, 0:20", "twice"),
])
def test_a_malformed_table_says_what_is_wrong(text, message):
    with pytest.raises(ValueError, match=message):
        parse_power_table(text)


def test_a_table_round_trips_through_its_text_form():
    table = parse_power_table("0:10, 40:25, 80:60")
    assert parse_power_table(format_power_table(table)) == table


def test_between_measured_points_the_power_is_interpolated():
    table = ((0.0, 10.0), (100.0, 50.0))

    assert interpolate_power_table(table, 0.0) == pytest.approx(10.0)
    assert interpolate_power_table(table, 50.0) == pytest.approx(30.0)
    assert interpolate_power_table(table, 100.0) == pytest.approx(50.0)


def test_beyond_the_measured_points_the_power_is_held_flat():
    """Not extrapolated: continuing the curve past the deepest point anyone
    measured is how a sample gets cooked."""
    table = ((0.0, 10.0), (100.0, 50.0))

    assert interpolate_power_table(table, 500.0) == pytest.approx(50.0)
    assert interpolate_power_table(table, -50.0) == pytest.approx(10.0)


def test_an_empty_table_is_refused_rather_than_defaulted():
    with pytest.raises(ValueError, match="empty"):
        interpolate_power_table((), 10.0)


def test_a_single_point_holds_that_power_everywhere():
    assert interpolate_power_table(((0.0, 12.0),), [0.0, 40.0, 400.0]) == pytest.approx([12.0] * 3)


def test_the_table_ignores_the_surface_power():
    """It gives absolute percentages. Scaling them by wherever the laser
    happened to be would silently rescale a measurement."""
    ramp = DepthCompensation(enabled=True, mode="table",
                             table=((0.0, 10.0), (100.0, 50.0)))
    depths = np.array([0.0, 50.0, 100.0])

    for base in (1.0, 10.0, 90.0):
        percents = compute_power_profile(ramp, base_percent=base, depths_um=depths)
        assert percents == pytest.approx([10.0, 30.0, 50.0])


def test_switching_mode_switches_which_numbers_are_used():
    depths = np.array([0.0, 100.0])
    exponential = DepthCompensation(attenuation_um_inv=0.01, enabled=True)
    tabulated = DepthCompensation(enabled=True, mode="table",
                                  table=((0.0, 10.0), (100.0, 12.0)))

    assert compute_power_profile(exponential, 10.0, depths)[1] == pytest.approx(10.0 * np.e)
    assert compute_power_profile(tabulated, 10.0, depths)[1] == pytest.approx(12.0)


# ---- through the panel -----------------------------------------------------

def test_the_panel_reports_a_bad_table_where_it_is_typed(qapp):
    from DeepLight.gui.widgets.Depth_Compensation_Widget import DepthCompensationWidget

    widget = DepthCompensationWidget()
    widget._z_active = True
    widget._z_size_um, widget._z_pixels = 100.0, 5

    widget.mode_combo.setCurrentText("Table")
    widget.table_edit.setText("0:10, oops")

    assert "Power table" in widget.status_label.text()


def test_an_unusable_table_holds_the_surface_power_rather_than_moving(qapp):
    from DeepLight.gui.widgets.Depth_Compensation_Widget import DepthCompensationWidget

    widget = DepthCompensationWidget()
    widget._z_active = True
    widget.mode_combo.setCurrentText("Table")
    widget.table_edit.setText("")               # nothing usable

    # Arming captures the surface power, so it is set afterwards.
    widget.activate_button.setChecked(True)
    widget._base_percent = 7.5

    assert widget.power_percent_at(40.0) == pytest.approx(7.5)


def test_the_panel_shows_only_what_the_mode_uses(qapp):
    from DeepLight.gui.widgets.Depth_Compensation_Widget import DepthCompensationWidget

    widget = DepthCompensationWidget()

    widget.mode_combo.setCurrentText("Table")
    assert widget.get_compensation().mode == "table"
    # The surface power stays visible but inert: hiding it would leave someone
    # wondering whether it still played a part.
    assert widget.base_power_edit.isEnabled() is False

    widget.mode_combo.setCurrentText("Exponential")
    assert widget.get_compensation().mode == "exponential"
    assert widget.base_power_edit.isEnabled() is True
