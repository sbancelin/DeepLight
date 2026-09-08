import numpy as np
from PySide6.QtCore import QObject, Signal, Slot, QThread, QTimer, QMetaObject, Qt
from threading import Lock

from .Microscopes.Mock_Microscope import MockMicroscope
from ..widgets.Log_Widget import logger
from .Microscopes.Microscope_Backend_Base import validate_backend_contract


class AcquisitionManager(QObject):
    acquisition_started = Signal()
    acquisition_stopped = Signal()
    image_updated = Signal(str, np.ndarray)
    shutter_requested = Signal(bool)
    acquisition_frame = Signal(int, tuple, dict)  # rep, idx_tuple, shown_images
    acquisition_done = Signal(object)   # object = dict acquis (self.acquired)
    samples_progress = Signal(int)      # delta samples analogiques réellement consommés
    stepper_move_requested = Signal(str, float, float, float, str)
    sample_status_updated = Signal(dict)

    def __init__(self, parent=None, scan_parameters=None, microscope_backend=None):
        super().__init__(parent)
        self.mode = None
        self.execution_plan = None

        self.microscope = microscope_backend or MockMicroscope(scan_parameters=scan_parameters)
        validate_backend_contract(self.microscope)
        self.lock = Lock()
        self.acquisition_thread = QThread()
        self.microscope.moveToThread(self.acquisition_thread)
        self.acquisition_thread.start()   # thread persistant — ne jamais le tuer entre acquisitions
        self.is_running = False

        self.update_timer = QTimer(self)
        self.update_timer.setInterval(250)
        self.update_timer.timeout.connect(self.update_image)

        self.microscope.acquisition_finished.connect(self._on_backend_acq_finished)
        self.microscope.rep_started.connect(self._on_rep_started)
        self.microscope.rep_finished.connect(self._on_rep_finished)
        self.microscope.frame_ready.connect(self._on_frame_ready)
        self.microscope.samples_progress.connect(self.samples_progress)
        self.microscope.stepper_move_requested.connect(self.stepper_move_requested)
        self.microscope.sample_status_updated.connect(self.sample_status_updated)
        self.microscope.sample_image_flush_requested.connect(self.update_image)

    @Slot(int, object, object)
    def _on_frame_ready(self, rep, idx_tuple, images_by_channel):
        self.update_image()     # force un flush visuel immédiat du dernier paquet
        self.acquisition_frame.emit(rep, tuple(idx_tuple), images_by_channel)      # re-emit to MainWindow
        
    def _invoke(self, method_name: str):
        # Exécute la méthode dans le thread où vit le backend microscope (QueuedConnection)
        QMetaObject.invokeMethod(self.microscope, method_name, Qt.QueuedConnection)
    
    @Slot(int)
    def _on_rep_started(self, rep):
        # si on coupe entre reps -> ré-ouvre au début des reps suivantes
        if self.microscope.laser_off_between_rep and rep > 0:
            self.shutter_requested.emit(True)

    @Slot(int)
    def _on_rep_finished(self, rep):
        if self.microscope.laser_off_between_rep and rep < self.microscope.repetitions - 1:
            self.shutter_requested.emit(False)
    
    def set_scan_parameters(self, scan_parameters: dict):
        """Update the microscope backend with the current parameters."""
        self.microscope.configure(scan_parameters)

    def set_execution_plan(self, plan):
        """
        Inject an ExecutionPlan for the next run.
        """
        self.execution_plan = plan
        self.microscope.configure_execution_plan(plan)

    @Slot()
    def _on_backend_acq_finished(self):
        self.update_image()     # dernier flush de sécurité avant arrêt
        acquired = self.microscope.acquired         # renvoyer les données acquises avant stop

        # stop thread + timers comme un stop normal
        if self.is_running:
            self.stop_acquisition()

        self.acquisition_done.emit(acquired)

    @Slot()
    def start_preview_single(self):
        """Start an acquisition in single-preview mode."""
        if self.is_running:
            return
    
        self.is_running = True
        self.mode = "single"
        self.acquisition_started.emit()
        self.shutter_requested.emit(True)   # Shutter ON
        self._invoke("run_single")
        self.update_timer.start()  # Démarre le timer pour la mise à jour de l'image

    @Slot()
    def start_preview_continuous(self):
        """Loop forever until stopped."""
        if self.is_running:
            return

        self.is_running = True
        self.mode = "continuous"
        self.acquisition_started.emit()
        self.shutter_requested.emit(True)       # shutter ON
        self._invoke("run_continuous")
        self.update_timer.start()

    @Slot()
    def stop_acquisition(self):
        """Stop the running acquisition."""
        if not self.is_running:
            return

        self.microscope.stop()
        # _write_ao_idle_offset is already called in run_single/run_acquisition finally blocks.

        self.update_timer.stop()
        self.shutter_requested.emit(False)
        self.is_running = False
        self.mode = None
        self.acquisition_stopped.emit()

    def close(self):
        """Clean shutdown: stop the acquisition, then kill the thread."""
        if self.is_running:
            self.stop_acquisition()
        self.acquisition_thread.quit()
        self.acquisition_thread.wait(3000)

    @Slot(dict)
    def start_acquisition(self, scan_parameters: dict):
        if self.is_running:
            return

        self.is_running = True
        self.mode = "acquisition"

        # configure microscope
        self.microscope.configure(scan_parameters)
        self.acquisition_started.emit()
        self.shutter_requested.emit(True)       # shutter open au début de l'acquisition
        self._invoke("run_acquisition")
        self.update_timer.start()       # option: refresh UI en continu

    @Slot()
    def update_image(self):
        """Update the image with the acquired data."""
        with self.lock:
            for ch, img in self.microscope.shared_images.items():
                if isinstance(img, np.ndarray):
                    arr = img
                else:
                    arr = np.ndarray(
                        (self.microscope.dim_image_y, self.microscope.dim_image_x),
                        dtype=np.float64,
                        buffer=img.buf
                    )

                self.image_updated.emit(ch, arr.copy())