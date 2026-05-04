from __future__ import annotations

from PySide6.QtCore import QObject, QThread, Signal, Slot, Qt, QTimer


class _LaserCommandWorker(QObject):
    command_finished = Signal(str, float)
    command_failed = Signal(str, float, str)
    enabled_finished = Signal(str, bool)
    enabled_failed = Signal(str, bool, str)

    gdd_finished = Signal(str, float)
    gdd_failed = Signal(str, float, str)
    rep_rate_finished = Signal(str, float)
    rep_rate_failed = Signal(str, float, str)

    def __init__(self, hardware_manager, laser_manager, settings_manager):
        super().__init__()
        self.hardware_manager = hardware_manager
        self.laser_manager = laser_manager
        self.settings_manager = settings_manager

        self._pending_values = {}
        self._running = False
        self._stopping = False

    @Slot(str, float)
    def enqueue(self, laser_name: str, value: float):
        self._pending_values[str(laser_name)] = float(value)

        if self._running or self._stopping:
            return

        QTimer.singleShot(0, self._process_next)

    @Slot()
    def stop(self):
        self._stopping = True
        self._pending_values.clear()

    @Slot()
    def _process_next(self):
        if self._stopping:
            self._running = False
            return

        next_command = self._take_next_command()
        if next_command is None:
            self._running = False
            return

        self._running = True
        laser_name, value = next_command

        try:
            self._apply_power_change(laser_name, value)
        except Exception as e:
            self.command_failed.emit(str(laser_name), float(value), str(e))
        else:
            self.command_finished.emit(str(laser_name), float(value))
        finally:
            self._running = False

        if self._pending_values and not self._stopping:
            QTimer.singleShot(0, self._process_next)

    def _take_next_command(self):
        if not self._pending_values:
            return None

        laser_name = next(iter(self._pending_values.keys()))
        value = self._pending_values.pop(laser_name)
        return str(laser_name), float(value)
    
    @Slot(str, bool)
    def set_enabled(self, laser_name: str, enabled: bool):
        try:
            self.laser_manager.set_enabled(str(laser_name), bool(enabled))
        except Exception as e:
            self.enabled_failed.emit(str(laser_name), bool(enabled), str(e))
        else:
            self.enabled_finished.emit(str(laser_name), bool(enabled))

    @Slot(str, float)
    def set_gdd(self, laser_name: str, gdd_fs2: float):
        try:
            self.laser_manager.set_gdd_fs2(str(laser_name), float(gdd_fs2))
        except Exception as e:
            self.gdd_failed.emit(str(laser_name), float(gdd_fs2), str(e))
        else:
            self.gdd_finished.emit(str(laser_name), float(gdd_fs2))


    @Slot(str, float)
    def set_rep_rate(self, laser_name: str, rep_rate_khz: float):
        try:
            self.laser_manager.set_rep_rate_khz(str(laser_name), float(rep_rate_khz))
        except Exception as e:
            self.rep_rate_failed.emit(str(laser_name), float(rep_rate_khz), str(e))
        else:
            self.rep_rate_finished.emit(str(laser_name), float(rep_rate_khz))
    
    def _apply_power_change(self, laser_name: str, value: float):
        laser_name = str(laser_name)

        if laser_name in ("Mira 900", "Tumecs"):
            cfg = self.settings_manager.get_laser_settings(laser_name)

            if "speed" not in cfg or "steps_per_degree" not in cfg or "offset_deg" not in cfg:
                raise RuntimeError(f"Missing laser settings for {laser_name!r}")

            self.hardware_manager.set_laser_power_percent(
                laser_name,
                float(value),
                speed=int(cfg["speed"]),
                steps_per_degree=float(cfg["steps_per_degree"]),
                offset_deg=float(cfg["offset_deg"]),
            )
            return

        self.laser_manager.set_power_percent(laser_name, float(value))


class LaserManager(QObject):
    command_finished = Signal(str, float)
    command_failed = Signal(str, float, str)
    enabled_finished = Signal(str, bool)
    enabled_failed = Signal(str, bool, str)

    gdd_finished = Signal(str, float)
    gdd_failed = Signal(str, float, str)
    rep_rate_finished = Signal(str, float)
    rep_rate_failed = Signal(str, float, str)

    _enqueue_requested = Signal(str, float)
    _stop_requested = Signal()
    _enabled_requested = Signal(str, bool)

    _gdd_requested = Signal(str, float)
    _rep_rate_requested = Signal(str, float)

    def __init__(self, hardware_manager, laser_manager, settings_manager, parent=None):
        super().__init__(parent)
        self.hardware_manager = hardware_manager
        self.laser_manager = laser_manager
        self.settings_manager = settings_manager

        self._thread = QThread(self)
        self._worker = _LaserCommandWorker(
            hardware_manager=self.hardware_manager,
            laser_manager=self.laser_manager,
            settings_manager=self.settings_manager,
        )
        self._worker.moveToThread(self._thread)

        self._enqueue_requested.connect(self._worker.enqueue, Qt.QueuedConnection)
        self._stop_requested.connect(self._worker.stop, Qt.QueuedConnection)

        self._worker.command_finished.connect(self.command_finished)
        self._worker.command_failed.connect(self.command_failed)

        self._enabled_requested.connect(self._worker.set_enabled, Qt.QueuedConnection)

        self._gdd_requested.connect(self._worker.set_gdd, Qt.QueuedConnection)
        self._rep_rate_requested.connect(self._worker.set_rep_rate, Qt.QueuedConnection)

        self._worker.enabled_finished.connect(self.enabled_finished)
        self._worker.enabled_failed.connect(self.enabled_failed)

        self._worker.gdd_finished.connect(self.gdd_finished)
        self._worker.gdd_failed.connect(self.gdd_failed)

        self._worker.rep_rate_finished.connect(self.rep_rate_finished)
        self._worker.rep_rate_failed.connect(self.rep_rate_failed)

        self._thread.start()

    @Slot(str, int)
    def enqueue_power(self, laser_name: str, value: float):
        self._enqueue_requested.emit(str(laser_name), float(value))

    @Slot(str, bool)
    def enqueue_enabled(self, laser_name: str, enabled: bool):
        self._enabled_requested.emit(str(laser_name), bool(enabled))
    
    @Slot(str, float)
    def enqueue_gdd(self, laser_name: str, gdd_fs2: float):
        self._gdd_requested.emit(str(laser_name), float(gdd_fs2))


    @Slot(str, float)
    def enqueue_rep_rate(self, laser_name: str, rep_rate_khz: float):
        self._rep_rate_requested.emit(str(laser_name), float(rep_rate_khz))
    
    def close(self):
        try:
            self._stop_requested.emit()
        except Exception:
            pass

        try:
            self._thread.quit()
            self._thread.wait(2000)
        except Exception:
            pass