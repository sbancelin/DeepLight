from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel,
    QPushButton, QSizePolicy, QDoubleSpinBox, QSpinBox,
    QLineEdit, QFileDialog, QPlainTextEdit, QComboBox, QMessageBox, QFrame
)
from PySide6.QtCore import Signal, QDate, Qt, QSize
from PySide6.QtGui import QIcon, QColor, QPalette

import os


_BUTTON_STYLE_TOGGLE = """
    QPushButton {
        background-color: #333;
        color: white;
        border: 1px solid #555;
        border-radius: 6px;
        padding: 6px;
        font-weight: bold;
        min-height: 28px;
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

_BUTTON_STYLE = """
    QPushButton {
        background-color: #333;
        color: white;
        border: 1px solid #555;
        border-radius: 3px;
        padding: 2px;
        font-weight: bold;
        min-height: 20px;
    }
    QPushButton:hover {
        background-color: #444;
    }
"""

_ICON_BUTTON_STYLE = """
    QPushButton {
        background-color: transparent;
        border: none;
        padding: 2px;
        min-height: 28px;
        min-width: 36px;
    }
    QPushButton:hover {
        background-color: #333;
        border-radius: 4px;
    }
    QPushButton:disabled {
        background-color: transparent;
    }
"""

_LINE_EDIT_STYLE = """
    QLineEdit {
        background-color: #333;
        color: white;
        border: 1px solid #555;
        border-radius: 3px;
        padding: 2px;
        min-height: 20px;
    }
"""

_COMBO_STYLE = """
    QComboBox {
        background-color: #333;
        color: white;
        border: 1px solid #555;
        border-radius: 3px;
        font-weight: bold;
        padding: 2px;
        min-height: 20px;
    }
"""

_PANEL_FRAME_STYLE = """
QFrame {
    background-color: #252525;
    border: 1px solid #444;
    border-radius: 4px;
}
QLabel {
    color: white;
    border: none;
    background: transparent;
}
"""

_STATUS_VALUE_STYLE = "color: #b0b0b0; background: transparent; border: none;"

_READONLY_VALUE_STYLE = """
QLabel {
    background-color: #252525;
    color: #888;
    border: 1px solid #444;
    border-radius: 3px;
    padding: 2px 6px 2px 6px;
    min-height: 20px;
}
"""


def _apply_spinbox_palette(spinbox):
    spin_palette = spinbox.palette()
    spin_palette.setColor(QPalette.Base, QColor("#333333"))
    spin_palette.setColor(QPalette.Text, QColor("white"))
    spin_palette.setColor(QPalette.Button, QColor("#333333"))
    spin_palette.setColor(QPalette.ButtonText, QColor("white"))
    spin_palette.setColor(QPalette.WindowText, QColor("white"))
    spin_palette.setColor(QPalette.Highlight, QColor("#2E8B57"))
    spin_palette.setColor(QPalette.HighlightedText, QColor("white"))
    spinbox.setPalette(spin_palette)
    spinbox.setAutoFillBackground(True)
    spinbox.setMinimumHeight(22)


class SpectroPanelWidget(QWidget):
    """
    Panneau d'acquisition Spectro indépendant de la logique Scan raster.
    """

    sigSpectroModeChanged = Signal(bool, bool)   # brillouin, raman
    sigAcquireClicked = Signal()
    sigStopClicked = Signal()

    # Hypothèse actuelle pour l'estimation Brillouin:
    # caméra Kuro 1024x1024 mono16 -> 2 bytes/pixel
    _BRILLOUIN_IMG_H = 1024
    _BRILLOUIN_IMG_W = 1024
    _BRILLOUIN_BYTES_PER_PIXEL = 2

    def __init__(self, parent=None):
        super().__init__(parent)

        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(2, 2, 2, 2)
        main_layout.setSpacing(6)

        # ==========================================================
        # Modes (sans label)
        # ==========================================================
        modes_frame = QFrame()
        modes_frame.setStyleSheet(_PANEL_FRAME_STYLE)
        modes_layout = QHBoxLayout(modes_frame)
        modes_layout.setContentsMargins(8, 6, 8, 6)
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

        main_layout.addWidget(modes_frame)

        # ==========================================================
        # Mapping (sans label titre)
        # ==========================================================
        mapping_frame = QFrame()
        mapping_frame.setStyleSheet(_PANEL_FRAME_STYLE)
        mapping_layout = QGridLayout(mapping_frame)
        mapping_layout.setContentsMargins(8, 6, 8, 6)
        mapping_layout.setHorizontalSpacing(6)
        mapping_layout.setVerticalSpacing(4)

        mapping_layout.addWidget(QLabel("Axis"), 0, 0)
        mapping_layout.addWidget(QLabel("Size (um)"), 0, 1)
        mapping_layout.addWidget(QLabel("#Pix"), 0, 2)
        mapping_layout.addWidget(QLabel("Step (um)"), 0, 3)

        # ---- X
        mapping_layout.addWidget(QLabel("X"), 1, 0)

        self.spin_size_x_um = QDoubleSpinBox()
        self.spin_size_x_um.setDecimals(3)
        self.spin_size_x_um.setRange(0.0, 1_000_000.0)
        self.spin_size_x_um.setValue(20.0)
        self.spin_size_x_um.setSingleStep(1.0)
        _apply_spinbox_palette(self.spin_size_x_um)
        mapping_layout.addWidget(self.spin_size_x_um, 1, 1)

        self.spin_pixels_x = QSpinBox()
        self.spin_pixels_x.setRange(1, 100000)
        self.spin_pixels_x.setValue(20)
        _apply_spinbox_palette(self.spin_pixels_x)
        mapping_layout.addWidget(self.spin_pixels_x, 1, 2)

        self.label_step_x_um = QLabel("1.000")
        self.label_step_x_um.setStyleSheet(_READONLY_VALUE_STYLE)
        mapping_layout.addWidget(self.label_step_x_um, 1, 3)

        # ---- Y
        mapping_layout.addWidget(QLabel("Y"), 2, 0)

        self.spin_size_y_um = QDoubleSpinBox()
        self.spin_size_y_um.setDecimals(3)
        self.spin_size_y_um.setRange(0.0, 1_000_000.0)
        self.spin_size_y_um.setValue(20.0)
        self.spin_size_y_um.setSingleStep(1.0)
        _apply_spinbox_palette(self.spin_size_y_um)
        mapping_layout.addWidget(self.spin_size_y_um, 2, 1)

        self.spin_pixels_y = QSpinBox()
        self.spin_pixels_y.setRange(1, 100000)
        self.spin_pixels_y.setValue(20)
        _apply_spinbox_palette(self.spin_pixels_y)
        mapping_layout.addWidget(self.spin_pixels_y, 2, 2)

        self.label_step_y_um = QLabel("1.000")
        self.label_step_y_um.setStyleSheet(_READONLY_VALUE_STYLE)
        mapping_layout.addWidget(self.label_step_y_um, 2, 3)

        # ---- Z
        mapping_layout.addWidget(QLabel("Z"), 3, 0)

        self.spin_size_z_um = QDoubleSpinBox()
        self.spin_size_z_um.setDecimals(3)
        self.spin_size_z_um.setRange(0.0, 1_000_000.0)
        self.spin_size_z_um.setValue(0.0)
        self.spin_size_z_um.setSingleStep(1.0)
        _apply_spinbox_palette(self.spin_size_z_um)
        mapping_layout.addWidget(self.spin_size_z_um, 3, 1)

        self.spin_pixels_z = QSpinBox()
        self.spin_pixels_z.setRange(1, 100000)
        self.spin_pixels_z.setValue(1)
        _apply_spinbox_palette(self.spin_pixels_z)
        mapping_layout.addWidget(self.spin_pixels_z, 3, 2)

        self.label_step_z_um = QLabel("0.000")
        self.label_step_z_um.setStyleSheet(_READONLY_VALUE_STYLE)
        mapping_layout.addWidget(self.label_step_z_um, 3, 3)

        main_layout.addWidget(mapping_frame)

        # ==========================================================
        # Acquisition (sans label titre)
        # ==========================================================
        acq_frame = QFrame()
        acq_frame.setStyleSheet(_PANEL_FRAME_STYLE)
        acq_layout = QGridLayout(acq_frame)
        acq_layout.setContentsMargins(8, 6, 8, 6)
        acq_layout.setHorizontalSpacing(6)
        acq_layout.setVerticalSpacing(4)

        acq_layout.addWidget(QLabel("Exposure"), 0, 0)

        self.spin_exposure_ms = QDoubleSpinBox()
        self.spin_exposure_ms.setDecimals(3)
        self.spin_exposure_ms.setRange(0.001, 1_000_000.0)
        self.spin_exposure_ms.setValue(100.0)
        self.spin_exposure_ms.setSingleStep(1.0)
        _apply_spinbox_palette(self.spin_exposure_ms)
        acq_layout.addWidget(self.spin_exposure_ms, 0, 1)

        acq_layout.addWidget(QLabel("(ms)"), 0, 2)

        acq_layout.addWidget(QLabel("Settle"), 0, 3)

        self.spin_settle_ms = QDoubleSpinBox()
        self.spin_settle_ms.setDecimals(3)
        self.spin_settle_ms.setRange(0.0, 1_000_000.0)
        self.spin_settle_ms.setValue(10.0)
        self.spin_settle_ms.setSingleStep(1.0)
        _apply_spinbox_palette(self.spin_settle_ms)
        acq_layout.addWidget(self.spin_settle_ms, 0, 4)

        acq_layout.addWidget(QLabel("(ms)"), 0, 5)

        acq_layout.addWidget(QLabel("Estimated time"), 1, 0)
        self.label_estimated_time = QLabel("—")
        self.label_estimated_time.setStyleSheet(_READONLY_VALUE_STYLE)
        acq_layout.addWidget(self.label_estimated_time, 1, 1, 1, 2)

        acq_layout.addWidget(QLabel("Estimated size"), 1, 3)
        self.label_estimated_size = QLabel("—")
        self.label_estimated_size.setStyleSheet(_READONLY_VALUE_STYLE)
        acq_layout.addWidget(self.label_estimated_size, 1, 4, 1, 2)

        actions_layout = QHBoxLayout()
        actions_layout.setContentsMargins(0, 2, 0, 0)
        actions_layout.setSpacing(6)

        self.button_acquire = QPushButton()
        self.button_acquire.setToolTip("Acquire spectro mapping")
        self.button_acquire.setIcon(QIcon("gui/Icons/REC.svg"))
        self.button_acquire.setIconSize(QSize(32, 32))
        self.button_acquire.setStyleSheet(_ICON_BUTTON_STYLE)
        self.button_acquire.clicked.connect(self._on_acquire_clicked)
        self.button_acquire.setFixedHeight(36)
        self.button_acquire.setFixedWidth(42)
        actions_layout.addWidget(self.button_acquire)

        self.button_stop = QPushButton()
        self.button_stop.setToolTip("Stop spectro mapping")
        self.button_stop.setIcon(QIcon("gui/Icons/stop.svg"))
        self.button_stop.setIconSize(QSize(32, 32))
        self.button_stop.setStyleSheet(_ICON_BUTTON_STYLE)
        self.button_stop.clicked.connect(self.sigStopClicked.emit)
        self.button_stop.setFixedHeight(36)
        self.button_stop.setFixedWidth(42)
        actions_layout.addWidget(self.button_stop)

        actions_layout.addStretch(1)
        acq_layout.addLayout(actions_layout, 2, 0, 1, 6)

        main_layout.addWidget(acq_frame)

        # ==========================================================
        # Save
        # ==========================================================
        save_frame = QFrame()
        save_frame.setStyleSheet(_PANEL_FRAME_STYLE)
        save_layout = QGridLayout(save_frame)
        save_layout.setContentsMargins(8, 6, 8, 6)
        save_layout.setHorizontalSpacing(6)
        save_layout.setVerticalSpacing(4)

        current_date = QDate.currentDate()
        year = current_date.toString("yyyy")
        month = current_date.toString("MMMM")
        day = current_date.toString("dd")
        default_folder = fr"C:\Data\{year}\{month}\{day}"

        save_layout.addWidget(QLabel("Folder"), 0, 0)

        self.folder_line_edit = QLineEdit(default_folder)
        self.folder_line_edit.setStyleSheet(_LINE_EDIT_STYLE)
        save_layout.addWidget(self.folder_line_edit, 0, 1)

        self.folder_button = QPushButton()
        self.folder_button.setIcon(QIcon("gui/Icons/folder.svg"))
        self.folder_button.setStyleSheet(_BUTTON_STYLE)
        self.folder_button.setFixedWidth(28)
        self.folder_button.clicked.connect(self.choose_folder)
        save_layout.addWidget(self.folder_button, 0, 2)

        save_layout.addWidget(QLabel("File Name"), 1, 0)

        self.filename_line_edit = QLineEdit()
        self.filename_line_edit.setPlaceholderText("Spectro acquisition name")
        self.filename_line_edit.setStyleSheet(_LINE_EDIT_STYLE)
        save_layout.addWidget(self.filename_line_edit, 1, 1, 1, 2)

        self.comment_text_edit = QPlainTextEdit()
        self.comment_text_edit.setPlaceholderText("Comments to be added to the spectro metadata")
        self.comment_text_edit.setStyleSheet(_LINE_EDIT_STYLE)
        self.comment_text_edit.setMinimumHeight(70)
        save_layout.addWidget(self.comment_text_edit, 2, 0, 1, 3)

        save_layout.addWidget(QLabel("Format"), 3, 0)

        self.format_combo = QComboBox()
        self.format_combo.addItems(["OME-TIFF", "OME-Zarr"])
        self.format_combo.setCurrentText("OME-TIFF")
        self.format_combo.setStyleSheet(_COMBO_STYLE)
        save_layout.addWidget(self.format_combo, 3, 1, 1, 2)

        main_layout.addWidget(save_frame)
        main_layout.addStretch()

        # ==========================================================
        # Connexions
        # ==========================================================
        self.spin_size_x_um.valueChanged.connect(self._update_derived_values)
        self.spin_size_y_um.valueChanged.connect(self._update_derived_values)
        self.spin_size_z_um.valueChanged.connect(self._update_derived_values)

        self.spin_pixels_x.valueChanged.connect(self._update_derived_values)
        self.spin_pixels_y.valueChanged.connect(self._update_derived_values)
        self.spin_pixels_z.valueChanged.connect(self._update_derived_values)

        self.spin_exposure_ms.valueChanged.connect(self._update_derived_values)
        self.spin_settle_ms.valueChanged.connect(self._update_derived_values)

        self.button_brillouin.toggled.connect(self._update_derived_values)
        self.button_raman.toggled.connect(self._update_derived_values)

        self._update_derived_values()
        self._emit_mode_changed()

    # ==========================================================
    # Helpers
    # ==========================================================
    def _emit_mode_changed(self):
        self.sigSpectroModeChanged.emit(
            self.button_brillouin.isChecked(),
            self.button_raman.isChecked()
        )

    @staticmethod
    def _compute_step(size_um: float, pixels: int) -> float:
        if int(pixels) <= 1:
            return 0.0
        return float(size_um) / float(pixels)

    @staticmethod
    def _format_duration(seconds: float) -> str:
        seconds = max(float(seconds), 0.0)
        if seconds < 60:
            return f"{seconds:.1f} s"

        minutes = int(seconds // 60)
        rem_s = seconds - 60 * minutes
        if minutes < 60:
            return f"{minutes} min {rem_s:.0f} s"

        hours = minutes // 60
        rem_m = minutes % 60
        return f"{hours} h {rem_m} min"

    @staticmethod
    def _format_bytes(n_bytes: float) -> str:
        n = float(max(n_bytes, 0.0))
        if n < 1024:
            return f"{n:.0f} B"
        if n < 1024**2:
            return f"{n / 1024:.1f} KB"
        if n < 1024**3:
            return f"{n / 1024**2:.1f} MB"
        return f"{n / 1024**3:.2f} GB"

    def _update_derived_values(self):
        step_x = self._compute_step(self.spin_size_x_um.value(), self.spin_pixels_x.value())
        step_y = self._compute_step(self.spin_size_y_um.value(), self.spin_pixels_y.value())
        step_z = self._compute_step(self.spin_size_z_um.value(), self.spin_pixels_z.value())

        self.label_step_x_um.setText(f"{step_x:.3f}")
        self.label_step_y_um.setText(f"{step_y:.3f}")
        self.label_step_z_um.setText(f"{step_z:.3f}")

        n_pix = (
            int(self.spin_pixels_x.value())
            * int(self.spin_pixels_y.value())
            * int(self.spin_pixels_z.value())
        )
        t_per_pix_s = (
            float(self.spin_exposure_ms.value()) + float(self.spin_settle_ms.value())
        ) / 1000.0
        total_s = n_pix * t_per_pix_s
        self.label_estimated_time.setText(self._format_duration(total_s))

        # Taille estimée:
        # Brillouin: 1024x1024 mono16 par position
        # Raman: non inclus ici, car ta demande porte explicitement
        # sur la série d'images caméra.
        img_bytes = (
            self._BRILLOUIN_IMG_H
            * self._BRILLOUIN_IMG_W
            * self._BRILLOUIN_BYTES_PER_PIXEL
        )

        if self.button_brillouin.isChecked():
            total_bytes = img_bytes * n_pix
        else:
            total_bytes = 0.0

        self.label_estimated_size.setText(self._format_bytes(total_bytes))

    def _on_acquire_clicked(self):
        folder = self.folder_line_edit.text().strip()
        filename = self.filename_line_edit.text().strip()

        if not self.button_brillouin.isChecked() and not self.button_raman.isChecked():
            QMessageBox.warning(
                self,
                "No spectro mode enabled",
                "Please enable Brillouin and/or Raman before starting acquisition."
            )
            return

        if not filename:
            QMessageBox.warning(
                self,
                "Missing file name",
                "Please enter a file name before starting acquisition."
            )
            return

        if folder:
            os.makedirs(folder, exist_ok=True)

        self.sigAcquireClicked.emit()

    # ==========================================================
    # Public API
    # ==========================================================
    def choose_folder(self):
        folder = QFileDialog.getExistingDirectory(
            self,
            "Select spectro output folder",
            self.folder_line_edit.text(),
            QFileDialog.ShowDirsOnly
        )
        if folder:
            self.folder_line_edit.setText(folder)

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
        self._update_derived_values()

    def get_acquisition_parameters(self):
        return {
            "exposure_ms": float(self.spin_exposure_ms.value()),
            "settle_ms": float(self.spin_settle_ms.value()),
            "size_x_um": float(self.spin_size_x_um.value()),
            "size_y_um": float(self.spin_size_y_um.value()),
            "size_z_um": float(self.spin_size_z_um.value()),
            "pixels_x": int(self.spin_pixels_x.value()),
            "pixels_y": int(self.spin_pixels_y.value()),
            "pixels_z": int(self.spin_pixels_z.value()),
            "step_x_um": self._compute_step(self.spin_size_x_um.value(), self.spin_pixels_x.value()),
            "step_y_um": self._compute_step(self.spin_size_y_um.value(), self.spin_pixels_y.value()),
            "step_z_um": self._compute_step(self.spin_size_z_um.value(), self.spin_pixels_z.value()),
            "serpentine": True,
        }

    def get_save_parameters(self):
        return {
            "folder": self.folder_line_edit.text().strip(),
            "filename": self.filename_line_edit.text().strip(),
            "comment": self.comment_text_edit.toPlainText().strip(),
            "format": self.format_combo.currentText().strip(),
        }

    def set_running(self, running: bool):
        self.button_acquire.setEnabled(not bool(running))
        self.button_stop.setEnabled(True)

    def set_estimated_time_text(self, text: str):
        self.label_estimated_time.setText(str(text))

    def set_estimated_size_text(self, text: str):
        self.label_estimated_size.setText(str(text))