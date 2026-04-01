from __future__ import annotations

import os
import ctypes
from ctypes import POINTER, byref
import numpy as np


# ----------------------------------------------------------------------
# PICam basic C types (from pil_platform.h / picam.h)
# ----------------------------------------------------------------------

piint = ctypes.c_int
piflt = ctypes.c_double
pi64s = ctypes.c_longlong
pibln = ctypes.c_int
PicamHandle = ctypes.c_void_p


class PicamCameraID(ctypes.Structure):
    _fields_ = [
        ("model", piint),
        ("computer_interface", piint),
        ("sensor_name", ctypes.c_char * 64),
        ("serial_number", ctypes.c_char * 64),
    ]


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


class PicamRangeConstraint(ctypes.Structure):
    _fields_ = [
        ("scope", piint),
        ("severity", piint),
        ("empty_set", pibln),
        ("minimum", piflt),
        ("maximum", piflt),
        ("increment", piflt),
        ("excluded_values_array", POINTER(piflt)),
        ("excluded_values_count", piint),
        ("outlying_values_array", POINTER(piflt)),
        ("outlying_values_count", piint),
    ]


class PicamRoisConstraint(ctypes.Structure):
    _fields_ = [
        ("scope", piint),
        ("severity", piint),
        ("empty_set", pibln),
        ("rules", piint),
        ("maximum_roi_count", piint),
        ("x_constraint", PicamRangeConstraint),
        ("width_constraint", PicamRangeConstraint),
        ("x_binning_limits_array", POINTER(piint)),
        ("x_binning_limits_count", piint),
        ("y_constraint", PicamRangeConstraint),
        ("height_constraint", PicamRangeConstraint),
        ("y_binning_limits_array", POINTER(piint)),
        ("y_binning_limits_count", piint),
    ]


# ----------------------------------------------------------------------
# Constants from picam.h
# ----------------------------------------------------------------------

PicamError_None = 0
PicamConstraintCategory_Capable = 1
PicamConstraintCategory_Required = 2

PicamParameter_ExposureTime = 33685527
PicamParameter_Rois = 67436581
PicamParameter_PixelFormat = 50593833
PicamParameter_FrameSize = 16842794
PicamParameter_FrameStride = 16842795
PicamParameter_FramesPerReadout = 16842796
PicamParameter_ReadoutStride = 16842808
PicamParameter_PixelBitDepth = 16908336
PicamParameter_SensorActiveWidth = 16842811
PicamParameter_SensorActiveHeight = 16842812

PicamPixelFormat_Monochrome16Bit = 1
PicamPixelFormat_Monochrome32Bit = 2

PicamAcquisitionErrorsMask_None = 0


class PiCamKuroManager:
    def __init__(self, dll_path: str | None = None):
        if dll_path is None:
            dll_path = r"C:\Program Files\Princeton Instruments\PICam\Runtime\Picam.dll"

        self.dll_path = os.path.abspath(dll_path)
        self.runtime_dir = os.path.dirname(self.dll_path)

        self.lib = None
        self.camera = PicamHandle()
        self.connected = False
        self._library_initialized = False
        self._dll_dir_handle = None

        self._last_shape = None
        self._last_dtype = np.uint16
        self._last_frame_size = None
        self._last_readout_stride = None
        self._last_frames_per_readout = 1

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------

    def _check(self, err: int, where: str):
        err = int(err)
        if err != PicamError_None:
            raise RuntimeError(f"{where} failed with PicamError={err}")

    def _require_connected(self):
        if not self.connected or not self.camera:
            raise RuntimeError("PICam camera is not connected")

    def _load_library(self):
        if self.lib is not None:
            return

        if not os.path.isfile(self.dll_path):
            raise FileNotFoundError(f"PICam DLL not found: {self.dll_path}")

        if hasattr(os, "add_dll_directory"):
            self._dll_dir_handle = os.add_dll_directory(self.runtime_dir)

        self.lib = ctypes.WinDLL(self.dll_path)

        self.lib.Picam_InitializeLibrary.argtypes = []
        self.lib.Picam_InitializeLibrary.restype = piint

        self.lib.Picam_UninitializeLibrary.argtypes = []
        self.lib.Picam_UninitializeLibrary.restype = piint

        self.lib.Picam_OpenFirstCamera.argtypes = [POINTER(PicamHandle)]
        self.lib.Picam_OpenFirstCamera.restype = piint

        self.lib.Picam_CloseCamera.argtypes = [PicamHandle]
        self.lib.Picam_CloseCamera.restype = piint

        self.lib.Picam_GetCameraID.argtypes = [PicamHandle, POINTER(PicamCameraID)]
        self.lib.Picam_GetCameraID.restype = piint

        self.lib.Picam_GetParameterIntegerValue.argtypes = [PicamHandle, piint, POINTER(piint)]
        self.lib.Picam_GetParameterIntegerValue.restype = piint

        self.lib.Picam_GetParameterFloatingPointValue.argtypes = [PicamHandle, piint, POINTER(piflt)]
        self.lib.Picam_GetParameterFloatingPointValue.restype = piint

        self.lib.Picam_SetParameterIntegerValue.argtypes = [PicamHandle, piint, piint]
        self.lib.Picam_SetParameterIntegerValue.restype = piint

        self.lib.Picam_SetParameterFloatingPointValue.argtypes = [PicamHandle, piint, piflt]
        self.lib.Picam_SetParameterFloatingPointValue.restype = piint

        self.lib.Picam_SetParameterRoisValue.argtypes = [PicamHandle, piint, POINTER(PicamRois)]
        self.lib.Picam_SetParameterRoisValue.restype = piint

        self.lib.Picam_DoesParameterExist.argtypes = [PicamHandle, piint, POINTER(pibln)]
        self.lib.Picam_DoesParameterExist.restype = piint

        self.lib.Picam_AreParametersCommitted.argtypes = [PicamHandle, POINTER(pibln)]
        self.lib.Picam_AreParametersCommitted.restype = piint

        self.lib.Picam_CommitParameters.argtypes = [PicamHandle, POINTER(POINTER(piint)), POINTER(piint)]
        self.lib.Picam_CommitParameters.restype = piint

        self.lib.Picam_DestroyParameters.argtypes = [POINTER(piint)]
        self.lib.Picam_DestroyParameters.restype = None

        self.lib.Picam_GetParameterRoisConstraint.argtypes = [
            PicamHandle, piint, piint, POINTER(POINTER(PicamRoisConstraint))
        ]
        self.lib.Picam_GetParameterRoisConstraint.restype = piint

        self.lib.Picam_DestroyRoisConstraints.argtypes = [POINTER(PicamRoisConstraint)]
        self.lib.Picam_DestroyRoisConstraints.restype = None

        self.lib.Picam_Acquire.argtypes = [
            PicamHandle,
            pi64s,
            piint,
            POINTER(PicamAvailableData),
            POINTER(piint),
        ]
        self.lib.Picam_Acquire.restype = piint

    def _parameter_exists(self, parameter: int) -> bool:
        exists = pibln(0)
        self._check(
            self.lib.Picam_DoesParameterExist(self.camera, piint(parameter), byref(exists)),
            f"Picam_DoesParameterExist({parameter})",
        )
        return bool(exists.value)

    def _get_integer_parameter(self, parameter: int) -> int:
        value = piint()
        self._check(
            self.lib.Picam_GetParameterIntegerValue(self.camera, piint(parameter), byref(value)),
            f"Picam_GetParameterIntegerValue({parameter})",
        )
        return int(value.value)

    def _get_float_parameter(self, parameter: int) -> float:
        value = piflt()
        self._check(
            self.lib.Picam_GetParameterFloatingPointValue(self.camera, piint(parameter), byref(value)),
            f"Picam_GetParameterFloatingPointValue({parameter})",
        )
        return float(value.value)

    @staticmethod
    def _parse_binning_factor(params: dict) -> int:
        text = str((params or {}).get("binning", "1x1")).lower()
        try:
            bx = int(text.split("x")[0])
            return max(1, bx)
        except Exception:
            return 1

    # ------------------------------------------------------------------
    # connection
    # ------------------------------------------------------------------

    def connect(self):
        if self.connected:
            return

        self._load_library()
        self._check(self.lib.Picam_InitializeLibrary(), "Picam_InitializeLibrary")
        self._library_initialized = True

        try:
            self._check(self.lib.Picam_OpenFirstCamera(byref(self.camera)), "Picam_OpenFirstCamera")
        except Exception:
            try:
                self.lib.Picam_UninitializeLibrary()
            finally:
                self._library_initialized = False
                self.camera = PicamHandle()
            raise

        cam_id = PicamCameraID()
        self._check(self.lib.Picam_GetCameraID(self.camera, byref(cam_id)), "Picam_GetCameraID")

        self.connected = True
        print(
            f"[PICam] Connected model={cam_id.model} "
            f"sn={cam_id.serial_number.decode(errors='ignore')} "
            f"sensor={cam_id.sensor_name.decode(errors='ignore')}"
        )

    def disconnect(self):
        try:
            if self.connected and self.camera:
                try:
                    self.lib.Picam_CloseCamera(self.camera)
                except Exception:
                    pass
        finally:
            self.connected = False
            self.camera = PicamHandle()

            if self._library_initialized and self.lib is not None:
                try:
                    self.lib.Picam_UninitializeLibrary()
                except Exception:
                    pass
                self._library_initialized = False

            if self._dll_dir_handle is not None:
                try:
                    self._dll_dir_handle.close()
                except Exception:
                    pass
                self._dll_dir_handle = None

    # ------------------------------------------------------------------
    # setup
    # ------------------------------------------------------------------

    def _set_exposure(self, exposure_ms: float):
        if self._parameter_exists(PicamParameter_ExposureTime):
            self._check(
                self.lib.Picam_SetParameterFloatingPointValue(
                    self.camera,
                    piint(PicamParameter_ExposureTime),
                    piflt(float(exposure_ms)),
                ),
                "Picam_SetParameterFloatingPointValue(ExposureTime)",
            )

    def _set_pixel_format(self, params: dict):
        if not self._parameter_exists(PicamParameter_PixelFormat):
            return

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
        if not self._parameter_exists(PicamParameter_Rois):
            sensor_h = self._get_integer_parameter(PicamParameter_SensorActiveHeight)
            sensor_w = self._get_integer_parameter(PicamParameter_SensorActiveWidth)
            self._last_shape = (sensor_h, sensor_w)
            return

        constraint_ptr = POINTER(PicamRoisConstraint)()
        try:
            self._check(
                self.lib.Picam_GetParameterRoisConstraint(
                    self.camera,
                    piint(PicamParameter_Rois),
                    piint(PicamConstraintCategory_Required),
                    byref(constraint_ptr),
                ),
                "Picam_GetParameterRoisConstraint",
            )

            c = constraint_ptr.contents
            sensor_w = int(c.width_constraint.maximum)
            sensor_h = int(c.height_constraint.maximum)

            binning = self._parse_binning_factor(params)

            valid_x_bins = None
            if c.x_binning_limits_count > 0:
                valid_x_bins = [int(c.x_binning_limits_array[i]) for i in range(c.x_binning_limits_count)]
            valid_y_bins = None
            if c.y_binning_limits_count > 0:
                valid_y_bins = [int(c.y_binning_limits_array[i]) for i in range(c.y_binning_limits_count)]

            if valid_x_bins and binning not in valid_x_bins:
                binning = valid_x_bins[0]
            if valid_y_bins and binning not in valid_y_bins:
                binning = valid_y_bins[0]

            full_w_binned = max(1, sensor_w // binning)
            full_h_binned = max(1, sensor_h // binning)

            roi_enabled = bool((params or {}).get("roi_enabled", False))
            if roi_enabled:
                x_b = int((params or {}).get("roi_x", 0) or 0)
                y_b = int((params or {}).get("roi_y", 0) or 0)
                w_b = int((params or {}).get("roi_width", full_w_binned) or full_w_binned)
                h_b = int((params or {}).get("roi_height", full_h_binned) or full_h_binned)
            else:
                x_b, y_b, w_b, h_b = 0, 0, full_w_binned, full_h_binned

            x_b = max(0, min(x_b, full_w_binned - 1))
            y_b = max(0, min(y_b, full_h_binned - 1))
            w_b = max(1, min(w_b, full_w_binned - x_b))
            h_b = max(1, min(h_b, full_h_binned - y_b))

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

            self._last_shape = (h_b, w_b)
        finally:
            if constraint_ptr:
                self.lib.Picam_DestroyRoisConstraints(constraint_ptr)

    def _commit(self):
        committed = pibln(0)
        self._check(self.lib.Picam_AreParametersCommitted(self.camera, byref(committed)),
                    "Picam_AreParametersCommitted")
        if committed.value:
            return

        failed = POINTER(piint)()
        failed_count = piint(0)
        try:
            self._check(
                self.lib.Picam_CommitParameters(self.camera, byref(failed), byref(failed_count)),
                "Picam_CommitParameters",
            )
            if int(failed_count.value) != 0:
                failed_list = [int(failed[i]) for i in range(failed_count.value)]
                raise RuntimeError(f"PICam rejected parameters during commit: {failed_list}")
        finally:
            if failed:
                self.lib.Picam_DestroyParameters(failed)

    def apply_parameters(self, params: dict | None):
        params = dict(params or {})
        self.connect()
        self._require_connected()

        self._set_exposure(float(params.get("exposure_ms", 10.0) or 10.0))
        self._set_pixel_format(params)
        self._set_roi_and_binning(params)
        self._commit()

        self._last_frame_size = self._get_integer_parameter(PicamParameter_FrameSize)
        self._last_readout_stride = self._get_integer_parameter(PicamParameter_ReadoutStride)
        self._last_frames_per_readout = self._get_integer_parameter(PicamParameter_FramesPerReadout)

    # ------------------------------------------------------------------
    # acquisition
    # ------------------------------------------------------------------

    def snap(self, params: dict | None = None) -> np.ndarray:
        self.apply_parameters(params)

        available = PicamAvailableData()
        errors = piint(PicamAcquisitionErrorsMask_None)

        exposure_ms = float((params or {}).get("exposure_ms", 10.0) or 10.0)
        timeout_ms = max(1000, int(round(exposure_ms * 5.0 + 2000.0)))

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

        if int(errors.value) != PicamAcquisitionErrorsMask_None:
            raise RuntimeError(f"PICam acquisition errors mask={int(errors.value)}")
        if not available.initial_readout:
            raise RuntimeError("PICam returned a null readout buffer")
        if int(available.readout_count) < 1:
            raise RuntimeError("PICam returned zero readouts")

        frame_size_bytes = int(self._last_frame_size)
        readout_stride_bytes = int(self._last_readout_stride)

        if frame_size_bytes <= 0 or readout_stride_bytes <= 0:
            raise RuntimeError(
                f"Invalid PICam sizes frame={frame_size_bytes} stride={readout_stride_bytes}"
            )
        if readout_stride_bytes < frame_size_bytes:
            raise RuntimeError(
                f"Invalid PICam stride/frame size stride={readout_stride_bytes} frame={frame_size_bytes}"
            )

        raw = ctypes.string_at(available.initial_readout, frame_size_bytes)
        arr = np.frombuffer(raw, dtype=self._last_dtype)

        h, w = self._last_shape
        expected = int(h) * int(w)

        if arr.size < expected:
            raise RuntimeError(
                f"PICam frame too small: got {arr.size} px, expected {expected} px for {(h, w)}"
            )

        image = arr[:expected].reshape((h, w)).astype(np.float32, copy=False)

        print(
            f"[PICam] snap ok shape={image.shape} "
            f"frame_size={frame_size_bytes} stride={readout_stride_bytes} "
            f"min={float(image.min())} max={float(image.max())}"
        )
        return image