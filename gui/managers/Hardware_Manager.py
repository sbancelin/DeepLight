from __future__ import annotations

import os
import time
import math
import struct
import serial
from threading import Lock
from typing import Optional

from PySide6.QtCore import QObject, Slot, QTimer

from .Positioner_Manager import MockPositionerManager, PositionerManager
from ..widgets.Log_Widget import logger
from .Motic_Camera_Manager import CameraController, OpenCVCameraBackend, MockCameraBackend
from .PiCam_Kuro_Manager import PiCamKuroManager


# =============================================================================
# HARD-CODED HARDWARE CONFIG
# =============================================================================

NI_DEVICE_NAME = "Dev1"

# NI AO / AI channels
NI_AO_X = f"{NI_DEVICE_NAME}/ao0"
NI_AO_Y = f"{NI_DEVICE_NAME}/ao1"

NI_AI_VIS = f"{NI_DEVICE_NAME}/ai0"
NI_AI_IR = f"{NI_DEVICE_NAME}/ai1"

# AI default range / config
NI_AI_MIN_V = -10.0
NI_AI_MAX_V = 10.0
NI_AI_TERMINAL_MODE = "DIFF"  # requested: differential

# NI counter defaults for digital PMTs
# These are only defaults: DeepLight may override them through detector specs / UI.
NI_CI_DEFAULT_COUNTER = f"{NI_DEVICE_NAME}/ctr2"
NI_CI_DEFAULT_SOURCE = f"/{NI_DEVICE_NAME}/PFI0"
NI_CI_DEFAULT_SAMPLE_CLOCK = f"/{NI_DEVICE_NAME}/ao/SampleClock"
NI_CI_DEFAULT_START_TRIGGER = f"/{NI_DEVICE_NAME}/ao/StartTrigger"

# Thorlabs serials
THORLABS_SHUTTER_SERIAL = "68800404"
THORLABS_ROTATOR_SERIALS = {
    "Mira 900": "27269600",
    "Tumecs": "27005331",
}

# PI serial (replace later)
PI_V308_SERIAL = "123041734"

# Kinesis path
THORLABS_KINESIS_PATH = r"C:\Program Files\Thorlabs\Kinesis"

# Half-wave plate power mapping:
# 0%   -> offset_deg
# 100% -> offset_deg + 45°
MIRA_POWER_MIN_PERCENT = 0.0
MIRA_POWER_MAX_PERCENT = 100.0
MIRA_ROTATOR_REL_MIN_DEG = 0.0
MIRA_ROTATOR_REL_MAX_DEG = 45.0

# PI Z defaults
PI_Z_AXIS_ID = 1
PI_Z_DEFAULT_VEL_MM_S = 0.5

# If your PRM controller class differs, change this later.
# Common values depending on controller family:
# - "KCubeDCServo"
# - "KCubeStepperMotor"
THORLABS_ROTATOR_CONTROLLER_KIND = "KCubeDCServo"

# Cobolt Flamenco
COBOLT_FLAMENCO_PORT = "COM12"
COBOLT_FLAMENCO_BAUDRATE = 115200
COBOLT_FLAMENCO_MAX_POWER_MW = 300.0

COBOLT_ELL14_PORT = "COM15"
COBOLT_ELL14_BAUDRATE = 9600
COBOLT_ELL14_ADDRESS = "0"
COBOLT_ELL14_COUNTS_PER_REV = 143360
COBOLT_ELL14_TIMEOUT_S = 1.0

# Spark Lasers ALCOR / XSight
SPARK_ALCOR_PORT = "COM14"
SPARK_ALCOR_BAUDRATE = 115200
SPARK_ALCOR_TIMEOUT_S = 0.7

SPARK_ALCOR_BASE_REP_RATE_KHZ = 80000.0
SPARK_ALCOR_REP_RATE_MIN_KHZ = 2900.0
SPARK_ALCOR_REP_RATE_MAX_KHZ = 81000.0

# Scientifica Motion 8 XY stage (virtual serial port)
SCIENTIFICA_STAGE_PORT = "COM11"
SCIENTIFICA_STAGE_BAUDRATE = 9600
SCIENTIFICA_STAGE_TIMEOUT_S = 1.0
SCIENTIFICA_STAGE_DEVICE_ID = 0
SCIENTIFICA_STAGE_X_AXIS_ID = 1
SCIENTIFICA_STAGE_Y_AXIS_ID = 0
SCIENTIFICA_STAGE_HOME_MODE = "in"   # "in" or "out"

SCIENTIFICA_STAGE_PROFILE_INDEX = 0
SCIENTIFICA_STAGE_DEFAULT_ACCEL_MM_S2 = 1.0
SCIENTIFICA_STAGE_HOME_USES_BOTH_AXES = True
SCIENTIFICA_STAGE_HOME_BEHAVIOR = "zero_offset"   # "zero_offset" or "device_home"
SCIENTIFICA_STAGE_PROFILE_ASSIGN_XY = 0x03

SCIENTIFICA_STAGE_SWAP_XY = False
SCIENTIFICA_STAGE_DEFAULT_SCALING_X = 0.635
SCIENTIFICA_STAGE_DEFAULT_SCALING_Y = 0.635

# =============================================================================
# OPTIONAL IMPORTS
# =============================================================================

_HAS_CLR = False
clr = None
CultureInfo = None
SystemDecimal = None

try:
    import clr  # pythonnet
    from System.Globalization import CultureInfo
    from System import Decimal as SystemDecimal
    _HAS_CLR = True
except Exception:
    clr = None
    CultureInfo = None
    SystemDecimal = None
    _HAS_CLR = False


try:
    from pipython import GCSDevice, GCSError
    _HAS_PI = True
except Exception:
    GCSDevice = None
    GCSError = Exception
    _HAS_PI = False



# =============================================================================
# COBOLT LASER CONTROLLERS
# =============================================================================

class _CoboltLaserController:
    """
    Minimal Cobolt serial controller (ASCII protocol).
    Used only for power read/write in DeepLight.
    Emission ON/OFF remains controlled by the physical key.
    """

    def __init__(self, port: str, baudrate: int = 115200):
        self.port = str(port)
        self.baudrate = int(baudrate)
        self.serial = None
        self.connected = False

    def connect(self):
        if self.connected and self.serial is not None:
            return

        self.serial = serial.Serial(
            self.port,
            self.baudrate,
            timeout=1,
            write_timeout=1,
        )
        self.connected = True
        logger.info(f"[Cobolt] Connected on {self.port} @ {self.baudrate}")

    def close(self):
        try:
            if self.serial is not None:
                self.serial.close()
        finally:
            self.serial = None
            self.connected = False

    def _ensure_connected(self):
        if not self.connected or self.serial is None:
            self.connect()

    def _write(self, cmd: str):
        self._ensure_connected()
        payload = (str(cmd).strip() + "\r").encode("ascii")
        self.serial.reset_input_buffer()
        self.serial.write(payload)
        self.serial.flush()

    def _query(self, cmd: str) -> str:
        self._write(cmd)
        return self.serial.readline().decode("ascii", errors="replace").strip()

    def get_serial_number(self) -> str:
        return self._query("sn?")

    def set_power_mw(self, power_mw: float):
        power_mw = max(0.0, float(power_mw))
        power_w = power_mw / 1000.0
        self._write(f"p {power_w:.5f}")

    def get_power_setpoint_w(self) -> float:
        ans = self._query("p?")
        return float(ans)

    def get_output_power_w(self) -> float:
        ans = self._query("pa?")
        return float(ans)

    def get_laser_on_state(self) -> bool:
        ans = self._query("l?")
        return bool(int(ans))

class _ElliptecELL14Controller:
    """
    Minimal Thorlabs ELL14 / ELLC serial controller.

    Used here as a half-wave plate rotator for Cobolt power control.

    Protocol used:
    - ASCII serial
    - address usually "0"
    - gp          : get position
    - maXXXXXXXX  : move absolute, signed 32-bit hex counts
    """

    COUNTS_PER_REV = COBOLT_ELL14_COUNTS_PER_REV
    COUNTS_PER_DEGREE = COUNTS_PER_REV / 360.0

    def __init__(
        self,
        port: str,
        address: str = "0",
        baudrate: int = 9600,
        timeout_s: float = 1.0,
    ):
        self.port = str(port)
        self.address = str(address)
        self.baudrate = int(baudrate)
        self.timeout_s = float(timeout_s)

        self.serial = None
        self.connected = False
        self._io_lock = Lock()
        self._last_angle_deg = 0.0

    def connect(self):
        if self.connected and self.serial is not None:
            return

        self.serial = serial.Serial(
            self.port,
            baudrate=self.baudrate,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
            timeout=self.timeout_s,
            write_timeout=self.timeout_s,
        )

        try:
            self.serial.reset_input_buffer()
            self.serial.reset_output_buffer()
        except Exception:
            pass

        self.connected = True

        try:
            pos = self.get_angle_deg()
            logger.info(f"[ELL14] Connected on {self.port} addr={self.address} angle={pos:.3f} deg")
        except Exception as e:
            logger.warning(f"[ELL14] Connected on {self.port}, but initial position read failed: {e}")

    def close(self):
        try:
            if self.serial is not None:
                self.serial.close()
        finally:
            self.serial = None
            self.connected = False

    def _ensure_connected(self):
        if not self.connected or self.serial is None:
            self.connect()

    def _write(self, payload: str):
        self._ensure_connected()
        msg = f"{self.address}{payload}\r".encode("ascii")

        with self._io_lock:
            try:
                self.serial.reset_input_buffer()
            except Exception:
                pass

            self.serial.write(msg)
            self.serial.flush()

    def _read_available(self, timeout_s: float = 2.0) -> str:
        self._ensure_connected()

        chunks = []
        t0 = time.time()

        with self._io_lock:
            while time.time() - t0 < float(timeout_s):
                n = self.serial.in_waiting
                if n:
                    chunks.append(self.serial.read(n))
                    time.sleep(0.02)
                    continue

                # petit délai pour laisser arriver la réponse
                time.sleep(0.02)

                if chunks and self.serial.in_waiting <= 0:
                    break

        raw = b"".join(chunks)
        return raw.decode("ascii", errors="replace").strip()

    def command(self, payload: str, timeout_s: float = 2.0) -> str:
        self._write(payload)
        return self._read_available(timeout_s=timeout_s)

    @classmethod
    def deg_to_counts(cls, deg: float) -> int:
        return int(round(float(deg) * cls.COUNTS_PER_DEGREE))

    @classmethod
    def counts_to_deg(cls, counts: int) -> float:
        return float(counts) / cls.COUNTS_PER_DEGREE

    @staticmethod
    def counts_to_hex32(counts: int) -> str:
        return f"{int(counts) & 0xFFFFFFFF:08X}"

    @staticmethod
    def hex32_to_counts(hex_value: str) -> int:
        value = int(str(hex_value), 16)
        if value & 0x80000000:
            value -= 0x100000000
        return int(value)

    @staticmethod
    def _power_percent_to_absolute_angle_deg(percent: float, offset_deg: float) -> float:
        """
        Same convention as Mira/Tumecs HWP mapping:
        - 0%   -> offset_deg
        - 100% -> offset_deg + 45 deg

        0..100% uses inverse Malus law:
            P = sin²(2 theta)
            theta = 0.5 asin(sqrt(P))
        """
        percent = float(percent)
        offset_deg = float(offset_deg)

        if 0.0 <= percent <= 100.0:
            p = percent / 100.0
            theta_rel_rad = 0.5 * math.asin(math.sqrt(p))
            theta_rel_deg = math.degrees(theta_rel_rad)
        elif percent < 0.0:
            theta_rel_deg = (percent / 100.0) * MIRA_ROTATOR_REL_MAX_DEG
        else:
            theta_rel_deg = MIRA_ROTATOR_REL_MAX_DEG + (
                (percent - 100.0) / 100.0
            ) * MIRA_ROTATOR_REL_MAX_DEG

        return offset_deg + theta_rel_deg

    @staticmethod
    def _absolute_angle_deg_to_power_percent(angle_deg: float, offset_deg: float) -> float:
        theta_rel_deg = float(angle_deg) - float(offset_deg)
        sign = -1.0 if theta_rel_deg < 0.0 else 1.0
        mag_deg = abs(theta_rel_deg)

        if mag_deg <= MIRA_ROTATOR_REL_MAX_DEG:
            theta_rel_rad = math.radians(mag_deg)
            p = math.sin(2.0 * theta_rel_rad) ** 2
            return sign * (100.0 * p)

        extra = 100.0 + 100.0 * (mag_deg - MIRA_ROTATOR_REL_MAX_DEG) / MIRA_ROTATOR_REL_MAX_DEG
        return sign * extra

    def get_angle_deg(self) -> float:
        response = self.command("gp", timeout_s=1.0)

        idx = response.find("PO")
        if idx < 0 or len(response) < idx + 10:
            raise RuntimeError(f"Unable to parse ELL14 position from response: {response!r}")

        hex_pos = response[idx + 2: idx + 10]
        counts = self.hex32_to_counts(hex_pos)
        self._last_angle_deg = self.counts_to_deg(counts)
        return float(self._last_angle_deg)

    def move_to_angle_deg(self, angle_deg: float, blocking: bool = True):
        self._ensure_connected()

        angle_deg = float(angle_deg)
        counts = self.deg_to_counts(angle_deg)
        hex_counts = self.counts_to_hex32(counts)

        self._last_angle_deg = angle_deg

        if blocking:
            time.sleep(0.2)
            try:
                self.get_angle_deg()
            except Exception:
                pass

    def set_power_percent(self, percent: float, offset_deg: float):
        angle = self._power_percent_to_absolute_angle_deg(
            percent=float(percent),
            offset_deg=float(offset_deg),
        )
        self.move_to_angle_deg(angle, blocking=True)

    def get_power_percent(self, offset_deg: float = 0.0) -> float:
        try:
            angle = self.get_angle_deg()
        except Exception:
            angle = self._last_angle_deg

        return self._absolute_angle_deg_to_power_percent(
            angle_deg=float(angle),
            offset_deg=float(offset_deg),
        )

# =============================================================================
# SPARK ALCOR LASER CONTROLLER
# =============================================================================

class _SparkAlcorSerialController:
    """
    Spark Lasers ALCOR serial controller.

    Protocol:
    - 115200 bauds, 8N1
    - binary packets
    - MODBUS CRC16, polynomial 0xA001, init 0xFFFF
    """

    MAGIC = 0x53

    CMD_PING = 0x00000000
    CMD_ERRORS = 0x00000001
    CMD_CLEAR_ERRORS = 0x80000003
    CMD_LASER_STATUS = 0x00000004
    CMD_SET_LASER_STATUS = 0x80000004
    CMD_OUTPUT_LEVEL_PERCENT = 0x0000000E
    CMD_SET_OUTPUT_LEVEL_PERCENT = 0x8000000E

    # Confirmés par sniff série COM14:
    CMD_GDD_FS2 = 0x0000001B
    CMD_SET_GDD_FS2 = 0x8000001B
    CMD_REP_RATE_HZ = 0x0000000B
    CMD_SET_REP_RATE_HZ = 0x8000000B

    def __init__(self, port: str, baudrate: int = 115200, timeout_s: float = 0.7):
        self.port = str(port)
        self.baudrate = int(baudrate)
        self.timeout_s = float(timeout_s)

        self.serial = None
        self.connected = False
        self._request_id = 0
        self._io_lock = Lock()

    def connect(self):
        if self.connected and self.serial is not None:
            return

        self.serial = serial.Serial(
            self.port,
            self.baudrate,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
            timeout=self.timeout_s,
            write_timeout=self.timeout_s,
        )

        self.connected = True

        try:
            self.ping()
            logger.info(f"[SparkAlcor] Connected on {self.port} @ {self.baudrate}")
        except Exception:
            self.close()
            raise

    def close(self):
        try:
            if self.serial is not None:
                self.serial.close()
        finally:
            self.serial = None
            self.connected = False

    def _ensure_connected(self):
        if not self.connected or self.serial is None:
            self.connect()

    @staticmethod
    def _crc16_modbus(data: bytes, crc: int = 0xFFFF) -> int:
        crc = int(crc) & 0xFFFF

        for byte in bytes(data):
            crc ^= int(byte)

            for _ in range(8):
                if crc & 0x0001:
                    crc = (crc >> 1) ^ 0xA001
                else:
                    crc >>= 1

                crc &= 0xFFFF

        return crc & 0xFFFF

    def _next_request_id(self) -> int:
        self._request_id = (int(self._request_id) + 1) & 0xFF
        return int(self._request_id)

    def _build_packet(self, command_id: int, data: bytes = b"") -> bytes:
        data = bytes(data or b"")

        request_id = self._next_request_id()

        header = bytearray(10)
        header[0] = self.MAGIC
        header[1] = request_id
        header[2:6] = struct.pack("<I", int(command_id) & 0xFFFFFFFF)
        header[6:8] = struct.pack("<H", len(data))
        header_crc = self._crc16_modbus(header[:8])
        header[8:10] = struct.pack("<H", header_crc)

        packet = bytes(header)

        if data:
            data_crc = self._crc16_modbus(data)
            packet += data + struct.pack("<H", data_crc)

        return packet

    def _transceive(self, command_id: int, data: bytes = b"") -> bytes:
        self._ensure_connected()

        packet = self._build_packet(command_id, data=data)
        request_id = packet[1]

        with self._io_lock:
            try:
                self.serial.reset_input_buffer()
            except Exception:
                pass

            self.serial.write(packet)
            self.serial.flush()

            response_header = self.serial.read(7)

        if len(response_header) != 7:
            raise TimeoutError(
                f"Spark ALCOR timeout: expected 7 response header bytes, "
                f"got {len(response_header)}"
            )

        if response_header[0] != self.MAGIC:
            raise RuntimeError(
                f"Spark ALCOR invalid response magic: 0x{response_header[0]:02X}"
            )

        if response_header[1] != request_id:
            raise RuntimeError(
                f"Spark ALCOR response request_id mismatch: "
                f"sent={request_id}, got={response_header[1]}"
            )

        protocol_error = int(response_header[2])
        data_len = struct.unpack("<H", response_header[3:5])[0]
        received_header_crc = struct.unpack("<H", response_header[5:7])[0]
        expected_header_crc = self._crc16_modbus(response_header[:5])

        if received_header_crc != expected_header_crc:
            raise RuntimeError(
                f"Spark ALCOR header CRC mismatch: "
                f"got=0x{received_header_crc:04X}, expected=0x{expected_header_crc:04X}"
            )

        if protocol_error != 0:
            raise RuntimeError(f"Spark ALCOR protocol error: 0x{protocol_error:02X}")

        if data_len <= 0:
            return b""

        with self._io_lock:
            body = self.serial.read(int(data_len) + 2)

        if len(body) != int(data_len) + 2:
            raise TimeoutError(
                f"Spark ALCOR timeout while reading response data: "
                f"expected {int(data_len) + 2}, got {len(body)}"
            )

        response_data = body[:data_len]
        received_data_crc = struct.unpack("<H", body[data_len:data_len + 2])[0]
        expected_data_crc = self._crc16_modbus(response_data)

        if received_data_crc != expected_data_crc:
            raise RuntimeError(
                f"Spark ALCOR data CRC mismatch: "
                f"got=0x{received_data_crc:04X}, expected=0x{expected_data_crc:04X}"
            )

        return response_data

    def ping(self):
        self._transceive(self.CMD_PING)

    def clear_errors(self):
        self._transceive(self.CMD_CLEAR_ERRORS)

    def get_errors(self) -> int:
        data = self._transceive(self.CMD_ERRORS)
        if len(data) < 8:
            return 0
        lo, hi = struct.unpack("<II", data[:8])
        return int(lo) | (int(hi) << 32)

    def set_enabled(self, enabled: bool):
        payload = bytes([1 if bool(enabled) else 0])
        self._transceive(self.CMD_SET_LASER_STATUS, payload)

    def get_enabled(self) -> bool:
        data = self._transceive(self.CMD_LASER_STATUS)
        if not data:
            return False
        return bool(int(data[0]))

    def set_power_percent(self, percent: float):
        percent = max(0.0, min(100.0, float(percent)))

        payload = struct.pack("<f", float(percent))
        self._transceive(self.CMD_SET_OUTPUT_LEVEL_PERCENT, payload)

    def get_power_percent(self) -> float:
        data = self._transceive(self.CMD_OUTPUT_LEVEL_PERCENT)
        if len(data) < 4:
            return 0.0
        return float(struct.unpack("<f", data[:4])[0])

    def set_gdd_fs2(self, gdd_fs2: float):
        gdd_fs2 = max(-60000.0, min(0.0, float(gdd_fs2)))

        payload = struct.pack("<f", float(gdd_fs2))
        self._transceive(self.CMD_SET_GDD_FS2, payload)

    def get_gdd_fs2(self) -> float:
        data = self._transceive(self.CMD_GDD_FS2)
        if len(data) < 4:
            return 0.0
        return float(struct.unpack("<f", data[:4])[0])

    def set_rep_rate_khz(self, rep_rate_khz: float):
        rep_rate_khz = max(
            SPARK_ALCOR_REP_RATE_MIN_KHZ,
            min(SPARK_ALCOR_REP_RATE_MAX_KHZ, float(rep_rate_khz)),
        )

        # Le widget/manager parlent en kHz.
        # Le laser Spark attend un payload float32 en Hz.
        rep_rate_hz = rep_rate_khz * 1000.0

        payload = struct.pack("<f", float(rep_rate_hz))
        self._transceive(self.CMD_SET_REP_RATE_HZ, payload)

    def get_rep_rate_khz(self) -> float:
        data = self._transceive(self.CMD_REP_RATE_HZ)
        if len(data) < 4:
            return SPARK_ALCOR_BASE_REP_RATE_KHZ

        rep_rate_hz = float(struct.unpack("<f", data[:4])[0])
        return rep_rate_hz / 1000.0

# =============================================================================
# LASER MANAGER
# =============================================================================

class LaserHardware(QObject):
    """
    Unified hardware-level controller for all laser-like devices.
    V2 scope:
    - Cobolt via serial
    - placeholders for Alcor later

    Renamed from LaserManager to avoid name collision with
    gui.managers.Laser_Manager.LaserManager (the threaded command queue).
    """

    def __init__(self, backend_name: str = "mock", parent=None):
        super().__init__(parent)

        self.backend_name = (backend_name or "mock").lower()

        self._cobolt = None
        self._alcor = None
        self._cobolt_hwp = None

        if self.backend_name == "nidaq":
            self._cobolt = _CoboltLaserController(
                port=COBOLT_FLAMENCO_PORT,
                baudrate=COBOLT_FLAMENCO_BAUDRATE,
            )

            try:
                self._cobolt.connect()
                sn = self._cobolt.get_serial_number()
                logger.info(f"[Cobolt] Serial number: {sn}")
            except Exception as e:
                logger.error(f"[Cobolt] Connection failed: {e}")

            self._cobolt_hwp = _ElliptecELL14Controller(
                port=COBOLT_ELL14_PORT,
                address=COBOLT_ELL14_ADDRESS,
                baudrate=COBOLT_ELL14_BAUDRATE,
                timeout_s=COBOLT_ELL14_TIMEOUT_S,
            )

            try:
                self._cobolt_hwp.connect()
            except Exception as e:
                logger.error(f"[ELL14] Cobolt HWP connection failed: {e}")
            
            self._alcor = _SparkAlcorSerialController(
                port=SPARK_ALCOR_PORT,
                baudrate=SPARK_ALCOR_BAUDRATE,
                timeout_s=SPARK_ALCOR_TIMEOUT_S,
            )

            try:
                self._alcor.connect()
                self._alcor.set_power_percent(0.0)
                enabled = self._alcor.get_enabled()
                logger.info(f"[SparkAlcor] Initialized power=0.0% enabled={enabled}")
            except Exception as e:
                logger.error(f"[SparkAlcor] Connection failed: {e}")

    def get_power_percent(self, laser_name: str) -> float:
        if self.backend_name != "nidaq":
            return 0.0

        if laser_name == "Cobolt 660":
            if self._cobolt_hwp is None:
                return 0.0

            try:
                return self._cobolt_hwp.get_power_percent(offset_deg=0.0)
            except Exception as e:
                logger.warning(f"[LaserHardware] Cobolt HWP get_power_percent failed: {e}")
                return 0.0

        if laser_name == "Alcor 920":
            if self._alcor is None:
                return 0.0
            return self._alcor.get_power_percent()

        return 0.0

    def get_output_power_mw(self, laser_name: str) -> float:
        if self.backend_name != "nidaq":
            return 0.0

        if laser_name == "Cobolt 660":
            if self._cobolt is None:
                return 0.0
            return 1000.0 * self._cobolt.get_output_power_w()

        return 0.0
    
    def _is_real_backend(self) -> bool:
        return self.backend_name == "nidaq"

    def get_enabled(self, laser_name: str) -> bool:
        if self.backend_name != "nidaq":
            return False

        laser_name = str(laser_name)

        if laser_name == "Cobolt 660":
            if self._cobolt is None:
                return False
            try:
                return bool(self._cobolt.get_laser_on_state())
            except Exception as e:
                logger.warning(f"[LaserHardware] Cobolt get_enabled failed: {e}")
                return False
            
        if laser_name == "Alcor 920":
            if self._alcor is None:
                return False
            try:
                return bool(self._alcor.get_enabled())
            except Exception as e:
                logger.warning(f"[LaserHardware] SparkAlcor get_enabled failed: {e}")
                return False

        return False
    
    def set_power_percent(self, laser_name: str, percent: float):
        if not self._is_real_backend():
            logger.debug(f"[LaserHardware] mock set_power_percent laser={laser_name} percent={percent}")
            return

        laser_name = str(laser_name)
        percent = max(0.0, min(100.0, float(percent)))

        if laser_name == "Cobolt 660":
            raise RuntimeError(
                "Direct Cobolt power setpoint is disabled. "
                "Use HardwareManager.set_laser_power_percent() to drive the ELL14 HWP."
            )

        if laser_name == "Alcor 920":
            if self._alcor is None:
                raise RuntimeError("Spark ALCOR controller is not initialized.")

            self._alcor.set_power_percent(percent)
            return

    def set_gdd_fs2(self, laser_name: str, gdd_fs2: float):
        if not self._is_real_backend():
            logger.debug(f"[LaserHardware] mock set_gdd_fs2 laser={laser_name} gdd_fs2={gdd_fs2}")
            return

        laser_name = str(laser_name)
        if laser_name != "Alcor 920":
            raise RuntimeError(f"GDD control is only implemented for Alcor 920, got {laser_name!r}")

        if self._alcor is None:
            raise RuntimeError("Spark ALCOR controller is not initialized.")

        self._alcor.set_gdd_fs2(float(gdd_fs2))


    def get_gdd_fs2(self, laser_name: str) -> float:
        if not self._is_real_backend():
            return 0.0

        if str(laser_name) != "Alcor 920" or self._alcor is None:
            return 0.0

        return float(self._alcor.get_gdd_fs2())


    def set_rep_rate_khz(self, laser_name: str, rep_rate_khz: float):
        if not self._is_real_backend():
            logger.debug(f"[LaserHardware] mock set_rep_rate_khz laser={laser_name} rep_rate_khz={rep_rate_khz}")
            return

        laser_name = str(laser_name)
        if laser_name != "Alcor 920":
            raise RuntimeError(f"Rep-rate control is only implemented for Alcor 920, got {laser_name!r}")

        if self._alcor is None:
            raise RuntimeError("Spark ALCOR controller is not initialized.")

        self._alcor.set_rep_rate_khz(float(rep_rate_khz))


    def get_rep_rate_khz(self, laser_name: str) -> float:
        if not self._is_real_backend():
            return SPARK_ALCOR_BASE_REP_RATE_KHZ

        if str(laser_name) != "Alcor 920" or self._alcor is None:
            return SPARK_ALCOR_BASE_REP_RATE_KHZ

        return float(self._alcor.get_rep_rate_khz())

    def close(self):
        try:
            if self._cobolt is not None:
                self._cobolt.close()
        except Exception as e:
            logger.debug(f"[LaserHardware.close] cobolt.close failed: {e}")

        if self._cobolt_hwp is not None:
            self._cobolt_hwp.close()

        try:
            if self._alcor is not None:
                self._alcor.close()
        except Exception as e:
            logger.debug(f"[LaserHardware.close] alcor.close failed: {e}")

    def set_enabled(self, laser_name: str, enabled: bool):
        if not self._is_real_backend():
            logger.debug(f"[LaserHardware] mock set_enabled laser={laser_name} enabled={enabled}")
            return

        laser_name = str(laser_name)

        if laser_name == "Alcor 920":
            if self._alcor is None:
                raise RuntimeError("Spark ALCOR controller is not initialized.")

            self._alcor.set_enabled(bool(enabled))
            return

        logger.warning(f"[LaserHardware] set_enabled not implemented for {laser_name!r}")

# =============================================================================
# THORLABS KINESIS LOADER
# =============================================================================

class _KinesisLoader:
    _loaded = False

    DeviceManagerCLI = None
    DeviceConfiguration = None
    KCubeSolenoid = None
    SolenoidStatus = None
    KCubeDCServo = None
    KCubeStepperMotor = None

    @classmethod
    def ensure_loaded(cls):
        if cls._loaded:
            return

        if not _HAS_CLR:
            raise RuntimeError("pythonnet / clr is not installed. Install pythonnet to use Thorlabs Kinesis devices.")

        dll_path = THORLABS_KINESIS_PATH
        if not os.path.isdir(dll_path):
            raise RuntimeError(
                f"Thorlabs Kinesis path not found: {dll_path!r}. "
                "Install Kinesis or update THORLABS_KINESIS_PATH."
            )

        clr.AddReference(os.path.join(dll_path, "Thorlabs.MotionControl.DeviceManagerCLI.dll"))
        clr.AddReference(os.path.join(dll_path, "Thorlabs.MotionControl.GenericMotorCLI.dll"))
        clr.AddReference(os.path.join(dll_path, "Thorlabs.MotionControl.KCube.SolenoidCLI.dll"))
        clr.AddReference(os.path.join(dll_path, "Thorlabs.MotionControl.KCube.DCServoCLI.dll"))

        from Thorlabs.MotionControl.DeviceManagerCLI import DeviceManagerCLI, DeviceConfiguration
        from Thorlabs.MotionControl.KCube.SolenoidCLI import KCubeSolenoid, SolenoidStatus

        cls.DeviceManagerCLI = DeviceManagerCLI
        cls.DeviceConfiguration = DeviceConfiguration
        cls.KCubeSolenoid = KCubeSolenoid
        cls.SolenoidStatus = SolenoidStatus

        try:
            from Thorlabs.MotionControl.KCube.DCServoCLI import KCubeDCServo
            cls.KCubeDCServo = KCubeDCServo
        except Exception:
            cls.KCubeDCServo = None

        try:
            from Thorlabs.MotionControl.KCube.StepperMotorCLI import KCubeStepperMotor
            cls.KCubeStepperMotor = KCubeStepperMotor
        except Exception:
            cls.KCubeStepperMotor = None

        cls.DeviceManagerCLI.BuildDeviceList()
        cls._loaded = True


# =============================================================================
# LOW-LEVEL CONTROLLERS
# =============================================================================

class _ThorlabsShutterController:
    def __init__(self, serial: str):
        self.serial = str(serial)
        self.device = None
        self.connected = False

    def connect(self):
        _KinesisLoader.ensure_loaded()

        self.device = _KinesisLoader.KCubeSolenoid.CreateKCubeSolenoid(self.serial)
        self.device.Connect(self.serial)

        if not self.device.IsSettingsInitialized():
            self.device.WaitForSettingsInitialized(10000)

        self.device.StartPolling(250)
        self.device.EnableDevice()
        self.device.SetOperatingMode(_KinesisLoader.SolenoidStatus.OperatingModes.Manual)

        self.connected = True

    def set_open(self, open_: bool):
        if not self.connected:
            raise RuntimeError("Shutter is not connected.")

        state = (
            _KinesisLoader.SolenoidStatus.OperatingStates.Active
            if bool(open_)
            else _KinesisLoader.SolenoidStatus.OperatingStates.Inactive
        )

        self.device.SetOperatingState(state)

    def close(self):
        try:
            if self.device is not None:
                self.device.StopPolling()
                self.device.Disconnect()
        finally:
            self.device = None
            self.connected = False


class _PIVoiceCoilController:
    def __init__(self, serial: str):
        self.serial = str(serial)
        self.device = None
        self.axis = PI_Z_AXIS_ID
        self.connected = False

    def connect(self):
        if not _HAS_PI:
            raise RuntimeError("pipython is not installed. Install pipython to use the PI V-308.")

        self.device = GCSDevice()
        self.device.ConnectUSB(serialnum=self.serial)

        if not self.device.IsConnected():
            raise RuntimeError(f"Failed to connect to PI device {self.serial!r}.")

        try:
            self.device.SVO(self.axis, 1)
        except Exception:
            pass

        try:
            self.device.VEL(self.axis, float(PI_Z_DEFAULT_VEL_MM_S))
        except Exception:
            pass

        self.connected = True

    def get_abs_um(self) -> float:
        if not self.connected:
            self.connect()

        pos = self.device.qPOS(self.axis)
        if isinstance(pos, dict):
            value_mm = float(pos[self.axis])
        else:
            value_mm = float(pos)

        return value_mm * 1000.0

    def is_moving(self) -> bool:
        if not self.connected or self.device is None:
            return False
        try:
            return bool(self.device.IsMoving(self.axis))
        except Exception:
            return False
    
    def get_travel_range_um(self) -> tuple[float, float]:
        """
        Best effort:
        - qTMN/qTMX are the most common PI travel-limit queries
        - fallback to qTMN()/qTMX() without axis if controller API differs
        """
        if not self.connected:
            self.connect()

        err = None

        for getter_min_name, getter_max_name in (
            ("qTMN", "qTMX"),
            ("TMN", "TMX"),
        ):
            try:
                getter_min = getattr(self.device, getter_min_name)
                getter_max = getattr(self.device, getter_max_name)

                try:
                    mn = getter_min(self.axis)
                    mx = getter_max(self.axis)
                except TypeError:
                    mn = getter_min()
                    mx = getter_max()

                if isinstance(mn, dict):
                    mn = mn[self.axis]
                if isinstance(mx, dict):
                    mx = mx[self.axis]

                return float(mn) * 1000.0, float(mx) * 1000.0
            except Exception as e:
                err = e

        raise RuntimeError(f"Unable to query PI travel range from device: {err}")
    
    def move_abs_um(self, target_um: float, speed_mm_s: Optional[float] = None, blocking: bool = True):
        if not self.connected:
            self.connect()

        if speed_mm_s is not None:
            try:
                self.device.VEL(self.axis, float(speed_mm_s))
            except Exception as e:
                logger.warning(f"[PIVoiceCoil] VEL failed axis={self.axis} speed={speed_mm_s}: {e}")

        target_mm = float(target_um) / 1000.0

        try:
            cur = self.device.qPOS(self.axis)
            cur_mm = float(cur[self.axis]) if isinstance(cur, dict) else float(cur)
        except Exception:
            cur_mm = None

        logger.debug(
            f"[PIVoiceCoil] MOV axis={self.axis} "
            f"cur_mm={cur_mm} target_mm={target_mm} speed_mm_s={speed_mm_s} "
            f"blocking={blocking}"
        )

        self.device.MOV(self.axis, target_mm)

        if blocking:
            self.wait_until_stopped(target_mm)

    def move_rel_um(self, delta_um: float, speed_mm_s: Optional[float] = None, blocking: bool = True):
        cur_um = self.get_abs_um()
        self.move_abs_um(cur_um + float(delta_um), speed_mm_s=speed_mm_s, blocking=blocking)

    def stop(self):
        if self.connected and self.device is not None:
            try:
                self.device.STP()
            except Exception:
                pass

    def wait_until_xy_reached(self, x_rel_target: float, y_rel_target: float, timeout_s: float = 30.0):
        """
        Attend que la platine XY atteigne la cible relative demandée.
        Critère robuste:
        - lecture réelle de position
        - plusieurs lectures stables à la cible
        - timeout basé aussi sur la distance à parcourir
        """
        if self._xy is None:
            raise RuntimeError("XY controller is not available.")

        # position de départ réelle
        self._refresh_from_hardware("x", force_emit=False)
        self._refresh_from_hardware("y", force_emit=False)

        start_x = float(self.get_rel_pos("x"))
        start_y = float(self.get_rel_pos("y"))

        dx = float(x_rel_target) - start_x
        dy = float(y_rel_target) - start_y
        distance_um = max(abs(dx), abs(dy))

        # vitesse max plausible du move courant
        speed_mm_s = max(
            0.01,
            float(self.get_max_speed("x")),
            float(self.get_max_speed("y")),
        )
        speed_um_s = speed_mm_s * 1000.0

        # temps théorique + marge
        estimated_s = distance_um / speed_um_s if speed_um_s > 0 else 0.0
        effective_timeout_s = max(float(timeout_s), estimated_s + 3.0)

        tol_x = max(float(self.get_tolerance("x")), 0.1)
        tol_y = max(float(self.get_tolerance("y")), 0.1)

        # petit temps de grâce après émission commande
        time.sleep(0.05)

        t0 = time.time()
        stable_hits = 0
        required_stable_hits = 3

        while True:
            self._refresh_from_hardware("x", force_emit=False)
            self._refresh_from_hardware("y", force_emit=False)

            cur_x = float(self.get_rel_pos("x"))
            cur_y = float(self.get_rel_pos("y"))

            arrived = (
                abs(cur_x - float(x_rel_target)) <= tol_x
                and abs(cur_y - float(y_rel_target)) <= tol_y
            )

            if arrived:
                stable_hits += 1
            else:
                stable_hits = 0

            if stable_hits >= required_stable_hits:
                break

            if time.time() - t0 > effective_timeout_s:
                raise RuntimeError(
                    f"Timeout while waiting for XY target: "
                    f"target=({float(x_rel_target):.3f}, {float(y_rel_target):.3f}) "
                    f"current=({cur_x:.3f}, {cur_y:.3f})"
                )

            time.sleep(0.02)

    def close(self):
        try:
            if self.device is not None:
                self.device.CloseConnection()
        finally:
            self.device = None
            self.connected = False


class _ThorlabsRotationController:
    """
    Best-effort V1 adapter for the PRM1MZ8 + K-cube.
    Important:
    - the exact Kinesis class can differ depending on the controller family
    - change THORLABS_ROTATOR_CONTROLLER_KIND if needed
    - this V1 assumes MoveTo(real-world angle) is accepted by the controller once settings are initialized
    """
    def __init__(self, serial: str):
        self.serial = str(serial)
        self.device = None
        self.connected = False
        self._last_angle_deg = 0.0

    def connect(self):
        _KinesisLoader.ensure_loaded()

        if THORLABS_ROTATOR_CONTROLLER_KIND != "KCubeDCServo":
            raise RuntimeError(
                f"Unsupported controller kind for KDC101: {THORLABS_ROTATOR_CONTROLLER_KIND!r}"
            )

        device_cls = _KinesisLoader.KCubeDCServo
        if device_cls is None:
            raise RuntimeError(
                "KCubeDCServo class is unavailable. "
                "Check Kinesis installation and loaded DLLs."
            )

        self.device = device_cls.CreateKCubeDCServo(self.serial)
        self.device.Connect(self.serial)

        if not self.device.IsSettingsInitialized():
            self.device.WaitForSettingsInitialized(10000)

        use_device_settings = _KinesisLoader.DeviceConfiguration.DeviceSettingsUseOptionType.UseDeviceSettings
        self.device.LoadMotorConfiguration(self.serial, use_device_settings)

        self.device.StartPolling(250)
        time.sleep(0.25)

        self.device.EnableDevice()
        time.sleep(0.25)

        self.connected = True

    @staticmethod
    def _clamp_relative_angle_deg(angle_deg: float) -> float:
        return max(MIRA_ROTATOR_REL_MIN_DEG, min(MIRA_ROTATOR_REL_MAX_DEG, float(angle_deg)))

    def _power_percent_to_absolute_angle_deg(self, percent: float, offset_deg: float) -> float:
        """
        Mapping user power (%) -> HWP absolute angle.

        Requested convention:
        - 0%   -> offset_deg
        - 100% -> offset_deg + 45°

        Behaviour:
        - for 0..100%: Malus-law inverse using HWP convention
              P = sin²(2 * theta_rel)
              theta_rel = 0.5 * asin(sqrt(P))
        - for values <0 or >100: linear angular extrapolation
          to allow checking the calibrated 0 and max positions.
        """
        percent = float(percent)
        offset_deg = float(offset_deg)

        if 0.0 <= percent <= 100.0:
            p = percent / 100.0
            theta_rel_rad = 0.5 * math.asin(math.sqrt(p))
            theta_rel_deg = math.degrees(theta_rel_rad)
        elif percent < 0.0:
            # Linear angular extension below 0%
            theta_rel_deg = (percent / 100.0) * MIRA_ROTATOR_REL_MAX_DEG
        else:
            # Linear angular extension above 100%
            theta_rel_deg = MIRA_ROTATOR_REL_MAX_DEG + ((percent - 100.0) / 100.0) * MIRA_ROTATOR_REL_MAX_DEG

        return offset_deg + theta_rel_deg

    def _absolute_angle_deg_to_power_percent(self, angle_deg: float, offset_deg: float) -> float:
        """
        Absolute angle -> signed user power (%), relative to offset.

        angle > offset -> positive %
        angle < offset -> negative %
        """
        theta_rel_deg = float(angle_deg) - float(offset_deg)
        sign = -1.0 if theta_rel_deg < 0.0 else 1.0
        mag_deg = abs(theta_rel_deg)

        if mag_deg <= MIRA_ROTATOR_REL_MAX_DEG:
            theta_rel_rad = math.radians(mag_deg)
            p = math.sin(2.0 * theta_rel_rad) ** 2
            return sign * (100.0 * p)

        extra = 100.0 + 100.0 * (mag_deg - MIRA_ROTATOR_REL_MAX_DEG) / MIRA_ROTATOR_REL_MAX_DEG
        return sign * extra
    
    def move_to_angle_deg(self, angle_deg: float, speed: int, steps_per_degree: float, blocking: bool = True):
        if not self.connected:
            self.connect()

        angle_deg = float(angle_deg)
        self._last_angle_deg = angle_deg

        target = SystemDecimal.Parse(str(angle_deg), CultureInfo.InvariantCulture)
        self.device.MoveTo(target, 60000 if blocking else 0)

    def stop(self):
        if self.connected and self.device is not None:
            try:
                self.device.StopImmediate()
            except Exception:
                try:
                    self.device.StopPolling()
                    self.device.StartPolling(250)
                except Exception:
                    pass

    def get_angle_deg(self) -> float:
        return float(self._last_angle_deg)

    def set_power_percent(self, percent: float, speed: int, steps_per_degree: float, offset_deg: float):
        angle = self._power_percent_to_absolute_angle_deg(
            percent=float(percent),
            offset_deg=float(offset_deg),
        )

        self.move_to_angle_deg(
            angle,
            speed=int(speed),
            steps_per_degree=float(steps_per_degree),
            blocking=True,
        )

    def get_power_percent(self, offset_deg: float = 0.0) -> float:
        angle = self.get_angle_deg()
        return self._absolute_angle_deg_to_power_percent(
            angle_deg=angle,
            offset_deg=float(offset_deg),
        )

    def close(self):
        try:
            if self.device is not None:
                self.device.StopPolling()
                self.device.Disconnect()
        finally:
            self.device = None
            self.connected = False


# =============================================================================
# SCIENTIFICA XY STAGE CONTROLLER
# =============================================================================

class _ScientificaMotion8XYController:
    """
    Scientifica Motion 8 XY controller over the virtual serial port.

    Transport:
    - binary protocol over virtual serial port
    - COBS encoded frames, delimited by 0x00

    Public API units:
    - positions in µm
    - speeds in mm/s when applicable

    Motion 8 commands used:
    - Get Position      : 0x00 0x14 device
    - XY relative move  : 0x02 0x02 device x_distance(int32) y_distance(int32)
    - XY absolute move  : 0x02 0x03 device x_position(int32) y_position(int32)
    - Home in / out     : 0x02 0x05 / 0x02 0x06
    - Graceful stop     : 0x02 0x07
    - Is moving         : 0x02 0x0F device
    """

    _POS_SCALE_UM = 0.01
    _SPEED_SCALE_UM_S = 0.01

    def __init__(
        self,
        port: str,
        *,
        baudrate: int = 9600,
        timeout_s: float = 1.0,
        device_id: int = 0,
        x_axis_id: int = 0,
        y_axis_id: int = 1,
        scaling_x: float = SCIENTIFICA_STAGE_DEFAULT_SCALING_X,
        scaling_y: float = SCIENTIFICA_STAGE_DEFAULT_SCALING_Y,
    ):
        self.port = str(port)
        self.baudrate = int(baudrate)
        self.timeout_s = float(timeout_s)
        self.device_id = int(device_id) & 0xFF
        self.x_axis_id = int(x_axis_id) & 0xFF
        self.y_axis_id = int(y_axis_id) & 0xFF

        # UMS calibration factor. See SCIENTIFICA_STAGE_DEFAULT_SCALING.
        # Stored as instance attribute so it can be updated at runtime.
        self._scaling_x = float(scaling_x) if scaling_x and scaling_x > 0 else 1.0
        self._scaling_y = float(scaling_y) if scaling_y and scaling_y > 0 else 1.0

        self.serial = None
        self.connected = False
        self._io_lock = Lock()

    @property
    def scaling_x(self) -> float:
        return float(self._scaling_x)

    @property
    def scaling_y(self) -> float:
        return float(self._scaling_y)

    def set_scaling_factors(self, scaling_x: float, scaling_y: float):
        """
        Update the UMS scaling factors at runtime. Each must be > 0.
        Pass 1.0 to disable the scaling on a given axis.
        """
        sx = float(scaling_x)
        sy = float(scaling_y)
        if sx <= 0.0 or sy <= 0.0:
            logger.warning(
                f"[ScientificaXY] Ignoring invalid scaling factors "
                f"(x={sx}, y={sy})"
            )
            return
        self._scaling_x = sx
        self._scaling_y = sy
        logger.info(f"[ScientificaXY] scaling factors set: x={sx}, y={sy}")
    
    @staticmethod
    def _cobs_encode(data: bytes) -> bytes:
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

    @staticmethod
    def _cobs_decode(data: bytes) -> bytes:
        if not data:
            return b""

        out = bytearray()
        idx = 0
        n = len(data)

        while idx < n:
            code = data[idx]
            idx += 1

            if code == 0:
                raise ValueError("Invalid COBS frame: unexpected 0 byte inside payload")

            next_idx = idx + code - 1
            if next_idx > n and code != 1:
                raise ValueError("Invalid COBS frame: code exceeds payload length")

            out.extend(data[idx:min(next_idx, n)])
            idx = next_idx

            if code != 0xFF and idx < n:
                out.append(0)

        return bytes(out)

    def connect(self):
        if self.connected and self.serial is not None:
            return

        self.serial = serial.Serial(
            self.port,
            baudrate=self.baudrate,
            timeout=self.timeout_s,
            write_timeout=self.timeout_s,
        )

        try:
            self.serial.reset_input_buffer()
            self.serial.reset_output_buffer()
        except Exception:
            pass

        time.sleep(0.1)
        self.connected = True
        logger.info(f"[ScientificaXY] Connected on {self.port} @ {self.baudrate}")

    def _logical_to_hw_xy(self, x_um: float, y_um: float) -> tuple[float, float]:
        if SCIENTIFICA_STAGE_SWAP_XY:
            return float(y_um), float(x_um)
        return float(x_um), float(y_um)

    def _hw_to_logical_xy(self, x_um: float, y_um: float) -> tuple[float, float]:
        if SCIENTIFICA_STAGE_SWAP_XY:
            return float(y_um), float(x_um)
        return float(x_um), float(y_um)
        
    def close(self):
        try:
            if self.serial is not None:
                self.serial.close()
        finally:
            self.serial = None
            self.connected = False

    def _ensure_connected(self):
        if not self.connected or self.serial is None:
            self.connect()
        
    def _transceive(self, payload: bytes, *, expect_reply: bool = True) -> bytes:
        self._ensure_connected()
        frame = self._cobs_encode(payload)

        with self._io_lock:
            try:
                self.serial.reset_input_buffer()
            except Exception:
                pass

            self.serial.write(frame)
            self.serial.flush()

            if not expect_reply:
                return b""

            raw = self.serial.read_until(b"\x00")
            if not raw:
                raise TimeoutError("Scientifica stage did not reply")

            if raw.endswith(b"\x00"):
                raw = raw[:-1]

            decoded = self._cobs_decode(raw)
            if not decoded:
                raise RuntimeError("Scientifica stage returned an empty reply")

            status = decoded[0]
            if status != 0:
                raise RuntimeError(f"Scientifica command failed with status=0x{status:02X}")

            return decoded

    def _scaling_for_axis(self, axis_id: int) -> float:
        if int(axis_id) == self.x_axis_id:
            return self._scaling_x
        if int(axis_id) == self.y_axis_id:
            return self._scaling_y
        # axe non géré -> pas de scaling
        return 1.0

    def _um_to_units(self, value_um: float, axis_id: int) -> int:
        # logical_um (DeepLight, real physical) -> firmware command counts
        # firmware_um = logical_um / scaling  (because real = firmware * scaling)
        scaling = self._scaling_for_axis(axis_id)
        firmware_um = float(value_um) * scaling
        return int(round(firmware_um / self._POS_SCALE_UM))

    def _units_to_um(self, value_units: int, axis_id: int) -> float:
        # firmware counts -> logical_um (DeepLight, real physical)
        scaling = self._scaling_for_axis(axis_id)
        firmware_um = float(value_units) * self._POS_SCALE_UM
        return firmware_um / scaling

    def _mm_s_to_units(self, value_mm_s: float, axis_id: int) -> int:
        scaling = self._scaling_for_axis(axis_id)
        um_s = max(0.0, float(value_mm_s) * 1000.0) * scaling
        return int(round(um_s / self._SPEED_SCALE_UM_S))
    
    @staticmethod
    def _pack_float64(value: float) -> bytes:
        return struct.pack("<d", float(value))
    
    def _mm_s_to_profile_speed_units(self, value_mm_s: float) -> float:
        # Profil global (pas per-axis). On prend le scaling max pour ne pas
        # sous-vitesser un axe : si scaling_x=0.639 et scaling_y=1.0, on
        # divise par max=1.0 -> X aura une vitesse réelle plus faible que
        # demandée, mais aucun axe ne dépasse la consigne logique.
        scaling = max(self._scaling_x, self._scaling_y)
        um_s = max(0.0, float(value_mm_s) * 1000.0) * scaling
        return um_s / self._SPEED_SCALE_UM_S

    def _mm_s2_to_profile_accel_units(self, value_mm_s2: float) -> float:
        scaling = max(self._scaling_x, self._scaling_y)
        um_s2 = max(0.0, float(value_mm_s2) * 1000.0) * scaling
        return um_s2 / self._SPEED_SCALE_UM_S
    
    def set_profile_top_speed(self, profile_index: int, speed_mm_s: float):
        """
        Motion 8:
        0x03 0x05 device profile top_speed(float64)
        """
        payload = (
            struct.pack(
                "<BBBBB",
                0xAA,
                0x03,
                0x05,
                self.device_id,
                int(profile_index) & 0xFF,
            )
            + self._pack_float64(self._mm_s_to_profile_speed_units(speed_mm_s))
        )
        self._transceive(payload)
    
    def set_profile_acceleration(self, profile_index: int, accel_mm_s2: float):
        """
        Motion 8:
        0x03 0x06 device profile acceleration(float64)
        """
        payload = (
            struct.pack(
                "<BBBBB",
                0xAA,
                0x03,
                0x06,
                self.device_id,
                int(profile_index) & 0xFF,
            )
            + self._pack_float64(self._mm_s2_to_profile_accel_units(accel_mm_s2))
        )
        self._transceive(payload)

    def get_profile_axis_assignment(self, profile_index: int) -> int:
        """
        Motion 8:
        0x03 0x11 device profile -> returns axis assignment for the profile
        """
        payload = struct.pack(
            "<BBBBB",
            0xAA,
            0x03,
            0x11,
            self.device_id,
            int(profile_index) & 0xFF,
        )
        reply = self._transceive(payload)

        # Expected:
        # status, echo, 0x03, 0x11, device, profile, axis_assignments
        if len(reply) < 7:
            raise RuntimeError(
                f"Scientifica get_profile_axis_assignment reply too short: {len(reply)} bytes"
            )

        return int(reply[6])

    def set_profile_axis_assignment(self, profile_index: int, axis_assignments: int):
        """
        Motion 8:
        0x03 0x11 device profile axis_assignments
        """
        payload = struct.pack(
            "<BBBBBB",
            0xAA,
            0x03,
            0x11,
            self.device_id,
            int(profile_index) & 0xFF,
            int(axis_assignments) & 0xFF,
        )
        self._transceive(payload)


    def get_profile_settings(self, profile_index: int) -> dict:
        """
        Motion 8 combined profile settings.

        Expected reply layout:
        status, echo, 0x03, 0x16, device, profile, axis_assignments,
        top_speed(float64), acceleration(float64)
        """
        payload = struct.pack(
            "<BBBBB",
            0xAA,
            0x03,
            0x16,
            self.device_id,
            int(profile_index) & 0xFF,
        )
        reply = self._transceive(payload)

        if len(reply) < 23:
            raise RuntimeError(
                f"Scientifica get_profile_settings reply too short: {len(reply)} bytes"
            )

        axis_assignments = int(reply[6])
        top_speed = struct.unpack_from("<d", reply, 7)[0]
        acceleration = struct.unpack_from("<d", reply, 15)[0]

        return {
            "axis_assignments": axis_assignments,
            "top_speed_units": float(top_speed),
            "acceleration_units": float(acceleration),
        }
    
    def configure_xy_profile(self, speed_mm_s: float, accel_mm_s2: float, profile_index: int = 0):
        """
        Configure the Motion 8 point-to-point profile used by XY absolute/relative moves.
        This is the only supported speed-control path for the current Scientifica setup.
        """
        wanted_assign = int(SCIENTIFICA_STAGE_PROFILE_ASSIGN_XY)

        try:
            current_assign = self.get_profile_axis_assignment(profile_index)
            if current_assign != wanted_assign:
                logger.debug(
                    f"[ScientificaXY] profile {profile_index} axis assignment "
                    f"{current_assign:#04x} -> {wanted_assign:#04x}"
                )
                self.set_profile_axis_assignment(profile_index, wanted_assign)
        except Exception as e:
            logger.warning(f"[ScientificaXY] set_profile_axis_assignment warning: {e}")

        try:
            self.set_profile_top_speed(profile_index, speed_mm_s)
        except Exception as e:
            logger.warning(f"[ScientificaXY] set_profile_top_speed warning: {e}")

        try:
            self.set_profile_acceleration(profile_index, accel_mm_s2)
        except Exception as e:
            logger.warning(f"[ScientificaXY] set_profile_acceleration warning: {e}")

        try:
            s = self.get_profile_settings(profile_index)
            logger.debug(
                f"[ScientificaXY] profile {profile_index} configured: "
                f"axis_assign={s['axis_assignments']:#04x} "
                f"top_speed_units={s['top_speed_units']:.3f} "
                f"accel_units={s['acceleration_units']:.3f}"
            )
        except Exception as e:
            logger.warning(f"[ScientificaXY] get_profile_settings warning: {e}")

    def get_xy_abs_um(self) -> tuple[float, float]:
        reply = self._transceive(
            struct.pack(
                "<BBBB",
                0xAA,
                0x00,
                0x14,
                self.device_id,
            )
        )

        if len(reply) < 13:
            raise RuntimeError(f"Scientifica get position reply too short: {len(reply)} bytes")

        device = reply[4]
        if device != self.device_id:
            logger.warning(
                f"[ScientificaXY] Warning: position reply device={device}, "
                f"expected={self.device_id}"
            )

        x_units, y_units = struct.unpack_from("<ii", reply, 5)
        x_hw = self._units_to_um(x_units, self.x_axis_id)
        y_hw = self._units_to_um(y_units, self.y_axis_id)
        return self._hw_to_logical_xy(x_hw, y_hw)

    def is_moving(self) -> bool:
        reply = self._transceive(
            struct.pack(
                "<BBBB",
                0xAA,
                0x02,
                0x0F,
                self.device_id,
            )
        )

        # status, echo, 0x02, 0x0F, moving
        if len(reply) < 5:
            raise RuntimeError(f"Scientifica is_moving reply too short: {len(reply)} bytes")

        return bool(reply[4])

    def move_axis_rel_um(self, axis_id: int, delta_um: float):
        payload = struct.pack(
            "<BBBBBi",
            0xAA,
            0x02,
            0x02,
            self.device_id,
            int(axis_id) & 0xFF,
            self._um_to_units(delta_um, axis_id),
        )
        self._transceive(payload)

    def move_xy_rel_um(self, dx_um: float, dy_um: float):
        # Important: après _logical_to_hw_xy, dx_hw peut correspondre à l'axe Y
        # physique (et inversement) si SCIENTIFICA_STAGE_SWAP_XY=True. On résout
        # quel axis_id appliquer en regardant l'identité du swap.
        dx_hw, dy_hw = self._logical_to_hw_xy(dx_um, dy_um)
        if SCIENTIFICA_STAGE_SWAP_XY:
            ax_first, ax_second = self.y_axis_id, self.x_axis_id
        else:
            ax_first, ax_second = self.x_axis_id, self.y_axis_id

        payload = struct.pack(
            "<BBBBii",
            0xAA,
            0x02,
            0x02,
            self.device_id,
            self._um_to_units(dx_hw, ax_first),
            self._um_to_units(dy_hw, ax_second),
        )
        self._transceive(payload)

    def move_xy_abs_um(self, x_um: float, y_um: float):
        x_hw, y_hw = self._logical_to_hw_xy(x_um, y_um)
        if SCIENTIFICA_STAGE_SWAP_XY:
            ax_first, ax_second = self.y_axis_id, self.x_axis_id
        else:
            ax_first, ax_second = self.x_axis_id, self.y_axis_id

        payload = struct.pack(
            "<BBBBii",
            0xAA,
            0x02,
            0x03,
            self.device_id,
            self._um_to_units(x_hw, ax_first),
            self._um_to_units(y_hw, ax_second),
        )
        self._transceive(payload)

    def set_xy_velocity(self, speed_x_mm_s: float, speed_y_mm_s: float):
        # Intentionnellement no-op:
        # la vitesse/accélération XY restent pilotées par la config du contrôleur.
        return

    def stop(self, abrupt: bool = False):
        cmd = 0x08 if abrupt else 0x07
        payload = struct.pack(
            "<BBBB",
            0xAA,
            0x02,
            cmd,
            self.device_id,
        )
        self._transceive(payload)

    def home(self, mode: str = "in"):
        cmd = 0x05 if str(mode).lower() != "out" else 0x06
        payload = struct.pack(
            "<BBBB",
            0xAA,
            0x02,
            cmd,
            self.device_id,
        )
        self._transceive(payload)

# =============================================================================
# REAL POSITIONER MANAGER
# =============================================================================

class RealHardwarePositionerManager(PositionerManager):
    """
    Real V1 hardware positioner manager.
    Implemented:
    - z -> PI V-308 via USB
    - p -> Thorlabs rotation mount (degrees)
    Not implemented in this V1:
    - x / y stages, because no hardware/controller was provided for them
    """

    def __init__(
        self,
        axes: list[str],
        *,
        xy_controller: Optional[_ScientificaMotion8XYController] = None,
        z_controller: Optional[_PIVoiceCoilController] = None,
        p_controller: Optional[_ThorlabsRotationController] = None,
        parent=None,
        poll_ms: int = 150,
    ):
        super().__init__(axes, parent=parent)
        self._xy = xy_controller
        self._z = z_controller
        self._p = p_controller
        self._pending_targets_abs = {}
        self._pending_sample_xy_rel = {"x": None, "y": None}

        # Initialize abs positions from hardware when possible
        self._refresh_from_hardware("x")
        self._refresh_from_hardware("y")
        self._refresh_from_hardware("z")
        self._refresh_from_hardware("p")

        # polling pour refléter les moves externes (MikroMove)
        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(int(poll_ms))
        self._poll_timer.timeout.connect(self._poll_hardware_positions)
        self._poll_timer.start()

    def _log(self, msg: str):
        logger.debug(f"[RealHardwarePositioner] {msg}")

    def _refresh_from_hardware(self, axis: str, force_emit: bool = False) -> bool:
        try:
            if axis in ("x", "y") and self._xy is not None:
                x_um, y_um = self._xy.get_xy_abs_um()
                new_abs = float(x_um if axis == "x" else y_um)
            elif axis == "z" and self._z is not None:
                new_abs = float(self._z.get_abs_um())
            elif axis == "p" and self._p is not None:
                new_abs = float(self._p.get_angle_deg())
            else:
                return False

            st = self._state[axis]
            changed = abs(float(st.abs_pos) - new_abs) > max(float(st.tolerance), 1e-6)
            st.abs_pos = new_abs

            if force_emit or changed:
                self._emit_positions(axis)

            return changed

        except Exception as e:
            #self._log(f"refresh failed axis={axis}: {e}")
            return False

    @Slot()
    def _poll_hardware_positions(self):
        for axis in ("x", "y", "z", "p"):
            if axis not in self._state:
                continue

            st = self._state[axis]

            # Toujours relire la position réelle
            self._refresh_from_hardware(axis, force_emit=False)

            if axis in ("x", "y") and st.moving and self._xy is not None:
                target = self._pending_targets_abs.get(axis, None)
                cur = float(st.abs_pos)
                tol = max(float(st.tolerance), 1.0)

                arrived = False
                if target is not None and abs(cur - float(target)) <= tol:
                    arrived = True

                # IMPORTANT:
                # on ne se fie plus à self._xy.is_moving() pour la Scientifica,
                # car ce retour peut être faux trop tôt selon le contrôleur / protocole.
                # La seule source de vérité ici est la position réellement relue.
                if arrived:
                    for ax_name in ("x", "y"):
                        if ax_name in self._state:
                            st_ax = self._state[ax_name]
                            st_ax.moving = False
                            st_ax.target_abs = None
                            self._pending_targets_abs.pop(ax_name, None)
                            self.movingChanged.emit(ax_name, False)
                            self._emit_positions(ax_name)
                    continue

            if axis == "z" and st.moving and self._z is not None:
                target = self._pending_targets_abs.get(axis, None)
                cur = float(st.abs_pos)
                tol = max(float(st.tolerance), 0.5)

                arrived = False
                if target is not None and abs(cur - float(target)) <= tol:
                    arrived = True

                if arrived or (not self._z.is_moving()):
                    st.moving = False
                    st.target_abs = None
                    self._pending_targets_abs.pop(axis, None)
                    self.movingChanged.emit(axis, False)
                    self._emit_positions(axis)
    
    def validate_scan_targets(self, scan_parameters: dict):
        """
        Vérifie que tous les offsets absolus / retours possibles des axes stepper
        restent dans la plage device.
        """
        if not scan_parameters:
            return

        offsets = dict(scan_parameters.get("offsets", {}) or {})
        sizes = dict(scan_parameters.get("sizes", {}) or {})
        initial_rel = dict(scan_parameters.get("initial_relative_positions", {}) or {})
        axis_order = list(scan_parameters.get("axis_order", []) or [])

        # positions fraîches avant toute validation
        for axis in ("x", "y", "z"):
            if axis in self._state:
                self._refresh_from_hardware(axis, force_emit=False)

        # Offsets absolus issus du ScanWidget
        for scan_axis_name, abs_target in offsets.items():
            axis = self.axis_from_scan_name(scan_axis_name)
            if axis is None or axis not in self._state:
                continue
            self.ensure_target_in_range(axis, float(abs_target))

            # bornes extrêmes possibles pendant le balayage stepper
            size_um = float(sizes.get(scan_axis_name, 0.0) or 0.0)
            half = 0.5 * abs(size_um)
            self.ensure_target_in_range(axis, float(abs_target) - half)
            self.ensure_target_in_range(axis, float(abs_target) + half)

        # retour final à la base relative
        for scan_axis_name, rel_target in initial_rel.items():
            axis = self.axis_from_scan_name(scan_axis_name)
            if axis is None or axis not in self._state:
                continue

            st = self._state[axis]
            target_abs = float(st.zero_offset) + float(rel_target)
            self.ensure_target_in_range(axis, target_abs)
    
    def _move_abs(self, axis: str, target_abs: float, speed: float):
        self._require_axis(axis)
        st = self._state[axis]

        # pour le réel, toujours relire le hardware avant de décider
        if axis in ("x", "y", "z", "p"):
            self._refresh_from_hardware(axis, force_emit=False)

        if not self._validate_move(axis, float(target_abs), float(speed)):
            return

        if axis == "z":
            if self._z is None:
                self._log("axis z requested but no PI V-308 controller is available.")
                return

            self._log(
                f"[Z MOVE] cur_abs={st.abs_pos:.3f} "
                f"target_abs={float(target_abs):.3f} "
                f"speed_mm_s={float(speed):.3f} "
                f"limits=[{st.min_pos:.3f}, {st.max_pos:.3f}] "
                f"max_speed={st.max_speed:.3f}"
            )

            st.moving = True
            st.target_abs = float(target_abs)
            self._pending_targets_abs[axis] = float(target_abs)
            self.movingChanged.emit(axis, True)

            try:
                self._z.move_abs_um(float(target_abs), speed_mm_s=float(speed), blocking=False)
            except Exception as e:
                st.moving = False
                st.target_abs = None
                self._pending_targets_abs.pop(axis, None)
                self.movingChanged.emit(axis, False)
                self._log(f"[Z MOVE] command failed: {e}")
                raise

            # refresh immédiat après envoi de la commande
            self._refresh_from_hardware(axis, force_emit=True)
            return

        if axis == "p":
            if self._p is None:
                self._log("axis p requested but no Thorlabs rotation controller is available.")
                return

            st.moving = True
            self.movingChanged.emit(axis, True)
            try:
                self._p.move_to_angle_deg(float(target_abs), blocking=True)
                st.abs_pos = float(self._p.get_angle_deg())
            finally:
                st.moving = False
                self.movingChanged.emit(axis, False)
                self._emit_positions(axis)
            return

        if axis in ("x", "y"):
            if self._xy is None:
                self._log(f"axis {axis!r} requested but no Scientifica XY controller is available.")
                return

            self._refresh_from_hardware("x", force_emit=False)
            self._refresh_from_hardware("y", force_emit=False)

            x_target = float(target_abs) if axis == "x" else float(self._state["x"].abs_pos)
            y_target = float(target_abs) if axis == "y" else float(self._state["y"].abs_pos)

            speed = max(0.001, float(speed))

            for ax_name, ax_target in (("x", x_target), ("y", y_target)):
                st_ax = self._state[ax_name]
                st_ax.moving = True
                st_ax.target_abs = float(ax_target)
                self._pending_targets_abs[ax_name] = float(ax_target)
                self.movingChanged.emit(ax_name, True)

            try:
                # IMPORTANT:
                # On n'écrit plus les paramètres de vitesse/accélération dans la UMS.
                # La platine garde les réglages définis côté contrôleur / LinLab.
                self._xy.move_xy_abs_um(x_target, y_target)

            except Exception:
                for ax_name in ("x", "y"):
                    st_ax = self._state[ax_name]
                    st_ax.moving = False
                    st_ax.target_abs = None
                    self._pending_targets_abs.pop(ax_name, None)
                    self.movingChanged.emit(ax_name, False)
                raise

            self._refresh_from_hardware("x", force_emit=True)
            self._refresh_from_hardware("y", force_emit=True)
            return

        self._log(f"axis {axis!r} requested, but no real controller is implemented in this V2.")

    @Slot(str, float, float)
    def move_to_rel(self, axis: str, rel_target: float, speed: float):
        self._require_axis(axis)
        self._refresh_from_hardware(axis, force_emit=False)
        st = self._state[axis]
        target_abs = float(st.zero_offset) + float(rel_target)
        self._move_abs(axis, target_abs=target_abs, speed=float(speed))

    @Slot(float, float, float, float)
    def move_xy_to_rel(self, x_rel_target: float, y_rel_target: float, speed_x: float, speed_y: float):
        """
        Déplacement XY atomique en coordonnées relatives DeepLight.
        Important pour les contrôleurs XY couplés comme la Scientifica :
        on calcule les deux cibles puis on envoie un seul move XY.
        """
        if self._xy is None:
            raise RuntimeError("XY controller is not available.")

        self._refresh_from_hardware("x", force_emit=False)
        self._refresh_from_hardware("y", force_emit=False)

        st_x = self._state["x"]
        st_y = self._state["y"]

        x_target_abs = float(st_x.zero_offset) + float(x_rel_target)
        y_target_abs = float(st_y.zero_offset) + float(y_rel_target)

        speed = max(float(speed_x), float(speed_y), 0.01)

        for ax_name, ax_target in (("x", x_target_abs), ("y", y_target_abs)):
            st_ax = self._state[ax_name]
            st_ax.moving = True
            st_ax.target_abs = float(ax_target)
            self._pending_targets_abs[ax_name] = float(ax_target)
            self.movingChanged.emit(ax_name, True)

        try:
            # IMPORTANT:
            # On n'écrit plus les paramètres de vitesse/accélération dans la UMS.
            # La platine garde les réglages définis côté contrôleur / LinLab.
            self._xy.move_xy_abs_um(x_target_abs, y_target_abs)

        except Exception:
            for ax_name in ("x", "y"):
                st_ax = self._state[ax_name]
                st_ax.moving = False
                st_ax.target_abs = None
                self._pending_targets_abs.pop(ax_name, None)
                self.movingChanged.emit(ax_name, False)
            raise

        self._refresh_from_hardware("x", force_emit=True)
        self._refresh_from_hardware("y", force_emit=True)
    
    def wait_until_xy_reached(self, x_rel_target: float, y_rel_target: float, timeout_s: float = 30.0):
        """
        Attend que la platine XY atteigne la cible relative demandée.
        Source de vérité = position réellement relue sur le hardware.
        """
        if self._xy is None:
            raise RuntimeError("XY controller is not available.")

        t0 = time.time()

        while True:
            self._refresh_from_hardware("x", force_emit=False)
            self._refresh_from_hardware("y", force_emit=False)

            cur_x = float(self.get_rel_pos("x"))
            cur_y = float(self.get_rel_pos("y"))

            tol_x = max(float(self.get_tolerance("x")), 0.1)
            tol_y = max(float(self.get_tolerance("y")), 0.1)

            if (
                abs(cur_x - float(x_rel_target)) <= tol_x
                and abs(cur_y - float(y_rel_target)) <= tol_y
            ):
                break

            if time.time() - t0 > float(timeout_s):
                raise RuntimeError(
                    f"Timeout while waiting for XY target: "
                    f"target=({float(x_rel_target):.3f}, {float(y_rel_target):.3f}) "
                    f"current=({cur_x:.3f}, {cur_y:.3f})"
                )

            time.sleep(0.01)

    def move_xy_to_rel_blocking(
        self,
        x_rel_target: float,
        y_rel_target: float,
        speed_x: float,
        speed_y: float,
        timeout_s: float = 30.0,
    ):
        """
        Déplacement XY atomique + attente de la cible.
        Utilisé pour le sample scan point par point.

        Robustesse:
        - 1er envoi du move
        - attente de la cible
        - si timeout: relire la position, réémettre UNE fois la même commande
        - si nouvel échec: lever l'erreur
        """
        self.move_xy_to_rel(
            float(x_rel_target),
            float(y_rel_target),
            float(speed_x),
            float(speed_y),
        )

        try:
            self.wait_until_xy_reached(
                float(x_rel_target),
                float(y_rel_target),
                timeout_s=float(timeout_s),
            )
            return
        except RuntimeError as first_error:
            # lecture fraîche avant retry
            try:
                self._refresh_from_hardware("x", force_emit=False)
                self._refresh_from_hardware("y", force_emit=False)

                cur_x = float(self.get_rel_pos("x"))
                cur_y = float(self.get_rel_pos("y"))

                tol_x = max(float(self.get_tolerance("x")), 0.1)
                tol_y = max(float(self.get_tolerance("y")), 0.1)

                if (
                    abs(cur_x - float(x_rel_target)) <= tol_x
                    and abs(cur_y - float(y_rel_target)) <= tol_y
                ):
                    return
            except Exception:
                pass

            self._log(
                "[XY MOVE BLOCKING] first wait timed out, retrying once "
                f"target=({float(x_rel_target):.3f}, {float(y_rel_target):.3f})"
            )

            # petit délai avant réémission
            time.sleep(0.05)

            self.move_xy_to_rel(
                float(x_rel_target),
                float(y_rel_target),
                float(speed_x),
                float(speed_y),
            )

            try:
                self.wait_until_xy_reached(
                    float(x_rel_target),
                    float(y_rel_target),
                    timeout_s=max(float(timeout_s), 5.0),
                )
                return
            except RuntimeError:
                raise first_error
    
    @Slot(str, float, float)
    def move_relative(self, axis: str, delta: float, speed: float):
        self._require_axis(axis)
        self._refresh_from_hardware(axis, force_emit=False)
        st = self._state[axis]
        target_abs = float(st.abs_pos) + float(delta)
        self._move_abs(axis, target_abs=target_abs, speed=float(speed))

    @Slot(str, float, float, float, str)
    def move_from_scan(
        self,
        axis_name: str,
        target_rel: float,
        velocity: float,
        t_sched_ms: float,
        reason: str
    ):
        axis = self.axis_from_scan_name(axis_name)
        if axis is None or axis not in self._state:
            logger.warning(
                f"[RealHardwarePositioner] move_from_scan ignored: "
                f"unknown axis_name={axis_name!r}"
            )
            return

        # ------------------------------------------------------------------
        # Sample scan XY: on regroupe sample_pixel_x + sample_pixel_y
        # en un seul move XY atomique.
        # ------------------------------------------------------------------
        if axis in ("x", "y") and reason in ("sample_pixel_x", "sample_pixel_y"):
            self._pending_sample_xy_rel[axis] = float(target_rel)

            other_axis = "y" if axis == "x" else "x"
            other_rel = self._pending_sample_xy_rel.get(other_axis, None)

            logger.debug(
                f"[RealHardwarePositioner] move_from_scan "
                f"axis_name={axis_name} axis={axis} "
                f"target_rel={target_rel} target_abs={float(self.rel_to_abs(axis, target_rel))} "
                f"velocity={velocity} t_sched_ms={t_sched_ms} "
                f"converted_speed=pending_xy reason={reason}"
            )

            # On attend d'avoir les 2 coordonnées du pixel avant de bouger.
            if other_rel is None:
                return

            x_rel = self._pending_sample_xy_rel["x"]
            y_rel = self._pending_sample_xy_rel["y"]
            self._pending_sample_xy_rel = {"x": None, "y": None}

            # En sample scan, velocity arrive actuellement à 0.0.
            # On prend une vitesse XY réaliste depuis les limites du manager.
            speed_x = max(0.01, float(self.get_max_speed("x")))
            speed_y = max(0.01, float(self.get_max_speed("y")))

            logger.debug(
                f"[RealHardwarePositioner] [SAMPLE XY ATOMIC] "
                f"x_rel={x_rel:.3f} y_rel={y_rel:.3f} "
                f"speed_x={speed_x:.3f} speed_y={speed_y:.3f}"
            )

            move_xy = getattr(self, "move_xy_to_rel", None)
            if callable(move_xy):
                move_xy(
                    float(x_rel),
                    float(y_rel),
                    float(speed_x),
                    float(speed_y),
                )
            else:
                # fallback de sécurité
                self._refresh_from_hardware("x", force_emit=False)
                self._refresh_from_hardware("y", force_emit=False)
                st_x = self._state["x"]
                st_y = self._state["y"]
                x_target_abs = float(st_x.zero_offset) + float(x_rel)
                y_target_abs = float(st_y.zero_offset) + float(y_rel)
                self._move_abs("x", target_abs=x_target_abs, speed=float(speed_x))
                self._move_abs("y", target_abs=y_target_abs, speed=float(speed_y))

            return

        # ------------------------------------------------------------------
        # Cas standard (laser / Z / P / autres commandes)
        # ------------------------------------------------------------------
        self._refresh_from_hardware(axis, force_emit=False)
        st = self._state[axis]
        target_abs = float(st.zero_offset) + float(target_rel)

        if axis in ("x", "y"):
            speed = max(0.01, float(velocity) / 1000.0)   # vel_um_s -> mm/s
        elif axis in ("z", "p"):
            speed = max(0.001, float(velocity))
        else:
            speed = 0.1

        logger.debug(
            f"[RealHardwarePositioner] move_from_scan "
            f"axis_name={axis_name} axis={axis} "
            f"target_rel={target_rel} target_abs={target_abs} "
            f"velocity={velocity} t_sched_ms={t_sched_ms} "
            f"converted_speed={speed} reason={reason}"
        )

        self._move_abs(axis, target_abs=target_abs, speed=speed)

    @Slot(str)
    def home(self, axis: str):
        if axis in ("x", "y") and self._xy is not None:
            # DeepLight semantic:
            # home = return to logical zero (zero_offset), not Motion 8 hardware home.
            if SCIENTIFICA_STAGE_HOME_BEHAVIOR == "zero_offset":
                self._refresh_from_hardware(axis, force_emit=False)
                st = self._state[axis]
                self._move_abs(
                    axis,
                    target_abs=float(st.zero_offset),
                    speed=max(0.01, float(st.max_speed)),
                )
                return

            # Optional true hardware home
            for ax_name in ("x", "y"):
                if ax_name in self._state:
                    st_ax = self._state[ax_name]
                    st_ax.moving = True
                    st_ax.target_abs = None
                    self.movingChanged.emit(ax_name, True)

            try:
                self._xy.home(mode=SCIENTIFICA_STAGE_HOME_MODE)
            finally:
                self._refresh_from_hardware("x", force_emit=True)
                self._refresh_from_hardware("y", force_emit=True)

            return

        st = self._state[axis]
        self._move_abs(axis, target_abs=float(st.zero_offset), speed=max(0.01, float(st.max_speed)))

    @Slot(str)
    def stop(self, axis: str):
        if axis in ("x", "y") and self._xy is not None:
            self._xy.stop(abrupt=False)

            for ax_name in ("x", "y"):
                if ax_name in self._state:
                    self._state[ax_name].moving = False
                    self._state[ax_name].target_abs = None
                    self._pending_targets_abs.pop(ax_name, None)
                    self.movingChanged.emit(ax_name, False)

            self._refresh_from_hardware("x")
            self._refresh_from_hardware("y")
            return
        elif axis == "z" and self._z is not None:
            self._z.stop()
        elif axis == "p" and self._p is not None:
            self._p.stop()

        st = self._state[axis]
        if st.moving:
            st.moving = False
            self.movingChanged.emit(axis, False)

        self._refresh_from_hardware(axis)

    @Slot(str)
    def set_zero(self, axis: str):
        self._refresh_from_hardware(axis, force_emit=False)
        st = self._state[axis]
        st.zero_offset = float(st.abs_pos)
        self._emit_positions(axis)

    def set_ums_scaling_factors(self, scaling_x: float, scaling_y: float):
        if self._xy is None:
            return
        try:
            self._xy.set_scaling_factors(float(scaling_x), float(scaling_y))
            # Re-sync displayed positions because the conversion changed.
            self._refresh_from_hardware("x", force_emit=True)
            self._refresh_from_hardware("y", force_emit=True)
        except Exception as e:
            self._log(f"set_ums_scaling_factors failed: {e}")

    def close(self):
        try:
            self._poll_timer.stop()
        except Exception:
            pass

        try:
            if self._xy is not None:
                self._xy.close()
        except Exception:
            pass

# =============================================================================
# HARDWARE MANAGER
# =============================================================================

class HardwareManager(QObject):
    """
    Real hardware facade / factory for DeepLight.

    Provides:
    - shutter open/close
    - real hardware positioner manager for z / p
    - Mira900 power mapping via rotation mount

    Current V1 scope:
    - NI channels are hard-coded here for easy editing
    - serials are hard-coded here for easy editing
    """

    def __init__(self, backend_name: str = "mock", settings_manager=None, parent=None):
        super().__init__(parent)
        self.backend_name = (backend_name or "mock").lower()
        self._positioner_axes = ["x", "y", "z", "p"]
        self.settings_manager = settings_manager

        self._shutter = None
        self._xy_controller = None
        self._z_controller = None
        self._rotators = {}

        self._devices_initialized = False
        self._shutter_failed = False
        self._xy_failed = False
        self._z_failed = False
        self._rotator_failed = {}

        self._camera_backend = None
        self._camera_controller = None
        self._brillouin_camera = None

        self._laser_manager = None

        if self.backend_name == "nidaq":
            self._ensure_real_devices()

    def create_laser_manager(self, parent=None):
        if self._laser_manager is not None:
            return self._laser_manager

        self._laser_manager = LaserHardware(self.backend_name, parent=parent)
        return self._laser_manager
    
    def create_camera_controller(self, parent=None):
        if self._camera_controller is not None:
            return self._camera_controller

        if self.backend_name == "mock":
            self._camera_backend = MockCameraBackend()
        else:
            self._camera_backend = OpenCVCameraBackend(camera_index=0)

        self._camera_controller = CameraController(self._camera_backend, parent=parent)
        return self._camera_controller
        
    def _get_laser_runtime_settings(self, laser_name: str) -> dict:
        if self.settings_manager is None:
            raise RuntimeError("HardwareManager has no settings_manager.")

        cfg = self.settings_manager.get_laser_settings(laser_name)
        if not cfg:
            raise RuntimeError(f"Missing laser settings for {laser_name!r}")

        required = ("speed", "steps_per_degree", "offset_deg")
        for key in required:
            if key not in cfg:
                raise RuntimeError(f"Missing laser setting {key!r} for {laser_name!r}")

        return cfg
    
    def _is_real_backend(self) -> bool:
        return self.backend_name == "nidaq"
    
    def _ensure_real_devices(self):
        if self.backend_name != "nidaq":
            return

        if self._devices_initialized:
            return

        if self._shutter is None:
            self._shutter = _ThorlabsShutterController(THORLABS_SHUTTER_SERIAL)

        if self._xy_controller is None:
            # Read per-axis UMS scaling factors from settings_manager if available.
            scaling_x = SCIENTIFICA_STAGE_DEFAULT_SCALING_X
            scaling_y = SCIENTIFICA_STAGE_DEFAULT_SCALING_Y
            try:
                if self._settings_manager is not None:
                    sx_settings = self._settings_manager.get_axis_settings("X-Stage") or {}
                    sy_settings = self._settings_manager.get_axis_settings("Y-Stage") or {}
                    scaling_x = float(sx_settings.get("ums_scaling", scaling_x))
                    scaling_y = float(sy_settings.get("ums_scaling", scaling_y))
            except Exception as e:
                logger.warning(
                    f"[HardwareManager] Could not read ums_scaling from settings, "
                    f"using defaults x={scaling_x}, y={scaling_y}: {e}"
                )

            self._xy_controller = _ScientificaMotion8XYController(
                port=SCIENTIFICA_STAGE_PORT,
                baudrate=SCIENTIFICA_STAGE_BAUDRATE,
                timeout_s=SCIENTIFICA_STAGE_TIMEOUT_S,
                device_id=SCIENTIFICA_STAGE_DEVICE_ID,
                x_axis_id=SCIENTIFICA_STAGE_X_AXIS_ID,
                y_axis_id=SCIENTIFICA_STAGE_Y_AXIS_ID,
                scaling_x=scaling_x,
                scaling_y=scaling_y,
            )

        if self._z_controller is None:
            self._z_controller = _PIVoiceCoilController(PI_V308_SERIAL)

        for laser_name, serial in THORLABS_ROTATOR_SERIALS.items():
            if laser_name not in self._rotators:
                self._rotators[laser_name] = _ThorlabsRotationController(serial)

        try:
            if self._shutter is not None and not self._shutter.connected:
                self._shutter.connect()
                logger.info(f"[HardwareManager] Connection to shutter serial={THORLABS_SHUTTER_SERIAL} successful")
            elif self._shutter is not None and self._shutter.connected:
                logger.info(f"[HardwareManager] Shutter serial={THORLABS_SHUTTER_SERIAL} already connected")
        except Exception as e:
            self._shutter_failed = True
            logger.error(f"[HardwareManager] ERROR connecting shutter serial={THORLABS_SHUTTER_SERIAL}: {e}")

        try:
            if self._xy_controller is not None and not self._xy_controller.connected:
                self._xy_controller.connect()
                x_um, y_um = self._xy_controller.get_xy_abs_um()
                logger.info(
                    f"[HardwareManager] Connection to Scientifica XY "
                    f"port={SCIENTIFICA_STAGE_PORT} successful "
                    f"(X={x_um:.2f} µm, Y={y_um:.2f} µm)"
                )
            elif self._xy_controller is not None and self._xy_controller.connected:
                logger.info(f"[HardwareManager] Scientifica XY port={SCIENTIFICA_STAGE_PORT} already connected")
        except Exception as e:
            self._xy_failed = True
            logger.error(f"[HardwareManager] ERROR connecting Scientifica XY port={SCIENTIFICA_STAGE_PORT}: {e}")

        try:
            if self._z_controller is not None and not self._z_controller.connected:
                self._z_controller.connect()
                logger.info(f"[HardwareManager] Connection to PI V-308 serial={PI_V308_SERIAL} successful")
            elif self._z_controller is not None and self._z_controller.connected:
                logger.info(f"[HardwareManager] PI V-308 serial={PI_V308_SERIAL} already connected")
        except Exception as e:
            self._z_failed = True
            logger.error(f"[HardwareManager] ERROR connecting PI V-308 serial={PI_V308_SERIAL}: {e}")

        for laser_name, rot in self._rotators.items():
            try:
                if rot is not None and not rot.connected:
                    serial = THORLABS_ROTATOR_SERIALS.get(laser_name, "unknown")
                    rot.connect()
                    logger.info(f"[HardwareManager] Connection to rotator {laser_name} serial={serial} successful")

                    cfg = self._get_laser_runtime_settings(laser_name)

                    rot.set_power_percent(
                        0.0,
                        speed=int(cfg["speed"]),
                        steps_per_degree=float(cfg["steps_per_degree"]),
                        offset_deg=float(cfg["offset_deg"]),
                    )
                    logger.info(f"[HardwareManager] Rotator {laser_name} initialized to 0% successfully")

                elif rot is not None and rot.connected:
                    serial = THORLABS_ROTATOR_SERIALS.get(laser_name, "unknown")
                    logger.info(f"[HardwareManager] Rotator {laser_name} serial={serial} already connected")
            except Exception as e:
                self._rotator_failed[laser_name] = True
                logger.error(f"[HardwareManager] ERROR connecting rotator for {laser_name}: {e}")

        self._devices_initialized = True

    def create_positioner_manager(self, parent=None):
        if self._is_real_backend():
            self._ensure_real_devices()
            return RealHardwarePositionerManager(
                self._positioner_axes,
                xy_controller=self._xy_controller,
                z_controller=self._z_controller,
                p_controller=None,
                parent=parent,
                poll_ms=80,
            )

        return MockPositionerManager(self._positioner_axes, parent=parent)

    def validate_scan_positions(self, positioner_manager, scan_parameters: dict):
        if self.backend_name != "nidaq":
            return

        if positioner_manager is None:
            return

        validator = getattr(positioner_manager, "validate_scan_targets", None)
        if callable(validator):
            validator(scan_parameters)
    
    @Slot(bool)
    def set_shutter(self, open_: bool):
        if self.backend_name != "nidaq":
            return

        if self._shutter is None:
            raise RuntimeError("Shutter controller is not initialized.")
        
        self._shutter.set_open(bool(open_))

    def set_laser_power_percent(self, laser_name: str, percent: float, speed: int, steps_per_degree: float, offset_deg: float):
        logger.debug(
            f"[HardwareManager] set_laser_power_percent "
            f"laser={laser_name} percent={percent} speed={speed} "
            f"steps_per_degree={steps_per_degree} offset_deg={offset_deg}"
        )

        if self.backend_name != "nidaq":
            return

        if str(laser_name) == "Cobolt 660":
            if self._laser_manager is None:
                self.create_laser_manager(parent=self)

            if self._laser_manager is None or self._laser_manager._cobolt_hwp is None:
                raise RuntimeError("Cobolt ELL14 HWP controller is not initialized.")

            self._laser_manager._cobolt_hwp.set_power_percent(
                float(percent),
                offset_deg=float(offset_deg),
            )
            return
        
        rot = self._rotators.get(str(laser_name))
        if rot is None:
            raise KeyError(f"No rotator configured for laser {laser_name!r}")

        rot.set_power_percent(
            float(percent),
            speed=int(speed),
            steps_per_degree=float(steps_per_degree),
            offset_deg=float(offset_deg),
        )

    def get_laser_power_percent(self, laser_name: str) -> float:
        if self.backend_name != "nidaq":
            return 0.0

        self._ensure_real_devices()

        if str(laser_name) == "Cobolt 660":
            if self._laser_manager is None:
                self.create_laser_manager(parent=self)

            if self._laser_manager is None or self._laser_manager._cobolt_hwp is None:
                return 0.0

            cfg = self._get_laser_runtime_settings(laser_name)
            return self._laser_manager._cobolt_hwp.get_power_percent(
                offset_deg=float(cfg["offset_deg"])
            )

        rot = self._rotators.get(str(laser_name))
        if rot is None:
            raise KeyError(f"No rotator configured for laser {laser_name!r}")

        cfg = self._get_laser_runtime_settings(laser_name)
        return rot.get_power_percent(offset_deg=float(cfg["offset_deg"]))
    
    # ==========================================================
    # Brillouin camera (Kuro / PICam)
    # ==========================================================

    def _get_brillouin_camera(self):
        """
        Lazy initialization of the Kuro camera backend.
        """
        if self._brillouin_camera is None:
            self._brillouin_camera = PiCamKuroManager()

        if not getattr(self._brillouin_camera, "connected", False):
            self._brillouin_camera.connect()

        return self._brillouin_camera


    def ensure_brillouin_camera_ready(self):
        """
        Force l'initialisation / connexion de la caméra Brillouin (Kuro)
        sans lancer d'acquisition.
        Utilisé pour préchauffer la caméra dès l'activation du mode Brillouin.
        """
        if self.backend_name == "mock":
            return None

        cam = self._get_brillouin_camera()
        return cam
    
    def acquire_brillouin_image(self, params=None):
        """
        Acquire a single Brillouin image.

        Behaviour depends on the global backend:
            mock  -> returns None (SpectroManager keeps using its mock)
            nidaq -> acquire real image from Kuro camera
        """
        logger.debug(f"[HardwareManager] acquire_brillouin_image backend={self.backend_name}")

        if self.backend_name == "mock":
            logger.debug("[HardwareManager] Brillouin source = mock fallback")
            return None

        if self.backend_name == "nidaq":
            logger.debug("[HardwareManager] Brillouin source = PICam/Kuro")
            cam = self._get_brillouin_camera()
            img = cam.snap(params or {})
            logger.debug(
                f"[HardwareManager] Kuro image shape={getattr(img, 'shape', None)} "
                f"dtype={getattr(img, 'dtype', None)}"
            )
            return img

        logger.warning("[HardwareManager] Brillouin source unavailable")
        return None

    def close(self):
        try:
            if self._camera_controller is not None:
                self._camera_controller.stop_live()
        except Exception:
            pass

        try:
            if self._camera_backend is not None:
                self._camera_backend.disconnect()
        except Exception:
            pass

        try:
            if self._brillouin_camera is not None:
                self._brillouin_camera.disconnect()
        except Exception:
            pass

        try:
            if self._laser_manager is not None:
                self._laser_manager.close()
        except Exception:
            pass

        for dev in (self._shutter, self._xy_controller, self._z_controller, *self._rotators.values()):
            try:
                if dev is not None:
                    dev.close()
            except Exception:
                pass

    def set_ums_scaling_factor(self, factor: float):
        """
        Update the UMS scaling factor at runtime.
        Propagates the change to the live XY controller if it exists.
        """
        if self._xy_controller is not None:
            try:
                self._xy_controller.set_scaling_factor(float(factor))
            except Exception as e:
                logger.error(f"[HardwareManager] set_ums_scaling_factor failed: {e}")