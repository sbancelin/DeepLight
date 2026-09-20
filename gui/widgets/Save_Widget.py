from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QGridLayout, QLabel,
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

HEADER_LABEL_STYLE = "color: white; font-weight: bold;"

class SaveWidget(QWidget):
    """Widget handling how files are saved."""

    sigSaveClicked = Signal(str, str, str, str)  # Signal émis lors de la sauvegarde (dossier, nom, format, comment)

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

        # ---------------------------------------------------------------
        # Where and under what name: shared by the frame save and REC.
        # ---------------------------------------------------------------
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
        folder_label.setStyleSheet(HEADER_LABEL_STYLE)
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
        name_label.setStyleSheet(HEADER_LABEL_STYLE)
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

        content_layout.addLayout(grid_layout)

        # ---------------------------------------------------------------
        # Two formats, side by side, no box: a centred header names each
        # column so "Acquisition" (REC, whole series) and "Frame" (one
        # displayed frame, on demand) stay visually apart without the
        # weight of a bordered group.
        # ---------------------------------------------------------------
        formats_layout = QHBoxLayout()
        formats_layout.setContentsMargins(0, 4, 0, 0)
        formats_layout.setSpacing(12)

        # --- Acquisition (REC) column ---
        rec_column = QVBoxLayout()
        rec_column.setSpacing(4)

        rec_title = QLabel("Acquisition Format")
        rec_title.setStyleSheet(HEADER_LABEL_STYLE)
        rec_title.setAlignment(Qt.AlignCenter)
        rec_title.setToolTip("Format used when recording a full acquisition (REC button), for the whole stack/series.")
        rec_column.addWidget(rec_title)

        rec_row = QHBoxLayout()
        rec_row.setSpacing(6)

        self.rec_format_combo = QComboBox()
        self.rec_format_combo.addItems(["OME-TIFF", "OME-Zarr"])
        self.rec_format_combo.setCurrentText("OME-TIFF")  # défaut: écrit à la fin
        self.rec_format_combo.setStyleSheet(COMBO_STYLE)
        self.rec_format_combo.setMinimumWidth(0)
        self.rec_format_combo.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.rec_format_combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.rec_format_combo.setMinimumContentsLength(1)
        rec_row.addWidget(self.rec_format_combo)

        self.estimated_size_label = QLabel("—")
        self.estimated_size_label.setAlignment(Qt.AlignCenter)
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
        rec_row.addWidget(self.estimated_size_label)

        rec_column.addLayout(rec_row)
        formats_layout.addLayout(rec_column)

        # --- Frame column ---
        frame_column = QVBoxLayout()
        frame_column.setSpacing(4)

        frame_title = QLabel("Frame Format")
        frame_title.setStyleSheet(HEADER_LABEL_STYLE)
        frame_title.setAlignment(Qt.AlignCenter)
        frame_title.setToolTip("Format used by the Save button, for the currently displayed frame's data.")
        frame_column.addWidget(frame_title)

        frame_row = QHBoxLayout()
        frame_row.setSpacing(6)

        self.format_combo = QComboBox()
        self.format_combo.addItems(["OME-TIFF", "OME-Zarr"])
        self.format_combo.setCurrentText("OME-TIFF")  # défaut
        self.format_combo.setStyleSheet(COMBO_STYLE)
        self.format_combo.setMinimumWidth(0)
        self.format_combo.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.format_combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.format_combo.setMinimumContentsLength(1)
        frame_row.addWidget(self.format_combo)

        self.save_button = QPushButton("Save")
        self.save_button.setStyleSheet(BUTTON_STYLE)
        self.save_button.setMinimumWidth(0)
        self.save_button.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.save_button.clicked.connect(self.save_file)
        frame_row.addWidget(self.save_button)

        frame_column.addLayout(frame_row)
        formats_layout.addLayout(frame_column)

        content_layout.addLayout(formats_layout)

        # Note : le snapshot affiché (PNG horodaté / presse-papier, barre
        # d'échelle incluse) n'a plus de boutons dédiés -- Ctrl+S et Ctrl+E
        # suffisent et n'encombrent pas ce panneau.

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
