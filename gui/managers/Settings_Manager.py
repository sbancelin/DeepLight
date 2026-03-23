from PySide6.QtCore import QObject


_LEGACY_AXIS_SETTING_KEYS = {
    "turnback",
    "acceleration_max",
    "jerk",
}


class SettingsManager(QObject):
    """Stockage partagé des réglages et positions de l'application."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.axis_settings = {}
        self.axis_positions_um = {}
        self.laser_settings = {}

    # ---------- helpers ----------
    def _sanitize_axis_settings_dict(self, data):
        clean = dict(data or {})
        for key in _LEGACY_AXIS_SETTING_KEYS:
            clean.pop(key, None)
        return clean

    # ---------- Axes ----------
    def get_axis_settings(self, axis_name):
        raw = self.axis_settings.get(axis_name, {})
        clean = self._sanitize_axis_settings_dict(raw)

        # auto-nettoyage du stockage interne si nécessaire
        if clean != raw:
            self.axis_settings[axis_name] = clean

        return dict(clean)

    def get_all_axis_settings(self):
        out = {}
        for axis_name, raw in self.axis_settings.items():
            clean = self._sanitize_axis_settings_dict(raw)
            if clean != raw:
                self.axis_settings[axis_name] = clean
            out[axis_name] = dict(clean)
        return out

    def update_axis_settings(self, axis_name, **kwargs):
        self.axis_settings.setdefault(axis_name, {})
        self.axis_settings[axis_name].update(kwargs)
        self.axis_settings[axis_name] = self._sanitize_axis_settings_dict(
            self.axis_settings[axis_name]
        )

    def get_axis_position_um(self, axis_name):
        return float(self.axis_positions_um.get(axis_name, 0.0))

    def set_axis_position_um(self, axis_name, value):
        self.axis_positions_um[axis_name] = float(value)

    # ---------- Lasers ----------
    def get_laser_settings(self, laser_name):
        return dict(self.laser_settings.get(laser_name, {}))

    def get_all_laser_settings(self):
        return dict(self.laser_settings)

    def update_laser_settings(self, laser_name, **kwargs):
        self.laser_settings.setdefault(laser_name, {})
        self.laser_settings[laser_name].update(kwargs)