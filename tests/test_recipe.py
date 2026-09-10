"""An acquisition described as data: what a script writes, and what it refuses.

A recipe is also what a preset saved from the window contains, so these rules
are what protects someone reloading a series six months later.
"""

import json

import pytest

from DeepLight.recipe import Axis, Recipe, polarization_sweep


def two_axes(**kwargs):
    return Recipe(
        axes=[Axis("X-Galvo", pixels=64, size_um=20.0),
              Axis("Y-Galvo", pixels=48, size_um=15.0)],
        **kwargs,
    )


def test_a_recipe_becomes_the_dictionary_the_pipeline_speaks():
    params = two_axes(dwell_us=4.0, detectors=["PMT-Vis", "Ch 0"]).to_scan_parameters()

    assert params["active_axes"] == ["X-Galvo", "Y-Galvo"]
    assert params["axis_order"] == ["X-Galvo", "Y-Galvo", "None", "None"]
    assert params["pixel_values"] == [64, 48, 1, 1]
    assert params["dwell_time"] == pytest.approx(4e-6)
    assert params["active_channels"] == ["PMT-Vis", "Ch 0"]
    assert params["step_sizes"]["X-Galvo"] == pytest.approx(20.0 / 63.0)


def test_the_per_axis_settings_come_from_the_same_defaults_as_the_panel():
    """A recipe and a click have to produce the same scan, which means reading
    the conversion factors and limits from one place."""
    params = two_axes().to_scan_parameters()

    assert params["conversion_factors"]["X-Galvo"] == pytest.approx(77.0)
    assert params["overscan_fraction"] == pytest.approx(0.10)     # fast axis
    assert params["frame_flyback_time_s"] == pytest.approx(0.001)  # slow axis


def test_a_scan_is_expressed_relative_to_where_the_sample_already_is():
    recipe = Recipe(axes=[
        Axis("X-Galvo", 64, 20.0),
        Axis("Y-Galvo", 64, 20.0),
        Axis("Z-Vcoil", 11, 50.0, offset_um=5.0),
    ])
    params = recipe.to_scan_parameters(positions_um={"Z-Vcoil": 300.0})

    assert params["offsets"]["Z-Vcoil"] == pytest.approx(305.0)
    assert params["initial_relative_positions"]["Z-Vcoil"] == pytest.approx(300.0)


@pytest.mark.parametrize("recipe, message", [
    (Recipe(axes=[Axis("X-Galvo", 64, 20.0)]), "at least two axes"),
    (Recipe(axes=[Axis("Z-Piezo", 64, 20.0), Axis("Y-Galvo", 64, 20.0)]), "Unknown axis"),
    (Recipe(axes=[Axis("X-Galvo", 64, 20.0), Axis("X-Galvo", 64, 20.0)]), "twice"),
    (two_axes(dwell_us=0.0), "dwell_us must be > 0"),
    (two_axes(detectors=["PMT-UV"]), "Unknown detectors"),
])
def test_an_impossible_recipe_is_refused_where_a_script_can_see_it(recipe, message):
    """The window says no with a dialog; a script has none, so the same rules
    are raised as exceptions instead of being logged and ignored."""
    with pytest.raises(ValueError, match=message):
        recipe.validate()


def test_a_slice_through_a_stepped_axis_is_refused():
    """An XZ slice needs a slow image axis that steps, which the execution plan
    cannot do. It used to be offered anyway, emit no movement at all, and record
    the same line N times -- silently."""
    with pytest.raises(ValueError, match="cannot draw the image"):
        Recipe(axes=[Axis("X-Galvo", 64, 20.0),
                     Axis("Z-Vcoil", 16, 30.0)]).validate()


def test_the_image_axes_have_to_match_the_scan_kind():
    """Galvos draw a laser scan, stages draw a sample scan; mixing the two
    would ask the plan to sweep one axis and step the other."""
    with pytest.raises(ValueError, match="cannot draw the image"):
        Recipe(axes=[Axis("X-Galvo", 64, 20.0), Axis("Y-Stage", 64, 20.0)]).validate()

    Recipe(axes=[Axis("X-Stage", 64, 20.0), Axis("Y-Stage", 64, 20.0)],
           scan_kind="sample").validate()


def test_a_stage_axis_cannot_step_a_stack():
    with pytest.raises(ValueError, match="cannot be a stack axis"):
        Recipe(axes=[Axis("X-Galvo", 64, 20.0), Axis("Y-Galvo", 64, 20.0),
                     Axis("X-Stage", 4, 100.0)]).validate()


def test_a_polarisation_stack_cannot_be_repeated():
    """It is already a series; repeating it would overwrite its own planes."""
    recipe = Recipe(
        axes=[Axis("X-Galvo", 64, 20.0), Axis("Y-Galvo", 64, 20.0),
              polarization_sweep(count=8)],
        repetitions=3,
    )
    with pytest.raises(ValueError, match="repetitions = 1"):
        recipe.validate()


def test_a_polarisation_sweep_stops_short_of_wrapping_onto_its_first_angle():
    sweep = polarization_sweep(count=8, span_deg=180.0)
    angles = [sweep.offset_um + i * sweep.step_um() for i in range(sweep.pixels)]

    assert angles == pytest.approx([0, 22.5, 45, 67.5, 90, 112.5, 135, 157.5])
    assert sweep.mode == "from"


def test_unevenly_spaced_angles_are_refused_rather_than_approximated():
    """The axis is stepped, not listed: an uneven series has to be several runs."""
    with pytest.raises(ValueError, match="evenly spaced"):
        polarization_sweep(angles_deg=[0, 30, 100])


def test_a_recipe_survives_a_round_trip_through_a_file():
    """This is what makes a preset shareable and a batch reproducible."""
    original = Recipe(
        axes=[Axis("X-Galvo", 128, 42.0, offset_um=1.5),
              Axis("Y-Galvo", 96, 33.0),
              Axis("Z-Vcoil", 7, 30.0, mode="from")],
        dwell_us=6.0, detectors=["PMT-Vis"], bidirectional=True,
    )
    restored = Recipe.from_dict(json.loads(json.dumps(original.to_dict())))

    assert restored.to_scan_parameters() == original.to_scan_parameters()
