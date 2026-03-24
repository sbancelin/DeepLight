from __future__ import annotations

import ctypes
import os
import time
import cv2

import numpy as np
from PySide6.QtCore import QObject, Signal, Slot, QTimer

from .Camera_Manager import CameraBackendBase, CameraParameters


# =============================================================================
# MOCK CAMERA BACKEND
# =============================================================================

class MockCameraBackend(CameraBackendBase):
    def __init__(self):
        super().__init__()
        self._t0 = time.perf_counter()
        self._width = 512
        self._height = 512
        self._phase = 0.0

    def connect(self) -> None:
        self.connected = True
        print("[MockCamera] connected")

    def disconnect(self) -> None:
        self.stop_live()
        self.connected = False
        print("[MockCamera] disconnected")

    def list_binning(self):
        return ["1x1", "2x2", "4x4"]

    def list_pixel_formats(self):
        return ["Mono8", "Mono12", "Mono16", "RGB24"]

    def set_parameters(self, params: CameraParameters) -> None:
        super().set_parameters(params)

        binning = str(params.binning)
        if binning == "1x1":
            self._width, self._height = 512, 512
        elif binning == "2x2":
            self._width, self._height = 256, 256
        elif binning == "4x4":
            self._width, self._height = 128, 128
        else:
            self._width, self._height = 512, 512

    def _mono_dtype_and_max(self):
        pf = str(self.params.pixel_format)
        if pf == "Mono16":
            return np.uint16, 65535.0
        if pf == "Mono12":
            return np.uint16, 4095.0
        return np.uint8, 255.0

    def _generate_base_pattern(self):
        h, w = self._height, self._width
        yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)

        t = time.perf_counter() - self._t0
        self._phase += 0.12

        cx = w * (0.5 + 0.18 * np.cos(0.7 * t))
        cy = h * (0.5 + 0.16 * np.sin(0.9 * t))
        sigma = max(8.0, 20.0 - 0.5 * float(self.params.gain))

        spot = np.exp(-(((xx - cx) ** 2 + (yy - cy) ** 2) / (2.0 * sigma ** 2)))
        fringes = 0.35 * (1.0 + np.sin(0.05 * xx + 0.07 * yy + self._phase))
        grad = 0.15 + 0.35 * (xx / max(1.0, float(w - 1)))
        noise = 0.04 * np.random.randn(h, w).astype(np.float32)

        exposure_scale = min(4.0, max(0.05, float(self.params.exposure_ms) / 10.0))
        gain_scale = 1.0 + 0.1 * float(self.params.gain)

        img = (0.15 + grad + fringes + 1.8 * spot + noise) * exposure_scale * gain_scale
        return np.clip(img, 0.0, 1.0)

    def snap(self) -> np.ndarray:
        if not self.connected:
            self.connect()

        pf = str(self.params.pixel_format)
        base = self._generate_base_pattern()

        if pf == "RGB24":
            r = base
            g = np.roll(base, shift=8, axis=1)
            b = np.roll(base, shift=12, axis=0)
            rgb = np.stack([r, g, b], axis=-1)
            return (rgb * 255.0).astype(np.uint8, copy=False)

        dtype, vmax = self._mono_dtype_and_max()
        return (base * vmax).astype(dtype, copy=False)

    def get_frame(self) -> np.ndarray:
        return self.snap()

    def start_live(self) -> None:
        if not self.connected:
            self.connect()
        self.live_running = True
        print("[MockCamera] live started")

    def stop_live(self) -> None:
        self.live_running = False
        print("[MockCamera] live stopped")


# =============================================================================
# REAL MOTIC CAMERA BACKEND
# =============================================================================

class OpenCVCameraBackend(CameraBackendBase):
    def __init__(self, camera_index: int = 0):
        super().__init__()
        self.camera_index = int(camera_index)
        self.cap = None
        self._last_frame = None

    def connect(self) -> None:
        if self.connected:
            return

        print(f"[OpenCVCamera] opening camera index={self.camera_index}")
        cap = cv2.VideoCapture(self.camera_index, cv2.CAP_DSHOW)

        if not cap or not cap.isOpened():
            raise RuntimeError(f"Cannot open camera index={self.camera_index}")

        self.cap = cap
        self.connected = True
        print("[OpenCVCamera] connected")

        self.set_parameters(self.params)

    def disconnect(self) -> None:
        self.stop_live()
        if self.cap is not None:
            self.cap.release()
            self.cap = None
        self.connected = False
        print("[OpenCVCamera] disconnected")

    def list_binning(self):
        return ["1x1"]

    def list_pixel_formats(self):
        return ["Mono8", "RGB24"]

    def set_parameters(self, params: CameraParameters) -> None:
        super().set_parameters(params)

        if self.cap is None:
            return

        # FPS
        try:
            self.cap.set(cv2.CAP_PROP_FPS, float(params.fps))
        except Exception:
            pass

        # Exposure
        if not bool(params.auto_exposure):
            try:
                self.cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, 0.25)
            except Exception:
                pass
            try:
                self.cap.set(cv2.CAP_PROP_EXPOSURE, float(params.exposure_ms))
            except Exception:
                pass
        else:
            try:
                self.cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, 0.75)
            except Exception:
                pass

        # Gain
        if not bool(params.auto_gain):
            try:
                self.cap.set(cv2.CAP_PROP_GAIN, float(params.gain))
            except Exception:
                pass

    def _read_frame(self) -> np.ndarray:
        if not self.connected:
            self.connect()

        if self.cap is None:
            raise RuntimeError("Camera is not opened")

        ok, frame = self.cap.read()
        if not ok or frame is None:
            raise RuntimeError("Failed to read frame from OpenCV camera")

        self._last_frame = frame
        return frame

    def _convert_to_requested_format(self, frame_bgr: np.ndarray) -> np.ndarray:
        pf = str(self.params.pixel_format)

        if pf == "RGB24":
            return cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)

        gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
        return gray

    def snap(self) -> np.ndarray:
        frame = self._read_frame()
        return self._convert_to_requested_format(frame)

    def start_live(self) -> None:
        if not self.connected:
            self.connect()
        self.live_running = True
        print("[OpenCVCamera] live started")

    def stop_live(self) -> None:
        self.live_running = False
        print("[OpenCVCamera] live stopped")

    def get_frame(self) -> np.ndarray:
        return self.snap()

# =============================================================================
# QT CONTROLLER
# =============================================================================

class CameraController(QObject):
    frame_ready = Signal(object)
    status_changed = Signal(str)
    running_changed = Signal(bool)

    def __init__(self, backend: CameraBackendBase, parent=None):
        super().__init__(parent)
        self.backend = backend

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._on_live_timer)

    def _params_from_widget_dict(self, d: dict) -> CameraParameters:
        return CameraParameters(
            exposure_ms=float(d.get("exposure_ms", 10.0)),
            fps=max(0.1, float(d.get("fps", 10.0))),
            gain=float(d.get("gain", 0.0)),
            binning=str(d.get("binning", "1x1")),
            pixel_format=str(d.get("pixel_format", "Mono8")),
            auto_exposure=bool(d.get("auto_exposure", False)),
            auto_gain=bool(d.get("auto_gain", False)),
        )

    def connect_camera(self):
        self.backend.connect()
        self.status_changed.emit("Connected")

    def disconnect_camera(self):
        self.stop_live()
        self.backend.disconnect()
        self.status_changed.emit("Disconnected")

    def list_binning(self):
        return list(self.backend.list_binning())

    def list_pixel_formats(self):
        return list(self.backend.list_pixel_formats())

    @Slot(dict)
    def apply_parameters(self, widget_params: dict):
        params = self._params_from_widget_dict(widget_params)
        self.backend.set_parameters(params)

    @Slot(dict)
    def snap(self, widget_params: dict):
        try:
            self.connect_camera()
            self.apply_parameters(widget_params)
            img = self.backend.snap()
            self.frame_ready.emit(img)
            self.status_changed.emit("Snap done")
        except Exception as e:
            self.status_changed.emit(f"Camera error: {e}")
            raise

    @Slot(dict)
    def start_live(self, widget_params: dict):
        try:
            self.connect_camera()
            self.apply_parameters(widget_params)

            fps = max(0.1, float(self.backend.get_parameters().fps))
            interval_ms = max(1, int(round(1000.0 / fps)))

            self.backend.start_live()
            self._timer.start(interval_ms)

            self.running_changed.emit(True)
            self.status_changed.emit("Live running")
        except Exception as e:
            self.running_changed.emit(False)
            self.status_changed.emit(f"Camera error: {e}")
            raise

    @Slot()
    def stop_live(self):
        self._timer.stop()
        try:
            self.backend.stop_live()
        finally:
            self.running_changed.emit(False)
            self.status_changed.emit("Idle")

    @Slot()
    def _on_live_timer(self):
        try:
            img = self.backend.get_frame()
            self.frame_ready.emit(img)
        except Exception as e:
            self.stop_live()
            self.status_changed.emit(f"Camera error: {e}")