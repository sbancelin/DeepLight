from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QGridLayout, QLabel, QLineEdit, QMessageBox, QSizePolicy, QCheckBox, QApplication)
from PySide6.QtCore import Signal, Qt, Slot, QEvent
from PySide6.QtGui import QIcon
from functools import partial

from ..managers.Scan_Types import STEPPER_AXIS_DEFAULTS
from .Log_Widget import logger

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

def setup_positioner_settings_dialog(dialog):
    """Construit le dialogue de configuration des limites/vitesses/tolérances des axes stepper."""
    positioner_widget = dialog.parent()

    axes = [
        # axis_key, axis_label, shared_axis_name, pos_unit, vel_unit, has_ums_scaling, has_backlash
        ("x", "X - stage", "X-Stage",      "µm", "mm/s", True,  True),
        ("y", "Y - stage", "Y-Stage",      "µm", "mm/s", True,  True),
        ("z", "Z-VCoil",   "Z-Vcoil",      "µm", "mm/s", False, False),
        ("p", "P",         "Polarization", "°",  "°/s",  False, False),
    ]

    # ---- build UI avec valeurs actuelles ----
    for axis_key, axis_label, shared_axis_name, pos_unit, vel_unit, has_ums, has_backlash in axes:
        axis_title_container = QWidget()
        axis_title_layout = QVBoxLayout(axis_title_container)
        axis_title_layout.setContentsMargins(0, 0, 0, 0)
        axis_title_layout.setSpacing(2)

        separator = QLabel()
        separator.setFrameShape(QLabel.HLine)
        separator.setFrameShadow(QLabel.Sunken)
        separator.setStyleSheet("color: #555; margin-top: 0px; margin-bottom: 5px;")
        dialog.add_widget(separator)

        # Ligne 1: Nom de l'axe
        axis_title_layout.addWidget(QLabel(f"<b>{axis_label}</b>"))
        dialog.add_widget(axis_title_container)

        # valeurs actuelles stockées dans le widget
        axis_cfg = {}
        if positioner_widget.axis_settings_manager is not None:
            axis_cfg = positioner_widget.axis_settings_manager.get_axis_settings(shared_axis_name)

        defaults = STEPPER_AXIS_DEFAULTS.get(shared_axis_name, {})

        cur_min = float(axis_cfg.get("min_um", defaults.get("min_um", positioner_widget.axis_limits[axis_key]["min"])))
        cur_max = float(axis_cfg.get("max_um", defaults.get("max_um", positioner_widget.axis_limits[axis_key]["max"])))
        cur_vmax = float(axis_cfg.get("vel_max", defaults.get("vel_max", positioner_widget.axis_velocity_limits[axis_key]["max"])))

        # Min/Max
        position_layout = QHBoxLayout()
        min_edit = QLineEdit(str(cur_min))
        max_edit = QLineEdit(str(cur_max))
        min_edit.setObjectName(f"min_position_edit_{axis_key}")
        max_edit.setObjectName(f"max_position_edit_{axis_key}")

        position_layout.addWidget(QLabel(f"Min Position ({pos_unit}):"))
        position_layout.addWidget(min_edit)
        position_layout.addWidget(QLabel(f"Max Position ({pos_unit}):"))
        position_layout.addWidget(max_edit)
        dialog.add_layout(position_layout)

        # Speed
        vel_layout = QHBoxLayout()
        vel_edit = QLineEdit(str(cur_vmax))
        vel_edit.setObjectName(f"velocity_edit_{axis_key}")
        vel_layout.addWidget(QLabel(f"Velocity max ({vel_unit}):"))
        vel_layout.addWidget(vel_edit)
        dialog.add_layout(vel_layout)

        # Backlash (X/Y) ou Tolerance (Z/P)
        if has_backlash:
            cur_backlash = float(axis_cfg.get("backlash_um", defaults.get("backlash_um", 1.2)))
            backlash_layout = QHBoxLayout()
            backlash_edit = QLineEdit(str(cur_backlash))
            backlash_edit.setObjectName(f"backlash_edit_{axis_key}")
            backlash_edit.setToolTip(
                "Correction de jeu mécanique (µm).\n"
                "En mode serpentin, le stage dépasse de cette valeur\n"
                "le premier pixel de chaque ligne inversée avant de revenir."
            )
            backlash_layout.addWidget(QLabel(f"Backlash ({pos_unit}):"))
            backlash_layout.addWidget(backlash_edit)
            dialog.add_layout(backlash_layout)
        else:
            cur_tol = float(axis_cfg.get("tolerance", defaults.get("tolerance", 0.1)))
            tol_layout = QHBoxLayout()
            tol_edit = QLineEdit(str(cur_tol))
            tol_edit.setObjectName(f"tolerance_edit_{axis_key}")
            tol_layout.addWidget(QLabel(f"Tolerance ({pos_unit}):"))
            tol_layout.addWidget(tol_edit)
            dialog.add_layout(tol_layout)

        # UMS scaling factor (X et Y seulement)
        if has_ums:
            ums_value = float(axis_cfg.get(
                "ums_scaling",
                defaults.get("ums_scaling", 1.0)
            ))
            ums_layout = QHBoxLayout()
            ums_edit = QLineEdit(str(ums_value))
            ums_edit.setObjectName(f"ums_scaling_edit_{axis_key}")
            ums_edit.setToolTip(
                "Scientifica UMS calibration factor:\n"
                "real_motion_µm = firmware_command_µm × factor\n"
                "Default = 0.639. Set to 1.0 to disable scaling on this axis."
            )
            ums_layout.addWidget(QLabel("UMS scaling factor:"))
            ums_layout.addWidget(ums_edit)
            dialog.add_layout(ums_layout)

    # ---- apply on accept ----
    def on_dialog_accepted():
        for axis_key, _axis_label, shared_axis_name, _pos_unit, _vel_unit, has_ums, has_backlash in axes:
            min_edit = dialog.findChild(QLineEdit, f"min_position_edit_{axis_key}")
            max_edit = dialog.findChild(QLineEdit, f"max_position_edit_{axis_key}")
            vel_edit = dialog.findChild(QLineEdit, f"velocity_edit_{axis_key}")

            if not (min_edit and max_edit and vel_edit):
                continue

            min_position = float(min_edit.text().replace(",", "."))
            max_position = float(max_edit.text().replace(",", "."))
            velocity_limit = float(vel_edit.text().replace(",", "."))

            # update widget cache
            positioner_widget.axis_limits[axis_key]["min"] = min_position
            positioner_widget.axis_limits[axis_key]["max"] = max_position
            positioner_widget.axis_velocity_limits[axis_key]["max"] = velocity_limit

            if has_backlash:
                backlash_edit_w = dialog.findChild(QLineEdit, f"backlash_edit_{axis_key}")
                backlash_um = max(0.0, float(backlash_edit_w.text().replace(",", "."))) if backlash_edit_w else 1.2

                if positioner_widget.axis_settings_manager is not None:
                    positioner_widget.axis_settings_manager.update_axis_settings(
                        shared_axis_name,
                        min_um=min_position,
                        max_um=max_position,
                        vel_max=velocity_limit,
                        backlash_um=backlash_um,
                    )

                if positioner_widget.manager:
                    positioner_widget.manager.set_limits(
                        axis_key, min_position, max_position, velocity_limit, 0.1
                    )
            else:
                tol_edit = dialog.findChild(QLineEdit, f"tolerance_edit_{axis_key}")
                tolerance = float(tol_edit.text().replace(",", ".")) if tol_edit else 0.1

                if positioner_widget.axis_settings_manager is not None:
                    positioner_widget.axis_settings_manager.update_axis_settings(
                        shared_axis_name,
                        min_um=min_position,
                        max_um=max_position,
                        vel_max=velocity_limit,
                        tolerance=tolerance,
                    )

                if positioner_widget.manager:
                    positioner_widget.manager.set_limits(
                        axis_key, min_position, max_position, velocity_limit, tolerance
                    )

            # UMS scaling factor : sauvegarde + push manager
            if has_ums:
                ums_edit_w = dialog.findChild(QLineEdit, f"ums_scaling_edit_{axis_key}")
                if ums_edit_w is not None:
                    try:
                        ums_value = float(ums_edit_w.text().replace(",", "."))
                    except ValueError:
                        ums_value = 1.0
                    if ums_value <= 0.0:
                        ums_value = 1.0

                    if positioner_widget.axis_settings_manager is not None:
                        positioner_widget.axis_settings_manager.update_axis_settings(
                            shared_axis_name,
                            ums_scaling=ums_value,
                        )

        # Propagation des facteurs UMS X+Y au manager (qui propage au controller XY)
        if positioner_widget.manager is not None and positioner_widget.axis_settings_manager is not None:
            try:
                sx = float(
                    positioner_widget.axis_settings_manager
                    .get_axis_settings("X-Stage").get("ums_scaling", 1.0)
                )
                sy = float(
                    positioner_widget.axis_settings_manager
                    .get_axis_settings("Y-Stage").get("ums_scaling", 1.0)
                )
                positioner_widget.manager.set_ums_scaling_factors(sx, sy)
            except Exception as e:
                logger.error(f"[PositionerWidget] set_ums_scaling_factors failed: {e}")

    dialog.accepted.connect(on_dialog_accepted)

class PositionerWidget(QWidget):
    """Widget pour les contrôles des détecteurs."""
    stepperPositionForVisualizer = Signal(str, float)  # axis label UI, relative position

    def __init__(self, manager=None, parent=None):
        
        super().__init__(parent)
        self.main_layout = QVBoxLayout(self)
        self.main_layout.setContentsMargins(6, 6, 6, 6)
        self.main_layout.setSpacing(6)

        self.manager = None  # injecté
        self.axis_settings_manager = None
        self.axis_ui = {}
        self.axis_keys = ["x", "y", "z", "p"]
        self.axis_labels = {"x": "X - stage", "y": "Y - stage", "z": "Z-VCoil", "p": "P"}
        self._buttons_connected = False
        self._keyboard_shortcuts_locked = True

        # lien entre label UI et clé d’axe manager
        self._label_to_axis = {
            "X - stage": "x",
            "Y - stage": "y",
            "Z-VCoil": "z",
            "P": "p",
        }
        self._axis_to_stepper_visualizer_name = {
            "x": "X-Stage",
            "y": "Y-Stage",
            "z": "Z-Vcoil",
            "p": "Polarization",
        }
        # Limites par défaut des axes
        self.axis_limits = {
            "x": {"min": -20000, "max": 20000},
            "y": {"min": -20000, "max": 20000},
            "z": {"min": 0, "max": 7000},
            "p": {"min": 0, "max": 180}
        }
        self.axis_velocity_limits = {
            "x": {"min": 0.01, "max": 4},
            "y": {"min": 0.01, "max": 4},
            "z": {"min": 0.01, "max": 200},
            "p": {"min": 0.01, "max": 100}
        }

        content_widget = QWidget()
        content_widget.setMinimumWidth(0)
        content_widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)

        content_layout = QVBoxLayout(content_widget)
        content_layout.setContentsMargins(2, 2, 2, 2)
        content_layout.setSpacing(4)

        # Layout pour les paramètres (grid)
        grid_layout = QGridLayout()
        grid_layout.setContentsMargins(0, 0, 0, 0)
        grid_layout.setHorizontalSpacing(4)
        grid_layout.setVerticalSpacing(4)

        grid_layout.setColumnMinimumWidth(0, 20)   # Axis
        grid_layout.setColumnMinimumWidth(2, 20)   # Position
        grid_layout.setColumnMinimumWidth(5, 20)   # Step
        grid_layout.setColumnMinimumWidth(7, 20)   # Speed
        grid_layout.setColumnMinimumWidth(8, 20)   # Abs Pos

        grid_layout.setColumnStretch(0, 0)
        grid_layout.setColumnStretch(1, 0)
        grid_layout.setColumnStretch(2, 1)
        grid_layout.setColumnStretch(3, 0)
        grid_layout.setColumnStretch(4, 0)
        grid_layout.setColumnStretch(5, 1)
        grid_layout.setColumnStretch(6, 0)
        grid_layout.setColumnStretch(7, 1)
        grid_layout.setColumnStretch(8, 1)

        # En-têtes
        headers = ["Axis", "Home", "Pos (µm)", "Set 0", "", "Step (µm)", "", "Speed (mm/s)", "Abs Pos"]
        for col, header in enumerate(headers):
            label = QLabel(header)
            label.setStyleSheet("color: white; font-weight: bold; padding-bottom: 5px;")
            label.setAlignment(Qt.AlignCenter)
            grid_layout.addWidget(label, 0, col)

        # Axes et champs éditables
        pos_axis = ["X - stage", "Y - stage", "Z-VCoil", "P"]
        self.pos_edits = {}
        
        for row, pos in enumerate(pos_axis, 1):
            # Colonne 0: Labels
            positioner_label = QLabel(pos)
            positioner_label.setStyleSheet("color: white;")
            positioner_label.setAlignment(Qt.AlignVCenter | Qt.AlignLeft)
            positioner_label.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
            grid_layout.addWidget(positioner_label, row, 0)

            # Colonne 1: Bouton Home
            home_button = QPushButton()
            home_button.setFixedSize(40, 25)
            home_button.setIcon(QIcon.fromTheme(QIcon.ThemeIcon.GoHome))
            home_button.setStyleSheet("""
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
            """)
            home_button.setToolTip("Home")
            grid_layout.addWidget(home_button, row, 1)
            setattr(self, f"home_button_{pos.lower().replace('-', '_')}", home_button)

            # Colonne 2: Position
            pos_edit = QLineEdit("0")
            pos_edit.setStyleSheet("""
                QLineEdit {
                    background-color: #333;
                    color: white;
                    border: 1px solid #555;
                    border-radius: 3px;
                    padding: 2px;
                    min-height: 20px;
                }
            """)
            pos_edit.setMinimumWidth(20)
            pos_edit.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            grid_layout.addWidget(pos_edit, row, 2)
            setattr(self, f"pos_edit_{pos.lower().replace('-', '_')}", pos_edit)

            # Colonne 3: Bouton Set 0 avec icône
            set_zero_button = QPushButton("Set 0")
            set_zero_button.setFixedSize(40, 25)
            set_zero_button.setStyleSheet("""
                QPushButton {
                    background-color: #333;
                    color: white;
                    border: 1px solid #555;
                    border-radius: 3px;
                    padding: 2px;
                }
                QPushButton:hover {
                    background-color: #444;
                }
            """)
            set_zero_button.setToolTip("Set Zero")
            grid_layout.addWidget(set_zero_button, row, 3)
            setattr(self, f"set_zero_button_{pos.lower().replace('-', '_')}", set_zero_button)

            # Colonne 4: Bouton -
            minus_button = QPushButton("-")
            minus_button.setFixedSize(20, 20)
            minus_button.setStyleSheet("""
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
            """)
            grid_layout.addWidget(minus_button, row, 4)
            setattr(self, f"minus_button_{pos.lower().replace('-', '_')}", minus_button)

            # Colonne 5: Step
            step_edit = QLineEdit("100")
            step_edit.setStyleSheet("""
                QLineEdit {
                    background-color: #333;
                    color: white;
                    border: 1px solid #555;
                    border-radius: 3px;
                    padding: 2px;
                    min-height: 20px;
                }
            """)
            step_edit.setMinimumWidth(20)
            step_edit.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            grid_layout.addWidget(step_edit, row, 5)
            setattr(self, f"step_edit_{pos.lower().replace('-', '_')}", step_edit)

            # Colonne 6: Bouton +
            plus_button = QPushButton("+")
            plus_button.setFixedSize(20, 20)
            plus_button.setStyleSheet("""
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
            """)
            grid_layout.addWidget(plus_button, row, 6)
            setattr(self, f"plus_button_{pos.lower().replace('-', '_')}", plus_button)

            # Colonne 7: Speed
            if pos in ("X - stage", "Y - stage"):
                default_speed = "3.8"
            elif pos == "Z-VCoil":
                default_speed = "200"
            else:
                default_speed = "1"
            speed_edit = QLineEdit(default_speed)
            if pos in ("X - stage", "Y - stage"):
                speed_edit.setReadOnly(True)
                speed_edit.setFocusPolicy(Qt.NoFocus)
                speed_edit.setStyleSheet("""
                    QLineEdit {
                        background-color: #252525;
                        color: #888;
                        border: 1px solid #444;
                        border-radius: 3px;
                        padding: 2px;
                        min-height: 20px;
                    }
                """)
            else:
                speed_edit.setStyleSheet("""
                    QLineEdit {
                        background-color: #333;
                        color: white;
                        border: 1px solid #555;
                        border-radius: 3px;
                        padding: 2px;
                        min-height: 20px;
                    }
                """)
            speed_edit.setMinimumWidth(20)
            speed_edit.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            grid_layout.addWidget(speed_edit, row, 7)
            setattr(self, f"speed_edit_{pos.lower().replace('-', '_')}", speed_edit)

            # Colonne 8: Actual position
            abs_pos_edit = QLineEdit("0.00")
            abs_pos_edit.setReadOnly(True)  # Rend le champ en lecture seule
            abs_pos_edit.setStyleSheet("""
                QLineEdit {
                    background-color: #252525;
                    color: #888;
                    border: 1px solid #444;
                    border-radius: 3px;
                    padding: 2px;
                    min-height: 20px;
                }
            """)
            abs_pos_edit.setMinimumWidth(20)
            abs_pos_edit.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            grid_layout.addWidget(abs_pos_edit, row, 8)
            setattr(self, f"abs_pos_edit_{pos.lower().replace('-', '_')}", abs_pos_edit)
            self.pos_edits[pos] = abs_pos_edit

            axis = self._label_to_axis[pos]

            self.axis_ui[axis] = {
                "home": home_button,
                "set0": set_zero_button,
                "minus": minus_button,
                "plus": plus_button,
                "pos": pos_edit,       # Position relative (éditable)
                "abs": abs_pos_edit,   # Position absolue (lecture seule)
                "step": step_edit,
                "speed": speed_edit,
            }

        content_layout.addLayout(grid_layout)

        # ---- bottom control row ----
        bottom_row = QHBoxLayout()
        bottom_row.setContentsMargins(0, 0, 0, 0)
        bottom_row.setSpacing(6)

        # checkbox shortcuts
        self.keyboard_shortcuts_lock_checkbox = QCheckBox("Lock shortcuts")
        self.keyboard_shortcuts_lock_checkbox.setChecked(True)
        self.keyboard_shortcuts_lock_checkbox.setToolTip(
            "When checked, keyboard arrows and +/- cannot move the stages."
        )
        self.keyboard_shortcuts_lock_checkbox.setStyleSheet(CHECKBOX_STYLE)
        self.keyboard_shortcuts_lock_checkbox.toggled.connect(self.set_keyboard_shortcuts_locked)

        # stop button
        self.stop_all_button = QPushButton("Stop All")
        self.stop_all_button.setFixedHeight(24)
        self.stop_all_button.setStyleSheet("""
                QPushButton {
                    background-color: #333;
                    color: white;
                    border: 1px solid #555;
                    border-radius: 3px;
                    padding: 2px 6px;
                }
                QPushButton:hover {
                    background-color: #444;
                }
            """)

        bottom_row.addWidget(self.stop_all_button)
        bottom_row.addWidget(self.keyboard_shortcuts_lock_checkbox)

        content_layout.addLayout(bottom_row)

        self.main_layout.addWidget(content_widget)
        self.main_layout.addStretch()

        QApplication.instance().installEventFilter(self)

        if manager is not None:
            self.set_manager(manager)

    def open_settings_dialog(self):
        from .Dialogs import SettingsDialog
        dialog = SettingsDialog("Positioner - Settings", self)
        setup_positioner_settings_dialog(dialog)
        dialog.exec()
    
    def set_limits(self, axis, min_limit, max_limit, velocity_limit):
        """Définir les limites pour un axe donné."""
        self.axis_limits[axis]["min"] = min_limit
        self.axis_limits[axis]["max"] = max_limit
        self.axis_velocity_limits[axis]["max"] = velocity_limit
    
    def set_settings_manager(self, manager):
        """Injection du AxisSettingsManager partagé."""
        self.axis_settings_manager = manager

        for axis_name, defaults in STEPPER_AXIS_DEFAULTS.items():
            if not self.axis_settings_manager.get_axis_settings(axis_name):
                self.axis_settings_manager.update_axis_settings(axis_name, **defaults)

        self._load_stepper_settings_from_manager()
    
    def _load_stepper_settings_from_manager(self):
        if self.axis_settings_manager is None:
            return

        axis_map = {
            "x": "X-Stage",
            "y": "Y-Stage",
            "z": "Z-Vcoil",
            "p": "Polarization",
        }

        for axis_key, shared_name in axis_map.items():
            s = self.axis_settings_manager.get_axis_settings(shared_name)

            if axis_key in ("x", "y"):
                self.axis_limits[axis_key]["min"] = -20000.0
                self.axis_limits[axis_key]["max"] = 20000.0
                self.axis_velocity_limits[axis_key]["min"] = 0.01
                self.axis_velocity_limits[axis_key]["max"] = 3.8

                ui = self.axis_ui.get(axis_key)
                if ui is not None:
                    ui["speed"].setText("3.8")
            else:
                self.axis_limits[axis_key]["min"] = float(s.get("min_um", self.axis_limits[axis_key]["min"]))
                self.axis_limits[axis_key]["max"] = float(s.get("max_um", self.axis_limits[axis_key]["max"]))
                self.axis_velocity_limits[axis_key]["max"] = float(s.get("vel_max", self.axis_velocity_limits[axis_key]["max"]))

            tol = float(s.get("tolerance", 0.1))

            if self.manager is not None:
                try:
                    self.manager.set_limits(
                        axis_key,
                        self.axis_limits[axis_key]["min"],
                        self.axis_limits[axis_key]["max"],
                        self.axis_velocity_limits[axis_key]["max"],
                        tol,
                    )
                except Exception:
                    pass

        # Push UMS scaling factors to the manager / hardware at startup
        if self.manager is not None:
            try:
                sx_cfg = self.axis_settings_manager.get_axis_settings("X-Stage") or {}
                sy_cfg = self.axis_settings_manager.get_axis_settings("Y-Stage") or {}
                sx = float(sx_cfg.get("ums_scaling", 1.0))
                sy = float(sy_cfg.get("ums_scaling", 1.0))
                self.manager.set_ums_scaling_factors(sx, sy)
            except Exception:
                pass
    
    def set_manager(self, manager):
        """Permet d’injecter (ou remplacer) le manager après construction."""
        self.manager = manager

        # IMPORTANT: pousser les limites/settings dans le manager
        self._load_stepper_settings_from_manager()

        # manager -> GUI
        self.manager.relPositionChanged.connect(self._on_rel_position_changed)
        self.manager.absPositionChanged.connect(self._on_abs_position_changed)

        try:
            self.manager.movingChanged.connect(self._on_moving_changed)
        except Exception:
            pass

        # GUI -> manager
        self._connect_buttons_to_manager()

        # sync initial display
        for axis in self.axis_ui.keys():
            try:
                self._on_rel_position_changed(axis, float(self.manager.get_rel_pos(axis)))
            except Exception:
                pass
            try:
                self._on_abs_position_changed(axis, float(self.manager.get_abs_pos(axis)))
            except Exception:
                pass

    def set_keyboard_shortcuts_locked(self, locked: bool):
        self._keyboard_shortcuts_locked = bool(locked)

        try:
            if hasattr(self, "keyboard_shortcuts_lock_checkbox"):
                self.keyboard_shortcuts_lock_checkbox.blockSignals(True)
                self.keyboard_shortcuts_lock_checkbox.setChecked(bool(locked))
                self.keyboard_shortcuts_lock_checkbox.blockSignals(False)
        except Exception:
            pass

    def keyboard_shortcuts_locked(self) -> bool:
        return bool(self._keyboard_shortcuts_locked)

    def _focused_widget_blocks_shortcuts(self) -> bool:
        fw = QApplication.focusWidget()
        if fw is None:
            return False

        # Si on édite un champ texte, on laisse les touches au champ
        return isinstance(fw, QLineEdit)

    def _validate_axis_speed(self, axis: str, ui: dict) -> bool:
        speed = self._read_float(ui["speed"], 0.0)

        if speed > self.axis_velocity_limits[axis]["max"]:
            QMessageBox.warning(
                self,
                "Vitesse invalide",
                f"La vitesse {speed} pour l'axe {axis} dépasse la limite autorisée "
                f"({self.axis_velocity_limits[axis]['max']} mm/s)."
            )
            ui["speed"].setText(str(self.axis_velocity_limits[axis]["max"]))
            return False

        return True

    def _move_axis_step(self, axis: str, direction: int):
        if self.manager is None:
            return

        ui = self.axis_ui.get(axis)
        if ui is None:
            return

        if not self._validate_axis_speed(axis, ui):
            return

        step = self._read_float(ui["step"], 0.0)
        speed = self._read_float(ui["speed"], 0.0)

        delta = abs(step) * (1 if int(direction) > 0 else -1)

        current_rel = self.manager.get_rel_pos(axis)
        target_rel = current_rel + delta

        if not self.manager.is_rel_target_allowed(axis, target_rel):
            min_abs, max_abs = self.manager.get_limits(axis)
            QMessageBox.warning(
                self,
                "Position invalide",
                f"La cible relative {target_rel:.2f} pour l'axe {axis} est hors limites "
                f"(plage absolue device : {min_abs:.2f} à {max_abs:.2f})."
            )
            return

        self.manager.move_relative(axis, delta, speed)
    
    def eventFilter(self, obj, event):
        if event.type() == QEvent.KeyPress:
            if self.keyboard_shortcuts_locked():
                return super().eventFilter(obj, event)

            if self._focused_widget_blocks_shortcuts():
                return super().eventFilter(obj, event)

            key = event.key()

            # X : gauche / droite
            if key == Qt.Key_Left:
                self._move_axis_step("x", -1)
                return True

            if key == Qt.Key_Right:
                self._move_axis_step("x", +1)
                return True

            # Y : haut / bas
            if key == Qt.Key_Up:
                self._move_axis_step("y", +1)
                return True

            if key == Qt.Key_Down:
                self._move_axis_step("y", -1)
                return True

            # Z : Page Up / Page Down
            if key == Qt.Key_PageUp:
                self._move_axis_step("z", +1)
                return True

            if key == Qt.Key_PageDown:
                self._move_axis_step("z", -1)
                return True

        return super().eventFilter(obj, event)
    
    def _connect_buttons_to_manager(self):
        if self.manager is None:
            return
        if self._buttons_connected:
            return
        self._buttons_connected = True

        for axis, ui in self.axis_ui.items():
            def do_home(*_arg, a=axis):
                self.manager.home(a)

            ui["home"].clicked.connect(do_home)

            ui["set0"].clicked.connect(partial(self.manager.set_zero, axis))

            def go_to_typed_position(*_arg, a=axis, u=ui):
                if not self._validate_axis_speed(a, u):
                    return

                rel_target = self._read_float(u["pos"], 0.0)
                speed = self._read_float(u["speed"], 0.0)

                if not self.manager.is_rel_target_allowed(a, rel_target):
                    min_abs, max_abs = self.manager.get_limits(a)
                    target_abs = self.manager.rel_to_abs(a, rel_target)
                    QMessageBox.warning(
                        self,
                        "Position invalide",
                        f"La position absolue correspondante {target_abs:.2f} pour l'axe {a} "
                        f"dépasse les limites autorisées ({min_abs:.2f} à {max_abs:.2f})."
                    )
                    return

                self.manager.move_to_rel(a, rel_target, speed)

            ui["speed"].editingFinished.connect(
                lambda a=axis, u=ui: self._validate_axis_speed(a, u)
            )
            ui["plus"].clicked.connect(
                lambda *_arg, a=axis: self._move_axis_step(a, +1)
            )
            ui["minus"].clicked.connect(
                lambda *_arg, a=axis: self._move_axis_step(a, -1)
            )
            ui["pos"].returnPressed.connect(go_to_typed_position)

        self.stop_all_button.clicked.connect(self.manager.stop_all)

    def stop_all_axes(self):
        """Arrête le mouvement de tous les axes."""
        if self.manager is not None:
            for axis in self.axis_ui.keys():
                self.manager.stop(axis)
    
    def _read_float(self, lineedit: QLineEdit, default: float = 0.0) -> float:
        try:
            return float(lineedit.text().replace(",", "."))
        except Exception:
            return float(default)

    @Slot(str, float)
    def _on_rel_position_changed(self, axis: str, rel: float):
        ui = self.axis_ui.get(axis)
        if ui is None:
            return

        # Toujours refléter la position réelle pour éviter un affichage figé après Home
        ui["pos"].setText(f"{rel:.2f}")

        visu_name = self._axis_to_stepper_visualizer_name.get(axis)
        if visu_name is not None:
            self.stepperPositionForVisualizer.emit(visu_name, float(rel))

    @Slot(str, float)
    def _on_abs_position_changed(self, axis: str, abs_pos: float):
        ui = self.axis_ui.get(axis)
        if ui is None:
            return

        # Colonne "Abs Pos" (absolue)
        ui["abs"].setText(f"{abs_pos:.2f}")

        if self.axis_settings_manager is not None:
            shared_axis_name = self._axis_to_stepper_visualizer_name.get(axis)
            if shared_axis_name is not None:
                self.axis_settings_manager.set_axis_position_um(shared_axis_name, float(abs_pos))

    @Slot(str, bool)
    def _on_moving_changed(self, axis: str, moving: bool):
        ui = self.axis_ui.get(axis)
        if ui is None:
            return

        is_xy_pair = axis in ("x", "y")

        try:
            ui["plus"].setEnabled(not bool(moving))
            ui["minus"].setEnabled(not bool(moving))
            ui["set0"].setEnabled(not bool(moving))
        except Exception:
            pass

        # home on Scientifica is device-level XY, so keep both buttons visually coherent
        if is_xy_pair:
            for ax2 in ("x", "y"):
                ui2 = self.axis_ui.get(ax2)
                if ui2 is None:
                    continue
                try:
                    ui2["home"].setEnabled(not bool(moving))
                except Exception:
                    pass
        else:
            try:
                ui["home"].setEnabled(not bool(moving))
            except Exception:
                pass