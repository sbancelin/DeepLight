import ctypes
from ctypes import *

# ---------- types ----------
piint = c_int
pibln = c_int
PicamHandle = c_void_p

class PicamCameraID(Structure):
    _fields_ = [
        ("model", piint),
        ("computer_interface", piint),
        ("sensor_name", c_char * 64),
        ("serial_number", c_char * 64),
    ]

# ---------- constants ----------
PicamError_None = 0
PicamEnumeratedType_Model = 2
PicamEnumeratedType_ComputerInterface = 3
PicamEnumeratedType_Error = 1

dll_path = r"C:\Program Files\Princeton Instruments\PICam\Runtime\Picam.dll"
picam = ctypes.WinDLL(dll_path)

# ---------- prototypes ----------
picam.Picam_InitializeLibrary.argtypes = []
picam.Picam_InitializeLibrary.restype = piint

picam.Picam_UninitializeLibrary.argtypes = []
picam.Picam_UninitializeLibrary.restype = piint

picam.Picam_GetAvailableCameraIDs.argtypes = [POINTER(POINTER(PicamCameraID)), POINTER(piint)]
picam.Picam_GetAvailableCameraIDs.restype = piint

picam.Picam_GetUnavailableCameraIDs.argtypes = [POINTER(POINTER(PicamCameraID)), POINTER(piint)]
picam.Picam_GetUnavailableCameraIDs.restype = piint

picam.Picam_DestroyCameraIDs.argtypes = [POINTER(PicamCameraID)]
picam.Picam_DestroyCameraIDs.restype = piint

picam.Picam_IsCameraIDConnected.argtypes = [POINTER(PicamCameraID), POINTER(pibln)]
picam.Picam_IsCameraIDConnected.restype = piint

picam.Picam_IsCameraIDOpenElsewhere.argtypes = [POINTER(PicamCameraID), POINTER(pibln)]
picam.Picam_IsCameraIDOpenElsewhere.restype = piint

picam.Picam_GetEnumerationString.argtypes = [piint, piint, POINTER(c_char_p)]
picam.Picam_GetEnumerationString.restype = piint

picam.Picam_DestroyString.argtypes = [c_char_p]
picam.Picam_DestroyString.restype = piint


def enum_string(enum_type, value):
    s = c_char_p()
    err = picam.Picam_GetEnumerationString(enum_type, int(value), byref(s))
    if err != PicamError_None or not s.value:
        return f"<enum {enum_type}:{value}>"
    try:
        return s.value.decode(errors="replace")
    finally:
        picam.Picam_DestroyString(s)


def print_camera_list(title, arr_ptr, count):
    print(f"\n=== {title}: {count} ===")
    if count <= 0 or not arr_ptr:
        return

    for i in range(count):
        cam = arr_ptr[i]

        connected = pibln(0)
        open_elsewhere = pibln(0)

        err_conn = picam.Picam_IsCameraIDConnected(byref(cam), byref(connected))
        err_open = picam.Picam_IsCameraIDOpenElsewhere(byref(cam), byref(open_elsewhere))

        model_str = enum_string(PicamEnumeratedType_Model, cam.model)
        iface_str = enum_string(PicamEnumeratedType_ComputerInterface, cam.computer_interface)

        print(f"[{i}] model={cam.model} -> {model_str}")
        print(f"    serial={cam.serial_number.decode(errors='replace')}")
        print(f"    sensor={cam.sensor_name.decode(errors='replace')}")
        print(f"    interface={cam.computer_interface} -> {iface_str}")
        print(f"    connected={bool(connected.value)} (err={int(err_conn)})")
        print(f"    open_elsewhere={bool(open_elsewhere.value)} (err={int(err_open)})")


def main():
    print("Initializing PICam...")
    err = picam.Picam_InitializeLibrary()
    if err != PicamError_None:
        print("Init failed:", err, enum_string(PicamEnumeratedType_Error, err))
        return

    try:
        avail_ptr = POINTER(PicamCameraID)()
        avail_count = piint(0)

        err = picam.Picam_GetAvailableCameraIDs(byref(avail_ptr), byref(avail_count))
        print("\nGetAvailableCameraIDs err =", int(err), enum_string(PicamEnumeratedType_Error, err))
        print_camera_list("AVAILABLE", avail_ptr, int(avail_count.value))

        unavail_ptr = POINTER(PicamCameraID)()
        unavail_count = piint(0)

        err = picam.Picam_GetUnavailableCameraIDs(byref(unavail_ptr), byref(unavail_count))
        print("\nGetUnavailableCameraIDs err =", int(err), enum_string(PicamEnumeratedType_Error, err))
        print_camera_list("UNAVAILABLE", unavail_ptr, int(unavail_count.value))

        if avail_ptr:
            picam.Picam_DestroyCameraIDs(avail_ptr)
        if unavail_ptr:
            picam.Picam_DestroyCameraIDs(unavail_ptr)

    finally:
        picam.Picam_UninitializeLibrary()
        print("\nPICam uninitialized.")


if __name__ == "__main__":
    main()