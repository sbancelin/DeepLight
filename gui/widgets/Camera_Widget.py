# DeepLight/gui/widgets/Camera_Widget.py

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QDoubleSpinBox, QComboBox, QSizePolicy, QCheckBox,
    QDialog, QDialogButtonBox, QFormLayout, QMessageBox
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QTransform

import numpy as np
import pyqtgraph as pg


_CHECKBOX_STYLE = """
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


def ask_levels_min_max(parent=None, title="LUT Levels", lo0=0.0, hi0=255.0):
    """Ouvre un dialogue simple pour saisir les niveaux min/max de la LUT."""
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
    """
    Widget de visualisation et de pilotage d'une caméra.

    Ce widget ne parle pas directement au hardware.
    Il expose simplement une API UI que le manager/backend peut utiliser.
    """
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
        # Barre de contrôle haute
        # ==========================================================
        control_bar = QHBoxLayout()
        control_bar.setContentsMargins(0, 0, 0, 0)
        control_bar.setSpacing(8)

        control_bar.addWidget(QLabel("Exposure (ms)"))
        self.spin_exposure_ms = QDoubleSpinBox()
        self.spin_exposure_ms.setDecimals(3)
        self.spin_exposure_ms.setRange(0.001, 1_000_000.0)
        self.spin_exposure_ms.setValue(1)
        self.spin_exposure_ms.setSingleStep(1.0)
        control_bar.addWidget(self.spin_exposure_ms)

        control_bar.addWidget(QLabel("FPS"))
        self.spin_fps = QDoubleSpinBox()
        self.spin_fps.setDecimals(3)
        self.spin_fps.setRange(0.001, 10_000.0)
        self.spin_fps.setValue(10.0)
        self.spin_fps.setSingleStep(1.0)
        control_bar.addWidget(self.spin_fps)

        control_bar.addWidget(QLabel("Gain"))
        self.spin_gain = QDoubleSpinBox()
        self.spin_gain.setDecimals(3)
        self.spin_gain.setRange(0.0, 1000.0)
        self.spin_gain.setValue(0.0)
        self.spin_gain.setSingleStep(1.0)
        control_bar.addWidget(self.spin_gain)

        control_bar.addWidget(QLabel("Binning"))
        self.combo_binning = QComboBox()
        self.combo_binning.addItems(["1x1", "2x2", "4x4"])
        control_bar.addWidget(self.combo_binning)

        control_bar.addWidget(QLabel("Pixel format"))
        self.combo_pixel_format = QComboBox()
        self.combo_pixel_format.addItems(["Mono8", "RGB24"])
        control_bar.addWidget(self.combo_pixel_format)

        self.cb_auto_exposure = QCheckBox("Auto Exp")
        self.cb_auto_exposure.setStyleSheet(_CHECKBOX_STYLE)
        control_bar.addWidget(self.cb_auto_exposure)

        self.cb_auto_gain = QCheckBox("Auto Gain")
        self.cb_auto_gain.setStyleSheet(_CHECKBOX_STYLE)
        control_bar.addWidget(self.cb_auto_gain)

        self.button_snap = QPushButton("Snap")
        control_bar.addWidget(self.button_snap)

        self.button_live = QPushButton("Start Live")
        control_bar.addWidget(self.button_live)

        self.button_stop = QPushButton("Stop")
        self.button_stop.setEnabled(False)
        control_bar.addWidget(self.button_stop)

        self.label_status_run = QLabel("Idle")
        self.label_status_run.setMinimumWidth(180)
        control_bar.addWidget(self.label_status_run)

        control_bar.addStretch(1)
        main_layout.addLayout(control_bar)

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
        footer_layout.setContentsMargins(0, 0, 0, 0)
        footer_layout.setSpacing(8)

        self.label_pixel_status = QLabel("x: -, y: -, I: -")
        self.label_pixel_status.setStyleSheet("color: #aaa; padding: 2px;")
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

        # ==========================================================
        # Connexions
        # ==========================================================
        self.cb_autoscale.toggled.connect(self._on_autoscale_toggled)
        self.cb_lock.toggled.connect(self._on_lock_toggled)
        self.cb_grid.toggled.connect(self._on_grid_toggled)
        self.button_set_levels.clicked.connect(self._on_set_levels_clicked)
        self.button_reset_levels.clicked.connect(self._on_reset_levels_clicked)
        self.cb_auto_exposure.toggled.connect(self._on_auto_exposure_toggled)
        self.cb_auto_gain.toggled.connect(self._on_auto_gain_toggled)

        try:
            self.image_view.getView().scene().sigMouseMoved.connect(self._on_mouse_moved)
        except Exception:
            pass

        self.image_view.getView().showGrid(True, True)
        self._apply_levels(0.0, 255.0)

    # ==========================================================
    # Public API
    # ==========================================================
    def get_parameters(self):
        return {
            "exposure_ms": self.spin_exposure_ms.value(),
            "fps": self.spin_fps.value(),
            "gain": self.spin_gain.value(),
            "binning": self.combo_binning.currentText(),
            "pixel_format": self.combo_pixel_format.currentText(),
            "auto_exposure": self.cb_auto_exposure.isChecked(),
            "auto_gain": self.cb_auto_gain.isChecked(),
        }

    def set_parameters(
        self,
        exposure_ms=None,
        fps=None,
        gain=None,
        binning=None,
        pixel_format=None,
        auto_exposure=None,
        auto_gain=None,
    ):
        if exposure_ms is not None:
            self.spin_exposure_ms.setValue(float(exposure_ms))
        if fps is not None:
            self.spin_fps.setValue(float(fps))
        if gain is not None:
            self.spin_gain.setValue(float(gain))
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
        if auto_gain is not None:
            self.cb_auto_gain.setChecked(bool(auto_gain))

    def set_status(self, text):
        self.label_status_run.setText(str(text))

    def set_running(self, running: bool):
        self.button_snap.setEnabled(not running)
        self.button_live.setEnabled(True)

        self.spin_exposure_ms.setEnabled(not running and not self.cb_auto_exposure.isChecked())
        self.spin_fps.setEnabled(not running)
        self.spin_gain.setEnabled(not running and not self.cb_auto_gain.isChecked())
        self.combo_binning.setEnabled(not running)
        self.combo_pixel_format.setEnabled(not running)

        self.cb_auto_exposure.setEnabled(not running)
        self.cb_auto_gain.setEnabled(not running)

        self.cb_autoscale.setEnabled(True)
        self.cb_lock.setEnabled(True)
        self.cb_grid.setEnabled(True)
        self.button_set_levels.setEnabled(True)
        self.button_reset_levels.setEnabled(True)

        self.button_stop.setEnabled(running)

    def set_live_button_state(self, live_running: bool):
        self.button_live.setText("Stop Live" if live_running else "Start Live")

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

    # ==========================================================
    # Internal helpers
    # ==========================================================
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

    # ==========================================================
    # Slots UI
    # ==========================================================
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

    def _on_reset_levels_clicked(self):
        self.autoscale_enabled = False
        self.cb_autoscale.blockSignals(True)
        self.cb_autoscale.setChecked(False)
        self.cb_autoscale.blockSignals(False)

        self._apply_levels(0.0, 255.0)

    def _on_auto_exposure_toggled(self, checked):
        self.spin_exposure_ms.setEnabled(not bool(checked))

    def _on_auto_gain_toggled(self, checked):
        self.spin_gain.setEnabled(not bool(checked))

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
                    f"x: {x:4d}  y: {y:4d}  I: {float(intensity):.2f}"
                )
            else:
                self.label_pixel_status.setText("x: -  y: -  Counts: -")
        except Exception:
            self.label_pixel_status.setText("x: -  y: -  Counts: -")