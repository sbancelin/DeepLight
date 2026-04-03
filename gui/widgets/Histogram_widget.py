from PySide6.QtWidgets import (QVBoxLayout, QWidget, QHBoxLayout, QPushButton, QComboBox, 
                               QDialog, QDialogButtonBox, QFormLayout, QDoubleSpinBox, QCheckBox, QSizePolicy)
from PySide6.QtCore import Qt, QPointF, QLocale
from PySide6.QtGui import QIcon
import pyqtgraph as pg
import numpy as np

class DoubleClickAxis(pg.AxisItem):
    def __init__(self, orientation, on_double_click=None, *args, **kwargs):
        super().__init__(orientation=orientation, *args, **kwargs)
        self.on_double_click = on_double_click

    def mouseDoubleClickEvent(self, ev):
        ev.accept()
        if callable(self.on_double_click):
            self.on_double_click(self.orientation)


class DoubleClickViewBox(pg.ViewBox):
    def __init__(self, on_double_click=None, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.on_double_click = on_double_click

    def mouseDoubleClickEvent(self, ev):
        ev.accept()
        if callable(self.on_double_click):
            self.on_double_click()


class HistogramWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)

        self.main_layout = QVBoxLayout(self)
        self.main_layout.setContentsMargins(2, 2, 2, 2)
        self.main_layout.setSpacing(4)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)

        self.image_view = None
        self.im_widgets = {}
        self._attached = False
        self._temp_rect = None

        button_style = """
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

        checkbox_style = """
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

        container = QWidget()
        container.setMinimumWidth(0)
        container.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)

        layout = QVBoxLayout(container)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(4)

        # Layout horizontal: bouton ROI + choix canal
        top_layout = QHBoxLayout()
        top_layout.setContentsMargins(0, 0, 0, 0)
        top_layout.setSpacing(6)

        self.toggle_btn = QPushButton()
        self.toggle_btn.setIcon(QIcon("gui/Icons/ROI.svg"))
        self.toggle_btn.setCheckable(True)
        self.toggle_btn.setFixedHeight(22)
        self.toggle_btn.setMinimumWidth(0)
        self.toggle_btn.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.toggle_btn.setStyleSheet(button_style)
        self.toggle_btn.toggled.connect(self.toggle_rect_visibility)
        top_layout.addWidget(self.toggle_btn, stretch=0)

        self.channel_combo = QComboBox()
        self.channel_combo.setMinimumWidth(40)
        self.channel_combo.setStyleSheet("""
                QComboBox {
                    background-color: #333;
                    color: white;
                    border: 1px solid #555;
                    border-radius: 3px;
                    padding: 2px;
                    min-height: 20px;
                }
            """)
        self.channel_combo.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.channel_combo.currentTextChanged.connect(self.on_channel_changed)
        top_layout.addWidget(self.channel_combo, stretch=1)

        self.autoscale_checkbox = QCheckBox("Autoscale")
        self.autoscale_checkbox.setChecked(True)
        self.autoscale_checkbox.setStyleSheet(checkbox_style)
        self.autoscale_checkbox.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.autoscale_checkbox.toggled.connect(self._on_autoscale_toggled)
        top_layout.addWidget(self.autoscale_checkbox, stretch=0)

        layout.addLayout(top_layout)

        # Graphique Histogramme
        self.hist_plot = pg.PlotWidget(
            viewBox=DoubleClickViewBox(self.reset_histogram_view),
            axisItems={
                'left': DoubleClickAxis('left', self._on_axis_double_clicked),
                'bottom': DoubleClickAxis('bottom', self._on_axis_double_clicked),
            }
        )
        self.hist_plot.setBackground('#2b2b2b')
        self.hist_plot.showGrid(x=True, y=True, alpha=0.3)
        self.hist_plot.setLabel('left', 'Count', color='w')
        self.hist_plot.setLabel('bottom', 'Value', color='w')

        ax_left = self.hist_plot.getAxis('left')
        ax_bottom = self.hist_plot.getAxis('bottom')
        ax_left.setTextPen(pg.mkPen('w'))
        ax_left.setPen(pg.mkPen('w'))
        ax_bottom.setTextPen(pg.mkPen('w'))
        ax_bottom.setPen(pg.mkPen('w'))

        self.hist_plot.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.hist_plot.setMinimumHeight(80)

        layout.addWidget(self.hist_plot)
        self.main_layout.addWidget(container)
        self.main_layout.addStretch()

        # ROI
        self.rect_roi = None
        self._click_stage = 0      # 0: attente 1er clic, 1: attente 2e clic
        self._start_point = None
        self._scene = None

        self._start_marker = pg.ScatterPlotItem(
            size=12,
            pen=pg.mkPen('#2E8B57'),
            brush=pg.mkBrush(60, 179, 113, 160)
        )
        self._start_marker.setVisible(False)

    def _on_autoscale_toggled(self, checked: bool):
        if checked:
            self.reset_histogram_view()
    
    def _on_axis_double_clicked(self, orientation: str):
        if self.hist_plot is None:
            return

        plot_item = self.hist_plot.getPlotItem()
        view_box = plot_item.vb
        x_range, y_range = view_box.viewRange()

        if orientation == 'bottom':
            current_min, current_max = float(x_range[0]), float(x_range[1])
            title = "Set X axis range"
        elif orientation == 'left':
            current_min, current_max = float(y_range[0]), float(y_range[1])
            title = "Set Y axis range"
        else:
            return

        result = self._ask_axis_range(title, current_min, current_max)
        if result is None:
            return

        new_min, new_max = result
        if new_max <= new_min:
            return

        if orientation == 'bottom':
            self.hist_plot.enableAutoRange(axis='x', enable=False)
            self.hist_plot.setXRange(new_min, new_max, padding=0)
        elif orientation == 'left':
            self.hist_plot.enableAutoRange(axis='y', enable=False)
            self.hist_plot.setYRange(new_min, new_max, padding=0)

    def _ask_axis_range(self, title: str, current_min: float, current_max: float):
        dialog = QDialog(self)
        dialog.setWindowTitle(title)

        layout = QVBoxLayout(dialog)
        form = QFormLayout()

        min_spin = QDoubleSpinBox(dialog)
        min_spin.setLocale(QLocale.c())
        min_spin.setDecimals(6)
        min_spin.setRange(-1e12, 1e12)
        min_spin.setValue(current_min)
        min_spin.setSingleStep(max(0.1, abs(current_max - current_min) / 100.0))

        max_spin = QDoubleSpinBox(dialog)
        max_spin.setLocale(QLocale.c())
        max_spin.setDecimals(6)
        max_spin.setRange(-1e12, 1e12)
        max_spin.setValue(current_max)
        max_spin.setSingleStep(max(0.1, abs(current_max - current_min) / 100.0))

        form.addRow("Min:", min_spin)
        form.addRow("Max:", max_spin)
        layout.addLayout(form)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, parent=dialog)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)

        if dialog.exec() != QDialog.Accepted:
            return None

        new_min = float(min_spin.value())
        new_max = float(max_spin.value())
        return new_min, new_max

    def reset_histogram_view(self):
        plot_item = self.hist_plot.getPlotItem()
        plot_item.enableAutoRange(axis='x', enable=True)
        plot_item.enableAutoRange(axis='y', enable=True)
        plot_item.vb.autoRange()
    # ------------------------------------------------------------------
    # API
    # ------------------------------------------------------------------
    def set_im_widgets(self, im_widgets: dict):
        self.im_widgets = dict(im_widgets) if im_widgets else {}

        self.channel_combo.blockSignals(True)
        self.channel_combo.clear()
        for ch in sorted(self.im_widgets.keys(), key=str):
            self.channel_combo.addItem(str(ch))
        self.channel_combo.blockSignals(False)

        if self.channel_combo.count() > 0 and self.channel_combo.currentIndex() < 0:
            self.channel_combo.setCurrentIndex(0)

        if self.channel_combo.count() > 0:
            self.on_channel_changed(self.channel_combo.currentText())
        else:
            self.set_image_view(None)

    def on_channel_changed(self, channel_name: str):
        self.set_image_view(self.im_widgets.get(channel_name))

    def set_image_view(self, image_view):
        self._disconnect_scene()

        # Détacher la ROI de l'ancienne view
        if self.image_view is not None and self.rect_roi is not None and self._attached:
            try:
                self.image_view.getView().removeItem(self.rect_roi)
            except Exception:
                pass
            self._attached = False

        # Enlever le marqueur de l'ancienne view
        if self.image_view is not None:
            try:
                self.image_view.getView().removeItem(self._start_marker)
            except Exception:
                pass

        self.image_view = image_view

        if self.image_view is not None:
            self._scene = self.image_view.getView().scene()
            try:
                self._scene.sigMouseClicked.connect(self._on_image_clicked)
                self._scene.sigMouseMoved.connect(self._on_mouse_moved)
            except Exception:
                pass

            try:
                self.image_view.getView().addItem(self._start_marker, ignoreBounds=True)
            except Exception:
                pass

        if self.image_view is not None and self.rect_roi is not None:
            try:
                self.image_view.getView().addItem(self.rect_roi, ignoreBounds=True)
                self._attached = True
            except Exception:
                self._attached = False
        else:
            self._attached = False

        self._apply_visibility()
        self.update_histogram()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _disconnect_scene(self):
        if self._scene is not None:
            try:
                self._scene.sigMouseClicked.disconnect(self._on_image_clicked)
            except Exception:
                pass
            try:
                self._scene.sigMouseMoved.disconnect(self._on_mouse_moved)
            except Exception:
                pass
            self._scene = None

    def _reset_roi_creation_state(self):
        self._start_marker.setVisible(False)
        self._click_stage = 0
        self._start_point = None

        if self._temp_rect is not None and self.image_view is not None:
            try:
                self.image_view.getView().removeItem(self._temp_rect)
            except Exception:
                pass
        self._temp_rect = None

    def _clear_histogram(self):
        self.hist_plot.clear()

    def _get_displayed_image_data(self):
        if self.image_view is None:
            return None

        try:
            image_item = self.image_view.getImageItem()
        except Exception:
            return None

        image_data = getattr(image_item, "image", None)
        if image_data is None or not hasattr(image_data, "shape"):
            return None

        return np.asarray(image_data)

    def _extract_displayed_pixel_values(self):
        image_data = self._get_displayed_image_data()
        if image_data is None:
            return None

        vals = np.asarray(image_data, dtype=np.float64).ravel()
        vals = vals[np.isfinite(vals)]

        if vals.size == 0:
            return None

        return vals
    
    def _extract_pixel_values_in_roi(self):
        if self.image_view is None or self.rect_roi is None:
            return None

        try:
            image_item = self.image_view.getImageItem()
        except Exception:
            return None

        image_data = getattr(image_item, "image", None)
        if image_data is None:
            return None

        image_data = np.asarray(image_data)
        if image_data.ndim < 2:
            return None

        h, w = image_data.shape[:2]

        try:
            roi_path = self.rect_roi.mapToItem(image_item, self.rect_roi.shape())
        except Exception:
            return None

        br = roi_path.boundingRect()

        x_min = max(0, int(np.floor(br.left())))
        x_max = min(w - 1, int(np.ceil(br.right())))
        y_min = max(0, int(np.floor(br.top())))
        y_max = min(h - 1, int(np.ceil(br.bottom())))

        if x_max < x_min or y_max < y_min:
            return None

        vals = []

        for y in range(y_min, y_max + 1):
            for x in range(x_min, x_max + 1):
                # centre du pixel
                pt = QPointF(x + 0.5, y + 0.5)
                if roi_path.contains(pt):
                    vals.append(image_data[y, x])

        if not vals:
            return None

        vals = np.asarray(vals, dtype=np.float64)
        vals = vals[np.isfinite(vals)]

        if vals.size == 0:
            return None

        return vals
    # ------------------------------------------------------------------
    # UI logic
    # ------------------------------------------------------------------
    def toggle_rect_visibility(self, checked: bool):
        self._apply_visibility()

        if checked:
            self.update_histogram()
        else:
            self._clear_histogram()

    def _apply_visibility(self):
        if self.rect_roi is None:
            self._start_marker.setVisible(False)
            return

        if not self._attached:
            try:
                self.rect_roi.hide()
            except Exception:
                pass
            return

        if self.toggle_btn.isChecked():
            self.rect_roi.show()
        else:
            self.rect_roi.hide()
            self._reset_roi_creation_state()
            self._clear_histogram()

    # ------------------------------------------------------------------
    # Mouse interaction
    # ------------------------------------------------------------------
    def _on_mouse_moved(self, ev):
        if self.image_view is None or not self.toggle_btn.isChecked() or self._click_stage != 1:
            return

        plot_item = self.image_view.getView()
        vb = plot_item.vb
        p = vb.mapSceneToView(ev)
        x, y = float(p.x()), float(p.y())

        image_data = self._get_displayed_image_data()
        if image_data is None:
            return

        h, w = image_data.shape[:2]
        x = max(0.0, min(x, w - 1))
        y = max(0.0, min(y, h - 1))

        x0, y0 = self._start_point

        if self._temp_rect is None:
            self._temp_rect = pg.RectROI(
                pos=(x0, y0),
                size=(1, 1),
                pen=pg.mkPen(color='#2E8B57', width=2, style=Qt.DashLine),
                movable=False
            )
            self.image_view.getView().addItem(self._temp_rect, ignoreBounds=True)

        left = min(x0, x)
        right = max(x0, x)
        top = min(y0, y)
        bottom = max(y0, y)

        self._temp_rect.setPos((left, top))
        self._temp_rect.setSize((max(1.0, right - left), max(1.0, bottom - top)))

    def _on_image_clicked(self, ev):
        if self.image_view is None or not self.toggle_btn.isChecked():
            return
        if ev.button() != Qt.LeftButton:
            return

        plot_item = self.image_view.getView()
        vb = plot_item.vb

        if not vb.sceneBoundingRect().contains(ev.scenePos()):
            return

        p = vb.mapSceneToView(ev.scenePos())
        x, y = float(p.x()), float(p.y())

        image_data = self._get_displayed_image_data()
        if image_data is None:
            return

        h, w = image_data.shape[:2]
        x = max(0.0, min(x, w - 1))
        y = max(0.0, min(y, h - 1))

        if self._click_stage == 0:
            self._start_point = (x, y)
            self._start_marker.setData([x], [y])
            self._start_marker.setVisible(True)

            if self._temp_rect is not None:
                try:
                    self.image_view.getView().removeItem(self._temp_rect)
                except Exception:
                    pass
                self._temp_rect = None

            self._click_stage = 1
            return

        # 2e clic
        x0, y0 = self._start_point
        x1, y1 = x, y

        left = min(x0, x1)
        right = max(x0, x1)
        top = min(y0, y1)
        bottom = max(y0, y1)

        if self._temp_rect is not None:
            try:
                self.image_view.getView().removeItem(self._temp_rect)
            except Exception:
                pass
            self._temp_rect = None

        if self.rect_roi is not None and self.image_view is not None:
            try:
                self.image_view.getView().removeItem(self.rect_roi)
            except Exception:
                pass

        self.rect_roi = pg.RectROI(
            pos=(left, top),
            size=(max(1.0, right - left), max(1.0, bottom - top)),
            pen=pg.mkPen(color='#2E8B57', width=2),
            movable=True
        )

        try:
            self.rect_roi.addScaleHandle([1, 1], [0, 0])
        except Exception:
            pass

        try:
            self.rect_roi.addRotateHandle([0, 0], [0.5, 0.5])
        except Exception:
            pass

        self.rect_roi.sigRegionChanged.connect(self.update_histogram)

        try:
            self.image_view.getView().addItem(self.rect_roi, ignoreBounds=True)
            self._attached = True
            self.rect_roi.show()
        except Exception:
            self._attached = False

        self._reset_roi_creation_state()
        self.update_histogram()

    # ------------------------------------------------------------------
    # Histogram
    # ------------------------------------------------------------------
    def update_histogram(self):
        self._clear_histogram()

        if self.image_view is None:
            return

        # IMPORTANT:
        # Aucun calcul si ROI non activée.
        if not self.toggle_btn.isChecked():
            return

        if self.rect_roi is None or not self.rect_roi.isVisible():
            return

        vals = self._extract_pixel_values_in_roi()
        if vals is None or vals.size == 0:
            return

        # Si les données sont entières/discrètes, on fait un histogramme exact par valeur
        if np.allclose(vals, np.round(vals)):
            vals_int = vals.astype(np.int64)
            vmin = int(vals_int.min())
            vmax = int(vals_int.max())

            bins = np.arange(vmin, vmax + 2) - 0.5
            hist, edges = np.histogram(vals_int, bins=bins)
        else:
            vmin = float(vals.min())
            vmax = float(vals.max())

            if vmax <= vmin:
                return

            hist, edges = np.histogram(vals, bins=256, range=(vmin, vmax))

        self.hist_plot.plot(
            edges,
            hist,
            stepMode=True,
            fillLevel=0,
            pen=pg.mkPen(color='#FF7700', width=2)
        )

        if self.autoscale_checkbox.isChecked():
            self.reset_histogram_view()