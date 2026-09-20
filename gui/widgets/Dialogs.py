from PySide6.QtWidgets import (QVBoxLayout, QHBoxLayout, QPushButton, QDialog)

class SettingsDialog(QDialog):
    def __init__(self, title, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(300)

        # Layout principal
        self.main_layout = QVBoxLayout(self)

        # Conteneur pour les widgets spécifiques
        self.content_layout = QVBoxLayout()
        self.main_layout.addLayout(self.content_layout)

        self._on_save_values = None
        self._on_reset_origin = None

        button_row = QHBoxLayout()

        # Cachés tant qu'aucun appelant n'a fourni de callbacks (cf.
        # set_persistence_callbacks) : tous les popups settings n'ont pas
        # encore été convertis à la persistance disque.
        self.reset_button = QPushButton("Reset origin")
        self.reset_button.setToolTip(
            "Discard any customisation and go back to the built-in defaults."
        )
        self.reset_button.clicked.connect(self._handle_reset_origin)
        self.reset_button.setVisible(False)
        button_row.addWidget(self.reset_button)

        self.save_values_button = QPushButton("Save value")
        self.save_values_button.setToolTip(
            "Apply these values and remember them for the next time DeepLight starts."
        )
        self.save_values_button.clicked.connect(self._handle_save_values)
        self.save_values_button.setVisible(False)
        button_row.addWidget(self.save_values_button)

        button_row.addStretch()

        # Bouton de validation
        self.ok_button = QPushButton("OK")
        self.ok_button.clicked.connect(self.accept)
        button_row.addWidget(self.ok_button)

        self.main_layout.addLayout(button_row)

    def add_widget(self, widget):
        """Add a custom widget to the dialog."""
        self.content_layout.addWidget(widget)

    def add_layout(self, layout):
        """Add a custom layout to the dialog."""
        self.content_layout.addLayout(layout)

    def set_persistence_callbacks(self, on_save_values, on_reset_origin):
        """Reveal 'Save value' / 'Reset origin', wired to this dialog's own logic.

        `on_save_values` applies the current fields (as OK would) and writes
        them to disk, so they are still there next time DeepLight starts.
        `on_reset_origin` restores the built-in defaults -- into the fields,
        into memory, and onto disk, so it is a genuine undo of an earlier
        mistaken save rather than a cosmetic reset a restart would discard.
        """
        self._on_save_values = on_save_values
        self._on_reset_origin = on_reset_origin
        self.save_values_button.setVisible(True)
        self.reset_button.setVisible(True)

    def _handle_save_values(self):
        if self._on_save_values is not None:
            self._on_save_values()

    def _handle_reset_origin(self):
        if self._on_reset_origin is not None:
            self._on_reset_origin()
