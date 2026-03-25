from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QSizePolicy
)
from PySide6.QtCore import Signal


_BUTTON_STYLE_TOGGLE = """
    QPushButton {
        background-color: #333;
        color: white;
        border: 1px solid #555;
        border-radius: 6px;
        padding: 6px;
        font-weight: bold;
    }
    QPushButton:checked {
        background-color: #FF7700;
        border: 1px solid #FF9200;
    }
    QPushButton:hover {
        background-color: #444;
    }
    QPushButton:checked:hover {
        background-color: #FF9200;
    }
"""

_ACQUIRE_BUTTON_STYLE = """
    QPushButton {
        background-color: #333;
        color: white;
        border: 1px solid #555;
        border-radius: 6px;
        padding: 6px;
        font-weight: bold;
    }
    QPushButton:checked {
        background-color: #2E8B57;
        border: 1px solid #58d68d;
    }
    QPushButton:hover {
        background-color: #444;
    }
    QPushButton:checked:hover {
        background-color: #3AB16F;
    }
"""


class SpectroPanelWidget(QWidget):
    """
    Panel de contrôle Spectro minimal :
    - Brillouin ON/OFF
    - Raman ON/OFF
    - Acquire
    """

    sigSpectroModeChanged = Signal(bool, bool)   # brillouin, raman
    sigAcquireClicked = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)

        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(2, 2, 2, 2)
        main_layout.setSpacing(6)

        # =========================
        # Boutons de modes
        # =========================
        modes_layout = QHBoxLayout()
        modes_layout.setContentsMargins(0, 0, 0, 0)
        modes_layout.setSpacing(6)

        self.button_brillouin = QPushButton("Brillouin")
        self.button_brillouin.setCheckable(True)
        self.button_brillouin.setChecked(False)
        self.button_brillouin.setStyleSheet(_BUTTON_STYLE_TOGGLE)
        self.button_brillouin.setMinimumHeight(30)
        self.button_brillouin.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.button_brillouin.toggled.connect(self._emit_mode_changed)
        modes_layout.addWidget(self.button_brillouin)

        self.button_raman = QPushButton("Raman")
        self.button_raman.setCheckable(True)
        self.button_raman.setChecked(False)
        self.button_raman.setStyleSheet(_BUTTON_STYLE_TOGGLE)
        self.button_raman.setMinimumHeight(30)
        self.button_raman.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.button_raman.toggled.connect(self._emit_mode_changed)
        modes_layout.addWidget(self.button_raman)

        main_layout.addLayout(modes_layout)

        # =========================
        # Bouton Acquire
        # =========================
        self.button_acquire = QPushButton("Acquire")
        self.button_acquire.setStyleSheet(_ACQUIRE_BUTTON_STYLE)
        self.button_acquire.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.button_acquire.clicked.connect(self.sigAcquireClicked.emit)
        main_layout.addWidget(self.button_acquire)

        main_layout.addStretch()

        self._emit_mode_changed()

    def _emit_mode_changed(self):
        self.sigSpectroModeChanged.emit(
            self.button_brillouin.isChecked(),
            self.button_raman.isChecked()
        )

    def get_modes(self):
        return {
            "brillouin": self.button_brillouin.isChecked(),
            "raman": self.button_raman.isChecked(),
        }

    def set_modes(self, brillouin: bool, raman: bool):
        self.button_brillouin.blockSignals(True)
        self.button_raman.blockSignals(True)

        self.button_brillouin.setChecked(bool(brillouin))
        self.button_raman.setChecked(bool(raman))

        self.button_brillouin.blockSignals(False)
        self.button_raman.blockSignals(False)

        self._emit_mode_changed()