import json
import os

from PySide6.QtCore import QObject

from ...config import hardware_settings_path
from ..widgets.Log_Widget import logger


class SettingsManager(QObject):
    """Shared storage for the application's settings and positions."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.axis_settings = {}
        self.axis_positions_um = {}            # positions ABSOLUES
        self.axis_relative_positions_um = {}   # positions RELATIVES (repère set-0)
        self.laser_settings = {}
        self.spectro_settings = {}
        self.bipd_settings = {}

    # ---------- Axes ----------
    def get_axis_settings(self, axis_name):
        return dict(self.axis_settings.get(axis_name, {}))

    def get_all_axis_settings(self):
        return {
            axis_name: dict(settings)
            for axis_name, settings in self.axis_settings.items()
        }

    def update_axis_settings(self, axis_name, **kwargs):
        self.axis_settings.setdefault(axis_name, {})
        self.axis_settings[axis_name].update(kwargs)

    def get_axis_position_um(self, axis_name):
        return float(self.axis_positions_um.get(axis_name, 0.0))

    def set_axis_position_um(self, axis_name, value):
        self.axis_positions_um[axis_name] = float(value)

    def get_axis_relative_position_um(self, axis_name):
        return float(self.axis_relative_positions_um.get(axis_name, 0.0))

    def set_axis_relative_position_um(self, axis_name, value):
        self.axis_relative_positions_um[axis_name] = float(value)

    # ---------- Lasers ----------
    def get_laser_settings(self, laser_name):
        return dict(self.laser_settings.get(laser_name, {}))

    def get_all_laser_settings(self):
        return dict(self.laser_settings)

    def update_laser_settings(self, laser_name, **kwargs):
        self.laser_settings.setdefault(laser_name, {})
        self.laser_settings[laser_name].update(kwargs)

    # ---------- Spectro (settings that don't have an axis/laser shape) ----------
    def get_spectro_settings(self):
        return dict(self.spectro_settings)

    def update_spectro_settings(self, **kwargs):
        self.spectro_settings.update(kwargs)

    # ---------- BIPD filter (steps, extinction search) ----------
    def get_bipd_settings(self):
        return dict(self.bipd_settings)

    def update_bipd_settings(self, **kwargs):
        self.bipd_settings.update(kwargs)

    # ---------- Persistence: "Save value" / "Reset origin" in a settings popup ----------
    #
    # One file, one section per settings popup (axis_settings covers both the
    # Scan and Positioner popups, which calibrate different axis names within
    # the same dict). A popup's "Save value" writes only its own section, so
    # saving from one popup never touches what another one holds.

    @staticmethod
    def _read_persisted_file() -> dict:
        path = hardware_settings_path()
        if not os.path.isfile(path):
            return {}
        try:
            with open(path, encoding="utf-8") as fh:
                return json.load(fh)
        except Exception:
            return {}

    def load_persisted(self):
        """Load previously saved calibration, if any.

        Called once at startup, before each widget fills in its hard-coded
        defaults (they only fill an axis that is still empty), so a saved
        value wins and an axis never saved yet still gets a sane default.
        """
        doc = self._read_persisted_file()
        for axis_name, settings in dict(doc.get("axis_settings", {}) or {}).items():
            self.update_axis_settings(axis_name, **dict(settings or {}))
        for laser_name, settings in dict(doc.get("laser_settings", {}) or {}).items():
            self.update_laser_settings(laser_name, **dict(settings or {}))
        self.spectro_settings = dict(doc.get("spectro", {}) or {})
        self.bipd_settings = dict(doc.get("bipd", {}) or {})

        # Saying so is the point: a saved value silently overriding a built-in
        # default is exactly what makes a calibration behave differently on two
        # machines, or on two branches, with the same code. Silent when the file
        # does not exist, which is every launch that has never saved anything.
        if not doc:
            return
        restored = []
        for section, label in (("axis_settings", "axes"), ("laser_settings", "lasers")):
            names = sorted(dict(doc.get(section, {}) or {}).keys())
            if names:
                restored.append(f"{label}: {', '.join(names)}")
        for section in ("spectro", "bipd"):
            if doc.get(section):
                restored.append(f"{section}: " + ", ".join(
                    f"{k}={v}" for k, v in sorted(dict(doc[section]).items())
                ))
        if restored:
            logger.notice(
                f"[Settings] saved values in force, from {hardware_settings_path()} "
                f"-- {' | '.join(restored)}"
            )

    def save_section(self, section: str, data: dict):
        """Persist one named section, leaving the others in the file untouched."""
        path = hardware_settings_path()
        doc = self._read_persisted_file()
        doc[section] = data
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(doc, fh, indent=2, default=str)

    def save_axis_settings(self):
        self.save_section("axis_settings", self.get_all_axis_settings())

    def save_laser_settings(self):
        self.save_section("laser_settings", self.get_all_laser_settings())

    def save_spectro_settings(self):
        self.save_section("spectro", self.get_spectro_settings())

    def save_bipd_settings(self):
        self.save_section("bipd", self.get_bipd_settings())
