"""Helper panel: raise the laser power with depth during a Z stack."""

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QGridLayout, QLabel, QLineEdit, QComboBox,
    QPushButton, QSizePolicy,
)
from PySide6.QtCore import Signal
from PySide6.QtGui import QDoubleValidator

from ..managers.Depth_Compensation import (
    DepthCompensation,
    compute_power_profile,
    depth_axis_um,
    format_power_table,
    max_reachable_depth_um,
    parse_power_table,
    validate_power_profile,
)
from .Log_Widget import logger

#: Only this axis is a depth. A polarisation or wavelength stack is not.
DEPTH_AXIS = "Z-Vcoil"

#: Lasers whose power can be ramped, in the order shown.
RAMPABLE_LASERS = ("Mira 900", "Tumecs", "Alcor 920", "Cobolt 660")

LINE_EDIT_STYLE = """
    QLineEdit {
        background-color: #333;
        color: white;
        border: 1px solid #555;
        border-radius: 3px;
        padding: 2px;
        min-height: 20px;
    }
    QLineEdit:disabled {
        background-color: #1e1e1e;
        color: #555;
        border: 1px solid #333;
    }
"""

READONLY_STYLE = """
    QLineEdit {
        background-color: #252525;
        color: #888;
        border: 1px solid #444;
        border-radius: 3px;
        padding: 2px;
        min-height: 20px;
    }
"""

COMBO_STYLE = """
    QComboBox {
        background-color: #333;
        color: white;
        border: 1px solid #555;
        border-radius: 3px;
        font-weight: bold;
        padding: 2px;
        min-height: 20px;
    }
    QComboBox:disabled {
        background-color: #1e1e1e;
        color: #555;
    }
"""

ACTIVATE_STYLE = """
    QPushButton {
        background-color: #333;
        color: white;
        border: 1px solid #555;
        border-radius: 3px;
        padding: 2px;
        font-weight: bold;
        min-height: 24px;
    }
    QPushButton:checked {
        background-color: #FF7700;
        border: 1px solid #FF9200;
    }
    QPushButton:disabled {
        background-color: #1e1e1e;
        color: #555;
        border: 1px solid #333;
    }
"""


class DepthCompensationWidget(QWidget):
    """Compensate depth attenuation by ramping the laser power over a Z stack."""

    #: (laser_name, percent) -- same contract as LaserWidget.laser_power_changed,
    #: so the existing command queue carries it to the hardware.
    powerRampRequested = Signal(str, float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)

        self._laser_widget = None
        self._z_active = False
        self._z_size_um = 0.0
        self._z_pixels = 1
        #: Power at the top of the stack, captured when the ramp is armed.
        self._base_percent = 0.0

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(2, 2, 2, 2)
        main_layout.setSpacing(4)

        # --- Activate, at the top: it is the control you reach for.
        self.activate_button = QPushButton("Activate")
        self.activate_button.setCheckable(True)
        self.activate_button.setStyleSheet(ACTIVATE_STYLE)
        self.activate_button.setEnabled(False)
        self.activate_button.setToolTip(
            "Available only when Z-Vcoil is an active scan axis: there is no\n"
            "depth to compensate along a polarisation stack."
        )
        self.activate_button.toggled.connect(self._on_activate_toggled)
        main_layout.addWidget(self.activate_button)

        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(6)
        grid.setVerticalSpacing(4)
        grid.setColumnStretch(0, 0)
        grid.setColumnStretch(1, 1)

        # --- mode
        grid.addWidget(QLabel("Mode"), 0, 0)
        self.mode_combo = QComboBox()
        self.mode_combo.addItems(["Exponential", "Table"])
        self.mode_combo.setStyleSheet(COMBO_STYLE)
        self.mode_combo.setToolTip(
            "Exponential: P(z) = P(0)·exp(µz) from the surface power.\n"
            "Table: measured powers read off a list, for a sample that does\n"
            "not follow Beer-Lambert. The table gives absolute percentages,\n"
            "so the surface power is not used."
        )
        self.mode_combo.currentTextChanged.connect(self._on_mode_changed)
        grid.addWidget(self.mode_combo, 0, 1)

        # --- attenuation
        self.attenuation_label = QLabel("Attenuation (µm⁻¹)")
        grid.addWidget(self.attenuation_label, 1, 0)
        self.attenuation_edit = QLineEdit("0.005")
        self.attenuation_edit.setStyleSheet(LINE_EDIT_STYLE)
        self.attenuation_edit.setValidator(QDoubleValidator(0.0, 10.0, 6))
        self.attenuation_edit.setToolTip(
            "Attenuation coefficient of the excitation beam in the sample, in µm⁻¹.\n"
            "Beer-Lambert: the focus receives I(0)·exp(-µz), so the incident power\n"
            "is raised by exp(+µz) to keep the intensity in the focal volume\n"
            "constant at every depth.\n"
            "This does not depend on the process order: holding the focal intensity\n"
            "constant holds an n-photon signal constant for any n."
        )
        self.attenuation_edit.textChanged.connect(self._recompute)
        grid.addWidget(self.attenuation_edit, 1, 1)

        # --- table (mode "Table")
        self.table_label = QLabel("Depth:power")
        grid.addWidget(self.table_label, 2, 0)
        self.table_edit = QLineEdit("0:10, 40:25, 80:60")
        self.table_edit.setStyleSheet(LINE_EDIT_STYLE)
        self.table_edit.setToolTip(
            "Measured powers, as depth:percent pairs — for example\n"
            "    0:10, 40:25, 80:60\n"
            "Interpolated linearly between the points, and held flat beyond\n"
            "them: continuing the curve past the deepest measured point is\n"
            "how a sample gets cooked."
        )
        self.table_edit.textChanged.connect(self._recompute)
        grid.addWidget(self.table_edit, 2, 1)

        # --- laser
        grid.addWidget(QLabel("Laser"), 3, 0)
        self.laser_combo = QComboBox()
        self.laser_combo.addItems(RAMPABLE_LASERS)
        self.laser_combo.setStyleSheet(COMBO_STYLE)
        self.laser_combo.setToolTip("Laser whose power is ramped during the stack.")
        self.laser_combo.currentIndexChanged.connect(self._recompute)
        grid.addWidget(self.laser_combo, 3, 1)

        # --- surface power (read from the laser, not typed twice)
        self.base_power_label = QLabel("Surface power (%)")
        grid.addWidget(self.base_power_label, 4, 0)
        self.base_power_edit = QLineEdit("")
        self.base_power_edit.setReadOnly(True)
        self.base_power_edit.setStyleSheet(READONLY_STYLE)
        self.base_power_edit.setToolTip(
            "Power at the top of the stack, P(0) in P(z) = P(0)·exp(µz).\n"
            "Taken from the selected laser when the ramp is armed, and held\n"
            "afterwards: reading it live would feed the ramp its own output and\n"
            "make the power run away step after step."
        )
        grid.addWidget(self.base_power_edit, 4, 1)

        main_layout.addLayout(grid)

        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        self.status_label.setStyleSheet("color: #888;")
        main_layout.addWidget(self.status_label)

        main_layout.addStretch()

        # Après la construction complète : _on_mode_changed recompute, et le
        # recalcul a besoin du status_label créé juste au-dessus.
        self._on_mode_changed(self.mode_combo.currentText())

    # ------------------------------------------------------------------
    # Wiring
    # ------------------------------------------------------------------

    def set_laser_widget(self, laser_widget):
        """The laser panel owns the power; this one only reads and ramps it."""
        self._laser_widget = laser_widget
        self._refresh_base_power_display()

    def laser_name(self) -> str:
        return self.laser_combo.currentText()

    # ------------------------------------------------------------------
    # State fed from the scan
    # ------------------------------------------------------------------

    def update_from_scan_parameters(self, scan_parameters: dict):
        """Follow the scan: the ramp only means something along Z.

        Disarms itself if Z is dropped, rather than leaving an armed
        compensation pointing at an axis that is no longer scanned.
        """
        rows = [r for r in (scan_parameters or {}).get("rows", []) if r.get("axis") != "None"]
        z_row = next((r for r in rows if str(r.get("axis", "")) == DEPTH_AXIS), None)

        self._z_active = z_row is not None
        self._z_size_um = float(z_row.get("size_um", 0.0) or 0.0) if z_row else 0.0
        self._z_pixels = int(z_row.get("pixels", 1) or 1) if z_row else 1

        self.activate_button.setEnabled(self._z_active)
        if not self._z_active and self.activate_button.isChecked():
            self.activate_button.setChecked(False)
            logger.info("[DepthComp] Z is no longer scanned; compensation disabled.")

        self._recompute()

    # ------------------------------------------------------------------
    # Model
    # ------------------------------------------------------------------

    def mode(self) -> str:
        return "table" if self.mode_combo.currentText() == "Table" else "exponential"

    def get_compensation(self) -> DepthCompensation:
        try:
            table = parse_power_table(self.table_edit.text())
        except ValueError:
            table = ()          # _recompute reports it; the model stays honest
        return DepthCompensation(
            attenuation_um_inv=self._read_float(self.attenuation_edit, 0.0),
            enabled=bool(self.activate_button.isChecked() and self._z_active),
            mode=self.mode(),
            table=table,
        )

    def base_power_percent(self) -> float:
        """Power at the top of the stack."""
        return float(self._base_percent)

    def power_percent_at(self, depth_um: float) -> float:
        """Power to apply at a given depth; the surface power when disabled."""
        comp = self.get_compensation()
        if not comp.enabled:
            return self._base_percent
        try:
            return float(compute_power_profile(comp, self._base_percent, float(depth_um)))
        except ValueError as e:
            # An unusable table must not move the laser at all.
            logger.warning(f"[DepthComp] {e}; holding the surface power.")
            return self._base_percent

    def _on_mode_changed(self, _text=None):
        """Show only what the chosen mode uses.

        The surface power stays visible in table mode but greyed: the table
        gives absolute percentages, so P(0) plays no part, and hiding it would
        leave someone wondering whether it still did.
        """
        table_mode = self.mode() == "table"

        self.attenuation_label.setVisible(not table_mode)
        self.attenuation_edit.setVisible(not table_mode)
        self.table_label.setVisible(table_mode)
        self.table_edit.setVisible(table_mode)

        self.base_power_edit.setEnabled(not table_mode)
        self.base_power_label.setEnabled(not table_mode)

        self._recompute()

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    @staticmethod
    def _read_float(edit: QLineEdit, default: float) -> float:
        try:
            return float((edit.text() or "").replace(",", "."))
        except ValueError:
            return default

    def _laser_power_now(self) -> float:
        if self._laser_widget is None:
            return 0.0
        try:
            return float(self._laser_widget.get_laser_power_value(self.laser_name()))
        except Exception as e:
            logger.debug(f"[DepthComp] could not read the laser power: {e}")
            return 0.0

    def _refresh_base_power_display(self):
        if not self.activate_button.isChecked():
            self._base_percent = self._laser_power_now()
        self.base_power_edit.setText(f"{self._base_percent:.3g}")

    def _on_activate_toggled(self, checked: bool):
        if checked:
            # Freeze P(0) here: the ramp writes the laser power, so reading it
            # again later would compound its own output.
            self._base_percent = self._laser_power_now()
            logger.info(
                f"[DepthComp] armed on {self.laser_name()} "
                f"from {self._base_percent:.3g} % at the surface."
            )
        self._refresh_base_power_display()
        self._recompute()

    def _recompute(self):
        if not self.activate_button.isChecked():
            self._refresh_base_power_display()

        if not self._z_active:
            self.status_label.setStyleSheet("color: #888;")
            self.status_label.setText("Z-Vcoil is not an active scan axis.")
            return

        if self.mode() == "table":
            # Report a malformed table where it is being typed, rather than
            # silently falling back to something that looks like it worked.
            try:
                parse_power_table(self.table_edit.text())
            except ValueError as e:
                self.status_label.setStyleSheet("color: #FF7700;")
                self.status_label.setText(f"Power table: {e}.")
                return

        comp = self.get_compensation()
        depths = depth_axis_um(self._z_size_um, self._z_pixels)
        try:
            percents = compute_power_profile(comp, self._base_percent, depths)
        except ValueError as e:
            self.status_label.setStyleSheet("color: #FF7700;")
            self.status_label.setText(str(e))
            return
        report = validate_power_profile(percents, depths)

        if report.ok:
            self.status_label.setStyleSheet("color: #9CFF9C;")
            self.status_label.setText(f"{self._z_size_um:.0f} µm deep: {report.summary()}")
        else:
            reachable = max_reachable_depth_um(comp, self._base_percent)
            extra = "" if reachable == float("inf") else f" Reachable down to {reachable:.0f} µm."
            self.status_label.setStyleSheet("color: #FF7700;")
            self.status_label.setText(report.reason + extra)
