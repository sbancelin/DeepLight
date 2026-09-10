import os
import sys
import threading
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
    """Singleton emitting thread-safe log signals.

    Also writes to files, when any are open: a session file covering one run of
    the application, and a run file living beside the data of one acquisition.
    The panel and the console drop debug messages, the files keep them -- a log
    read after the fact is exactly where the detail is wanted.
    """

    message_logged = Signal(str, str)   # (message_html, level)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._file_lock = threading.Lock()
        self._session_file = None
        self._run_file = None

    # ---- file sinks ---------------------------------------------------

    def _open(self, path: str):
        """Open a log file, or return None and say so rather than raise.

        A log that cannot be written is worth a warning; it is never worth
        interrupting an acquisition that is otherwise fine.
        """
        try:
            folder = os.path.dirname(os.path.abspath(path))
            if folder:
                os.makedirs(folder, exist_ok=True)
            handle = open(path, "a", encoding="utf-8")
        except OSError as e:
            self.warning(f"[Log] could not open {path}: {e}")
            return None

        handle.write(
            f"\n=== DeepLight log opened {datetime.now().isoformat(timespec='seconds')} ===\n"
        )
        handle.flush()
        return handle

    def open_session_file(self, path: str) -> str | None:
        """Start the log covering this run of the application."""
        self.close_session_file()
        # Opened outside the lock: _open reports a failure through warning(),
        # which takes that same lock to reach the files.
        handle = self._open(path)
        with self._file_lock:
            self._session_file = handle
        if handle is None:
            return None
        self.info(f"[Log] session log: {path}")
        return path

    def close_session_file(self):
        with self._file_lock:
            handle, self._session_file = self._session_file, None
        self._close(handle)

    def open_run_file(self, path: str) -> str | None:
        """Start the log of one acquisition, beside the data it describes."""
        self.close_run_file()
        handle = self._open(path)
        with self._file_lock:
            self._run_file = handle
        return path if handle is not None else None

    def close_run_file(self):
        with self._file_lock:
            handle, self._run_file = self._run_file, None
        self._close(handle)

    def close_files(self):
        self.close_run_file()
        self.close_session_file()

    @staticmethod
    def _close(handle):
        if handle is None:
            return
        try:
            handle.write(
                f"=== closed {datetime.now().isoformat(timespec='seconds')} ===\n"
            )
            handle.close()
        except OSError:
            pass

    # ---- emission -----------------------------------------------------

    def _emit(self, level: str, msg: str):
        now = datetime.now()
        ts = now.strftime("%H:%M:%S")
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

        # Full date and milliseconds in the files: a line read weeks later has
        # to say which day it belongs to, and acquisition timing is in ms.
        line = f"{now.strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]} {label} {msg}\n"
        with self._file_lock:
            for handle in (self._session_file, self._run_file):
                if handle is None:
                    continue
                try:
                    handle.write(line)
                    handle.flush()      # a crash is exactly when the tail matters
                except (OSError, ValueError):
                    pass

    def debug(self, msg: str):
        self._emit("debug", msg)

    def info(self, msg: str):
        self._emit("info", msg)

    def warning(self, msg: str):
        self._emit("warning", msg)

    def error(self, msg: str):
        self._emit("error", msg)


logger = _AppLogger()


def open_session_log() -> str | None:
    """Start this run's session log under the configured data root.

    One file per launch, named by the moment it started, so a day of work
    leaves a readable trail without anything to rotate or clean up. Called by
    the window and by a scripted session alike, so both leave the same trace.
    """
    from ...config import log_folder

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return logger.open_session_file(str(log_folder() / f"deeplight_{stamp}.log"))


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