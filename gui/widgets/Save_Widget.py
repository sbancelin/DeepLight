from PySide6.QtWidgets import (QWidget, QVBoxLayout, QPushButton, QGridLayout, QLabel, 
                               QLineEdit, QComboBox, QFileDialog, QPlainTextEdit, QMessageBox, QSizePolicy)
from PySide6.QtCore import Qt, Signal

from ...config import default_dated_folder
from ..resources import icon_path
from PySide6.QtGui import QIcon

import os


LINE_EDIT_STYLE = """
    QLineEdit {
        background-color: #333;
        color: white;
        border: 1px solid #555;
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

class SaveWidget(QWidget):
    """Widget handling how files are saved."""

    sigSaveClicked = Signal(str, str, str, str)  # Signal émis lors de la sauvegarde (dossier, nom, format, comment)

    #: Snapshot of what is displayed, scale bar burnt in: to a PNG beside the
    #: data, or straight to the clipboard for a notebook or a slide.
    sigSnapshotPng = Signal()
    sigSnapshotClipboard = Signal()

    #: Acquisition preset: the scan, the detectors and the optics as a file,
    #: so a series can be set up once and picked up again next week.
    sigPresetSave = Signal()
    sigPresetLoad = Signal()

    def __init__(self, parent=None):

        super().__init__(parent)
        self.main_layout = QVBoxLayout(self)
        self.main_layout.setContentsMargins(2, 2, 2, 2)
        self.main_layout.setSpacing(4)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)

        content_widget = QWidget()
        content_widget.setMinimumWidth(0)
        content_widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)

        content_layout = QVBoxLayout(content_widget)
        content_layout.setContentsMargins(2, 2, 2, 2)
        content_layout.setSpacing(4)

        # Layout pour les paramètres (grid)
        grid_layout = QGridLayout()
        grid_layout.setContentsMargins(0, 0, 0, 0)
        grid_layout.setHorizontalSpacing(6)
        grid_layout.setVerticalSpacing(4)

        grid_layout.setColumnMinimumWidth(0, 10)   # labels
        grid_layout.setColumnStretch(0, 0)
        grid_layout.setColumnStretch(1, 1)
        grid_layout.setColumnStretch(2, 1)
        grid_layout.setColumnStretch(3, 0)

        # --- Ligne 1 : Folder ---
        folder_label = QLabel("Folder")
        folder_label.setStyleSheet("color: white; font-weight: bold;")
        grid_layout.addWidget(folder_label, 0, 0)

        # Champ pour l'adresse du dossier (racine configurable, voir config.py)
        self.folder_line_edit = QLineEdit(default_dated_folder())
        self.folder_line_edit.setStyleSheet(LINE_EDIT_STYLE)
        self.folder_line_edit.setMinimumWidth(0)
        self.folder_line_edit.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        grid_layout.addWidget(self.folder_line_edit, 0, 1)

        # Bouton pour choisir le dossier (avec icône)
        self.folder_button = QPushButton()
        self.folder_button.setIcon(QIcon(icon_path("folder.svg")))
        self.folder_button.setStyleSheet(BUTTON_STYLE)
        self.folder_button.clicked.connect(self.choose_folder)
        self.folder_button.setFixedWidth(28)
        self.folder_button.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        grid_layout.addWidget(self.folder_button, 0, 2)

        # --- Ligne 2 : Name ---
        name_label = QLabel("File Name")
        name_label.setStyleSheet("color: white; font-weight: bold;")
        grid_layout.addWidget(name_label, 1, 0)

        # Champ pour le nom du fichier
        self.filename_line_edit = QLineEdit()
        self.filename_line_edit.setPlaceholderText("File name")
        self.filename_line_edit.setStyleSheet(LINE_EDIT_STYLE)
        self.filename_line_edit.setMinimumWidth(0)
        self.filename_line_edit.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        grid_layout.addWidget(self.filename_line_edit, 1, 1, 1, 2)  # Sur 2 colonnes

        self.comment_text_edit = QPlainTextEdit()
        self.comment_text_edit.setPlaceholderText("Comments to be added to the file metadata")
        self.comment_text_edit.setStyleSheet(LINE_EDIT_STYLE)
        self.comment_text_edit.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self.comment_text_edit.setMinimumHeight(0)
        grid_layout.addWidget(self.comment_text_edit, 2, 0, 2, 3)

        # --- Ligne 3 : Format and Save ---
        format_label = QLabel("File Format")
        format_label.setStyleSheet("color: white; font-weight: bold;")
        grid_layout.addWidget(format_label, 4, 0)

        # Menu déroulant pour le format
        self.format_combo = QComboBox()
        self.format_combo.addItems(["OME-TIFF", "OME-Zarr"])
        self.format_combo.setCurrentText("OME-TIFF")  # défaut
        self.format_combo.setStyleSheet(COMBO_STYLE)
        self.format_combo.setMinimumWidth(0)
        self.format_combo.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.format_combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.format_combo.setMinimumContentsLength(1)
        grid_layout.addWidget(self.format_combo, 4, 1)

        # Bouton Save
        self.save_button = QPushButton("Save")
        self.save_button.setStyleSheet(BUTTON_STYLE)
        self.save_button.setMinimumWidth(0)
        self.save_button.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.save_button.clicked.connect(self.save_file)
        grid_layout.addWidget(self.save_button, 4, 2)

        # --- Ligne 5 : REC Save ---
        rec_format_label = QLabel("REC Format")
        rec_format_label.setStyleSheet("color: white; font-weight: bold;")
        grid_layout.addWidget(rec_format_label, 5, 0)

        # Menu déroulant pour le format
        self.rec_format_combo = QComboBox()
        self.rec_format_combo.addItems(["OME-TIFF", "OME-Zarr"])
        self.rec_format_combo.setCurrentText("OME-TIFF")  # défaut: écrit à la fin
        self.rec_format_combo.setStyleSheet(COMBO_STYLE)
        self.rec_format_combo.setMinimumWidth(0)
        self.rec_format_combo.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.rec_format_combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.rec_format_combo.setMinimumContentsLength(1)
        grid_layout.addWidget(self.rec_format_combo, 5, 1)

        # --- Estimated size (read-only) ---
        self.estimated_size_label = QLabel("—")
        self.estimated_size_label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.estimated_size_label.setStyleSheet("""
            QLabel {
                background-color: #252525;
                color: #888;
                border: 1px solid #444;
                border-radius: 3px;
                padding: 2px 6px 2px 6px;
                min-height: 20px;
            }
        """)
        self.estimated_size_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        grid_layout.addWidget(self.estimated_size_label, 5, 2)

        # --- Ligne 6 : Snapshot ---
        # L'image telle qu'elle est affichée, barre d'échelle incluse : le PNG
        # va à côté des données, le presse-papier directement dans un carnet.
        snapshot_label = QLabel("Snapshot")
        snapshot_label.setStyleSheet("color: white; font-weight: bold;")
        grid_layout.addWidget(snapshot_label, 6, 0)

        self.snapshot_clipboard_button = QPushButton("Clipboard")
        self.snapshot_clipboard_button.setToolTip(
            "Copy the displayed image with its scale bar (Ctrl+Shift+C)"
        )
        self.snapshot_clipboard_button.setStyleSheet(BUTTON_STYLE)
        self.snapshot_clipboard_button.setMinimumWidth(0)
        self.snapshot_clipboard_button.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.snapshot_clipboard_button.clicked.connect(lambda: self.sigSnapshotClipboard.emit())
        grid_layout.addWidget(self.snapshot_clipboard_button, 6, 1)

        self.snapshot_png_button = QPushButton("PNG")
        self.snapshot_png_button.setToolTip(
            "Write the displayed image with its scale bar as PNG (Ctrl+P)"
        )
        self.snapshot_png_button.setStyleSheet(BUTTON_STYLE)
        self.snapshot_png_button.setMinimumWidth(0)
        self.snapshot_png_button.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.snapshot_png_button.clicked.connect(lambda: self.sigSnapshotPng.emit())
        grid_layout.addWidget(self.snapshot_png_button, 6, 2)

        # --- Ligne 7 : Preset d'acquisition ---
        # Le scan, les détecteurs et l'optique dans un fichier : une série se
        # règle une fois et se retrouve la semaine suivante.
        preset_label = QLabel("Preset")
        preset_label.setStyleSheet("color: white; font-weight: bold;")
        grid_layout.addWidget(preset_label, 7, 0)

        self.preset_load_button = QPushButton("Load")
        self.preset_load_button.setToolTip("Load an acquisition preset (scan, detectors, optics)")
        self.preset_load_button.setStyleSheet(BUTTON_STYLE)
        self.preset_load_button.setMinimumWidth(0)
        self.preset_load_button.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.preset_load_button.clicked.connect(lambda: self.sigPresetLoad.emit())
        grid_layout.addWidget(self.preset_load_button, 7, 1)

        self.preset_save_button = QPushButton("Save")
        self.preset_save_button.setToolTip("Save the current acquisition settings as a preset")
        self.preset_save_button.setStyleSheet(BUTTON_STYLE)
        self.preset_save_button.setMinimumWidth(0)
        self.preset_save_button.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.preset_save_button.clicked.connect(lambda: self.sigPresetSave.emit())
        grid_layout.addWidget(self.preset_save_button, 7, 2)

        # Ajout du layout grid au layout principal
        content_layout.addLayout(grid_layout)
        self.main_layout.addWidget(content_widget)
        self.main_layout.addStretch()

    def choose_folder(self):
        """Open a dialog for choosing a folder."""
        folder = QFileDialog.getExistingDirectory(
            self,
            "Select a folder",
            self.folder_line_edit.text(),
            QFileDialog.ShowDirsOnly
        )
        if folder:
            self.folder_line_edit.setText(folder)

    def save_file(self):
        """Emit a signal carrying the save information."""
        folder = self.folder_line_edit.text().strip()
        filename = self.filename_line_edit.text().strip()
        file_format = self.format_combo.currentText()
        comment = self.comment_text_edit.toPlainText().strip()

        # Vérifications basiques
        if not os.path.exists(folder):
            os.makedirs(folder, exist_ok=True)

        if not filename:
            QMessageBox.warning(
                self,
                "Missing file name",
                "Please enter a file name before saving."
            )
            return

        self.sigSaveClicked.emit(folder, filename, file_format, comment)

    def get_manual_format(self) -> str:
        return self.format_combo.currentText().strip()

    def get_rec_format(self) -> str:
        return self.rec_format_combo.currentText().strip()

    def set_manual_format(self, fmt: str):
        if self.format_combo.findText(str(fmt)) >= 0:
            self.format_combo.setCurrentText(str(fmt))

    def set_rec_format(self, fmt: str):
        if self.rec_format_combo.findText(str(fmt)) >= 0:
            self.rec_format_combo.setCurrentText(str(fmt))

    def set_estimated_size_text(self, text: str):
        self.estimated_size_label.setText(text)