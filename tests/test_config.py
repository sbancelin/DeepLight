"""The configuration file: one file per installation, everything optional.

The promise is that a fresh machine works with no file at all, that a file only
overrides what it mentions, and that a typo is reported rather than silently
ignored -- a mis-typed COM port that falls back to a default would send commands
to the wrong instrument.
"""

import pytest

from DeepLight import config as cfg


def test_the_built_in_defaults_come_from_the_template_itself():
    """The generated file and the defaults are parsed from one string, so they
    cannot drift apart."""
    assert cfg.DEFAULTS["ni"]["device_name"] == "Dev1"
    assert cfg.DEFAULTS["ni"]["ai_max_v"] == 10.0
    assert "cobolt" in cfg.DEFAULTS and "elliptec" in cfg.DEFAULTS


def test_a_section_reads_like_an_object():
    section = cfg.Section({"port": "COM12", "nested": {"value": 3}})

    assert section.port == "COM12"
    assert section.nested.value == 3
    assert "port" in section
    assert section.as_dict() == {"port": "COM12", "nested": {"value": 3}}


def test_a_missing_key_says_what_is_available():
    """The message has to name the keys that do exist, or a typo costs an
    afternoon."""
    section = cfg.Section({"flamenco_port": "COM12"}, _path="cobolt")

    with pytest.raises(AttributeError) as raised:
        section.flamengo_port                       # typo

    assert "cobolt.flamengo_port" in str(raised.value)
    assert "flamenco_port" in str(raised.value)     # the real key is listed


def test_get_falls_back_without_raising():
    """Used where a key is genuinely optional."""
    section = cfg.Section({"a": 1})

    assert section.get("a") == 1
    assert section.get("absent", 42) == 42


def test_a_file_overrides_only_what_it_mentions():
    """Deleting a line has to restore the default, not break the key."""
    merged = cfg._deep_merge(cfg.DEFAULTS, {"ni": {"device_name": "Dev2"}})

    assert merged["ni"]["device_name"] == "Dev2"          # overridden
    assert merged["ni"]["ai_max_v"] == 10.0               # untouched
    assert merged["cobolt"]["flamenco_port"] == cfg.DEFAULTS["cobolt"]["flamenco_port"]


def test_the_merge_leaves_the_defaults_alone():
    """It returns a new mapping; mutating the module's defaults would leak
    between whatever loads next."""
    before = cfg.DEFAULTS["ni"]["device_name"]
    cfg._deep_merge(cfg.DEFAULTS, {"ni": {"device_name": "Dev9"}})

    assert cfg.DEFAULTS["ni"]["device_name"] == before


def test_the_lookup_order_is_the_documented_one(monkeypatch):
    monkeypatch.setenv("DEEPLIGHT_CONFIG", r"X:\somewhere\deeplight.toml")
    paths = [str(p) for p in cfg.candidate_paths()]

    assert paths[0].endswith("deeplight.toml")            # the env var wins
    assert any("DeepLight" in p or "deeplight" in p for p in paths[1:])


def test_the_data_root_has_a_fallback_when_left_empty(monkeypatch):
    """An empty root must not mean writing to the current directory."""
    monkeypatch.setattr(cfg, "CONFIG", cfg.Section({"data": {"root": ""}}))

    assert cfg.data_root().name == "DeepLight_data"


def test_the_save_folder_is_dated_and_locale_independent():
    """Month names are spelled out in the code rather than taken from the C
    library, so a machine in another locale writes to the same folder."""
    import datetime

    folder = cfg.default_dated_folder(datetime.date(2026, 9, 10))

    assert folder.endswith(("2026\\September\\10", "2026/September/10"))


def test_logs_and_presets_live_under_the_data_root():
    assert cfg.log_folder().parent == cfg.data_root()
    assert cfg.preset_folder().parent == cfg.data_root()
