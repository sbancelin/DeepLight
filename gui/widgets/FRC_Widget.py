from __future__ import annotations

import numpy as np
import pyqtgraph as pg

from PySide6.QtCore import Signal, Slot, Qt
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QCheckBox, QMessageBox, QComboBox, QLineEdit, QSizePolicy)

class FRCWidget(QWidget):
    """
    Dock de calcul FRC sur une image unique,
    découpée en deux sous-images complémentaires.
    """

    frc_resolution_computed = Signal(float)   # résolution estimée en µm
    request_single_frame = Signal(str)        # demande à MainWindow de lancer un single sur ce canal

    def __init__(self, parent=None):
        super().__init__(parent)

        self.main_layout = QVBoxLayout(self)
        self.main_layout.setContentsMargins(2, 2, 2, 2)
        self.main_layout.setSpacing(4)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        
        self._armed = False
        self._img = None
        self._pixel_size_um = 1.0
        self._selected_channel = None
        self._current_image_getter = None

        self.checkbox_style = """
            QCheckBox::indicator {
                width: 12px;
                height: 12px;
                background-color: #333;
                border: 1px solid #555;
                border-radius: 3px;
            }
            QCheckBox::indicator:checked {
                background-color: #2E8B57;
                border: 1px solid #555;
                border-radius: 3px;
            }
            QCheckBox::indicator:checked:hover {
                border: 1px solid #777;
                background-color: #3AB16F;
            }
            QCheckBox::indicator:unchecked:hover {
                background-color: #444;
                border: 1px solid #777;
            }
        """

        self._build_ui()
        self.set_active_channels([])
        self._on_threshold_mode_changed(self.threshold_combo.currentText())

    def _build_ui(self):
        container = QWidget()
        container.setMinimumWidth(0)
        container.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)

        layout = QVBoxLayout(container)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(4)

        # ---------- Ligne 1 ----------
        row1 = QHBoxLayout()
        row1.setContentsMargins(0, 0, 0, 0)
        row1.setSpacing(6)

        row1.addWidget(QLabel("Channel"))

        self.channel_combo = QComboBox()
        self.channel_combo.setMinimumWidth(20)
        self.channel_combo.setStyleSheet("""
                QComboBox {
                    background-color: #333;
                    color: white;
                    border: 1px solid #555;
                    border-radius: 3px;
                    padding: 2px;
                    min-height: 20px;
                }
            """)
        self.channel_combo.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        row1.addWidget(self.channel_combo, stretch=1)

        self.get_frame_button = QPushButton("Get frame")
        self.get_frame_button.setMinimumWidth(20)
        self.get_frame_button.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.get_frame_button.setStyleSheet("""
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
        )
        self.get_frame_button.clicked.connect(self.arm_capture)
        row1.addWidget(self.get_frame_button)

        self.use_hanning_checkbox = QCheckBox("Hanning window")
        self.use_hanning_checkbox.setChecked(True)
        self.use_hanning_checkbox.setStyleSheet(self.checkbox_style)
        self.use_hanning_checkbox.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        row1.addWidget(self.use_hanning_checkbox)

        row1.addStretch()
        layout.addLayout(row1)

        # ---------- Ligne 2 ----------
        row2 = QHBoxLayout()
        row2.setContentsMargins(0, 0, 0, 0)
        row2.setSpacing(6)

        row2.addWidget(QLabel("Threshold"))

        self.threshold_combo = QComboBox()
        self.threshold_combo.addItems(["1/7","0.5", "0.3", "Custom"])
        self.threshold_combo.setMinimumWidth(70)
        self.threshold_combo.setStyleSheet("""
                QComboBox {
                    background-color: #333;
                    color: white;
                    border: 1px solid #555;
                    border-radius: 3px;
                    padding: 2px;
                    min-height: 20px;
                }
            """)
        self.threshold_combo.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.threshold_combo.currentTextChanged.connect(self._on_threshold_mode_changed)
        row2.addWidget(self.threshold_combo)

        self.threshold_edit = QLineEdit("0.143")
        self.threshold_edit.setEnabled(False)
        self.threshold_edit.setMinimumWidth(55)
        self.threshold_edit.setMaximumWidth(80)
        self.threshold_edit.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.threshold_edit.returnPressed.connect(self._recompute_if_possible)
        row2.addWidget(self.threshold_edit)

        row2.addSpacing(12)

        self.result_label = QLabel("Resolution: -")
        self.result_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        row2.addWidget(self.result_label, stretch=1)

        row2.addStretch()
        layout.addLayout(row2)

        # ---------- Plot ----------
        self.plot_widget = pg.PlotWidget()
        self.plot_widget.setLabel("left", "FRC")
        self.plot_widget.setLabel("bottom", "Spatial frequency", units="cycles/pixel")
        self.plot_widget.showGrid(x=True, y=True, alpha=0.3)
        self.plot_widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.plot_widget.setMinimumHeight(80)

        self.frc_curve = self.plot_widget.plot(
            [], [],
            pen=pg.mkPen("#FF7700", width=2),
            name="FRC"
        )

        self.threshold_curve = self.plot_widget.plot(
            [], [],
            pen=pg.mkPen("#2E8B57", width=2, style=Qt.DashLine),
            name="Threshold"
        )

        layout.addWidget(self.plot_widget)
        self.main_layout.addWidget(container)
        self.main_layout.addStretch()

    def set_current_image_getter(self, getter):
        """
        getter(channel:str) -> np.ndarray | None
        """
        self._current_image_getter = getter
    
    def set_active_channels(self, channels: list[str]):
        current = self.channel_combo.currentText()

        self.channel_combo.blockSignals(True)
        self.channel_combo.clear()

        valid_channels = [str(ch) for ch in channels if str(ch).strip()]
        if valid_channels:
            self.channel_combo.addItems(valid_channels)
        else:
            self.channel_combo.addItem("No channel")

        idx = self.channel_combo.findText(current)
        if idx >= 0:
            self.channel_combo.setCurrentIndex(idx)
        else:
            self.channel_combo.setCurrentIndex(0)

        self.channel_combo.blockSignals(False)

    def _on_threshold_mode_changed(self, mode: str):
        self.threshold_edit.setEnabled(mode == "Custom")
        self._replot_threshold_if_possible()
        self._recompute_if_possible()

    def _read_custom_threshold(self) -> float:
        try:
            value = float((self.threshold_edit.text() or "0").replace(",", "."))
        except Exception:
            raise ValueError("Custom threshold must be a valid number.")

        if value <= 0 or value >= 1:
            raise ValueError("Custom threshold must be between 0 and 1.")
        return value

    def _get_threshold_value(self) -> float:
        mode = self.threshold_combo.currentText()

        if mode == "0.5":
            return 0.5
        if mode == "0.3":
            return 0.3
        if mode == "1/7":
            return 1.0 / 7.0
        if mode == "Custom":
            return self._read_custom_threshold()

        return 1.0 / 7.0

    def _replot_threshold_if_possible(self):
        x, _ = self.threshold_curve.getData()
        if x is None or len(x) == 0:
            return

        try:
            thr = self._get_threshold_value()
        except Exception:
            return

        self.threshold_curve.setData(x, np.full_like(x, thr, dtype=np.float64))

    def _recompute_if_possible(self):
        if self._img is None:
            return
        try:
            self.compute_and_plot_frc()
        except Exception:
            pass

    def arm_capture(self):
        channel = self.channel_combo.currentText().strip()
        if not channel or channel == "No channel":
            QMessageBox.warning(self, "FRC", "No active channel available.")
            return

        self._selected_channel = channel
        self._img = None

        self._clear_plot_and_result()

        # 1) essayer d'utiliser l'image déjà affichée
        if callable(self._current_image_getter):
            try:
                current = self._current_image_getter(channel)
            except Exception:
                current = None

            if current is not None:
                arr = np.asarray(current, dtype=np.float64)
                if arr.ndim == 2 and arr.size > 0:
                    self._img = arr.copy()
                    self._armed = False
                    self.compute_and_plot_frc()
                    return

        # 2) sinon on arme et on demande un single
        self._armed = True
        self.request_single_frame.emit(channel)

    def reset_capture(self):
        self._armed = False
        self._selected_channel = None
        self._img = None
        self._clear_plot_and_result()

    def _clear_plot_and_result(self):
        self.result_label.setText("Resolution: -")
        self.frc_curve.setData([], [])
        self.threshold_curve.setData([], [])
    
    def set_pixel_size_um(self, pixel_size_um: float):
        if pixel_size_um > 0:
            self._pixel_size_um = float(pixel_size_um)

    @Slot(str, object)
    def on_new_image(self, channel: str, image: np.ndarray):
        """
        Reçoit les images du flux final affiché.
        Si le dock est armé, capture l'image du canal sélectionné
        et calcule la FRC.
        """
        if image is None:
            return

        if not self._armed:
            return

        if channel != self._selected_channel:
            return

        arr = np.asarray(image, dtype=np.float64)
        if arr.ndim != 2:
            return

        self._img = arr.copy()
        self._armed = False
        self.compute_and_plot_frc()

    def _split_single_image_for_frc(self, image: np.ndarray):
        """
        Découpe une image unique en 2 sous-images complémentaires
        par split damier équilibré.
        """
        arr = np.asarray(image, dtype=np.float64)
        if arr.ndim != 2:
            raise ValueError("FRC expects a 2D image.")

        ny, nx = arr.shape
        ny2 = ny - (ny % 2)
        nx2 = nx - (nx % 2)

        if ny2 < 2 or nx2 < 2:
            raise ValueError("Image too small for single-image FRC.")

        arr = arr[:ny2, :nx2]

        a = arr[0::2, 0::2]
        b = arr[0::2, 1::2]
        c = arr[1::2, 0::2]
        d = arr[1::2, 1::2]

        img1 = 0.5 * (a + d)
        img2 = 0.5 * (b + c)

        if img1.shape != img2.shape:
            raise ValueError(f"Split shape mismatch: {img1.shape} vs {img2.shape}")

        return img1, img2

    def compute_and_plot_frc(self):
        if self._img is None:
            return

        img_a, img_b = self._split_single_image_for_frc(self._img)

        freq, frc = self._compute_frc(
            img_a,
            img_b,
            apply_hanning=self.use_hanning_checkbox.isChecked()
        )

        threshold_value = self._get_threshold_value()
        threshold = np.full_like(frc, threshold_value, dtype=np.float64)

        self.frc_curve.setData(freq, frc)
        self.threshold_curve.setData(freq, threshold)

        cutoff_freq = self._find_cutoff_frequency(freq, frc, threshold)

        if cutoff_freq is None or cutoff_freq <= 0:
            self.result_label.setText("Resolution: not found")
            return

        # Sous-échantillonnage par 2 -> pixel effectif doublé
        effective_pixel_size_um = 2.0 * self._pixel_size_um
        resolution_um = effective_pixel_size_um / cutoff_freq

        self.result_label.setText(f"Resolution: {resolution_um:.3f} µm")

        self.frc_resolution_computed.emit(resolution_um)

    def _compute_frc(self, img1, img2, apply_hanning=True):
        a = np.asarray(img1, dtype=np.float64)
        b = np.asarray(img2, dtype=np.float64)

        if a.shape != b.shape:
            raise ValueError("FRC images must have the same shape.")

        a = a - np.mean(a)
        b = b - np.mean(b)

        if apply_hanning:
            wy = np.hanning(a.shape[0])
            wx = np.hanning(a.shape[1])
            w = np.outer(wy, wx)
            a = a * w
            b = b * w

        fa = np.fft.fftshift(np.fft.fft2(a))
        fb = np.fft.fftshift(np.fft.fft2(b))

        ny, nx = a.shape
        cy = ny // 2
        cx = nx // 2

        yy, xx = np.indices((ny, nx))
        rr = np.sqrt((yy - cy) ** 2 + (xx - cx) ** 2)
        rr_int = rr.astype(np.int32)

        max_r = min(cx, cy)

        frc_vals = []
        freq_vals = []

        for r in range(1, max_r):
            mask = rr_int == r
            if not np.any(mask):
                continue

            fa_r = fa[mask]
            fb_r = fb[mask]

            num = np.sum(fa_r * np.conj(fb_r))
            den = np.sqrt(np.sum(np.abs(fa_r) ** 2) * np.sum(np.abs(fb_r) ** 2))

            if den == 0:
                frc_val = 0.0
            else:
                frc_val = np.abs(num) / den

            frc_vals.append(float(frc_val))
            freq_vals.append(r / max(nx, ny))

        freq = np.asarray(freq_vals, dtype=np.float64)
        frc = np.asarray(frc_vals, dtype=np.float64)

        return freq, frc

    def _find_cutoff_frequency(self, freq, frc, threshold):
        if len(freq) < 2:
            return None

        diff = frc - threshold
        idx = np.where(diff < 0)[0]

        if len(idx) == 0:
            return None

        i = idx[0]
        if i == 0:
            return freq[0]

        x1, x2 = freq[i - 1], freq[i]
        y1, y2 = diff[i - 1], diff[i]

        if y2 == y1:
            return x2

        x_cross = x1 - y1 * (x2 - x1) / (y2 - y1)
        return float(x_cross)