"""The BIPD filter, the extinction search, and the cut along the spectrum.

The filter is a device family like the others (contract, simulation,
validator); the search is checked against a level whose minimum is known; the
map guard against frames that drift; the cut against images whose profile is
known in advance.
"""

import sys

import numpy as np
import pytest

from DeepLight.gui.managers.Bipd_Filter import (
    BipdFilterBase, MockBipdFilter, SpectoBipdFilter, create_bipd_filter,
    validate_bipd_filter_contract,
)
from DeepLight.gui.managers.Extinction import (
    AutoLcSettings, ExtinctionGuard, border_intensity, search_lc_minimum,
)


# ----------------------------------------------------------------------------
# the device family
# ----------------------------------------------------------------------------

def test_the_bipd_filters_satisfy_their_contract():
    validate_bipd_filter_contract(create_bipd_filter("mock"))
    # Built without the vendor module: it is only imported on connect.
    validate_bipd_filter_contract(create_bipd_filter("specto", "COM4"))


def test_an_incomplete_bipd_filter_is_rejected():
    with pytest.raises(TypeError, match="does not satisfy"):
        validate_bipd_filter_contract(object())


def test_an_unknown_bipd_filter_says_so():
    with pytest.raises(ValueError, match="Unknown BIPD filter"):
        create_bipd_filter("polariser")


def test_the_simulated_filter_actually_moves():
    bipd = MockBipdFilter()
    bipd.connect()

    assert bipd.set_lc_level_mv(1234.5) == 1234.5
    assert bipd.lc_level_mv() == 1234.5
    bipd.move_rotator_deg(12.0)
    assert bipd.rotator_angle_deg() == 12.0
    bipd.home_rotator()
    assert bipd.rotator_angle_deg() == 0.0
    bipd.set_heater_enabled(True)
    assert bipd.heater_enabled() is True
    assert set(bipd.state()) >= {"lc_level_mv", "rotator_deg", "heater_enabled", "temperature_c"}


def test_the_liquid_crystal_never_gets_past_its_safety_limit():
    bipd = MockBipdFilter()
    bipd.connect()
    assert bipd.set_lc_level_mv(9999.0) == BipdFilterBase.LC_MAX_MV == 4500.0
    assert bipd.set_lc_level_mv(-50.0) == BipdFilterBase.LC_MIN_MV == 0.0
    with pytest.raises(ValueError):
        bipd.set_lc_level_mv(float("nan"))


def test_a_bipd_filter_refuses_commands_before_it_is_connected():
    with pytest.raises(RuntimeError, match="not connected"):
        MockBipdFilter().set_lc_level_mv(100.0)


def test_close_disconnects_the_filter():
    bipd = MockBipdFilter()
    bipd.connect()
    bipd.close()
    assert bipd.connected is False


def test_the_simulated_filter_needs_no_vendor_module():
    sys.modules.pop("libspecto", None)
    bipd = create_bipd_filter("mock")
    bipd.connect()
    bipd.set_lc_level_mv(1000.0)
    assert "libspecto" not in sys.modules


def test_a_missing_vendor_module_is_reported_on_connect(monkeypatch):
    monkeypatch.setitem(sys.modules, "libspecto", None)     # import fails
    with pytest.raises(RuntimeError, match="libspecto"):
        SpectoBipdFilter().connect()


def test_the_real_filter_drives_libspecto_as_documented(monkeypatch):
    """The calls the vendor's README documents, with a stand-in for the device."""
    calls = []

    class FakeSpecto:
        firmware_version = "1.0"

        def __init__(self, *args, **kwargs):
            calls.append(("open", args, kwargs))

        def lc_get_level_target(self): return 1500.0
        def lc_get_level(self): return 1498.0
        def lc_get_enabled(self): return False
        def lc_set_enabled(self, on): calls.append(("lc_enabled", on))
        def lc_set_level(self, mv): calls.append(("lc_level", mv))
        def heater_get_enabled(self): return True
        def rotator_move_absolute(self, deg): calls.append(("rotator", deg))
        def rotator_get_position(self): return 7.5
        def close(self): calls.append(("close",))

    fake_module = type(sys)("libspecto")
    fake_module.SpectoBIPD = FakeSpecto
    monkeypatch.setitem(sys.modules, "libspecto", fake_module)

    bipd = SpectoBipdFilter("COM4")
    bipd.connect()
    assert calls[0] == ("open", ("COM4",), {"init_rotator_move_home": False})
    assert bipd.lc_level_mv() == 1500.0          # starts from the device's setpoint
    assert bipd.heater_enabled() is True

    bipd.set_lc_level_mv(6000.0)
    assert ("lc_enabled", True) in calls          # enabled before the first level
    assert calls[-1] == ("lc_level", 4500.0)      # clamped before it leaves DeepLight

    bipd.move_rotator_deg(30.0)
    assert calls[-1] == ("rotator", 30.0)
    assert bipd.rotator_angle_deg() == 7.5

    bipd.close()
    assert calls[-1] == ("close",) and bipd.connected is False


# ----------------------------------------------------------------------------
# the figure of merit and the search
# ----------------------------------------------------------------------------

def test_the_border_level_is_the_mean_of_the_edge_band():
    img = np.full((100, 120), 10.0, dtype=np.float32)
    img[5:95, 5:115] = 1000.0             # bright inside, 5-pixel dim band
    assert border_intensity(img, 5) == pytest.approx(10.0)


def test_without_a_band_the_whole_frame_counts():
    img = np.arange(16, dtype=float).reshape(4, 4)
    assert border_intensity(img, 0) == pytest.approx(img.mean())
    assert border_intensity(img, 50) == pytest.approx(img.mean())   # too small for a band


def _parabola(best_mv, current):
    """A simulated filter whose level is lowest at best_mv."""
    def set_level(mv):
        current["mv"] = min(4500.0, max(0.0, mv))
        return current["mv"]

    def measure():
        return 100.0 + ((current["mv"] - best_mv) / 10.0) ** 2

    return set_level, measure


def test_the_search_walks_down_to_the_minimum():
    current = {"mv": 1000.0}
    set_level, measure = _parabola(1040.0, current)
    result = search_lc_minimum(set_level, lambda: None, measure,
                               start_mv=1000.0, start_level=measure(), step_mv=5.0)
    assert result.best_mv == pytest.approx(1040.0)
    assert current["mv"] == pytest.approx(1040.0)       # left at the best voltage
    assert result.improved and result.direction_mv > 0


def test_the_search_turns_round_when_the_first_step_goes_uphill():
    current = {"mv": 1000.0}
    set_level, measure = _parabola(970.0, current)
    result = search_lc_minimum(set_level, lambda: None, measure,
                               start_mv=1000.0, start_level=measure(), step_mv=5.0)
    assert result.best_mv == pytest.approx(970.0)
    assert result.direction_mv < 0                      # remembered for next time


def test_the_search_stays_put_at_a_minimum():
    current = {"mv": 1000.0}
    set_level, measure = _parabola(1000.0, current)
    result = search_lc_minimum(set_level, lambda: None, measure,
                               start_mv=1000.0, start_level=measure(), step_mv=5.0)
    assert result.best_mv == 1000.0 and current["mv"] == 1000.0
    assert result.moves == 2 and not result.improved


def test_the_search_stops_at_the_safety_limit():
    current = {"mv": 4480.0}
    set_level, measure = _parabola(9000.0, current)     # minimum beyond the limit
    result = search_lc_minimum(set_level, lambda: None, measure,
                               start_mv=4480.0, start_level=measure(), step_mv=5.0)
    assert result.best_mv == 4500.0 and current["mv"] <= 4500.0


def test_a_stopped_search_leaves_the_best_voltage_set():
    current = {"mv": 1000.0}
    set_level, measure = _parabola(2000.0, current)
    calls = {"n": 0}

    def should_stop():
        calls["n"] += 1
        return calls["n"] > 4

    result = search_lc_minimum(set_level, lambda: None, measure, start_mv=1000.0,
                               start_level=measure(), step_mv=5.0, should_stop=should_stop)
    assert result.stopped
    assert current["mv"] == result.best_mv > 1000.0


# ----------------------------------------------------------------------------
# during a map
# ----------------------------------------------------------------------------

def _frames_for(bipd, base=10.0):
    """Frames whose border level follows the simulated filter's leak."""
    def frame():
        level = base * (1.0 + bipd.simulated_leak())
        img = np.full((60, 60), level, dtype=np.float32)
        img[10:50, 10:50] = 500.0
        return img
    return frame


def test_the_guard_corrects_a_drift_and_takes_the_point_again():
    bipd = MockBipdFilter()
    bipd.connect()
    bipd.set_lc_level_mv(bipd._BEST_LC_MV)
    frame = _frames_for(bipd)
    settings = AutoLcSettings(margin_px=10, step_mv=10.0, settle_s=0.0, threshold=1.5,
                              check_every=1, check_mode="points", max_steps=100)
    guard = ExtinctionGuard(bipd, settings, sleep=lambda *_: None)

    assert guard.check(frame(), frame) is not None          # first point: the reference
    assert guard.reference_level == pytest.approx(10.0)

    bipd.set_lc_level_mv(bipd._BEST_LC_MV + 400.0)          # the extinction drifts
    reacquired = []

    def reacquire():
        reacquired.append(1)
        return frame()

    image = guard.check(frame(), reacquire, point_index=1)
    assert len(guard.corrections) == 1
    assert abs(bipd.lc_level_mv() - bipd._BEST_LC_MV) <= 10.0
    assert border_intensity(image, 10) < 1.5 * guard.reference_level
    assert reacquired                                        # the point was taken anew

    record = guard.record()
    assert record["corrections"][0]["lc_before_mv"] == pytest.approx(bipd._BEST_LC_MV + 400.0)
    assert record["state_at_start"]["lc_level_mv"] == pytest.approx(bipd._BEST_LC_MV)


def test_a_disabled_guard_only_records():
    bipd = MockBipdFilter()
    bipd.connect()
    frame = _frames_for(bipd)
    settings = AutoLcSettings(margin_px=10, threshold=1.01, check_every=1, check_mode="points")
    guard = ExtinctionGuard(bipd, settings, enabled=False, sleep=lambda *_: None)

    guard.check(frame(), frame)
    bipd.set_lc_level_mv(4000.0)
    guard.check(frame(), frame)
    assert guard.corrections == []
    assert guard.record()["auto_lc_enabled"] is False


def test_the_guard_checks_at_its_period_only():
    bipd = MockBipdFilter()
    bipd.connect()
    bipd.set_lc_level_mv(bipd._BEST_LC_MV)
    frame = _frames_for(bipd)
    clock = {"t": 0.0}
    settings = AutoLcSettings(margin_px=10, threshold=1.01, check_every=60, check_mode="seconds",
                              settle_s=0.0, step_mv=10.0)
    guard = ExtinctionGuard(bipd, settings, clock=lambda: clock["t"], sleep=lambda *_: None)

    guard.check(frame(), frame)                    # reference at t = 0
    bipd.set_lc_level_mv(bipd.lc_level_mv() + 300.0)
    clock["t"] = 30.0
    guard.check(frame(), frame)
    assert guard.corrections == []                 # not due yet
    clock["t"] = 61.0
    guard.check(frame(), frame)
    assert len(guard.corrections) == 1


# ----------------------------------------------------------------------------
# the cut along the spectrum
# ----------------------------------------------------------------------------

def test_the_cut_reads_the_diagonal():
    from DeepLight.gui.widgets.Spectrum_Cut_Widget import line_profile

    img = np.zeros((50, 50))
    img[np.arange(50), np.arange(50)] = 7.0       # bright main diagonal
    distance, values = line_profile(img, (0.0, 0.0), (50.0, 50.0))
    assert distance[-1] == pytest.approx(50.0 * np.sqrt(2.0))
    assert np.nanmean(values) > 5.0               # nearly every sample on it


def test_the_cut_averages_across_its_width():
    from DeepLight.gui.widgets.Spectrum_Cut_Widget import line_profile

    img = np.zeros((40, 40))
    img[20, :] = 9.0                              # one bright row
    _, narrow = line_profile(img, (0.0, 20.5), (40.0, 20.5), width=1)
    _, wide = line_profile(img, (0.0, 20.5), (40.0, 20.5), width=3)
    assert np.nanmax(narrow) == pytest.approx(9.0)
    assert np.nanmax(wide) == pytest.approx(3.0)  # one row in three


def test_the_cut_ignores_what_falls_outside_the_frame():
    from DeepLight.gui.widgets.Spectrum_Cut_Widget import line_profile

    img = np.ones((10, 10))
    _, values = line_profile(img, (-10.0, 5.0), (20.0, 5.0))
    inside = values[np.isfinite(values)]
    assert inside.size > 0 and np.all(inside == 1.0)
    assert np.isnan(values[0]) and np.isnan(values[-1])
