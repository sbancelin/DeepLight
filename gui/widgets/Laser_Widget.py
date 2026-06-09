from PySide6.QtWidgets import (QWidget, QVBoxLayout, QGroupBox, QPushButton, QGridLayout, QLabel, QDoubleSpinBox, 
                               QSlider, QSizePolicy, QDialog, QHBoxLayout, QLineEdit, QMessageBox, QTabWidget)
from PySide6.QtCore import Signal, Qt, QLocale
from PySide6.QtGui import QPalette, QColor

SMALL_BUTTON_STYLE = """
    QPushButton {
        background-color: #333;
        color: white;
        border: 1px solid #555;
        border-radius: 3px;
        min-height: 20px;
        padding: 0px;
    }
    QPushButton:hover {
        background-color: #444;
    }
"""

POWER_BUTTON_ON_STYLE = """
    QPushButton {
        border: none;
        background-color: transparent;
    }
    QPushButton:checked {
        background-color: #FF7700;
        border-radius: 6px;
    }
    QPushButton:checked:hover {
        background-color: #FF9200;
        border-radius: 6px;
    }
"""

# En OFF, le bouton reprend le style du bouton "Connect" (SMALL_BUTTON_STYLE)
POWER_BUTTON_OFF_STYLE = """
    QPushButton {
        background-color: #333;
        color: white;
        border: 1px solid #555;
        border-radius: 3px;
        padding: 0px;
    }
    QPushButton:disabled {
        qproperty-iconOpacity: 0.05;
    }
    QPushButton:hover {
        background-color: #444;
    }
"""

POWER_BUTTON_STYLE = """
    QPushButton {
        border: none;
        background-color: transparent;
    }
    QPushButton:checked {
        background-color: #FF7700;
        border-radius: 6px;
    }
    QPushButton:disabled {
        qproperty-iconOpacity: 0.05;
    }
    QPushButton:hover {
        background-color: #444;
        border-radius: 6px;
    }
    QPushButton:checked:hover {
        background-color: #FF9200;
        border-radius: 6px;
    }
"""

LASER_DEFAULTS = {
    "Mira 900": {
        "speed": 429410,
        "steps_per_degree": 1919.14,
        "offset_deg": 10,
    },
    "Tumecs": {
        "speed": 429410,
        "steps_per_degree": 1919.14,
        "offset_deg": 8.0,
    },
    "Cobolt 660": {
        # ELL14 via ELLC.
        # speed / steps_per_degree sont gardés pour compatibilité avec le dialogue existant,
        # mais ne sont pas utilisés par l'ELL14.
        "speed": 1,
        "steps_per_degree": 398.222222,
        "offset_deg": 24.3,
    },
}

LASERS_WITHOUT_POWER_BUTTON = {"Mira 900", "Tumecs", "Cobolt 660"}
POWER_UI_MIN = -10.0
POWER_UI_MAX = 110.0
POWER_UI_DECIMALS = 1
POWER_UI_STEP = 0.1
POWER_BUTTON_STEP = 1.0
POWER_SLIDER_SCALE = 10   # 0.1% resolution -> 0..1000
ALCOR_GDD_MIN_FS2 = -60020.0 #fs^2
ALCOR_GDD_MAX_FS2 = 0.0
ALCOR_GDD_STEP_FS2 = 100.0

ALCOR_REP_RATE_MIN_MHZ = 2.9
ALCOR_REP_RATE_MAX_MHZ = 81.0
ALCOR_REP_RATE_BASE_MHZ = 80.0

def _force_dot_locale_on_spinbox(spinbox):
    spinbox.setLocale(QLocale.c())

def setup_laser_settings_dialog(dialog):
    laser_widget = dialog.parent()
    laser_names = ["Mira 900", "Tumecs", "Cobolt 660"]

    for index, laser_name in enumerate(laser_names):
        title = QLabel(f"<b>{laser_name}</b>")
        dialog.add_widget(title)

        offset_layout = QHBoxLayout()
        offset_layout.addWidget(QLabel("Offset (°):"))
        offset_edit = QLineEdit()
        offset_edit.setObjectName(f"offset_deg_edit_{laser_name.replace(' ', '_')}")
        offset_layout.addWidget(offset_edit)
        dialog.add_layout(offset_layout)

        speed_layout = QHBoxLayout()
        speed_layout.addWidget(QLabel("Velocity:"))
        speed_edit = QLineEdit()
        speed_edit.setObjectName(f"speed_edit_{laser_name.replace(' ', '_')}")
        speed_layout.addWidget(speed_edit)
        dialog.add_layout(speed_layout)

        conv_layout = QHBoxLayout()
        conv_layout.addWidget(QLabel("Steps / degree:"))
        conv_edit = QLineEdit()
        conv_edit.setObjectName(f"steps_per_deg_edit_{laser_name.replace(' ', '_')}")
        conv_layout.addWidget(conv_edit)
        dialog.add_layout(conv_layout)

        if laser_widget is not None and laser_widget.settings_manager is not None:
            settings = laser_widget.settings_manager.get_laser_settings(laser_name)
        else:
            settings = {}

        defaults = LASER_DEFAULTS.get(laser_name, {})
        speed_edit.setText(str(settings.get("speed", defaults.get("speed", 429410))))
        conv_edit.setText(str(settings.get("steps_per_degree", defaults.get("steps_per_degree", 1919.14))))
        offset_edit.setText(str(settings.get("offset_deg", defaults.get("offset_deg", 0.0))))

        if index < len(laser_names) - 1:
            sep = QLabel()
            sep.setFrameShape(QLabel.HLine)
            sep.setFrameShadow(QLabel.Sunken)
            sep.setStyleSheet("color: #555; margin-top: 0px; margin-bottom: 0px;")
            dialog.add_widget(sep)

    def _read_float(le: QLineEdit, default: float) -> float:
        try:
            return float(le.text())
        except Exception:
            return float(default)

    def _read_int(le: QLineEdit, default: int) -> int:
        try:
            return int(float(le.text().replace(",", ".")))
        except Exception:
            return int(default)

    def on_dialog_accepted():
        if laser_widget is None:
            return

        for laser_name in laser_names:
            key = laser_name.replace(" ", "_")
            speed_edit = dialog.findChild(QLineEdit, f"speed_edit_{key}")
            conv_edit = dialog.findChild(QLineEdit, f"steps_per_deg_edit_{key}")
            offset_edit = dialog.findChild(QLineEdit, f"offset_deg_edit_{key}")

            if speed_edit is None or conv_edit is None or offset_edit is None:
                continue

            if laser_widget.settings_manager is not None:
                old = laser_widget.settings_manager.get_laser_settings(laser_name)
            else:
                old = {}

            defaults = LASER_DEFAULTS.get(laser_name, {})
            old_speed = int(old.get("speed", defaults.get("speed", 429410)))
            old_conv = float(old.get("steps_per_degree", defaults.get("steps_per_degree", 1919.14)))
            old_offset = float(old.get("offset_deg", defaults.get("offset_deg", 0.0)))

            speed = _read_int(speed_edit, old_speed)
            conv = _read_float(conv_edit, old_conv)
            offset = _read_float(offset_edit, old_offset)

            if speed <= 0:
                QMessageBox.warning(laser_widget, "Invalid laser setting", f"{laser_name}: speed must be > 0")
                speed_edit.setText(str(old_speed))
                continue

            if conv <= 0:
                QMessageBox.warning(laser_widget, "Invalid laser setting", f"{laser_name}: steps/degree must be > 0")
                conv_edit.setText(str(old_conv))
                continue

            if offset < 0.0 or offset > 360.0:
                QMessageBox.warning(laser_widget, "Invalid laser setting", f"{laser_name}: offset must be between 0 and 360 deg")
                offset_edit.setText(str(old_offset))
                continue
            
            laser_widget.settings_manager.update_laser_settings(
                laser_name,
                speed=speed,
                steps_per_degree=conv,
                offset_deg=offset,
            )

    dialog.accepted.connect(on_dialog_accepted)


class LaserWidget(QWidget):
    """Widget pour le contrôle des lasers."""
    laser_power_changed = Signal(str, float)
    laser_power_toggled = Signal(str, bool)

    # Alcor-specific controls
    laser_gdd_changed = Signal(str, float)          # fs^2
    laser_rep_rate_changed = Signal(str, float)  # kHz

    alcor_connect_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)

        self.settings_manager = None

        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)

        self.main_layout = QVBoxLayout(self)
        self.main_layout.setContentsMargins(6, 6, 6, 6)
        self.main_layout.setSpacing(6)
    
        self.laser_tabs = QTabWidget()
        self.laser_tabs.setMinimumWidth(0)
        self.laser_tabs.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self.laser_tabs.setStyleSheet("""
            QTabWidget::pane {
                border: 1px solid #444;
                background-color: #2a2a2a;
                border-radius: 4px;
            }
            QTabBar::tab {
                background: #333;
                color: white;
                border: 1px solid #555;
                padding: 4px 8px;
                margin-right: 1px;
            }
            QTabBar::tab:selected {
                background: #555;
                color: white;
            }
            QTabBar::tab:hover {
                background: #444;
            }
        """)

        # Ajout des contrôles pour chaque laser
        self.laser_controls = {}
        self._add_laser_control("Alcor 920")
        self._add_laser_control("Cobolt 660")
        self._add_laser_control("Mira 900")
        self._add_laser_control("Tumecs")

        self.main_layout.addWidget(self.laser_tabs)
        self.main_layout.addStretch()

    def set_settings_manager(self, manager):
        self.settings_manager = manager

        for laser_name, defaults in LASER_DEFAULTS.items():
            if not self.settings_manager.get_laser_settings(laser_name):
                self.settings_manager.update_laser_settings(laser_name, **defaults)
    
    def _add_laser_control(self, laser_name):
        """Ajoute un onglet de contrôle pour un laser spécifique."""
        tab = QWidget()
        tab.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        tab.setStyleSheet("""
            QWidget {
                background-color: #2a2a2a;
            }
            QLabel {
                color: white;
            }
        """)

        laser_layout = QGridLayout(tab)
        laser_layout.setHorizontalSpacing(4)
        laser_layout.setVerticalSpacing(4)
        laser_layout.setContentsMargins(6, 8, 6, 8)

        laser_layout.setColumnStretch(0, 0)  # label
        laser_layout.setColumnStretch(1, 0)  # spinbox
        laser_layout.setColumnStretch(2, 0)  # bouton -
        laser_layout.setColumnStretch(3, 1)  # slider / spacer
        laser_layout.setColumnStretch(4, 0)  # valeur / rep-rate
        laser_layout.setColumnStretch(5, 0)  # bouton +
        laser_layout.setColumnStretch(6, 0)  # ON/OFF éventuel

        laser_layout.setColumnMinimumWidth(0, 70)

        power_label = QLabel("Power")
        power_label.setStyleSheet("""
            QLabel {
                color: white;
                font-weight: bold;
                font-size: 13px;
            }
        """)
        power_label.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        power_label.setAlignment(Qt.AlignVCenter | Qt.AlignLeft)

        setpoint_spin = QDoubleSpinBox()
        setpoint_spin.setMinimumWidth(70)
        setpoint_spin.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        setpoint_spin.setDecimals(POWER_UI_DECIMALS)
        setpoint_spin.setSingleStep(POWER_UI_STEP)
        setpoint_spin.setRange(POWER_UI_MIN, POWER_UI_MAX)
        setpoint_spin.setValue(0.0)
        setpoint_spin.setSuffix(" %")
        _force_dot_locale_on_spinbox(setpoint_spin)

        spin_palette = setpoint_spin.palette()
        spin_palette.setColor(QPalette.Base, QColor("#333333"))
        spin_palette.setColor(QPalette.Text, QColor("white"))
        spin_palette.setColor(QPalette.Button, QColor("#333333"))
        spin_palette.setColor(QPalette.ButtonText, QColor("white"))
        spin_palette.setColor(QPalette.WindowText, QColor("white"))
        spin_palette.setColor(QPalette.Highlight, QColor("#2E8B57"))
        spin_palette.setColor(QPalette.HighlightedText, QColor("white"))

        setpoint_spin.setPalette(spin_palette)
        setpoint_spin.setAutoFillBackground(True)

        setpoint_slider = QSlider(Qt.Horizontal)
        setpoint_slider.setMinimumWidth(60)
        setpoint_slider.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        setpoint_slider.setRange(
            int(round(POWER_UI_MIN * POWER_SLIDER_SCALE)),
            int(round(POWER_UI_MAX * POWER_SLIDER_SCALE)),
        )
        setpoint_slider.setValue(0)
        setpoint_slider.setStyleSheet("""
            QSlider::groove:horizontal {
                border: 1px solid #444;
                height: 8px;
                background: #333;
                border-radius: 4px;
            }
            QSlider::handle:horizontal {
                background: #777;
                border: 1px solid #444;
                width: 16px;
                margin: -4px 0;
                border-radius: 8px;
            }
        """)

        plus_button = QPushButton("+")
        plus_button.setFixedSize(20, 20)
        plus_button.setStyleSheet(SMALL_BUTTON_STYLE)

        minus_button = QPushButton("-")
        minus_button.setFixedSize(20, 20)
        minus_button.setStyleSheet(SMALL_BUTTON_STYLE)

        plus_button.clicked.connect(
            lambda _, sp=setpoint_spin: sp.setValue(min(POWER_UI_MAX, sp.value() + POWER_BUTTON_STEP))
        )

        minus_button.clicked.connect(
            lambda _, sp=setpoint_spin: sp.setValue(max(POWER_UI_MIN, sp.value() - POWER_BUTTON_STEP))
        )

        show_power_button = laser_name not in LASERS_WITHOUT_POWER_BUTTON
        power_button = None

        if show_power_button:
            power_button = QPushButton("OFF")
            power_button.setCheckable(True)
            power_button.setChecked(False)
            power_button.setFixedSize(30, 25)
            power_button.setStyleSheet(POWER_BUTTON_OFF_STYLE)

        laser_layout.addWidget(power_label, 0, 0)
        laser_layout.addWidget(setpoint_spin, 0, 1)
        laser_layout.addWidget(minus_button, 0, 2)
        laser_layout.addWidget(setpoint_slider, 0, 3)
        laser_layout.addWidget(plus_button, 0, 4)

        if power_button is not None:
            laser_layout.addWidget(power_button, 0, 5)

        gdd_spin = None
        rep_rate_spin = None
        rep_rate_valid_label = None
        alcor_connect_btn = None
        alcor_status_label = None

        if laser_name == "Alcor 920":
            gdd_label = QLabel("GDD (fs²)")
            gdd_label.setStyleSheet("color: white; font-weight: bold;")

            gdd_spin = QDoubleSpinBox()
            gdd_spin.setMinimumWidth(110)
            gdd_spin.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
            gdd_spin.setDecimals(0)
            gdd_spin.setSingleStep(ALCOR_GDD_STEP_FS2)
            gdd_spin.setRange(ALCOR_GDD_MIN_FS2, ALCOR_GDD_MAX_FS2)
            gdd_spin.setValue(0.0)
            gdd_spin.setKeyboardTracking(False)
            gdd_spin.setPalette(spin_palette)
            gdd_spin.setAutoFillBackground(True)
            _force_dot_locale_on_spinbox(gdd_spin)

            rep_rate_label = QLabel(" Rep rate (MHz)")
            rep_rate_label.setStyleSheet("color: white; font-weight: bold;")

            rep_rate_spin = QDoubleSpinBox()
            rep_rate_spin.setMinimumWidth(95)
            rep_rate_spin.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
            rep_rate_spin.setDecimals(3)
            rep_rate_spin.setSingleStep(0.1)
            rep_rate_spin.setRange(ALCOR_REP_RATE_MIN_MHZ, ALCOR_REP_RATE_MAX_MHZ)
            rep_rate_spin.setValue(ALCOR_REP_RATE_BASE_MHZ)
            rep_rate_spin.setKeyboardTracking(False)
            rep_rate_spin.setPalette(spin_palette)
            rep_rate_spin.setAutoFillBackground(True)
            _force_dot_locale_on_spinbox(rep_rate_spin)

            rep_rate_valid_label = QLabel("N=1")
            rep_rate_valid_label.setStyleSheet("color: #bbbbbb;")
            rep_rate_valid_label.setMinimumWidth(50)

            # GDD et rep-rate sur la même ligne
            laser_layout.addWidget(gdd_label, 1, 0)
            laser_layout.addWidget(gdd_spin, 1, 1, 1, 2)
            laser_layout.addWidget(rep_rate_label, 1, 3)
            laser_layout.addWidget(rep_rate_spin, 1, 4)
            laser_layout.addWidget(rep_rate_valid_label, 1, 5, 1, 2)

            # Bouton de connexion Alcor + label de statut
            alcor_connect_btn = QPushButton("Connect")
            alcor_connect_btn.setStyleSheet(SMALL_BUTTON_STYLE)
            alcor_connect_btn.setFixedHeight(22)
            alcor_connect_btn.clicked.connect(self._on_alcor_connect_clicked)
            laser_layout.addWidget(alcor_connect_btn, 2, 0, 1, 2)

            alcor_status_label = QLabel("Not connected")
            alcor_status_label.setStyleSheet("color: #888; background: transparent; border: none;")
            laser_layout.addWidget(alcor_status_label, 2, 2, 1, 5)

        def _on_spin_changed(val, slider=setpoint_slider):
            slider_value = self._power_to_slider_value(val)
            if slider.value() != slider_value:
                slider.blockSignals(True)
                slider.setValue(slider_value)
                slider.blockSignals(False)

        def _on_slider_changed(slider_val, spin=setpoint_spin):
            power_val = self._slider_to_power_value(slider_val)
            if abs(spin.value() - power_val) > 1e-9:
                spin.blockSignals(True)
                spin.setValue(power_val)
                spin.blockSignals(False)

        setpoint_spin.valueChanged.connect(_on_spin_changed)
        setpoint_slider.valueChanged.connect(_on_slider_changed)

        if power_button is not None:
            power_button.toggled.connect(lambda state: self._update_power_button_style(power_button, state))
            power_button.toggled.connect(lambda state, name=laser_name: self.laser_power_toggled.emit(name, state))

        setpoint_spin.editingFinished.connect(
            lambda name=laser_name, sp=setpoint_spin: self.laser_power_changed.emit(name, float(sp.value()))
        )
        setpoint_slider.sliderReleased.connect(
            lambda name=laser_name, sl=setpoint_slider: self.laser_power_changed.emit(
                name, self._slider_to_power_value(sl.value())
            )
        )
        plus_button.clicked.connect(
            lambda _=False, name=laser_name, sp=setpoint_spin: self.laser_power_changed.emit(name, float(sp.value()))
        )
        minus_button.clicked.connect(
            lambda _=False, name=laser_name, sp=setpoint_spin: self.laser_power_changed.emit(name, float(sp.value()))
        )

        if gdd_spin is not None:
            gdd_spin.editingFinished.connect(
                lambda name=laser_name, sp=gdd_spin: self.laser_gdd_changed.emit(name, float(sp.value()))
            )

        if rep_rate_spin is not None and rep_rate_valid_label is not None:
            def _on_rep_rate_finished(sp=rep_rate_spin, label=rep_rate_valid_label, name=laser_name):
                freq_mhz = self._snap_alcor_rep_rate_mhz(float(sp.value()))
                n = self._alcor_rep_rate_divider_from_mhz(freq_mhz)
                freq_khz = freq_mhz * 1000.0

                if abs(float(sp.value()) - freq_mhz) > 1e-9:
                    sp.blockSignals(True)
                    sp.setValue(freq_mhz)
                    sp.blockSignals(False)

                label.setText(f"N={n}")
                self.laser_rep_rate_changed.emit(name, float(freq_khz))

            rep_rate_spin.editingFinished.connect(_on_rep_rate_finished)

        self.laser_tabs.addTab(tab, laser_name)

        self.laser_controls[laser_name] = {
            'tab': tab,
            'spin': setpoint_spin,
            'slider': setpoint_slider,
            'button': power_button,
            'plus_button': plus_button,
            'minus_button': minus_button,
            'gdd_spin': gdd_spin,
            'rep_rate_spin': rep_rate_spin,
            'rep_rate_valid_label': rep_rate_valid_label,
            'connect_button': alcor_connect_btn,
            'connect_status_label': alcor_status_label,
        }

    @staticmethod
    def _power_to_slider_value(value: float) -> int:
        value = max(POWER_UI_MIN, min(POWER_UI_MAX, float(value)))
        return int(round(value * POWER_SLIDER_SCALE))

    @staticmethod
    def _slider_to_power_value(value: int) -> float:
        power = float(value) / POWER_SLIDER_SCALE
        return max(POWER_UI_MIN, min(POWER_UI_MAX, power))
    
    @staticmethod
    def _alcor_rep_rate_divider_from_mhz(freq_mhz: float) -> int:
        freq_mhz = max(ALCOR_REP_RATE_MIN_MHZ, min(ALCOR_REP_RATE_MAX_MHZ, float(freq_mhz)))
        n = int(round(ALCOR_REP_RATE_BASE_MHZ / freq_mhz))
        n = max(1, n)

        # Respecte la borne basse: 80 / N doit rester >= 2.9 MHz.
        max_n = int(ALCOR_REP_RATE_BASE_MHZ / ALCOR_REP_RATE_MIN_MHZ)
        return max(1, min(max_n, n))

    @classmethod
    def _snap_alcor_rep_rate_mhz(cls, freq_mhz: float) -> float:
        n = cls._alcor_rep_rate_divider_from_mhz(freq_mhz)
        return ALCOR_REP_RATE_BASE_MHZ / float(n)
    
    def set_laser_power_value(self, laser_name: str, value: float):
        controls = self.laser_controls.get(laser_name)
        if not controls:
            return

        value = max(POWER_UI_MIN, min(POWER_UI_MAX, float(value)))
        slider_value = self._power_to_slider_value(value)

        spin = controls.get("spin")
        slider = controls.get("slider")

        if spin is not None:
            spin.blockSignals(True)
            spin.setValue(value)
            spin.blockSignals(False)

        if slider is not None:
            slider.blockSignals(True)
            slider.setValue(slider_value)
            slider.blockSignals(False)

    def get_laser_power_value(self, laser_name: str) -> float:
        controls = self.laser_controls.get(laser_name)
        if not controls:
            return 0.0

        spin = controls.get("spin")
        if spin is None:
            return 0.0

        return float(spin.value())
    
    def set_laser_gdd_value(self, laser_name: str, value_fs2: float):
        controls = self.laser_controls.get(laser_name)
        if not controls:
            return

        spin = controls.get("gdd_spin")
        if spin is None:
            return

        value_fs2 = max(ALCOR_GDD_MIN_FS2, min(ALCOR_GDD_MAX_FS2, float(value_fs2)))

        spin.blockSignals(True)
        spin.setValue(value_fs2)
        spin.blockSignals(False)

    def get_laser_gdd_value(self, laser_name: str) -> float:
        controls = self.laser_controls.get(laser_name)
        if not controls:
            return 0.0

        spin = controls.get("gdd_spin")
        if spin is None:
            return 0.0

        return float(spin.value())

    def set_laser_rep_rate_value_khz(self, laser_name: str, value_khz: float):
        controls = self.laser_controls.get(laser_name)
        if not controls:
            return

        spin = controls.get("rep_rate_spin")
        label = controls.get("rep_rate_valid_label")

        if spin is None:
            return

        freq_mhz = self._snap_alcor_rep_rate_mhz(float(value_khz) / 1000.0)
        n = self._alcor_rep_rate_divider_from_mhz(freq_mhz)

        spin.blockSignals(True)
        spin.setValue(freq_mhz)
        spin.blockSignals(False)

        if label is not None:
            label.setText(f"N={n}")

    def get_laser_rep_rate_value_khz(self, laser_name: str) -> float:
        controls = self.laser_controls.get(laser_name)
        if not controls:
            return ALCOR_REP_RATE_BASE_MHZ * 1000.0

        spin = controls.get("rep_rate_spin")
        if spin is None:
            return ALCOR_REP_RATE_BASE_MHZ * 1000.0

        freq_mhz = self._snap_alcor_rep_rate_mhz(float(spin.value()))
        return float(freq_mhz) * 1000.0
    
    def set_laser_enabled(self, laser_name: str, enabled: bool):
        controls = self.laser_controls.get(laser_name)
        if not controls:
            return

        button = controls.get("button")
        if button is None:
            return

        button.blockSignals(True)
        button.setChecked(bool(enabled))
        self._update_power_button_style(button, bool(enabled))
        button.blockSignals(False)
    
    def _on_alcor_connect_clicked(self):
        controls = self.laser_controls.get("Alcor 920", {})
        btn = controls.get("connect_button")
        lbl = controls.get("connect_status_label")
        if btn is not None:
            btn.setEnabled(False)
            btn.setText("Connecting…")
        if lbl is not None:
            lbl.setText("Connecting…")
            lbl.setStyleSheet("color: #888; background: transparent; border: none;")
        self.alcor_connect_requested.emit()

    def set_alcor_connected(self, connected: bool, message: str = ""):
        controls = self.laser_controls.get("Alcor 920", {})
        btn = controls.get("connect_button")
        lbl = controls.get("connect_status_label")
        if connected:
            if btn is not None:
                btn.setText("Connected")
                btn.setEnabled(False)
            if lbl is not None:
                lbl.setText("Connected")
                lbl.setStyleSheet("color: #7ec87e; background: transparent; border: none;")
        else:
            if btn is not None:
                btn.setText("Connect")
                btn.setStyleSheet(SMALL_BUTTON_STYLE)
                btn.setEnabled(True)
            if lbl is not None:
                text = message if message else "Not connected"
                lbl.setText(text)
                lbl.setStyleSheet("color: #cc6666; background: transparent; border: none;")

    def open_settings_dialog(self):
        from .Dialogs import SettingsDialog
        dialog = SettingsDialog("Laser - Settings", self)
        setup_laser_settings_dialog(dialog)
        dialog.exec()
    
    def _update_power_button_style(self, button, checked):
        """Met à jour le style du bouton ON/OFF."""
        if checked:
            button.setText("ON")
            button.setStyleSheet(POWER_BUTTON_ON_STYLE)
        else:
            button.setText("OFF")
            button.setStyleSheet(POWER_BUTTON_OFF_STYLE)