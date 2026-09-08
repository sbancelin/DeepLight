import sys
from datetime import datetime
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout,
                                QPushButton, QTextEdit, QApplication)
from PySide6.QtCore import QObject, Signal, Qt
from PySide6.QtGui import QTextCursor

COLORS = {
    "debug":   "#888888",
    "info":    "#cccccc",
    "warning": "#f0a500",
    "error":   "#e05555",
}

LOG_WIDGET_STYLE = """
QTextEdit {
    background-color: #1a1a1a;
    color: #cccccc;
    border: none;
    font-family: Consolas, monospace;
    font-size: 11px;
}
QPushButton {
    background-color: #333;
    color: #aaa;
    border: 1px solid #555;
    border-radius: 4px;
    padding: 2px 8px;
    font-size: 11px;
}
QPushButton:hover {
    background-color: #444;
    color: #ccc;
}
"""


class _AppLogger(QObject):
    """Singleton emitting thread-safe log signals."""
    message_logged = Signal(str, str)   # (message_html, level)

    def _emit(self, level: str, msg: str):
        ts = datetime.now().strftime("%H:%M:%S")
        color = COLORS.get(level, COLORS["info"])
        label = level.upper().ljust(7)
        html = (
            f'<span style="color:#555">[{ts}]</span> '
            f'<span style="color:{color};font-weight:bold">{label}</span> '
            f'<span style="color:{color}">{msg}</span>'
        )
        self.message_logged.emit(html, level)
        if level != "debug":
            stream = sys.stderr if level in ("error", "warning") else sys.stdout
            print(f"[{ts}] {label} {msg}", file=stream, flush=True)

    def debug(self, msg: str):
        self._emit("debug", msg)

    def info(self, msg: str):
        self._emit("info", msg)

    def warning(self, msg: str):
        self._emit("warning", msg)

    def error(self, msg: str):
        self._emit("error", msg)


logger = _AppLogger()


class LogWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        self.text_edit = QTextEdit()
        self.text_edit.setReadOnly(True)
        self.text_edit.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)
        layout.addWidget(self.text_edit)

        btn_bar = QWidget()
        h = QHBoxLayout(btn_bar)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(4)

        self.btn_clear = QPushButton("Clear")
        self.btn_copy = QPushButton("Copy")
        h.addStretch()
        h.addWidget(self.btn_copy)
        h.addWidget(self.btn_clear)
        layout.addWidget(btn_bar)

        self.setStyleSheet(LOG_WIDGET_STYLE)

        self.btn_clear.clicked.connect(self.text_edit.clear)
        self.btn_copy.clicked.connect(self._copy_all)

        logger.message_logged.connect(self._append)

    def _append(self, html: str, level: str):
        if level == "debug":
            return
        cursor = self.text_edit.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        self.text_edit.setTextCursor(cursor)
        self.text_edit.insertHtml(html + "<br>")
        self.text_edit.ensureCursorVisible()

    def _copy_all(self):
        QApplication.clipboard().setText(self.text_edit.toPlainText())