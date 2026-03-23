# DeepLight/gui/widgets/Spectro_Widget.py

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


def ask_levels_min_max(parent=None, title="Levels", lo0=0.0, hi0=255.0):
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
    return lo, hi


class SpectroWidget(QWidget):
    """
    Widget spectro avec deux sections visibles en même temps :
    - Brillouin : bouton activable + contrôles caméra + image caméra
    - Raman     : bouton activable + contrôles Raman + graphe du spectre
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("SpectroWidget")

        # =========================
        # Etat Brillouin
        # =========================
        self.brillouin_enabled = True
        self.brillouin_image = np.zeros((512, 512), dtype=np.uint8)
        self.brillouin_autoscale_enabled = True
        self.brillouin_lock_enabled = True
        self.brillouin_grid_enabled = True

        # =========================
        # Etat Raman
        # =========================
        self.raman_enabled = True
        self.raman_x = np.linspace(500.0, 900.0, 1024)
        self.raman_y = np.zeros_like(self.raman_x, dtype=float)
        self.raman_autoscale_enabled = True
        self.raman_grid_enabled = True

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(6, 6, 6, 6)
        main_layout.setSpacing(8)

        # ==========================================================
        # SECTION BRILLOUIN
        # ==========================================================
        self.brillouin_section = QWidget()
        brillouin_section_layout = QVBoxLayout(self.brillouin_section)
        brillouin_section_layout.setContentsMargins(0, 0, 0, 0)
        brillouin_section_layout.setSpacing(6)

        # ---- Ligne titre / activation
        brillouin_header = QHBoxLayout()
        brillouin_header.setContentsMargins(0, 0, 0, 0)
        brillouin_header.setSpacing(8)

        self.button_brillouin = QPushButton("Brillouin")
        self.button_brillouin.setCheckable(True)
        self.button_brillouin.setChecked(True)
        brillouin_header.addWidget(self.button_brillouin)

        brillouin_header.addStretch(1)
        brillouin_section_layout.addLayout(brillouin_header)

        # ---- Barre de contrôle type Camera
        brillouin_control_bar = QHBoxLayout()
        brillouin_control_bar.setContentsMargins(0, 0, 0, 0)
        brillouin_control_bar.setSpacing(8)

        brillouin_control_bar.addWidget(QLabel("Exposure (ms)"))
        self.spin_brillouin_exposure_ms = QDoubleSpinBox()
        self.spin_brillouin_exposure_ms.setDecimals(3)
        self.spin_brillouin_exposure_ms.setRange(0.001, 1_000_000.0)
        self.spin_brillouin_exposure_ms.setValue(10.0)
        self.spin_brillouin_exposure_ms.setSingleStep(1.0)
        brillouin_control_bar.addWidget(self.spin_brillouin_exposure_ms)

        brillouin_control_bar.addWidget(QLabel("FPS"))
        self.spin_brillouin_fps = QDoubleSpinBox()
        self.spin_brillouin_fps.setDecimals(3)
        self.spin_brillouin_fps.setRange(0.001, 10_000.0)
        self.spin_brillouin_fps.setValue(10.0)
        self.spin_brillouin_fps.setSingleStep(1.0)
        brillouin_control_bar.addWidget(self.spin_brillouin_fps)

        brillouin_control_bar.addWidget(QLabel("Gain"))
        self.spin_brillouin_gain = QDoubleSpinBox()
        self.spin_brillouin_gain.setDecimals(3)
        self.spin_brillouin_gain.setRange(0.0, 1000.0)
        self.spin_brillouin_gain.setValue(0.0)
        self.spin_brillouin_gain.setSingleStep(1.0)
        brillouin_control_bar.addWidget(self.spin_brillouin_gain)

        brillouin_control_bar.addWidget(QLabel("Pixel format"))
        self.combo_brillouin_pixel_format = QComboBox()
        self.combo_brillouin_pixel_format.addItems(["Mono8", "Mono12", "Mono16"])
        brillouin_control_bar.addWidget(self.combo_brillouin_pixel_format)

        self.cb_brillouin_auto_exposure = QCheckBox("Auto Exp")
        self.cb_brillouin_auto_exposure.setStyleSheet(_CHECKBOX_STYLE)
        brillouin_control_bar.addWidget(self.cb_brillouin_auto_exposure)

        self.cb_brillouin_auto_gain = QCheckBox("Auto Gain")
        self.cb_brillouin_auto_gain.setStyleSheet(_CHECKBOX_STYLE)
        brillouin_control_bar.addWidget(self.cb_brillouin_auto_gain)

        self.button_brillouin_snap = QPushButton("Snap")
        brillouin_control_bar.addWidget(self.button_brillouin_snap)

        self.button_brillouin_live = QPushButton("Start Live")
        brillouin_control_bar.addWidget(self.button_brillouin_live)

        self.button_brillouin_stop = QPushButton("Stop")
        brillouin_control_bar.addWidget(self.button_brillouin_stop)

        self.label_brillouin_status = QLabel("Idle")
        self.label_brillouin_status.setMinimumWidth(160)
        brillouin_control_bar.addWidget(self.label_brillouin_status)

        brillouin_control_bar.addStretch(1)
        brillouin_section_layout.addLayout(brillouin_control_bar)

        # ---- Vue image
        self.brillouin_plot_item = pg.PlotItem()
        self.brillouin_plot_item.setLabel("left", "y (px)")
        self.brillouin_plot_item.setLabel("bottom", "x (px)")

        self.brillouin_image_view = pg.ImageView(parent=self.brillouin_section, view=self.brillouin_plot_item)
        self.brillouin_image_view.setPredefinedGradient("inferno")
        self.brillouin_image_view.setImage(
            self.brillouin_image,
            autoLevels=False,
            levels=(0, 255),
            autoRange=False,
            autoHistogramRange=False
        )
        self.brillouin_image_view.getView().setAspectLocked(True)
        self.brillouin_image_view.getView().showGrid(True, True)
        self.brillouin_image_view.getView().autoRange()

        brillouin_section_layout.addWidget(self.brillouin_image_view, stretch=1)

        # ---- Footer Brillouin
        brillouin_footer = QWidget()
        brillouin_footer_layout = QHBoxLayout(brillouin_footer)
        brillouin_footer_layout.setContentsMargins(0, 0, 0, 0)
        brillouin_footer_layout.setSpacing(8)

        self.label_brillouin_pixel_status = QLabel("x: -, y: -, I: -")
        self.label_brillouin_pixel_status.setStyleSheet("color: #aaa; padding: 2px;")
        self.label_brillouin_pixel_status.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.label_brillouin_pixel_status.setMinimumWidth(260)
        self.label_brillouin_pixel_status.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        brillouin_footer_layout.addWidget(self.label_brillouin_pixel_status, stretch=1)

        self.cb_brillouin_autoscale = QCheckBox("Autoscale")
        self.cb_brillouin_autoscale.setChecked(True)
        self.cb_brillouin_autoscale.setStyleSheet(_CHECKBOX_STYLE)
        brillouin_footer_layout.addWidget(self.cb_brillouin_autoscale)

        self.cb_brillouin_lock = QCheckBox("Lock")
        self.cb_brillouin_lock.setChecked(True)
        self.cb_brillouin_lock.setStyleSheet(_CHECKBOX_STYLE)
        brillouin_footer_layout.addWidget(self.cb_brillouin_lock)

        self.cb_brillouin_grid = QCheckBox("Grid")
        self.cb_brillouin_grid.setChecked(True)
        self.cb_brillouin_grid.setStyleSheet(_CHECKBOX_STYLE)
        brillouin_footer_layout.addWidget(self.cb_brillouin_grid)

        self.button_brillouin_set_levels = QPushButton("Set Levels")
        self.button_brillouin_set_levels.setFixedHeight(22)
        brillouin_footer_layout.addWidget(self.button_brillouin_set_levels)

        self.button_brillouin_reset_levels = QPushButton("Reset Levels")
        self.button_brillouin_reset_levels.setFixedHeight(22)
        brillouin_footer_layout.addWidget(self.button_brillouin_reset_levels)

        brillouin_section_layout.addWidget(brillouin_footer)

        main_layout.addWidget(self.brillouin_section, stretch=1)

        # ==========================================================
        # SECTION RAMAN
        # ==========================================================
        self.raman_section = QWidget()
        raman_section_layout = QVBoxLayout(self.raman_section)
        raman_section_layout.setContentsMargins(0, 0, 0, 0)
        raman_section_layout.setSpacing(6)

        # ---- Ligne titre / activation
        raman_header = QHBoxLayout()
        raman_header.setContentsMargins(0, 0, 0, 0)
        raman_header.setSpacing(8)

        self.button_raman = QPushButton("Raman")
        self.button_raman.setCheckable(True)
        self.button_raman.setChecked(True)
        raman_header.addWidget(self.button_raman)

        raman_header.addStretch(1)
        raman_section_layout.addLayout(raman_header)

        # ---- Barre de contrôle Raman
        raman_control_bar = QHBoxLayout()
        raman_control_bar.setContentsMargins(0, 0, 0, 0)
        raman_control_bar.setSpacing(8)

        raman_control_bar.addWidget(QLabel("Exposure (ms)"))
        self.spin_raman_exposure_ms = QDoubleSpinBox()
        self.spin_raman_exposure_ms.setDecimals(3)
        self.spin_raman_exposure_ms.setRange(0.001, 1_000_000.0)
        self.spin_raman_exposure_ms.setValue(100.0)
        self.spin_raman_exposure_ms.setSingleStep(1.0)
        raman_control_bar.addWidget(self.spin_raman_exposure_ms)

        raman_control_bar.addWidget(QLabel("Averages"))
        self.spin_raman_averages = QDoubleSpinBox()
        self.spin_raman_averages.setDecimals(0)
        self.spin_raman_averages.setRange(1, 100000)
        self.spin_raman_averages.setValue(1)
        self.spin_raman_averages.setSingleStep(1)
        raman_control_bar.addWidget(self.spin_raman_averages)

        raman_control_bar.addWidget(QLabel("Center (nm)"))
        self.spin_raman_center_nm = QDoubleSpinBox()
        self.spin_raman_center_nm.setDecimals(3)
        self.spin_raman_center_nm.setRange(0.0, 100000.0)
        self.spin_raman_center_nm.setValue(700.0)
        self.spin_raman_center_nm.setSingleStep(1.0)
        raman_control_bar.addWidget(self.spin_raman_center_nm)

        raman_control_bar.addWidget(QLabel("Span (nm)"))
        self.spin_raman_span_nm = QDoubleSpinBox()
        self.spin_raman_span_nm.setDecimals(3)
        self.spin_raman_span_nm.setRange(0.001, 100000.0)
        self.spin_raman_span_nm.setValue(100.0)
        self.spin_raman_span_nm.setSingleStep(1.0)
        raman_control_bar.addWidget(self.spin_raman_span_nm)

        self.button_raman_snap = QPushButton("Acquire")
        raman_control_bar.addWidget(self.button_raman_snap)

        self.button_raman_live = QPushButton("Start Live")
        raman_control_bar.addWidget(self.button_raman_live)

        self.button_raman_stop = QPushButton("Stop")
        raman_control_bar.addWidget(self.button_raman_stop)

        self.label_raman_status = QLabel("Idle")
        self.label_raman_status.setMinimumWidth(160)
        raman_control_bar.addWidget(self.label_raman_status)

        raman_control_bar.addStretch(1)
        raman_section_layout.addLayout(raman_control_bar)

        # ---- Graphe Raman
        self.raman_plot_widget = pg.PlotWidget()
        self.raman_plot_item = self.raman_plot_widget.getPlotItem()
        self.raman_plot_item.setLabel("left", "Intensity (a.u.)")
        self.raman_plot_item.setLabel("bottom", "Wavelength (nm)")
        self.raman_plot_item.showGrid(True, True)
        self.raman_plot_item.enableAutoRange()

        self.raman_curve = self.raman_plot_item.plot(self.raman_x, self.raman_y)

        self.raman_plot_item.setXRange(float(self.raman_x[0]), float(self.raman_x[-1]), padding=0)
        self.raman_plot_item.setYRange(0.0, 1.0, padding=0)

        self.raman_placeholder_text = pg.TextItem(
            text="en cours de construction",
            color=(140, 140, 140),
            anchor=(0.5, 0.5)
        )
        self.raman_plot_item.addItem(self.raman_placeholder_text)

        raman_section_layout.addWidget(self.raman_plot_widget, stretch=1)

        # ---- Footer Raman
        raman_footer = QWidget()
        raman_footer_layout = QHBoxLayout(raman_footer)
        raman_footer_layout.setContentsMargins(0, 0, 0, 0)
        raman_footer_layout.setSpacing(8)

        self.label_raman_point_status = QLabel("λ: -, I: -")
        self.label_raman_point_status.setStyleSheet("color: #aaa; padding: 2px;")
        self.label_raman_point_status.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.label_raman_point_status.setMinimumWidth(260)
        self.label_raman_point_status.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        raman_footer_layout.addWidget(self.label_raman_point_status, stretch=1)

        self.cb_raman_autoscale = QCheckBox("Autoscale")
        self.cb_raman_autoscale.setChecked(True)
        self.cb_raman_autoscale.setStyleSheet(_CHECKBOX_STYLE)
        raman_footer_layout.addWidget(self.cb_raman_autoscale)

        self.cb_raman_grid = QCheckBox("Grid")
        self.cb_raman_grid.setChecked(True)
        self.cb_raman_grid.setStyleSheet(_CHECKBOX_STYLE)
        raman_footer_layout.addWidget(self.cb_raman_grid)

        self.button_raman_set_levels = QPushButton("Set Levels")
        self.button_raman_set_levels.setFixedHeight(22)
        raman_footer_layout.addWidget(self.button_raman_set_levels)

        self.button_raman_reset_levels = QPushButton("Reset Levels")
        self.button_raman_reset_levels.setFixedHeight(22)
        raman_footer_layout.addWidget(self.button_raman_reset_levels)

        raman_section_layout.addWidget(raman_footer)

        main_layout.addWidget(self.raman_section, stretch=1)

        # ==========================================================
        # Connexions
        # ==========================================================
        self.button_brillouin.clicked.connect(self._on_brillouin_toggled)
        self.button_raman.clicked.connect(self._on_raman_toggled)

        self.cb_brillouin_auto_exposure.toggled.connect(self._on_brillouin_auto_exposure_toggled)
        self.cb_brillouin_auto_gain.toggled.connect(self._on_brillouin_auto_gain_toggled)
        self.cb_brillouin_autoscale.toggled.connect(self._on_brillouin_autoscale_toggled)
        self.cb_brillouin_lock.toggled.connect(self._on_brillouin_lock_toggled)
        self.cb_brillouin_grid.toggled.connect(self._on_brillouin_grid_toggled)
        self.button_brillouin_set_levels.clicked.connect(self._on_brillouin_set_levels_clicked)
        self.button_brillouin_reset_levels.clicked.connect(self._on_brillouin_reset_levels_clicked)

        self.cb_raman_autoscale.toggled.connect(self._on_raman_autoscale_toggled)
        self.cb_raman_grid.toggled.connect(self._on_raman_grid_toggled)
        self.button_raman_set_levels.clicked.connect(self._on_raman_set_levels_clicked)
        self.button_raman_reset_levels.clicked.connect(self._on_raman_reset_levels_clicked)

        try:
            self.brillouin_image_view.getView().scene().sigMouseMoved.connect(self._on_mouse_moved_brillouin)
        except Exception:
            pass

        try:
            self.raman_plot_widget.scene().sigMouseMoved.connect(self._on_mouse_moved_raman)
            self.raman_plot_item.vb.sigRangeChanged.connect(self._center_raman_placeholder)
        except Exception:
            pass

        self._update_toggle_button_style(self.button_brillouin)
        self._update_toggle_button_style(self.button_raman)
        self._apply_brillouin_levels(0.0, 255.0)
        self._center_raman_placeholder()

    # ==========================================================
    # API PUBLIQUE
    # ==========================================================
    def get_brillouin_parameters(self):
        return {
            "enabled": self.button_brillouin.isChecked(),
            "exposure_ms": self.spin_brillouin_exposure_ms.value(),
            "fps": self.spin_brillouin_fps.value(),
            "gain": self.spin_brillouin_gain.value(),
            "pixel_format": self.combo_brillouin_pixel_format.currentText(),
            "auto_exposure": self.cb_brillouin_auto_exposure.isChecked(),
            "auto_gain": self.cb_brillouin_auto_gain.isChecked(),
        }

    def get_raman_parameters(self):
        return {
            "enabled": self.button_raman.isChecked(),
            "exposure_ms": self.spin_raman_exposure_ms.value(),
            "averages": int(self.spin_raman_averages.value()),
            "center_nm": self.spin_raman_center_nm.value(),
            "span_nm": self.spin_raman_span_nm.value(),
        }

    def set_brillouin_status(self, text):
        self.label_brillouin_status.setText(str(text))

    def set_raman_status(self, text):
        self.label_raman_status.setText(str(text))

    def set_brillouin_live_button_state(self, live_running: bool):
        self.button_brillouin_live.setText("Stop Live" if live_running else "Start Live")

    def set_raman_live_button_state(self, live_running: bool):
        self.button_raman_live.setText("Stop Live" if live_running else "Start Live")

    def set_brillouin_pixel_format_list(self, values):
        current = self.combo_brillouin_pixel_format.currentText()
        self.combo_brillouin_pixel_format.blockSignals(True)
        self.combo_brillouin_pixel_format.clear()
        for v in values:
            self.combo_brillouin_pixel_format.addItem(str(v))
        idx = self.combo_brillouin_pixel_format.findText(current)
        if idx >= 0:
            self.combo_brillouin_pixel_format.setCurrentIndex(idx)
        self.combo_brillouin_pixel_format.blockSignals(False)

    def set_brillouin_enabled(self, enabled: bool):
        self.button_brillouin.setChecked(bool(enabled))
        self._on_brillouin_toggled(bool(enabled))

    def set_raman_enabled(self, enabled: bool):
        self.button_raman.setChecked(bool(enabled))
        self._on_raman_toggled(bool(enabled))

    def set_brillouin_image(self, img, width_um=None, height_um=None):
        self.brillouin_image = np.asarray(img, dtype=np.float32)

        self.brillouin_image_view.setImage(
            self.brillouin_image,
            autoLevels=self.brillouin_autoscale_enabled,
            autoRange=False,
            autoHistogramRange=False
        )

        img_item = self.brillouin_image_view.getImageItem()

        if width_um is not None and height_um is not None:
            h, w = self.brillouin_image.shape[:2]
            sx = float(width_um) / float(w) if w > 0 else 1.0
            sy = float(height_um) / float(h) if h > 0 else 1.0
            img_item.setTransform(QTransform.fromScale(sx, sy))
            img_item.setPos(0, 0)
            self.brillouin_plot_item.setLabel("left", "y (um)")
            self.brillouin_plot_item.setLabel("bottom", "x (um)")
        else:
            img_item.setTransform(QTransform())
            img_item.setPos(0, 0)
            self.brillouin_plot_item.setLabel("left", "y (px)")
            self.brillouin_plot_item.setLabel("bottom", "x (px)")

        self.brillouin_image_view.getView().setAspectLocked(self.brillouin_lock_enabled)

        if self.brillouin_autoscale_enabled:
            lo, hi = self._get_brillouin_image_minmax()
            self._apply_brillouin_levels(lo, hi)

        self.brillouin_image_view.getView().showGrid(
            self.brillouin_grid_enabled,
            self.brillouin_grid_enabled
        )

    def set_raman_spectrum(self, wavelengths_nm, intensities, show_placeholder=False):
        x = np.asarray(wavelengths_nm, dtype=float)
        y = np.asarray(intensities, dtype=float)

        if x.ndim != 1 or y.ndim != 1 or x.size != y.size or x.size == 0:
            return

        self.raman_x = x
        self.raman_y = y
        self.raman_curve.setData(self.raman_x, self.raman_y)
        self.raman_placeholder_text.setVisible(bool(show_placeholder))

        if self.raman_autoscale_enabled:
            self.raman_plot_item.enableAutoRange()

        self._center_raman_placeholder()

    def clear_raman_spectrum(self, show_placeholder=True):
        self.raman_x = np.linspace(500.0, 900.0, 1024)
        self.raman_y = np.zeros_like(self.raman_x, dtype=float)
        self.raman_curve.setData(self.raman_x, self.raman_y)
        self.raman_placeholder_text.setVisible(bool(show_placeholder))

        self.raman_plot_item.setXRange(float(self.raman_x[0]), float(self.raman_x[-1]), padding=0)
        self.raman_plot_item.setYRange(0.0, 1.0, padding=0)
        
        self._center_raman_placeholder()

    # ==========================================================
    # Helpers
    # ==========================================================
    def _update_toggle_button_style(self, button: QPushButton):
        active_style = """
        QPushButton {
            background-color: #2E8B57;
            color: white;
            border: 1px solid #555;
            border-radius: 4px;
            padding: 4px 10px;
        }
        QPushButton:hover {
            background-color: #3AB16F;
            border: 1px solid #777;
        }
        """
        inactive_style = """
        QPushButton {
            background-color: #333;
            color: white;
            border: 1px solid #555;
            border-radius: 4px;
            padding: 4px 10px;
        }
        QPushButton:hover {
            background-color: #444;
            border: 1px solid #777;
        }
        """
        button.setStyleSheet(active_style if button.isChecked() else inactive_style)

    def _set_section_enabled(self, section_widget: QWidget, enabled: bool, header_button: QPushButton):
        self._update_toggle_button_style(header_button)
        for child in section_widget.findChildren(QWidget):
            if child is header_button:
                continue
            child.setEnabled(enabled)

    def _get_brillouin_image_minmax(self):
        arr = np.asarray(self.brillouin_image)
        finite = arr[np.isfinite(arr)]
        if finite.size == 0:
            return 0.0, 255.0
        lo = float(np.min(finite))
        hi = float(np.max(finite))
        if hi <= lo:
            hi = lo + 1.0
        return lo, hi

    def _apply_brillouin_levels(self, lo, hi):
        if hi <= lo:
            hi = lo + 1.0

        self.brillouin_image_view.setLevels(lo, hi)

        hist = self.brillouin_image_view.ui.histogram

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

    def _get_raman_y_minmax(self):
        arr = np.asarray(self.raman_y, dtype=float)
        finite = arr[np.isfinite(arr)]
        if finite.size == 0:
            return 0.0, 1.0
        lo = float(np.min(finite))
        hi = float(np.max(finite))
        if hi <= lo:
            hi = lo + 1.0
        return lo, hi

    def _center_raman_placeholder(self, *args):
        try:
            x_range, y_range = self.raman_plot_item.vb.viewRange()
            x_mid = 0.5 * (x_range[0] + x_range[1])
            y_mid = 0.5 * (y_range[0] + y_range[1])
            self.raman_placeholder_text.setPos(x_mid, y_mid)
        except Exception:
            pass

    # ==========================================================
    # Slots Brillouin
    # ==========================================================
    def _on_brillouin_toggled(self, checked):
        self.brillouin_enabled = bool(checked)
        self._set_section_enabled(self.brillouin_section, bool(checked), self.button_brillouin)

    def _on_brillouin_auto_exposure_toggled(self, checked):
        self.spin_brillouin_exposure_ms.setEnabled(not bool(checked) and self.button_brillouin.isChecked())

    def _on_brillouin_auto_gain_toggled(self, checked):
        self.spin_brillouin_gain.setEnabled(not bool(checked) and self.button_brillouin.isChecked())

    def _on_brillouin_autoscale_toggled(self, checked):
        self.brillouin_autoscale_enabled = bool(checked)
        if checked:
            lo, hi = self._get_brillouin_image_minmax()
            self._apply_brillouin_levels(lo, hi)

    def _on_brillouin_lock_toggled(self, checked):
        self.brillouin_lock_enabled = bool(checked)
        self.brillouin_image_view.getView().setAspectLocked(bool(checked))

    def _on_brillouin_grid_toggled(self, checked):
        self.brillouin_grid_enabled = bool(checked)
        self.brillouin_image_view.getView().showGrid(bool(checked), bool(checked))

    def _on_brillouin_set_levels_clicked(self):
        self.brillouin_autoscale_enabled = False
        self.cb_brillouin_autoscale.blockSignals(True)
        self.cb_brillouin_autoscale.setChecked(False)
        self.cb_brillouin_autoscale.blockSignals(False)

        try:
            lo0, hi0 = self.brillouin_image_view.getLevels()
        except Exception:
            lo0, hi0 = self._get_brillouin_image_minmax()

        res = ask_levels_min_max(self, "Brillouin LUT", lo0, hi0)
        if res is None:
            return

        lo, hi = res
        if hi <= lo:
            QMessageBox.warning(self, "Invalid LUT values", "Max must be greater than Min.")
            return

        self._apply_brillouin_levels(float(lo), float(hi))

    def _on_brillouin_reset_levels_clicked(self):
        self.brillouin_autoscale_enabled = False
        self.cb_brillouin_autoscale.blockSignals(True)
        self.cb_brillouin_autoscale.setChecked(False)
        self.cb_brillouin_autoscale.blockSignals(False)

        self._apply_brillouin_levels(0.0, 255.0)

    def _on_mouse_moved_brillouin(self, pos):
        if self.brillouin_image is None:
            self.label_brillouin_pixel_status.setText("x: -, y: -, I: -")
            return

        try:
            vb = self.brillouin_image_view.getView().getViewBox()
            mouse_point = vb.mapSceneToView(pos)

            x = int(mouse_point.x())
            y = int(mouse_point.y())

            img = self.brillouin_image
            if 0 <= x < img.shape[1] and 0 <= y < img.shape[0]:
                intensity = img[y, x]
                self.label_brillouin_pixel_status.setText(
                    f"x: {x:4d}  y: {y:4d}  I: {float(intensity):.2f}"
                )
            else:
                self.label_brillouin_pixel_status.setText("x: -, y: -, I: -")
        except Exception:
            self.label_brillouin_pixel_status.setText("x: -, y: -, I: -")

    # ==========================================================
    # Slots Raman
    # ==========================================================
    def _on_raman_toggled(self, checked):
        self.raman_enabled = bool(checked)
        self._set_section_enabled(self.raman_section, bool(checked), self.button_raman)

    def _on_raman_autoscale_toggled(self, checked):
        self.raman_autoscale_enabled = bool(checked)
        if checked:
            self.raman_plot_item.enableAutoRange()

    def _on_raman_grid_toggled(self, checked):
        self.raman_grid_enabled = bool(checked)
        self.raman_plot_item.showGrid(bool(checked), bool(checked))

    def _on_raman_set_levels_clicked(self):
        self.raman_autoscale_enabled = False
        self.cb_raman_autoscale.blockSignals(True)
        self.cb_raman_autoscale.setChecked(False)
        self.cb_raman_autoscale.blockSignals(False)

        try:
            y_range = self.raman_plot_item.vb.viewRange()[1]
            lo0, hi0 = float(y_range[0]), float(y_range[1])
        except Exception:
            lo0, hi0 = self._get_raman_y_minmax()

        res = ask_levels_min_max(self, "Raman Y range", lo0, hi0)
        if res is None:
            return

        lo, hi = res
        if hi <= lo:
            QMessageBox.warning(self, "Invalid range", "Max must be greater than Min.")
            return

        self.raman_plot_item.setYRange(float(lo), float(hi), padding=0)
        self._center_raman_placeholder()

    def _on_raman_reset_levels_clicked(self):
        self.raman_autoscale_enabled = False
        self.cb_raman_autoscale.blockSignals(True)
        self.cb_raman_autoscale.setChecked(False)
        self.cb_raman_autoscale.blockSignals(False)

        lo, hi = self._get_raman_y_minmax()
        self.raman_plot_item.setYRange(lo, hi, padding=0)
        self._center_raman_placeholder()

    def _on_mouse_moved_raman(self, pos):
        try:
            mouse_point = self.raman_plot_item.vb.mapSceneToView(pos)
            x = float(mouse_point.x())

            if self.raman_x is None or self.raman_y is None or len(self.raman_x) == 0:
                self.label_raman_point_status.setText("λ: -, I: -")
                return

            idx = int(np.argmin(np.abs(self.raman_x - x)))
            if 0 <= idx < len(self.raman_x):
                lam = float(self.raman_x[idx])
                intensity = float(self.raman_y[idx])
                self.label_raman_point_status.setText(f"λ: {lam:8.3f} nm  I: {intensity:10.3f}")
            else:
                self.label_raman_point_status.setText("λ: -, I: -")
        except Exception:
            self.label_raman_point_status.setText("λ: -, I: -")