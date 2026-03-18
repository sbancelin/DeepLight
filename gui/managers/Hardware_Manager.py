from __future__ import annotations

import os
import time
import clr  # pythonnet
from System.Globalization import CultureInfo
from System import Decimal as SystemDecimal
from pipython import GCSDevice, GCSError

from typing import Optional

from PySide6.QtCore import QObject, Slot

from .Positioner_Manager import MockPositionerManager, PositionerManager


# =============================================================================
# HARD-CODED HARDWARE CONFIG
# =============================================================================

NI_DEVICE_NAME = "Dev1"

# NI AO / AI channels
NI_AO_X = f"{NI_DEVICE_NAME}/ao0"
NI_AO_Y = f"{NI_DEVICE_NAME}/ao1"
NI_AI_IR = f"{NI_DEVICE_NAME}/ai0"
NI_AI_VIS = f"{NI_DEVICE_NAME}/ai1"

# AI default range / config
NI_AI_MIN_V = -10.0
NI_AI_MAX_V = 10.0
NI_AI_TERMINAL_MODE = "DIFF"  # requested: differential

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

# Mira900 power mapping (requested for now: 0° = 0%, 180° = 100%)
MIRA_POWER_MIN_PERCENT = 0.0
MIRA_POWER_MAX_PERCENT = 100.0
MIRA_ROTATOR_MIN_DEG = 0.0
MIRA_ROTATOR_MAX_DEG = 180.0

# PI Z defaults
PI_Z_AXIS_ID = 1
PI_Z_DEFAULT_VEL_MM_S = 0.5

# If your PRM controller class differs, change this later.
# Common values depending on controller family:
# - "KCubeDCServo"
# - "KCubeStepperMotor"
THORLABS_ROTATOR_CONTROLLER_KIND = "KCubeDCServo"


# =============================================================================
# OPTIONAL IMPORTS
# =============================================================================

try:
    from pipython import GCSDevice, GCSError
    _HAS_PI = True
except Exception:
    GCSDevice = None
    GCSError = Exception
    _HAS_PI = False

try:
    import clr  # pythonnet
    _HAS_CLR = True
except Exception:
    clr = None
    _HAS_CLR = False


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

    def move_abs_um(self, target_um: float, speed_mm_s: Optional[float] = None, blocking: bool = True):
        if not self.connected:
            self.connect()

        if speed_mm_s is not None:
            try:
                self.device.VEL(self.axis, float(speed_mm_s))
            except Exception:
                pass

        target_mm = float(target_um) / 1000.0
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

    def wait_until_stopped(self, target_mm: float, timeout_s: float = 30.0):
        t0 = time.time()
        while True:
            moving = False
            try:
                moving = bool(self.device.IsMoving(self.axis))
            except Exception:
                pass

            try:
                pos = self.device.qPOS(self.axis)
                cur_mm = float(pos[self.axis]) if isinstance(pos, dict) else float(pos)
            except Exception:
                cur_mm = target_mm

            if (not moving) and abs(cur_mm - target_mm) < 1e-4:
                break

            if time.time() - t0 > timeout_s:
                raise RuntimeError("Timeout while waiting for PI V-308 motion to complete.")

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

    def move_to_angle_deg(self, angle_deg: float, speed: int, steps_per_degree: float, blocking: bool = True):
        if not self.connected:
            self.connect()

        angle_deg = max(MIRA_ROTATOR_MIN_DEG, min(MIRA_ROTATOR_MAX_DEG, float(angle_deg)))
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
        percent = max(MIRA_POWER_MIN_PERCENT, min(MIRA_POWER_MAX_PERCENT, float(percent)))
        offset_deg = float(offset_deg)

        angle = offset_deg + (percent / 100.0) * (MIRA_ROTATOR_MAX_DEG - MIRA_ROTATOR_MIN_DEG)

        self.move_to_angle_deg(
            angle,
            speed=int(speed),
            steps_per_degree=float(steps_per_degree),
            blocking=True,
        )

    def get_power_percent(self) -> float:
        angle = self.get_angle_deg()
        if MIRA_ROTATOR_MAX_DEG == MIRA_ROTATOR_MIN_DEG:
            return 0.0
        return 100.0 * (angle - MIRA_ROTATOR_MIN_DEG) / (MIRA_ROTATOR_MAX_DEG - MIRA_ROTATOR_MIN_DEG)

    def close(self):
        try:
            if self.device is not None:
                self.device.StopPolling()
                self.device.Disconnect()
        finally:
            self.device = None
            self.connected = False


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
        z_controller: Optional[_PIVoiceCoilController] = None,
        p_controller: Optional[_ThorlabsRotationController] = None,
        parent=None,
    ):
        super().__init__(axes, parent=parent)
        self._z = z_controller
        self._p = p_controller

        # Initialize abs positions from hardware when possible
        self._refresh_from_hardware("z")
        self._refresh_from_hardware("p")

    def _log(self, msg: str):
        print(f"[RealHardwarePositioner] {msg}")

    def _refresh_from_hardware(self, axis: str):
        try:
            if axis == "z" and self._z is not None:
                self._state["z"].abs_pos = float(self._z.get_abs_um())
                self._emit_positions("z")
            elif axis == "p" and self._p is not None:
                self._state["p"].abs_pos = float(self._p.get_angle_deg())
                self._emit_positions("p")
        except Exception as e:
            self._log(f"refresh failed axis={axis}: {e}")

    def _move_abs(self, axis: str, target_abs: float, speed: float):
        self._require_axis(axis)
        st = self._state[axis]

        if not self._validate_move(axis, float(target_abs), float(speed)):
            return

        if axis == "z":
            if self._z is None:
                self._log("axis z requested but no PI V-308 controller is available.")
                return

            st.moving = True
            self.movingChanged.emit(axis, True)
            try:
                self._z.move_abs_um(float(target_abs), speed_mm_s=float(speed), blocking=True)
                st.abs_pos = float(self._z.get_abs_um())
            finally:
                st.moving = False
                self.movingChanged.emit(axis, False)
                self._emit_positions(axis)
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

        # x / y not provided in the hardware description
        self._log(f"axis {axis!r} requested, but no real controller is implemented in this V1.")

    @Slot(str, float, float)
    def move_to_rel(self, axis: str, rel_target: float, speed: float):
        st = self._state[axis]
        target_abs = float(st.zero_offset) + float(rel_target)
        self._move_abs(axis, target_abs=target_abs, speed=float(speed))

    @Slot(str, float, float)
    def move_relative(self, axis: str, delta: float, speed: float):
        st = self._state[axis]
        target_abs = float(st.abs_pos) + float(delta)
        self._move_abs(axis, target_abs=target_abs, speed=float(speed))

    @Slot(str, float, float, float, float, float, str)
    def move_from_scan(
        self,
        axis_name: str,
        target_rel: float,
        vel_um_s: float,
        acc_um_s2: float,
        jerk_um_s3: float,
        t_sched_ms: float,
        reason: str
    ):
        axis = self.axis_from_scan_name(axis_name)
        if axis is None or axis not in self._state:
            return

        st = self._state[axis]
        target_abs = float(st.zero_offset) + float(target_rel)

        if axis == "z":
            # PositionerWidget uses mm/s; scan sends µm/s -> convert
            speed = max(0.001, float(vel_um_s) / 1000.0)
        elif axis == "p":
            # p axis is degrees in your UI/positioner model
            speed = max(0.001, float(vel_um_s))
        else:
            speed = 0.1

        self._move_abs(axis, target_abs=target_abs, speed=speed)

    @Slot(str)
    def home(self, axis: str):
        st = self._state[axis]
        self._move_abs(axis, target_abs=float(st.zero_offset), speed=max(0.01, float(st.max_speed)))

    @Slot(str)
    def stop(self, axis: str):
        if axis == "z" and self._z is not None:
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
        self._refresh_from_hardware(axis)
        st = self._state[axis]
        st.zero_offset = st.abs_pos
        self._emit_positions(axis)


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
        self._z_controller = None
        self._rotators = {}

        self._devices_initialized = False
        self._shutter_failed = False
        self._z_failed = False
        self._rotator_failed = {}

        if self.backend_name == "nidaq":
            self._ensure_real_devices()

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

        if self._z_controller is None:
            print(f"[HardwareManager] Creating PI V-308 controller serial={PI_V308_SERIAL}")
            self._z_controller = _PIVoiceCoilController(PI_V308_SERIAL)

        for laser_name, serial in THORLABS_ROTATOR_SERIALS.items():
            if laser_name not in self._rotators:
                print(f"[HardwareManager] Creating rotator for {laser_name} serial={serial}")
                self._rotators[laser_name] = _ThorlabsRotationController(serial)

        try:
            if self._shutter is not None and not self._shutter.connected:
                print(f"[HardwareManager] Connecting shutter serial={THORLABS_SHUTTER_SERIAL}...")
                self._shutter.connect()
                print(f"[HardwareManager] Connection to shutter serial={THORLABS_SHUTTER_SERIAL} successful")
            elif self._shutter is not None and self._shutter.connected:
                print(f"[HardwareManager] Shutter serial={THORLABS_SHUTTER_SERIAL} already connected")
        except Exception as e:
            self._shutter_failed = True
            print(f"[HardwareManager] ERROR connecting shutter serial={THORLABS_SHUTTER_SERIAL}: {e}")

        try:
            if self._z_controller is not None and not self._z_controller.connected:
                print(f"[HardwareManager] Connecting PI V-308 serial={PI_V308_SERIAL}...")
                self._z_controller.connect()
                print(f"[HardwareManager] Connection to PI V-308 serial={PI_V308_SERIAL} successful")
            elif self._z_controller is not None and self._z_controller.connected:
                print(f"[HardwareManager] PI V-308 serial={PI_V308_SERIAL} already connected")
        except Exception as e:
            self._z_failed = True
            print(f"[HardwareManager] ERROR connecting PI V-308 serial={PI_V308_SERIAL}: {e}")

        for laser_name, rot in self._rotators.items():
            try:
                if rot is not None and not rot.connected:
                    serial = THORLABS_ROTATOR_SERIALS.get(laser_name, "unknown")
                    print(f"[HardwareManager] Connecting rotator for {laser_name} serial={serial}...")
                    rot.connect()
                    print(f"[HardwareManager] Connection to rotator {laser_name} serial={serial} successful")

                    cfg = self._get_laser_runtime_settings(laser_name)

                    print(f"[HardwareManager] Initializing rotator {laser_name} to 0%")
                    rot.set_power_percent(
                        0.0,
                        speed=int(cfg["speed"]),
                        steps_per_degree=float(cfg["steps_per_degree"]),
                        offset_deg=float(cfg["offset_deg"]),
                    )
                    print(f"[HardwareManager] Rotator {laser_name} initialized to 0% successfully")

                elif rot is not None and rot.connected:
                    serial = THORLABS_ROTATOR_SERIALS.get(laser_name, "unknown")
                    print(f"[HardwareManager] Rotator {laser_name} serial={serial} already connected")
            except Exception as e:
                self._rotator_failed[laser_name] = True
                print(f"[HardwareManager] ERROR connecting rotator for {laser_name}: {e}")

        self._devices_initialized = True

    def create_positioner_manager(self, parent=None):
        if self._is_real_backend():
            self._ensure_real_devices()
            return RealHardwarePositionerManager(
                self._positioner_axes,
                z_controller=self._z_controller,
                p_controller=None,
                parent=parent,
            )

        return MockPositionerManager(self._positioner_axes, parent=parent)

    @Slot(bool)
    def set_shutter(self, open_: bool):
        if self.backend_name != "nidaq":
            return

        if self._shutter is None:
            raise RuntimeError("Shutter controller is not initialized.")
        
        self._shutter.set_open(bool(open_))

    def set_laser_power_percent(self, laser_name: str, percent: float, speed: int, steps_per_degree: float, offset_deg: float):
        print(
            f"[HardwareManager] set_laser_power_percent "
            f"laser={laser_name} percent={percent} speed={speed} "
            f"steps_per_degree={steps_per_degree} offset_deg={offset_deg}"
        )

        if self.backend_name != "nidaq":
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

        rot = self._rotators.get(str(laser_name))
        if rot is None:
            raise KeyError(f"No rotator configured for laser {laser_name!r}")

        return rot.get_power_percent()

    def close(self):
        for dev in (self._shutter, self._z_controller, *self._rotators.values()):
            try:
                if dev is not None:
                    dev.close()
            except Exception:
                pass