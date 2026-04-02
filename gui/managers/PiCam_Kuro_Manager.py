from __future__ import annotations

import os
import ctypes
from pathlib import Path
from ctypes import POINTER, byref
import numpy as np

# ----------------------------------------------------------------------
# PICam basic C types (from pil_platform.h / picam.h)
# ----------------------------------------------------------------------

piint = ctypes.c_int
piflt = ctypes.c_double
pi64s = ctypes.c_longlong
PicamHandle = ctypes.c_void_p


class PicamRoi(ctypes.Structure):
    _fields_ = [
        ("x", piint),
        ("width", piint),
        ("x_binning", piint),
        ("y", piint),
        ("height", piint),
        ("y_binning", piint),
    ]


class PicamRois(ctypes.Structure):
    _fields_ = [
        ("roi_array", POINTER(PicamRoi)),
        ("roi_count", piint),
    ]


class PicamAvailableData(ctypes.Structure):
    _fields_ = [
        ("initial_readout", ctypes.c_void_p),
        ("readout_count", pi64s),
    ]

class PicamCameraID(ctypes.Structure):
    _fields_ = [
        ("model", piint),
        ("computer_interface", piint),
        ("sensor_name", ctypes.c_char * 64),
        ("serial_number", ctypes.c_char * 64),
    ]

# ----------------------------------------------------------------------
# PICam parameter IDs
# ----------------------------------------------------------------------

PicamParameter_ExposureTime = 33685527
PicamParameter_Rois = 67436581
PicamParameter_PixelFormat = 50593833
PicamParameter_FrameSize = 16842794
PicamParameter_ReadoutStride = 16842808
PicamParameter_SensorActiveWidth = 16842811
PicamParameter_SensorActiveHeight = 16842812

PicamPixelFormat_Monochrome16Bit = 1
PicamPixelFormat_Monochrome32Bit = 2


class PiCamKuroManager:
    """
    Minimal PICam manager for Princeton Instruments Kuro.

    Scope:
    - lazy connect / disconnect
    - exposure
    - ROI + binning
    - single-frame acquisition
    - returns numpy float32 image for DeepLight SpectroManager
    """

    def __init__(self, dll_path: str | None = None):
        self.dll_path = self._find_picam_dll(dll_path)
        self.runtime_dir = os.path.dirname(self.dll_path)

        self.lib = None
        self.camera = PicamHandle()
        self.connected = False

        self._dll_dir_handle = None
        self._last_shape = (1200, 1200)
        self._last_dtype = np.uint16

    # ------------------------------------------------------------------
    # low-level helpers
    # ------------------------------------------------------------------

    def _find_picam_dll(self, user_path: str | None = None) -> str:
        """
        Find Picam.dll from an official PICAM installation.
        """
        candidates = []

        if user_path:
            candidates.append(Path(user_path))

        picam_root = os.environ.get("PicamRoot")
        if picam_root:
            candidates.append(Path(picam_root) / "Runtime" / "Picam.dll")
            candidates.append(Path(picam_root) / "Picam.dll")

        candidates.append(Path(r"C:\Program Files\Princeton Instruments\PICam\Runtime\Picam.dll"))
        candidates.append(Path(r"C:\Program Files\Princeton Instruments\PICam\Picam.dll"))
        candidates.append(Path(r"C:\Program Files\Common Files\Princeton Instruments\PICam\Runtime\Picam.dll"))

        for p in candidates:
            if p.is_file():
                return str(p.resolve())

        raise FileNotFoundError(
            "Picam.dll not found in official PICAM installation paths. "
            "Expected something like "
            r"C:\Program Files\Princeton Instruments\PICam\Runtime\Picam.dll"
        )
    
    def _check(self, err: int, where: str):
        if int(err) != 0:
            raise RuntimeError(f"{where} failed with PicamError={int(err)}")

    def _decode_c_string(self, arr) -> str:
        raw = bytes(arr)
        return raw.split(b"\x00", 1)[0].decode("ascii", errors="replace")
    
    def _discover_cameras(self):
        """
        Ask PICAM to actively discover cameras, then stop discovery.
        """
        discovering = piint(0)

        self._check(
            self.lib.PicamAdvanced_IsDiscoveringCameras(byref(discovering)),
            "PicamAdvanced_IsDiscoveringCameras(pre)",
        )
        print(f"[PICam] discovering(pre)={int(discovering.value)}")

        self._check(
            self.lib.PicamAdvanced_DiscoverCameras(),
            "PicamAdvanced_DiscoverCameras",
        )

        self._check(
            self.lib.PicamAdvanced_IsDiscoveringCameras(byref(discovering)),
            "PicamAdvanced_IsDiscoveringCameras(post-start)",
        )
        print(f"[PICam] discovering(post-start)={int(discovering.value)}")

        try:
            self._check(
                self.lib.PicamAdvanced_StopDiscoveringCameras(),
                "PicamAdvanced_StopDiscoveringCameras",
            )
        except Exception as e:
            print(f"[PICam] stop discovery warning: {e}")

        self._check(
            self.lib.PicamAdvanced_IsDiscoveringCameras(byref(discovering)),
            "PicamAdvanced_IsDiscoveringCameras(post-stop)",
        )
        print(f"[PICam] discovering(post-stop)={int(discovering.value)}")
    
    def _load_library(self):
        if not os.path.isfile(self.dll_path):
            raise FileNotFoundError(f"PICam DLL not found: {self.dll_path}")

        if hasattr(os, "add_dll_directory"):
            self._dll_dir_handle = os.add_dll_directory(self.runtime_dir)

        self.lib = ctypes.WinDLL(self.dll_path)

        self.lib.Picam_InitializeLibrary.restype = piint
        self.lib.Picam_UninitializeLibrary.restype = piint

        self.lib.PicamAdvanced_DiscoverCameras.argtypes = []
        self.lib.PicamAdvanced_DiscoverCameras.restype = piint

        self.lib.PicamAdvanced_StopDiscoveringCameras.argtypes = []
        self.lib.PicamAdvanced_StopDiscoveringCameras.restype = piint

        self.lib.PicamAdvanced_IsDiscoveringCameras.argtypes = [POINTER(piint)]
        self.lib.PicamAdvanced_IsDiscoveringCameras.restype = piint

        self.lib.Picam_OpenFirstCamera.argtypes = [POINTER(PicamHandle)]
        self.lib.Picam_OpenFirstCamera.restype = piint

        self.lib.Picam_GetAvailableCameraIDs.argtypes = [
            POINTER(POINTER(PicamCameraID)),
            POINTER(piint),
        ]
        self.lib.Picam_GetAvailableCameraIDs.restype = piint

        self.lib.Picam_DestroyCameraIDs.argtypes = [POINTER(PicamCameraID)]
        self.lib.Picam_DestroyCameraIDs.restype = piint

        self.lib.Picam_OpenCamera.argtypes = [
            POINTER(PicamCameraID),
            POINTER(PicamHandle),
        ]
        self.lib.Picam_OpenCamera.restype = piint

        self.lib.Picam_CloseCamera.argtypes = [PicamHandle]
        self.lib.Picam_CloseCamera.restype = piint

        self.lib.Picam_GetParameterIntegerValue.argtypes = [PicamHandle, piint, POINTER(piint)]
        self.lib.Picam_GetParameterIntegerValue.restype = piint

        self.lib.Picam_SetParameterIntegerValue.argtypes = [PicamHandle, piint, piint]
        self.lib.Picam_SetParameterIntegerValue.restype = piint

        self.lib.Picam_SetParameterFloatingPointValue.argtypes = [PicamHandle, piint, piflt]
        self.lib.Picam_SetParameterFloatingPointValue.restype = piint

        self.lib.Picam_SetParameterRoisValue.argtypes = [PicamHandle, piint, POINTER(PicamRois)]
        self.lib.Picam_SetParameterRoisValue.restype = piint

        self.lib.Picam_CommitParameters.argtypes = [PicamHandle, POINTER(POINTER(piint)), POINTER(piint)]
        self.lib.Picam_CommitParameters.restype = piint

        self.lib.Picam_Acquire.argtypes = [
            PicamHandle,
            pi64s,
            piint,
            POINTER(PicamAvailableData),
            POINTER(piint),
        ]
        self.lib.Picam_Acquire.restype = piint

    # ------------------------------------------------------------------
    # connection
    # ------------------------------------------------------------------

    def connect(self):
        if self.connected:
            return

        print(f"[PICam] dll_path={self.dll_path}")
        
        self._load_library()
        self._check(self.lib.Picam_InitializeLibrary(), "Picam_InitializeLibrary")

        self._discover_cameras()

        id_array = POINTER(PicamCameraID)()
        id_count = piint(0)

        try:
            self._check(
                self.lib.Picam_GetAvailableCameraIDs(
                    byref(id_array),
                    byref(id_count),
                ),
                "Picam_GetAvailableCameraIDs",
            )

            count = int(id_count.value)
            print(f"[PICam] available cameras: {count}")

            if count <= 0:
                raise RuntimeError(
                    "PICAM sees no available cameras. "
                    "Check the official PICAM installation and camera driver."
                )

            chosen_index = None

            for i in range(count):
                cam_id = id_array[i]
                model = int(cam_id.model)
                iface = int(cam_id.computer_interface)
                sensor = self._decode_c_string(cam_id.sensor_name)
                serial = self._decode_c_string(cam_id.serial_number)

                print(
                    f"[PICam] cam[{i}] model={model} interface={iface} "
                    f"sensor='{sensor}' serial='{serial}'"
                )

                # Prefer Kuro models
                if model in (1901, 1902, 1903):
                    chosen_index = i
                    break

            if chosen_index is None:
                chosen_index = 0

            self._check(
                self.lib.Picam_OpenCamera(
                    byref(id_array[chosen_index]),
                    byref(self.camera),
                ),
                "Picam_OpenCamera",
            )

            self.connected = True
            print(f"[PICam] Connected using {self.dll_path}")

        finally:
            try:
                if id_array:
                    self.lib.Picam_DestroyCameraIDs(id_array)
            except Exception:
                pass

    def disconnect(self):
        if not self.connected:
            return

        try:
            self.lib.Picam_CloseCamera(self.camera)
        finally:
            try:
                self.lib.Picam_UninitializeLibrary()
            finally:
                self.connected = False
                self.camera = PicamHandle()

                if self._dll_dir_handle is not None:
                    try:
                        self._dll_dir_handle.close()
                    except Exception:
                        pass
                    self._dll_dir_handle = None

    # ------------------------------------------------------------------
    # parameter helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_binning_factor(params: dict) -> int:
        text = str((params or {}).get("binning", "1x1")).lower()
        try:
            bx = int(text.split("x")[0])
            return max(1, bx)
        except Exception:
            return 1

    def _get_integer_parameter(self, parameter: int) -> int:
        value = piint()
        self._check(
            self.lib.Picam_GetParameterIntegerValue(self.camera, piint(parameter), byref(value)),
            f"Picam_GetParameterIntegerValue({parameter})",
        )
        return int(value.value)

    def _get_sensor_shape(self) -> tuple[int, int]:
        width = self._get_integer_parameter(PicamParameter_SensorActiveWidth)
        height = self._get_integer_parameter(PicamParameter_SensorActiveHeight)
        return int(height), int(width)

    def _commit(self):
        failed_array = POINTER(piint)()
        failed_count = piint(0)
        self._check(
            self.lib.Picam_CommitParameters(self.camera, byref(failed_array), byref(failed_count)),
            "Picam_CommitParameters",
        )
        if int(failed_count.value) != 0:
            raise RuntimeError(f"PICam rejected {int(failed_count.value)} parameter(s) during commit")

    def _set_exposure(self, exposure_ms: float):
        self._check(
            self.lib.Picam_SetParameterFloatingPointValue(
                self.camera,
                piint(PicamParameter_ExposureTime),
                piflt(float(exposure_ms)),
            ),
            "Picam_SetParameterFloatingPointValue(ExposureTime)",
        )

    def _set_pixel_format(self, params: dict):
        fmt = str((params or {}).get("pixel_format", "Mono16")).lower()
        if "32" in fmt:
            picam_fmt = PicamPixelFormat_Monochrome32Bit
            self._last_dtype = np.uint32
        else:
            picam_fmt = PicamPixelFormat_Monochrome16Bit
            self._last_dtype = np.uint16

        self._check(
            self.lib.Picam_SetParameterIntegerValue(
                self.camera,
                piint(PicamParameter_PixelFormat),
                piint(picam_fmt),
            ),
            "Picam_SetParameterIntegerValue(PixelFormat)",
        )

    def _set_roi_and_binning(self, params: dict):
        sensor_h, sensor_w = self._get_sensor_shape()
        binning = self._parse_binning_factor(params)

        full_w_binned = max(1, sensor_w // binning)
        full_h_binned = max(1, sensor_h // binning)

        roi_enabled = bool((params or {}).get("roi_enabled", False))

        if roi_enabled:
            x_b = int((params or {}).get("roi_x", 0) or 0)
            y_b = int((params or {}).get("roi_y", 0) or 0)
            w_b = int((params or {}).get("roi_width", full_w_binned) or full_w_binned)
            h_b = int((params or {}).get("roi_height", full_h_binned) or full_h_binned)

            x_b = max(0, min(x_b, full_w_binned - 1))
            y_b = max(0, min(y_b, full_h_binned - 1))
            w_b = max(1, min(w_b, full_w_binned - x_b))
            h_b = max(1, min(h_b, full_h_binned - y_b))
        else:
            x_b = 0
            y_b = 0
            w_b = full_w_binned
            h_b = full_h_binned

        x = int(x_b * binning)
        y = int(y_b * binning)
        width = int(w_b * binning)
        height = int(h_b * binning)

        roi = PicamRoi(
            x=piint(x),
            width=piint(width),
            x_binning=piint(binning),
            y=piint(y),
            height=piint(height),
            y_binning=piint(binning),
        )
        roi_array = (PicamRoi * 1)(roi)
        rois = PicamRois(roi_array=roi_array, roi_count=piint(1))

        self._check(
            self.lib.Picam_SetParameterRoisValue(
                self.camera,
                piint(PicamParameter_Rois),
                byref(rois),
            ),
            "Picam_SetParameterRoisValue(Rois)",
        )

        self._last_shape = (int(h_b), int(w_b))

    def apply_parameters(self, params: dict | None):
        params = dict(params or {})
        self.connect()
        self._set_exposure(float(params.get("exposure_ms", 10.0) or 10.0))
        self._set_pixel_format(params)
        self._set_roi_and_binning(params)
        self._commit()

    # ------------------------------------------------------------------
    # acquisition
    # ------------------------------------------------------------------

    def snap(self, params: dict | None = None) -> np.ndarray:
        self.apply_parameters(params)

        available = PicamAvailableData()
        errors = piint(0)

        timeout_ms = max(
            1000,
            int(round(float((params or {}).get("exposure_ms", 10.0) or 10.0) * 5.0 + 2000.0))
        )

        self._check(
            self.lib.Picam_Acquire(
                self.camera,
                pi64s(1),
                piint(timeout_ms),
                byref(available),
                byref(errors),
            ),
            "Picam_Acquire",
        )

        readout_stride_bytes = self._get_integer_parameter(PicamParameter_ReadoutStride)
        frame_size_bytes = self._get_integer_parameter(PicamParameter_FrameSize)

        if frame_size_bytes <= 0:
            raise RuntimeError("PICam returned an invalid frame size")

        raw = ctypes.string_at(available.initial_readout, readout_stride_bytes)
        arr = np.frombuffer(raw[:frame_size_bytes], dtype=self._last_dtype)

        h, w = self._last_shape
        expected = int(h) * int(w)

        if arr.size < expected:
            raise RuntimeError(
                f"PICam frame too small: got {arr.size} px, expected {expected} px "
                f"for shape {(h, w)}"
            )

        image = arr[:expected].reshape((h, w)).astype(np.float32, copy=False)
        print(
            f"[PICam] snap ok shape={image.shape} dtype={image.dtype} "
            f"min={float(image.min())} max={float(image.max())}"
        )
        return image