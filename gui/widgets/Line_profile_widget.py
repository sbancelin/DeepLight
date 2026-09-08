from PySide6.QtWidgets import (
    QVBoxLayout, QWidget, QLabel, QHBoxLayout, QPushButton, QComboBox,
    QSizePolicy, QDialog, QDialogButtonBox, QFormLayout, QDoubleSpinBox
)
from PySide6.QtCore import Qt, QLocale
from PySide6.QtGui import QIcon
import pyqtgraph as pg
import numpy as np
from ._pg_common import DoubleClickAxis, DoubleClickViewBox
from ..resources import icon_path


class LineProfileWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)

        self.main_layout = QVBoxLayout(self)
        self.main_layout.setContentsMargins(2, 2, 2, 2)
        self.main_layout.setSpacing(4)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)

        self.im_widgets = {}
        self.image_view = None
        self._scene = None

        self.line_segment = None
        self.temp_image_line = None
        self._start_point = None
        self._current_temp_end_point = None
        self._waiting_for_end_point = False

        self.scale_um_per_pixel = 1.0
        self._profile_measure_start = None
        self.temp_graph_line = None

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

        self._start_marker = pg.ScatterPlotItem(
            size=12,
            pen=pg.mkPen("#2E8B57"),
            brush=pg.mkBrush(60, 179, 113, 160),
        )
        self._start_marker.setVisible(False)

        container = QWidget()
        container.setMinimumWidth(0)
        container.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)

        layout = QVBoxLayout(container)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(4)

        top_layout = QHBoxLayout()
        top_layout.setContentsMargins(0, 0, 0, 0)
        top_layout.setSpacing(6)

        self.toggle_btn = QPushButton()
        self.toggle_btn.setIcon(QIcon(icon_path("line.svg")))
        self.toggle_btn.setCheckable(True)
        self.toggle_btn.setFixedHeight(22)
        self.toggle_btn.setStyleSheet(button_style)
        self.toggle_btn.setMinimumWidth(40)
        self.toggle_btn.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.toggle_btn.toggled.connect(self.toggle_line_visibility)
        top_layout.addWidget(self.toggle_btn, stretch=0)

        self.channel_combo = QComboBox()
        self.channel_combo.setMinimumWidth(90)
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

        layout.addLayout(top_layout)

        self.profile_plot = pg.PlotWidget(
            viewBox=DoubleClickViewBox(self.reset_profile_view),
            axisItems={
                'left': DoubleClickAxis('left', self._on_axis_double_clicked),
                'bottom': DoubleClickAxis('bottom', self._on_axis_double_clicked),
            }
        )
        self.profile_plot.setBackground("#2b2b2b")
        self.profile_plot.showGrid(x=True, y=True, alpha=0.3)
        self.profile_plot.setLabel("left", "Intensity", color="w")
        self.profile_plot.setLabel("bottom", "Position", units="µm", color="w")

        for axis_name in ("left", "bottom"):
            axis = self.profile_plot.getAxis(axis_name)
            axis.setTextPen(pg.mkPen("w"))
            axis.setPen(pg.mkPen("w"))

        self.profile_plot.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.profile_plot.setMinimumHeight(80)
        layout.addWidget(self.profile_plot)

        self.distance_label = QLabel("Length: 0.00 µm")
        self.distance_label.setStyleSheet("color: white; padding-top: 2px;")
        self.distance_label.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        layout.addWidget(self.distance_label)

        self.main_layout.addWidget(container)
        self.main_layout.addStretch()

        self.profile_plot.scene().sigMouseClicked.connect(self._on_profile_plot_clicked)
        self.profile_plot.scene().sigMouseMoved.connect(self._on_profile_plot_moved)

    def set_im_widgets(self, im_widgets: dict):
        self.im_widgets = dict(im_widgets) if im_widgets else {}

        self.channel_combo.blockSignals(True)
        self.channel_combo.clear()
        for ch in sorted(self.im_widgets.keys(), key=str):
            self.channel_combo.addItem(str(ch))
        self.channel_combo.blockSignals(False)

        if self.channel_combo.count() > 0:
            self.channel_combo.setCurrentIndex(0)
            self.on_channel_changed(self.channel_combo.currentText())
        else:
            self.set_image_view(None)

    def _on_axis_double_clicked(self, orientation: str):
        if self.profile_plot is None:
            return

        plot_item = self.profile_plot.getPlotItem()
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
            self.profile_plot.enableAutoRange(axis='x', enable=False)
            self.profile_plot.setXRange(new_min, new_max, padding=0)
        elif orientation == 'left':
            self.profile_plot.enableAutoRange(axis='y', enable=False)
            self.profile_plot.setYRange(new_min, new_max, padding=0)

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

    def reset_profile_view(self):
        plot_item = self.profile_plot.getPlotItem()
        plot_item.enableAutoRange(axis='x', enable=True)
        plot_item.enableAutoRange(axis='y', enable=True)
        plot_item.vb.autoRange()
    
    def _on_profile_plot_clicked(self, ev):
        if ev.button() != Qt.LeftButton:
            return

        vb = self.profile_plot.getViewBox()
        if not vb.sceneBoundingRect().contains(ev.scenePos()):
            return

        pos = vb.mapSceneToView(ev.scenePos())
        x, y = float(pos.x()), float(pos.y())

        if self._profile_measure_start is None:
            self._profile_measure_start = (x, y)

            if self.temp_graph_line is not None:
                try:
                    self.profile_plot.removeItem(self.temp_graph_line)
                except Exception:
                    pass
                self.temp_graph_line = None
            return

        x0, y0 = self._profile_measure_start

        if self.temp_graph_line is not None:
            try:
                self.profile_plot.removeItem(self.temp_graph_line)
            except Exception:
                pass

        self.temp_graph_line = pg.PlotCurveItem(
            [x0, x],
            [y0, y],
            pen=pg.mkPen(color="#2E8B57", width=2),
        )
        self.profile_plot.addItem(self.temp_graph_line)

        distance = np.hypot(x - x0, y - y0)
        self.distance_label.setText(f"Length: {distance:.2f} µm")

        self._profile_measure_start = None
    
    def _on_profile_plot_moved(self, pos):
        if self._profile_measure_start is None:
            return

        vb = self.profile_plot.getViewBox()
        if not vb.sceneBoundingRect().contains(pos):
            return

        mouse_pos = vb.mapSceneToView(pos)
        x, y = float(mouse_pos.x()), float(mouse_pos.y())
        x0, y0 = self._profile_measure_start

        if self.temp_graph_line is not None:
            try:
                self.profile_plot.removeItem(self.temp_graph_line)
            except Exception:
                pass

        self.temp_graph_line = pg.PlotCurveItem(
            [x0, x],
            [y0, y],
            pen=pg.mkPen(color="#2E8B57", width=2, style=Qt.DashLine),
        )
        self.profile_plot.addItem(self.temp_graph_line)
    
    def on_channel_changed(self, channel_name: str):
        self.set_image_view(self.im_widgets.get(channel_name))

    def set_image_view(self, image_view):
        self._disconnect_scene()
        self._remove_items_from_current_view()

        self.image_view = image_view

        if self.image_view is not None:
            view = self.image_view.getView()
            self._scene = view.scene()
            self._scene.sigMouseClicked.connect(self._on_image_clicked)
            self._scene.sigMouseMoved.connect(self._on_image_moved)
            view.addItem(self._start_marker, ignoreBounds=True)

            if self.line_segment is not None:
                view.addItem(self.line_segment, ignoreBounds=True)

        self._apply_visibility()
        self.update_profile()

    def _disconnect_scene(self):
        if self._scene is None:
            return
        try:
            self._scene.sigMouseClicked.disconnect(self._on_image_clicked)
        except Exception:
            pass
        try:
            self._scene.sigMouseMoved.disconnect(self._on_image_moved)
        except Exception:
            pass
        self._scene = None

    def _remove_items_from_current_view(self):
        if self.image_view is None:
            return

        view = self.image_view.getView()

        for item in (self.line_segment, self.temp_image_line, self._start_marker):
            if item is None:
                continue
            try:
                view.removeItem(item)
            except Exception:
                pass

        self.temp_image_line = None

    def toggle_line_visibility(self, checked: bool):
        self._apply_visibility()
        self.update_profile()

    def _apply_visibility(self):
        if self.line_segment is not None:
            self.line_segment.setVisible(self.toggle_btn.isChecked())

        if not self.toggle_btn.isChecked():
            self._reset_drawing_state()

    def _reset_drawing_state(self):
        self._waiting_for_end_point = False
        self._start_point = None
        self._current_temp_end_point = None
        self._start_marker.setVisible(False)

        if self.temp_image_line is not None and self.image_view is not None:
            try:
                self.image_view.getView().removeItem(self.temp_image_line)
            except Exception:
                pass
            self.temp_image_line = None

    def _on_image_clicked(self, ev):
        if self.image_view is None or not self.toggle_btn.isChecked():
            return
        if ev.button() != Qt.LeftButton:
            return

        view = self.image_view.getView()
        vb = view.vb

        if not vb.sceneBoundingRect().contains(ev.scenePos()):
            return

        p = vb.mapSceneToView(ev.scenePos())
        image_data = self._get_displayed_image_data()

        if image_data is None or not hasattr(image_data, "shape"):
            return

        try:
            img_item = self.image_view.getImageItem()
            local = img_item.mapFromParent(p)
            px = np.clip(float(local.x()), 0, image_data.shape[1] - 1)
            py = np.clip(float(local.y()), 0, image_data.shape[0] - 1)
            pt = img_item.mapToParent(pg.Point(px, py))
            x, y = float(pt.x()), float(pt.y())
        except Exception:
            x = np.clip(float(p.x()), 0, image_data.shape[1] - 1)
            y = np.clip(float(p.y()), 0, image_data.shape[0] - 1)

        if not self._waiting_for_end_point:
            # Si une ancienne ligne existe déjà, on l'efface immédiatement
            if self.line_segment is not None:
                try:
                    view.removeItem(self.line_segment)
                except Exception:
                    pass
                self.line_segment = None

            if self.temp_image_line is not None:
                try:
                    view.removeItem(self.temp_image_line)
                except Exception:
                    pass
                self.temp_image_line = None

            self._start_point = (x, y)
            self._current_temp_end_point = (x, y)
            self._waiting_for_end_point = True
            self._start_marker.setData([x], [y])
            self._start_marker.setVisible(True)
            self.profile_plot.clear()
            self.distance_label.setText("Length: 0.00 µm")
            self.update_profile()
            return

        x0, y0 = self._start_point
        self._create_line_segment(x0, y0, x, y)
        self._reset_drawing_state()
        self.update_profile()

    def _on_image_moved(self, pos):
        if (
            self.image_view is None
            or not self.toggle_btn.isChecked()
            or not self._waiting_for_end_point
            or self._start_point is None
        ):
            return

        view = self.image_view.getView()
        vb = view.vb

        if not vb.sceneBoundingRect().contains(pos):
            return

        p = vb.mapSceneToView(pos)

        image_data = self._get_displayed_image_data()
        if image_data is None or image_data.ndim < 2:
            return

        try:
            img_item = self.image_view.getImageItem()
            local = img_item.mapFromParent(p)
            px = np.clip(float(local.x()), 0, image_data.shape[1] - 1)
            py = np.clip(float(local.y()), 0, image_data.shape[0] - 1)
            pt = img_item.mapToParent(pg.Point(px, py))
            x, y = float(pt.x()), float(pt.y())
        except Exception:
            x = np.clip(float(p.x()), 0, image_data.shape[1] - 1)
            y = np.clip(float(p.y()), 0, image_data.shape[0] - 1)
        x0, y0 = self._start_point
        self._current_temp_end_point = (x, y)

        if self.temp_image_line is not None:
            try:
                view.removeItem(self.temp_image_line)
            except Exception:
                pass

        self.temp_image_line = pg.LineSegmentROI(
            [[x0, y0], [x, y]],
            movable=False,
            pen=pg.mkPen(color="#2E8B57", width=2, style=Qt.DashLine),
        )

        try:
            view.addItem(self.temp_image_line, ignoreBounds=True)
        except Exception:
            pass

        self.update_profile()

    def _create_line_segment(self, x0, y0, x1, y1):
        if self.image_view is None:
            return

        view = self.image_view.getView()

        if self.line_segment is not None:
            try:
                view.removeItem(self.line_segment)
            except Exception:
                pass

        if self.temp_image_line is not None:
            try:
                view.removeItem(self.temp_image_line)
            except Exception:
                pass
            self.temp_image_line = None

        self.line_segment = pg.LineSegmentROI(
            [[x0, y0], [x1, y1]],
            movable=True,
            pen=pg.mkPen(color="#2E8B57", width=2),
        )
        for h in self.line_segment.getHandles():
            h.hide()
        self.line_segment.sigRegionChanged.connect(self.update_profile)

        try:
            view.addItem(self.line_segment, ignoreBounds=True)
        except Exception:
            pass

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
    
    def _get_active_line_points(self):
        # Ligne finale
        if self.line_segment is not None and self.toggle_btn.isChecked():
            try:
                if self.line_segment.isVisible():
                    pts = self.line_segment.listPoints()
                    if pts is not None and len(pts) >= 2:
                        return pts[0], pts[1]
            except Exception:
                pass

        # Ligne temporaire entre 1er et 2e clic
        if (
            self.toggle_btn.isChecked()
            and self._waiting_for_end_point
            and self._start_point is not None
            and self._current_temp_end_point is not None
        ):
            x0, y0 = self._start_point
            x1, y1 = self._current_temp_end_point
            return pg.Point(x0, y0), pg.Point(x1, y1)

        return None, None
    
    def update_profile(self):
        self.profile_plot.clear()
        self.distance_label.setText("Length: 0.00 µm")

        if self.image_view is None or not self.toggle_btn.isChecked():
            return

        image_data = self._get_displayed_image_data()
        if image_data is None or not hasattr(image_data, "shape"):
            return

        start_pos, end_pos = self._get_active_line_points()
        if start_pos is None or end_pos is None:
            return

        x0, y0 = start_pos.x(), start_pos.y()
        x1, y1 = end_pos.x(), end_pos.y()

        length_um = np.hypot(x1 - x0, y1 - y0)

        profile = self.extract_line_profile(image_data, start_pos, end_pos)
        if profile.size == 0:
            return

        x_axis = np.linspace(0, length_um, profile.size)
        self.profile_plot.plot(x_axis, profile, pen=pg.mkPen(color="#FF7700", width=2))
        self.profile_plot.setXRange(0, max(length_um, 1e-9), padding=0)
        self.distance_label.setText(f"Length: {length_um:.2f} µm")

    def extract_line_profile(self, image_data, start_pos, end_pos):
        img_item = self.image_view.getImageItem()

        # Convertir les coordonnées DATA vers les indices pixel de l'image,
        # en tenant compte de setPos() ET setTransform() (scale).
        local0 = img_item.mapFromParent(start_pos)
        local1 = img_item.mapFromParent(end_pos)
        x0, y0 = float(local0.x()), float(local0.y())
        x1, y1 = float(local1.x()), float(local1.y())

        num_points = max(2, int(np.ceil(np.hypot(x1 - x0, y1 - y0))) + 1)

        x_values = np.rint(np.linspace(x0, x1, num_points)).astype(int)
        y_values = np.rint(np.linspace(y0, y1, num_points)).astype(int)

        x_values = np.clip(x_values, 0, image_data.shape[1] - 1)
        y_values = np.clip(y_values, 0, image_data.shape[0] - 1)

        return image_data[y_values, x_values]