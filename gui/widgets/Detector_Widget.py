from PySide6.QtWidgets import (QWidget, QVBoxLayout, QPushButton, QGridLayout, QLabel, QSizePolicy)
from PySide6.QtCore import Signal, Qt

class ToggleButton(QPushButton):
    """Checkable round button used to enable/disable a detector."""
    def __init__(self, text="", parent=None):
        super().__init__(text, parent)

        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedSize(26, 26)
        self.setFocusPolicy(Qt.NoFocus)

        self.setStyleSheet("""
        QPushButton {
            background-color: qradialgradient(
                cx:0.35, cy:0.35, radius:0.9,
                stop:0 #5a5a5a,
                stop:0.5 #3f3f3f,
                stop:1 #2a2a2a
            );
            border: none;
            border-radius: 13px;
        }
        QPushButton:hover {
            background-color: qradialgradient(
                cx:0.35, cy:0.35, radius:0.9,
                stop:0 #6a6a6a,
                stop:0.5 #4a4a4a,
                stop:1 #333333
            );
        }
        QPushButton:checked {
            background-color: qradialgradient(
                cx:0.35, cy:0.35, radius:1.0,
                stop:0 #ffe0b0,
                stop:0.3 #ffb347,
                stop:0.6 #ff8c1a,
                stop:1 #cc5f00
            );
        }
        QPushButton:checked:hover {
            background-color: qradialgradient(
                cx:0.35, cy:0.35, radius:1.0,
                stop:0 #fff0d0,
                stop:0.3 #ffc266,
                stop:0.6 #ff9a2f,
                stop:1 #d96a05
            );
        }
        """)

class DetectorWidget(QWidget):
    """Widget for selecting the active detectors."""
    detectors_changed = Signal(list)

    def __init__(self, parent=None):
        super().__init__(parent)

        self.detectors = []
        self.toggle_buttons = {}

        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)

        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(2, 2, 2, 2)
        root_layout.setSpacing(2)

        content_widget = QWidget()
        content_widget.setMinimumWidth(0)
        content_widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)

        grid_layout = QGridLayout(content_widget)
        grid_layout.setContentsMargins(0, 0, 0, 0)
        grid_layout.setHorizontalSpacing(6)
        grid_layout.setVerticalSpacing(4)

        detectors = ["PMT-Vis", "PMT-IR", "Ch 0", "Ch 1"]

        for col in range(len(detectors)):
            grid_layout.setColumnStretch(col, 1)

        for col, header in enumerate(detectors):
            label_detect = QLabel(header)
            label_detect.setStyleSheet("color: white; font-weight: bold; padding-bottom: 2px;")
            label_detect.setAlignment(Qt.AlignCenter)
            label_detect.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            grid_layout.addWidget(label_detect, 0, col)

            toggle_button = ToggleButton()
            toggle_button.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
            toggle_button.clicked.connect(lambda _checked, h=header: self.on_detector_toggled(h))
            toggle_button.setChecked(header == "PMT-Vis")
            grid_layout.addWidget(toggle_button, 1, col, Qt.AlignCenter)

            self.toggle_buttons[header] = toggle_button

        root_layout.addWidget(content_widget)
        root_layout.addStretch()

        self.detectors = ["PMT-Vis"]
        self.detectors_changed.emit(self.detectors)

    def get_detector_specs(self):
        """
        Return the structured description of the 4 detector channels.

        DeepLight V1 convention:
        - PMT-Vis, PMT-IR -> analog
        - Ch 0, Ch 1      -> digital
        """
        ordered_names = ["PMT-Vis", "PMT-IR", "Ch 0", "Ch 1"]
        specs = []

        for name in ordered_names:
            btn = self.toggle_buttons.get(name)
            enabled = bool(btn and btn.isChecked())

            if name in ("PMT-Vis", "PMT-IR"):
                kind = "analog"
                digital_source = None
            else:
                kind = "digital"
                digital_source = name

            specs.append({
                "name": name,
                "kind": kind,
                "enabled": enabled,
                "digital_source": digital_source,
                "digital_mode": "counts",
            })

        return specs
    
    def on_detector_toggled(self, detector_name):
        """Update the list of active detectors, then emit the matching signal."""
        is_checked = self.toggle_buttons[detector_name].isChecked()

        if is_checked and detector_name not in self.detectors:
            self.detectors.append(detector_name)
        elif not is_checked and detector_name in self.detectors:
            self.detectors.remove(detector_name)

        self.detectors_changed.emit(self.detectors)