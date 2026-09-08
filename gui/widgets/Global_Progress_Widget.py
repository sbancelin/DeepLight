import time

from PySide6.QtWidgets import QWidget, QHBoxLayout, QLabel, QProgressBar, QSizePolicy
from PySide6.QtCore import QTimer, Slot

try:
    import psutil
except ImportError:
    psutil = None

# Même style que la barre de progression du widget Spectro
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

SOURCE_LABEL_STYLE = "color: white; font-weight: bold;"
INFO_LABEL_STYLE = "color: #ccc;"


class GlobalProgressWidget(QWidget):
    """
    Global progress bar shown at the bottom of the GUI.

    Shared by the Spectro and Scan acquisitions:
    - progress bar (spectro style) with done/total and %
    - elapsed / remaining time, refreshed every second
    - CPU usage (through psutil when available)
    """

    def __init__(self, parent=None):
        super().__init__(parent)

        self._source = None
        self._t0 = None
        self._done = 0
        self._total = 0
        # ETA fournie par la source (ex: Spectro). Si None, calculée
        # à partir de done/total et du temps écoulé.
        self._remaining_s = None
        self._remaining_set_t = None

        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 2, 6, 2)
        layout.setSpacing(10)

        self.source_label = QLabel("Idle")
        self.source_label.setStyleSheet(SOURCE_LABEL_STYLE)
        self.source_label.setMinimumWidth(55)
        layout.addWidget(self.source_label)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setFormat("Idle")
        self.progress_bar.setTextVisible(True)
        self.progress_bar.setStyleSheet(PROGRESS_BAR_STYLE)
        self.progress_bar.setMinimumWidth(180)
        self.progress_bar.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        layout.addWidget(self.progress_bar, 1)

        self.time_label = QLabel("—")
        self.time_label.setStyleSheet(INFO_LABEL_STYLE)
        self.time_label.setMinimumWidth(190)
        layout.addWidget(self.time_label)

        self.cpu_label = QLabel("CPU —")
        self.cpu_label.setStyleSheet(INFO_LABEL_STYLE)
        self.cpu_label.setMinimumWidth(70)
        layout.addWidget(self.cpu_label)

        if psutil is not None:
            # premier appel non bloquant pour initialiser la mesure
            psutil.cpu_percent(interval=None)
        else:
            self.cpu_label.hide()

        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._on_tick)
        self._timer.start()

    # ==========================================================
    # API publique
    # ==========================================================
    @Slot(str)
    def start_task(self, source: str, total: int = 0):
        self._source = str(source)
        self._t0 = time.monotonic()
        self._done = 0
        self._total = max(0, int(total))
        self._remaining_s = None
        self._remaining_set_t = None

        self.source_label.setText(self._source)
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self._update_progress_text()
        self._update_time_label()

    @Slot(int, int)
    def set_progress(self, done: int, total: int, source: str = None):
        # Pas de redémarrage implicite : les événements de progression
        # résiduels reçus après un stop sont ignorés (sinon le chrono repart).
        if self._source is None:
            return

        if source is not None:
            self.source_label.setText(str(source))
            self._source = str(source)

        self._total = max(0, int(total))
        self._done = max(0, min(int(done), self._total if self._total > 0 else int(done)))
        self._update_progress_text()

    @Slot(float, float)
    def set_eta(self, elapsed_s: float, remaining_s: float):
        if self._source is None:
            return
        self._remaining_s = max(0.0, float(remaining_s))
        self._remaining_set_t = time.monotonic()

    @Slot()
    def finish_task(self, message: str = "Done"):
        if self._source is None:
            return

        elapsed = time.monotonic() - self._t0 if self._t0 is not None else 0.0

        if self._total > 0 and self._done >= self._total:
            self.progress_bar.setValue(100)
            self.progress_bar.setFormat(f"{self._source}: {message} (100%)")
        else:
            self.progress_bar.setFormat(f"{self._source}: {message}")

        self.time_label.setText(f"elapsed {self._format_duration(elapsed)}")
        self.source_label.setText("Idle")

        self._source = None
        self._t0 = None
        self._remaining_s = None
        self._remaining_set_t = None

    def is_running(self) -> bool:
        return self._source is not None

    # ==========================================================
    # Interne
    # ==========================================================
    def _update_progress_text(self):
        if self._total > 0:
            percent = int(round(100.0 * self._done / self._total))
            percent = max(0, min(100, percent))
            self.progress_bar.setValue(percent)
            self.progress_bar.setFormat(f"{percent}%")
        else:
            self.progress_bar.setValue(0)
            self.progress_bar.setFormat("…")

    def _current_remaining_s(self):
        # ETA fournie par la source, corrigée du temps écoulé depuis sa réception
        if self._remaining_s is not None and self._remaining_set_t is not None:
            return max(0.0, self._remaining_s - (time.monotonic() - self._remaining_set_t))

        # sinon, estimation depuis la progression
        if self._t0 is not None and self._total > 0 and self._done > 0:
            elapsed = time.monotonic() - self._t0
            return elapsed * (self._total - self._done) / self._done

        return None

    def _update_time_label(self):
        if self._t0 is None:
            return

        elapsed = time.monotonic() - self._t0
        remaining = self._current_remaining_s()

        if remaining is not None:
            self.time_label.setText(
                f"elapsed {self._format_duration(elapsed)} / "
                f"remaining {self._format_duration(remaining)}"
            )
        else:
            self.time_label.setText(f"elapsed {self._format_duration(elapsed)}")

    @Slot()
    def _on_tick(self):
        if self._source is not None:
            self._update_time_label()

        if psutil is not None:
            try:
                cpu = psutil.cpu_percent(interval=None)
                self.cpu_label.setText(f"CPU {cpu:.0f}%")
            except Exception:
                pass

    @staticmethod
    def _format_duration(seconds: float) -> str:
        seconds = max(0, int(round(float(seconds))))
        h, rem = divmod(seconds, 3600)
        m, s = divmod(rem, 60)
        if h > 0:
            return f"{h:d}:{m:02d}:{s:02d}"
        return f"{m:02d}:{s:02d}"