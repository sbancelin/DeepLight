from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QDoubleSpinBox, QComboBox, QSizePolicy, QCheckBox,
    QDialog, QDialogButtonBox, QFormLayout, QMessageBox, QFrame
)
from PySide6.QtCore import Qt, QLocale, Signal
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
QComboBox:disabled {
    background-color: #1e1e1e;
    color: #555;
    border: 1px solid #333;
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
    spin_palette.setColor(QPalette.Disabled, QPalette.Base, QColor("#1e1e1e"))
    spin_palette.setColor(QPalette.Disabled, QPalette.Text, QColor("#555555"))
    spin_palette.setColor(QPalette.Disabled, QPalette.ButtonText, QColor("#555555"))
    spin_palette.setColor(QPalette.Disabled, QPalette.WindowText, QColor("#555555"))
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
    sigReconnectRequested = Signal()
    sigSaveRequested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("CameraWidget")

        self.current_image = np.zeros((512, 512), dtype=np.uint8)
        self.autoscale_enabled = True
        self.lock_enabled = True
        self.grid_enabled = True
        self._camera_full_shape = (1536, 2048)  # H, W — updated on first full-frame
        self._roi_updating_from_ui = False
        self._roi_updating_from_graphics = False
        self._saved_exposure_ms = 250.0  # restored when auto is turned off

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(6, 6, 6, 6)
        main_layout.setSpacing(6)

        # ==========================================================
        # Controls frame — 2 rows
        # ==========================================================
        controls_frame = QFrame()
        controls_frame.setStyleSheet(_PANEL_FRAME_STYLE)

        controls_layout = QVBoxLayout(controls_frame)
        controls_layout.setContentsMargins(8, 6, 8, 6)
        controls_layout.setSpacing(4)

        # ---- Row 1: Snap / Live / Stop / Save / Reconnect / Status
        row_1 = QHBoxLayout()
        row_1.setContentsMargins(0, 0, 0, 0)
        row_1.setSpacing(6)
        controls_layout.addLayout(row_1)

        self.button_snap = QPushButton("Snap")
        self.button_snap.setStyleSheet(_BUTTON_STYLE)
        self.button_snap.setFixedWidth(58)
        row_1.addWidget(self.button_snap)

        self.button_live = QPushButton("Live")
        self.button_live.setCheckable(True)
        self.button_live.setStyleSheet(_BUTTON_STYLE_TOGGLE)
        self.button_live.setFixedWidth(58)
        row_1.addWidget(self.button_live)

        self.button_stop = QPushButton("Stop")
        self.button_stop.setStyleSheet(_BUTTON_STYLE)
        self.button_stop.setFixedWidth(52)
        row_1.addWidget(self.button_stop)

        self.button_save = QPushButton("Save")
        self.button_save.setStyleSheet(_BUTTON_STYLE)
        self.button_save.setFixedWidth(52)
        row_1.addWidget(self.button_save)

        self.button_reconnect = QPushButton("Connect")
        self.button_reconnect.setStyleSheet(_BUTTON_STYLE)
        row_1.addWidget(self.button_reconnect)

        self.label_status_run = QLabel("Idle")
        self.label_status_run.setStyleSheet(_STATUS_VALUE_STYLE)
        self.label_status_run.setMinimumWidth(70)
        row_1.addWidget(self.label_status_run)

        row_1.addStretch(1)

        # ---- Row 2: Exposure / Auto / Binning / ROI / Reset
        row_2 = QHBoxLayout()
        row_2.setContentsMargins(0, 0, 0, 0)
        row_2.setSpacing(6)
        controls_layout.addLayout(row_2)

        self.cb_auto_exposure = QCheckBox("Auto")
        self.cb_auto_exposure.setStyleSheet(_CHECKBOX_STYLE)
        row_2.addWidget(self.cb_auto_exposure)

        row_2.addWidget(QLabel("Exposure"))
        self.spin_exposure_ms = QDoubleSpinBox()
        self.spin_exposure_ms.setDecimals(3)
        # 0.0 is the sentinel for auto-exposure; setSpecialValueText makes it show "--"
        self.spin_exposure_ms.setRange(0.0, 1_000_000.0)
        self.spin_exposure_ms.setSpecialValueText("--")
        self.spin_exposure_ms.setValue(250.0)
        self.spin_exposure_ms.setSingleStep(1.0)
        self.spin_exposure_ms.setFixedWidth(78)
        _apply_spinbox_palette(self.spin_exposure_ms)
        row_2.addWidget(self.spin_exposure_ms)

        exp_unit = QLabel("ms")
        exp_unit.setStyleSheet(_STATUS_VALUE_STYLE)
        row_2.addWidget(exp_unit)

        row_2.addWidget(QLabel("Binning"))
        self.combo_binning = QComboBox()
        self.combo_binning.addItems(["1x1", "2x2", "4x4"])
        self.combo_binning.setStyleSheet(_COMBO_STYLE)
        self.combo_binning.setMinimumWidth(66)
        row_2.addWidget(self.combo_binning)

        self.cb_roi_enabled = QCheckBox("ROI")
        self.cb_roi_enabled.setStyleSheet(_CHECKBOX_STYLE)
        row_2.addWidget(self.cb_roi_enabled)

        full_h, full_w = self._camera_full_shape
        for label_txt, attr, lo, hi, default in (
            ("X", "spin_roi_x",      0, full_w - 1, 0),
            ("Y", "spin_roi_y",      0, full_h - 1, 0),
            ("W", "spin_roi_width",  1, full_w,     full_w),
            ("H", "spin_roi_height", 1, full_h,     full_h),
        ):
            row_2.addWidget(QLabel(label_txt))
            sp = QDoubleSpinBox()
            sp.setDecimals(0)
            sp.setRange(lo, hi)
            sp.setValue(default)
            sp.setSingleStep(1)
            sp.setFixedWidth(62)
            _apply_spinbox_palette(sp)
            row_2.addWidget(sp)
            setattr(self, attr, sp)

        self.button_reset = QPushButton("Reset")
        self.button_reset.setStyleSheet(_BUTTON_STYLE)
        self.button_reset.setFixedWidth(58)
        row_2.addWidget(self.button_reset)

        row_2.addStretch(1)

        # Format fixed to Mono8 — kept as hidden attribute for API compatibility
        self.combo_pixel_format = QComboBox()
        self.combo_pixel_format.addItems(["Mono8"])
        self.combo_pixel_format.setVisible(False)

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

        # ROI rectangle overlay
        self.roi_item = pg.RectROI(
            [0, 0],
            [full_w, full_h],
            pen=pg.mkPen((80, 220, 120), width=2),
            movable=True,
            removable=False,
            resizable=True,
            rotatable=False,
        )
        self.roi_item.setZValue(10)
        for handle_pos in (
            (0, 0), (1, 0), (0, 1), (1, 1),
            (0.5, 0), (0.5, 1), (0, 0.5), (1, 0.5),
        ):
            self.roi_item.addScaleHandle(handle_pos, (1 - handle_pos[0], 1 - handle_pos[1]))
        self.plot_item.addItem(self.roi_item)
        self.roi_item.setVisible(False)

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

        # ==========================================================
        # Connections
        # ==========================================================
        self.button_reconnect.clicked.connect(self.sigReconnectRequested.emit)
        self.button_save.clicked.connect(self.sigSaveRequested.emit)
        self.cb_autoscale.toggled.connect(self._on_autoscale_toggled)
        self.cb_lock.toggled.connect(self._on_lock_toggled)
        self.cb_grid.toggled.connect(self._on_grid_toggled)
        self.button_set_levels.clicked.connect(self._on_set_levels_clicked)
        self.button_reset_levels.clicked.connect(self._on_reset_levels_clicked)
        self.cb_auto_exposure.toggled.connect(self._on_auto_exposure_toggled)
        self.button_reset.clicked.connect(self.reset_controls)
        self.cb_roi_enabled.toggled.connect(self._on_roi_enabled_toggled)
        self.spin_roi_x.valueChanged.connect(self._on_roi_spin_changed)
        self.spin_roi_y.valueChanged.connect(self._on_roi_spin_changed)
        self.spin_roi_width.valueChanged.connect(self._on_roi_spin_changed)
        self.spin_roi_height.valueChanged.connect(self._on_roi_spin_changed)
        self.roi_item.sigRegionChangeFinished.connect(self._on_roi_graphics_changed)

        try:
            self.image_view.getView().scene().sigMouseMoved.connect(self._on_mouse_moved)
        except Exception:
            pass

        self.image_view.getView().showGrid(True, True)
        self._apply_levels(0.0, 255.0)
        self._update_roi_controls_enabled()

    # ==========================================================
    # Public API
    # ==========================================================

    def set_camera_full_shape(self, h: int, w: int):
        h, w = int(h), int(w)
        if (h, w) == self._camera_full_shape:
            return
        self._camera_full_shape = (h, w)
        self.spin_roi_x.setRange(0, w - 1)
        self.spin_roi_y.setRange(0, h - 1)
        self.spin_roi_width.setRange(1, w)
        self.spin_roi_height.setRange(1, h)

    def get_parameters(self):
        # When auto-exposure is on, the spinbox shows "--" (value=0); use the saved ms instead
        if self.cb_auto_exposure.isChecked():
            exp_ms = self._saved_exposure_ms
        else:
            exp_ms = self.spin_exposure_ms.value()
        return {
            "exposure_ms": exp_ms,
            "binning": self.combo_binning.currentText(),
            "pixel_format": self.combo_pixel_format.currentText(),
            "auto_exposure": self.cb_auto_exposure.isChecked(),
            "roi_enabled": self.cb_roi_enabled.isChecked(),
            "roi_x": int(self.spin_roi_x.value()),
            "roi_y": int(self.spin_roi_y.value()),
            "roi_width": int(self.spin_roi_width.value()),
            "roi_height": int(self.spin_roi_height.value()),
        }

    def set_parameters(
        self,
        exposure_ms=None,
        binning=None,
        pixel_format=None,
        auto_exposure=None,
        roi_enabled=None,
        roi_x=None,
        roi_y=None,
        roi_width=None,
        roi_height=None,
    ):
        if exposure_ms is not None:
            self.spin_exposure_ms.setValue(float(exposure_ms))
        if binning is not None:
            idx = self.combo_binning.findText(str(binning))
            if idx >= 0:
                self.combo_binning.setCurrentIndex(idx)
        if auto_exposure is not None:
            self.cb_auto_exposure.setChecked(bool(auto_exposure))
        if roi_enabled is not None:
            self.cb_roi_enabled.setChecked(bool(roi_enabled))
        if roi_x is not None:
            self.spin_roi_x.setValue(int(roi_x))
        if roi_y is not None:
            self.spin_roi_y.setValue(int(roi_y))
        if roi_width is not None:
            self.spin_roi_width.setValue(int(roi_width))
        if roi_height is not None:
            self.spin_roi_height.setValue(int(roi_height))
        self._update_roi_controls_enabled()
        self._sync_roi_rect_from_controls()

    def set_status(self, text):
        self.label_status_run.setText(str(text))

    def set_running(self, running: bool):
        self.button_snap.setEnabled(not running)
        self.button_live.setEnabled(True)
        self.button_stop.setEnabled(True)
        self.button_reset.setEnabled(True)

        self.spin_exposure_ms.setEnabled(not self.cb_auto_exposure.isChecked())
        self.cb_auto_exposure.setEnabled(True)
        self.combo_binning.setEnabled(not running)
        self._update_roi_controls_enabled()

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
        # Format fixed to Mono8 — ignore
        pass

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

    def reset_controls(self):
        self._saved_exposure_ms = 250.0
        self.cb_auto_exposure.blockSignals(True)
        self.cb_auto_exposure.setChecked(False)
        self.cb_auto_exposure.blockSignals(False)
        self.spin_exposure_ms.setEnabled(True)
        self.spin_exposure_ms.setValue(250.0)

        idx = self.combo_binning.findText("1x1")
        if idx >= 0:
            self.combo_binning.setCurrentIndex(idx)

        self.cb_roi_enabled.setChecked(False)
        full_h, full_w = self._camera_full_shape
        self.spin_roi_x.setValue(0)
        self.spin_roi_y.setValue(0)
        self.spin_roi_width.setValue(full_w)
        self.spin_roi_height.setValue(full_h)
        self._update_roi_controls_enabled()
        self._sync_roi_rect_from_controls()

        self.label_status_run.setText("Idle")
        self.button_live.setChecked(False)

    # ==========================================================
    # Private helpers
    # ==========================================================

    def _update_roi_controls_enabled(self):
        enabled = self.cb_roi_enabled.isChecked()
        for sp in (self.spin_roi_x, self.spin_roi_y,
                   self.spin_roi_width, self.spin_roi_height):
            sp.setEnabled(enabled)
        self.roi_item.setVisible(enabled)

    def _sync_roi_rect_from_controls(self):
        if self._roi_updating_from_graphics:
            return
        self._roi_updating_from_ui = True
        try:
            full_h, full_w = self._camera_full_shape
            x = max(0, min(int(self.spin_roi_x.value()), full_w - 1))
            y = max(0, min(int(self.spin_roi_y.value()), full_h - 1))
            w = max(1, min(int(self.spin_roi_width.value()), full_w - x))
            h = max(1, min(int(self.spin_roi_height.value()), full_h - y))
            self.roi_item.blockSignals(True)
            self.roi_item.setPos((x, y))
            self.roi_item.setSize((w, h))
            self.roi_item.blockSignals(False)
        finally:
            self._roi_updating_from_ui = False

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
    # Slots
    # ==========================================================

    def _on_roi_enabled_toggled(self, checked):
        if checked:
            full_h, full_w = self._camera_full_shape
            w_def, h_def = 640, 480
            x_def = max(0, (full_w - w_def) // 2)
            y_def = max(0, (full_h - h_def) // 2)
            for sp in (self.spin_roi_x, self.spin_roi_y,
                       self.spin_roi_width, self.spin_roi_height):
                sp.blockSignals(True)
            self.spin_roi_x.setValue(x_def)
            self.spin_roi_y.setValue(y_def)
            self.spin_roi_width.setValue(w_def)
            self.spin_roi_height.setValue(h_def)
            for sp in (self.spin_roi_x, self.spin_roi_y,
                       self.spin_roi_width, self.spin_roi_height):
                sp.blockSignals(False)
        self._update_roi_controls_enabled()
        self._sync_roi_rect_from_controls()

    def _on_roi_spin_changed(self, *_args):
        if self._roi_updating_from_ui:
            return
        self._sync_roi_rect_from_controls()

    def _on_roi_graphics_changed(self):
        if self._roi_updating_from_ui:
            return
        self._roi_updating_from_graphics = True
        try:
            full_h, full_w = self._camera_full_shape
            pos = self.roi_item.pos()
            size = self.roi_item.size()
            x = max(0, min(int(round(pos.x())), full_w - 1))
            y = max(0, min(int(round(pos.y())), full_h - 1))
            w = max(1, min(int(round(size.x())), full_w - x))
            h = max(1, min(int(round(size.y())), full_h - y))
            self.spin_roi_x.setValue(x)
            self.spin_roi_y.setValue(y)
            self.spin_roi_width.setValue(w)
            self.spin_roi_height.setValue(h)
        finally:
            self._roi_updating_from_graphics = False
        self._sync_roi_rect_from_controls()

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
        res = ask_levels_min_max(parent=self, title="Camera LUT", lo0=lo0, hi0=hi0)
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
        if checked:
            if self.spin_exposure_ms.value() > 0:
                self._saved_exposure_ms = self.spin_exposure_ms.value()
            self.spin_exposure_ms.blockSignals(True)
            self.spin_exposure_ms.setValue(0.0)  # triggers "--" via setSpecialValueText
            self.spin_exposure_ms.blockSignals(False)
            self.spin_exposure_ms.setEnabled(False)
        else:
            self.spin_exposure_ms.setEnabled(True)
            self.spin_exposure_ms.blockSignals(True)
            self.spin_exposure_ms.setValue(self._saved_exposure_ms)
            self.spin_exposure_ms.blockSignals(False)

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
