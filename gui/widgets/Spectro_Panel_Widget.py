from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel,
    QPushButton, QSizePolicy, QLineEdit, QFileDialog,
    QPlainTextEdit, QComboBox, QMessageBox, QFrame, QProgressBar
)
from PySide6.QtCore import Signal, Qt

from ...config import default_dated_folder
from ..resources import icon_path
from .Log_Widget import logger
from PySide6.QtGui import QIcon, QDoubleValidator, QIntValidator

import os


#: Built-in defaults for the Spectro settings popup, and what "Reset origin"
#: there goes back to.
SPECTRO_SETTINGS_DEFAULTS = {
    "settle_ms": 10.0,
    "line_offset_x_um": -1.2,
    "origin_overshoot_um": 10.0,
    "stage_speed_mm_s": 4.0,
    # Raman. Excitation 0 = not stated: it is only recorded, never used for a
    # computation. Rows 0/0 = the full sensor height. No calibration = spectra
    # on raw detector pixels, the axis that cannot be wrong.
    "raman_excitation_nm": 0.0,
    "raman_row_start": 0,
    "raman_row_count": 0,
    "raman_calibration": None,
}


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

MODE_BUTTON_STYLE = """
    QPushButton {
        background-color: #333;
        color: white;
        border: 1px solid #555;
        border-radius: 6px;
        padding: 6px;
        font-weight: bold;
        min-height: 28px;
    }
    QPushButton:checked {
        background-color: #FF7700;
        border: 1px solid #FF9200;
    }
    QPushButton:hover {
        background-color: #444;
    }
    QPushButton:checked:hover {
        background-color: #FF9200;
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
"""

PANEL_FRAME_STYLE = """
QFrame {
    background-color: #252525;
    border: 1px solid #444;
    border-radius: 4px;
}
QLabel {
    color: white;
    border: none;
    background: transparent;
}
"""

PROGRESS_BAR_STYLE = """
    QProgressBar {
        background-color: #252525;
        color: white;
        border: 1px solid #444;
        border-radius: 3px;
        text-align: center;
        min-height: 18px;
    }
    QProgressBar::chunk {
        background-color: #FF7700;
        border-radius: 2px;
    }
"""

HEADER_LABEL_STYLE = "color: white; font-weight: bold; padding-bottom: 2px;"


class SpectroPanelWidget(QWidget):
    """
    Spectro acquisition panel, independent of the raster Scan logic.
    """

    sigSpectroModeChanged = Signal(bool, bool)   # brillouin, raman
    sigAcquireClicked = Signal()
    sigStopClicked = Signal()
    settleTimeChanged = Signal(float)            # settle_ms
    sigRamanSettingsChanged = Signal(dict)       # see get_raman_settings()

    _BRILLOUIN_IMG_H = 1200
    _BRILLOUIN_IMG_W = 1200
    _BRILLOUIN_BYTES_PER_PIXEL = 4

    _RAMAN_POINTS = 1024
    _RAMAN_BYTES_PER_POINT = 4

    # Overhead fixe par pixel (arm/readout caméra, transactions série,
    # détection d'arrivée platine). Valeur à recaler après mesure réelle
    # (cf. logs [PICam] snap: acquire=...).
    _PIXEL_OVERHEAD_S = 0.20

    def __init__(self, parent=None):
        super().__init__(parent)

        self.settings_manager = None

        # Paramètres de settings (non exposés dans l'UI principale)
        self._settle_ms = SPECTRO_SETTINGS_DEFAULTS["settle_ms"]
        self._line_offset_x_um = SPECTRO_SETTINGS_DEFAULTS["line_offset_x_um"]
        self._origin_overshoot_um = SPECTRO_SETTINGS_DEFAULTS["origin_overshoot_um"]
        self._brillouin_exposure_ms = 100.0
        self._raman_exposure_ms = 100.0
        self._raman_averages = 1
        self._raman_spectral_pixels = self._RAMAN_POINTS   # until the detector says
        self._stage_speed_mm_s = SPECTRO_SETTINGS_DEFAULTS["stage_speed_mm_s"]

        # Raman (Settings) : enregistré avec la carte ; la calibration est la
        # forme to_dict() d'une SpectralCalibration, None = pixels bruts.
        self._raman_excitation_nm = SPECTRO_SETTINGS_DEFAULTS["raman_excitation_nm"]
        self._raman_row_start = SPECTRO_SETTINGS_DEFAULTS["raman_row_start"]
        self._raman_row_count = SPECTRO_SETTINGS_DEFAULTS["raman_row_count"]
        self._raman_calibration = SPECTRO_SETTINGS_DEFAULTS["raman_calibration"]

        #: Callable(path) -> SpectralCalibration, provided by the window: only
        #: the spectrometer knows the pixel count and the current setting a
        #: calibration file must be checked against.
        self.raman_calibration_loader = None

        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)

        self._brillouin_roi_state = {
            "enabled": False,
            "x": 0,
            "y": 0,
            "width": 1200,
            "height": 1200,
        }

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(2, 2, 2, 2)
        main_layout.setSpacing(4)

        content_widget = QWidget()
        content_widget.setMinimumWidth(0)
        content_widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)

        content_layout = QVBoxLayout(content_widget)
        content_layout.setContentsMargins(2, 2, 2, 2)
        content_layout.setSpacing(4)

        # ==========================================================
        # Modes
        # ==========================================================
        modes_frame = QFrame()
        modes_frame.setStyleSheet(PANEL_FRAME_STYLE)
        modes_layout = QHBoxLayout(modes_frame)
        modes_layout.setContentsMargins(6, 6, 6, 6)
        modes_layout.setSpacing(6)

        self.button_brillouin = QPushButton("Brillouin")
        self.button_brillouin.setCheckable(True)
        self.button_brillouin.setChecked(False)
        self.button_brillouin.setStyleSheet(MODE_BUTTON_STYLE)
        self.button_brillouin.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.button_brillouin.toggled.connect(self._emit_mode_changed)
        self.button_brillouin.toggled.connect(self._update_derived_values)
        modes_layout.addWidget(self.button_brillouin)

        self.button_raman = QPushButton("Raman")
        self.button_raman.setCheckable(True)
        self.button_raman.setChecked(False)
        self.button_raman.setStyleSheet(MODE_BUTTON_STYLE)
        self.button_raman.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.button_raman.toggled.connect(self._emit_mode_changed)
        self.button_raman.toggled.connect(self._update_derived_values)
        modes_layout.addWidget(self.button_raman)

        content_layout.addWidget(modes_frame)

        # ==========================================================
        # Mapping  (X / Y / Z en µm,  T en s)
        # ==========================================================
        mapping_frame = QFrame()
        mapping_frame.setStyleSheet(PANEL_FRAME_STYLE)
        mapping_layout = QGridLayout(mapping_frame)
        mapping_layout.setContentsMargins(6, 6, 6, 6)
        mapping_layout.setHorizontalSpacing(6)
        mapping_layout.setVerticalSpacing(4)

        mapping_layout.setColumnStretch(0, 0)
        mapping_layout.setColumnStretch(1, 1)
        mapping_layout.setColumnStretch(2, 1)
        mapping_layout.setColumnStretch(3, 1)

        # En-têtes sans unités (X/Y/Z = µm, T = s)
        for col, header in enumerate(["Axis", "Size", "#Pix", "Step"]):
            lbl = QLabel(header)
            lbl.setStyleSheet(HEADER_LABEL_STYLE)
            lbl.setAlignment(Qt.AlignCenter)
            mapping_layout.addWidget(lbl, 0, col)

        float_val = QDoubleValidator(bottom=0.0)
        int_val = QIntValidator(1, 100000)

        # ---- X
        mapping_layout.addWidget(QLabel("X (µm)"), 1, 0)
        self.size_x_edit = QLineEdit("100")
        self.size_x_edit.setStyleSheet(LINE_EDIT_STYLE)
        self.size_x_edit.setValidator(float_val)
        mapping_layout.addWidget(self.size_x_edit, 1, 1)
        self.pix_x_edit = QLineEdit("11")
        self.pix_x_edit.setStyleSheet(LINE_EDIT_STYLE)
        self.pix_x_edit.setValidator(int_val)
        mapping_layout.addWidget(self.pix_x_edit, 1, 2)
        self.step_x_edit = QLineEdit("0.000")
        self.step_x_edit.setReadOnly(True)
        self.step_x_edit.setStyleSheet(READONLY_LINEEDIT_STYLE)
        mapping_layout.addWidget(self.step_x_edit, 1, 3)

        # ---- Y
        mapping_layout.addWidget(QLabel("Y (µm)"), 2, 0)
        self.size_y_edit = QLineEdit("100")
        self.size_y_edit.setStyleSheet(LINE_EDIT_STYLE)
        self.size_y_edit.setValidator(float_val)
        mapping_layout.addWidget(self.size_y_edit, 2, 1)
        self.pix_y_edit = QLineEdit("11")
        self.pix_y_edit.setStyleSheet(LINE_EDIT_STYLE)
        self.pix_y_edit.setValidator(int_val)
        mapping_layout.addWidget(self.pix_y_edit, 2, 2)
        self.step_y_edit = QLineEdit("0.000")
        self.step_y_edit.setReadOnly(True)
        self.step_y_edit.setStyleSheet(READONLY_LINEEDIT_STYLE)
        mapping_layout.addWidget(self.step_y_edit, 2, 3)

        # ---- Z
        mapping_layout.addWidget(QLabel("Z (µm)"), 3, 0)
        self.size_z_edit = QLineEdit("0")
        self.size_z_edit.setStyleSheet(LINE_EDIT_STYLE)
        self.size_z_edit.setValidator(float_val)
        mapping_layout.addWidget(self.size_z_edit, 3, 1)
        self.pix_z_edit = QLineEdit("1")
        self.pix_z_edit.setStyleSheet(LINE_EDIT_STYLE)
        self.pix_z_edit.setValidator(int_val)
        mapping_layout.addWidget(self.pix_z_edit, 3, 2)
        self.step_z_edit = QLineEdit("0.000")
        self.step_z_edit.setReadOnly(True)
        self.step_z_edit.setStyleSheet(READONLY_LINEEDIT_STYLE)
        mapping_layout.addWidget(self.step_z_edit, 3, 3)

        # ---- T  (taille calculée, #pix et step éditables)
        t_label = QLabel("T (s)")
        t_label.setToolTip(
            "Time lapse\n"
            "#Pix  = number of acquisitions\n"
            "Step  = interval between acquisitions (s)\n"
            "Size  = computed total duration = (#Pix - 1) × Step"
        )
        mapping_layout.addWidget(t_label, 4, 0)

        self.total_t_edit = QLineEdit("0.0")
        self.total_t_edit.setReadOnly(True)
        self.total_t_edit.setStyleSheet(READONLY_LINEEDIT_STYLE)
        self.total_t_edit.setToolTip("Total duration (computed)")
        mapping_layout.addWidget(self.total_t_edit, 4, 1)

        self.pix_t_edit = QLineEdit("1")
        self.pix_t_edit.setStyleSheet(LINE_EDIT_STYLE)
        self.pix_t_edit.setValidator(int_val)
        self.pix_t_edit.setToolTip("Number of time-lapse acquisitions")
        mapping_layout.addWidget(self.pix_t_edit, 4, 2)

        self.step_t_edit = QLineEdit("0")
        self.step_t_edit.setStyleSheet(LINE_EDIT_STYLE)
        self.step_t_edit.setValidator(QDoubleValidator(0.0, 1e9, 1))
        self.step_t_edit.setToolTip("Intervalle entre acquisitions (s)")
        mapping_layout.addWidget(self.step_t_edit, 4, 3)

        content_layout.addWidget(mapping_frame)

        # ==========================================================
        # Estimations
        # ==========================================================
        est_frame = QFrame()
        est_frame.setStyleSheet(PANEL_FRAME_STYLE)
        est_layout = QGridLayout(est_frame)
        est_layout.setContentsMargins(6, 6, 6, 6)
        est_layout.setHorizontalSpacing(6)
        est_layout.setVerticalSpacing(4)
        est_layout.setColumnStretch(0, 0)
        est_layout.setColumnStretch(1, 1)
        est_layout.setColumnStretch(2, 0)
        est_layout.setColumnStretch(3, 1)

        lbl_time = QLabel("Estimated time")
        lbl_time.setStyleSheet("color: white; font-weight: bold;")
        est_layout.addWidget(lbl_time, 0, 0)
        self.estimated_time_edit = QLineEdit("—")
        self.estimated_time_edit.setReadOnly(True)
        self.estimated_time_edit.setStyleSheet(READONLY_LINEEDIT_STYLE)
        est_layout.addWidget(self.estimated_time_edit, 0, 1)

        lbl_size = QLabel("Estimated size")
        lbl_size.setStyleSheet("color: white; font-weight: bold;")
        est_layout.addWidget(lbl_size, 0, 2)
        self.estimated_size_edit = QLineEdit("—")
        self.estimated_size_edit.setReadOnly(True)
        self.estimated_size_edit.setStyleSheet(READONLY_LINEEDIT_STYLE)
        est_layout.addWidget(self.estimated_size_edit, 0, 3)

        content_layout.addWidget(est_frame)

        # ==========================================================
        # Save  (dossier, nom, commentaires, format)
        # ==========================================================
        save_frame = QFrame()
        save_frame.setStyleSheet(PANEL_FRAME_STYLE)
        save_layout = QGridLayout(save_frame)
        save_layout.setContentsMargins(6, 6, 6, 6)
        save_layout.setHorizontalSpacing(6)
        save_layout.setVerticalSpacing(4)
        save_layout.setColumnStretch(0, 0)
        save_layout.setColumnStretch(1, 1)
        save_layout.setColumnStretch(2, 0)

        default_folder = default_dated_folder()

        folder_label = QLabel("Folder")
        folder_label.setStyleSheet("color: white; font-weight: bold;")
        save_layout.addWidget(folder_label, 0, 0)
        self.folder_line_edit = QLineEdit(default_folder)
        self.folder_line_edit.setStyleSheet(LINE_EDIT_STYLE)
        self.folder_line_edit.setMinimumWidth(0)
        self.folder_line_edit.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        save_layout.addWidget(self.folder_line_edit, 0, 1)
        self.folder_button = QPushButton()
        self.folder_button.setIcon(QIcon(icon_path("folder.svg")))
        self.folder_button.setStyleSheet(BUTTON_STYLE)
        self.folder_button.setFixedWidth(28)
        self.folder_button.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.folder_button.clicked.connect(self.choose_folder)
        save_layout.addWidget(self.folder_button, 0, 2)

        filename_label = QLabel("File Name")
        filename_label.setStyleSheet("color: white; font-weight: bold;")
        save_layout.addWidget(filename_label, 1, 0)
        self.filename_line_edit = QLineEdit()
        self.filename_line_edit.setPlaceholderText("Spectro acquisition name")
        self.filename_line_edit.setStyleSheet(LINE_EDIT_STYLE)
        self.filename_line_edit.setMinimumWidth(0)
        self.filename_line_edit.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        save_layout.addWidget(self.filename_line_edit, 1, 1, 1, 2)

        self.comment_text_edit = QPlainTextEdit()
        self.comment_text_edit.setPlaceholderText("Comments to be added to the spectro metadata")
        self.comment_text_edit.setStyleSheet(LINE_EDIT_STYLE)
        self.comment_text_edit.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self.comment_text_edit.setMinimumHeight(0)
        save_layout.addWidget(self.comment_text_edit, 2, 0, 2, 3)

        format_label = QLabel("File Format")
        format_label.setStyleSheet("color: white; font-weight: bold;")
        save_layout.addWidget(format_label, 4, 0)
        self.format_combo = QComboBox()
        self.format_combo.addItems(["OME-TIFF", "OME-Zarr", "HDF5-BLS"])
        self.format_combo.setToolTip(
            "OME-TIFF / OME-Zarr: the folder layout used so far.\n"
            "HDF5-BLS: one self-describing HDF5 file, for exchange with other\n"
            "Brillouin setups."
        )
        self.format_combo.setCurrentText("OME-TIFF")
        self.format_combo.setStyleSheet(COMBO_STYLE)
        self.format_combo.setMinimumWidth(0)
        self.format_combo.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.format_combo.setMinimumContentsLength(1)
        save_layout.addWidget(self.format_combo, 4, 1, 1, 2)

        content_layout.addWidget(save_frame)

        # ==========================================================
        # Acquire / Stop / Barre de progression
        # ==========================================================
        acq_frame = QFrame()
        acq_frame.setStyleSheet(PANEL_FRAME_STYLE)
        acq_layout = QGridLayout(acq_frame)
        acq_layout.setContentsMargins(6, 6, 6, 6)
        acq_layout.setHorizontalSpacing(6)
        acq_layout.setVerticalSpacing(4)
        acq_layout.setColumnStretch(0, 1)
        acq_layout.setColumnStretch(1, 1)

        self.button_acquire = QPushButton("Acquire")
        self.button_acquire.setIcon(QIcon(icon_path("REC.svg")))
        self.button_acquire.setStyleSheet(BUTTON_STYLE)
        self.button_acquire.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.button_acquire.clicked.connect(self._on_acquire_clicked)
        acq_layout.addWidget(self.button_acquire, 0, 0)

        self.button_stop = QPushButton("Stop")
        self.button_stop.setIcon(QIcon(icon_path("stop.svg")))
        self.button_stop.setStyleSheet(BUTTON_STYLE)
        self.button_stop.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.button_stop.clicked.connect(self.sigStopClicked.emit)
        acq_layout.addWidget(self.button_stop, 0, 1)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setFormat("Idle")
        self.progress_bar.setTextVisible(True)
        self.progress_bar.setStyleSheet(PROGRESS_BAR_STYLE)
        self.progress_bar.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        acq_layout.addWidget(self.progress_bar, 1, 0, 1, 2)
        # La progression spectro est affichée dans la barre globale en bas
        # du GUI (GlobalProgressWidget) ; la barre locale est masquée.
        self.progress_bar.hide()

        content_layout.addWidget(acq_frame)

        self.main_layout = main_layout
        self.main_layout.addWidget(content_widget)
        self.main_layout.addStretch()

        # ==========================================================
        # Connexions
        # ==========================================================
        for edit in (
            self.size_x_edit, self.size_y_edit, self.size_z_edit,
            self.pix_x_edit, self.pix_y_edit, self.pix_z_edit,
            self.pix_t_edit, self.step_t_edit,
        ):
            edit.textChanged.connect(self._update_derived_values)

        self._update_derived_values()
        self._emit_mode_changed()

    # ==========================================================
    # Settings dialog
    # ==========================================================
    def set_settings_manager(self, manager):
        """Inject the shared SettingsManager and load any saved settle/backlash.

        Same "fill only if empty" spirit as the other panels: a settings file
        with nothing under "spectro" yet (first run, or a version that
        predates this) leaves the built-in defaults set in __init__ alone.
        """
        self.settings_manager = manager
        saved = manager.get_spectro_settings() if manager is not None else {}
        if not saved:
            return

        self._settle_ms = float(saved.get("settle_ms", self._settle_ms))
        self._line_offset_x_um = float(saved.get("line_offset_x_um", self._line_offset_x_um))
        self._origin_overshoot_um = float(saved.get("origin_overshoot_um", self._origin_overshoot_um))
        self._stage_speed_mm_s = float(saved.get("stage_speed_mm_s", self._stage_speed_mm_s))
        self._raman_excitation_nm = float(saved.get("raman_excitation_nm", self._raman_excitation_nm) or 0.0)
        self._raman_row_start = int(saved.get("raman_row_start", self._raman_row_start) or 0)
        self._raman_row_count = int(saved.get("raman_row_count", self._raman_row_count) or 0)
        self._raman_calibration = saved.get("raman_calibration", self._raman_calibration) or None
        self._update_derived_values()

    def _export_settings(self) -> dict:
        return {
            "settle_ms": float(self._settle_ms),
            "line_offset_x_um": float(self._line_offset_x_um),
            "origin_overshoot_um": float(self._origin_overshoot_um),
            "stage_speed_mm_s": float(self._stage_speed_mm_s),
            "raman_excitation_nm": float(self._raman_excitation_nm),
            "raman_row_start": int(self._raman_row_start),
            "raman_row_count": int(self._raman_row_count),
            "raman_calibration": self._raman_calibration,
        }

    def get_raman_settings(self) -> dict:
        """What the Raman chain and the dataset need from Settings."""
        return {
            "excitation_nm": float(self._raman_excitation_nm),
            "row_start": int(self._raman_row_start),
            "row_count": int(self._raman_row_count),
            "calibration": self._raman_calibration,
        }

    @staticmethod
    def _describe_calibration(calibration: dict | None) -> str:
        if not calibration:
            return "none -- spectra on raw detector pixels"
        wavelengths = list(calibration.get("wavelengths_nm") or [])
        span = (f", {min(wavelengths):.2f}-{max(wavelengths):.2f} nm"
                if wavelengths else "")
        grating = calibration.get("grating")
        center = calibration.get("center_nm")
        setting = (f"grating {grating}, centre {float(center):.3f} nm"
                   if grating is not None and center is not None else "setting unknown")
        return f"{calibration.get('source') or 'table'}: {setting}, {len(wavelengths)} px{span}"

    def open_settings_dialog(self):
        from .Dialogs import SettingsDialog
        dialog = SettingsDialog("Spectro - Settings", self)

        row = QHBoxLayout()
        row.addWidget(QLabel("Settle time (ms):"))
        settle_edit = QLineEdit(str(self._settle_ms))
        row.addWidget(settle_edit)
        dialog.add_layout(row)

        row2 = QHBoxLayout()
        row2.addWidget(QLabel("Offset backlash X (µm):"))
        line_offset_edit = QLineEdit(str(self._line_offset_x_um))
        line_offset_edit.setToolTip(
            "Directional compensation of the mechanical play (backlash) in X.\n"
            "Signed offset applied to the commanded position of the return\n"
            "lines (odd, right→left) so that they physically coincide with\n"
            "the forward lines. The recorded position stays nominal.\n"
            "Positive value = shifts towards X+ ; negative = towards X−."
        )
        row2.addWidget(line_offset_edit)
        dialog.add_layout(row2)

        row3 = QHBoxLayout()
        row3.addWidget(QLabel("Origin return overshoot (µm):"))
        origin_overshoot_edit = QLineEdit(str(self._origin_overshoot_um))
        origin_overshoot_edit.setToolTip(
            "Anti-backlash for the return to origin at the end of a scan (X axis).\n"
            "The origin is always approached from the left (final move in +X)\n"
            "so as to take up the mechanical play.\n"
            "If the stage is right of centre (the normal case) it first moves\n"
            "left by this distance then approaches; if it is already on the\n"
            "left, it approaches directly (no extra move).\n"
            "At most one additional move per scan.\n"
            "0 = disabled. Must be greater than the mechanical play (~2 µm)."
        )
        row3.addWidget(origin_overshoot_edit)
        dialog.add_layout(row3)

        row4 = QHBoxLayout()
        row4.addWidget(QLabel("XY stage speed (mm/s):"))
        speed_edit = QLineEdit(str(self._stage_speed_mm_s))
        speed_edit.setToolTip(
            "Travel speed of the XY stage.\n"
            "Used only to estimate the acquisition time."
        )
        row4.addWidget(speed_edit)
        dialog.add_layout(row4)

        # ---- Raman -----------------------------------------------------
        raman_title = QLabel("Raman")
        raman_title.setStyleSheet("font-weight: bold; padding-top: 6px;")
        dialog.add_widget(raman_title)

        row5 = QHBoxLayout()
        row5.addWidget(QLabel("Excitation wavelength (nm):"))
        excitation_edit = QLineEdit(str(self._raman_excitation_nm))
        excitation_edit.setToolTip(
            "Recorded with every Raman dataset, so the Raman shift can be\n"
            "computed at analysis time. Not used for any computation here.\n"
            "0 = not stated."
        )
        row5.addWidget(excitation_edit)
        dialog.add_layout(row5)

        row6 = QHBoxLayout()
        row6.addWidget(QLabel("Detector rows binned: first"))
        row_start_edit = QLineEdit(str(self._raman_row_start))
        row6.addWidget(row_start_edit)
        row6.addWidget(QLabel("count"))
        row_count_edit = QLineEdit(str(self._raman_row_count))
        row_count_edit.setToolTip(
            "Band of sensor rows, along the slit, summed into each spectrum\n"
            "(on the chip when the camera allows it). Count 0 = full height.\n"
            "Narrowing it to the slit image keeps the read noise of the\n"
            "unlit rows out of the spectrum."
        )
        row6.addWidget(row_count_edit)
        dialog.add_layout(row6)

        # La calibration chargée reste en attente jusqu'à OK / Save value,
        # comme les autres champs du popup.
        pending = {"calibration": self._raman_calibration}

        row7 = QHBoxLayout()
        row7.addWidget(QLabel("Wavelength calibration:"))
        calibration_label = QLabel(self._describe_calibration(pending["calibration"]))
        calibration_label.setWordWrap(True)
        calibration_label.setStyleSheet("color: #b0b0b0;")
        calibration_label.setToolTip(
            "Pixel-to-wavelength table measured in LightField (IntelliCal) or\n"
            "WinSpec. Applied only at the grating and centre wavelength it was\n"
            "measured at; anywhere else spectra stay on raw detector pixels.\n"
            "Text file: one wavelength per line, or columns with a header\n"
            "naming the wavelength column. Optional '# grating = 2' and\n"
            "'# center_nm = 785.0' lines state the setting it belongs to;\n"
            "without them, the current setting is assumed."
        )
        row7.addWidget(calibration_label, stretch=1)
        load_button = QPushButton("Load...")
        clear_button = QPushButton("Clear")
        row7.addWidget(load_button)
        row7.addWidget(clear_button)
        dialog.add_layout(row7)

        def _load_calibration():
            if self.raman_calibration_loader is None:
                QMessageBox.warning(dialog, "Calibration", "No Raman spectrometer to check the file against.")
                return
            path, _ = QFileDialog.getOpenFileName(
                dialog, "Load a wavelength calibration", "",
                "Text tables (*.txt *.csv *.dat *.tsv);;All files (*)",
            )
            if not path:
                return
            try:
                calibration = self.raman_calibration_loader(path)
            except Exception as e:
                QMessageBox.warning(dialog, "Calibration refused", f"{os.path.basename(path)}:\n{e}")
                return
            pending["calibration"] = calibration.to_dict()
            calibration_label.setText(self._describe_calibration(pending["calibration"]))

        def _clear_calibration():
            pending["calibration"] = None
            calibration_label.setText(self._describe_calibration(None))

        load_button.clicked.connect(_load_calibration)
        clear_button.clicked.connect(_clear_calibration)

        def _on_accepted():
            try:
                self._settle_ms = max(0.0, float(settle_edit.text().replace(",", ".")))
            except Exception:
                pass
            try:
                self._line_offset_x_um = float(line_offset_edit.text().replace(",", "."))
            except Exception:
                pass
            try:
                self._origin_overshoot_um = float(origin_overshoot_edit.text().replace(",", "."))
            except Exception:
                pass
            try:
                self._stage_speed_mm_s = max(0.001, float(speed_edit.text().replace(",", ".")))
            except Exception:
                pass
            try:
                self._raman_excitation_nm = max(0.0, float(excitation_edit.text().replace(",", ".")))
            except Exception:
                pass
            try:
                self._raman_row_start = max(0, int(float(row_start_edit.text().replace(",", "."))))
            except Exception:
                pass
            try:
                self._raman_row_count = max(0, int(float(row_count_edit.text().replace(",", "."))))
            except Exception:
                pass
            self._raman_calibration = pending["calibration"]
            self._update_derived_values()
            self.settleTimeChanged.emit(float(self._settle_ms))
            self.sigRamanSettingsChanged.emit(self.get_raman_settings())

        dialog.accepted.connect(_on_accepted)

        def _save_values():
            _on_accepted()
            if self.settings_manager is not None:
                self.settings_manager.update_spectro_settings(**self._export_settings())
                self.settings_manager.save_spectro_settings()
                logger.info("[Spectro] settings saved for next launch")

        def _reset_origin():
            self._settle_ms = SPECTRO_SETTINGS_DEFAULTS["settle_ms"]
            self._line_offset_x_um = SPECTRO_SETTINGS_DEFAULTS["line_offset_x_um"]
            self._origin_overshoot_um = SPECTRO_SETTINGS_DEFAULTS["origin_overshoot_um"]
            self._stage_speed_mm_s = SPECTRO_SETTINGS_DEFAULTS["stage_speed_mm_s"]
            self._raman_excitation_nm = SPECTRO_SETTINGS_DEFAULTS["raman_excitation_nm"]
            self._raman_row_start = SPECTRO_SETTINGS_DEFAULTS["raman_row_start"]
            self._raman_row_count = SPECTRO_SETTINGS_DEFAULTS["raman_row_count"]
            self._raman_calibration = SPECTRO_SETTINGS_DEFAULTS["raman_calibration"]
            pending["calibration"] = self._raman_calibration

            settle_edit.setText(str(self._settle_ms))
            line_offset_edit.setText(str(self._line_offset_x_um))
            origin_overshoot_edit.setText(str(self._origin_overshoot_um))
            speed_edit.setText(str(self._stage_speed_mm_s))
            excitation_edit.setText(str(self._raman_excitation_nm))
            row_start_edit.setText(str(self._raman_row_start))
            row_count_edit.setText(str(self._raman_row_count))
            calibration_label.setText(self._describe_calibration(None))

            self._update_derived_values()
            self.settleTimeChanged.emit(float(self._settle_ms))
            self.sigRamanSettingsChanged.emit(self.get_raman_settings())

            if self.settings_manager is not None:
                self.settings_manager.update_spectro_settings(**self._export_settings())
                self.settings_manager.save_spectro_settings()
            logger.info("[Spectro] settings reset to built-in defaults")

        dialog.set_persistence_callbacks(_save_values, _reset_origin)
        dialog.exec()

    # ==========================================================
    # Helpers
    # ==========================================================
    def _emit_mode_changed(self):
        self.sigSpectroModeChanged.emit(
            self.button_brillouin.isChecked(),
            self.button_raman.isChecked()
        )

    @staticmethod
    def _safe_float(text: str, default: float = 0.0) -> float:
        try:
            return float((text or str(default)).replace(",", "."))
        except Exception:
            return float(default)

    @staticmethod
    def _safe_int(text: str, default: int = 1) -> int:
        try:
            return int(float((text or str(default)).replace(",", ".")))
        except Exception:
            return int(default)

    @staticmethod
    def _compute_step(size_um: float, pixels: int) -> float:
        if int(pixels) <= 1:
            return 0.0
        return float(size_um) / float(pixels - 1)

    @staticmethod
    def _format_duration(seconds: float) -> str:
        seconds = max(float(seconds), 0.0)
        if seconds < 60:
            return f"{seconds:.1f} s"
        minutes = int(seconds // 60)
        rem_s = seconds - 60 * minutes
        if minutes < 60:
            return f"{minutes} min {rem_s:.0f} s"
        hours = minutes // 60
        rem_m = minutes % 60
        return f"{hours} h {rem_m} min"

    @staticmethod
    def _format_bytes(n_bytes: float) -> str:
        n = float(max(n_bytes, 0.0))
        if n < 1024:
            return f"{n:.0f} B"
        if n < 1024 ** 2:
            return f"{n / 1024:.1f} KB"
        if n < 1024 ** 3:
            return f"{n / 1024 ** 2:.1f} MB"
        return f"{n / 1024 ** 3:.2f} GB"

    def _update_derived_values(self):
        size_x = self._safe_float(self.size_x_edit.text(), 100.0)
        size_y = self._safe_float(self.size_y_edit.text(), 100.0)
        size_z = self._safe_float(self.size_z_edit.text(), 0.0)

        pix_x = max(1, self._safe_int(self.pix_x_edit.text(), 11))
        pix_y = max(1, self._safe_int(self.pix_y_edit.text(), 11))
        pix_z = max(1, self._safe_int(self.pix_z_edit.text(), 1))

        pix_t = max(1, self._safe_int(self.pix_t_edit.text(), 1))
        step_t_s = max(0.0, self._safe_float(self.step_t_edit.text(), 0.0))

        # X/Y/Z : step calculé depuis size/pix
        self.step_x_edit.setText(f"{self._compute_step(size_x, pix_x):.3f}")
        self.step_y_edit.setText(f"{self._compute_step(size_y, pix_y):.3f}")
        self.step_z_edit.setText(f"{self._compute_step(size_z, pix_z):.3f}")

        # T : durée totale calculée depuis pix et step
        total_t_s = (pix_t - 1) * step_t_s if pix_t > 1 else 0.0
        self.total_t_edit.setText(f"{total_t_s:.1f}")

        # Exposition par point : les deux modes s'enchaînent à chaque point
        # (Brillouin puis Raman), donc leurs durées s'additionnent ; un spectre
        # Raman dure exposition x moyennes.
        raman_ms = self._raman_exposure_ms * max(1, int(self._raman_averages))
        exposure_ms = 0.0
        if self.button_brillouin.isChecked():
            exposure_ms += self._brillouin_exposure_ms
        if self.button_raman.isChecked():
            exposure_ms += raman_ms
        if not self.button_brillouin.isChecked() and not self.button_raman.isChecked():
            exposure_ms = max(self._brillouin_exposure_ms, raman_ms)

        n_xy = pix_x * pix_y
        step_x = self._compute_step(size_x, pix_x)
        step_y = self._compute_step(size_y, pix_y)
        if n_xy > 1:
            total_x_um = pix_y * max(0, pix_x - 1) * step_x
            total_y_um = max(0, pix_y - 1) * step_y
            avg_xy_um = (total_x_um + total_y_um) / n_xy
        else:
            avg_xy_um = 0.0
        stage_speed_um_s = self._stage_speed_mm_s * 1000.0
        move_time_s = avg_xy_um / stage_speed_um_s if stage_speed_um_s > 0 else 0.0

        n_pix = n_xy * pix_z
        t_per_pix_s = (
            max(0.0, exposure_ms + self._settle_ms) / 1000.0
            + move_time_s
            + self._PIXEL_OVERHEAD_S
        )
        scan_s = n_pix * t_per_pix_s
        total_acq_s = pix_t * scan_s + max(0, pix_t - 1) * step_t_s
        self.estimated_time_edit.setText(self._format_duration(total_acq_s))

        total_bytes = 0.0
        has_any_mode = False

        if self.button_brillouin.isChecked():
            roi = dict(self._brillouin_roi_state or {})
            if bool(roi.get("enabled", False)):
                img_h = max(1, int(roi.get("height", self._BRILLOUIN_IMG_H)))
                img_w = max(1, int(roi.get("width", self._BRILLOUIN_IMG_W)))
            else:
                img_h = self._BRILLOUIN_IMG_H
                img_w = self._BRILLOUIN_IMG_W
            has_any_mode = True
            total_bytes += img_h * img_w * 4 * n_pix

        if self.button_raman.isChecked():
            has_any_mode = True
            total_bytes += self._raman_spectral_pixels * self._RAMAN_BYTES_PER_POINT * n_pix

        if not has_any_mode:
            self.estimated_size_edit.setText("—")
        else:
            self.estimated_size_edit.setText(self._format_bytes(total_bytes * pix_t))

    def _on_acquire_clicked(self):
        if not self.button_brillouin.isChecked() and not self.button_raman.isChecked():
            QMessageBox.warning(
                self,
                "No spectro mode enabled",
                "Please enable Brillouin and/or Raman before starting acquisition."
            )
            return

        if not self.filename_line_edit.text().strip():
            QMessageBox.warning(
                self,
                "Missing file name",
                "Please enter a file name before starting acquisition."
            )
            return

        folder = self.folder_line_edit.text().strip()
        if folder:
            os.makedirs(folder, exist_ok=True)

        self.sigAcquireClicked.emit()

    # ==========================================================
    # Public API
    # ==========================================================
    def choose_folder(self):
        folder = QFileDialog.getExistingDirectory(
            self,
            "Select spectro output folder",
            self.folder_line_edit.text(),
            QFileDialog.ShowDirsOnly
        )
        if folder:
            self.folder_line_edit.setText(folder)

    def get_modes(self):
        return {
            "brillouin": self.button_brillouin.isChecked(),
            "raman": self.button_raman.isChecked(),
        }

    def set_modes(self, brillouin: bool, raman: bool):
        self.button_brillouin.blockSignals(True)
        self.button_raman.blockSignals(True)
        self.button_brillouin.setChecked(bool(brillouin))
        self.button_raman.setChecked(bool(raman))
        self.button_brillouin.blockSignals(False)
        self.button_raman.blockSignals(False)
        self._emit_mode_changed()
        self._update_derived_values()

    def set_brillouin_roi_state(self, roi: dict):
        roi = dict(roi or {})
        self._brillouin_roi_state = {
            "enabled": bool(roi.get("enabled", False)),
            "x": int(roi.get("x", 0)),
            "y": int(roi.get("y", 0)),
            "width": int(roi.get("width", 1200)),
            "height": int(roi.get("height", 1200)),
        }
        self._update_derived_values()

    def set_brillouin_exposure_ms(self, ms: float):
        self._brillouin_exposure_ms = max(0.0, float(ms))
        self._update_derived_values()

    def set_raman_averages(self, averages: float):
        self._raman_averages = max(1, int(averages))
        self._update_derived_values()

    def set_raman_spectral_pixels(self, n_pixels: int):
        """Spectrum length, once the detector has said it: sizes the estimate."""
        self._raman_spectral_pixels = max(1, int(n_pixels))
        self._update_derived_values()

    def set_raman_exposure_ms(self, ms: float):
        self._raman_exposure_ms = max(0.0, float(ms))
        self._update_derived_values()

    def get_acquisition_parameters(self):
        size_x = self._safe_float(self.size_x_edit.text(), 100.0)
        size_y = self._safe_float(self.size_y_edit.text(), 100.0)
        size_z = self._safe_float(self.size_z_edit.text(), 0.0)

        pix_x = max(1, self._safe_int(self.pix_x_edit.text(), 11))
        pix_y = max(1, self._safe_int(self.pix_y_edit.text(), 11))
        pix_z = max(1, self._safe_int(self.pix_z_edit.text(), 1))

        pix_t = max(1, self._safe_int(self.pix_t_edit.text(), 1))
        step_t_s = max(0.0, self._safe_float(self.step_t_edit.text(), 0.0))

        return {
            "settle_ms": self._settle_ms,
            "line_offset_x_um": self._line_offset_x_um,
            "origin_overshoot_um": self._origin_overshoot_um,
            "size_x_um": size_x,
            "size_y_um": size_y,
            "size_z_um": size_z,
            "pixels_x": pix_x,
            "pixels_y": pix_y,
            "pixels_z": pix_z,
            "step_x_um": self._compute_step(size_x, pix_x),
            "step_y_um": self._compute_step(size_y, pix_y),
            "step_z_um": self._compute_step(size_z, pix_z),
            "serpentine": True,
            "brillouin_roi": dict(self._brillouin_roi_state),
            "n_repeats": pix_t,
            "repeat_delay_s": step_t_s,
        }

    def reset_progress(self):
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setFormat("Idle")
        self._update_derived_values()

    def set_progress(self, done: int, total: int):
        total = max(1, int(total))
        done = max(0, min(int(done), total))
        percent = int(round(100.0 * done / total))
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(percent)
        self.progress_bar.setFormat(f"{done}/{total} ({percent}%)")

    def get_save_parameters(self):
        return {
            "folder": self.folder_line_edit.text().strip(),
            "filename": self.filename_line_edit.text().strip(),
            "comment": self.comment_text_edit.toPlainText().strip(),
            "format": self.format_combo.currentText().strip(),
        }

    def set_running(self, running: bool):
        self.button_acquire.setEnabled(not bool(running))
        self.button_stop.setEnabled(True)
        if bool(running):
            self.progress_bar.setFormat("0%")
            self.progress_bar.setValue(0)
        else:
            if self.progress_bar.value() < 100:
                self.progress_bar.setFormat("Stopped")

    def set_estimated_time_text(self, text: str):
        self.estimated_time_edit.setText(str(text))

    def set_estimated_size_text(self, text: str):
        self.estimated_size_edit.setText(str(text))
