from PySide6.QtWidgets import (QWidget, QVBoxLayout, QGroupBox, QPushButton, QGridLayout, 
                               QLabel, QSpinBox, QSlider, QSizePolicy)
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


class LaserWidget(QWidget):
    """Widget pour le contrôle des lasers."""

    def __init__(self, parent=None):
        super().__init__(parent)

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

    def _update_power_button_style(self, button, checked):
        """Met à jour le style du bouton ON/OFF."""
        if checked:
            button.setText("ON")
            button.setStyleSheet(POWER_BUTTON_ON_STYLE)
        else:
            button.setText("OFF")
            button.setStyleSheet(POWER_BUTTON_OFF_STYLE)
