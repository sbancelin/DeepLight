from __future__ import annotations

import time
import math
import json
import numpy as np

from PySide6.QtCore import QObject, Signal, Slot, QTimer

from .Sample_Scan_Manager import SampleScanManager


class SpectroManager(QObject):
    """
    Manager mock pour le mode spectro point-par-point.

    - réutilise les paramètres du ScanWidget en mode sample scan
    - génère une image Brillouin mock et/ou un spectre Raman mock à chaque position
    - met à jour le SpectroWidget
    - accumule un dataset brut pour sauvegarde post hoc
    """

    acquisition_started = Signal()
    acquisition_finished = Signal(dict)   # dataset brut
    acquisition_failed = Signal(str)
    status_changed = Signal(str)
    progress_changed = Signal(int, int)   # done, total

    brillouin_image_ready = Signal(object)
    raman_spectrum_ready = Signal(object, object)   # wavelengths, intensities
    brillouin_running_changed = Signal(bool)
    raman_running_changed = Signal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.sample_scan_manager = SampleScanManager()

        self._running = False
        self._dataset = None
        self._mock_rng = np.random.default_rng()
        self._wavelength_axis_nm = np.linspace(500.0, 900.0, 1024, dtype=np.float64)

        self._brillouin_live_running = False
        self._raman_live_running = False
        self._brillouin_live_ix = 0
        self._brillouin_live_iy = 0
        self._raman_live_ix = 0
        self._raman_live_iy = 0

        self._brillouin_live_timer = QTimer(self)
        self._brillouin_live_timer.setInterval(200)
        self._brillouin_live_timer.timeout.connect(self._on_brillouin_live_tick)

        self._raman_live_timer = QTimer(self)
        self._raman_live_timer.setInterval(250)
        self._raman_live_timer.timeout.connect(self._on_raman_live_tick)

    def is_running(self) -> bool:
        return bool(self._running)

    def is_brillouin_live_running(self) -> bool:
        return bool(self._brillouin_live_running)

    def is_raman_live_running(self) -> bool:
        return bool(self._raman_live_running)
    
    def _make_empty_dataset(self, scan_parameters: dict, modes: dict, brillouin_params: dict, raman_params: dict):
        return {
            "created": time.strftime("%Y-%m-%d %H:%M:%S"),
            "scan_parameters": json.loads(json.dumps(scan_parameters, default=str)),
            "modes": dict(modes),
            "brillouin_parameters": dict(brillouin_params),
            "raman_parameters": dict(raman_params),
            "positions": [],
            "brillouin_images": [],
            "raman_spectra": [],
            "raman_wavelengths_nm": self._wavelength_axis_nm.copy(),
        }

    def _make_mock_brillouin_image(self, ny: int, nx: int, ix: int, iy: int) -> np.ndarray:
        y = np.linspace(-1.0, 1.0, ny, dtype=np.float32)
        x = np.linspace(-1.0, 1.0, nx, dtype=np.float32)
        xx, yy = np.meshgrid(x, y)

        cx1 = 0.45 * math.sin(0.15 * ix)
        cy1 = 0.45 * math.cos(0.12 * iy)
        cx2 = 0.35 * math.cos(0.09 * (ix + iy))
        cy2 = 0.35 * math.sin(0.11 * (ix - iy))

        g1 = np.exp(-((xx - cx1) ** 2 + (yy - cy1) ** 2) / 0.05)
        g2 = 0.7 * np.exp(-((xx - cx2) ** 2 + (yy - cy2) ** 2) / 0.02)

        ring_r = np.sqrt(xx**2 + yy**2)
        ring = 0.2 * np.exp(-((ring_r - 0.45) ** 2) / 0.01)

        noise = 0.08 * self._mock_rng.normal(size=(ny, nx)).astype(np.float32)

        img = 30.0 + 150.0 * g1 + 90.0 * g2 + 35.0 * ring + 20.0 * noise
        img = np.clip(img, 0.0, 255.0).astype(np.float32, copy=False)
        return img

    def _gaussian(self, x: np.ndarray, mu: float, sigma: float, amp: float) -> np.ndarray:
        return amp * np.exp(-0.5 * ((x - mu) / sigma) ** 2)

    def _make_mock_raman_spectrum(self, ix: int, iy: int) -> np.ndarray:
        x = self._wavelength_axis_nm

        p1 = self._gaussian(x, 620.0 + 8.0 * math.sin(0.08 * ix), 8.0, 120.0 + 15.0 * math.cos(0.05 * iy))
        p2 = self._gaussian(x, 705.0 + 12.0 * math.cos(0.07 * iy), 14.0, 180.0 + 20.0 * math.sin(0.04 * ix))
        p3 = self._gaussian(x, 790.0 + 10.0 * math.sin(0.05 * (ix + iy)), 10.0, 100.0)

        baseline = 15.0 + 8.0 * np.sin(0.01 * x + 0.05 * ix)
        noise = self._mock_rng.normal(0.0, 4.0, size=x.shape)

        y = baseline + p1 + p2 + p3 + noise
        y = np.clip(y, 0.0, None).astype(np.float64, copy=False)
        return y

    @Slot(dict)
    def snap_brillouin(self, params: dict | None = None):
        try:
            params = dict(params or {})
            img = self._make_mock_brillouin_image(
                ny=512,
                nx=512,
                ix=int(self._brillouin_live_ix),
                iy=int(self._brillouin_live_iy),
            )
            self.brillouin_image_ready.emit(img)
            self.status_changed.emit("Brillouin snap")
            self._brillouin_live_ix += 1
            self._brillouin_live_iy += 1
        except Exception as e:
            self.acquisition_failed.emit(f"Brillouin snap failed: {e}")

    @Slot()
    def start_live_brillouin(self):
        if self._brillouin_live_running:
            return
        self._brillouin_live_running = True
        self._brillouin_live_timer.start()
        self.brillouin_running_changed.emit(True)
        self.status_changed.emit("Brillouin live started")

    @Slot()
    def stop_live_brillouin(self):
        if not self._brillouin_live_running:
            return
        self._brillouin_live_running = False
        self._brillouin_live_timer.stop()
        self.brillouin_running_changed.emit(False)
        self.status_changed.emit("Brillouin live stopped")

    def _on_brillouin_live_tick(self):
        img = self._make_mock_brillouin_image(
            ny=512,
            nx=512,
            ix=int(self._brillouin_live_ix),
            iy=int(self._brillouin_live_iy),
        )
        self.brillouin_image_ready.emit(img)
        self._brillouin_live_ix += 1
        self._brillouin_live_iy += 1

    @Slot(dict)
    def snap_raman(self, params: dict | None = None):
        try:
            params = dict(params or {})
            spec = self._make_mock_raman_spectrum(
                ix=int(self._raman_live_ix),
                iy=int(self._raman_live_iy),
            )
            self.raman_spectrum_ready.emit(self._wavelength_axis_nm.copy(), spec)
            self.status_changed.emit("Raman snap")
            self._raman_live_ix += 1
            self._raman_live_iy += 1
        except Exception as e:
            self.acquisition_failed.emit(f"Raman snap failed: {e}")

    @Slot()
    def start_live_raman(self):
        if self._raman_live_running:
            return
        self._raman_live_running = True
        self._raman_live_timer.start()
        self.raman_running_changed.emit(True)
        self.status_changed.emit("Raman live started")

    @Slot()
    def stop_live_raman(self):
        if not self._raman_live_running:
            return
        self._raman_live_running = False
        self._raman_live_timer.stop()
        self.raman_running_changed.emit(False)
        self.status_changed.emit("Raman live stopped")

    def _on_raman_live_tick(self):
        spec = self._make_mock_raman_spectrum(
            ix=int(self._raman_live_ix),
            iy=int(self._raman_live_iy),
        )
        self.raman_spectrum_ready.emit(self._wavelength_axis_nm.copy(), spec)
        self._raman_live_ix += 1
        self._raman_live_iy += 1

    @Slot()
    def stop_all_live(self):
        self.stop_live_brillouin()
        self.stop_live_raman()
    
    @Slot(dict, dict, dict, dict)
    def start_mapping(self, scan_parameters: dict, modes: dict, brillouin_params: dict, raman_params: dict):
        if self._running:
            return
        
        self.stop_all_live()

        try:
            scan_kind = str(scan_parameters.get("scan_kind", "laser") or "laser")
            if scan_kind != "sample":
                raise ValueError("Spectro acquisition requires sample scan mode.")

            if not bool(modes.get("brillouin", False)) and not bool(modes.get("raman", False)):
                raise ValueError("At least one spectro mode must be enabled.")

            self.sample_scan_manager.configure(scan_parameters)
            ny, nx = self.sample_scan_manager.image_shape()

            self._dataset = self._make_empty_dataset(
                scan_parameters=scan_parameters,
                modes=modes,
                brillouin_params=brillouin_params,
                raman_params=raman_params,
            )

            frame_plans = list(self.sample_scan_manager.iter_frame_plans())
            if not frame_plans:
                frame_plans = [None]

            total_positions = self.sample_scan_manager.frame_count() * (nx * ny)
            done = 0

            self._running = True
            self.acquisition_started.emit()
            self.status_changed.emit("Spectro mapping started")

            for frame_plan in frame_plans:
                if not self._running:
                    break

                axis3_value = None if frame_plan is None else frame_plan.axis3_value
                axis4_value = None if frame_plan is None else frame_plan.axis4_value
                axis3_index = 0 if frame_plan is None else int(frame_plan.axis3_index)
                axis4_index = 0 if frame_plan is None else int(frame_plan.axis4_index)

                for event in self.sample_scan_manager.iter_pixel_events():
                    if not self._running:
                        break

                    entry = {
                        "ix": int(event.ix),
                        "iy": int(event.iy),
                        "x_um": float(event.x_target_rel_um),
                        "y_um": float(event.y_target_rel_um),
                        "axis3_index": axis3_index,
                        "axis4_index": axis4_index,
                        "axis3_value": axis3_value,
                        "axis4_value": axis4_value,
                    }
                    self._dataset["positions"].append(entry)

                    if bool(modes.get("brillouin", False)):
                        img = self._make_mock_brillouin_image(
                            ny=512,
                            nx=512,
                            ix=int(event.ix),
                            iy=int(event.iy),
                        )
                        self._dataset["brillouin_images"].append(img.copy())
                        self.brillouin_image_ready.emit(img)

                    if bool(modes.get("raman", False)):
                        spec = self._make_mock_raman_spectrum(
                            ix=int(event.ix),
                            iy=int(event.iy),
                        )
                        self._dataset["raman_spectra"].append(spec.copy())
                        self.raman_spectrum_ready.emit(self._wavelength_axis_nm.copy(), spec)

                    done += 1
                    self.progress_changed.emit(done, total_positions)
                    self.status_changed.emit(f"Spectro {done}/{total_positions}")

                    dwell_s = max(
                        float(scan_parameters.get("sample_settle_time_s", 0.0) or 0.0)
                        + float(scan_parameters.get("dwell_time", 0.0) or 0.0),
                        0.001,
                    )
                    QTimer.singleShot(0, lambda: None)
                    time.sleep(min(dwell_s, 0.02))

            was_running = self._running
            dataset = self._finalize_dataset()
            if was_running:
                self.status_changed.emit("Spectro mapping finished")
            else:
                self.status_changed.emit("Spectro mapping stopped")
            self.acquisition_finished.emit(dataset)

        except Exception as e:
            self._running = False
            self.status_changed.emit(f"Spectro error: {e}")
            self.acquisition_failed.emit(str(e))

    @Slot()
    def stop_mapping(self):
        if not self._running:
            return
        self._running = False
        self.stop_all_live()
        self.status_changed.emit("Spectro stopping...")

    def _finalize_dataset(self) -> dict:
        self._running = False
        ds = self._dataset if self._dataset is not None else {}

        if isinstance(ds.get("brillouin_images"), list):
            if ds["brillouin_images"]:
                ds["brillouin_images"] = np.stack(ds["brillouin_images"], axis=0).astype(np.float32, copy=False)
            else:
                ds["brillouin_images"] = np.zeros((0, 512, 512), dtype=np.float32)

        if isinstance(ds.get("raman_spectra"), list):
            if ds["raman_spectra"]:
                ds["raman_spectra"] = np.stack(ds["raman_spectra"], axis=0).astype(np.float32, copy=False)
            else:
                ds["raman_spectra"] = np.zeros((0, self._wavelength_axis_nm.size), dtype=np.float32)

        return ds