"""Named stage positions: captured, revisited, and kept with the preset."""

import pytest

from DeepLight.gui.widgets.Positions_Widget import POSITION_AXES, PositionsWidget


@pytest.fixture
def stage():
    """A stand-in for the positioner: a dict the test can move."""
    return {"x": 0.0, "y": 0.0, "z": 0.0, "p": 0.0}


@pytest.fixture
def positions(qapp, stage):
    widget = PositionsWidget()
    widget.set_position_getter(lambda: dict(stage))
    return widget


def test_it_starts_empty_and_says_so(positions):
    assert positions.positions() == []
    assert "No positions" in positions.status_label.text()


def test_add_captures_where_the_stage_is_now(positions, stage):
    stage.update({"x": 120.5, "y": -33.25, "z": 40.0, "p": 22.5})
    positions.add_current()

    saved = positions.positions()
    assert len(saved) == 1
    assert saved[0]["name"] == "Pos 1"
    assert saved[0]["x"] == pytest.approx(120.5)
    assert saved[0]["y"] == pytest.approx(-33.25)
    assert saved[0]["z"] == pytest.approx(40.0)
    assert saved[0]["p"] == pytest.approx(22.5)


def test_several_positions_keep_their_own_coordinates(positions, stage):
    for x in (10.0, 20.0, 30.0):
        stage["x"] = x
        positions.add_current()

    assert [p["x"] for p in positions.positions()] == [10.0, 20.0, 30.0]
    assert [p["name"] for p in positions.positions()] == ["Pos 1", "Pos 2", "Pos 3"]


def test_go_asks_for_the_selected_position(positions, stage):
    stage.update({"x": 5.0, "y": 6.0, "z": 7.0, "p": 8.0})
    positions.add_current()
    stage.update({"x": 500.0, "y": 600.0, "z": 700.0, "p": 800.0})
    positions.add_current()

    asked = []
    positions.sigGoToPosition.connect(asked.append)

    positions.table.selectRow(0)
    positions.go_to_selected()

    assert len(asked) == 1
    assert asked[0] == {"x": 5.0, "y": 6.0, "z": 7.0, "p": 8.0}


def test_go_does_nothing_with_no_selection(positions):
    asked = []
    positions.sigGoToPosition.connect(asked.append)

    positions.table.clearSelection()
    positions.table.setCurrentCell(-1, -1)
    positions.go_to_selected()

    assert asked == []


def test_update_replaces_the_selected_one(positions, stage):
    positions.add_current()
    stage.update({"x": 99.0, "y": 98.0, "z": 97.0, "p": 96.0})

    positions.table.selectRow(0)
    positions.update_selected()

    assert positions.positions()[0]["x"] == pytest.approx(99.0)
    assert positions.positions()[0]["name"] == "Pos 1"      # the name is kept


def test_remove_and_clear(positions, stage):
    for _ in range(3):
        positions.add_current()

    positions.table.selectRow(1)
    positions.remove_selected()
    assert len(positions.positions()) == 2

    positions.clear()
    assert positions.positions() == []


def test_the_list_survives_a_round_trip_as_data(positions, stage):
    """This is what lets it travel in a preset and be looped over by a script."""
    import json

    for x in (1.5, 2.5):
        stage["x"] = x
        positions.add_current()

    saved = json.loads(json.dumps(positions.positions()))
    positions.clear()
    rejected = positions.set_positions(saved)

    assert rejected == []
    assert positions.positions() == saved


def test_a_malformed_entry_is_reported_not_swallowed(positions):
    rejected = positions.set_positions([
        {"name": "good", "x": 1.0, "y": 2.0, "z": 3.0, "p": 4.0},
        {"name": "bad", "x": "over there"},
        "not even a mapping",
    ])

    assert len(rejected) == 2
    assert len(positions.positions()) == 1          # the good one still loaded


def test_a_partial_entry_fills_the_missing_axes_with_zero(positions):
    positions.set_positions([{"name": "xy only", "x": 4.0, "y": 5.0}])

    saved = positions.positions()[0]
    assert saved["x"] == pytest.approx(4.0)
    assert all(axis in saved for axis in POSITION_AXES)
    assert saved["z"] == pytest.approx(0.0)


def test_without_a_positioner_it_declines_instead_of_inventing(qapp):
    """Built with no stage attached -- as in a test, or before the hardware is
    connected -- it must not record a position of all zeros."""
    widget = PositionsWidget()

    widget.add_current()
    assert widget.positions() == []
    assert widget.button_add.isEnabled() is False


def test_the_buttons_follow_what_is_possible(positions, stage):
    assert positions.button_go.isEnabled() is False      # nothing selected yet
    assert positions.button_clear.isEnabled() is False

    positions.add_current()
    assert positions.button_go.isEnabled() is True
    assert positions.button_clear.isEnabled() is True

    positions.clear()
    assert positions.button_clear.isEnabled() is False
