from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel,
    QPushButton, QSizePolicy, QLineEdit, QFileDialog,
    QPlainTextEdit, QComboBox, QMessageBox, QFrame, QProgressBar, QCheckBox
)
from PySide6.QtCore import Signal, QDate, Qt
from PySide6.QtGui import QIcon

from PySide6.QtGui import QDoubleValidator, QIntValidator

import os


LINE_EDIT_STYLE = """
    QLineEdit {
        background-color: #333;
        color: white;
        border: 1px solid #555;
        border-radius: 3px;
        padding: 2px;
        min-height: 20px;
    }
    QLineEdit:disabled {
        background-color: #1e1e1e;
        color: #555;
        border: 1px solid #333;
    }
"""

READONLY_LINEEDIT_STYLE = """
    QLineEdit {
        background-color: #252525;
        color: #888;
        border: 1px solid #444;
        border-radius: 3px;
        padding: 2px;
        min-height: 20px;
    }
"""

BUTTON_STYLE = """
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

MODE_BUTTON_STYLE = """
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

COMBO_STYLE = """
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

PANEL_FRAME_STYLE = """
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

PROGRESS_BAR_STYLE = """
    QProgressBar {
        background-color: #252525;
        color: white;
        border: 1px solid #444;
        border-radius: 3px;
        text-align: center;
        min-height: 18px;
    }
    QProgressBar::chunk {
        background-color: #FF7700;
        border-radius: 2px;
    }
"""

HEADER_LABEL_STYLE = "color: white; font-weight: bold; padding-bottom: 2px;"

CHECKBOX_STYLE = """
QCheckBox {
    color: white;
}
QCheckBox::indicator {
    width: 12px;
    height: 12px;
    background-color: #333;
    border: 1px solid #555;
    border-radius: 3px;
}
QCheckBox::indicator:checked {
    background-color: #2E8B57;
    border: 1px solid #555;
    border-radius: 3px;
}
QCheckBox::indicator:checked:hover {
    background-color: #3AB16F;
    border: 1px solid #777;
}
QCheckBox::indicator:unchecked:hover {
    background-color: #444;
    border: 1px solid #777;
}
"""


class SpectroPanelWidget(QWidget):
    """
    Panneau d'acquisition Spectro indépendant de la logique Scan raster.
    Style aligné sur ScanWidget et SaveWidget.
    """

    sigSpectroModeChanged = Signal(bool, bool)   # brillouin, raman
    sigAcquireClicked = Signal()
    sigStopClicked = Signal()

    # Mock Brillouin aligné sur KURO full frame
    # et sauvegarde mock en float32
    _BRILLOUIN_IMG_H = 1200
    _BRILLOUIN_IMG_W = 1200
    _BRILLOUIN_BYTES_PER_PIXEL = 4

    _RAMAN_POINTS = 1024
    _RAMAN_BYTES_PER_POINT = 4

    def __init__(self, parent=None):
        super().__init__(parent)

        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)

        self._brillouin_roi_state = {
            "enabled": False,
            "x": 0,
            "y": 0,
            "width": 1200,
            "height": 1200,
        }

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(2, 2, 2, 2)
        main_layout.setSpacing(4)

        content_widget = QWidget()
        content_widget.setMinimumWidth(0)
        content_widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)

        content_layout = QVBoxLayout(content_widget)
        content_layout.setContentsMargins(2, 2, 2, 2)
        content_layout.setSpacing(4)

        # ==========================================================
        # Modes
        # ==========================================================
        modes_frame = QFrame()
        modes_frame.setStyleSheet(PANEL_FRAME_STYLE)
        modes_layout = QHBoxLayout(modes_frame)
        modes_layout.setContentsMargins(6, 6, 6, 6)
        modes_layout.setSpacing(6)

        self.button_brillouin = QPushButton("Brillouin")
        self.button_brillouin.setCheckable(True)
        self.button_brillouin.setChecked(False)
        self.button_brillouin.setStyleSheet(MODE_BUTTON_STYLE)
        self.button_brillouin.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.button_brillouin.toggled.connect(self._emit_mode_changed)
        self.button_brillouin.toggled.connect(self._update_derived_values)
        modes_layout.addWidget(self.button_brillouin)

        self.button_raman = QPushButton("Raman")
        self.button_raman.setCheckable(True)
        self.button_raman.setChecked(False)
        self.button_raman.setStyleSheet(MODE_BUTTON_STYLE)
        self.button_raman.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.button_raman.toggled.connect(self._emit_mode_changed)
        self.button_raman.toggled.connect(self._update_derived_values)
        modes_layout.addWidget(self.button_raman)

        content_layout.addWidget(modes_frame)

        # ==========================================================
        # Mapping
        # ==========================================================
        mapping_frame = QFrame()
        mapping_frame.setStyleSheet(PANEL_FRAME_STYLE)
        mapping_layout = QGridLayout(mapping_frame)
        mapping_layout.setContentsMargins(6, 6, 6, 6)
        mapping_layout.setHorizontalSpacing(6)
        mapping_layout.setVerticalSpacing(4)

        mapping_layout.setColumnStretch(0, 0)
        mapping_layout.setColumnStretch(1, 1)
        mapping_layout.setColumnStretch(2, 1)
        mapping_layout.setColumnStretch(3, 1)

        headers = ["Axis", "Size (µm)", "#Pix", "Step (µm)"]
        for col, header in enumerate(headers):
            label = QLabel(header)
            label.setStyleSheet(HEADER_LABEL_STYLE)
            label.setAlignment(Qt.AlignCenter)
            mapping_layout.addWidget(label, 0, col)

        float_validator = QDoubleValidator(bottom=0.0)
        int_validator = QIntValidator(1, 100000)

        # ---- X
        mapping_layout.addWidget(QLabel("X"), 1, 0)

        self.size_x_edit = QLineEdit("100")
        self.size_x_edit.setStyleSheet(LINE_EDIT_STYLE)
        self.size_x_edit.setValidator(float_validator)
        mapping_layout.addWidget(self.size_x_edit, 1, 1)

        self.pix_x_edit = QLineEdit("11")
        self.pix_x_edit.setStyleSheet(LINE_EDIT_STYLE)
        self.pix_x_edit.setValidator(int_validator)
        mapping_layout.addWidget(self.pix_x_edit, 1, 2)

        self.step_x_edit = QLineEdit("1.000")
        self.step_x_edit.setReadOnly(True)
        self.step_x_edit.setStyleSheet(READONLY_LINEEDIT_STYLE)
        mapping_layout.addWidget(self.step_x_edit, 1, 3)

        # ---- Y
        mapping_layout.addWidget(QLabel("Y"), 2, 0)

        self.size_y_edit = QLineEdit("100")
        self.size_y_edit.setStyleSheet(LINE_EDIT_STYLE)
        self.size_y_edit.setValidator(float_validator)
        mapping_layout.addWidget(self.size_y_edit, 2, 1)

        self.pix_y_edit = QLineEdit("11")
        self.pix_y_edit.setStyleSheet(LINE_EDIT_STYLE)
        self.pix_y_edit.setValidator(int_validator)
        mapping_layout.addWidget(self.pix_y_edit, 2, 2)

        self.step_y_edit = QLineEdit("1.000")
        self.step_y_edit.setReadOnly(True)
        self.step_y_edit.setStyleSheet(READONLY_LINEEDIT_STYLE)
        mapping_layout.addWidget(self.step_y_edit, 2, 3)

        # ---- Z
        mapping_layout.addWidget(QLabel("Z"), 3, 0)

        self.size_z_edit = QLineEdit("0")
        self.size_z_edit.setStyleSheet(LINE_EDIT_STYLE)
        self.size_z_edit.setValidator(float_validator)
        mapping_layout.addWidget(self.size_z_edit, 3, 1)

        self.pix_z_edit = QLineEdit("1")
        self.pix_z_edit.setStyleSheet(LINE_EDIT_STYLE)
        self.pix_z_edit.setValidator(int_validator)
        mapping_layout.addWidget(self.pix_z_edit, 3, 2)

        self.step_z_edit = QLineEdit("0.000")
        self.step_z_edit.setReadOnly(True)
        self.step_z_edit.setStyleSheet(READONLY_LINEEDIT_STYLE)
        mapping_layout.addWidget(self.step_z_edit, 3, 3)

        # ---- T (time lapse)
        self.cb_timelapse = QCheckBox("Time lapse")
        self.cb_timelapse.setStyleSheet(CHECKBOX_STYLE)
        mapping_layout.addWidget(self.cb_timelapse, 4, 0, 1, 2)

        self.repeats_edit = QLineEdit("5")
        self.repeats_edit.setStyleSheet(LINE_EDIT_STYLE)
        self.repeats_edit.setValidator(QIntValidator(1, 100000))
        self.repeats_edit.setEnabled(False)
        mapping_layout.addWidget(self.repeats_edit, 4, 2)

        delay_container = QWidget()
        delay_container.setStyleSheet("background: transparent;")
        delay_hbox = QHBoxLayout(delay_container)
        delay_hbox.setContentsMargins(0, 0, 0, 0)
        delay_hbox.setSpacing(4)
        self.delay_s_edit = QLineEdit("60")
        self.delay_s_edit.setStyleSheet(LINE_EDIT_STYLE)
        self.delay_s_edit.setValidator(QDoubleValidator(0.0, 1e9, 1))
        self.delay_s_edit.setEnabled(False)
        delay_hbox.addWidget(self.delay_s_edit)
        delay_unit = QLabel("s")
        delay_unit.setStyleSheet("color: #aaa; border: none; background: transparent;")
        delay_hbox.addWidget(delay_unit)
        mapping_layout.addWidget(delay_container, 4, 3)

        content_layout.addWidget(mapping_frame)

        # ==========================================================
        # Acquisition
        # ==========================================================
        acq_frame = QFrame()
        acq_frame.setStyleSheet(PANEL_FRAME_STYLE)
        acq_layout = QGridLayout(acq_frame)
        acq_layout.setContentsMargins(6, 6, 6, 6)
        acq_layout.setHorizontalSpacing(6)
        acq_layout.setVerticalSpacing(4)

        acq_layout.setColumnStretch(0, 0)
        acq_layout.setColumnStretch(1, 1)
        acq_layout.setColumnStretch(2, 0)
        acq_layout.setColumnStretch(3, 1)

        exposure_label = QLabel("Exposure (ms)")
        exposure_label.setStyleSheet("color: white; font-weight: bold;")
        acq_layout.addWidget(exposure_label, 0, 0)

        self.exposure_edit = QLineEdit("100")
        self.exposure_edit.setStyleSheet(LINE_EDIT_STYLE)
        self.exposure_edit.setValidator(float_validator)
        acq_layout.addWidget(self.exposure_edit, 0, 1)

        settle_label = QLabel("Settle (ms)")
        settle_label.setStyleSheet("color: white; font-weight: bold;")
        acq_layout.addWidget(settle_label, 0, 2)

        self.settle_edit = QLineEdit("10")
        self.settle_edit.setStyleSheet(LINE_EDIT_STYLE)
        self.settle_edit.setValidator(float_validator)
        acq_layout.addWidget(self.settle_edit, 0, 3)

        estimated_time_label = QLabel("Estimated time")
        estimated_time_label.setStyleSheet("color: white; font-weight: bold;")
        acq_layout.addWidget(estimated_time_label, 1, 0)

        self.estimated_time_edit = QLineEdit("—")
        self.estimated_time_edit.setReadOnly(True)
        self.estimated_time_edit.setStyleSheet(READONLY_LINEEDIT_STYLE)
        acq_layout.addWidget(self.estimated_time_edit, 1, 1)

        estimated_size_label = QLabel("Estimated size")
        estimated_size_label.setStyleSheet("color: white; font-weight: bold;")
        acq_layout.addWidget(estimated_size_label, 1, 2)

        self.estimated_size_edit = QLineEdit("—")
        self.estimated_size_edit.setReadOnly(True)
        self.estimated_size_edit.setStyleSheet(READONLY_LINEEDIT_STYLE)
        acq_layout.addWidget(self.estimated_size_edit, 1, 3)

        self.button_acquire = QPushButton("Acquire")
        self.button_acquire.setIcon(QIcon("gui/Icons/REC.svg"))
        self.button_acquire.setStyleSheet(BUTTON_STYLE)
        self.button_acquire.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.button_acquire.clicked.connect(self._on_acquire_clicked)
        acq_layout.addWidget(self.button_acquire, 2, 0, 1, 2)

        self.button_stop = QPushButton("Stop")
        self.button_stop.setIcon(QIcon("gui/Icons/stop.svg"))
        self.button_stop.setStyleSheet(BUTTON_STYLE)
        self.button_stop.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.button_stop.clicked.connect(self.sigStopClicked.emit)
        acq_layout.addWidget(self.button_stop, 2, 2, 1, 2)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setFormat("Idle")
        self.progress_bar.setTextVisible(True)
        self.progress_bar.setStyleSheet(PROGRESS_BAR_STYLE)
        self.progress_bar.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        acq_layout.addWidget(self.progress_bar, 3, 0, 1, 4)

        content_layout.addWidget(acq_frame)

        # ==========================================================
        # Save
        # ==========================================================
        save_frame = QFrame()
        save_frame.setStyleSheet(PANEL_FRAME_STYLE)
        save_layout = QGridLayout(save_frame)
        save_layout.setContentsMargins(6, 6, 6, 6)
        save_layout.setHorizontalSpacing(6)
        save_layout.setVerticalSpacing(4)

        save_layout.setColumnStretch(0, 0)
        save_layout.setColumnStretch(1, 1)
        save_layout.setColumnStretch(2, 0)

        current_date = QDate.currentDate()
        year = current_date.toString("yyyy")
        month = current_date.toString("MMMM")
        day = current_date.toString("dd")
        default_folder = fr"C:\Data\{year}\{month}\{day}"

        folder_label = QLabel("Folder")
        folder_label.setStyleSheet("color: white; font-weight: bold;")
        save_layout.addWidget(folder_label, 0, 0)

        self.folder_line_edit = QLineEdit(default_folder)
        self.folder_line_edit.setStyleSheet(LINE_EDIT_STYLE)
        self.folder_line_edit.setMinimumWidth(0)
        self.folder_line_edit.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        save_layout.addWidget(self.folder_line_edit, 0, 1)

        self.folder_button = QPushButton()
        self.folder_button.setIcon(QIcon("gui/Icons/folder.svg"))
        self.folder_button.setStyleSheet(BUTTON_STYLE)
        self.folder_button.setFixedWidth(28)
        self.folder_button.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.folder_button.clicked.connect(self.choose_folder)
        save_layout.addWidget(self.folder_button, 0, 2)

        filename_label = QLabel("File Name")
        filename_label.setStyleSheet("color: white; font-weight: bold;")
        save_layout.addWidget(filename_label, 1, 0)

        self.filename_line_edit = QLineEdit()
        self.filename_line_edit.setPlaceholderText("Spectro acquisition name")
        self.filename_line_edit.setStyleSheet(LINE_EDIT_STYLE)
        self.filename_line_edit.setMinimumWidth(0)
        self.filename_line_edit.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        save_layout.addWidget(self.filename_line_edit, 1, 1, 1, 2)

        self.comment_text_edit = QPlainTextEdit()
        self.comment_text_edit.setPlaceholderText("Comments to be added to the spectro metadata")
        self.comment_text_edit.setStyleSheet(LINE_EDIT_STYLE)
        self.comment_text_edit.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self.comment_text_edit.setMinimumHeight(0)
        save_layout.addWidget(self.comment_text_edit, 2, 0, 2, 3)

        format_label = QLabel("File Format")
        format_label.setStyleSheet("color: white; font-weight: bold;")
        save_layout.addWidget(format_label, 4, 0)

        self.format_combo = QComboBox()
        self.format_combo.addItems(["OME-TIFF", "OME-Zarr"])
        self.format_combo.setCurrentText("OME-TIFF")
        self.format_combo.setStyleSheet(COMBO_STYLE)
        self.format_combo.setMinimumWidth(0)
        self.format_combo.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.format_combo.setMinimumContentsLength(1)
        save_layout.addWidget(self.format_combo, 4, 1, 1, 2)

        content_layout.addWidget(save_frame)

        self.main_layout = main_layout
        self.main_layout.addWidget(content_widget)
        self.main_layout.addStretch()

        # ==========================================================
        # Connexions
        # ==========================================================
        for edit in (
            self.size_x_edit, self.size_y_edit, self.size_z_edit,
            self.pix_x_edit, self.pix_y_edit, self.pix_z_edit,
            self.exposure_edit, self.settle_edit,
            self.repeats_edit, self.delay_s_edit,
        ):
            edit.textChanged.connect(self._update_derived_values)

        self.cb_timelapse.toggled.connect(self._on_timelapse_toggled)

        self._update_derived_values()
        self._emit_mode_changed()

    # ==========================================================
    # Slots
    # ==========================================================
    def _on_timelapse_toggled(self, checked: bool):
        self.repeats_edit.setEnabled(bool(checked))
        self.delay_s_edit.setEnabled(bool(checked))
        self._update_derived_values()

    # ==========================================================
    # Helpers
    # ==========================================================
    def _emit_mode_changed(self):
        self.sigSpectroModeChanged.emit(
            self.button_brillouin.isChecked(),
            self.button_raman.isChecked()
        )

    @staticmethod
    def _safe_float(text: str, default: float = 0.0) -> float:
        try:
            return float((text or str(default)).replace(",", "."))
        except Exception:
            return float(default)

    @staticmethod
    def _safe_int(text: str, default: int = 1) -> int:
        try:
            return int(float((text or str(default)).replace(",", ".")))
        except Exception:
            return int(default)

    @staticmethod
    def _compute_step(size_um: float, pixels: int) -> float:
        if int(pixels) <= 1:
            return 0.0
        return float(size_um) / float(pixels - 1)

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
        size_x = self._safe_float(self.size_x_edit.text(), 100.0)
        size_y = self._safe_float(self.size_y_edit.text(), 100.0)
        size_z = self._safe_float(self.size_z_edit.text(), 0.0)

        pix_x = max(1, self._safe_int(self.pix_x_edit.text(), 11))
        pix_y = max(1, self._safe_int(self.pix_y_edit.text(), 11))
        pix_z = max(1, self._safe_int(self.pix_z_edit.text(), 1))

        step_x = self._compute_step(size_x, pix_x)
        step_y = self._compute_step(size_y, pix_y)
        step_z = self._compute_step(size_z, pix_z)

        self.step_x_edit.setText(f"{step_x:.3f}")
        self.step_y_edit.setText(f"{step_y:.3f}")
        self.step_z_edit.setText(f"{step_z:.3f}")

        exposure_ms = self._safe_float(self.exposure_edit.text(), 100.0)
        settle_ms = self._safe_float(self.settle_edit.text(), 10.0)

        timelapse_on = self.cb_timelapse.isChecked()
        n_repeats = max(1, self._safe_int(self.repeats_edit.text(), 1)) if timelapse_on else 1
        delay_s = max(0.0, self._safe_float(self.delay_s_edit.text(), 0.0)) if timelapse_on else 0.0

        n_pix = pix_x * pix_y * pix_z
        t_per_pix_s = max(0.0, exposure_ms + settle_ms) / 1000.0
        scan_s = n_pix * t_per_pix_s
        total_s = n_repeats * scan_s + max(0, n_repeats - 1) * delay_s
        self.estimated_time_edit.setText(self._format_duration(total_s))

        total_bytes = 0.0
        has_any_mode = False

        if self.button_brillouin.isChecked():
            roi = dict(self._brillouin_roi_state or {})
            roi_enabled = bool(roi.get("enabled", False))

            if roi_enabled:
                img_h = max(1, int(roi.get("height", self._BRILLOUIN_IMG_H)))
                img_w = max(1, int(roi.get("width", self._BRILLOUIN_IMG_W)))
            else:
                img_h = self._BRILLOUIN_IMG_H
                img_w = self._BRILLOUIN_IMG_W

            # dataset mock stocké en float32
            has_any_mode = True
            brillouin_img_bytes = img_h * img_w * 4
            total_bytes += brillouin_img_bytes * n_pix

        if self.button_raman.isChecked():
            has_any_mode = True
            raman_spec_bytes = self._RAMAN_POINTS * self._RAMAN_BYTES_PER_POINT
            total_bytes += raman_spec_bytes * n_pix

        if not has_any_mode:
            self.estimated_size_edit.setText("—")
        else:
            self.estimated_size_edit.setText(self._format_bytes(total_bytes * n_repeats))

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

    def set_brillouin_roi_state(self, roi: dict):
        roi = dict(roi or {})
        self._brillouin_roi_state = {
            "enabled": bool(roi.get("enabled", False)),
            "x": int(roi.get("x", 0)),
            "y": int(roi.get("y", 0)),
            "width": int(roi.get("width", 1200)),
            "height": int(roi.get("height", 1200)),
        }
        self._update_derived_values()
    
    def get_acquisition_parameters(self):
        size_x = self._safe_float(self.size_x_edit.text(), 100.0)
        size_y = self._safe_float(self.size_y_edit.text(), 100.0)
        size_z = self._safe_float(self.size_z_edit.text(), 0.0)

        pix_x = max(1, self._safe_int(self.pix_x_edit.text(), 11))
        pix_y = max(1, self._safe_int(self.pix_y_edit.text(), 11))
        pix_z = max(1, self._safe_int(self.pix_z_edit.text(), 1))

        return {
            "exposure_ms": self._safe_float(self.exposure_edit.text(), 100.0),
            "settle_ms": self._safe_float(self.settle_edit.text(), 10.0),
            "size_x_um": size_x,
            "size_y_um": size_y,
            "size_z_um": size_z,
            "pixels_x": pix_x,
            "pixels_y": pix_y,
            "pixels_z": pix_z,
            "step_x_um": self._compute_step(size_x, pix_x),
            "step_y_um": self._compute_step(size_y, pix_y),
            "step_z_um": self._compute_step(size_z, pix_z),
            "serpentine": True,
            "brillouin_roi": dict(self._brillouin_roi_state),
            "n_repeats": max(1, self._safe_int(self.repeats_edit.text(), 1)) if self.cb_timelapse.isChecked() else 1,
            "repeat_delay_s": max(0.0, self._safe_float(self.delay_s_edit.text(), 0.0)) if self.cb_timelapse.isChecked() else 0.0,
        }

    def reset_progress(self):
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setFormat("Idle")

    def set_progress(self, done: int, total: int):
        total = max(1, int(total))
        done = max(0, min(int(done), total))
        percent = int(round(100.0 * done / total))

        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(percent)
        self.progress_bar.setFormat(f"{done}/{total} ({percent}%)")
    
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

        if bool(running):
            self.progress_bar.setFormat("0%")
            self.progress_bar.setValue(0)
        else:
            if self.progress_bar.value() < 100:
                self.progress_bar.setFormat("Stopped")

    def set_estimated_time_text(self, text: str):
        self.estimated_time_edit.setText(str(text))

    def set_estimated_size_text(self, text: str):
        self.estimated_size_edit.setText(str(text))