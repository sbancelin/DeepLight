"""Right-hand panel of the Spectro tab: the BIPD filter and the extinction.

Manual control of the filter (liquid crystal, rotator, heater), the border
level of the current Brillouin frame -- the figure of merit of the extinction
-- an on-demand search of the best liquid-crystal voltage, and the switch that
lets a map re-optimise it by itself when the extinction drifts.

Step sizes and the search parameters are in the Settings popup (gear), and are
remembered with "Save value", like every other popup.
"""

from __future__ import annotations

import numpy as np
from PySide6.QtCore import QLocale, Qt, QTimer, Signal, Slot
from PySide6.QtGui import QColor, QPen
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDoubleSpinBox, QFormLayout, QGraphicsRectItem, QHBoxLayout,
    QLabel, QPushButton, QSizePolicy, QSpinBox, QVBoxLayout, QWidget,
)

from ..managers.Bipd_Filter import BipdFilterBase
from ..managers.Extinction import border_intensity
from .Log_Widget import logger
from .Spectro_Widget import (
    _BUTTON_STYLE, _BUTTON_STYLE_TOGGLE, _CHECKBOX_STYLE, _apply_combo_style,
    _apply_spinbox_palette,
)

#: Built-in defaults, as on the bench program the filter came with.
BIPD_SETTINGS_DEFAULTS = {
    "lc_small_step_mv": 1.0,
    "lc_large_step_mv": 50.0,
    "rotator_small_step_deg": 1.0,
    "rotator_large_step_deg": 30.0,
    "margin_px": 20,
    "step_mv": 5.0,
    "settle_s": 0.5,
    "max_steps": 30,
    "threshold": 3.0,
    "check_every": 60.0,
    "check_mode": "seconds",
}

_SECTION_STYLE = "color: white; font-weight: bold; padding-top: 4px;"
_HINT_STYLE = "color: #b0b0b0;"
_ERROR_STYLE = "color: #e05555;"
_VALUE_STYLE = "color: white; font-weight: bold;"


class _HardwareSpinBox(QDoubleSpinBox):
    """A value the mouse wheel only changes once the field has been clicked into.

    These fields drive hardware: scrolling the panel past one must not turn the
    liquid crystal or the rotator on the way.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFocusPolicy(Qt.StrongFocus)          # the wheel gives no focus

    def wheelEvent(self, event):
        if self.hasFocus():
            super().wheelEvent(event)
        else:
            event.ignore()                           # left to the scroll area


def _small_button(text: str, tooltip: str = "") -> QPushButton:
    button = QPushButton(text)
    button.setStyleSheet(_BUTTON_STYLE)
    button.setFixedWidth(30)
    if tooltip:
        button.setToolTip(tooltip)
    return button


class BipdWidget(QWidget):
    sigOptimizeRequested = Signal()

    #: The level is recomputed at most this often, whatever the live rate.
    LEVEL_REFRESH_MS = 200

    def __init__(self, parent=None):
        super().__init__(parent)
        self.manager = None
        self.settings_manager = None
        self._settings = dict(BIPD_SETTINGS_DEFAULTS)
        self._connected = False
        self._busy = False

        self._image_view = None
        self._region_item = None
        self._frame_shape = None
        self._pending_frame = None

        self._level_timer = QTimer(self)
        self._level_timer.setSingleShot(True)
        self._level_timer.setInterval(self.LEVEL_REFRESH_MS)
        self._level_timer.timeout.connect(self._refresh_level)

        self._build()
        self._set_controls_enabled(False)
        self._update_auto_lc_hint()

    # ------------------------------------------------------------------
    # layout
    # ------------------------------------------------------------------
    def _build(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        # --- connection + heater
        row = QHBoxLayout()
        self.button_connect = QPushButton("Connect")
        self.button_connect.setCheckable(True)
        self.button_connect.setStyleSheet(_BUTTON_STYLE_TOGGLE)
        self.button_connect.setToolTip("Open or close the serial link to the BIPD filter.")
        self.button_connect.toggled.connect(self._on_connect_toggled)
        row.addWidget(self.button_connect)
        self.label_status = QLabel("not connected")
        self.label_status.setStyleSheet(_HINT_STYLE)
        self.label_status.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        row.addWidget(self.label_status, stretch=1)
        layout.addLayout(row)

        row = QHBoxLayout()
        self.cb_heater = QCheckBox("Heater")
        self.cb_heater.setStyleSheet(_CHECKBOX_STYLE)
        self.cb_heater.setToolTip(
            "Keep the liquid crystal at a constant temperature.\n"
            "Its retardance -- and so the extinction -- depends on it."
        )
        self.cb_heater.toggled.connect(self._on_heater_toggled)
        row.addWidget(self.cb_heater)
        row.addStretch()
        self.label_temperature = QLabel("T: -- °C")
        self.label_temperature.setStyleSheet(_HINT_STYLE)
        row.addWidget(self.label_temperature)
        layout.addLayout(row)

        # --- liquid crystal
        title = QLabel("Liquid crystal (mV)")
        title.setStyleSheet(_SECTION_STYLE)
        layout.addWidget(title)

        self.spin_lc = _HardwareSpinBox()
        self.spin_lc.setLocale(QLocale.c())
        self.spin_lc.setDecimals(1)
        self.spin_lc.setRange(BipdFilterBase.LC_MIN_MV, BipdFilterBase.LC_MAX_MV)
        self.spin_lc.setKeyboardTracking(False)
        self.spin_lc.setAlignment(Qt.AlignCenter)
        self.spin_lc.setButtonSymbols(QDoubleSpinBox.NoButtons)
        self.spin_lc.setToolTip(
            f"Liquid-crystal voltage. Type a value and press Enter.\n"
            f"Limited to {BipdFilterBase.LC_MAX_MV:.0f} mV, the safety limit of the liquid crystal."
        )
        _apply_spinbox_palette(self.spin_lc)
        self.spin_lc.valueChanged.connect(self._on_lc_edited)
        row, self._lc_buttons = self._stepper_row(self.spin_lc, self._step_lc)
        layout.addLayout(row)

        self.label_lc_measured = QLabel("measured: -- mV")
        self.label_lc_measured.setStyleSheet(_HINT_STYLE)
        layout.addWidget(self.label_lc_measured)

        # --- rotator
        title = QLabel("Rotator (°)")
        title.setStyleSheet(_SECTION_STYLE)
        layout.addWidget(title)

        self.spin_rotator = _HardwareSpinBox()
        self.spin_rotator.setLocale(QLocale.c())
        self.spin_rotator.setDecimals(2)
        self.spin_rotator.setRange(-360.0, 360.0)
        self.spin_rotator.setKeyboardTracking(False)
        self.spin_rotator.setAlignment(Qt.AlignCenter)
        self.spin_rotator.setButtonSymbols(QDoubleSpinBox.NoButtons)
        self.spin_rotator.setToolTip("Rotator angle. Type a value and press Enter.")
        _apply_spinbox_palette(self.spin_rotator)
        self.spin_rotator.valueChanged.connect(self._on_rotator_edited)
        row, self._rotator_buttons = self._stepper_row(self.spin_rotator, self._step_rotator)
        self.button_home = QPushButton("Home")
        self.button_home.setStyleSheet(_BUTTON_STYLE)
        self.button_home.setToolTip("Send the rotator to its home position.")
        self.button_home.clicked.connect(self._on_home_clicked)
        row.addWidget(self.button_home)
        layout.addLayout(row)

        # --- extinction
        title = QLabel("Extinction")
        title.setStyleSheet(_SECTION_STYLE)
        layout.addWidget(title)

        row = QHBoxLayout()
        row.addWidget(QLabel("Border level:"))
        self.label_level = QLabel("--")
        self.label_level.setStyleSheet(_VALUE_STYLE)
        self.label_level.setToolTip(
            "Mean level of the band along the edges of the current Brillouin\n"
            "frame (width: margin, in Settings), where the light the filter\n"
            "should extinguish shows up. Lower is better."
        )
        row.addWidget(self.label_level, stretch=1)
        self.cb_show_region = QCheckBox("Show")
        self.cb_show_region.setStyleSheet(_CHECKBOX_STYLE)
        self.cb_show_region.setToolTip(
            "Outline, on the Brillouin image, the inner edge of the band the level is measured on."
        )
        self.cb_show_region.toggled.connect(self._on_show_region_toggled)
        row.addWidget(self.cb_show_region)
        layout.addLayout(row)

        self.button_optimize = QPushButton("Optimise LC")
        self.button_optimize.setStyleSheet(_BUTTON_STYLE)
        self.button_optimize.setToolTip(
            "Search the liquid-crystal voltage for the lowest border level,\n"
            "with the camera settings of the Spectro tab. The live view is\n"
            "paused meanwhile and resumed afterwards."
        )
        self.button_optimize.clicked.connect(self._on_optimize_clicked)
        layout.addWidget(self.button_optimize)

        self.cb_auto_lc = QCheckBox("Re-optimise during maps")
        self.cb_auto_lc.setStyleSheet(_CHECKBOX_STYLE)
        self.cb_auto_lc.setToolTip(
            "During a Brillouin map, compare the border level with the one of\n"
            "the first point; past the threshold, search the voltage again and\n"
            "acquire the point anew. Every correction is logged and recorded\n"
            "with the map.\n\n"
            "The reference is the first point of the map: on a sample whose\n"
            "edges scatter much more than there, raise the threshold."
        )
        self.cb_auto_lc.toggled.connect(lambda _checked: self._update_auto_lc_hint())
        layout.addWidget(self.cb_auto_lc)

        self.label_auto_lc = QLabel("")
        self.label_auto_lc.setStyleSheet(_HINT_STYLE)
        self.label_auto_lc.setWordWrap(True)
        layout.addWidget(self.label_auto_lc)

        self.label_result = QLabel("")
        self.label_result.setStyleSheet(_HINT_STYLE)
        self.label_result.setWordWrap(True)
        layout.addWidget(self.label_result)

        layout.addStretch()

    def _stepper_row(self, spin, step_fn) -> tuple[QHBoxLayout, list]:
        """<< < [value] > >> : small and large steps, sizes in Settings."""
        row = QHBoxLayout()
        row.setSpacing(2)
        buttons = []
        for text, small, sign in (("<<", False, -1), ("<", True, -1), (">", True, 1), (">>", False, 1)):
            button = _small_button(
                text, f"{'Small' if small else 'Large'} step {'up' if sign > 0 else 'down'} (sizes in Settings)"
            )
            button.clicked.connect(lambda _=False, s=small, d=sign: step_fn(s, d))
            buttons.append(button)
        row.addWidget(buttons[0])
        row.addWidget(buttons[1])
        row.addWidget(spin, stretch=1)
        row.addWidget(buttons[2])
        row.addWidget(buttons[3])
        return row, buttons

    # ------------------------------------------------------------------
    # wiring
    # ------------------------------------------------------------------
    def set_manager(self, manager):
        self.manager = manager
        manager.sigConnectionChanged.connect(self._on_connection_changed)
        manager.sigState.connect(self._on_state)
        manager.sigBusy.connect(self._on_busy)
        manager.sigOptimizationDone.connect(self._on_optimization_done)
        manager.sigError.connect(self._on_error)

    def set_settings_manager(self, manager):
        """Load the saved step sizes and search parameters, if any."""
        self.settings_manager = manager
        saved = manager.get_bipd_settings() if manager is not None else {}
        for key, default in BIPD_SETTINGS_DEFAULTS.items():
            if key in saved:
                try:
                    self._settings[key] = type(default)(saved[key])
                except (TypeError, ValueError):
                    pass
        self._update_auto_lc_hint()

    def attach_image_view(self, image_view):
        """Draw the measured band's inner edge on this (Brillouin) image view."""
        self._image_view = image_view
        pen = QPen(QColor(255, 60, 60))
        pen.setWidth(2)
        pen.setCosmetic(True)
        pen.setStyle(Qt.DashLine)
        # Child of the image item, so it follows the image's own transform.
        self._region_item = QGraphicsRectItem(image_view.getImageItem())
        self._region_item.setPen(pen)
        self._region_item.setZValue(20)
        self._region_item.setVisible(False)

    # ------------------------------------------------------------------
    # settings
    # ------------------------------------------------------------------
    def get_settings(self) -> dict:
        """The search parameters, as Extinction.AutoLcSettings takes them."""
        keys = ("margin_px", "step_mv", "settle_s", "max_steps", "threshold", "check_every", "check_mode")
        return {k: self._settings[k] for k in keys}

    def auto_lc_enabled(self) -> bool:
        return bool(self.cb_auto_lc.isChecked())

    def _update_auto_lc_hint(self):
        s = self._settings
        unit = "s" if s["check_mode"] == "seconds" else "points"
        every = f"{s['check_every']:g} {unit}"
        state = "" if self.cb_auto_lc.isChecked() else " (off)"
        self.label_auto_lc.setText(
            f"Above {s['threshold']:.2f} × first point, checked every {every}{state}"
        )
        if self._frame_shape is not None:
            self._update_region(*self._frame_shape)

    def open_settings_dialog(self):
        from .Dialogs import SettingsDialog

        dialog = SettingsDialog("BIPD filter - Settings", self)
        s = dict(self._settings)

        def dspin(value, lo, hi, decimals, tip=""):
            spin = QDoubleSpinBox()
            spin.setLocale(QLocale.c())
            spin.setDecimals(decimals)
            spin.setRange(lo, hi)
            spin.setValue(float(value))
            if tip:
                spin.setToolTip(tip)
            return spin

        def ispin(value, lo, hi, tip=""):
            spin = QSpinBox()
            spin.setRange(lo, hi)
            spin.setValue(int(value))
            if tip:
                spin.setToolTip(tip)
            return spin

        lc_small = dspin(s["lc_small_step_mv"], 0.1, 1000.0, 1)
        lc_large = dspin(s["lc_large_step_mv"], 0.1, 1000.0, 1)
        rot_small = dspin(s["rotator_small_step_deg"], 0.01, 360.0, 2)
        rot_large = dspin(s["rotator_large_step_deg"], 0.01, 360.0, 2)
        margin = ispin(s["margin_px"], 0, 2000,
                       "Width of the band along the frame edges the border level is measured on.")
        step = dspin(s["step_mv"], 0.1, 500.0, 1,
                     "Voltage step of the search. Its direction is remembered between searches.")
        settle = dspin(s["settle_s"], 0.2, 10.0, 2,
                       "Wait after each voltage change before acquiring.\n"
                       "The liquid crystal needs 200 ms at least.")
        max_steps = ispin(s["max_steps"], 1, 1000, "Most steps taken in the downhill direction.")
        threshold = dspin(s["threshold"], 1.01, 100.0, 2,
                          "During a map, re-optimise when the border level exceeds this\n"
                          "multiple of the level at the first point.")
        check_every = dspin(s["check_every"], 1.0, 100000.0, 0)
        check_mode = QComboBox()
        check_mode.addItems(["seconds", "points"])
        check_mode.setCurrentText(str(s["check_mode"]))
        _apply_combo_style(check_mode)

        form = QFormLayout()
        title = QLabel("Manual steps")
        title.setStyleSheet("font-weight: bold;")
        form.addRow(title)
        form.addRow("LC small step (mV):", lc_small)
        form.addRow("LC large step (mV):", lc_large)
        form.addRow("Rotator small step (°):", rot_small)
        form.addRow("Rotator large step (°):", rot_large)
        title = QLabel("Extinction")
        title.setStyleSheet("font-weight: bold; padding-top: 6px;")
        form.addRow(title)
        form.addRow("Border margin (px):", margin)
        form.addRow("Search step (mV):", step)
        form.addRow("Settle after a step (s):", settle)
        form.addRow("Max steps:", max_steps)
        form.addRow("Map threshold (× first point):", threshold)
        every_row = QHBoxLayout()
        every_row.addWidget(check_every, stretch=1)
        every_row.addWidget(check_mode)
        form.addRow("Map check every:", every_row)
        dialog.add_layout(form)

        def collect() -> dict:
            return {
                "lc_small_step_mv": float(lc_small.value()),
                "lc_large_step_mv": float(lc_large.value()),
                "rotator_small_step_deg": float(rot_small.value()),
                "rotator_large_step_deg": float(rot_large.value()),
                "margin_px": int(margin.value()),
                "step_mv": float(step.value()),
                "settle_s": float(settle.value()),
                "max_steps": int(max_steps.value()),
                "threshold": float(threshold.value()),
                "check_every": float(check_every.value()),
                "check_mode": str(check_mode.currentText()),
            }

        def fill(values: dict):
            lc_small.setValue(values["lc_small_step_mv"])
            lc_large.setValue(values["lc_large_step_mv"])
            rot_small.setValue(values["rotator_small_step_deg"])
            rot_large.setValue(values["rotator_large_step_deg"])
            margin.setValue(int(values["margin_px"]))
            step.setValue(values["step_mv"])
            settle.setValue(values["settle_s"])
            max_steps.setValue(int(values["max_steps"]))
            threshold.setValue(values["threshold"])
            check_every.setValue(values["check_every"])
            check_mode.setCurrentText(values["check_mode"])

        def on_accepted():
            self._settings.update(collect())
            self._update_auto_lc_hint()

        def save_values():
            on_accepted()
            if self.settings_manager is not None:
                self.settings_manager.update_bipd_settings(**self._settings)
                self.settings_manager.save_bipd_settings()
                logger.info("[BIPD] settings saved for next launch")

        def reset_origin():
            self._settings = dict(BIPD_SETTINGS_DEFAULTS)
            fill(self._settings)
            self._update_auto_lc_hint()
            if self.settings_manager is not None:
                self.settings_manager.update_bipd_settings(**self._settings)
                self.settings_manager.save_bipd_settings()
            logger.info("[BIPD] settings reset to built-in defaults")

        dialog.accepted.connect(on_accepted)
        dialog.set_persistence_callbacks(save_values, reset_origin)
        dialog.exec()

    # ------------------------------------------------------------------
    # user actions
    # ------------------------------------------------------------------
    @Slot(bool)
    def _on_connect_toggled(self, checked: bool):
        if self.manager is None:
            return
        self.label_status.setText("connecting..." if checked else "disconnecting...")
        self.label_status.setStyleSheet(_HINT_STYLE)
        self.button_connect.setEnabled(False)
        self.manager.set_connected(bool(checked))

    @Slot(bool)
    def _on_heater_toggled(self, checked: bool):
        if self.manager is not None and self._connected:
            self.manager.set_heater(bool(checked))

    def _step_lc(self, small: bool, sign: int):
        step = self._settings["lc_small_step_mv" if small else "lc_large_step_mv"]
        self.spin_lc.setValue(self.spin_lc.value() + sign * step)   # clamped by the range

    def _step_rotator(self, small: bool, sign: int):
        step = self._settings["rotator_small_step_deg" if small else "rotator_large_step_deg"]
        self.spin_rotator.setValue(self.spin_rotator.value() + sign * step)

    @Slot(float)
    def _on_lc_edited(self, value: float):
        if self.manager is not None and self._connected:
            self.manager.set_lc_level(float(value))

    @Slot(float)
    def _on_rotator_edited(self, value: float):
        if self.manager is not None and self._connected:
            self.manager.move_rotator(float(value))

    @Slot()
    def _on_home_clicked(self):
        if self.manager is not None and self._connected:
            self.manager.home_rotator()

    @Slot()
    def _on_optimize_clicked(self):
        if self._busy:
            if self.manager is not None:
                self.manager.cancel_optimization()
            return
        self.label_result.setStyleSheet(_HINT_STYLE)
        self.label_result.setText("")
        self.sigOptimizeRequested.emit()

    # ------------------------------------------------------------------
    # from the manager
    # ------------------------------------------------------------------
    def _set_controls_enabled(self, enabled: bool):
        lc_enabled = bool(enabled and not self._busy)
        for widget in (self.spin_lc, *self._lc_buttons):
            widget.setEnabled(lc_enabled)
        for widget in (self.spin_rotator, *self._rotator_buttons, self.button_home, self.cb_heater):
            widget.setEnabled(bool(enabled))
        self.button_optimize.setEnabled(bool(enabled))

    @Slot(bool, str)
    def _on_connection_changed(self, connected: bool, text: str):
        self._connected = bool(connected)
        self.button_connect.blockSignals(True)
        self.button_connect.setChecked(self._connected)
        self.button_connect.setText("Disconnect" if self._connected else "Connect")
        self.button_connect.blockSignals(False)
        self.button_connect.setEnabled(True)
        self.label_status.setText(text if self._connected else (text or "not connected"))
        self.label_status.setToolTip(text)
        self.label_status.setStyleSheet(
            _HINT_STYLE if self._connected or text in ("", "disconnected") else _ERROR_STYLE
        )
        self._set_controls_enabled(self._connected)
        if not self._connected:
            self.label_temperature.setText("T: -- °C")
            self.label_lc_measured.setText("measured: -- mV")

    @Slot(dict)
    def _on_state(self, state: dict):
        state = dict(state or {})
        if "lc_level_mv" in state and not self.spin_lc.hasFocus():
            self.spin_lc.blockSignals(True)
            self.spin_lc.setValue(float(state["lc_level_mv"]))
            self.spin_lc.blockSignals(False)
        measured = state.get("lc_measured_mv")
        self.label_lc_measured.setText(
            "measured: -- mV" if measured is None else f"measured: {float(measured):.1f} mV"
        )
        if "rotator_deg" in state and not self.spin_rotator.hasFocus():
            self.spin_rotator.blockSignals(True)
            self.spin_rotator.setValue(float(state["rotator_deg"]))
            self.spin_rotator.blockSignals(False)
        if "heater_enabled" in state:
            self.cb_heater.blockSignals(True)
            self.cb_heater.setChecked(bool(state["heater_enabled"]))
            self.cb_heater.blockSignals(False)
        temperature = state.get("temperature_c")
        self.label_temperature.setText(
            "T: -- °C" if temperature is None else f"T: {float(temperature):.2f} °C"
        )

    @Slot(bool)
    def _on_busy(self, busy: bool):
        self._busy = bool(busy)
        self.button_optimize.setText("Stop optimising" if self._busy else "Optimise LC")
        self.button_optimize.setStyleSheet(_BUTTON_STYLE_TOGGLE if self._busy else _BUTTON_STYLE)
        self.button_optimize.setCheckable(self._busy)
        self.button_optimize.setChecked(self._busy)
        if self._busy:
            self.label_result.setStyleSheet(_HINT_STYLE)
            self.label_result.setText("Searching the liquid-crystal voltage...")
        self._set_controls_enabled(self._connected)

    @Slot(dict)
    def _on_optimization_done(self, result: dict):
        r = dict(result or {})
        verdict = "stopped" if r.get("stopped") else ("" if r.get("improved") else "already at the minimum, ")
        self.label_result.setStyleSheet(_HINT_STYLE)
        self.label_result.setText(
            f"{verdict}LC {r.get('start_mv', 0):.1f} → {r.get('best_mv', 0):.1f} mV, "
            f"border {r.get('start_level', 0):.1f} → {r.get('best_level', 0):.1f} "
            f"({r.get('moves', 0)} moves)"
        )

    @Slot(str)
    def _on_error(self, message: str):
        self.label_result.setStyleSheet(_ERROR_STYLE)
        self.label_result.setText(str(message))

    # ------------------------------------------------------------------
    # frames
    # ------------------------------------------------------------------
    @Slot(object)
    def on_frame(self, image):
        """Every Brillouin frame; the level is computed at a bounded rate."""
        self._pending_frame = image
        if not self._level_timer.isActive():
            self._level_timer.start()

    @Slot()
    def _refresh_level(self):
        image, self._pending_frame = self._pending_frame, None
        if image is None:
            return
        try:
            img = np.asarray(image)
            if img.ndim != 2 or img.size == 0:
                return
            if img.shape != self._frame_shape:
                self._frame_shape = img.shape
                self._update_region(*img.shape)
            # Only worth the sum over a megapixel when someone can see it.
            if self.isVisible():
                level = border_intensity(img, int(self._settings["margin_px"]))
                self.label_level.setText(f"{level:.2f}")
        except Exception as e:
            logger.debug(f"[BIPD] border level not computed: {e}")

    @Slot(bool)
    def _on_show_region_toggled(self, checked: bool):
        if self._region_item is not None:
            self._region_item.setVisible(bool(checked))
            if self._frame_shape is not None:
                self._update_region(*self._frame_shape)

    def _update_region(self, h: int, w: int):
        if self._region_item is None:
            return
        margin = int(self._settings["margin_px"])
        mx = min(margin, w // 2 - 1)
        my = min(margin, h // 2 - 1)
        if mx <= 0 or my <= 0:
            self._region_item.setRect(0, 0, w, h)
        else:
            self._region_item.setRect(mx, my, w - 2 * mx, h - 2 * my)
