"""Helper panel: raise the laser power with depth during a Z stack."""

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QGridLayout, QLabel, QLineEdit, QComboBox,
    QPushButton, QSizePolicy,
)
from PySide6.QtCore import Signal, Qt
from PySide6.QtGui import QDoubleValidator

from ..managers.Depth_Compensation import (
    DepthCompensation,
    SUPPORTED_ORDERS,
    compute_power_profile,
    depth_axis_um,
    max_reachable_depth_um,
    validate_power_profile,
)
from .Log_Widget import logger

#: Only this axis is a depth. A polarisation or wavelength stack is not.
DEPTH_AXIS = "Z-Vcoil"

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
        min-height: 22px;
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

    compensationChanged = Signal(object)   # DepthCompensation

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)

        self._z_active = False
        self._z_size_um = 0.0
        self._z_pixels = 1
        self._base_percent = 100.0

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(2, 2, 2, 2)
        main_layout.setSpacing(4)

        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(6)
        grid.setVerticalSpacing(4)
        grid.setColumnStretch(0, 0)
        grid.setColumnStretch(1, 1)

        # --- attenuation
        grid.addWidget(QLabel("Attenuation (µm⁻¹)"), 0, 0)
        self.attenuation_edit = QLineEdit("0.005")
        self.attenuation_edit.setStyleSheet(LINE_EDIT_STYLE)
        self.attenuation_edit.setValidator(QDoubleValidator(0.0, 10.0, 6))
        self.attenuation_edit.setToolTip(
            "Attenuation coefficient fitted on the signal decay of an n-photon\n"
            "z-stack, in µm⁻¹.\n"
            "An n-photon signal follows the n-th power of the excitation, so a\n"
            "signal decaying as exp(-µz) comes from a beam decaying as exp(-µz/n):\n"
            "the compensation applied is P(z) = P(0)·exp(µ·z/n)."
        )
        self.attenuation_edit.textChanged.connect(self._recompute)
        grid.addWidget(self.attenuation_edit, 0, 1)

        # --- process order
        grid.addWidget(QLabel("Process"), 1, 0)
        self.order_combo = QComboBox()
        self.order_combo.addItems([f"{n} photon" for n in SUPPORTED_ORDERS])
        self.order_combo.setCurrentIndex(1)          # 2-photon by default
        self.order_combo.setStyleSheet(COMBO_STYLE)
        self.order_combo.currentIndexChanged.connect(self._recompute)
        grid.addWidget(self.order_combo, 1, 1)

        # --- surface power
        grid.addWidget(QLabel("Surface power (%)"), 2, 0)
        self.base_power_edit = QLineEdit("20")
        self.base_power_edit.setStyleSheet(LINE_EDIT_STYLE)
        self.base_power_edit.setValidator(QDoubleValidator(0.0, 100.0, 3))
        self.base_power_edit.setToolTip("Power applied at the top of the stack.")
        self.base_power_edit.textChanged.connect(self._recompute)
        grid.addWidget(self.base_power_edit, 2, 1)

        main_layout.addLayout(grid)

        # --- verdict
        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        self.status_label.setStyleSheet("color: #888;")
        main_layout.addWidget(self.status_label)

        # --- activate
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

        main_layout.addStretch()

        self._recompute()

    # ------------------------------------------------------------------
    # State fed from the scan
    # ------------------------------------------------------------------

    def update_from_scan_parameters(self, scan_parameters: dict):
        """Follow the scan: the ramp only means something along Z.

        Deactivates itself if Z is dropped, rather than leaving an armed
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

    def set_base_power_percent(self, percent: float):
        """Surface power, when the laser widget owns it."""
        self.base_power_edit.setText(f"{float(percent):.3g}")

    # ------------------------------------------------------------------
    # Model
    # ------------------------------------------------------------------

    def get_compensation(self) -> DepthCompensation:
        return DepthCompensation(
            attenuation_um_inv=self._read_float(self.attenuation_edit, 0.0),
            order=SUPPORTED_ORDERS[max(0, self.order_combo.currentIndex())],
            enabled=bool(self.activate_button.isChecked() and self._z_active),
        )

    def power_percent_at(self, depth_um: float) -> float:
        """Power to apply at a given depth; the surface power when disabled."""
        comp = self.get_compensation()
        base = self._read_float(self.base_power_edit, 0.0)
        if not comp.enabled:
            return base
        return float(base * comp.power_gain(float(depth_um)))

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    @staticmethod
    def _read_float(edit: QLineEdit, default: float) -> float:
        try:
            return float((edit.text() or "").replace(",", "."))
        except ValueError:
            return default

    def _on_activate_toggled(self, checked: bool):
        self._recompute()
        self.compensationChanged.emit(self.get_compensation())

    def _recompute(self):
        if not self._z_active:
            self.status_label.setStyleSheet("color: #888;")
            self.status_label.setText("Z-Vcoil is not an active scan axis.")
            return

        comp = self.get_compensation()
        base = self._read_float(self.base_power_edit, 0.0)
        depths = depth_axis_um(self._z_size_um, self._z_pixels)
        percents = compute_power_profile(comp, base, depths)
        report = validate_power_profile(percents, depths)

        if report.ok:
            self.status_label.setStyleSheet("color: #9CFF9C;")
            self.status_label.setText(
                f"{self._z_size_um:.0f} µm deep: {report.summary()}"
            )
        else:
            reachable = max_reachable_depth_um(comp, base)
            extra = ""
            if reachable != float("inf"):
                extra = f" Reachable down to {reachable:.0f} µm."
            self.status_label.setStyleSheet("color: #FF7700;")
            self.status_label.setText(report.reason + extra)
