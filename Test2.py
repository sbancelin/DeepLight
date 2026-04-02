import serial
import struct
import time

PORT = "COM11"
DEVICE_ID = 0

def cobs_encode(data: bytes) -> bytes:
    read_index = 0
    write_index = 1
    code_index = 0
    code = 1
    encoded = bytearray(len(data) + len(data) // 254 + 2)

    while read_index < len(data):
        byte = data[read_index]
        read_index += 1
        if byte == 0:
            encoded[code_index] = code
            code = 1
            code_index = write_index
            write_index += 1
        else:
            encoded[write_index] = byte
            write_index += 1
            code += 1
            if code == 0xFF:
                encoded[code_index] = code
                code = 1
                code_index = write_index
                write_index += 1

    encoded[code_index] = code
    return bytes(encoded[:write_index]) + b"\x00"

def cobs_decode(data: bytes) -> bytes:
    if not data:
        return b""
    out = bytearray()
    idx = 0
    n = len(data)
    while idx < n:
        code = data[idx]
        idx += 1
        if code == 0:
            raise ValueError("Invalid COBS frame")
        end = idx + code - 1
        out.extend(data[idx:min(end, n)])
        idx = end
        if code != 0xFF and idx < n:
            out.append(0)
    return bytes(out)

def transact(ser, payload, label=""):
    ser.reset_input_buffer()
    ser.write(cobs_encode(payload))
    ser.flush()
    raw = ser.read_until(b"\x00")
    if not raw:
        raise TimeoutError(label)
    raw = raw[:-1]
    dec = cobs_decode(raw)
    status = dec[0] if dec else None
    print(f"{label}: STATUS=0x{status:02X}")
    if not dec or status != 0:
        raise RuntimeError(f"{label} failed")
    return dec

def set_profile_axis_assignment(ser, profile_index: int, axis_assign: int):
    payload = struct.pack(
        "<BBBBBB",
        0xAA, 0x03, 0x11, DEVICE_ID,
        int(profile_index) & 0xFF,
        int(axis_assign) & 0xFF,
    )
    transact(ser, payload, f"set_profile_assign_{profile_index}")

def set_profile_top_speed(ser, profile_index: int, speed_units: float):
    payload = (
        struct.pack("<BBBBB", 0xAA, 0x03, 0x05, DEVICE_ID, int(profile_index) & 0xFF)
        + struct.pack("<d", float(speed_units))
    )
    transact(ser, payload, f"set_profile_speed_{profile_index}")

def set_profile_accel(ser, profile_index: int, accel_units: float):
    payload = (
        struct.pack("<BBBBB", 0xAA, 0x03, 0x06, DEVICE_ID, int(profile_index) & 0xFF)
        + struct.pack("<d", float(accel_units))
    )
    transact(ser, payload, f"set_profile_accel_{profile_index}")

def main():
    ser = serial.Serial(PORT, baudrate=9600, timeout=1, write_timeout=1)
    time.sleep(1.0)
    try:
        # profil 0 = XY
        set_profile_axis_assignment(ser, 0, 0x03)

        # 1 mm/s -> 100000 en hundredths of µm/s
        set_profile_top_speed(ser, 0, 100000.0)

        # 1 mm/s² -> 100000 en hundredths of µm/s²
        set_profile_accel(ser, 0, 100000.0)

        # les autres profils hors XY
        set_profile_axis_assignment(ser, 1, 0x00)
        set_profile_axis_assignment(ser, 2, 0x00)

        print("Profile 0 restored to sane XY defaults.")
    finally:
        ser.close()

if __name__ == "__main__":
    main()