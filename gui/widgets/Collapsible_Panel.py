from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QSizePolicy, QFrame)
from PySide6.QtCore import Qt, QPropertyAnimation, QEasingCurve, Signal, QPoint
from PySide6.QtGui import QIcon


class ResizeHandle(QWidget):
    dragDelta = Signal(int)  # delta vertical

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(8)
        self.setCursor(Qt.SizeVerCursor)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._dragging = False
        self._last_global_pos = QPoint()

        self.setStyleSheet("""
            QWidget {
                background: transparent;
            }
        """)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._dragging = True
            self._last_global_pos = event.globalPosition().toPoint()
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._dragging:
            pos = event.globalPosition().toPoint()
            dy = pos.y() - self._last_global_pos.y()
            self._last_global_pos = pos
            self.dragDelta.emit(dy)
            event.accept()
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._dragging = False
        super().mouseReleaseEvent(event)

    def paintEvent(self, event):
        from PySide6.QtGui import QPainter, QColor, QPen
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)

        pen = QPen(QColor("#666"))
        pen.setWidth(1)
        p.setPen(pen)

        w = self.width()
        y = self.height() // 2
        for offset in (-3, 0, 3):
            p.drawLine(w // 2 - 20, y + offset, w // 2 + 20, y + offset)


class CollapsiblePanel(QWidget):
    toggled = Signal(bool)  # True = ouvert, False = fermé

    def __init__(
            self,
            title="",
            content_widget=None,
            collapsed=False,
            settings_callback=None,
            preferred_content_height=None,
            parent=None
        ):
        super().__init__(parent)

        self._collapsed = collapsed
        self._settings_callback = settings_callback
        self._content_widget = content_widget or QWidget()
        self._preferred_content_height = preferred_content_height
        self._user_content_height = preferred_content_height
        self._min_content_height = 80
        self._max_content_height = 1200
        
        self._content_widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)

        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)

        self.main_layout = QVBoxLayout(self)
        self.main_layout.setContentsMargins(0, 0, 0, 0)
        self.main_layout.setSpacing(0)

        self.header = self._create_header(title)
        self.main_layout.addWidget(self.header)

        self.content_container = QFrame(self)
        self.content_container.setObjectName("contentContainer")
        self.content_layout = QVBoxLayout(self.content_container)
        self.content_container.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self.content_layout.setContentsMargins(3, 3, 3, 3)
        self.content_layout.setSpacing(0)
        self.content_layout.addWidget(self._content_widget)
        self.main_layout.addWidget(self.content_container)

        self.resize_handle = ResizeHandle(self)
        self.resize_handle.setVisible(not self._collapsed)
        self.resize_handle.dragDelta.connect(self._resize_by_handle)
        self.main_layout.addWidget(self.resize_handle)

        self.animation = QPropertyAnimation(self.content_container, b"maximumHeight", self)
        self.animation.setDuration(180)
        self.animation.setEasingCurve(QEasingCurve.InOutCubic)
        self.animation.finished.connect(self._on_animation_finished)

        self.setStyleSheet("""
            QFrame#dockHeader {
                background-color: #2E8B57;
                border: 1px solid #3AB16F;
                border-bottom: 1px solid #1f5c3a;
                border-top-left-radius: 8px;
                border-top-right-radius: 8px;
            }

            QFrame#contentContainer {
                background-color: #222;
                border-left: 1px solid #3a3a3a;
                border-right: 1px solid #3a3a3a;
                border-bottom: 1px solid #3a3a3a;
                border-bottom-left-radius: 8px;
                border-bottom-right-radius: 8px;
            }

            QLabel {
                color: white;
            }
        """)

        if self._collapsed:
            self.content_container.setVisible(False)
            self.content_container.setMinimumHeight(0)
            self.content_container.setMaximumHeight(0)
            self.resize_handle.setVisible(False)
            self._update_arrow()
        else:
            h = self._content_target_height()
            self.content_container.setVisible(True)
            self.content_container.setMinimumHeight(0)
            self.content_container.setMaximumHeight(h)
            self.resize_handle.setVisible(True)
            self._update_arrow()

    def _create_header(self, title):
        header = QFrame(self)
        header.setObjectName("dockHeader")
        header.setFixedHeight(22)

        layout = QHBoxLayout(header)
        layout.setContentsMargins(8, 0, 8, 0)
        layout.setSpacing(6)

        self.toggle_button = QPushButton("▾", header)
        self.toggle_button.setFixedSize(22, 22)
        self.toggle_button.setStyleSheet("""
            QPushButton {
                background: transparent;
                border: none;
                border-radius: 5px;
                color: white;
                font-size: 14px;
                font-weight: bold;
            }
            QPushButton:hover {
                background: rgba(255,255,255,0.12);
            }
        """)
        self.toggle_button.clicked.connect(self.toggle)

        self.title_label = QLabel(title, header)
        self.title_label.setAlignment(Qt.AlignVCenter | Qt.AlignLeft)
        self.title_label.setStyleSheet("""
            color: white;
            font-weight: 600;
            font-size: 13px;
        """)
        self.title_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)

        self.settings_button = QPushButton("", header)
        self.settings_button.setIcon(QIcon("./gui/Icon/Setting.svg"))
        self.settings_button.setFixedSize(22, 22)
        self.settings_button.setStyleSheet("""
            QPushButton {
                background: transparent;
                border: none;
                border-radius: 5px;
                color: white;
                font-size: 13px;
            }
            QPushButton:hover {
                background: rgba(255,255,255,0.12);
            }
        """)
        self.settings_button.setVisible(callable(self._settings_callback))
        self.settings_button.clicked.connect(self._on_settings_clicked)

        layout.addWidget(self.toggle_button)
        layout.addWidget(self.title_label)
        layout.addWidget(self.settings_button)

        return header

    def _on_settings_clicked(self):
        if callable(self._settings_callback):
            self._settings_callback()

    def _update_arrow(self):
        self.toggle_button.setText("▸" if self._collapsed else "▾")

    def toggle(self):
        self.set_collapsed(not self._collapsed)

    def _content_target_height(self):
        natural_h = self.content_layout.sizeHint().height() + 16

        # hauteur choisie par l'utilisateur via la poignée
        if self._user_content_height is not None:
            return max(
                self._min_content_height,
                min(int(self._user_content_height), self._max_content_height)
            )

        # hauteur préférée donnée lors de la création
        if self._preferred_content_height is not None:
            return max(
                self._min_content_height,
                min(int(self._preferred_content_height), natural_h, self._max_content_height)
            )

        return max(
            self._min_content_height,
            min(natural_h, self._max_content_height)
        )
    
    def _resize_by_handle(self, dy: int):
        if self._collapsed:
            return

        current = self.content_container.maximumHeight()
        if current <= 0 or current >= 16777215:
            current = self._content_target_height()

        new_h = current + dy
        new_h = max(self._min_content_height, min(new_h, self._max_content_height))

        self._user_content_height = new_h
        self.content_container.setMaximumHeight(new_h)
        self.content_container.setMinimumHeight(0)

        self.updateGeometry()
        self.adjustSize()
        self.toggled.emit(True)

    def set_collapsed(self, collapsed: bool):
        if self._collapsed == collapsed:
            return

        self._collapsed = collapsed
        self.animation.stop()

        content_h = self._content_target_height()

        if not collapsed:
            self.content_container.setVisible(True)
            self.resize_handle.setVisible(True)

        start_height = self.content_container.maximumHeight()
        if start_height < 0 or start_height == 16777215:
            start_height = content_h

        end_height = 0 if collapsed else content_h

        self._update_arrow()
        self.animation.setStartValue(start_height)
        self.animation.setEndValue(end_height)
        self.animation.start()

    def _on_animation_finished(self):
        content_h = self._content_target_height()

        if self._collapsed:
            self.content_container.setVisible(False)
            self.content_container.setMinimumHeight(0)
            self.content_container.setMaximumHeight(0)
            self.resize_handle.setVisible(False)
        else:
            self.content_container.setVisible(True)
            self.content_container.setMinimumHeight(0)
            self.content_container.setMaximumHeight(content_h)
            self.resize_handle.setVisible(True)

        self.updateGeometry()
        self.adjustSize()
        self.toggled.emit(not self._collapsed)

    def set_preferred_content_height(self, height: int):
        self._preferred_content_height = max(0, int(height))
        self._user_content_height = self._preferred_content_height

        if not self._collapsed:
            self.content_container.setMaximumHeight(self._content_target_height())
            self.updateGeometry()
            self.adjustSize()
    
    def content_widget(self):
        return self._content_widget