from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QGridLayout, QLabel, QPushButton, QSizePolicy
)
from PySide6.QtCore import Signal, Qt

from .Save_Widget import SaveWidget
from .Detector_Widget import ToggleButton


class SpectroPanelWidget(QWidget):
    """
    Panel de contrôle Spectro (dock droit)
    """

    sigSpectroModeChanged = Signal(bool, bool)  # (brillouin, raman)
    sigAcquireClicked = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)

        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(2, 2, 2, 2)
        main_layout.setSpacing(4)

        # =========================
        # Selection modes
        # =========================
        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(6)
        grid.setVerticalSpacing(4)

        labels = ["Brillouin", "Raman"]

        self.toggle_buttons = {}

        for col, name in enumerate(labels):
            lbl = QLabel(name)
            lbl.setStyleSheet("color: white; font-weight: bold;")
            lbl.setAlignment(Qt.AlignCenter)
            grid.addWidget(lbl, 0, col)

            btn = ToggleButton()
            btn.setChecked(True)
            btn.clicked.connect(self._emit_mode_changed)
            grid.addWidget(btn, 1, col, Qt.AlignCenter)

            self.toggle_buttons[name] = btn

        main_layout.addLayout(grid)

        # =========================
        # Acquire button
        # =========================
        self.acquire_button = QPushButton("Acquire")
        self.acquire_button.setStyleSheet("""
            QPushButton {
                background-color: #333;
                color: white;
                border: 2px solid #2E8B57;
                border-radius: 3px;
                font-weight: bold;
                min-height: 24px;
            }
            QPushButton:hover {
                background-color: #444;
            }
        """)
        self.acquire_button.clicked.connect(self.sigAcquireClicked)
        main_layout.addWidget(self.acquire_button)

        # =========================
        # Save widget
        # =========================
        self.save_widget = SaveWidget()
        main_layout.addWidget(self.save_widget)

        main_layout.addStretch()

        # emit initial state
        self._emit_mode_changed()

    def _emit_mode_changed(self):
        b = self.toggle_buttons["Brillouin"].isChecked()
        r = self.toggle_buttons["Raman"].isChecked()
        self.sigSpectroModeChanged.emit(b, r)