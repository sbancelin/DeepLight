from PySide6.QtWidgets import (QWidget, QVBoxLayout, QGroupBox, QPushButton, QGridLayout, QLabel, QSpinBox, 
                               QSlider, QSizePolicy, QDialog, QHBoxLayout, QLineEdit, QMessageBox)
from PySide6.QtCore import Signal, Qt
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

POWER_BUTTON_OFF_STYLE = """
    QPushButton {
        border: none;
        background-color: transparent;
    }
    QPushButton:disabled {
        qproperty-iconOpacity: 0.05;
    }
    QPushButton:hover {
        background-color: #444;
        border-radius: 6px;
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
        "offset_deg": 0.0,
    },
    "Tumecs": {
        "speed": 429410,
        "steps_per_degree": 1919.14,
        "offset_deg": 0.0,
    },
}

def setup_laser_settings_dialog(dialog):
    laser_widget = dialog.parent()
    laser_names = ["Mira 900", "Tumecs"]

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
            return float(le.text().replace(",", "."))
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
    laser_power_changed = Signal(str, int)
    laser_power_toggled = Signal(str, bool)

    def __init__(self, parent=None):
        super().__init__(parent)

        self.settings_manager = None

        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)

        self.main_layout = QVBoxLayout(self)
        self.main_layout.setContentsMargins(6, 6, 6, 6)
        self.main_layout.setSpacing(6)
    
        content_widget = QWidget()
        content_widget.setMinimumWidth(0)
        content_widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)

        self.laser_layout = QVBoxLayout(content_widget)
        self.laser_layout.setContentsMargins(2, 2, 2, 2)
        self.laser_layout.setSpacing(4)

        # Ajout des contrôles pour chaque laser
        self.laser_controls = {}
        self._add_laser_control("Alcor 920")
        self._add_laser_control("Cobolt 660")
        self._add_laser_control("Mira 900")
        self._add_laser_control("Tumecs")

        self.main_layout.addWidget(content_widget)
        self.main_layout.addStretch()

    def set_settings_manager(self, manager):
        self.settings_manager = manager

        for laser_name, defaults in LASER_DEFAULTS.items():
            if not self.settings_manager.get_laser_settings(laser_name):
                self.settings_manager.update_laser_settings(laser_name, **defaults)
    
    def _add_laser_control(self, laser_name):
        """Ajoute un groupe de contrôle pour un laser spécifique."""
        # GroupBox pour chaque laser
        laser_group = QGroupBox()
        laser_group.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        laser_group.setStyleSheet("""
            QGroupBox {
                background-color: #2a2a2a;
                border: 1px solid #444;
                border-radius: 4px;
                margin-top: 2px;
                margin-bottom: 2px;
                padding: 0px;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                color: white;
                font-weight: bold;
                font-size: 14px;
                padding: 0 5px;
            }
        """)

        # Layout pour le laser
        laser_layout = QGridLayout()
        laser_layout.setHorizontalSpacing(2)
        laser_layout.setVerticalSpacing(2)
        laser_layout.setContentsMargins(6, 8, 6, 8)

        laser_layout.setColumnStretch(0, 0)  # nom laser
        laser_layout.setColumnStretch(1, 0)  # spinbox
        laser_layout.setColumnStretch(2, 0)  # bouton -
        laser_layout.setColumnStretch(3, 1)  # slider prend l'espace
        laser_layout.setColumnStretch(4, 0)  
        laser_layout.setColumnStretch(5, 0)  # bouton +
        laser_layout.setColumnStretch(6, 0)  # ON/OFF

        laser_layout.setColumnMinimumWidth(0, 85)

        laser_name_label = QLabel(laser_name)
        laser_name_label.setStyleSheet("""
            QLabel {
                color: white;
                font-weight: bold;
                font-size: 14px;
            }
        """)
        laser_name_label.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        laser_name_label.setAlignment(Qt.AlignVCenter | Qt.AlignLeft)

        # Champ de valeur numérique
        setpoint_spin = QSpinBox()
        setpoint_spin.setMinimumWidth(70)
        setpoint_spin.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        setpoint_spin.setRange(0, 100)
        setpoint_spin.setValue(0)
        setpoint_spin.setSuffix(" %")
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

        # Slider
        setpoint_slider = QSlider(Qt.Horizontal)
        setpoint_slider.setMinimumWidth(60)
        setpoint_slider.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        setpoint_slider.setRange(0, 100)
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

        current_value_label = QLabel("0%")

        # Bouton ON/OFF
        power_button = QPushButton("OFF")
        power_button.setCheckable(True)
        power_button.setChecked(False)
        power_button.setFixedSize(30, 25)  # Taille fixe
        power_button.setStyleSheet(POWER_BUTTON_STYLE)

        # Bouton +
        plus_button = QPushButton("+")
        plus_button.setFixedSize(20, 20)
        plus_button.setStyleSheet(SMALL_BUTTON_STYLE)

        # Bouton -
        minus_button = QPushButton("-")
        minus_button.setFixedSize(20, 20)
        minus_button.setStyleSheet(SMALL_BUTTON_STYLE)

        # Ajout des widgets au layout
        laser_layout.addWidget(laser_name_label, 0, 0)
        laser_layout.addWidget(setpoint_spin, 0, 1)
        laser_layout.addWidget(minus_button, 0, 2)
        laser_layout.addWidget(setpoint_slider, 0, 3, 1, 2)
        laser_layout.addWidget(plus_button, 0, 5)
        laser_layout.addWidget(power_button, 0, 6)

        # Connexions
        setpoint_spin.valueChanged.connect(setpoint_slider.setValue)
        setpoint_slider.valueChanged.connect(setpoint_spin.setValue)
        setpoint_slider.valueChanged.connect(lambda val: current_value_label.setText(f"{val}%"))

        power_button.toggled.connect(lambda state: self._update_power_button_style(power_button, state))
        power_button.toggled.connect(lambda state, name=laser_name: self.laser_power_toggled.emit(name, state))

        # émission seulement quand l'utilisateur valide vraiment
        setpoint_spin.editingFinished.connect(
            lambda name=laser_name, sp=setpoint_spin: self.laser_power_changed.emit(name, sp.value())
        )
        setpoint_slider.sliderReleased.connect(
            lambda name=laser_name, sl=setpoint_slider: self.laser_power_changed.emit(name, sl.value())
        )

        laser_group.setLayout(laser_layout)
        self.laser_layout.addWidget(laser_group)

        # Stocker les références pour chaque laser
        self.laser_controls[laser_name] = {
            'spin': setpoint_spin,
            'slider': setpoint_slider,
            'label': current_value_label,
            'button': power_button,
             'plus_button': plus_button,
            'minus_button': minus_button
        }

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
