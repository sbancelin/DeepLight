from PySide6.QtWidgets import (QDockWidget, QWidget, QVBoxLayout, QScrollArea, QSizePolicy)
from PySide6.QtCore import Qt


class PanelDock(QDockWidget):
    """Dock vertical contenant plusieurs panels dans une zone scrollable."""
    def __init__(self, title="", parent=None):
        super().__init__(title, parent)

        self.setFeatures(QDockWidget.NoDockWidgetFeatures)
        self.setFloating(False)
        self.setTitleBarWidget(QWidget())

        root = QWidget()
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setFrameShape(QScrollArea.NoFrame)

        MIN_DOCK_WIDTH = 400
        MAX_DOCK_WIDTH = 1000
        self.setMinimumWidth(MIN_DOCK_WIDTH)
        self.setMaximumWidth(MAX_DOCK_WIDTH)

        self.container = QWidget()
        self.container.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)

        self.container_layout = QVBoxLayout(self.container)
        self.container_layout.setContentsMargins(2, 2, 2, 2)
        self.container_layout.setSpacing(8)
        self.container_layout.setAlignment(Qt.AlignTop)

        self.scroll.setWidget(self.container)
        root_layout.addWidget(self.scroll)

        self.setWidget(root)

    def add_panel(self, panel):
        self.container_layout.addWidget(panel)
        if hasattr(panel, "toggled"):
            panel.toggled.connect(self._refresh_scroll_area)

    def _refresh_scroll_area(self):
        self.container.adjustSize()
        self.container.updateGeometry()