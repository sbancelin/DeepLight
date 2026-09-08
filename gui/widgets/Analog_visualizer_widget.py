from PySide6.QtWidgets import (QVBoxLayout, QWidget, QLabel, QCheckBox, QHBoxLayout, QDialog, QFormLayout, QLineEdit, QPushButton, QSizePolicy, QDoubleSpinBox)
from PySide6.QtCore import Qt, Slot
from PySide6.QtGui import QIcon
import pyqtgraph as pg
import numpy as np

from ..resources import icon_path


class AnalogOutVisualizerWidget(QWidget):
    def reset_buffer(self):
        """Reset the monitoring buffer (new acquisition)."""
        self._t_full = np.zeros((0,), dtype=np.float64)
        self._x_full = np.zeros((0,), dtype=np.float64)
        self._y_full = np.zeros((0,), dtype=np.float64)

        self._show_full_y_duration = True

        if hasattr(self, "_curve_x"):
            self._curve_x.setData([], [])
        if hasattr(self, "_curve_y"):
            self._curve_y.setData([], [])
    
    def __init__(self, parent=None):
        super().__init__(parent)

        self.main_layout = QVBoxLayout(self)
        self.main_layout.setContentsMargins(2, 2, 2, 2)
        self.main_layout.setSpacing(4)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)

        # Définir le style sombre pour pyqtgraph
        pg.setConfigOptions(
            background='#2b2b2b',  # Fond noir
            foreground='w',  # Texte et lignes en blanc
            antialias=True
        )

        # Fenêtres temporelles affichées (ms)
        self._window_x_ms = 20.0
        self._window_y_ms = 3000.0

        self._run_total_ms = 0.0
        self._show_full_y_duration = True

        line_edit_style = """
            QLineEdit {
                background-color: #333;
                color: white;
                border: 1px solid #555;
                border-radius: 3px;
                padding: 2px;
                min-height: 20px;
            }
        """

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

        # Container principal
        container = QWidget()
        container.setMinimumWidth(0)
        container.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)

        layout = QVBoxLayout(container)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(4)

        # Layout horizontal pour les checkboxes Autoscale
        layout.addSpacing(4)
        Galvo_X_layout = QHBoxLayout()
        Galvo_X_layout.setContentsMargins(0, 0, 0, 0)
        Galvo_X_layout.setSpacing(6)

        # Titre pour le graphique du Galvo X
        self.galvo_x_label = QLabel("Galvo X")
        self.galvo_x_label.setStyleSheet("color: #ccc; font-weight: bold")
        self.galvo_x_label.setMinimumWidth(20)
        self.galvo_x_label.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        Galvo_X_layout.addWidget(self.galvo_x_label)
        Galvo_X_layout.addStretch(1)

        # Champ pour la fenêtre temporelle X
        self.window_x_edit = QLineEdit(str(self._window_x_ms))
        self.window_x_edit.setStyleSheet(line_edit_style)
        self.window_x_edit.setMinimumWidth(0)
        self.window_x_edit.setMaximumWidth(80)
        self.window_x_edit.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.window_x_edit.returnPressed.connect(self.apply_time_window_x)

        Galvo_X_layout.addWidget(QLabel("Window:"))
        Galvo_X_layout.addWidget(self.window_x_edit)
        Galvo_X_layout.addWidget(QLabel("ms"))
        Galvo_X_layout.addSpacing(6)

        # Bouton Monitor pour Galvo X
        self.monitor_x_btn = QPushButton()
        self.monitor_x_btn.setIcon(QIcon(icon_path("monitor.svg")))
        self.monitor_x_btn.setCheckable(True)
        self.monitor_x_btn.setChecked(False)
        self.monitor_x_btn.setFixedHeight(22)
        self.monitor_x_btn.setMinimumWidth(20)
        self.monitor_x_btn.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.monitor_x_btn.setStyleSheet(button_style)
        Galvo_X_layout.addWidget(self.monitor_x_btn)
        Galvo_X_layout.addSpacing(6)

        # Checkbox Autoscale pour Galvo X
        self.autoscale_x_checkbox = QCheckBox("Autoscale")
        self.autoscale_x_checkbox.setStyleSheet(checkbox_style)
        self.autoscale_x_checkbox.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.autoscale_x_checkbox.setChecked(False)
        self.autoscale_x_checkbox.stateChanged.connect(
            lambda: self.toggle_autoscale(self.galvo_x_plot, self.autoscale_x_checkbox)
        )
        Galvo_X_layout.addWidget(self.autoscale_x_checkbox)
        Galvo_X_layout.addSpacing(12)

        # Ajout du layout des contrôles au layout principal
        layout.addLayout(Galvo_X_layout)
        layout.addSpacing(6)

        # Graphique pour le Galvo X
        self.galvo_x_plot = pg.PlotWidget()
        self.configure_plot(self.galvo_x_plot)
        self.galvo_x_plot.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.galvo_x_plot.setMinimumHeight(80)
        self.galvo_x_plot.setLabel('left', 'Voltage', units='V', **{"color": "white", "font-size": "10pt"})
        self.galvo_x_plot.setLabel('bottom', 'Time', units='s', **{"color": "white", "font-size": "10pt"})
        self.galvo_x_plot.scene().sigMouseClicked.connect(lambda event: self.on_axis_double_click(event, self.galvo_x_plot))
        layout.addWidget(self.galvo_x_plot)
        layout.addSpacing(8)
        self._curve_x = self.galvo_x_plot.plot([], [], pen=pg.mkPen(color='#FF7700', width=2))
        self._curve_x.setDownsampling(auto=True, method='peak')
        self._curve_x.setClipToView(True)
        self._curve_x.setSkipFiniteCheck(True)

        # Layout horizontal pour les checkboxes Autoscale
        Galvo_Y_layout = QHBoxLayout()
        Galvo_Y_layout.setContentsMargins(0, 0, 0, 0)
        Galvo_Y_layout.setSpacing(6)

        # Titre pour le graphique du Galvo Y
        self.galvo_y_label = QLabel("Galvo Y")
        self.galvo_y_label.setStyleSheet("color: #ccc; font-weight: bold")
        self.galvo_y_label.setMinimumWidth(20)
        self.galvo_y_label.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        Galvo_Y_layout.addWidget(self.galvo_y_label)
        Galvo_Y_layout.addStretch(1)

        # Champ pour la fenêtre temporelle Y
        self.window_y_edit = QLineEdit(str(self._window_y_ms))
        self.window_y_edit.setStyleSheet(line_edit_style)
        self.window_y_edit.setMinimumWidth(0)
        self.window_y_edit.setMaximumWidth(75)
        self.window_y_edit.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.window_y_edit.returnPressed.connect(self.apply_time_window_y)

        Galvo_Y_layout.addWidget(QLabel("Window:"))
        Galvo_Y_layout.addWidget(self.window_y_edit)
        Galvo_Y_layout.addWidget(QLabel("ms"))
        Galvo_Y_layout.addSpacing(6)

        self.monitor_y_btn = QPushButton()
        self.monitor_y_btn.setIcon(QIcon(icon_path("monitor.svg")))
        self.monitor_y_btn.setCheckable(True)
        self.monitor_y_btn.setChecked(False)
        self.monitor_y_btn.setFixedHeight(22)
        self.monitor_y_btn.setMinimumWidth(0)
        self.monitor_y_btn.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.monitor_y_btn.setStyleSheet(button_style)
        Galvo_Y_layout.addWidget(self.monitor_y_btn)
        Galvo_Y_layout.addSpacing(6)

        # Checkbox Autoscale pour Galvo Y
        self.autoscale_y_checkbox = QCheckBox("Autoscale")
        self.autoscale_y_checkbox.setStyleSheet(checkbox_style)
        self.autoscale_y_checkbox.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.autoscale_y_checkbox.setChecked(False)
        self.autoscale_y_checkbox.stateChanged.connect(
            lambda: self.toggle_autoscale(self.galvo_y_plot, self.autoscale_y_checkbox)
        )
        Galvo_Y_layout.addWidget(self.autoscale_y_checkbox)
        Galvo_Y_layout.addSpacing(10)

        # Ajout du layout des contrôles au layout principal
        layout.addLayout(Galvo_Y_layout)
        layout.addSpacing(6)

        # Graphique pour le Galvo Y
        self.galvo_y_plot = pg.PlotWidget()
        self.configure_plot(self.galvo_y_plot)
        self.galvo_y_plot.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.galvo_y_plot.setMinimumHeight(80)
        self.galvo_y_plot.setLabel('left', 'Voltage', units='V', **{"color": "white", "font-size": "10pt"})
        self.galvo_y_plot.setLabel('bottom', 'Time', units='s', **{"color": "white", "font-size": "10pt"})
        self.galvo_y_plot.scene().sigMouseClicked.connect(lambda event: self.on_axis_double_click(event, self.galvo_y_plot))
        layout.addWidget(self.galvo_y_plot)
        self._curve_y = self.galvo_y_plot.plot([], [], pen=pg.mkPen(color='#ff7f0e', width=2))
        self._curve_y.setDownsampling(auto=True, method='peak')
        self._curve_y.setClipToView(True)
        self._curve_y.setSkipFiniteCheck(True)

        self._t_full = np.zeros((0,), dtype=np.float64)
        self._x_full = np.zeros((0,), dtype=np.float64)
        self._y_full = np.zeros((0,), dtype=np.float64)

        self.main_layout.addWidget(container)
        self.main_layout.addStretch()
    
    def set_run_total_ms(self, total_ms: float):
        try:
            self._run_total_ms = max(0.0, float(total_ms))
        except Exception:
            self._run_total_ms = 0.0
    
    @Slot()
    def apply_time_window_y(self):
        try:
            self._window_y_ms = float(self.window_y_edit.text())

            # l'utilisateur demande un zoom manuel sur Y
            self._show_full_y_duration = False

            if not self.autoscale_y_checkbox.isChecked():
                xmax_s = max(self._window_y_ms / 1000.0, 1e-6)
                self.galvo_y_plot.setXRange(0.0, xmax_s, padding=0)

        except ValueError:
            self.window_y_edit.setText(str(self._window_y_ms))
    
    def configure_plot(self, plot):
        plot.setBackground('#2b2b2b')
        plot.showGrid(x=True, y=True, alpha=0.3)

        plot.getAxis('left').setPen(pg.mkPen(color='w', width=1))
        plot.getAxis('bottom').setPen(pg.mkPen(color='w', width=1))

        plot.setYRange(-5, 5, padding=0)

        if plot is self.galvo_x_plot:
            plot.setXRange(0.0, self._window_x_ms / 1000.0, padding=0)
        elif plot is self.galvo_y_plot:
            plot.setXRange(0.0, self._window_y_ms / 1000.0, padding=0)

        # Ligne horizontale fixe à Y = 0
        zero_line = pg.InfiniteLine(
            pos=0,
            angle=0,
            pen=pg.mkPen('#3AB16F', width=1, style=Qt.DashLine)
        )

        plot.addItem(zero_line)

    def toggle_autoscale(self, plot, checkbox):
        """Enable or disable autoscaling for a given plot."""
        if checkbox.isChecked():
            plot.enableAutoRange()
            plot.autoRange()
        else:
            plot.disableAutoRange()

            # Pour Y, retour au mode "durée complète du run"
            if plot is self.galvo_y_plot:
                self._show_full_y_duration = True

                xmax_s = max(self._run_total_ms / 1000.0, 1e-6)
                self.galvo_y_plot.setXRange(0.0, xmax_s, padding=0)
                self.galvo_y_plot.setYRange(-5, 5, padding=0)

            # Pour X, on garde la logique fenêtre courte
            elif plot is self.galvo_x_plot:
                xmax_s = max(self._window_x_ms / 1000.0, 1e-6)
                self.galvo_x_plot.setXRange(0.0, xmax_s, padding=0)
                self.galvo_x_plot.setYRange(-5, 5, padding=0)

    def on_axis_double_click(self, event, plot):
        """Handle a double-click on the axes to open the scale editing dialog."""
        if event.double():
            pos = event.scenePos()
            if plot.sceneBoundingRect().contains(pos):
                axis = plot.getAxis('bottom') if pos.y() > plot.height() / 2 else plot.getAxis('left')
                self.show_axis_range_dialog(axis, plot)

    def show_axis_range_dialog(self, axis, plot):
        """Show a dialog for editing the axis scales."""
        dialog = QDialog(self)
        dialog.setWindowTitle("Set Axis Range")
        dialog.setStyleSheet("background-color: #333; color: white;")

        layout = QFormLayout(dialog)

        # Champs pour min et max
        min_spinbox = QDoubleSpinBox()
        min_spinbox.setStyleSheet("background-color: #555; color: white;")
        min_spinbox.setRange(-1e6, 1e6)
        min_spinbox.setValue(axis.range[0])

        max_spinbox = QDoubleSpinBox()
        max_spinbox.setStyleSheet("background-color: #555; color: white;")
        max_spinbox.setRange(-1e6, 1e6)
        max_spinbox.setValue(axis.range[1])

        layout.addRow("Min:", min_spinbox)
        layout.addRow("Max:", max_spinbox)

        # Boutons OK et Annuler
        button_layout = QHBoxLayout()
        ok_button = QPushButton("OK")
        ok_button.setStyleSheet("background-color: #444; color: white;")
        ok_button.clicked.connect(dialog.accept)
        button_layout.addWidget(ok_button)

        cancel_button = QPushButton("Cancel")
        cancel_button.setStyleSheet("background-color: #444; color: white;")
        cancel_button.clicked.connect(dialog.reject)
        button_layout.addWidget(cancel_button)

        layout.addRow(button_layout)

        if dialog.exec() == QDialog.DialogCode.Accepted:
            new_min = min_spinbox.value()
            new_max = max_spinbox.value()
            if axis == plot.getAxis('bottom'):
                plot.setXRange(new_min, new_max)
            else:
                plot.setYRange(new_min, new_max)

    def show_full_waveforms(self, t_ms, x_v, y_v):
        return

    @Slot()
    def apply_time_window_x(self):
        try:
            self._window_x_ms = float(self.window_x_edit.text())

            if not self.autoscale_x_checkbox.isChecked():
                xmax_s = max(self._window_x_ms / 1000.0, 1e-6)
                self.galvo_x_plot.setXRange(0.0, xmax_s, padding=0)

        except ValueError:
            self.window_x_edit.setText(str(self._window_x_ms))
    
    def update_plots(self, t_ms, x_v, y_v):
        if t_ms is None:
            return

        if not self.monitor_x_btn.isChecked() and not self.monitor_y_btn.isChecked():
            return

        t_ms = np.asarray(t_ms, dtype=np.float64)
        x_v = np.asarray(x_v, dtype=np.float64)
        y_v = np.asarray(y_v, dtype=np.float64)

        if t_ms.size == 0:
            return

        # nouvelle acquisition si le temps repart en arrière
        if self._t_full.size > 0 and float(t_ms[0]) < float(self._t_full[-1]):
            self.reset_buffer()

        self._t_full = np.concatenate([self._t_full, t_ms])
        self._x_full = np.concatenate([self._x_full, x_v])
        self._y_full = np.concatenate([self._y_full, y_v])

        # ---------- Galvo X ----------
        if self.monitor_x_btn.isChecked():
            t_x_s = self._t_full / 1000.0
            self._curve_x.setData(t_x_s, self._x_full)

            if self.autoscale_x_checkbox.isChecked():
                self.galvo_x_plot.enableAutoRange()
                self.galvo_x_plot.autoRange()
            else:
                tmax_s = float(t_x_s[-1]) if t_x_s.size else (self._window_x_ms / 1000.0)
                xmin_s = max(0.0, tmax_s - self._window_x_ms / 1000.0)
                self.galvo_x_plot.setXRange(xmin_s, tmax_s, padding=0)
                self.galvo_x_plot.setYRange(-5, 5, padding=0)

        # ---------- Galvo Y ----------
        if self.monitor_y_btn.isChecked():
            t_y_s = self._t_full / 1000.0
            self._curve_y.setData(t_y_s, self._y_full)

            if self.autoscale_y_checkbox.isChecked():
                self.galvo_y_plot.enableAutoRange()
                self.galvo_y_plot.autoRange()
            else:
                if self._show_full_y_duration:
                    xmax_s = max(self._run_total_ms / 1000.0, 1e-6)
                    self.galvo_y_plot.setXRange(0.0, xmax_s, padding=0)
                else:
                    tmax_s = float(t_y_s[-1]) if t_y_s.size else (self._window_y_ms / 1000.0)
                    xmin_s = max(0.0, tmax_s - self._window_y_ms / 1000.0)
                    self.galvo_y_plot.setXRange(xmin_s, tmax_s, padding=0)

                self.galvo_y_plot.setYRange(-5, 5, padding=0)