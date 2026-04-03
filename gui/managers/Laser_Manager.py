from __future__ import annotations

from PySide6.QtCore import QObject, QThread, Signal, Slot, Qt, QTimer


class _LaserCommandWorker(QObject):
    command_finished = Signal(str, int)
    command_failed = Signal(str, int, str)

    def __init__(self, hardware_manager, laser_manager, settings_manager):
        super().__init__()
        self.hardware_manager = hardware_manager
        self.laser_manager = laser_manager
        self.settings_manager = settings_manager

        self._pending_values = {}
        self._running = False
        self._stopping = False

    @Slot(str, int)
    def enqueue(self, laser_name: str, value: int):
        self._pending_values[str(laser_name)] = int(value)

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
            self.command_failed.emit(str(laser_name), int(value), str(e))
        else:
            self.command_finished.emit(str(laser_name), int(value))
        finally:
            self._running = False

        if self._pending_values and not self._stopping:
            QTimer.singleShot(0, self._process_next)

    def _take_next_command(self):
        if not self._pending_values:
            return None

        laser_name = next(iter(self._pending_values.keys()))
        value = self._pending_values.pop(laser_name)
        return str(laser_name), int(value)

    def _apply_power_change(self, laser_name: str, value: int):
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
    command_finished = Signal(str, int)
    command_failed = Signal(str, int, str)

    _enqueue_requested = Signal(str, int)
    _stop_requested = Signal()

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

        self._thread.start()

    @Slot(str, int)
    def enqueue_power(self, laser_name: str, value: int):
        self._enqueue_requested.emit(str(laser_name), int(value))

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