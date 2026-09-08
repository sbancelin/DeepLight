from __future__ import annotations

from threading import Event
from PySide6.QtCore import QObject, Signal, Slot


class MicroscopeBackendBase(QObject):
    """
    Contrat commun de tous les backends microscope.

    Ce backend :
    - reçoit des scan parameters
    - reçoit éventuellement un ExecutionPlan
    - produit des images / événements de progression
    - expose toujours les mêmes attributs runtime
    """

    acquisition_finished = Signal()
    rep_started = Signal(int)
    rep_finished = Signal(int)
    frame_ready = Signal(int, tuple, dict)
    samples_progress = Signal(int)
    stepper_move_requested = Signal(str, float, float, float, str)
    sample_status_updated = Signal(dict)
    sample_image_flush_requested = Signal()

    def __init__(self, scan_parameters=None, parent=None):
        super().__init__(parent)

        # runtime contract attendu par AcquisitionManager
        self.shared_images = {}
        self.acquired = {}
        self.execution_plan = None
        self.acquisition_stop_event = Event()

        self.scan_parameters = {}
        self.channels = ["default"]
        self.repetitions = 1
        self.laser_off_between_rep = False

        self.dim_image_x = 1
        self.dim_image_y = 1

        if scan_parameters is not None:
            self.configure(scan_parameters)

    def configure(self, scan_parameters: dict):
        self.scan_parameters = dict(scan_parameters or {})
        self.channels = list(self.scan_parameters.get("active_channels", [])) or ["default"]
        self.repetitions = int(self.scan_parameters.get("repetitions", 1) or 1)

        self.laser_off_between_rep = bool(
            self.scan_parameters.get("laser_off_between_rep", False)
        )

        self.shared_images = {}
        self.acquired = {}
        self.acquisition_stop_event.clear()

        self.dim_image_x = 1
        self.dim_image_y = 1

    def configure_execution_plan(self, plan):
        self.execution_plan = plan

    @Slot()
    def run_single(self):
        raise NotImplementedError

    @Slot()
    def run_continuous(self):
        raise NotImplementedError

    @Slot()
    def run_acquisition(self):
        raise NotImplementedError

    @Slot()
    def stop(self):
        self.acquisition_stop_event.set()


def validate_backend_contract(backend) -> None:
    """
    Vérifie à chaud que le backend respecte le contrat minimal attendu.
    """
    required_methods = (
        "configure",
        "configure_execution_plan",
        "run_single",
        "run_continuous",
        "run_acquisition",
        "stop",
    )
    required_attrs = (
        "shared_images",
        "acquired",
        "dim_image_x",
        "dim_image_y",
        "repetitions",
        "laser_off_between_rep",
    )
    required_signals = (
        "acquisition_finished",
        "rep_started",
        "rep_finished",
        "frame_ready",
        "samples_progress",
        "stepper_move_requested",
        "sample_status_updated",
        "sample_image_flush_requested",
    )

    missing = []

    for name in required_methods:
        if not hasattr(backend, name) or not callable(getattr(backend, name)):
            missing.append(f"method:{name}")

    for name in required_attrs:
        if not hasattr(backend, name):
            missing.append(f"attr:{name}")

    for name in required_signals:
        if not hasattr(backend, name):
            missing.append(f"signal:{name}")

    if missing:
        raise TypeError(
            f"Backend {backend.__class__.__name__} does not satisfy microscope contract. "
            f"Missing: {', '.join(missing)}"
        )