from PySide6.QtCore import QObject


class SettingsManager(QObject):
    """Stockage partagé des réglages et positions de l'application."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.axis_settings = {}
        self.axis_positions_um = {}            # positions ABSOLUES
        self.axis_relative_positions_um = {}   # positions RELATIVES (repère set-0)
        self.laser_settings = {}

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