from PySide6.QtCore import QObject


class AxisSettingsManager(QObject):
    """Stockage partagé des réglages et positions courantes des axes."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.axis_settings = {}
        self.axis_positions_um = {}

    def get_axis_settings(self, axis_name):
        return dict(self.axis_settings.get(axis_name, {}))

    def get_all_axis_settings(self):
        return dict(self.axis_settings)

    def update_axis_settings(self, axis_name, **kwargs):
        self.axis_settings.setdefault(axis_name, {})
        self.axis_settings[axis_name].update(kwargs)

    def get_axis_position_um(self, axis_name):
        return float(self.axis_positions_um.get(axis_name, 0.0))

    def set_axis_position_um(self, axis_name, value):
        self.axis_positions_um[axis_name] = float(value)