import struct
import time
import serial


PORT = "COM11"
BAUDRATE = 9600
TIMEOUT_S = 1.0


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
            raise ValueError("Invalid COBS frame: unexpected 0 inside payload")

        next_idx = idx + code - 1
        if next_idx > n and code != 1:
            raise ValueError("Invalid COBS frame: code exceeds payload length")

        out.extend(data[idx:min(next_idx, n)])
        idx = next_idx

        if code != 0xFF and idx < n:
            out.append(0)

    return bytes(out)


def transceive(ser: serial.Serial, payload: bytes) -> bytes:
    frame = cobs_encode(payload)
    ser.reset_input_buffer()
    ser.write(frame)
    ser.flush()

    raw = ser.read_until(b"\x00")
    if not raw:
        raise TimeoutError("No reply from Motion 8")

    if raw.endswith(b"\x00"):
        raw = raw[:-1]

    decoded = cobs_decode(raw)
    return decoded


def hexdump(data: bytes) -> str:
    return data.hex(" ")


def print_reply(label: str, payload: bytes, reply: bytes):
    status = reply[0] if len(reply) >= 1 else None
    print(f"\n=== {label} ===")
    print(f"TX: {hexdump(payload)}")
    print(f"RX: {hexdump(reply)}")
    if status is not None:
        print(f"status = 0x{status:02X}")


def cmd_get_software_version() -> bytes:
    return struct.pack("<BBB", 0xAA, 0x00, 0x01)


def cmd_get_device_count() -> bytes:
    return struct.pack("<BBB", 0xAA, 0x00, 0x09)


def cmd_get_channel_count() -> bytes:
    return struct.pack("<BBB", 0xAA, 0x00, 0x0A)


def cmd_get_profile_count() -> bytes:
    return struct.pack("<BBB", 0xAA, 0x00, 0x0C)


def cmd_get_unique_rack_identifier() -> bytes:
    return struct.pack("<BBB", 0xAA, 0x00, 0x0D)


def cmd_get_port1_device_information() -> bytes:
    return struct.pack("<BBB", 0xAA, 0x00, 0x12)


def cmd_get_port2_device_information() -> bytes:
    return struct.pack("<BBB", 0xAA, 0x00, 0x13)


def cmd_get_position(device_id: int) -> bytes:
    return struct.pack("<BBBB", 0xAA, 0x00, 0x14, int(device_id) & 0xFF)


def try_parse_software_version(reply: bytes):
    if len(reply) >= 9 and reply[0] == 0x00 and reply[2] == 0x00 and reply[3] == 0x01:
        major, minor, build = struct.unpack_from("<HHH", reply, 4)
        print(f"Parsed software version: major={major}, minor={minor}, build={build}")


def try_parse_device_count(reply: bytes):
    if len(reply) >= 5 and reply[0] == 0x00 and reply[2] == 0x00 and reply[3] == 0x09:
        print(f"Parsed device count: {reply[4]}")


def try_parse_channel_count(reply: bytes):
    if len(reply) >= 5 and reply[0] == 0x00 and reply[2] == 0x00 and reply[3] == 0x0A:
        print(f"Parsed channel count: {reply[4]}")


def try_parse_profile_count(reply: bytes):
    if len(reply) >= 5 and reply[0] == 0x00 and reply[2] == 0x00 and reply[3] == 0x0C:
        print(f"Parsed profile count: {reply[4]}")


def try_parse_uid(reply: bytes):
    if len(reply) >= 16 and reply[0] == 0x00 and reply[2] == 0x00 and reply[3] == 0x0D:
        uid = reply[4:16]
        print(f"Parsed UID: {uid.hex(' ')}")


def try_parse_port_info(reply: bytes, port_label: str):
    if len(reply) < 5 or reply[0] != 0x00 or reply[2] != 0x00:
        return

    received = reply[4]
    print(f"{port_label}: received={received}")
    if received == 0:
        return

    if len(reply) >= 21:
        devid = struct.unpack_from("<I", reply, 5)[0]
        main_major, main_minor, main_build = struct.unpack_from("<HHH", reply, 9)
        loader_major, loader_minor, loader_build = struct.unpack_from("<HHH", reply, 15)
        uid = reply[21:33] if len(reply) >= 33 else b""

        print(f"{port_label}: device id on bus = {devid}")
        print(
            f"{port_label}: main fw = {main_major}.{main_minor}.{main_build}, "
            f"loader fw = {loader_major}.{loader_minor}.{loader_build}"
        )
        if uid:
            print(f"{port_label}: uid = {uid.hex(' ')}")


def try_parse_position(reply: bytes):
    if len(reply) >= 13 and reply[0] == 0x00 and reply[2] == 0x00 and reply[3] == 0x14:
        device = reply[4]
        x_units, y_units = struct.unpack_from("<ii", reply, 5)
        x_um = x_units * 0.01
        y_um = y_units * 0.01
        print(f"Parsed position: device={device}, X={x_um:.2f} µm, Y={y_um:.2f} µm")


def run_command(ser: serial.Serial, label: str, payload: bytes, parser=None):
    try:
        reply = transceive(ser, payload)
        print_reply(label, payload, reply)
        if parser is not None:
            parser(reply)
    except Exception as e:
        print(f"\n=== {label} ===")
        print(f"TX: {hexdump(payload)}")
        print(f"ERROR: {e}")


def main():
    print(f"Opening {PORT} @ {BAUDRATE}...")
    with serial.Serial(PORT, baudrate=BAUDRATE, timeout=TIMEOUT_S, write_timeout=TIMEOUT_S) as ser:
        time.sleep(0.1)

        run_command(ser, "Get software version", cmd_get_software_version(), try_parse_software_version)
        run_command(ser, "Get device count", cmd_get_device_count(), try_parse_device_count)
        run_command(ser, "Get channel count", cmd_get_channel_count(), try_parse_channel_count)
        run_command(ser, "Get profile count", cmd_get_profile_count(), try_parse_profile_count)
        run_command(ser, "Get unique rack identifier", cmd_get_unique_rack_identifier(), try_parse_uid)
        run_command(
            ser,
            "Get port 1 device information",
            cmd_get_port1_device_information(),
            lambda r: try_parse_port_info(r, "Port 1"),
        )
        run_command(
            ser,
            "Get port 2 device information",
            cmd_get_port2_device_information(),
            lambda r: try_parse_port_info(r, "Port 2"),
        )

        for device_id in [0, 1, 2, 3, 4, 5, 6, 7, 11]:
            run_command(
                ser,
                f"Get position (device_id={device_id})",
                cmd_get_position(device_id),
                try_parse_position,
            )


if __name__ == "__main__":
    main()