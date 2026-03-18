from PySide6.QtWidgets import (
    QVBoxLayout, QWidget, QLabel, QComboBox, QCheckBox, QDialog,
    QFormLayout, QDoubleSpinBox, QPushButton, QHBoxLayout, QLineEdit, QSizePolicy
)
from PySide6.QtCore import Qt, Slot
from PySide6.QtGui import QIcon
import pyqtgraph as pg
import numpy as np

class StepperVisualizerWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)

        self.main_layout = QVBoxLayout(self)
        self.main_layout.setContentsMargins(2, 2, 2, 2)
        self.main_layout.setSpacing(4)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)

        pg.setConfigOptions(
            background='#2b2b2b',
            foreground='w',
            antialias=True
        )

        self.buffer_ms = 8000.0
        self._default_hold_ms = 10.0
        self._xy_frame_hold_ms = None   # injecté depuis le ScanManager si dispo
        self._last_sched_ms = 0.0
        self._show_full_duration = True
        self._run_total_ms = 0.0

        combo_line_edit_style = """
            QComboBox, QLineEdit {
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

        container = QWidget()
        container.setMinimumWidth(0)
        container.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)

        layout = QVBoxLayout(container)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(4)

        selector_layout = QHBoxLayout()
        selector_layout.setContentsMargins(0, 0, 0, 0)
        selector_layout.setSpacing(6)

        self.stepper_selector = QComboBox()
        self.stepper_selector.addItems(["Z-Vcoil", "Polarization", "X-Stage", "Y-Stage", "None"])
        self.stepper_selector.setStyleSheet(combo_line_edit_style)
        self.stepper_selector.currentTextChanged.connect(self.update_plot_labels)
        self.stepper_selector.currentTextChanged.connect(self._refresh_from_selection)
        self.stepper_selector.setMinimumWidth(0)
        self.stepper_selector.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        selector_layout.addWidget(self.stepper_selector)

        selector_layout.addWidget(QLabel("Window:"))

        self.window_edit = QLineEdit(str(self.buffer_ms))
        self.window_edit.setStyleSheet(combo_line_edit_style)
        self.window_edit.setMinimumWidth(0)
        self.window_edit.setMaximumWidth(80)
        self.window_edit.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.window_edit.returnPressed.connect(self.apply_time_window)
        selector_layout.addWidget(self.window_edit)

        selector_layout.addWidget(QLabel("ms"))
        selector_layout.addStretch(1)

        self.monitor_btn = QPushButton()
        self.monitor_btn.setIcon(QIcon("gui/Icons/monitor.svg"))
        self.monitor_btn.setCheckable(True)
        self.monitor_btn.setChecked(False)
        self.monitor_btn.setFixedHeight(22)
        self.monitor_btn.setMinimumWidth(0)
        self.monitor_btn.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.monitor_btn.setStyleSheet(button_style)
        self.monitor_btn.toggled.connect(self._on_monitor_toggled)
        selector_layout.addWidget(self.monitor_btn)
        selector_layout.addSpacing(12)

        self.autoscale_checkbox = QCheckBox("Autoscale")
        self.autoscale_checkbox.setStyleSheet(checkbox_style)
        self.autoscale_checkbox.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.autoscale_checkbox.setChecked(False)
        self.autoscale_checkbox.stateChanged.connect(self.toggle_autoscale)
        selector_layout.addWidget(self.autoscale_checkbox)
        selector_layout.addSpacing(8)

        layout.addLayout(selector_layout)
        layout.addSpacing(4)

        self.stepper_plot = pg.PlotWidget()
        self.configure_plot(self.stepper_plot)
        self.stepper_plot.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.stepper_plot.setMinimumHeight(80)
        layout.addWidget(self.stepper_plot)

        # axis_name -> ([t_ms], [pos_um])
        self._stream_points = {}
        self._full_points = {}
        self._full_reasons = {}   # axis_name -> [reason0, reason1, ...]
        self._position_stream_points = {}  # axis -> ([t_ms], [rel_pos])

        self._curve = self.stepper_plot.plot([], [], pen=pg.mkPen(color='#FF7700', width=2))
        self._curve.setDownsampling(auto=True, method='peak')
        self._curve.setClipToView(True)
        self._curve.setSkipFiniteCheck(True)

        self._is_running = False

        self.stepper_plot.scene().sigMouseClicked.connect(self.on_axis_double_click)

        self.main_layout.addWidget(container)
        self.main_layout.addStretch()

    # Config / helpers
    @Slot()
    def apply_time_window(self):
        try:
            value = float(self.window_edit.text())
            if value <= 0:
                raise ValueError

            self.buffer_ms = value
            self.window_edit.setText(str(self.buffer_ms))

            # l'utilisateur demande un zoom manuel
            self._show_full_duration = False

        except ValueError:
            self.window_edit.setText(str(self.buffer_ms))
            return

        axis_name = self.stepper_selector.currentText()
        self._refresh_full_curve(axis_name)
        
    def _on_monitor_toggled(self, checked: bool):
        axis = self.stepper_selector.currentText()

        if not checked:
            self.reset_buffer()
            return

        self._refresh_from_selection(axis)

    def set_xy_frame_hold_ms(self, hold_ms: float):
        """
        Permet au ScanManager/MainWindow d'indiquer combien de temps
        une position stepper doit visuellement rester constante.
        """
        try:
            hold_ms = float(hold_ms)
            if hold_ms > 0:
                self._xy_frame_hold_ms = hold_ms
        except Exception:
            pass

    def _effective_hold_ms(self) -> float:
        if self._xy_frame_hold_ms is not None and self._xy_frame_hold_ms > 0:
            return float(self._xy_frame_hold_ms)
        return float(self._default_hold_ms)

    def _merged_points_for_axis(self, axis_name: str):
        """
        Fusionne intelligemment :
        - la courbe complète planifiée (_full_points)
        - les positions réelles (_position_stream_points)

        On conserve toujours le point initial de _full_points si disponible.
        """
        full_buf = self._full_points.get(axis_name)
        pos_buf = self._position_stream_points.get(axis_name)

        if full_buf is None and pos_buf is None:
            return [], []

        full_t, full_y = full_buf if full_buf is not None else ([], [])
        pos_t, pos_y = pos_buf if pos_buf is not None else ([], [])

        merged_t = []
        merged_y = []

        # 1) garder explicitement le point initial de la planification
        if full_t and full_y:
            merged_t.append(float(full_t[0]))
            merged_y.append(float(full_y[0]))

        # 2) ajouter les positions réelles si elles existent
        for t, y in zip(pos_t, pos_y):
            t = float(t)
            y = float(y)

            if merged_t and abs(merged_t[-1] - t) < 1e-9 and abs(merged_y[-1] - y) < 1e-12:
                continue

            merged_t.append(t)
            merged_y.append(y)

        # 3) si on n'a toujours rien, fallback sur le plan complet
        if not merged_t and full_t and full_y:
            for t, y in zip(full_t, full_y):
                merged_t.append(float(t))
                merged_y.append(float(y))

        return merged_t, merged_y
    
    def _stairs_from_points(self, tt, yy, final_hold_ms=None):
        """
        Convertit une suite de points (t, y) en vraie courbe en paliers.

        Règle :
        - le point i est tenu jusqu'au point i+1
        - le dernier point est prolongé de hold_ms
        """
        if not tt or not yy:
            return [], []

        tt = [float(v) for v in tt]
        yy = [float(v) for v in yy]

        if len(tt) != len(yy):
            n = min(len(tt), len(yy))
            tt = tt[:n]
            yy = yy[:n]

        if not tt:
            return [], []

        hold_ms = self._effective_hold_ms() if final_hold_ms is None else float(final_hold_ms)

        if len(tt) == 1:
            t0 = tt[0]
            y0 = yy[0]
            return [t0, t0 + hold_ms], [y0, y0]

        x_plot = [tt[0]]
        y_plot = [yy[0]]

        for i in range(1, len(tt)):
            x_plot.append(tt[i])
            y_plot.append(yy[i - 1])
            x_plot.append(tt[i])
            y_plot.append(yy[i])

        x_plot.append(tt[-1] + hold_ms)
        y_plot.append(yy[-1])

        return x_plot, y_plot

    # Slots scan manager
    def set_initial_position(self, axis_name: str, pos_value: float):
        if not self.monitor_btn.isChecked():
            return

        buf = self._full_points.get(axis_name)
        if buf is None:
            buf = ([], [])
            self._full_points[axis_name] = buf

        tt, yy = buf

        if not tt:
            tt.append(0.0)
            yy.append(float(pos_value))

        if self.stepper_selector.currentText() == axis_name:
            if self._is_running and self.monitor_btn.isChecked():
                self._refresh_stream_curve(axis_name)
            else:
                self._refresh_full_curve(axis_name)
    
    def on_stepper_move_requested(
        self,
        axis_name: str,
        target_um: float,
        vel_um_s: float,
        acc_um_s2: float,
        jerk_um_s3: float,
        t_sched_ms: float,
        reason: str
    ):
        if not self.monitor_btn.isChecked():
            return

        self._last_sched_ms = float(t_sched_ms)

        buf = self._full_points.get(axis_name)
        if buf is None:
            buf = ([], [])
            self._full_points[axis_name] = buf

        tt, yy = buf

        reasons = self._full_reasons.get(axis_name)
        if reasons is None:
            reasons = []
            self._full_reasons[axis_name] = reasons

        # sécurité : s'il n'y a pas encore de point initial,
        # on injecte un état initial à t=0
        if not tt:
            tt.append(0.0)
            yy.append(float(target_um))
            reasons.append("initial")

        tt.append(float(t_sched_ms))
        yy.append(float(target_um))
        reasons.append(str(reason))

        if self.stepper_selector.currentText() == axis_name:
            self._refresh_from_selection(axis_name)

    def on_stepper_waveform_ready(self, axis_name: str, x_data, y_data):
        """
        Si un vrai waveform complet est fourni, on le montre directement hors run.
        """
        if self._is_running:
            return

        if self.stepper_selector.currentText() == axis_name:
            self.update_plot(x_data, y_data)

    def on_stepper_waveform_chunk(self, axis_name: str, t_ms, pos):
        """
        Reçoit un chunk de waveform stepper et l'ajoute au buffer live.
        On ne stocke que si Monitor est actif.
        """
        if not self.monitor_btn.isChecked():
            return

        try:
            t_list = [float(v) for v in t_ms]
            p_list = [float(v) for v in pos]
        except Exception:
            return

        if not t_list:
            return

        buf = self._stream_points.get(axis_name)
        if buf is None:
            buf = ([], [])
            self._stream_points[axis_name] = buf

        tt, yy = buf
        tt.extend(t_list)
        yy.extend(p_list)

        # buffer glissant
        tmax = tt[-1]
        tmin = tmax - float(self.buffer_ms)
        while tt and tt[0] < tmin:
            tt.pop(0)
            yy.pop(0)

        if self.stepper_selector.currentText() == axis_name and self._is_running:
            self._refresh_stream_curve(axis_name)

    # Refresh plot
    def _refresh_stream_curve(self, axis_name: str):
        """
        Rafraîchit la courbe live pour l'axe sélectionné. On conserve toujours le point initial du plan complet si disponible,
        puis on affiche les positions réelles reçues.
        """
        tt, yy = self._merged_points_for_axis(axis_name)

        if not tt:
            # fallback ultime
            buf = self._stream_points.get(axis_name)
            if buf is not None:
                tt, yy = buf

        if not tt:
            self._curve.setData([], [])
            return

        x_plot, y_plot = self._stairs_from_points(tt, yy)
        self.update_plot(x_plot, y_plot)

    def _refresh_full_curve(self, axis_name: str):
        buf = self._full_points.get(axis_name)

        if buf is None:
            self._curve.setData([], [])
            self.update_plot([], [])
            return

        tt, yy = buf
        reasons = self._full_reasons.get(axis_name, [])

        final_hold_ms = None

        # Si le dernier point correspond à un retour à la base,
        # on ne lui donne pas une frame complète artificielle.
        if reasons:
            last_reason = str(reasons[-1])
            if "return_to_base" in last_reason:
                final_hold_ms = 0.0

        x_plot, y_plot = self._stairs_from_points(tt, yy, final_hold_ms=final_hold_ms)
        self.update_plot(x_plot, y_plot)

    @Slot(str, float)
    def on_positioner_relative_position_changed(self, axis_name: str, rel_pos: float):
        """
        Reçoit la position relative réelle et l'aligne sur la dernière
        abscisse planifiée connue, pour garder un affichage temporel cohérent.
        """
        if not self.monitor_btn.isChecked():
            return

        now_ms = float(self._last_sched_ms)

        buf = self._position_stream_points.get(axis_name)
        if buf is None:
            buf = ([], [])
            self._position_stream_points[axis_name] = buf

        tt, yy = buf

        # éviter de dupliquer inutilement le même point
        if tt and yy:
            if abs(float(tt[-1]) - now_ms) < 1e-9 and abs(float(yy[-1]) - float(rel_pos)) < 1e-12:
                return

        tt.append(now_ms)
        yy.append(float(rel_pos))

        if self.stepper_selector.currentText() == axis_name:
            self._refresh_from_selection(axis_name)
    
    def update_plot(self, x_data, y_data):
        x_data = np.asarray(x_data, dtype=np.float64) / 1000.0
        y_data = np.asarray(y_data, dtype=np.float64)

        self._curve.setData(x_data, y_data)

        if self.autoscale_checkbox.isChecked():
            self.stepper_plot.enableAutoRange()
            self.stepper_plot.autoRange()
            return

        if self._show_full_duration:
            xmax = max(
                float(self._run_total_ms) / 1000.0,
                float(x_data[-1]) if len(x_data) > 0 else 0.0,
                1e-6
            )
            self.stepper_plot.setXRange(0.0, xmax, padding=0)
            return

        if len(x_data) > 0:
            xmax = float(x_data[-1])
            xmin = max(0.0, xmax - float(self.buffer_ms) / 1000.0)
            self.stepper_plot.setXRange(xmin, xmax, padding=0)
        else:
            self.stepper_plot.setXRange(0.0, float(self.buffer_ms) / 1000.0, padding=0)

    # Plot config
    def configure_plot(self, plot):
        plot.setBackground('#2b2b2b')
        plot.showGrid(x=True, y=True, alpha=0.3)
        plot.getAxis('left').setPen(pg.mkPen(color='w', width=1))
        plot.getAxis('bottom').setPen(pg.mkPen(color='w', width=1))
        styles = {"color": "white", "font-size": "10pt"}
        plot.setLabel('bottom', 'Time', units='s', **styles)
        plot.setLabel('left', 'Pos', units='µm', **styles)
        plot.setYRange(-5, 5, padding=0)

        zero_line = pg.InfiniteLine(
            pos=0,
            angle=0,
            pen=pg.mkPen('#2E8B57', width=1, style=Qt.DashLine)
        )
        plot.addItem(zero_line)

    def update_plot_labels(self, text):
        styles = {"color": "white", "font-size": "10pt"}
        if text in ("Z-Vcoil", "X-Stage", "Y-Stage", "None"):
            self.stepper_plot.setLabel('left', 'Pos', units='µm', **styles)
        elif text == "Polarization":
            self.stepper_plot.setLabel('left', 'Angle', units='°', **styles)

    def toggle_autoscale(self, state):
        if state == Qt.CheckState.Checked.value:
            self.stepper_plot.enableAutoRange()
            self.stepper_plot.autoRange()
        else:
            self.stepper_plot.disableAutoRange()
            # quand on quitte autoscale, on revient à la vue complète par défaut
            self._show_full_duration = True
            axis = self.stepper_selector.currentText()
            self._refresh_full_curve(axis)

    def _refresh_from_selection(self, axis_name: str):
        # Pour les steppers, on veut toujours voir la séquence cumulée
        # des commandes déjà émises.
        self._refresh_full_curve(axis_name)

    def reset_buffer(self):
        self._stream_points.clear()
        self._full_points.clear()
        self._full_reasons.clear()
        self._position_stream_points.clear()
        self._last_sched_ms = 0.0
        self._show_full_duration = True
        self._run_total_ms = 0.0
        self._curve.setData([], [])

    def set_run_total_ms(self, total_ms: float):
        try:
            self._run_total_ms = max(0.0, float(total_ms))
        except Exception:
            self._run_total_ms = 0.0

        if self._show_full_duration:
            axis = self.stepper_selector.currentText()
            self._refresh_from_selection(axis)
    
    def set_running(self, running: bool):
        self._is_running = bool(running)
        axis = self.stepper_selector.currentText()
        self._refresh_from_selection(axis)

    def show_full_selected(self):
        axis = self.stepper_selector.currentText()
        self._refresh_full_curve(axis)

    # Axis range dialog
    def on_axis_double_click(self, event):
        if event.double():
            pos = event.scenePos()
            if self.stepper_plot.sceneBoundingRect().contains(pos):
                axis = self.stepper_plot.getAxis('bottom') if pos.y() > self.stepper_plot.height() / 2 else self.stepper_plot.getAxis('left')
                self.show_axis_range_dialog(axis)

    def show_axis_range_dialog(self, axis):
        dialog = QDialog(self)
        dialog.setWindowTitle("Set Axis Range")
        dialog.setStyleSheet("background-color: #333; color: white;")

        layout = QFormLayout(dialog)

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
            if axis == self.stepper_plot.getAxis('bottom'):
                self.stepper_plot.setXRange(new_min, new_max, padding=0)
            else:
                self.stepper_plot.setYRange(new_min, new_max, padding=0)