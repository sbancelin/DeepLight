# DeepLight/gui/widgets/Stitching_Widget.py

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QSpinBox, QComboBox, QSizePolicy, QCheckBox,
    QDialog, QDialogButtonBox, QFormLayout, QDoubleSpinBox, QMessageBox, QGraphicsRectItem
)
from PySide6.QtCore import Qt, QRectF
from PySide6.QtGui import QTransform, QPen, QColor

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

class StitchingWidget(QWidget):
    """Widget de prévisualisation et de pilotage d'une acquisition mosaïque."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("StitchingWidget")

        self.current_image = np.zeros((512, 512), dtype=np.uint8)
        self.autoscale_enabled = True
        self.lock_enabled = True
        self.grid_enabled = True
        self._mosaic_rect_items = []
        self.layout_preview_enabled = True

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(6, 6, 6, 6)
        main_layout.setSpacing(6)

        # ==========================================
        # Barre de contrôle haute
        # ==========================================
        control_bar = QHBoxLayout()
        control_bar.setContentsMargins(0, 0, 0, 0)
        control_bar.setSpacing(8)

        control_bar.addWidget(QLabel("Tiles X"))
        self.spin_tiles_x = QSpinBox()
        self.spin_tiles_x.setRange(1, 1000)
        self.spin_tiles_x.setValue(3)
        control_bar.addWidget(self.spin_tiles_x)

        control_bar.addWidget(QLabel("Tiles Y"))
        self.spin_tiles_y = QSpinBox()
        self.spin_tiles_y.setRange(1, 1000)
        self.spin_tiles_y.setValue(3)
        control_bar.addWidget(self.spin_tiles_y)

        control_bar.addWidget(QLabel("Overlap (px)"))
        self.spin_overlap = QSpinBox()
        self.spin_overlap.setRange(0, 10000)
        self.spin_overlap.setValue(0)
        control_bar.addWidget(self.spin_overlap)

        control_bar.addWidget(QLabel("Channel"))
        self.combo_channel = QComboBox()
        control_bar.addWidget(self.combo_channel)

        self.cb_show_layout = QCheckBox("Show layout")
        self.cb_show_layout.setChecked(True)
        self.cb_show_layout.setStyleSheet(_CHECKBOX_STYLE)
        control_bar.addWidget(self.cb_show_layout)

        self.button_acquire = QPushButton("Acquire Mosaic")
        control_bar.addWidget(self.button_acquire)

        self.button_stop = QPushButton("Stop")
        control_bar.addWidget(self.button_stop)

        self.label_status_run = QLabel("Idle")
        self.label_status_run.setMinimumWidth(180)
        control_bar.addWidget(self.label_status_run)

        control_bar.addStretch(1)
        main_layout.addLayout(control_bar)

        # ==========================================
        # ImageView
        # ==========================================
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
        self.image_view.getView().autoRange()

        main_layout.addWidget(self.image_view, stretch=1)

        # ==========================================
        # Footer identique à Scan
        # ==========================================
        footer = QWidget()
        footer_layout = QHBoxLayout(footer)
        footer_layout.setContentsMargins(0, 0, 0, 0)
        footer_layout.setSpacing(8)

        self.label_pixel_status = QLabel("x: -, y: -, I: -")
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

        # ==========================================
        # Connexions
        # ==========================================
        self.cb_autoscale.toggled.connect(self._on_autoscale_toggled)
        self.cb_lock.toggled.connect(self._on_lock_toggled)
        self.cb_grid.toggled.connect(self._on_grid_toggled)
        self.button_set_levels.clicked.connect(self._on_set_levels_clicked)
        self.button_reset_levels.clicked.connect(self._on_reset_levels_clicked)
        self.cb_show_layout.toggled.connect(self._on_show_layout_toggled)

        try:
            self.image_view.getView().scene().sigMouseMoved.connect(self._on_mouse_moved)
        except Exception:
            pass

        self.image_view.getView().showGrid(True, True)
        # init levels
        self._apply_levels(0.0, 255.0)

    # ==========================================================
    # Public API
    # ==========================================================
    def _on_show_layout_toggled(self, checked):
        self.layout_preview_enabled = bool(checked)

        if not checked:
            self._clear_mosaic_preview()
    
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

    def get_parameters(self):
        return {
            "tiles_x": self.spin_tiles_x.value(),
            "tiles_y": self.spin_tiles_y.value(),
            "overlap_px": self.spin_overlap.value(),
            "channel": self.combo_channel.currentText(),
        }

    def set_status(self, text):
        self.label_status_run.setText(str(text))

    def set_running(self, running: bool):
        self.button_acquire.setEnabled(not running)
        self.spin_tiles_x.setEnabled(not running)
        self.spin_tiles_y.setEnabled(not running)
        self.spin_overlap.setEnabled(not running)
        self.combo_channel.setEnabled(not running)

        self.cb_autoscale.setEnabled(not running)
        self.cb_lock.setEnabled(not running)
        self.cb_grid.setEnabled(not running)
        self.button_set_levels.setEnabled(not running)
        self.button_reset_levels.setEnabled(not running)

        self.button_stop.setEnabled(True)         # Le bouton Stop reste disponible même pendant l'arrêt / l'idle.

    def set_image(self, img, width_um=None, height_um=None):
        self.current_image = np.asarray(img, dtype=np.float32)

        self.image_view.setImage(
            self.current_image,
            autoLevels=self.autoscale_enabled,
            autoRange=False,
            autoHistogramRange=False
        )

        if width_um is not None and height_um is not None:
            h, w = self.current_image.shape[:2]
            sx = float(width_um) / float(w) if w > 0 else 1.0
            sy = float(height_um) / float(h) if h > 0 else 1.0

            img_item = self.image_view.getImageItem()
            img_item.setTransform(QTransform.fromScale(sx, sy))
            img_item.setPos(0, 0)

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

    def _on_autoscale_toggled(self, checked):
        self.autoscale_enabled = bool(checked)
        if checked:
            lo, hi = self._get_image_minmax()
            self._apply_levels(lo, hi)

    def _on_lock_toggled(self, checked):
        self.lock_enabled = bool(checked)
        self.image_view.getView().setAspectLocked(bool(checked))

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

    def _clear_mosaic_preview(self):
        view = self.image_view.getView()
        for item in self._mosaic_rect_items:
            try:
                view.removeItem(item)
            except Exception:
                pass
        self._mosaic_rect_items.clear()

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
        """
        Dessine les contours prévus de la mosaïque en vert pointillé.
        Coordonnées en µm, comme l'image affichée.
        """
        self._clear_mosaic_preview()

        if not self.layout_preview_enabled:
            return

        if tiles_x <= 0 or tiles_y <= 0:
            return
        if tile_width_px <= 0 or tile_height_px <= 0:
            return

        step_x_um = tile_width_um * (tile_width_px - overlap_px) / float(tile_width_px)
        step_y_um = tile_height_um * (tile_height_px - overlap_px) / float(tile_height_px)

        pen = QPen(QColor("#3AB16F"))
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

    def set_mosaic_layout_preview(self, scan_params: dict):
        """
        Met à jour l'overlay des futures tuiles à partir des paramètres scan
        et des contrôles mosaïque du widget.
        """
        try:
            rows = [row for row in scan_params.get("rows", []) if row.get("axis") != "None"]
            row_x = next(row for row in rows if str(row.get("axis", "")).startswith("X"))
            row_y = next(row for row in rows if str(row.get("axis", "")).startswith("Y"))
        except Exception:
            self._clear_mosaic_preview()
            return

        self.update_mosaic_preview_grid(
            tiles_x=self.spin_tiles_x.value(),
            tiles_y=self.spin_tiles_y.value(),
            tile_width_um=float(row_x.get("size_um", 1.0) or 1.0),
            tile_height_um=float(row_y.get("size_um", 1.0) or 1.0),
            overlap_px=int(self.spin_overlap.value() or 0),
            tile_width_px=int(row_x.get("pixels", 1) or 1),
            tile_height_px=int(row_y.get("pixels", 1) or 1),
        )