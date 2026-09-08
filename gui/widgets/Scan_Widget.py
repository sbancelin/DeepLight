from PySide6.QtWidgets import (QVBoxLayout, QHBoxLayout, QGroupBox, QPushButton, QGridLayout, QLabel,
                              QLineEdit, QComboBox, QCheckBox, QWidget, QMessageBox, QSizePolicy)
from PySide6.QtCore import Signal, Qt
from ..managers.Scan_Types import SCAN_AXIS_DEFAULTS, STEPPER_AXIS_DEFAULTS

DAQ_SAMPLE_RATE_HZ = 500_000.0
DAQ_SAMPLE_PERIOD_S = 1.0 / DAQ_SAMPLE_RATE_HZ
DAQ_SAMPLE_PERIOD_US = DAQ_SAMPLE_PERIOD_S * 1e6

GROUPBOX_STYLE = """
    QGroupBox {
        border: 1px solid #444;
        border-radius: 4px;
        margin-top: 2px;
    }
"""

EDITABLE_LINEEDIT_STYLE = """
    QLineEdit {
        background-color: #333;
        color: white;
        border: 1px solid #555;
        border-radius: 3px;
        padding: 2px;
        min-height: 20px;
    }
"""

READONLY_LINEEDIT_STYLE = """
    QLineEdit {
        background-color: #252525;
        color: #888;
        border: 1px solid #444;
        border-radius: 3px;
        padding: 2px;
        min-height: 20px;
    }
"""

SCAN_COMBO_STYLE = """
    QComboBox {
        background-color: #333;
        color: white;
        border: 1px solid #555;
        border-radius: 3px;
        padding: 2px;
        min-height: 20px;
    }
"""

SCAN_COMBO_DISABLED_STYLE = """
    QComboBox {
        background-color: #252525;
        color: #888;
        border: 1px solid #444;
        border-radius: 3px;
        padding: 2px;
        min-height: 20px;
    }
    QComboBox:disabled {
        background-color: #252525;
        color: #888;
        border: 1px solid #444;
    }
"""

HEADER_LABEL_STYLE = "color: white; font-weight: bold; padding-bottom: 5px;"

BUTTON_STYLE = """
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

APPLY_BUTTON_STYLE = """
    QPushButton {
        background-color: #333;
        color: white;
        border: 2px solid #2E8B57;
        border-radius: 3px;
        padding: 2px;
        font-weight: bold;
        min-height: 20px;
    }
    QPushButton:hover {
        background-color: #444;
    }
"""

BIDIRECTIONAL_BUTTON_STYLE = """
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

CHECKBOX_STYLE = """
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

LASER_TOGGLE_STYLE = """
    QPushButton {
        background-color: #2c2c2c;
        color: #aaa;
        border: 1px solid #555;
        border-right: none;
        border-top-left-radius: 5px;
        border-bottom-left-radius: 5px;
        border-top-right-radius: 0px;
        border-bottom-right-radius: 0px;
        padding: 2px 8px;
        font-weight: bold;
    }
    QPushButton:checked {
        background-color: #2E8B57;
        color: white;
        border: 1px solid #3AB16F;
        border-right: none;
    }
    QPushButton:hover:!checked {
        background-color: #3a3a3a;
        color: #ddd;
    }
    QPushButton:checked:hover {
        background-color: #3AB16F;
        color: white;
    }
"""

SAMPLE_TOGGLE_STYLE = """
    QPushButton {
        background-color: #2c2c2c;
        color: #aaa;
        border: 1px solid #555;
        border-top-right-radius: 5px;
        border-bottom-right-radius: 5px;
        border-top-left-radius: 0px;
        border-bottom-left-radius: 0px;
        padding: 2px 8px;
        font-weight: bold;
    }
    QPushButton:checked {
        background-color: #FF7700;
        color: white;
        border: 1px solid #FF9200;
    }
    QPushButton:hover:!checked {
        background-color: #3a3a3a;
        color: #ddd;
    }
    QPushButton:checked:hover {
        background-color: #FF9200;
        color: white;
    }
"""


def setup_scan_settings_dialog(dialog):
    """Construit le dialogue des réglages avancés des axes de scan."""
    scan_widget = dialog.parent()
    axes = ["X-Galvo", "Y-Galvo"]

    for index, axis_name in enumerate(axes):
        axis_conversion_container = QWidget()
        axis_conversion_layout = QVBoxLayout(axis_conversion_container)
        axis_conversion_layout.setContentsMargins(0, 0, 0, 0)
        axis_conversion_layout.setSpacing(2)

        axis_label = QLabel(f"<b>{axis_name} Axis</b>")
        axis_conversion_layout.addWidget(axis_label)

        separator = QLabel()
        separator.setFrameShape(QLabel.HLine)
        separator.setFrameShadow(QLabel.Sunken)
        separator.setStyleSheet("color: #555; margin-top: 0px; margin-bottom: 0px;")
        dialog.add_widget(separator)

        axis_cfg = {}
        if scan_widget is not None and scan_widget.axis_settings_manager is not None:
            axis_cfg = scan_widget.axis_settings_manager.get_axis_settings(axis_name)

        defaults = SCAN_AXIS_DEFAULTS.get(axis_name, {})

        cur_conv = axis_cfg.get("conv_um_per_v", defaults.get("conv_um_per_v"))
        cur_vmin = axis_cfg.get("vmin", defaults.get("vmin", -10.0))
        cur_vmax = axis_cfg.get("vmax", defaults.get("vmax", 10.0))
        cur_overscan = float(axis_cfg.get("overscan_fraction", defaults.get("overscan_fraction", 0.0)))
        cur_frame_flyback_s = float(axis_cfg.get("frame_flyback_time_s", defaults.get("frame_flyback_time_s", 0.0)))
        cur_vel = float(axis_cfg.get("vel_max", defaults.get("vel_max", 1.0)))

        conversion_layout = QHBoxLayout()
        conversion_label = QLabel("Conversion Factor (µm/V):")
        conversion_edit = QLineEdit(str(cur_conv))
        conversion_edit.setObjectName(f"conv_edit_{axis_name.replace('-', '_')}")
        conversion_layout.addWidget(conversion_label)
        conversion_layout.addWidget(conversion_edit)
        axis_conversion_layout.addLayout(conversion_layout)

        dialog.add_widget(axis_conversion_container)

        voltage_layout = QHBoxLayout()
        min_voltage_label = QLabel("Min Voltage (V):")
        max_voltage_label = QLabel("Max Voltage (V):")
        min_voltage_edit = QLineEdit(str(cur_vmin))
        max_voltage_edit = QLineEdit(str(cur_vmax))
        min_voltage_edit.setObjectName(f"vmin_edit_{axis_name.replace('-', '_')}")
        max_voltage_edit.setObjectName(f"vmax_edit_{axis_name.replace('-', '_')}")
        voltage_layout.addWidget(min_voltage_label)
        voltage_layout.addWidget(min_voltage_edit)
        voltage_layout.addWidget(max_voltage_label)
        voltage_layout.addWidget(max_voltage_edit)
        dialog.add_layout(voltage_layout)

        dyn_layout_1 = QHBoxLayout()
        vel_edit = QLineEdit(str(cur_vel))
        vel_edit.setObjectName(f"vel_edit_{axis_name.replace('-', '_')}")
        dyn_layout_1.addWidget(QLabel("Velocity max (mm/s):"))
        dyn_layout_1.addWidget(vel_edit)
        dialog.add_layout(dyn_layout_1)

        dyn_layout_2 = QHBoxLayout()
        if axis_name == "X-Galvo":
            overscan_edit = QLineEdit(str(100.0 * cur_overscan))
            overscan_edit.setObjectName(f"overscan_edit_{axis_name.replace('-', '_')}")
            dyn_layout_2.addWidget(QLabel("Overscan (%):"))
            dyn_layout_2.addWidget(overscan_edit)

        elif axis_name == "Y-Galvo":
            flyback_edit = QLineEdit(str(cur_frame_flyback_s * 1e3))
            flyback_edit.setObjectName(f"flyback_edit_{axis_name.replace('-', '_')}")
            dyn_layout_2.addWidget(QLabel("Frame flyback (ms):"))
            dyn_layout_2.addWidget(flyback_edit)

        dialog.add_layout(dyn_layout_2)

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
        if scan_widget is None or scan_widget.axis_settings_manager is None:
            return

        for axis_name in axes:
            key = axis_name.replace("-", "_")

            conv_edit = dialog.findChild(QLineEdit, f"conv_edit_{key}")
            vmin_edit = dialog.findChild(QLineEdit, f"vmin_edit_{key}")
            vmax_edit = dialog.findChild(QLineEdit, f"vmax_edit_{key}")
            vel_edit = dialog.findChild(QLineEdit, f"vel_edit_{key}")

            if not all([conv_edit, vmin_edit, vmax_edit, vel_edit]):
                continue

            old = scan_widget.axis_settings_manager.get_axis_settings(axis_name)
            defaults = SCAN_AXIS_DEFAULTS.get(axis_name, {})

            old_conv = float(old.get("conv_um_per_v", defaults.get("conv_um_per_v")))
            old_vmin = float(old.get("vmin", defaults.get("vmin", -5.0)))
            old_vmax = float(old.get("vmax", defaults.get("vmax", 5.0)))
            old_vel = float(old.get("vel_max", defaults.get("vel_max", 1.0)))
            old_overscan = float(old.get("overscan_fraction", defaults.get("overscan_fraction", 0.0)))
            old_flyback_s = float(old.get("frame_flyback_time_s", defaults.get("frame_flyback_time_s", 0.0)))

            conv = _read_float(conv_edit, old_conv)
            vmin = _read_float(vmin_edit, old_vmin)
            vmax = _read_float(vmax_edit, old_vmax)
            vel = _read_float(vel_edit, old_vel)

            if axis_name == "X-Galvo":
                overscan_edit = dialog.findChild(QLineEdit, f"overscan_edit_{key}")
                if overscan_edit is None:
                    continue

                overscan_percent = _read_float(overscan_edit, 100.0 * old_overscan)
                overscan_fraction = max(0.0, min(0.30, overscan_percent / 100.0))

                ok, msg = scan_widget.validate_axis_setting_values(
                    axis_name=axis_name,
                    conv=conv,
                    vmin=vmin,
                    vmax=vmax,
                    vel=vel,
                    overscan_percent=overscan_percent,
                    frame_flyback_ms=None,
                )
                if not ok:
                    QMessageBox.warning(scan_widget, "Invalid scan setting", msg)
                    conv_edit.setText(str(old_conv))
                    vmin_edit.setText(str(old_vmin))
                    vmax_edit.setText(str(old_vmax))
                    vel_edit.setText(str(old_vel))
                    overscan_edit.setText(str(100.0 * old_overscan))
                    continue

                scan_widget.axis_settings_manager.update_axis_settings(
                    axis_name,
                    conv_um_per_v=conv,
                    vmin=vmin,
                    vmax=vmax,
                    overscan_fraction=overscan_fraction,
                    frame_flyback_time_s=0.0,
                    vel_max=vel,
                )

            elif axis_name == "Y-Galvo":
                flyback_edit = dialog.findChild(QLineEdit, f"flyback_edit_{key}")
                if flyback_edit is None:
                    continue

                frame_flyback_ms = _read_float(flyback_edit, old_flyback_s * 1e3)
                frame_flyback_time_s = max(0.0, frame_flyback_ms * 1e-3)

                ok, msg = scan_widget.validate_axis_setting_values(
                    axis_name=axis_name,
                    conv=conv,
                    vmin=vmin,
                    vmax=vmax,
                    vel=vel,
                    overscan_percent=None,
                    frame_flyback_ms=frame_flyback_ms,
                )
                if not ok:
                    QMessageBox.warning(scan_widget, "Invalid scan setting", msg)
                    conv_edit.setText(str(old_conv))
                    vmin_edit.setText(str(old_vmin))
                    vmax_edit.setText(str(old_vmax))
                    vel_edit.setText(str(old_vel))
                    flyback_edit.setText(str(old_flyback_s * 1e3))
                    continue

                scan_widget.axis_settings_manager.update_axis_settings(
                    axis_name,
                    conv_um_per_v=conv,
                    vmin=vmin,
                    vmax=vmax,
                    overscan_fraction=0.0,
                    frame_flyback_time_s=frame_flyback_time_s,
                    vel_max=vel,
                )

        scan_widget.axis_settings = scan_widget.axis_settings_manager.get_all_axis_settings()
        scan_widget._update_scan_duration()
        scan_widget._on_param_changed()

    dialog.accepted.connect(on_dialog_accepted)

class ScanWidget(QWidget):
    """Widget pour les contrôles de scan."""
    view_update_requested = Signal(object)   # pix_x, pix_y

    def __init__(self, parent=None):
        super().__init__(parent)
        self.main_layout = QVBoxLayout(self)
        self.main_layout.setContentsMargins(2, 2, 2, 2)
        self.main_layout.setSpacing(4)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        
        self.axis_settings_manager = None

        self.params_changed = False

        # Valeurs par défaut (modifiables dans popup settings)
        self.axis_settings = {}

        # Valeurs par défaut UI pour chaque axe
        self.default_values = {
            "X-Galvo": {"size": "100", "pixels": "256", "offset": "0"},
            "Y-Galvo": {"size": "100", "pixels": "256", "offset": "0"},
            "X-Stage": {"size": "100", "pixels": "64", "offset": "0"},
            "Y-Stage": {"size": "100", "pixels": "64", "offset": "0"},
            "Z-Vcoil": {"size": "10", "pixels": "10", "offset": "0"},
            "Polarization": {"size": "180", "pixels": "19", "offset": "0"},
        }

        self.scan_kind = "laser"
        self._settle_ms = 0.0

        # =========================
        # Scan mode group
        # =========================
        mode_group = QGroupBox("", self)
        mode_group.setMinimumWidth(0)
        mode_group.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        mode_group.setStyleSheet("QGroupBox { border: none; margin: 0; padding: 2px; }")

        mode_layout = QHBoxLayout(mode_group)
        mode_layout.setContentsMargins(4, 1, 4, 1)
        mode_layout.setSpacing(0)

        self.laser_mode_button = QPushButton("Laser scanning")
        self.laser_mode_button.setCheckable(True)
        self.laser_mode_button.setChecked(True)

        self.sample_mode_button = QPushButton("Sample scanning")
        self.sample_mode_button.setCheckable(True)
        self.sample_mode_button.setChecked(False)

        for btn in (self.laser_mode_button, self.sample_mode_button):
            btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        mode_layout.addWidget(self.laser_mode_button)
        mode_layout.addWidget(self.sample_mode_button)

        self.main_layout.addWidget(mode_group)

        # =========================
        # Spatial parameters group
        # =========================
        spatial_group = QGroupBox("", self)
        spatial_group.setMinimumWidth(0)
        spatial_group.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        spatial_group.setStyleSheet(GROUPBOX_STYLE)

        spatial_layout = QVBoxLayout(spatial_group)
        spatial_layout.setContentsMargins(6, 6, 6, 6)
        spatial_layout.setSpacing(4)

        grid_layout = QGridLayout()
        grid_layout.setContentsMargins(0, 0, 0, 0)
        grid_layout.setHorizontalSpacing(6)
        grid_layout.setVerticalSpacing(4)

        grid_layout.setColumnMinimumWidth(0, 10)   # Axis
        grid_layout.setColumnMinimumWidth(1, 10)   # Size
        grid_layout.setColumnMinimumWidth(2, 10)   # Pix
        grid_layout.setColumnMinimumWidth(3, 10)   # Step
        grid_layout.setColumnMinimumWidth(4, 10)   # Offset

        grid_layout.setColumnMinimumWidth(5, 10)   # Mode

        grid_layout.setColumnStretch(0, 0)
        grid_layout.setColumnStretch(1, 1)
        grid_layout.setColumnStretch(2, 1)
        grid_layout.setColumnStretch(3, 1)
        grid_layout.setColumnStretch(4, 1)
        grid_layout.setColumnStretch(5, 1)

        # En-têtes
        headers = ["Axis", "Size (µm)", "# Pix", "Step (µm)", "Offset (µm)", "Scan"]
        for col, header in enumerate(headers):
            label = QLabel(header)
            label.setStyleSheet(HEADER_LABEL_STYLE)
            label.setAlignment(Qt.AlignCenter)
            label.setMinimumWidth(0)
            label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            grid_layout.addWidget(label, 0, col)

        # Dimension et champs éditables
        self.initial_axes = ["X-Galvo", "Y-Galvo", "None", "None"]
        defaut_axes = list(self.initial_axes)

        # Listes widgets
        self.pixel_edits = []
        self.scan_dim_combos = []
        self.size_edits = []
        self.step_edits = []
        self.offset_edits = []
        self.scan_mode_combos = []

        for row, pos in enumerate(defaut_axes, 1):
            # Colonne 0: Scan dim
            scan_dim_combo = QComboBox()
            scan_dim_combo.addItems(["None"])
            scan_dim_combo.setCurrentText(pos)
            scan_dim_combo.setProperty("prev_text", scan_dim_combo.currentText())
            scan_dim_combo.setStyleSheet("""
                QComboBox {
                    background-color: #333;
                    color: white;
                    border: 1px solid #555;
                    border-radius: 3px;
                    padding: 2px;
                    min-height: 20px;
                }
            """)
            scan_dim_combo.setMinimumWidth(0)
            scan_dim_combo.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            grid_layout.addWidget(scan_dim_combo, row, 0)
            setattr(self, f"scan_dim_combo_{pos.replace('-', '_')}", scan_dim_combo)
            self.scan_dim_combos.append(scan_dim_combo)

            # Colonne 1: Size
            size_edit = QLineEdit("" if pos == "None" else self.default_values.get(pos, {}).get("size", "100"))
            self._set_editable_lineedit_style(size_edit)
            size_edit.setMinimumWidth(0)
            size_edit.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            grid_layout.addWidget(size_edit, row, 1)
            setattr(self, f"size_edit_{pos.replace('-', '_')}", size_edit)
            size_edit.setProperty("last_valid_text", size_edit.text())
            self.size_edits.append(size_edit)

            # Colonne 2: Pixels
            pixel_edit = QLineEdit("" if pos == "None" else self.default_values.get(pos, {}).get("pixels", "256"))
            self._set_editable_lineedit_style(pixel_edit)
            pixel_edit.setMinimumWidth(0)
            pixel_edit.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            grid_layout.addWidget(pixel_edit, row, 2)
            setattr(self, f"pixels_label_{pos.replace('-', '_')}", pixel_edit)
            pixel_edit.setProperty("last_valid_text", pixel_edit.text())
            self.pixel_edits.append(pixel_edit)

            # Colonne 3: Step size
            step_edit = QLineEdit()
            step_edit.setReadOnly(True)
            self._set_disabled_lineedit_style(step_edit)
            step_edit.setMinimumWidth(0)
            step_edit.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            grid_layout.addWidget(step_edit, row, 3)
            setattr(self, f"step_edit_{pos.replace('-', '_')}", step_edit)
            self.step_edits.append(step_edit)

            # Colonne 4: Offset
            offset_edit = QLineEdit("" if pos == "None" else self.default_values.get(pos, {}).get("offset", "0"))
            self._set_editable_lineedit_style(offset_edit)
            offset_edit.setMinimumWidth(0)
            offset_edit.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            grid_layout.addWidget(offset_edit, row, 4)
            setattr(self, f"offset_edit_{pos.replace('-', '_')}", offset_edit)
            offset_edit.setProperty("last_valid_text", offset_edit.text())
            self.offset_edits.append(offset_edit)

            # Colonne 5: Mode de balayage (stack) — Around / From.
            # Pertinent uniquement pour les axes platine stack (Z-Vcoil,
            # Polarization) ; désactivé sinon.
            mode_combo = QComboBox()
            mode_combo.addItems(["Around", "From"])
            mode_combo.setCurrentText("Around")
            mode_combo.setToolTip(
                "Around: ±size/2 around the current relative position.\n"
                "From: the full size STARTING FROM the current relative\n"
                "position (start = current position)."
            )
            mode_combo.setStyleSheet(SCAN_COMBO_STYLE)
            mode_combo.setMinimumWidth(0)
            mode_combo.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            grid_layout.addWidget(mode_combo, row, 5)
            setattr(self, f"scan_mode_combo_{pos.replace('-', '_')}", mode_combo)
            self.scan_mode_combos.append(mode_combo)

            self._update_steps(size_edit, pixel_edit, step_edit)

            i = row - 1

            size_edit.textChanged.connect(lambda _=None, idx=i: self._on_param_changed_if_xy(idx))
            pixel_edit.textChanged.connect(lambda _=None, idx=i: self._on_param_changed_if_xy(idx))
            scan_dim_combo.currentTextChanged.connect(lambda _=None, idx=i: self._on_param_changed_if_xy(idx))
            size_edit.textChanged.connect(
                lambda _=None, s=size_edit, p=pixel_edit, st=step_edit: self._update_steps(s, p, st)
            )
            pixel_edit.textChanged.connect(
                lambda _=None, s=size_edit, p=pixel_edit, st=step_edit: self._update_steps(s, p, st)
            )

            scan_dim_combo.currentTextChanged.connect(
                lambda text, combo=scan_dim_combo, p=pixel_edit: self._on_scan_axis_changed(combo, p, text)
            )

            size_edit.textChanged.connect(self._update_total_pixels)
            pixel_edit.textChanged.connect(self._update_total_pixels)
            scan_dim_combo.currentTextChanged.connect(self._update_total_pixels)

            size_edit.textChanged.connect(self._update_scan_duration)
            pixel_edit.textChanged.connect(self._update_scan_duration)
            scan_dim_combo.currentTextChanged.connect(self._update_scan_duration)

            size_edit.returnPressed.connect(lambda idx=i: self._validate_row_and_revert_if_needed(idx))
            pixel_edit.returnPressed.connect(lambda idx=i: self._validate_row_and_revert_if_needed(idx))
            offset_edit.returnPressed.connect(lambda idx=i: self._validate_row_and_revert_if_needed(idx))

            mode_combo.currentTextChanged.connect(lambda _=None: self._on_param_changed())
            mode_combo.currentTextChanged.connect(lambda _=None: self._update_scan_duration())

            # État initial de la combobox Mode (activée seulement pour Z/P)
            self._update_scan_mode_combo(i, pos)

            if pos == "None":
                self._disable_axis_fields(i)

        # Ligne boutons spatiaux
        self.bidirectional_button = QPushButton("Bidirect.")
        self.bidirectional_button.setCheckable(True)
        self.bidirectional_button.setChecked(False)
        self.bidirectional_button.setStyleSheet(BIDIRECTIONAL_BUTTON_STYLE)
        grid_layout.addWidget(self.bidirectional_button, 5, 0)

        bidirectional_shift_label = QLabel("Back shift (px)")
        bidirectional_shift_label.setStyleSheet("color: white;")
        bidirectional_shift_label.setMinimumWidth(0)
        bidirectional_shift_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        grid_layout.addWidget(bidirectional_shift_label, 5, 1)

        self.bidirectional_shift_edit = QLineEdit("0")
        self.bidirectional_shift_edit.setEnabled(False)
        self._set_disabled_lineedit_style(self.bidirectional_shift_edit)
        self.bidirectional_shift_edit.setProperty("last_valid_text", "0")
        self.bidirectional_shift_edit.setToolTip(
            "Integer pixel shift applied on reverse lines.\n"
            "Once calibrated, the delay (µs) is stored and the shift\n"
            "adapts automatically when dwell time changes."
        )
        self.bidirectional_shift_edit.setMinimumWidth(0)
        self.bidirectional_shift_edit.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        grid_layout.addWidget(self.bidirectional_shift_edit, 5, 2)

        self.bidir_delay_label = QLabel("—")
        self.bidir_delay_label.setStyleSheet("color: #888; font-size: 9px;")
        self.bidir_delay_label.setMinimumWidth(0)
        self.bidir_delay_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.bidir_delay_label.setToolTip("Calibrated galvo delay (τ). Auto-updates shift when dwell changes.")
        grid_layout.addWidget(self.bidir_delay_label, 5, 3)

        self.apply_button = QPushButton("Update")
        self.apply_button.setStyleSheet(BUTTON_STYLE)
        self.apply_button.setMinimumWidth(0)
        self.apply_button.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        grid_layout.addWidget(self.apply_button, 5, 4)

        self.reset_button = QPushButton("Reset")
        self.reset_button.setStyleSheet(BUTTON_STYLE)
        self.reset_button.setMinimumWidth(0)
        self.reset_button.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        grid_layout.addWidget(self.reset_button, 5, 5)

        spatial_layout.addLayout(grid_layout)

        # ==========================
        # Temporal parameters group
        # ==========================
        temporal_group = QGroupBox("", self)
        temporal_group.setMinimumWidth(0)
        temporal_group.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        temporal_group.setStyleSheet(GROUPBOX_STYLE)

        temporal_layout = QGridLayout(temporal_group)
        temporal_layout.setHorizontalSpacing(6)
        temporal_layout.setVerticalSpacing(4)
        temporal_layout.setContentsMargins(6, 6, 6, 6)

        for col in range(4):
            temporal_layout.setColumnMinimumWidth(col, 20)
            temporal_layout.setColumnStretch(col, 1)

        dwell_label = QLabel("Dwell T. (µs)")
        dwell_label.setStyleSheet("color: white;")
        dwell_label.setMinimumWidth(0)
        dwell_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        temporal_layout.addWidget(dwell_label, 0, 0)

        self.dwell_edit = QLineEdit("10")
        self._set_editable_lineedit_style(self.dwell_edit)
        self.dwell_edit.returnPressed.connect(self._on_dwell_return_pressed)
        self.dwell_edit.setProperty("last_valid_text", self.dwell_edit.text())
        self.dwell_edit.setMinimumWidth(0)
        self.dwell_edit.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        temporal_layout.addWidget(self.dwell_edit, 1, 0)

        spp_label = QLabel("DAQ samples/px")
        spp_label.setStyleSheet("color: white;")
        spp_label.setMinimumWidth(0)
        spp_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        temporal_layout.addWidget(spp_label, 0, 1)

        self.samples_per_pixel_edit = QLineEdit("1")
        self.samples_per_pixel_edit.setEnabled(False)
        self.samples_per_pixel_edit.setReadOnly(True)
        self.samples_per_pixel_edit.setToolTip(
            "Calculated from fixed DAQ sampling:\n"
            "DAQ sampling = 0.5 MHz\n"
            "DAQ samples/px = ceil(dwell time × 0.5 MHz)."
        )
        self._set_disabled_lineedit_style(self.samples_per_pixel_edit)
        self.samples_per_pixel_edit.setProperty("last_valid_text", self.samples_per_pixel_edit.text())
        self.samples_per_pixel_edit.setMinimumWidth(0)
        self.samples_per_pixel_edit.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        temporal_layout.addWidget(self.samples_per_pixel_edit, 1, 1)

        total_pixels_label = QLabel("# of Pixels")
        total_pixels_label.setStyleSheet("color: white;")
        total_pixels_label.setMinimumWidth(0)
        total_pixels_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        temporal_layout.addWidget(total_pixels_label, 0, 2)

        self.total_pixels_edit = QLineEdit()
        self.total_pixels_edit.setReadOnly(True)
        self._set_disabled_lineedit_style(self.total_pixels_edit)
        self.total_pixels_edit.setMinimumWidth(0)
        self.total_pixels_edit.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        temporal_layout.addWidget(self.total_pixels_edit, 1, 2)

        duration_label = QLabel("Duration (s)")
        duration_label.setStyleSheet("color: white;")
        duration_label.setMinimumWidth(0)
        duration_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        temporal_layout.addWidget(duration_label, 0, 3)

        self.duration_edit = QLineEdit()
        self.duration_edit.setReadOnly(True)
        self._set_disabled_lineedit_style(self.duration_edit)
        self.duration_edit.setMinimumWidth(0)
        self.duration_edit.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        temporal_layout.addWidget(self.duration_edit, 1, 3)

        rep_mode_label = QLabel("Repetitions")
        rep_mode_label.setStyleSheet("color: white;")
        rep_mode_label.setMinimumWidth(0)
        rep_mode_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        temporal_layout.addWidget(rep_mode_label, 2, 0)

        rep_label = QLabel("# of Repetitions")
        rep_label.setStyleSheet("color: white;")
        rep_label.setAlignment(Qt.AlignCenter)
        rep_label.setMinimumWidth(0)
        rep_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        temporal_layout.addWidget(rep_label, 2, 1)

        delay_label = QLabel("Delay (s)")
        delay_label.setStyleSheet("color: white;")
        delay_label.setMinimumWidth(0)
        delay_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        temporal_layout.addWidget(delay_label, 2, 2)

        laser_label = QLabel("Laser off between Rep")
        laser_label.setStyleSheet("color: white;")
        laser_label.setMinimumWidth(0)
        laser_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        temporal_layout.addWidget(laser_label, 2, 3)

        self.rep_checkbox = QCheckBox()
        self.rep_checkbox.setChecked(False)
        self.rep_checkbox.setStyleSheet(CHECKBOX_STYLE)
        temporal_layout.addWidget(self.rep_checkbox, 3, 0, Qt.AlignCenter)

        self.rep_edit = QLineEdit("1")
        self._set_editable_lineedit_style(self.rep_edit)
        self.rep_edit.setEnabled(False)
        self._set_disabled_lineedit_style(self.rep_edit)
        self.rep_edit.setProperty("last_valid_text", self.rep_edit.text())
        self.rep_edit.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        temporal_layout.addWidget(self.rep_edit, 3, 1)

        self.delay_edit = QLineEdit("0")
        self._set_editable_lineedit_style(self.delay_edit)
        self.delay_edit.setEnabled(False)
        self._set_disabled_lineedit_style(self.delay_edit)
        self.delay_edit.setProperty("last_valid_text", self.delay_edit.text())
        self.delay_edit.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        temporal_layout.addWidget(self.delay_edit, 3, 2)

        self.laser_checkbox = QCheckBox()
        self.laser_checkbox.setChecked(True)
        self.laser_checkbox.setEnabled(False)
        self.laser_checkbox.setStyleSheet(CHECKBOX_STYLE)
        temporal_layout.addWidget(self.laser_checkbox, 3, 3, Qt.AlignCenter)

        # Settle time (sample mode only — shared with spectro settings)
        self.settle_label = QLabel("Settle T. (ms)")
        self.settle_label.setStyleSheet("color: #888;")
        self.settle_label.setMinimumWidth(0)
        self.settle_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.settle_label.setVisible(False)
        temporal_layout.addWidget(self.settle_label, 4, 0)

        self.settle_ms_display = QLineEdit("0")
        self.settle_ms_display.setReadOnly(True)
        self._set_disabled_lineedit_style(self.settle_ms_display)
        self.settle_ms_display.setMinimumWidth(0)
        self.settle_ms_display.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.settle_ms_display.setToolTip("Settling time shared with Spectro settings (configurable in Spectro → Settings).")
        self.settle_ms_display.setVisible(False)
        temporal_layout.addWidget(self.settle_ms_display, 4, 1)

        # Ajout des deux group box au layout principal du widget
        self.main_layout.addWidget(spatial_group)
        self.main_layout.addWidget(temporal_group)
        self.main_layout.addStretch()

        # Connexion des signaux
        self.apply_button.clicked.connect(self._on_update_view_clicked)
        self.reset_button.clicked.connect(self._reset_to_defaults)
        self.bidirectional_button.toggled.connect(self._on_bidirectional_toggled)
        self.bidirectional_shift_edit.returnPressed.connect(self._on_bidirectional_shift_return_pressed)

        self.rep_checkbox.toggled.connect(self._on_rep_checkbox_toggled)
        self.rep_checkbox.toggled.connect(self._update_scan_duration)

        self.dwell_edit.textChanged.connect(self._update_daq_samples_per_pixel_display)
        self.dwell_edit.textChanged.connect(self._update_scan_duration)
        self.samples_per_pixel_edit.textChanged.connect(self._update_scan_duration)
        self.rep_edit.textChanged.connect(self._update_scan_duration)
        self.delay_edit.textChanged.connect(self._update_scan_duration)

        self.dwell_edit.textChanged.connect(self._on_param_changed)
        self.samples_per_pixel_edit.textChanged.connect(self._on_param_changed)
        self.rep_edit.textChanged.connect(self._on_param_changed)
        self.delay_edit.textChanged.connect(self._on_param_changed)

        self.rep_edit.returnPressed.connect(self._on_rep_return_pressed)
        self.delay_edit.returnPressed.connect(self._on_delay_return_pressed)

        self.laser_checkbox.toggled.connect(self._on_param_changed)
        self.laser_checkbox.toggled.connect(self._update_scan_duration)

        self.laser_mode_button.clicked.connect(lambda: self._set_scan_kind("laser", apply_defaults=True))
        self.sample_mode_button.clicked.connect(lambda: self._set_scan_kind("sample", apply_defaults=True))

        # Initialisation des champs calculés
        self._refresh_scan_axis_combos()
        self._update_total_pixels()
        self._update_scan_duration()
        self._update_button_style()
        self._update_scan_mode_buttons()

    def _on_rep_return_pressed(self):
        rep = self._read_int_edit(self.rep_edit, 1)

        if rep <= 0:
            QMessageBox.warning(self, "Invalid repetitions", "# of Repetitions must be > 0.")
            old = self.rep_edit.property("last_valid_text")
            if old is not None:
                self.rep_edit.blockSignals(True)
                self.rep_edit.setText(str(old))
                self.rep_edit.blockSignals(False)
            self._update_scan_duration()
            return

        self.rep_edit.setText(str(rep))
        self.rep_edit.setProperty("last_valid_text", str(rep))
        self._update_scan_duration()
        self._on_param_changed()

    def _on_delay_return_pressed(self):
        delay = self._read_float_edit(self.delay_edit, 0.0)

        if delay < 0:
            QMessageBox.warning(self, "Invalid delay", "Delay between repetitions must be >= 0 s.")
            old = self.delay_edit.property("last_valid_text")
            if old is not None:
                self.delay_edit.blockSignals(True)
                self.delay_edit.setText(str(old))
                self.delay_edit.blockSignals(False)
            self._update_scan_duration()
            return

        self.delay_edit.setText(str(delay))
        self.delay_edit.setProperty("last_valid_text", str(delay))
        self._update_scan_duration()
        self._on_param_changed()
    
    def open_settings_dialog(self):
        from .Dialogs import SettingsDialog
        dialog = SettingsDialog("Scan - Settings", self)
        setup_scan_settings_dialog(dialog)
        dialog.exec()

    def embed_save_section(self, save_widget):
        """Embeds the save widget as a grouped section at the bottom of the scan panel."""
        count = self.main_layout.count()
        if count > 0:
            last = self.main_layout.itemAt(count - 1)
            if last and last.spacerItem() is not None:
                self.main_layout.removeItem(last)

        save_group = QGroupBox("", self)
        save_group.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        save_group.setStyleSheet(GROUPBOX_STYLE)

        group_layout = QVBoxLayout(save_group)
        group_layout.setContentsMargins(4, 4, 4, 4)
        group_layout.setSpacing(0)
        group_layout.addWidget(save_widget)

        self.main_layout.addWidget(save_group)
        self.main_layout.addStretch()

    def set_settings_manager(self, manager):
        """Injection du AxisSettingsManager partagé."""
        self.axis_settings_manager = manager

        for axis_name, defaults in SCAN_AXIS_DEFAULTS.items():
            if not self.axis_settings_manager.get_axis_settings(axis_name):
                self.axis_settings_manager.update_axis_settings(axis_name, **defaults)

        self.axis_settings = self.axis_settings_manager.get_all_axis_settings()
    
    def _update_bidir_delay_label(self):
        delay = getattr(self, "_bidir_calibrated_delay_samples", 0.0)
        if delay > 0:
            from ..managers.Scan_manager import DAQ_SAMPLE_RATE_HZ
            delay_us = delay / DAQ_SAMPLE_RATE_HZ * 1e6
            self.bidir_delay_label.setText(f"τ≈{delay_us:.1f} µs")
        else:
            self.bidir_delay_label.setText("—")

    def _on_bidirectional_shift_return_pressed(self):
        value = self._read_int_edit(self.bidirectional_shift_edit, 0)

        # borne large volontaire, à ajuster si besoin
        if value < -100000 or value > 100000:
            QMessageBox.warning(
                self,
                "Invalid bidirectional shift",
                "Bidirectional shift must be a reasonable integer value."
            )
            old = self.bidirectional_shift_edit.property("last_valid_text")
            if old is not None:
                self.bidirectional_shift_edit.blockSignals(True)
                self.bidirectional_shift_edit.setText(str(old))
                self.bidirectional_shift_edit.blockSignals(False)
            return

        self.bidirectional_shift_edit.setText(str(value))
        self.bidirectional_shift_edit.setProperty("last_valid_text", str(value))

        # Stocker le délai physique calibré (τ en samples = shift_px × spp)
        spp = self._compute_daq_samples_per_pixel_from_dwell()
        self._bidir_calibrated_delay_samples = float(abs(value) * spp)
        self._update_bidir_delay_label()

        self._on_param_changed()

    def _count_active_scan_dimensions_except(self, excluded_combo: QComboBox) -> int:
        count = 0
        for cb in self.scan_dim_combos:
            if cb is excluded_combo:
                continue
            if cb.currentText() != "None":
                count += 1
        return count
    
    def _get_active_scan_axes(self) -> list[str]:
        return [cb.currentText() for cb in self.scan_dim_combos if cb.currentText() != "None"]

    def _count_active_scan_dimensions(self) -> int:
        return len(self._get_active_scan_axes())

    def _would_exceed_dimension_limit(self, extra_dim: int = 0, replacing_none: bool = False) -> bool:
        """
        Channel occupe déjà 1 dimension.
        Il reste donc max 4 dimensions pour le scan :
        X, Y, Z, P, Rep

        extra_dim = 1 pour tester l'ajout d'une nouvelle dimension
        replacing_none = True si on passe de None -> axe actif
        """
        active_dims = self._count_active_scan_dimensions()
        rep_dim = 1 if self.rep_checkbox.isChecked() else 0

        # Si on remplace un axe déjà actif par un autre axe actif, on n'ajoute pas de dimension.
        add_axis_dim = 1 if replacing_none else 0

        total_scan_dims = active_dims + rep_dim + add_axis_dim + extra_dim
        return total_scan_dims > 4
    
    def _mode_primary_axes(self) -> tuple[str, str]:
        if self.scan_kind == "sample":
            return "X-Stage", "Y-Stage"
        return "X-Galvo", "Y-Galvo"

    def _set_combo_items(self, combo: QComboBox, items: list[str], preferred: str | None = None):
        current = combo.currentText()

        combo.blockSignals(True)
        combo.clear()
        combo.addItems(items)

        if preferred in items:
            combo.setCurrentText(preferred)
        elif current in items:
            combo.setCurrentText(current)
        elif items:
            combo.setCurrentText(items[0])

        combo.setProperty("prev_text", combo.currentText())
        combo.blockSignals(False)

    def _get_allowed_axes_for_row(self, row_index: int) -> list[str]:
        ax_x, ax_y = self._mode_primary_axes()
        current = [cb.currentText() for cb in self.scan_dim_combos]
        a0, a1, a2, a3 = current

        if row_index == 0:
            return [ax_x, ax_y]

        if row_index == 1:
            if a0 == ax_x:
                return [ax_y, "Z-Vcoil"]
            if a0 == ax_y:
                return [ax_x, "Z-Vcoil"]
            return [ax_x, ax_y, "Z-Vcoil"]

        pair = (a0, a1)
        pair_set = {a0, a1}

        is_xy_family = pair_set == {ax_x, ax_y}
        is_xz_or_yz_family = pair in ((ax_x, "Z-Vcoil"), (ax_y, "Z-Vcoil"))

        if row_index == 2:
            if is_xy_family:
                return ["Z-Vcoil", "Polarization", "None"]
            if is_xz_or_yz_family:
                return ["None"]
            return ["None"]

        if row_index == 3:
            if is_xy_family:
                if a2 == "Z-Vcoil":
                    return ["Polarization", "None"]
                if a2 == "Polarization":
                    return ["Z-Vcoil", "None"]
                return ["None"]
            return ["None"]

        return ["None"]

    def _refresh_scan_axis_combos(self):
        for i, combo in enumerate(self.scan_dim_combos):
            old_text = combo.currentText()
            allowed = self._get_allowed_axes_for_row(i)

            self._set_combo_items(combo, allowed, preferred=old_text)

            axis = combo.currentText()
            if axis == "None":
                self._disable_axis_fields(i)
            elif axis == "Polarization":
                # Verrouillé : size=180°, 19 points (step 10°), non modifiable.
                self._apply_polarization_row_lock(i)
            else:
                self.size_edits[i].setEnabled(True)
                self.pixel_edits[i].setEnabled(True)
                self.offset_edits[i].setEnabled(True)

                # Réautorise l'édition si la ligne sortait d'un verrou Polarization.
                for e in (self.size_edits[i], self.pixel_edits[i], self.offset_edits[i]):
                    e.setReadOnly(False)

                self._set_editable_lineedit_style(self.size_edits[i])
                self._set_editable_lineedit_style(self.pixel_edits[i])
                self._set_editable_lineedit_style(self.offset_edits[i])

                if not self.size_edits[i].text():
                    self.size_edits[i].setText(self.default_values[axis]["size"])
                if not self.pixel_edits[i].text():
                    self.pixel_edits[i].setText(self.default_values[axis]["pixels"])
                if not self.offset_edits[i].text():
                    self.offset_edits[i].setText(self.default_values[axis]["offset"])

                self.size_edits[i].setProperty("last_valid_text", self.size_edits[i].text())
                self.pixel_edits[i].setProperty("last_valid_text", self.pixel_edits[i].text())
                self.offset_edits[i].setProperty("last_valid_text", self.offset_edits[i].text())

                self._update_scan_mode_combo(i, axis)

                self._update_steps(self.size_edits[i], self.pixel_edits[i], self.step_edits[i])

    def _update_scan_mode_buttons(self):
        self.laser_mode_button.blockSignals(True)
        self.sample_mode_button.blockSignals(True)

        self.laser_mode_button.setChecked(self.scan_kind == "laser")
        self.sample_mode_button.setChecked(self.scan_kind == "sample")

        self.laser_mode_button.setStyleSheet(LASER_TOGGLE_STYLE)
        self.sample_mode_button.setStyleSheet(SAMPLE_TOGGLE_STYLE)

        self.laser_mode_button.blockSignals(False)
        self.sample_mode_button.blockSignals(False)
    
    def set_settle_ms(self, ms: float):
        """Reçoit le settle time partagé depuis le widget Spectro."""
        self._settle_ms = max(0.0, float(ms))
        self.settle_ms_display.setText(f"{self._settle_ms:.1f}")
        self._update_scan_duration()

    def _set_scan_kind(self, scan_kind: str, apply_defaults: bool = False):
        self.scan_kind = "sample" if str(scan_kind) == "sample" else "laser"

        if apply_defaults:
            if self.scan_kind == "sample":
                self._apply_sample_mode_defaults()
            else:
                self._apply_laser_mode_defaults()

        is_sample = (self.scan_kind == "sample")
        self.settle_label.setVisible(is_sample)
        self.settle_ms_display.setVisible(is_sample)

        self._refresh_scan_axis_combos()
        self._update_scan_mode_buttons()
        self._update_total_pixels()
        self._update_scan_duration()
        self._on_param_changed()

    def _apply_sample_mode_defaults(self):
        sample_axes = ["X-Stage", "Y-Stage", "None", "None"]

        for i, axis in enumerate(sample_axes):
            combo = self.scan_dim_combos[i]
            combo.blockSignals(True)
            combo.setCurrentText(axis)
            combo.setProperty("prev_text", axis)
            combo.blockSignals(False)

            if axis == "None":
                self._disable_axis_fields(i)
            else:
                self._enable_axis_fields(i, axis)

        # defaults prudents
        self.size_edits[0].setText("100")
        self.size_edits[1].setText("100")
        self.pixel_edits[0].setText("64")
        self.pixel_edits[1].setText("64")
        self.offset_edits[0].setText("0")
        self.offset_edits[1].setText("0")

        self.dwell_edit.setText("1000")   # 1 ms
        self.dwell_edit.setProperty("last_valid_text", "1000")

        self.samples_per_pixel_edit.setText("1")
        self.samples_per_pixel_edit.setProperty("last_valid_text", "1")

        self.bidirectional_button.setChecked(True)
        self.bidirectional_shift_edit.setText("0")
        self.bidirectional_shift_edit.setProperty("last_valid_text", "0")

        for i in range(2):
            self.size_edits[i].setProperty("last_valid_text", self.size_edits[i].text())
            self.pixel_edits[i].setProperty("last_valid_text", self.pixel_edits[i].text())
            self.offset_edits[i].setProperty("last_valid_text", self.offset_edits[i].text())
            self._update_steps(self.size_edits[i], self.pixel_edits[i], self.step_edits[i])

    def _apply_laser_mode_defaults(self):
        laser_axes = ["X-Galvo", "Y-Galvo", "None", "None"]

        for i, axis in enumerate(laser_axes):
            combo = self.scan_dim_combos[i]
            combo.blockSignals(True)
            combo.setCurrentText(axis)
            combo.setProperty("prev_text", axis)
            combo.blockSignals(False)

            if axis == "None":
                self._disable_axis_fields(i)
            else:
                self._enable_axis_fields(i, axis)

        self.size_edits[0].setText("100")
        self.size_edits[1].setText("100")
        self.pixel_edits[0].setText("256")
        self.pixel_edits[1].setText("256")
        self.offset_edits[0].setText("0")
        self.offset_edits[1].setText("0")

        self.dwell_edit.setText("10")
        self.dwell_edit.setProperty("last_valid_text", "10")

        self.samples_per_pixel_edit.setText("1")
        self.samples_per_pixel_edit.setProperty("last_valid_text", "1")

        self.bidirectional_button.setChecked(False)
        self.bidirectional_shift_edit.setText("0")
        self.bidirectional_shift_edit.setEnabled(False)
        self._set_disabled_lineedit_style(self.bidirectional_shift_edit)
        self.bidirectional_shift_edit.setProperty("last_valid_text", "0")

        for i in range(2):
            self.size_edits[i].setProperty("last_valid_text", self.size_edits[i].text())
            self.pixel_edits[i].setProperty("last_valid_text", self.pixel_edits[i].text())
            self.offset_edits[i].setProperty("last_valid_text", self.offset_edits[i].text())
            self._update_steps(self.size_edits[i], self.pixel_edits[i], self.step_edits[i])
    
    def get_scan_kind(self) -> str:
        return self.scan_kind

    def _is_stage_axis(self, axis_name: str) -> bool:
        return axis_name in ("X-Stage", "Y-Stage", "Z-Vcoil", "Polarization")
    
    def _is_laser_xy_primary(self) -> bool:
        """
        True si les 2 premiers axes actifs sont les galvos X/Y (ordre quelconque).
        """
        active_axes = self._get_active_scan_axes()
        if len(active_axes) < 2:
            return False

        pair = {active_axes[0], active_axes[1]}
        return pair == {"X-Galvo", "Y-Galvo"}

    def _should_skip_stage_speed_check(self, row_index: int, axis_name: str) -> bool:
        """
        Pour un vrai Z-stack laser :
        - les 2 premiers axes actifs sont X/Y galvo
        - Z-Vcoil est un axe supplémentaire (row >= 2)
        Dans ce cas, Z ne bouge pas au rythme du dwell pixel,
        donc le test de vitesse step/dwell est faux et doit être ignoré.
        """
        if axis_name != "Z-Vcoil":
            return False

        if self.scan_kind != "laser":
            return False

        if row_index < 2:
            return False

        return self._is_laser_xy_primary()
    
    def _reset_to_defaults(self):
        """Réinitialise complètement le widget selon le mode courant."""
        if self.scan_kind == "sample":
            self._apply_sample_mode_defaults()
        else:
            self._apply_laser_mode_defaults()

        self.rep_checkbox.setChecked(False)
        self.rep_edit.setText("1")
        self.delay_edit.setText("0")
        self.rep_edit.setProperty("last_valid_text", "1")
        self.delay_edit.setProperty("last_valid_text", "0")
        self.laser_checkbox.setChecked(True)

        self.rep_edit.setEnabled(False)
        self.delay_edit.setEnabled(False)
        self.laser_checkbox.setEnabled(False)

        self._set_disabled_lineedit_style(self.rep_edit)
        self._set_disabled_lineedit_style(self.delay_edit)

        self._refresh_scan_axis_combos()
        self._update_total_pixels()
        self._update_scan_duration()
        self._on_param_changed()

    def _on_bidirectional_toggled(self, checked):
        """Gère l'état du bouton Bidirectional."""
        if checked:
            self.bidirectional_shift_edit.setEnabled(True)
            self._set_editable_lineedit_style(self.bidirectional_shift_edit)
        else:
            self.bidirectional_shift_edit.setText("0")
            self.bidirectional_shift_edit.setEnabled(False)
            self._set_disabled_lineedit_style(self.bidirectional_shift_edit)
            self.bidirectional_shift_edit.setProperty("last_valid_text", "0")

        self._on_param_changed()

    def _set_editable_lineedit_style(self, line_edit: QLineEdit):
        line_edit.setStyleSheet(EDITABLE_LINEEDIT_STYLE)

    def _set_disabled_lineedit_style(self, line_edit: QLineEdit):
        line_edit.setStyleSheet(READONLY_LINEEDIT_STYLE)
    
    def _on_rep_checkbox_toggled(self, checked):
        """Active/désactive les champs liés aux répétitions avec contrôle de limite de dimensions."""
        if checked:
            # Activer Rep ajoute 1 dimension de scan.
            if self._count_active_scan_dimensions() >= 4:
                QMessageBox.warning(
                    self,
                    "Invalid configuration",
                    "You cannot enable Repetitions here.\n"
                    "Channel already reserves one dimension, so only 4 scan dimensions are allowed.\n"
                    "Please disable one scan axis first."
                )
                self.rep_checkbox.blockSignals(True)
                self.rep_checkbox.setChecked(False)
                self.rep_checkbox.blockSignals(False)
                checked = False

        if checked:
            self.rep_edit.setEnabled(True)
            self.delay_edit.setEnabled(True)
            self.laser_checkbox.setEnabled(True)

            self._set_editable_lineedit_style(self.rep_edit)
            self._set_editable_lineedit_style(self.delay_edit)
        else:
            self.rep_edit.setText("1")
            self.delay_edit.setText("0")
            self.laser_checkbox.setChecked(True)

            self.rep_edit.setEnabled(False)
            self.delay_edit.setEnabled(False)
            self.laser_checkbox.setEnabled(False)

            self._set_disabled_lineedit_style(self.rep_edit)
            self._set_disabled_lineedit_style(self.delay_edit)

        self._on_param_changed()
    
    def _on_update_view_clicked(self):
        params = self.get_scan_parameters()
        self.view_update_requested.emit(params)
        self._reset_button_style()

    def _compute_daq_samples_per_pixel_from_dwell(self) -> int:
        dwell_us = self._read_float_edit(self.dwell_edit, 0.0)

        if dwell_us <= 0:
            return 1

        dwell_s = dwell_us * 1e-6
        return max(1, int(__import__("math").ceil(dwell_s * DAQ_SAMPLE_RATE_HZ - 1e-12)))


    def _update_daq_samples_per_pixel_display(self):
        if not hasattr(self, "samples_per_pixel_edit"):
            return

        spp = self._compute_daq_samples_per_pixel_from_dwell()
        new = str(int(spp))

        if self.samples_per_pixel_edit.text() == new:
            return

        self.samples_per_pixel_edit.blockSignals(True)
        self.samples_per_pixel_edit.setText(new)
        self.samples_per_pixel_edit.setProperty("last_valid_text", new)
        self.samples_per_pixel_edit.blockSignals(False)    
    
    def get_estimated_scan_duration_s(self) -> float:
        """Durée estimée d'UNE acquisition (le champ 'duration', en s), telle
        qu'affichée. Utilisée par le stitching pour estimer le temps total."""
        try:
            return max(0.0, float((self.duration_edit.text() or "0").replace(",", ".")))
        except Exception:
            return 0.0

    def _update_scan_duration(self):
        """Met à jour la durée de scan."""
        try:
            dwell_us = float(self.dwell_edit.text() or "0")
            self._update_daq_samples_per_pixel_display()

            if dwell_us <= 0:
                self.duration_edit.setText("0")
                return

            active_rows = [i for i, cb in enumerate(self.scan_dim_combos) if cb.currentText() != "None"]
            if len(active_rows) < 2:
                self.duration_edit.setText("0")
                return

            row_fast = active_rows[0]
            row_slow = active_rows[1]

            pix_fast = int(float(self.pixel_edits[row_fast].text() or "1"))
            pix_slow = int(float(self.pixel_edits[row_slow].text() or "1"))

            if pix_fast <= 0 or pix_slow <= 0:
                self.duration_edit.setText("0")
                return

            frame_flyback_time_s = 0.0

            if self.scan_kind == "laser":
                overscan_fraction = self._get_fast_axis_overscan_fraction()
                frame_flyback_time_s = self._get_frame_flyback_time_s()

                lead_px = int(round(overscan_fraction * pix_fast))
                trail_px = lead_px
                pix_fast_scanned = pix_fast + lead_px + trail_px
                scanned_pixels_total = pix_fast_scanned * pix_slow

                extra_factor = 1
                for i in active_rows[2:]:
                    px = int(float(self.pixel_edits[i].text() or "1"))
                    extra_factor *= max(1, px)
                scanned_pixels_total *= extra_factor

                base_duration = (
                    dwell_us * scanned_pixels_total / 1_000_000
                ) + (frame_flyback_time_s * extra_factor)

            else:
                # Sample scanning : inclure le temps de déplacement de platine + settling par pixel
                settle_s = self._settle_ms / 1000.0

                fast_axis = self.scan_dim_combos[row_fast].currentText()
                slow_axis = self.scan_dim_combos[row_slow].currentText()
                step_fast_um = max(0.0, self._read_float_edit(self.step_edits[row_fast], 0.0))
                step_slow_um = max(0.0, self._read_float_edit(self.step_edits[row_slow], 0.0))

                vel_fast_um_s = 4000.0
                vel_slow_um_s = 4000.0
                if self.axis_settings_manager is not None:
                    s = self.axis_settings_manager.get_axis_settings(fast_axis) or {}
                    dflt = STEPPER_AXIS_DEFAULTS.get(fast_axis, {})
                    vel_fast_um_s = max(1.0, float(s.get("vel_max", dflt.get("vel_max", 4.0)))) * 1000.0
                    s = self.axis_settings_manager.get_axis_settings(slow_axis) or {}
                    dflt = STEPPER_AXIS_DEFAULTS.get(slow_axis, {})
                    vel_slow_um_s = max(1.0, float(s.get("vel_max", dflt.get("vel_max", 4.0)))) * 1000.0

                # X se déplace à chaque pixel ; Y se déplace une fois par ligne (amorti sur pix_fast)
                move_s = 0.0
                if step_fast_um > 0 and vel_fast_um_s > 0:
                    move_s += step_fast_um / vel_fast_um_s
                if step_slow_um > 0 and vel_slow_um_s > 0 and pix_fast > 0:
                    move_s += (step_slow_um / vel_slow_um_s) / pix_fast

                time_per_pixel_s = dwell_us * 1e-6 + settle_s + move_s

                extra_factor = 1
                for i in active_rows[2:]:
                    px = int(float(self.pixel_edits[i].text() or "1"))
                    extra_factor *= max(1, px)

                base_duration = time_per_pixel_s * pix_fast * pix_slow * extra_factor

            if self.rep_checkbox.isChecked():
                repetitions = self._read_int_edit(self.rep_edit, 1)
                delay = self._read_float_edit(self.delay_edit, 0.0)

                if repetitions <= 0:
                    repetitions = 1
                if delay < 0:
                    delay = 0.0

                duration = base_duration * repetitions + max(0, repetitions - 1) * delay
            else:
                duration = base_duration

            self.duration_edit.setText(f"{duration:.3f}")

        except ValueError:
            self.duration_edit.setText("0")

    def validate_axis_scan_vs_limits(self, axis_name: str, conv: float, vmin: float, vmax: float) -> tuple[bool, str]:
        # lire size/offset actuellement affichés dans la grille pour cet axe
        size_edit = getattr(self, f"size_edit_{axis_name.replace('-', '_')}", None)
        offset_edit = getattr(self, f"offset_edit_{axis_name.replace('-', '_')}", None)
        if size_edit is None or offset_edit is None:
            return True, ""  # si axe pas dans la grille (ou renommage), on skip

        try:
            size_um = float(size_edit.text().replace(",", "."))
            rel_off_um = float(offset_edit.text().replace(",", "."))
        except Exception:
            return True, ""

        current_pos_um = self._get_axis_current_position_um(axis_name)
        center_um = current_pos_um + rel_off_um

        lo_v = (center_um - size_um / 2.0) / conv
        hi_v = (center_um + size_um / 2.0) / conv

        if lo_v < vmin or hi_v > vmax:
            return False, (
                f"{axis_name}: scan requires [{lo_v:.2f}, {hi_v:.2f}] V "
                f"but limits are [{vmin:.2f}, {vmax:.2f}] V "
                f"(size={size_um}µm, current_pos={current_pos_um}µm, "
                f"relative_offset={rel_off_um}µm, center={center_um}µm, conv={conv}µm/V)"
            )
        return True, ""
    
    def validate_axis_setting_values(
        self,
        axis_name: str,
        conv: float,
        vmin: float,
        vmax: float,
        vel: float,
        overscan_percent: float | None = None,
        frame_flyback_ms: float | None = None,
    ) -> tuple[bool, str]:
        if conv <= 0:
            return False, f"{axis_name}: Conversion factor must be > 0 (µm/V)."

        if vmin >= vmax:
            return False, f"{axis_name}: Min Voltage must be < Max Voltage."

        if overscan_percent is not None:
            if overscan_percent < 0 or overscan_percent > 30:
                return False, f"{axis_name}: Overscan must be between 0 and 30 (%)."

        if frame_flyback_ms is not None:
            if frame_flyback_ms < 0 or frame_flyback_ms > 100:
                return False, f"{axis_name}: Frame flyback must be between 0 and 100 ms."

        if vel <= 0:
            return False, f"{axis_name}: Velocity max must be > 0 (mm/s)."

        return True, ""
    
    def _on_param_changed_if_xy(self, changed_row: int):
        ix, iy = self._get_xy_row_indices()
        # si pas 2 axes actifs, on ne force pas le bouton (ou tu peux choisir de l’allumer)
        if ix is None:
            return
        if changed_row in (ix, iy):
            self._on_param_changed()   # allume le bouton
    
    def _get_xy_row_indices(self):
        """Retourne les indices de lignes (0..3) qui correspondent aux 2 premiers axes actifs."""
        active_rows = [i for i, cb in enumerate(self.scan_dim_combos) if cb.currentText() != "None"]
        if len(active_rows) < 2:
            return None, None
        return active_rows[0], active_rows[1]

    def _get_fast_axis_overscan_fraction(self) -> float:
        """
        Retourne l'overscan de l'axe rapide (1er axe actif) depuis les settings.
        Ne code aucune valeur en dur hors fallback ultime sur SCAN_AXIS_DEFAULTS.
        """
        ix, _ = self._get_xy_row_indices()
        if ix is None:
            return 0.0

        fast_axis = self.scan_dim_combos[ix].currentText()
        if fast_axis == "None":
            return 0.0

        defaults = SCAN_AXIS_DEFAULTS.get(fast_axis, {})
        default_overscan = float(defaults.get("overscan_fraction", 0.0))

        if self.axis_settings_manager is None:
            return default_overscan

        s = self.axis_settings_manager.get_axis_settings(fast_axis) or {}
        return float(s.get("overscan_fraction", default_overscan))
    
    def _get_frame_flyback_time_s(self) -> float:
        """
        Retourne le frame flyback de l'axe lent (2ème axe actif) depuis les settings.
        """
        _, iy = self._get_xy_row_indices()
        if iy is None:
            return 0.0

        axis_name = self.scan_dim_combos[iy].currentText()
        if axis_name == "None":
            return 0.0

        defaults = SCAN_AXIS_DEFAULTS.get(axis_name, {})
        default_value = float(defaults.get("frame_flyback_time_s", 0.0))

        if self.axis_settings_manager is None:
            return default_value

        s = self.axis_settings_manager.get_axis_settings(axis_name) or {}
        return max(0.0, float(s.get("frame_flyback_time_s", default_value) or 0.0))
    
    def get_xy_pixels(self):
        """Retourne pix_x, pix_y (basé sur les 2 premiers axes actifs)."""
        ix, iy = self._get_xy_row_indices()
        if ix is None:
            return 1, 1
        try:
            pix_x = int(float(self.pixel_edits[ix].text() or "1"))
            pix_y = int(float(self.pixel_edits[iy].text() or "1"))
        except ValueError:
            pix_x, pix_y = 1, 1
        return pix_x, pix_y
    
    def _read_float_edit(self, edit: QLineEdit, default: float = 0.0) -> float:
        try:
            return float((edit.text() or str(default)).replace(",", "."))
        except Exception:
            return float(default)

    def _get_axis_current_position_um(self, axis_name: str) -> float:
        """Position ABSOLUE actuelle de l'axe (Positioner). Sert au contrôle
        des limites device (bornes absolues)."""
        if axis_name == "None":
            return 0.0
        if self.axis_settings_manager is None:
            return 0.0
        return float(self.axis_settings_manager.get_axis_position_um(axis_name))

    def _get_axis_current_relative_position_um(self, axis_name: str) -> float:
        """Position RELATIVE actuelle de l'axe (repère set-0). Sert de base au
        scan : on balaye autour/à partir du relatif, pas de l'absolu.
        Pour les galvos (sans platine) le relatif vaut 0."""
        if axis_name == "None":
            return 0.0
        if self.axis_settings_manager is None:
            return 0.0
        return float(self.axis_settings_manager.get_axis_relative_position_um(axis_name))
    
    def _read_int_edit(self, edit: QLineEdit, default: int = 1) -> int:
        try:
            return int(float((edit.text() or str(default)).replace(",", ".")))
        except Exception:
            return int(default)
    
    def _validate_scan_row_values(self, row_index: int) -> tuple[bool, str]:
        axis_name = self.scan_dim_combos[row_index].currentText()

        if axis_name == "None":
            return True, ""

        size_um = self._read_float_edit(self.size_edits[row_index], 0.0)
        pixels = self._read_int_edit(self.pixel_edits[row_index], 1)
        offset_um = self._read_float_edit(self.offset_edits[row_index], 0.0)
        dwell_us = self._read_float_edit(self.dwell_edit, 0.0)

        if size_um < 0:
            return False, f"{axis_name}: Size must be >= 0 µm."
        if pixels <= 0:
            return False, f"{axis_name}: # Pix must be > 0."
        if dwell_us <= 0:
            return False, "Dwell Time must be > 0 µs."

        if self._is_stage_axis(axis_name):
            settings = self.axis_settings_manager.get_axis_settings(axis_name) if self.axis_settings_manager is not None else {}
            defaults = STEPPER_AXIS_DEFAULTS.get(axis_name, {})

            min_um = float(settings.get("min_um", defaults.get("min_um", -1e9)))
            max_um = float(settings.get("max_um", defaults.get("max_um", 1e9)))
            vel_max_mm_s = float(settings.get("vel_max", defaults.get("vel_max", 1.0)))

            current_pos_um = self._get_axis_current_position_um(axis_name)
            center_um = current_pos_um + offset_um

            mode = self._get_row_scan_mode(row_index, axis_name)
            lo_um, hi_um = self._scan_range_for_mode(center_um, size_um, axis_name, mode)

            if lo_um < min_um or hi_um > max_um:
                return False, (
                    f"{axis_name}: requested scan exceeds stage limits.\n"
                    f"Current position = {current_pos_um:.2f} µm, relative offset = {offset_um:.2f} µm\n"
                    f"Mode = {mode}, requested range = [{lo_um:.2f}, {hi_um:.2f}] µm\n"
                    f"Allowed range = [{min_um:.2f}, {max_um:.2f}] µm."
                )

            # Pour un Z-stack laser (XY raster + Z en axe supplémentaire),
            # Z-Vcoil ne bouge pas au rythme du dwell pixel mais entre les frames.
            # Le test step/dwell est donc faux dans ce cas, on le saute.
            if self._should_skip_stage_speed_check(row_index, axis_name):
                return True, ""

            step_um = size_um / (pixels - 1) if pixels > 1 else size_um
            dwell_s = dwell_us * 1e-6
            speed_um_s = step_um / dwell_s if dwell_s > 0 else float("inf")
            speed_mm_s = speed_um_s / 1000.0

            if speed_mm_s > vel_max_mm_s:
                return False, (
                    f"{axis_name}: requested speed is too high.\n"
                    f"Step = {step_um:.4f} µm, Dwell = {dwell_us:.4f} µs\n"
                    f"Estimated speed = {speed_mm_s:.4f} mm/s\n"
                    f"Velocity max = {vel_max_mm_s:.4f} mm/s."
                )

            return True, ""

        s = self.axis_settings_manager.get_axis_settings(axis_name) if self.axis_settings_manager is not None else {}
        conv = float(s.get("conv_um_per_v", 20.0))
        vmin = float(s.get("vmin", -5.0))
        vmax = float(s.get("vmax", 5.0))
        vel_max_mm_s = float(s.get("vel_max", 1.0))

        if conv <= 0:
            return False, f"{axis_name}: invalid conversion factor."

        current_pos_um = self._get_axis_current_position_um(axis_name)
        center_um = current_pos_um + offset_um

        lo_um = center_um - size_um / 2.0
        hi_um = center_um + size_um / 2.0
        lo_v = lo_um / conv
        hi_v = hi_um / conv

        if lo_v < vmin or hi_v > vmax:
            max_span_um = (vmax - vmin) * conv
            return False, (
                f"{axis_name}: requested scan exceeds hardware limits.\n"
                f"Current position = {current_pos_um:.2f} µm, relative offset = {offset_um:.2f} µm\n"
                f"Scan center = {center_um:.2f} µm\n"
                f"Requested range = [{lo_um:.2f}, {hi_um:.2f}] µm "
                f"-> [{lo_v:.2f}, {hi_v:.2f}] V\n"
                f"Allowed voltage range = [{vmin:.2f}, {vmax:.2f}] V\n"
                f"With conv = {conv:.2f} µm/V, max full span is {max_span_um:.2f} µm."
            )

        step_um = size_um / (pixels - 1) if pixels > 1 else size_um
        dwell_s = dwell_us * 1e-6
        speed_um_s = step_um / dwell_s if dwell_s > 0 else float("inf")
        speed_mm_s = speed_um_s / 1000.0

        if speed_mm_s > vel_max_mm_s:
            return False, (
                f"{axis_name}: requested speed is too high.\n"
                f"Step = {step_um:.4f} µm, Dwell = {dwell_us:.4f} µs\n"
                f"Estimated speed = {speed_mm_s:.4f} mm/s\n"
                f"Velocity max = {vel_max_mm_s:.4f} mm/s."
            )

        return True, ""

    def _validate_row_and_revert_if_needed(self, row_index: int):
        axis_name = self.scan_dim_combos[row_index].currentText()
        if axis_name == "None":
            return

        ok, msg = self._validate_scan_row_values(row_index)
        if not ok:
            QMessageBox.warning(self, "Invalid scan parameters", msg)

            for edit in (self.size_edits[row_index], self.pixel_edits[row_index], self.offset_edits[row_index]):
                old = edit.property("last_valid_text")
                if old is not None:
                    edit.blockSignals(True)
                    edit.setText(str(old))
                    edit.blockSignals(False)

            self._update_steps(
                self.size_edits[row_index],
                self.pixel_edits[row_index],
                self.step_edits[row_index]
            )
            self._update_total_pixels()
            self._update_scan_duration()
            return

        # si c'est valide, on mémorise les valeurs
        self.size_edits[row_index].setProperty("last_valid_text", self.size_edits[row_index].text())
        self.pixel_edits[row_index].setProperty("last_valid_text", self.pixel_edits[row_index].text())
        self.offset_edits[row_index].setProperty("last_valid_text", self.offset_edits[row_index].text())

        self._update_steps(
            self.size_edits[row_index],
            self.pixel_edits[row_index],
            self.step_edits[row_index]
        )
        self._update_total_pixels()
        self._update_scan_duration()
        self._on_param_changed()
    
    def _on_dwell_return_pressed(self):
        dwell_us = self._read_float_edit(self.dwell_edit, 0.0)

        if dwell_us <= 0:
            QMessageBox.warning(self, "Invalid dwell time", "Dwell Time must be > 0 µs.")
            old = self.dwell_edit.property("last_valid_text")
            if old is not None:
                self.dwell_edit.blockSignals(True)
                self.dwell_edit.setText(str(old))
                self.dwell_edit.blockSignals(False)
            self._update_scan_duration()
            return

        if dwell_us < DAQ_SAMPLE_PERIOD_US - 1e-12:
            QMessageBox.warning(
                self,
                "Invalid dwell time",
                f"Dwell Time is too short for fixed DAQ sampling.\n\n"
                f"DAQ sampling = {DAQ_SAMPLE_RATE_HZ / 1e6:.3f} MHz\n"
                f"DAQ period = {DAQ_SAMPLE_PERIOD_US:.3f} µs\n"
                f"Minimum dwell time = {DAQ_SAMPLE_PERIOD_US:.3f} µs."
            )
            old = self.dwell_edit.property("last_valid_text")
            if old is not None:
                self.dwell_edit.blockSignals(True)
                self.dwell_edit.setText(str(old))
                self.dwell_edit.blockSignals(False)
            self._update_scan_duration()
            return
        
        # Vérifie toutes les lignes actives avec ce nouveau dwell
        for i, combo in enumerate(self.scan_dim_combos):
            if combo.currentText() == "None":
                continue
            ok, msg = self._validate_scan_row_values(i)
            if not ok:
                QMessageBox.warning(self, "Invalid dwell time", msg)
                old = self.dwell_edit.property("last_valid_text")
                if old is not None:
                    self.dwell_edit.blockSignals(True)
                    self.dwell_edit.setText(str(old))
                    self.dwell_edit.blockSignals(False)
                self._update_scan_duration()
                return

        self.dwell_edit.setProperty("last_valid_text", self.dwell_edit.text())
        self._update_scan_duration()
        self._on_param_changed()
    
    def _on_samples_per_pixel_return_pressed(self):
        spp = self._read_int_edit(self.samples_per_pixel_edit, 1)

        if spp <= 0:
            QMessageBox.warning(self, "Invalid samples/pixel", "Samples / Pixel must be > 0.")
            old = self.samples_per_pixel_edit.property("last_valid_text")
            if old is not None:
                self.samples_per_pixel_edit.blockSignals(True)
                self.samples_per_pixel_edit.setText(str(old))
                self.samples_per_pixel_edit.blockSignals(False)
            self._update_scan_duration()
            return

        self.samples_per_pixel_edit.setProperty("last_valid_text", self.samples_per_pixel_edit.text())
        self._update_scan_duration()
        self._on_param_changed()

    def get_scan_parameters(self):
        """Récupère tous les paramètres de scan sous forme de dictionnaire."""
        # Récupérer les valeurs de #Pix pour tous les axes actifs
        pixel_values = []
        for pixel_edit in self.pixel_edits:
            pixel_values.append(int(float(pixel_edit.text() or "1")))

        dwell_time = float(self.dwell_edit.text() or "0") / 1_000_000  # Convertir le dwell_time de microsecondes en secondes
        self._update_daq_samples_per_pixel_display()
        samples_per_pixel = self._compute_daq_samples_per_pixel_from_dwell()

        # Récupérer tous les axes actifs
        active_axes = []
        for combo in self.scan_dim_combos:
            if combo.currentText() != "None":
                active_axes.append(combo.currentText())

        # Récupérer les valeurs des champs de conversion factor, min voltage, max voltage, etc.
        conversion_factors = {}
        min_voltages = {}
        max_voltages = {}
        velocity_max = {}

        overscan_fraction = self._get_fast_axis_overscan_fraction()
        frame_flyback_time_s = self._get_frame_flyback_time_s()

        for combo in self.scan_dim_combos:
            axis_name = combo.currentText()
            if axis_name == "None":
                continue

            s = self.axis_settings_manager.get_axis_settings(axis_name) if self.axis_settings_manager is not None else {}
            defaults = SCAN_AXIS_DEFAULTS.get(axis_name, {})

            conversion_factors[axis_name] = float(s.get("conv_um_per_v", defaults.get("conv_um_per_v", 20.0)))
            min_voltages[axis_name] = float(s.get("vmin", defaults.get("vmin", -10.0)))
            max_voltages[axis_name] = float(s.get("vmax", defaults.get("vmax", 10.0)))
            velocity_max[axis_name] = float(s.get("vel_max", defaults.get("vel_max", 1.0)))

        # Backlash X pour le mode sample serpentin (lu depuis les settings X-Stage)
        backlash_x_um = 0.0
        backlash_x_forward_um = 0.0
        if self.axis_settings_manager is not None:
            x_stage_cfg = self.axis_settings_manager.get_axis_settings("X-Stage")
            backlash_x_um = float(x_stage_cfg.get("backlash_um", 0.0))
            backlash_x_forward_um = float(x_stage_cfg.get("backlash_forward_um", 0.0))

        # Récupérer les autres paramètres
        bidirectional_scan = self.bidirectional_button.isChecked()
        bidirectional_shift_px = self._read_int_edit(self.bidirectional_shift_edit, 0) if bidirectional_scan else 0
        if self.rep_checkbox.isChecked():
            repetitions = int(self.rep_edit.text() or "1")
            delay_between_rep = float(self.delay_edit.text() or "0")
            laser_off_between_rep = self.laser_checkbox.isChecked()
        else:
            repetitions = 1
            delay_between_rep = 0.0
            laser_off_between_rep = False

        # Récupérer sizes/offsets/steps de manière robuste (par ligne)
        rows = []
        sizes = {}
        offsets = {}    # base relative du scan (pos relative courante + offset UI)
        relative_offsets = {}    # offsets relatifs saisis dans le widget
        current_positions = {}   # positions RELATIVES courantes (Positioner)
        step_sizes = {}
        scan_modes = {}    # "around" / "from" par axe (stack Z/P)

        axis_order = [cb.currentText() for cb in self.scan_dim_combos]

        for i, combo in enumerate(self.scan_dim_combos):
            axis = combo.currentText()

            # pixels
            try:
                px = int(float(self.pixel_edits[i].text() or "1"))
            except Exception:
                px = 1

            # size
            try:
                sz = float((self.size_edits[i].text() or "0").replace(",", "."))
            except Exception:
                sz = 0.0

            # offset relatif saisi dans le ScanWidget
            try:
                rel_off = float((self.offset_edits[i].text() or "0").replace(",", "."))
            except Exception:
                rel_off = 0.0

            # Base du scan = position RELATIVE courante (repère set-0), pas
            # l'absolue : le pipeline (StepEvent.target_rel -> move_from_scan ->
            # rel_to_abs) travaille en relatif. Utiliser l'absolu doublait le
            # zero_offset (Z abs 500 -> scan autour de 1000). Bug corrigé.
            current_pos = self._get_axis_current_relative_position_um(axis) if axis != "None" else 0.0
            off = current_pos + rel_off

            # step (champ non éditable déjà calculé dans le widget)
            try:
                step = float((self.step_edits[i].text() or "0").replace(",", "."))
            except Exception:
                step = 0.0

            rows.append({
                "axis": axis,
                "pixels": px,
                "size_um": sz,
                "offset_um": off,                # offset absolu utilisé par le scan
                "relative_offset_um": rel_off,   # offset relatif saisi dans l'UI
                "current_position_um": current_pos,
                "step_um": step,
            })

            if axis != "None":
                sizes[axis] = sz
                offsets[axis] = off
                relative_offsets[axis] = rel_off
                current_positions[axis] = current_pos
                step_sizes[axis] = step
                if i < len(self.scan_mode_combos) and self._is_stack_mode_axis(axis):
                    scan_modes[axis] = self.scan_mode_combos[i].currentText().strip().lower()
                else:
                    scan_modes[axis] = "around"

        total_pixels = 1
        for row in rows:
            if row["axis"] != "None":
                total_pixels *= max(1, int(row["pixels"]))

        return {
            "rows": rows,
            "pixel_values": pixel_values,
            "dwell_time": dwell_time,
            "samples_per_pixel": samples_per_pixel,
            "scan_kind": self.scan_kind,
            "pixel_source_kind": "analog_integrating",
            "sample_settle_time_s": self._settle_ms / 1000.0,
            "active_axes": active_axes,
            "axis_order": axis_order,
            "conversion_factors": conversion_factors,
            "min_voltages": min_voltages,
            "max_voltages": max_voltages,
            "velocity_max": velocity_max,
            "overscan_fraction": overscan_fraction,
            "frame_flyback_time_s": frame_flyback_time_s,
            "bidirectional_scan": bidirectional_scan,
            "bidirectional_shift_px": int(bidirectional_shift_px),
            "backlash_x_um": backlash_x_um,
            "backlash_x_forward_um": backlash_x_forward_um,
            "repetitions": repetitions,
            "delay_between_rep": delay_between_rep,
            "laser_off_between_rep": laser_off_between_rep,
            "sizes": sizes,
            "offsets": offsets,                       # base relative du scan
            "relative_offsets": relative_offsets,     # UI
            "current_positions": current_positions,   # positions relatives
            "initial_relative_positions": dict(current_positions),
            "scan_modes": scan_modes,                 # "around"/"from" par axe
            "step_sizes": step_sizes,
            "total_pixels": total_pixels,
        }

    def _reset_button_style(self):
        """Réinitialise le style du bouton quand on clique dessus."""
        self.params_changed = False
        self._update_button_style()

    def _update_total_pixels(self):
        """Calcule le nombre total de pixels en multipliant les dimensions actives."""
        total_pixels = 1

        for i, combo in enumerate(self.scan_dim_combos):
            pixel_edit = self.pixel_edits[i]
            if combo.currentText() != "None":
                try:
                    pixels = int(float(pixel_edit.text() or "1"))
                    total_pixels *= pixels
                except ValueError:
                    pass

        self.total_pixels_edit.setText(str(total_pixels))
        self._update_scan_duration()

    def _update_pixel_default(self, pixel_edit, selected_dim):
        """Met à jour la valeur par défaut du champ # Pix selon l'axe sélectionné."""
        if selected_dim == "None":
            pixel_edit.setText("")
        else:
            pixel_edit.setText(self.default_values[selected_dim]["pixels"])

        self._update_total_pixels()

    def _is_xyzp_active(self) -> bool:
        active_axes = [c.currentText() for c in self.scan_dim_combos if c.currentText() != "None"]
        return ("Z-Vcoil" in active_axes) and ("Polarization" in active_axes)

    def _on_scan_axis_changed(self, combo: QComboBox, pixel_edit: QLineEdit, new_text: str):
        prev = combo.property("prev_text") or "None"
        row_index = self.scan_dim_combos.index(combo)

        allowed = self._get_allowed_axes_for_row(row_index)
        if new_text not in allowed:
            combo.blockSignals(True)
            combo.setCurrentText(prev if prev in allowed else allowed[0])
            combo.blockSignals(False)
            return

        prev_was_none = (prev == "None")
        new_is_none = (new_text == "None")

        if new_is_none:
            self._disable_axis_fields(row_index)
            combo.setProperty("prev_text", new_text)
            self._refresh_scan_axis_combos()
            self._update_total_pixels()
            self._update_scan_duration()
            self._on_param_changed()
            return

        if prev_was_none:
            rep_dim = 1 if self.rep_checkbox.isChecked() else 0
            active_axes_except_current = self._count_active_scan_dimensions_except(combo)

            if active_axes_except_current + 1 + rep_dim > 4:
                QMessageBox.warning(
                    self,
                    "Invalid configuration",
                    "You cannot activate this scan axis.\n"
                    "Channel already reserves one dimension, so only 4 scan dimensions are allowed.\n"
                    "Please disable Repetitions or one scan axis first."
                )
                combo.blockSignals(True)
                combo.setCurrentText(prev)
                combo.blockSignals(False)
                return

        self.size_edits[row_index].setEnabled(True)
        self.pixel_edits[row_index].setEnabled(True)
        self.offset_edits[row_index].setEnabled(True)

        self._set_editable_lineedit_style(self.size_edits[row_index])
        self._set_editable_lineedit_style(self.pixel_edits[row_index])
        self._set_editable_lineedit_style(self.offset_edits[row_index])

        if prev != new_text:
            self.size_edits[row_index].setText(self.default_values[new_text]["size"])
            self.pixel_edits[row_index].setText(self.default_values[new_text]["pixels"])
            self.offset_edits[row_index].setText(self.default_values[new_text]["offset"])

        self.size_edits[row_index].setProperty("last_valid_text", self.size_edits[row_index].text())
        self.pixel_edits[row_index].setProperty("last_valid_text", self.pixel_edits[row_index].text())
        self.offset_edits[row_index].setProperty("last_valid_text", self.offset_edits[row_index].text())

        self._update_steps(
            self.size_edits[row_index],
            self.pixel_edits[row_index],
            self.step_edits[row_index]
        )

        combo.setProperty("prev_text", new_text)

        self._refresh_scan_axis_combos()
        self._update_total_pixels()
        self._update_scan_duration()
        self._on_param_changed()
        
    def _enable_axis_fields(self, row_index, axis):
        """Active les champs pour les axes actifs avec les valeurs par défaut."""
        self.size_edits[row_index].setEnabled(True)
        self._set_editable_lineedit_style(self.size_edits[row_index])
        self.pixel_edits[row_index].setEnabled(True)
        self._set_editable_lineedit_style(self.pixel_edits[row_index])
        self.offset_edits[row_index].setEnabled(True)
        self._set_editable_lineedit_style(self.offset_edits[row_index])

        self.size_edits[row_index].setText(self.default_values[axis]["size"])
        self.pixel_edits[row_index].setText(self.default_values[axis]["pixels"])
        self.offset_edits[row_index].setText(self.default_values[axis]["offset"])

        self.size_edits[row_index].setProperty("last_valid_text", self.size_edits[row_index].text())
        self.pixel_edits[row_index].setProperty("last_valid_text", self.pixel_edits[row_index].text())
        self.offset_edits[row_index].setProperty("last_valid_text", self.offset_edits[row_index].text())

        self._update_steps(self.size_edits[row_index], self.pixel_edits[row_index], self.step_edits[row_index])
    
    def _disable_axis_fields(self, row_index):
        """Désactive et grise les champs pour les axes non actifs."""
        self.size_edits[row_index].setEnabled(False)
        self._set_disabled_lineedit_style(self.size_edits[row_index])
        self.pixel_edits[row_index].setEnabled(False)
        self._set_disabled_lineedit_style(self.pixel_edits[row_index])
        self.offset_edits[row_index].setEnabled(False)
        self._set_disabled_lineedit_style(self.offset_edits[row_index])

        self.size_edits[row_index].clear()
        self.pixel_edits[row_index].clear()
        self.offset_edits[row_index].clear()
        self.step_edits[row_index].clear()

        self._update_scan_mode_combo(row_index, "None")

    def _apply_polarization_row_lock(self, row_index):
        """Axe Polarization : valeurs imposées et non modifiables (pour l'instant).

        size = 180°, 19 points -> pas de 10°. L'axe balaie l'AZIMUT de 0 à 180° ;
        les positions physiques des lames (λ/2, λ/4) sont dérivées de la table de
        calibration au moment du scan (cf. Positioner_Manager)."""
        for edit, val in (
            (self.size_edits[row_index], "180"),
            (self.pixel_edits[row_index], "19"),
            (self.offset_edits[row_index], "0"),
        ):
            edit.blockSignals(True)
            edit.setText(val)
            edit.setProperty("last_valid_text", val)
            edit.blockSignals(False)
            edit.setEnabled(True)
            edit.setReadOnly(True)
            self._set_disabled_lineedit_style(edit)

        self._update_scan_mode_combo(row_index, "Polarization")
        self._update_steps(
            self.size_edits[row_index],
            self.pixel_edits[row_index],
            self.step_edits[row_index],
        )

    def _is_stack_mode_axis(self, axis_name: str) -> bool:
        """Axes platine 'stack' pour lesquels le mode Around/From s'applique."""
        return axis_name in ("Z-Vcoil", "Polarization")

    def _get_row_scan_mode(self, row_index: int, axis_name: str) -> str:
        """Mode de balayage ('around'/'from') de la ligne pour un axe stack."""
        if not self._is_stack_mode_axis(axis_name):
            return "around"
        if row_index >= len(self.scan_mode_combos):
            return "around"
        return self.scan_mode_combos[row_index].currentText().strip().lower()

    def _scan_range_for_mode(self, center_um: float, size_um: float,
                             axis_name: str, mode: str) -> tuple[float, float]:
        """Bornes [lo, hi] du balayage selon le mode (around/from) et le sens
        de l'axe (Z-Vcoil descend, les autres montent)."""
        if str(mode) == "from":
            if axis_name == "Z-Vcoil":
                return center_um - size_um, center_um
            return center_um, center_um + size_um
        return center_um - size_um / 2.0, center_um + size_um / 2.0

    def _update_scan_mode_combo(self, row_index, axis_name):
        """Active la combobox Mode pour les axes stack (Z/P), la désactive et
        la remet sur 'Around' sinon."""
        if row_index >= len(self.scan_mode_combos):
            return
        combo = self.scan_mode_combos[row_index]
        enabled = self._is_stack_mode_axis(axis_name)
        combo.setEnabled(enabled)
        combo.setStyleSheet(SCAN_COMBO_STYLE if enabled else SCAN_COMBO_DISABLED_STYLE)
        if not enabled and combo.currentText() != "Around":
            combo.blockSignals(True)
            combo.setCurrentText("Around")
            combo.blockSignals(False)
    
    def _on_param_changed(self):
        """Marque que les paramètres ont changé."""
        self.params_changed = True
        self._update_button_style()

    def _update_steps(self, size_edit, pixel_edit, step_edit):
        """Met à jour la taille de pas (µm) à partir de Size et # Pix."""
        try:
            size = float(size_edit.text() or "0")
            pixel = float(pixel_edit.text() or "1")
            if pixel > 1:
                step = size / (pixel - 1)
                step_edit.setText(f"{step:.3f}")
            elif pixel == 1:
                step_edit.setText(f"{size:.3f}")
            else:
                step_edit.setText("0")
        except ValueError:
            step_edit.setText("0")

    def _update_button_style(self):
        """Met à jour le style du bouton en fonction des changements."""
        if hasattr(self, "params_changed") and self.params_changed:
            self.apply_button.setStyleSheet(APPLY_BUTTON_STYLE)
        else:
            self.apply_button.setStyleSheet(BUTTON_STYLE)