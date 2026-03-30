from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QSpinBox, QComboBox, QSizePolicy, QCheckBox,
    QDialog, QDialogButtonBox, QFormLayout, QDoubleSpinBox, QMessageBox,
    QGraphicsRectItem, QFrame
)
from PySide6.QtCore import Qt, QRectF
from PySide6.QtGui import QTransform, QPen, QColor, QPalette

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


class StitchingWidget(QWidget):
    """Widget de prévisualisation et de pilotage d'une acquisition mosaïque."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("StitchingWidget")

        self.current_image = np.zeros((512, 512), dtype=np.uint8)
        self.autoscale_enabled = True
        self.lock_enabled = True
        self.grid_enabled = True
        self.layout_preview_enabled = True

        self._mosaic_rect_items = []
        self._layout_placeholder_item = None

        self._default_tile_x = 3
        self._default_tile_y = 3
        self._default_overlap = 10
        self._default_show_layout = True

        # taille physique par défaut affichée
        self._default_width_um = 512.0
        self._default_height_um = 512.0

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(6, 6, 6, 6)
        main_layout.setSpacing(6)

        # ==========================================================
        # Bandeau compact
        # ==========================================================
        controls_frame = QFrame()
        controls_frame.setStyleSheet(_PANEL_FRAME_STYLE)
        controls_layout = QHBoxLayout(controls_frame)
        controls_layout.setContentsMargins(8, 6, 8, 6)
        controls_layout.setSpacing(6)

        self.button_acquire = QPushButton("Acquire")
        self.button_acquire.setCheckable(True)
        self.button_acquire.setStyleSheet(_BUTTON_STYLE_TOGGLE)
        self.button_acquire.setFixedWidth(72)
        controls_layout.addWidget(self.button_acquire)

        self.button_stop = QPushButton("Stop")
        self.button_stop.setStyleSheet(_BUTTON_STYLE)
        self.button_stop.setFixedWidth(52)
        controls_layout.addWidget(self.button_stop)

        self.label_status = QLabel("Idle")
        self.label_status.setStyleSheet(_STATUS_VALUE_STYLE)
        self.label_status.setMinimumWidth(42)
        controls_layout.addWidget(self.label_status)

        self.button_reset = QPushButton("Reset")
        self.button_reset.setStyleSheet(_BUTTON_STYLE)
        self.button_reset.setFixedWidth(58)
        controls_layout.addWidget(self.button_reset)

        channel_label = QLabel("Channel")
        controls_layout.addWidget(channel_label)

        self.combo_channel = QComboBox()
        self.combo_channel.setStyleSheet("""
                QComboBox {
                    background-color: #333;
                    color: white;
                    border: 1px solid #555;
                    border-radius: 3px;
                    padding: 2px;
                    min-height: 20px;
                }
            """)
        self.combo_channel.setMinimumWidth(90)
        controls_layout.addWidget(self.combo_channel)

        tiles_x_label = QLabel("Tiles X")
        controls_layout.addWidget(tiles_x_label)

        self.spin_tile_x = QSpinBox()
        self.spin_tile_x.setRange(1, 1000)
        self.spin_tile_x.setValue(self._default_tile_x)
        self.spin_tile_x.setFixedWidth(58)
        _apply_spinbox_palette(self.spin_tile_x)
        controls_layout.addWidget(self.spin_tile_x)

        tiles_y_label = QLabel("Tiles Y")
        controls_layout.addWidget(tiles_y_label)

        self.spin_tile_y = QSpinBox()
        self.spin_tile_y.setRange(1, 1000)
        self.spin_tile_y.setValue(self._default_tile_y)
        self.spin_tile_y.setFixedWidth(58)
        _apply_spinbox_palette(self.spin_tile_y)
        controls_layout.addWidget(self.spin_tile_y)

        overlap_label = QLabel("Overlap")
        controls_layout.addWidget(overlap_label)

        self.spin_overlap = QSpinBox()
        self.spin_overlap.setRange(0, 10000)
        self.spin_overlap.setValue(self._default_overlap)
        self.spin_overlap.setFixedWidth(64)
        _apply_spinbox_palette(self.spin_overlap)
        controls_layout.addWidget(self.spin_overlap)

        overlap_unit_label = QLabel("(px)")
        overlap_unit_label.setStyleSheet(_STATUS_VALUE_STYLE)
        controls_layout.addWidget(overlap_unit_label)

        self.cb_show_layout = QCheckBox("Layout")
        self.cb_show_layout.setChecked(self._default_show_layout)
        self.cb_show_layout.setStyleSheet(_CHECKBOX_STYLE)
        controls_layout.addWidget(self.cb_show_layout)

        controls_layout.addStretch(1)

        main_layout.addWidget(controls_frame)        

        # ==========================================================
        # ImageView
        # ==========================================================
        self.plot_item = pg.PlotItem()
        self.plot_item.setLabel("left", "y (um)")
        self.plot_item.setLabel("bottom", "x (um)")

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

        main_layout.addWidget(self.image_view, stretch=1)

        # ==========================================================
        # Footer
        # ==========================================================
        footer = QWidget()
        footer_layout = QHBoxLayout(footer)
        footer_layout.setContentsMargins(0, 0, 0, 0)
        footer_layout.setSpacing(8)

        self.label_pixel_status = QLabel("x: -, y: -, Counts: -")
        self.label_pixel_status.setStyleSheet("color: #aaa; padding: 2px;")
        self.label_pixel_status.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.label_pixel_status.setMinimumWidth(220)
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
        self.cb_show_layout.toggled.connect(self._on_show_layout_toggled)
        self.button_set_levels.clicked.connect(self._on_set_levels_clicked)
        self.button_reset_levels.clicked.connect(self._on_reset_levels_clicked)
        self.button_reset.clicked.connect(self._reset_controls)

        try:
            self.image_view.getView().scene().sigMouseMoved.connect(self._on_mouse_moved)
        except Exception:
            pass

        self.image_view.getView().showGrid(True, True)
        self._apply_levels(0.0, 255.0)

        # Init vue avec taille physique explicite
        self.set_image(
            self.current_image,
            width_um=self._default_width_um,
            height_um=self._default_height_um
        )
        self._set_initial_view_range()
        self._update_layout_placeholder()

    # ==========================================================
    # Public API
    # ==========================================================
    def get_parameters(self):
        return {
            "tiles_x": self.spin_tile_x.value(),
            "tiles_y": self.spin_tile_y.value(),
            "overlap_px": self.spin_overlap.value(),
            "channel": self.combo_channel.currentText(),
            "show_layout": self.cb_show_layout.isChecked(),
        }

    def set_status(self, text):
        self.label_status.setText(str(text))

    def set_running(self, running: bool):
        self.button_acquire.setEnabled(not running)
        self.spin_tile_x.setEnabled(not running)
        self.spin_tile_y.setEnabled(not running)
        self.spin_overlap.setEnabled(not running)
        self.combo_channel.setEnabled(not running)
        self.cb_show_layout.setEnabled(not running)

        self.cb_autoscale.setEnabled(not running)
        self.cb_lock.setEnabled(not running)
        self.cb_grid.setEnabled(not running)
        self.button_set_levels.setEnabled(not running)
        self.button_reset_levels.setEnabled(not running)

        self.button_stop.setEnabled(True)

    def set_channel_list(self, channels):
        current = self.combo_channel.currentText()
        self.combo_channel.blockSignals(True)
        self.combo_channel.clear()
        for ch in channels:
            self.combo_channel.addItem(str(ch))

        idx = self.combo_channel.findText(current)
        if idx >= 0:
            self.combo_channel.setCurrentIndex(idx)
        self.combo_channel.blockSignals(False)

    def set_image(self, img, width_um=None, height_um=None):
        self.current_image = np.asarray(img, dtype=np.float32)

        self.image_view.setImage(
            self.current_image,
            autoLevels=self.autoscale_enabled,
            autoRange=False,
            autoHistogramRange=False
        )

        img_item = self.image_view.getImageItem()

        if width_um is None:
            width_um = float(self.current_image.shape[1])
        if height_um is None:
            height_um = float(self.current_image.shape[0])

        h, w = self.current_image.shape[:2]
        sx = float(width_um) / float(w) if w > 0 else 1.0
        sy = float(height_um) / float(h) if h > 0 else 1.0

        img_item.setTransform(QTransform.fromScale(sx, sy))
        img_item.setPos(0, 0)

        self.plot_item.setLabel("left", "y (um)")
        self.plot_item.setLabel("bottom", "x (um)")

        self.image_view.getView().setAspectLocked(self.lock_enabled)

        if self.autoscale_enabled:
            lo, hi = self._get_image_minmax()
            self._apply_levels(lo, hi)

        self.image_view.getView().showGrid(self.grid_enabled, self.grid_enabled)

        for item in self._mosaic_rect_items:
            try:
                item.setZValue(10)
            except Exception:
                pass

        if self._layout_placeholder_item is not None:
            self._layout_placeholder_item.setZValue(11)

    def set_mosaic_layout_preview(self, scan_params: dict):
        try:
            rows = [row for row in scan_params.get("rows", []) if row.get("axis") != "None"]
            row_x = next(row for row in rows if str(row.get("axis", "")).startswith("X"))
            row_y = next(row for row in rows if str(row.get("axis", "")).startswith("Y"))
        except Exception:
            self._clear_mosaic_preview()
            self._update_layout_placeholder()
            return

        self.update_mosaic_preview_grid(
            tiles_x=self.spin_tile_x.value(),
            tiles_y=self.spin_tile_y.value(),
            tile_width_um=float(row_x.get("size_um", 1.0) or 1.0),
            tile_height_um=float(row_y.get("size_um", 1.0) or 1.0),
            overlap_px=int(self.spin_overlap.value() or 0),
            tile_width_px=int(row_x.get("pixels", 1) or 1),
            tile_height_px=int(row_y.get("pixels", 1) or 1),
        )

    # ==========================================================
    # Internal helpers
    # ==========================================================
    def _set_initial_view_range(self):
        vb = self.image_view.getView().getViewBox()
        vb.setRange(
            xRange=(0.0, self._default_width_um),
            yRange=(0.0, self._default_height_um),
            padding=0.02
        )

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

    def _reset_controls(self):
        self.spin_tile_x.setValue(self._default_tile_x)
        self.spin_tile_y.setValue(self._default_tile_y)
        self.spin_overlap.setValue(self._default_overlap)
        self.cb_show_layout.setChecked(self._default_show_layout)
        self.label_status.setText("Idle")
        self.button_acquire.setChecked(False)
        self._clear_mosaic_preview()
        self._update_layout_placeholder()

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

    def _on_show_layout_toggled(self, checked):
        self.layout_preview_enabled = bool(checked)

        if not checked:
            self._clear_mosaic_preview()
            self._clear_layout_placeholder()
            return

        if len(self._mosaic_rect_items) == 0:
            self._update_layout_placeholder()

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
            title="Stitching LUT",
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

    def _clear_mosaic_preview(self):
        view = self.image_view.getView()
        for item in self._mosaic_rect_items:
            try:
                view.removeItem(item)
            except Exception:
                pass
        self._mosaic_rect_items.clear()

    def _clear_layout_placeholder(self):
        if self._layout_placeholder_item is None:
            return
        try:
            self.image_view.getView().removeItem(self._layout_placeholder_item)
        except Exception:
            pass
        self._layout_placeholder_item = None

    def _update_layout_placeholder(self):
        self._clear_layout_placeholder()

        if not self.layout_preview_enabled:
            return

        if len(self._mosaic_rect_items) > 0:
            return

        pen = QPen(QColor("#9CFF9C"))
        pen.setStyle(Qt.PenStyle.DashLine)
        pen.setWidth(1)

        rect = QGraphicsRectItem(
            QRectF(0.0, 0.0, self._default_width_um, self._default_height_um)
        )
        rect.setPen(pen)
        rect.setBrush(Qt.BrushStyle.NoBrush)
        rect.setZValue(11)

        self.image_view.getView().addItem(rect)
        self._layout_placeholder_item = rect

    def update_mosaic_preview_grid(
        self,
        tiles_x: int,
        tiles_y: int,
        tile_width_um: float,
        tile_height_um: float,
        overlap_px: int,
        tile_width_px: int,
        tile_height_px: int,
    ):
        self._clear_mosaic_preview()
        self._clear_layout_placeholder()

        if not self.layout_preview_enabled:
            return
        if tiles_x <= 0 or tiles_y <= 0:
            return
        if tile_width_px <= 0 or tile_height_px <= 0:
            return

        step_x_um = tile_width_um * (tile_width_px - overlap_px) / float(tile_width_px)
        step_y_um = tile_height_um * (tile_height_px - overlap_px) / float(tile_height_px)

        pen = QPen(QColor("#9CFF9C"))
        pen.setStyle(Qt.PenStyle.DashLine)
        pen.setWidth(1)

        view = self.image_view.getView()

        for iy in range(tiles_y):
            for ix in range(tiles_x):
                x0 = ix * step_x_um
                y0 = iy * step_y_um

                rect = QGraphicsRectItem(QRectF(x0, y0, tile_width_um, tile_height_um))
                rect.setPen(pen)
                rect.setBrush(Qt.BrushStyle.NoBrush)
                rect.setZValue(10)
                view.addItem(rect)
                self._mosaic_rect_items.append(rect)