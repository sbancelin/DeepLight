from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QDoubleSpinBox, QComboBox, QSizePolicy, QCheckBox,
    QDialog, QDialogButtonBox, QFormLayout, QMessageBox, QFrame
)
from PySide6.QtCore import Qt, QLocale
from PySide6.QtGui import QTransform, QPalette, QColor

import numpy as np
import pyqtgraph as pg


_BUTTON_STYLE_TOGGLE = """
QPushButton {
    background-color: #333;
    color: white;
    border: 1px solid #555;
    border-radius: 3px;
    padding: 2px;
    font-weight: bold;
    min-height: 20px;
}
QPushButton:checked {
    background-color: #2E8B57;
}
QPushButton:hover {
    background-color: #444;
}
QPushButton:checked:hover {
    background-color: #3AB16F;
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

_CHECKBOX_STYLE = """
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
    border: 1px solid #777;
    background-color: #3AB16F;
}
QCheckBox::indicator:unchecked:hover {
    background-color: #444;
    border: 1px solid #777;
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

_COMBO_STYLE = """
QComboBox {
    background-color: #333;
    color: white;
    border: 1px solid #555;
    border-radius: 3px;
    padding: 2px;
    min-height: 20px;
}
"""

_STATUS_VALUE_STYLE = "color: #b0b0b0; background: transparent; border: none;"


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
    spinbox.setLocale(QLocale.c())

def ask_levels_min_max(parent=None, title="LUT Levels", lo0=0.0, hi0=255.0):
    dlg = QDialog(parent)
    dlg.setWindowTitle(title)

    form = QFormLayout(dlg)

    sp_lo = QDoubleSpinBox(dlg)
    sp_lo.setDecimals(6)
    sp_lo.setRange(-1e12, 1e12)
    sp_lo.setValue(float(lo0))

    sp_hi = QDoubleSpinBox(dlg)
    sp_hi.setDecimals(6)
    sp_hi.setRange(-1e12, 1e12)
    sp_hi.setValue(float(hi0))

    form.addRow("Min:", sp_lo)
    form.addRow("Max:", sp_hi)

    buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, parent=dlg)
    form.addRow(buttons)

    buttons.accepted.connect(dlg.accept)
    buttons.rejected.connect(dlg.reject)

    if dlg.exec() != QDialog.Accepted:
        return None

    lo = float(sp_lo.value())
    hi = float(sp_hi.value())
    if hi <= lo:
        return None
    return (lo, hi)


class CameraWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("CameraWidget")

        self.current_image = np.zeros((512, 512), dtype=np.uint8)
        self.autoscale_enabled = True
        self.lock_enabled = True
        self.grid_enabled = True

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(6, 6, 6, 6)
        main_layout.setSpacing(6)

        # ==========================================================
        # Bandeau compact sur 1 ligne
        # ==========================================================
        controls_frame = QFrame()
        controls_frame.setStyleSheet(_PANEL_FRAME_STYLE)

        controls_layout = QHBoxLayout(controls_frame)
        controls_layout.setContentsMargins(8, 6, 8, 6)
        controls_layout.setSpacing(6)

        self.button_snap = QPushButton("Snap")
        self.button_snap.setStyleSheet(_BUTTON_STYLE)
        self.button_snap.setFixedWidth(58)
        controls_layout.addWidget(self.button_snap)

        self.button_live = QPushButton("Live")
        self.button_live.setCheckable(True)
        self.button_live.setStyleSheet(_BUTTON_STYLE_TOGGLE)
        self.button_live.setFixedWidth(58)
        controls_layout.addWidget(self.button_live)

        self.button_stop = QPushButton("Stop")
        self.button_stop.setStyleSheet(_BUTTON_STYLE)
        self.button_stop.setFixedWidth(52)
        controls_layout.addWidget(self.button_stop)

        self.label_status_run = QLabel("Idle")
        self.label_status_run.setStyleSheet(_STATUS_VALUE_STYLE)
        self.label_status_run.setMinimumWidth(70)
        controls_layout.addWidget(self.label_status_run)

        self.button_reset = QPushButton("Reset")
        self.button_reset.setStyleSheet(_BUTTON_STYLE)
        self.button_reset.setFixedWidth(58)
        controls_layout.addWidget(self.button_reset)

        sep = QFrame()
        sep.setFrameShape(QFrame.VLine)
        sep.setStyleSheet("color:#444;")
        controls_layout.addWidget(sep)

        exposure_label = QLabel("Exposure")
        controls_layout.addWidget(exposure_label)

        self.spin_exposure_ms = QDoubleSpinBox()
        self.spin_exposure_ms.setDecimals(3)
        self.spin_exposure_ms.setRange(0.001, 1_000_000.0)
        self.spin_exposure_ms.setValue(1.0)
        self.spin_exposure_ms.setSingleStep(1.0)
        self.spin_exposure_ms.setFixedWidth(78)
        _apply_spinbox_palette(self.spin_exposure_ms)
        controls_layout.addWidget(self.spin_exposure_ms)

        exposure_unit_label = QLabel("(ms)")
        exposure_unit_label.setStyleSheet(_STATUS_VALUE_STYLE)
        controls_layout.addWidget(exposure_unit_label)

        self.cb_auto_exposure = QCheckBox("Auto Exp")
        self.cb_auto_exposure.setStyleSheet(_CHECKBOX_STYLE)
        controls_layout.addWidget(self.cb_auto_exposure)

        binning_label = QLabel("Binning")
        controls_layout.addWidget(binning_label)

        self.combo_binning = QComboBox()
        self.combo_binning.addItems(["1x1", "2x2", "4x4"])
        self.combo_binning.setStyleSheet(_COMBO_STYLE)
        self.combo_binning.setFixedWidth(72)
        controls_layout.addWidget(self.combo_binning)

        format_label = QLabel("Format")
        controls_layout.addWidget(format_label)

        self.combo_pixel_format = QComboBox()
        self.combo_pixel_format.addItems(["Mono8", "RGB24"])
        self.combo_pixel_format.setStyleSheet(_COMBO_STYLE)
        self.combo_pixel_format.setFixedWidth(86)
        controls_layout.addWidget(self.combo_pixel_format)

        controls_layout.addStretch(1)

        main_layout.addWidget(controls_frame)

        # ==========================================================
        # ImageView
        # ==========================================================
        self.plot_item = pg.PlotItem()
        self.plot_item.setLabel("left", "y (px)")
        self.plot_item.setLabel("bottom", "x (px)")

        self.image_view = pg.ImageView(parent=self, view=self.plot_item)
        self.image_view.setPredefinedGradient("inferno")
        self.image_view.setImage(
            self.current_image,
            autoLevels=False,
            levels=(0, 255),
            autoRange=False,
            autoHistogramRange=False
        )
        self.image_view.getView().setAspectLocked(True)
        self.image_view.getView().autoRange()

        main_layout.addWidget(self.image_view, stretch=1)

        # ==========================================================
        # Footer
        # ==========================================================
        footer = QWidget()
        footer_layout = QHBoxLayout(footer)
        footer_layout.setContentsMargins(10, 6, 10, 6)
        footer_layout.setSpacing(8)

        self.label_pixel_status = QLabel("x: -, y: -, Counts: -")
        self.label_pixel_status.setStyleSheet("color: #aaa; padding: 2px; background: transparent; border: none;")
        self.label_pixel_status.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.label_pixel_status.setMinimumWidth(260)
        self.label_pixel_status.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        footer_layout.addWidget(self.label_pixel_status, stretch=1)

        self.cb_autoscale = QCheckBox("Autoscale")
        self.cb_autoscale.setChecked(True)
        self.cb_autoscale.setStyleSheet(_CHECKBOX_STYLE)
        footer_layout.addWidget(self.cb_autoscale)

        self.cb_lock = QCheckBox("Lock")
        self.cb_lock.setChecked(True)
        self.cb_lock.setStyleSheet(_CHECKBOX_STYLE)
        footer_layout.addWidget(self.cb_lock)

        self.cb_grid = QCheckBox("Grid")
        self.cb_grid.setChecked(True)
        self.cb_grid.setStyleSheet(_CHECKBOX_STYLE)
        footer_layout.addWidget(self.cb_grid)

        self.button_set_levels = QPushButton("Set Levels")
        self.button_set_levels.setFixedHeight(22)
        footer_layout.addWidget(self.button_set_levels)

        self.button_reset_levels = QPushButton("Reset Levels")
        self.button_reset_levels.setFixedHeight(22)
        footer_layout.addWidget(self.button_reset_levels)

        main_layout.addWidget(footer)

        # Connexions
        self.cb_autoscale.toggled.connect(self._on_autoscale_toggled)
        self.cb_lock.toggled.connect(self._on_lock_toggled)
        self.cb_grid.toggled.connect(self._on_grid_toggled)
        self.button_set_levels.clicked.connect(self._on_set_levels_clicked)
        self.button_reset_levels.clicked.connect(self._on_reset_levels_clicked)
        self.cb_auto_exposure.toggled.connect(self._on_auto_exposure_toggled)
        self.button_reset.clicked.connect(self.reset_controls)

        try:
            self.image_view.getView().scene().sigMouseMoved.connect(self._on_mouse_moved)
        except Exception:
            pass

        self.image_view.getView().showGrid(True, True)
        self._apply_levels(0.0, 255.0)

    def get_parameters(self):
        return {
            "exposure_ms": self.spin_exposure_ms.value(),
            "binning": self.combo_binning.currentText(),
            "pixel_format": self.combo_pixel_format.currentText(),
            "auto_exposure": self.cb_auto_exposure.isChecked(),
        }

    def set_parameters(
        self,
        exposure_ms=None,
        binning=None,
        pixel_format=None,
        auto_exposure=None,
    ):
        if exposure_ms is not None:
            self.spin_exposure_ms.setValue(float(exposure_ms))
        if binning is not None:
            idx = self.combo_binning.findText(str(binning))
            if idx >= 0:
                self.combo_binning.setCurrentIndex(idx)
        if pixel_format is not None:
            idx = self.combo_pixel_format.findText(str(pixel_format))
            if idx >= 0:
                self.combo_pixel_format.setCurrentIndex(idx)
        if auto_exposure is not None:
            self.cb_auto_exposure.setChecked(bool(auto_exposure))

    def set_status(self, text):
        self.label_status_run.setText(str(text))

    def set_running(self, running: bool):
        self.button_snap.setEnabled(not running)
        self.button_live.setEnabled(True)
        self.button_stop.setEnabled(running)
        self.button_reset.setEnabled(True)

        self.spin_exposure_ms.setEnabled(not running and not self.cb_auto_exposure.isChecked())
        self.combo_binning.setEnabled(not running)
        self.combo_pixel_format.setEnabled(not running)

        self.cb_auto_exposure.setEnabled(not running)

        self.cb_autoscale.setEnabled(True)
        self.cb_lock.setEnabled(True)
        self.cb_grid.setEnabled(True)
        self.button_set_levels.setEnabled(True)
        self.button_reset_levels.setEnabled(True)

    def set_live_button_state(self, live_running: bool):
        self.button_live.blockSignals(True)
        self.button_live.setChecked(bool(live_running))
        self.button_live.blockSignals(False)

    def set_binning_list(self, values):
        current = self.combo_binning.currentText()
        self.combo_binning.blockSignals(True)
        self.combo_binning.clear()
        for v in values:
            self.combo_binning.addItem(str(v))
        idx = self.combo_binning.findText(current)
        if idx >= 0:
            self.combo_binning.setCurrentIndex(idx)
        self.combo_binning.blockSignals(False)

    def set_pixel_format_list(self, values):
        current = self.combo_pixel_format.currentText()
        self.combo_pixel_format.blockSignals(True)
        self.combo_pixel_format.clear()
        for v in values:
            self.combo_pixel_format.addItem(str(v))
        idx = self.combo_pixel_format.findText(current)
        if idx >= 0:
            self.combo_pixel_format.setCurrentIndex(idx)
        self.combo_pixel_format.blockSignals(False)

    def set_image(self, img, width_um=None, height_um=None):
        self.current_image = np.asarray(img, dtype=np.float32)

        self.image_view.setImage(
            self.current_image,
            autoLevels=self.autoscale_enabled,
            autoRange=False,
            autoHistogramRange=False
        )

        img_item = self.image_view.getImageItem()

        if width_um is not None and height_um is not None:
            h, w = self.current_image.shape[:2]
            sx = float(width_um) / float(w) if w > 0 else 1.0
            sy = float(height_um) / float(h) if h > 0 else 1.0
            img_item.setTransform(QTransform.fromScale(sx, sy))
            img_item.setPos(0, 0)

            self.plot_item.setLabel("left", "y (um)")
            self.plot_item.setLabel("bottom", "x (um)")
        else:
            img_item.setTransform(QTransform())
            img_item.setPos(0, 0)
            self.plot_item.setLabel("left", "y (px)")
            self.plot_item.setLabel("bottom", "x (px)")

        self.image_view.getView().setAspectLocked(self.lock_enabled)

        if self.autoscale_enabled:
            lo, hi = self._get_image_minmax()
            self._apply_levels(lo, hi)

        self.image_view.getView().showGrid(self.grid_enabled, self.grid_enabled)

    def _get_image_minmax(self):
        arr = np.asarray(self.current_image)
        finite = arr[np.isfinite(arr)]
        if finite.size == 0:
            return 0.0, 255.0

        lo = float(np.min(finite))
        hi = float(np.max(finite))
        if hi <= lo:
            hi = lo + 1.0
        return lo, hi

    def _apply_levels(self, lo, hi):
        if hi <= lo:
            hi = lo + 1.0

        self.image_view.setLevels(lo, hi)

        hist = self.image_view.ui.histogram

        try:
            hist.region.setRegion((lo, hi))
        except Exception:
            pass

        try:
            hist.setLevels(lo, hi)
        except Exception:
            pass

        try:
            hist.item.vb.setYRange(lo, hi, padding=0)
        except Exception:
            pass

    def _on_autoscale_toggled(self, checked):
        self.autoscale_enabled = bool(checked)
        if checked:
            lo, hi = self._get_image_minmax()
            self._apply_levels(lo, hi)

    def _on_lock_toggled(self, checked):
        self.lock_enabled = bool(checked)
        self.image_view.getView().setAspectLocked(bool(checked))

    def _on_grid_toggled(self, checked):
        self.grid_enabled = bool(checked)
        self.image_view.getView().showGrid(bool(checked), bool(checked))

    def _on_set_levels_clicked(self):
        self.autoscale_enabled = False
        self.cb_autoscale.blockSignals(True)
        self.cb_autoscale.setChecked(False)
        self.cb_autoscale.blockSignals(False)

        try:
            lo0, hi0 = self.image_view.getLevels()
        except Exception:
            lo0, hi0 = self._get_image_minmax()

        res = ask_levels_min_max(
            parent=self,
            title="Camera LUT",
            lo0=lo0,
            hi0=hi0
        )
        if res is None:
            return

        lo, hi = res
        if hi <= lo:
            QMessageBox.warning(self, "Invalid LUT values", "Max must be greater than Min.")
            return

        self._apply_levels(float(lo), float(hi))

    def reset_controls(self):
        self.spin_exposure_ms.setValue(1.0)
        self.cb_auto_exposure.setChecked(False)

        idx = self.combo_binning.findText("1x1")
        if idx >= 0:
            self.combo_binning.setCurrentIndex(idx)

        idx = self.combo_pixel_format.findText("Mono8")
        if idx >= 0:
            self.combo_pixel_format.setCurrentIndex(idx)

        self.label_status_run.setText("Idle")
        self.button_live.setChecked(False)

    def _on_reset_levels_clicked(self):
        self.autoscale_enabled = False
        self.cb_autoscale.blockSignals(True)
        self.cb_autoscale.setChecked(False)
        self.cb_autoscale.blockSignals(False)

        self._apply_levels(0.0, 255.0)

    def _on_auto_exposure_toggled(self, checked):
        self.spin_exposure_ms.setEnabled(not bool(checked))

    def _on_mouse_moved(self, pos):
        if self.current_image is None:
            self.label_pixel_status.setText("x: -, y: -, Counts: -")
            return

        try:
            vb = self.image_view.getView().getViewBox()
            mouse_point = vb.mapSceneToView(pos)

            x = int(mouse_point.x())
            y = int(mouse_point.y())

            img = self.current_image
            if 0 <= x < img.shape[1] and 0 <= y < img.shape[0]:
                intensity = img[y, x]
                self.label_pixel_status.setText(
                    f"x: {x:4d}  y: {y:4d}  Counts: {float(intensity):.2f}"
                )
            else:
                self.label_pixel_status.setText("x: -  y: -  Counts: -")
        except Exception:
            self.label_pixel_status.setText("x: -  y: -  Counts: -")