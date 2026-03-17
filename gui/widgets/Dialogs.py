from PySide6.QtWidgets import (QVBoxLayout, QPushButton, QDialog)

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

        # Bouton de validation
        self.ok_button = QPushButton("OK")
        self.ok_button.clicked.connect(self.accept)
        self.main_layout.addWidget(self.ok_button)

    def add_widget(self, widget):
        """Ajoute un widget personnalisé au dialogue."""
        self.content_layout.addWidget(widget)

    def add_layout(self, layout):
        """Ajoute un layout personnalisé au dialogue."""
        self.content_layout.addLayout(layout)
