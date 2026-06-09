from PySide6.QtWidgets import QMainWindow, QFileDialog
from PySide6.QtGui import QIcon, QGuiApplication
from PySide6.QtCore import Slot, QTimer, Qt

import numpy as np
import pyqtgraph as pg
pg.setConfigOptions(imageAxisOrder='row-major')

from .main_window_design import Ui_MainWindowDesign     # Import de l'UI générée par Qt Designer
from .managers.Acquisition_Manager import AcquisitionManager
from .managers.Hardware_Manager import HardwareManager
from .managers.Save_Manager import SaveManager
from .managers.Scan_manager import ScanManager
from .managers.Settings_Manager import SettingsManager
from .managers.Stitching_Manager import StitchingManager
from .managers.Spectro_Manager import SpectroManager
from .managers.Laser_Manager import LaserManager
from .widgets.Log_Widget import logger


class MainWindow(QMainWindow):
    """
    Main window class for the Prog application.
    Attributes:
        guiReadyFlag (bool): Flag indicating if the GUI is ready.
        init_ready (bool): Flag indicating if the initialization is ready.
    """

    def __init__(self, args=None, microscope_backend=None):
        self.guiReadyFlag = False   # Initialise le drapeau de l'UI 
        self.init_ready = False # Initialise le drapeau d'initialisation

        super(MainWindow, self).__init__()

        self.args = args
        self.backend_name = getattr(args, "backend", "mock")
        self.microscope_backend = microscope_backend

        self.ui = Ui_MainWindowDesign()
        self.ui.setupUi(self)  # Appeler setupUi pour initialiser les attributs
        #self.setWindowState(self.windowState() | Qt.WindowMaximized)

        screen = self.screen() or QGuiApplication.primaryScreen()
        available = screen.availableGeometry()

        half_width = available.width() // 2

        self.showNormal()
        self.setGeometry(
            available.x() + half_width,
            available.y() + 30,
            half_width,
            available.height() - 30
        )

        self.settings_manager = SettingsManager()
        
        self.ui.scan_widget.set_settings_manager(self.settings_manager)
        self.ui.positioner_widget.set_settings_manager(self.settings_manager)
        self.ui.laser_widget.set_settings_manager(self.settings_manager)

        self.setDockOptions(
            QMainWindow.AnimatedDocks |
            QMainWindow.AllowNestedDocks
        )
        
        # Récupérer les références aux ImageView et au QSplitter
        self.im_widgets = self.ui.im_widgets
        self.splitter = self.ui.splitter

        # Initialisation des varialbles
        self.preset_dict = {}  # Dictionnaire pour les préréglages
        self.last_images = {}  # channel -> np.ndarray
        self._mouse_move_proxies = {}  # pour garder les références des slots lambda

        self.connect_image_mouse_tracking()
        
        # Définir le titre de la fenêtre
        self.setWindowTitle(f"DeepLight [{self.backend_name.upper()}]")
        self.setWindowIcon(QIcon("./gui/Icons/Microscope_icon_green.ico"))

        self.user_shutter_override = None  # None=no override, True/False=user forced state
        self.hardware = HardwareManager(backend_name=self.backend_name, settings_manager=self.settings_manager, parent=self)
        self.laser_manager = self.hardware.create_laser_manager()
        self.camera_controller = self.hardware.create_camera_controller(parent=self)
        self._connect_camera_controller_signals()
        self._connect_camera_controls()
        self._setup_laser_worker()
        self._connect_laser_controls()
        QTimer.singleShot(0, self._sync_laser_widget_from_hardware)
        QTimer.singleShot(1000, self._sync_laser_widget_from_hardware)

        self.save_manager = SaveManager()
        self.positioner_manager = self.hardware.create_positioner_manager(parent=self)

        self.scan_manager = ScanManager(self)
        self.ui.positioner_widget.set_manager(self.positioner_manager)

        self.spectro_manager = SpectroManager(hardware_manager=self.hardware, positioner_manager=self.positioner_manager, parent=self)

        # --- Spectro UI throttling ---
        self._pending_brillouin_image = None
        self._pending_raman_spectrum = None
        self._pending_raman_wavelengths = None

        self._spectro_brillouin_ui_timer = QTimer(self)
        self._spectro_brillouin_ui_timer.setInterval(500)
        self._spectro_brillouin_ui_timer.timeout.connect(self._flush_pending_brillouin_image)
        self._spectro_brillouin_ui_timer.start()

        self._spectro_raman_ui_timer = QTimer(self)
        self._spectro_raman_ui_timer.setInterval(500)
        self._spectro_raman_ui_timer.timeout.connect(self._flush_pending_raman_spectrum)
        self._spectro_raman_ui_timer.start()

        self._connect_spectro_controls()

        self._rec_saving_active = False
        self._stepper_return_targets_rel = {"x": None, "y": None, "z": None, "p": None}
        self._update_estimated_stack_size()

        # Connection des signaux
        scan_parameters = self._attach_initial_relative_positions(self.ui.scan_widget.get_scan_parameters())
        self.ui.frc_widget.set_current_image_getter(
            lambda channel: self.last_images.get(channel)
        )
        self.acquisition_manager = AcquisitionManager(
            scan_parameters=scan_parameters,
            microscope_backend=self.microscope_backend,
        )
        try:
            self.acquisition_manager.microscope.positioner_manager = self.positioner_manager
        except Exception as e:
            logger.error(f"[MainWindow] positioner_manager injection into microscope failed: {e}")

        self.acquisition_manager.acquisition_started.connect(self.on_acquisition_started)
        self.acquisition_manager.acquisition_stopped.connect(self.on_acquisition_stopped)
        self.acquisition_manager.image_updated.connect(self.update_image_views_with_data)
        self.acquisition_manager.image_updated.connect(self._forward_image_to_frc)
        self.ui.detector_widget.detectors_changed.connect(lambda _: self._update_frc_channels())
        self._update_frc_channels()
        self.ui.frc_widget.request_single_frame.connect(self.on_frc_request_single_frame)
        self.acquisition_manager.shutter_requested.connect(self.on_shutter_requested)
        self.acquisition_manager.shutter_requested.connect(self.hardware.set_shutter)
        self.acquisition_manager.acquisition_done.connect(self.on_acquisition_done)
        self.ui.detector_widget.detectors_changed.connect(lambda _: self._update_estimated_stack_size())
        self.ui.scan_widget.view_update_requested.connect(self.on_view_update_requested)
        self.scan_manager.analog_waveforms_chunk.connect(self.ui.analog_out_widget.update_plots)
        self.scan_manager.analog_waveforms_ready.connect(self.ui.analog_out_widget.show_full_waveforms)
        # Stepper visualizer (Z/P)       
                
        self.acquisition_manager.stepper_move_requested.connect(self.ui.visu_step_widget.on_stepper_move_requested)
        self.scan_manager.xy_frame_duration_ready.connect(self.ui.visu_step_widget.set_xy_frame_hold_ms)
        self.acquisition_manager.stepper_move_requested.connect(self.positioner_manager.move_from_scan)
        
        self.ui.save_widget.sigSaveClicked.connect(self.on_save_clicked)
        self.acquisition_manager.acquisition_frame.connect(self.on_rec_frame)
        self.acquisition_manager.samples_progress.connect(self.on_samples_progress)
        self.acquisition_manager.sample_status_updated.connect(self.on_sample_status_updated)
        self.ui.positioner_widget.stepperPositionForVisualizer.connect(self.ui.visu_step_widget.on_positioner_relative_position_changed)

        self.stitching_manager = StitchingManager(
            positioner_manager=self.positioner_manager,
            acquisition_manager=self.acquisition_manager,
            image_getter=lambda ch: self.last_images.get(ch),
            parent=self
        )
        self._stitching_geom_um = (1.0, 1.0)
        # --- Connexions StitchingManager -> UI / MainWindow ---
        self.stitching_manager.request_preview_single.connect(self._start_stitch_preview_single)
        self.stitching_manager.request_global_stop.connect(self._stop_stitching_hardware)
        self.stitching_manager.mosaic_updated.connect(self._on_stitch_mosaic_updated)
        self.stitching_manager.status_changed.connect(self.ui.stitch_widget.set_status)
        self.stitching_manager.run_started.connect(self._on_stitch_run_started)
        self.stitching_manager.run_finished.connect(self._on_stitch_run_finished)
        self.stitching_manager.run_failed.connect(self._on_stitch_run_failed)
        self.stitching_manager.run_progress.connect(self._on_stitch_run_progress)

        # --- Connexions UI -> StitchingManager ---
        self.ui.stitch_widget.button_acquire.clicked.connect(self.on_stitch_acquire_clicked)
        self.ui.stitch_widget.button_stop.clicked.connect(self.on_stitch_stop_clicked)
        self.ui.stitch_widget.spin_tile_x.valueChanged.connect(self.refresh_stitching_preview_grid)
        self.ui.stitch_widget.spin_tile_y.valueChanged.connect(self.refresh_stitching_preview_grid)
        self.ui.stitch_widget.spin_overlap.valueChanged.connect(self.refresh_stitching_preview_grid)
        self.ui.stitch_widget.cb_show_layout.toggled.connect(self.refresh_stitching_preview_grid)

        QTimer.singleShot(0, self.refresh_stitching_preview_grid)
        QTimer.singleShot(50, self.refresh_stitching_preview_grid)

        self._visualizer_flush_timer = QTimer(self)
        self._visualizer_flush_timer.setInterval(250)
        self._visualizer_flush_timer.timeout.connect(self._flush_visualizers)
                
        self.init_ready = True  # Marque l'initialisation comme terminée     
    
    @Slot()
    def _sync_laser_widget_from_hardware(self):
        try:
            percent = self.hardware.get_laser_power_percent("Cobolt 660")
            self.ui.laser_widget.set_laser_power_value("Cobolt 660", int(round(percent)))

            output_mw = self.laser_manager.get_output_power_mw("Cobolt 660")
            enabled = self.laser_manager.get_enabled("Cobolt 660")

            logger.debug(
                f"[MainWindow] Cobolt synced: "
                f"setpoint={percent:.1f}% output={output_mw:.1f} mW enabled={enabled}"
            )
        except Exception as e:
            logger.warning(f"[MainWindow] Cobolt sync failed: {e}")

    def _setup_laser_worker(self):
        self.laser_command_manager = LaserManager(
            hardware_manager=self.hardware,
            laser_manager=self.laser_manager,
            settings_manager=self.settings_manager,
            parent=self,
        )
    
    def _clear_spectro_ui_buffers(self):
        self._pending_brillouin_image = None
        self._pending_raman_spectrum = None
        self._pending_raman_wavelengths = None
    
    def _connect_spectro_controls(self):
        sp = self.ui.spectro_panel_widget
        sw = self.ui.spectro_widget
        sm = self.spectro_manager

        sp.sigSpectroModeChanged.connect(sw.set_modes)
        sp.sigSpectroModeChanged.connect(self._on_spectro_mode_changed)
        sw.sigBrillouinRoiChanged.connect(sp.set_brillouin_roi_state)
        sp.sigAcquireClicked.connect(self._on_spectro_acquire_clicked)
        sp.sigStopClicked.connect(self._on_spectro_stop_clicked)
        sp.settleTimeChanged.connect(self.ui.scan_widget.set_settle_ms)
        self.ui.scan_widget.set_settle_ms(float(sp._settle_ms))

        # --- Manager spectro ---
        sm.sigImageUpdate.connect(self._on_spectro_image_update)
        sm.sigSpectrumUpdate.connect(self._on_spectro_spectrum_update)
        sm.sigStatusMessage.connect(self._on_spectro_status_changed)
        sm.sigFinished.connect(self._on_spectro_acquisition_finished)
        sm.sigProgress.connect(self._on_spectro_progress_changed)
        sm.sigEta.connect(self._on_spectro_eta_changed)
        sm.sigFailed.connect(self._on_spectro_acquisition_failed)
        sm.sigBrillouinLiveRunningChanged.connect(sw.set_brillouin_live_button_state)
        sm.sigRamanLiveRunningChanged.connect(sw.set_raman_live_button_state)

        # --- Brillouin controls ---
        sw.button_brillouin_snap.clicked.connect(self._on_brillouin_snap_clicked)
        sw.button_brillouin_live.clicked.connect(self._on_brillouin_live_clicked)
        sw.button_brillouin_stop.clicked.connect(self._on_spectro_stop_clicked)
        sw.sigBrillouinReconnectRequested.connect(self._on_brillouin_reconnect_clicked)
        sw.sigBrillouinSaveRequested.connect(self._on_brillouin_save_clicked)
        sw.spin_brillouin_exposure_ms.valueChanged.connect(self._on_brillouin_acq_params_changed)
        sw.combo_brillouin_binning.currentIndexChanged.connect(self._on_brillouin_acq_params_changed)
        sw.spin_brillouin_exposure_ms.valueChanged.connect(sp.set_brillouin_exposure_ms)

        # --- Raman mock controls ---
        sw.button_raman_snap.clicked.connect(self._on_raman_snap_clicked)
        sw.button_raman_live.clicked.connect(self._on_raman_live_clicked)
        sw.button_raman_stop.clicked.connect(self._on_spectro_stop_clicked)
        sw.spin_raman_exposure_ms.valueChanged.connect(sp.set_raman_exposure_ms)

    @Slot(bool, bool)
    def _on_spectro_mode_changed(self, brillouin: bool, raman: bool):
        if not bool(brillouin):
            return

        if self.backend_name == "mock":
            self._on_spectro_status_changed("Simulated (mock)")
            return
        try:
            self._on_spectro_status_changed("Initializing Brillouin camera...")
            self.hardware.ensure_brillouin_camera_ready()
            self._on_spectro_status_changed("Brillouin camera connected")
        except Exception as e:
            self._on_spectro_status_changed(f"Brillouin init failed: {e}")

    @Slot()
    def _on_brillouin_reconnect_clicked(self):
        if self.backend_name == "mock":
            self._on_spectro_status_changed("Simulated (mock)")
            return
        try:
            self._on_spectro_status_changed("Connecting Brillouin camera...")
            self.hardware.ensure_brillouin_camera_ready()
            self._on_spectro_status_changed("Brillouin camera connected")
            logger.info("[Spectro] Brillouin camera connected successfully.")
        except Exception as e:
            self._on_spectro_status_changed(f"Connect failed: {e}")
            logger.error(f"[Spectro] Brillouin connect failed: {e}")

    @Slot()
    def _on_brillouin_save_clicked(self):
        import tifffile
        img = self.ui.spectro_widget.brillouin_image
        if img is None or img.size == 0:
            logger.warning("[Spectro] No Brillouin image to save.")
            return
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Save Brillouin image",
            "",
            "TIFF (*.tif *.tiff);;NumPy (*.npy);;All files (*)",
        )
        if not path:
            return
        try:
            if path.lower().endswith(".npy"):
                import numpy as np
                np.save(path, img)
            else:
                if not (path.lower().endswith(".tif") or path.lower().endswith(".tiff")):
                    path += ".tif"
                tifffile.imwrite(path, img)
            logger.info(f"[Spectro] Brillouin image saved: {path}")
            self._on_spectro_status_changed("Image saved")
        except Exception as e:
            logger.error(f"[Spectro] Save failed: {e}")
            self._on_spectro_status_changed(f"Save failed: {e}")

    @Slot()
    def _on_spectro_acquire_clicked(self):
        if self.spectro_manager.is_running():
            return

        try:
            modes = self.ui.spectro_panel_widget.get_modes()
            acquisition_params = self.ui.spectro_panel_widget.get_acquisition_parameters()
            save_params = self.ui.spectro_panel_widget.get_save_parameters()

            brillouin_params = self.ui.spectro_widget.get_brillouin_parameters()
            raman_params = self.ui.spectro_widget.get_raman_parameters()

            self._clear_spectro_ui_buffers()

            self.ui.spectro_widget.clear_raman_spectrum(show_placeholder=False)
            self.ui.spectro_widget.clear_brillouin_image()
            self.ui.spectro_widget.set_running(True)
            self.ui.spectro_panel_widget.set_running(True)
            self.ui.spectro_panel_widget.reset_progress()

            self.spectro_manager.start_mapping(
                scan_parameters=acquisition_params,
                modes=modes,
                brillouin_params=brillouin_params,
                raman_params=raman_params,
                save_params=save_params,
            )
        except Exception as e:
            self._on_spectro_status_changed(f"Spectro start failed: {e}")
            self.ui.spectro_panel_widget.set_running(False)
            self.ui.spectro_widget.set_running(False)

    @Slot()
    def _on_spectro_stop_clicked(self):
        try:
            self.spectro_manager.stop_mapping()
        except Exception as e:
            logger.debug(f"[MainWindow] ignored exception: {e}")

        try:
            self.spectro_manager.stop_all_live()
        except Exception as e:
            logger.debug(f"[MainWindow] ignored exception: {e}")

        try:
            self.positioner_manager.stop_all()
        except Exception as e:
            logger.debug(f"[MainWindow] ignored exception: {e}")

        self._clear_spectro_ui_buffers()

        try:
            self.ui.spectro_panel_widget.set_running(False)
            self.ui.spectro_widget.set_running(False)
        except Exception as e:
            logger.debug(f"[MainWindow] ignored exception: {e}")

        self._on_spectro_status_changed("Spectro stopped")

    @Slot()
    def _on_brillouin_snap_clicked(self):
        try:
            params = self.ui.spectro_widget.get_brillouin_parameters()
            self.spectro_manager.snap_brillouin(params)
        except Exception as e:
            logger.error(f"[Spectro] Brillouin snap failed: {e}")
            self._on_spectro_status_changed("Error")


    @Slot()
    def _on_brillouin_live_clicked(self):
        try:
            if self.spectro_manager.is_brillouin_live_running():
                self.spectro_manager.stop_live_brillouin()
            else:
                params = self.ui.spectro_widget.get_brillouin_parameters()
                self.spectro_manager.start_live_brillouin(params)
        except Exception as e:
            logger.error(f"[Spectro] Brillouin live failed: {e}")
            self._on_spectro_status_changed("Error")

    @Slot()
    def _on_brillouin_acq_params_changed(self):
        if self.spectro_manager.is_brillouin_live_running():
            params = self.ui.spectro_widget.get_brillouin_parameters()
            self.spectro_manager.update_brillouin_live_params(params)

    @Slot()
    def _on_raman_snap_clicked(self):
        try:
            params = self.ui.spectro_widget.get_raman_parameters()
            self.spectro_manager.snap_raman(params)
        except Exception as e:
            logger.error(f"[Spectro] Raman snap failed: {e}")
            self._on_spectro_status_changed("Error")


    @Slot()
    def _on_raman_live_clicked(self):
        try:
            if self.spectro_manager.is_raman_live_running():
                self.spectro_manager.stop_live_raman()
            else:
                params = self.ui.spectro_widget.get_raman_parameters()
                self.spectro_manager.start_live_raman(params)
        except Exception as e:
            logger.error(f"[Spectro] Raman live failed: {e}")
            self._on_spectro_status_changed("Error")

    @Slot(str)
    def _on_spectro_status_changed(self, text: str):
        modes = self.ui.spectro_panel_widget.get_modes()
        if bool(modes.get("brillouin", False)):
            self.ui.spectro_widget.set_brillouin_status(str(text))
        if bool(modes.get("raman", False)):
            self.ui.spectro_widget.set_raman_status(str(text))

    @Slot(object)
    def _on_spectro_image_update(self, image):
        try:
            self._pending_brillouin_image = np.asarray(image, dtype=np.float32)
        except Exception as e:
            self._on_spectro_status_changed(f"Brillouin buffer failed: {e}")

    @Slot()
    def _flush_pending_brillouin_image(self):
        if self._pending_brillouin_image is None:
            return

        try:
            image = self._pending_brillouin_image
            self._pending_brillouin_image = None
            self.ui.spectro_widget.set_brillouin_image(image)
        except Exception as e:
            self._on_spectro_status_changed(f"Brillouin display failed: {e}")

    @Slot(object)
    def _on_spectro_spectrum_update(self, spectrum):
        try:
            wavelengths = self.spectro_manager.dataset.get("raman_wavelengths", None)
            if wavelengths is None:
                return

            self._pending_raman_spectrum = np.asarray(spectrum, dtype=np.float32)
            self._pending_raman_wavelengths = np.asarray(wavelengths, dtype=np.float32)
        except Exception as e:
            self._on_spectro_status_changed(f"Spectrum buffer failed: {e}")

    @Slot()
    def _flush_pending_raman_spectrum(self):
        if self._pending_raman_spectrum is None or self._pending_raman_wavelengths is None:
            return

        try:
            spectrum = self._pending_raman_spectrum
            wavelengths = self._pending_raman_wavelengths

            self._pending_raman_spectrum = None
            self._pending_raman_wavelengths = None

            self.ui.spectro_widget.set_raman_spectrum(wavelengths, spectrum)
        except Exception as e:
            self._on_spectro_status_changed(f"Raman display failed: {e}")

    @Slot(float, float)
    def _on_spectro_eta_changed(self, elapsed_s: float, remaining_s: float):
        try:
            self.ui.spectro_panel_widget.update_eta(elapsed_s, remaining_s)
        except Exception as e:
            logger.debug(f"[MainWindow] ignored exception: {e}")

    @Slot(int, int)
    def _on_spectro_progress_changed(self, done: int, total: int):
        try:
            self.ui.spectro_panel_widget.set_progress(done, total)
        except Exception as e:
            logger.debug(f"[MainWindow] ignored exception: {e}")

        self._on_spectro_status_changed(f"Spectro {done}/{total}")

    @Slot(object)
    def _on_spectro_acquisition_finished(self, dataset):
        self._flush_pending_brillouin_image()
        self._flush_pending_raman_spectrum()

        self.ui.spectro_panel_widget.set_running(False)
        self.ui.spectro_widget.set_running(False)

        try:
            total = len(self.spectro_manager.pixel_list)
            self.ui.spectro_panel_widget.set_progress(total, total)
        except Exception as e:
            logger.debug(f"[MainWindow] ignored exception: {e}")

        try:
            save_params = self.ui.spectro_panel_widget.get_save_parameters()
            folder = save_params.get("folder", "")
            filename = save_params.get("filename", "")
            comment = save_params.get("comment", "")
            file_format = save_params.get("format", "OME-TIFF")
        except Exception:
            folder = ""
            filename = ""
            comment = ""
            file_format = "OME-TIFF"

        try:
            if not filename:
                filename = "SPECTRO"

            path = self.save_manager.save_spectro_dataset(
                folder=folder,
                filename=filename,
                comment=comment,
                dataset=dataset,
                fmt=file_format,
            )
            self._on_spectro_status_changed(f"Spectro saved: {path}")
        except Exception as e:
            self._on_spectro_status_changed(f"Spectro save failed: {e}")

    @Slot(str)
    def _on_spectro_acquisition_failed(self, message: str):
        self._clear_spectro_ui_buffers()

        self.ui.spectro_panel_widget.set_running(False)
        self.ui.spectro_widget.set_running(False)

        logger.error(f"[Spectro] {message}")
        self._on_spectro_status_changed("Error")
    
    def _connect_camera_controller_signals(self):
        self.camera_controller.frame_ready.connect(self._on_camera_frame_ready)
        self.camera_controller.status_changed.connect(self.ui.camera_widget.set_status)
        self.camera_controller.running_changed.connect(self._on_camera_running_changed)

    def _connect_camera_controls(self):
        cw = self.ui.camera_widget

        # boutons d'acquisition
        cw.button_snap.clicked.connect(self._on_camera_snap_clicked)
        cw.button_live.toggled.connect(self._on_camera_live_clicked)
        cw.button_stop.clicked.connect(self._on_camera_stop_clicked)
        cw.button_reset.clicked.connect(self._on_camera_reset_clicked)
        cw.sigReconnectRequested.connect(self._on_camera_reconnect_clicked)
        cw.sigSaveRequested.connect(self._on_camera_save_clicked)
        cw.sigRoiParamsChanged.connect(self._push_camera_parameters)

        # paramètres — pushés au controller à chaque changement
        cw.spin_exposure_ms.valueChanged.connect(self._push_camera_parameters)
        cw.cb_auto_exposure.toggled.connect(self._push_camera_parameters)
        cw.combo_binning.currentTextChanged.connect(self._push_camera_parameters)
        cw.cb_roi_enabled.toggled.connect(self._push_camera_parameters)
        cw.spin_roi_x.valueChanged.connect(self._push_camera_parameters)
        cw.spin_roi_y.valueChanged.connect(self._push_camera_parameters)
        cw.spin_roi_width.valueChanged.connect(self._push_camera_parameters)
        cw.spin_roi_height.valueChanged.connect(self._push_camera_parameters)

    @Slot()
    def _push_camera_parameters(self, *_args):
        try:
            self.camera_controller.apply_parameters(self.ui.camera_widget.get_parameters())
        except Exception as e:
            self.ui.camera_widget.set_status(f"Camera param error: {e}")

    @Slot()
    def _on_camera_snap_clicked(self):
        try:
            self.camera_controller.snap(self.ui.camera_widget.get_parameters())
        except Exception as e:
            self.ui.camera_widget.set_status(f"Snap failed: {e}")

    @Slot(bool)
    def _on_camera_live_clicked(self, checked: bool):
        try:
            if checked:
                self.camera_controller.start_live(self.ui.camera_widget.get_parameters())
            else:
                self.camera_controller.stop_live()
        except Exception as e:
            self.ui.camera_widget.set_status(f"Live failed: {e}")
            self.ui.camera_widget.set_live_button_state(False)

    @Slot()
    def _on_camera_stop_clicked(self):
        try:
            self.camera_controller.stop_live()
        except Exception as e:
            self.ui.camera_widget.set_status(f"Stop failed: {e}")

    @Slot()
    def _on_camera_reset_clicked(self):
        try:
            self.camera_controller.stop_live()
        except Exception as e:
            logger.debug(f"[MainWindow] ignored exception: {e}")

        try:
            self.ui.camera_widget.reset_controls()
            self.camera_controller.apply_parameters(self.ui.camera_widget.get_parameters())
            self.ui.camera_widget.set_status("Idle")
        except Exception as e:
            self.ui.camera_widget.set_status(f"Reset failed: {e}")

    @Slot()
    def _on_camera_reconnect_clicked(self):
        try:
            self.ui.camera_widget.set_status("Reconnecting...")
            self.camera_controller.reconnect_camera()
        except Exception as e:
            self.ui.camera_widget.set_status(f"Reconnect failed: {e}")

    @Slot()
    def _on_camera_save_clicked(self):
        import tifffile
        img = self.ui.camera_widget.current_image
        if img is None or img.size == 0:
            self.ui.camera_widget.set_status("No image to save")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Camera image", "",
            "TIFF (*.tif *.tiff);;NumPy (*.npy);;All files (*)",
        )
        if not path:
            return
        try:
            if path.lower().endswith(".npy"):
                import numpy as np
                np.save(path, img)
            else:
                if not (path.lower().endswith(".tif") or path.lower().endswith(".tiff")):
                    path += ".tif"
                tifffile.imwrite(path, img)
            self.ui.camera_widget.set_status("Image saved")
        except Exception as e:
            self.ui.camera_widget.set_status(f"Save failed: {e}")

    @Slot(bool)
    def _on_camera_running_changed(self, running: bool):
        self.ui.camera_widget.set_running(bool(running))
        self.ui.camera_widget.set_live_button_state(bool(running))

    @Slot(object)
    def _on_camera_frame_ready(self, img):
        try:
            cw = self.ui.camera_widget
            if img is not None:
                h, w = img.shape[:2]
                # Only update the native sensor shape from full, unbinned, un-cropped frames
                roi_off = not cw.cb_roi_enabled.isChecked()
                binning_off = cw.combo_binning.currentText() == "1x1"
                if roi_off and binning_off:
                    cw.set_camera_full_shape(h, w)
            cw.set_image(img)
            self._refresh_analysis_widgets_for_channel("Camera")
        except Exception as e:
            self.ui.camera_widget.set_status(f"Display failed: {e}")

    def _refresh_analysis_widgets_for_channel(self, channel: str):
        try:
            lpw = self.ui.line_profile_widget
            if lpw.toggle_btn.isChecked() and lpw.channel_combo.currentText() == channel:
                lpw.update_profile()
        except Exception:
            pass
        try:
            hw = self.ui.histogram_widget
            if (hw.toggle_btn.isChecked()
                    and hw.rect_roi is not None
                    and hw.rect_roi.isVisible()
                    and hw.channel_combo.currentText() == channel):
                hw.update_histogram()
        except Exception:
            pass

    def closeEvent(self, event):
        try:
            if getattr(self, "laser_command_manager", None) is not None:
                self.laser_command_manager.close()
        except Exception as e:
            logger.debug(f"[MainWindow] ignored exception: {e}")
        
        try:
            self._spectro_brillouin_ui_timer.stop()
        except Exception as e:
            logger.debug(f"[MainWindow] ignored exception: {e}")

        try:
            self._spectro_raman_ui_timer.stop()
        except Exception as e:
            logger.debug(f"[MainWindow] ignored exception: {e}")

        try:
            self.spectro_manager.stop_all_live()
        except Exception as e:
            logger.debug(f"[MainWindow] ignored exception: {e}")

        try:
            speed = max(0.01, float(self.positioner_manager.get_max_speed("x")))
            self.positioner_manager.move_xy_to_rel(0.0, 0.0, speed, speed)
            wait_fn = getattr(self.positioner_manager, "wait_until_xy_reached", None)
            if callable(wait_fn):
                wait_fn(0.0, 0.0, timeout_s=30.0)
            logger.info("[MainWindow] XY stage returned to origin.")
        except Exception as e:
            logger.warning(f"[MainWindow] XY return to origin at close failed: {e}")

        try:
            self.acquisition_manager.close()
        except Exception as e:
            logger.debug(f"[MainWindow] ignored exception: {e}")

        try:
            self.hardware.close()
        except Exception as e:
            logger.debug(f"[MainWindow] ignored exception: {e}")

        super().closeEvent(event)
    
    @Slot()
    def on_stitch_acquire_clicked(self):
        if self.stitching_manager.is_running():
            return

        scan_params = self.ui.scan_widget.get_scan_parameters()
        scan_params = self._attach_detector_specs(scan_params)
        channels = list(scan_params.get("active_channels", [])) or ["default"]
        scan_params["repetitions"] = 1

        # v1 : XY uniquement
        active_axes = list(scan_params.get("active_axes", []))
        if len(active_axes) < 2:
            self.ui.stitch_widget.set_status("Need at least 2 active scan axes.")
            return

        try:
            rows = [row for row in scan_params.get("rows", []) if row.get("axis") != "None"]
            row_x = next(row for row in rows if str(row.get("axis", "")).startswith("X"))
            row_y = next(row for row in rows if str(row.get("axis", "")).startswith("Y"))
        except Exception:
            self.ui.stitch_widget.set_status("Stitching v1 requires XY scan axes.")
            return

        tile_w_um = float(row_x.get("size_um", 1.0) or 1.0)
        tile_h_um = float(row_y.get("size_um", 1.0) or 1.0)
        tile_w_px = int(row_x.get("pixels", 1) or 1)
        tile_h_px = int(row_y.get("pixels", 1) or 1)

        mosaic_params = self.ui.stitch_widget.get_parameters()
        channels = list(self.ui.detector_widget.detectors) or ["default"]
        if mosaic_params["channel"] not in channels:
            mosaic_params["channel"] = channels[0]

        overlap_px = int(mosaic_params.get("overlap_px", 0) or 0)

        mosaic_w_um = mosaic_params["tiles_x"] * tile_w_um - max(0, mosaic_params["tiles_x"] - 1) * (
            tile_w_um * overlap_px / float(tile_w_px)
        )
        mosaic_h_um = mosaic_params["tiles_y"] * tile_h_um - max(0, mosaic_params["tiles_y"] - 1) * (
            tile_h_um * overlap_px / float(tile_h_px)
        )
        self._stitching_geom_um = (mosaic_w_um, mosaic_h_um)

        self.stitching_manager.start_run(mosaic_params, scan_params)

    @Slot()
    def on_stitch_stop_clicked(self):
        self.stitching_manager.stop_run()


    @Slot(dict)
    def _start_stitch_preview_single(self, scan_parameters: dict):
        """
        Lance une tuile unitaire pour le stitching.
        On réutilise exactement le pipeline preview_single existant.
        """
        self.acquisition_manager.set_scan_parameters(scan_parameters)
        self.start_scan_outputs(scan_parameters, mode="acquisition")
        self.acquisition_manager.start_preview_single()

    @Slot()
    def _stop_stitching_hardware(self):
        try:
            self.acquisition_manager.stop_acquisition()
        except Exception as e:
            logger.debug(f"[MainWindow] ignored exception: {e}")

        try:
            self.positioner_manager.stop_all()
        except Exception as e:
            logger.debug(f"[MainWindow] ignored exception: {e}")

    @Slot(object)
    def _on_stitch_mosaic_updated(self, mosaic):
        w_um, h_um = self._stitching_geom_um
        self.ui.stitch_widget.set_image(mosaic, width_um=w_um, height_um=h_um)

    def refresh_stitching_preview_grid(self):
        try:
            scan_params = self.ui.scan_widget.get_scan_parameters()
            self.ui.stitch_widget.set_mosaic_layout_preview(scan_params)
        except Exception as e:
            logger.debug(f"[MainWindow] ignored exception: {e}")

    @Slot()
    def _on_stitch_run_started(self):
        self.ui.stitch_widget.set_running(True)
        self.ui.stitch_widget.set_status("Mosaic started...")

    @Slot()
    def _on_stitch_run_finished(self):
        self.ui.stitch_widget.set_running(False)
        self.ui.stitch_widget.set_status("Mosaic finished.")

    @Slot(str)
    def _on_stitch_run_failed(self, message: str):
        self.ui.stitch_widget.set_running(False)
        self.ui.stitch_widget.set_status(f"Error: {message}")

    @Slot(int, int)
    def _on_stitch_run_progress(self, done: int, total: int):
        self.ui.stitch_widget.set_status(f"Tiles: {done}/{total}")
        
    def _capture_stepper_return_targets(self):
        """
        Mémorise la position relative initiale de X, Y, Z et P
        au début d'une acquisition.
        """
        for axis in ("x", "y", "z", "p"):
            try:
                self._stepper_return_targets_rel[axis] = float(self.positioner_manager.get_rel_pos(axis))
            except Exception:
                self._stepper_return_targets_rel[axis] = None
    
    
    def _attach_initial_relative_positions(self, scan_parameters: dict) -> dict:
        """
        Attache au dict de scan les positions relatives initiales des axes stepper.
        Ne modifie pas le dict d'origine : renvoie une copie.
        """
        params = dict(scan_parameters or {})

        initial = dict(params.get("initial_relative_positions", {}) or {})

        axis_map = {
            "X-Stage": "x",
            "Y-Stage": "y",
            "Z-Vcoil": "z",
            "Polarization": "p",
        }

        for scan_axis_name, pos_axis in axis_map.items():
            if scan_axis_name in initial:
                continue

            try:
                initial[scan_axis_name] = float(self.positioner_manager.get_rel_pos(pos_axis))
            except Exception:
                initial[scan_axis_name] = 0.0

        params["initial_relative_positions"] = initial
        return params
    
    def _attach_detector_specs(self, scan_parameters: dict) -> dict:
        """
        Attache au dict de scan:
        - active_channels
        - detector_channels (description structurée)
        """
        params = dict(scan_parameters or {})

        try:
            detector_specs = self.ui.detector_widget.get_detector_specs()
        except Exception:
            detector_specs = []

        params["detector_channels"] = detector_specs
        params["active_channels"] = [
            str(d.get("name"))
            for d in detector_specs
            if bool(d.get("enabled", True))
        ] or ["default"]

        return params
    
    def _channel_unit_label(self, channel: str) -> str:
        """
        Retourne l'unité affichée pour un canal.
        - analog  -> V
        - digital -> counts
        """
        try:
            specs = self.ui.detector_widget.get_detector_specs()
        except Exception:
            specs = []

        for spec in specs:
            if str(spec.get("name")) != str(channel):
                continue
            kind = str(spec.get("kind", "analog"))
            return "V·µs" if kind == "analog" else "counts"

        return "value"
    
    def start_scan_outputs(self, scan_parameters: dict, mode: str):
        """Prépare le plan analog/stepper; le temps réel viendra de l'acquisition."""
        self.ui.analog_out_widget.reset_buffer()
        try:
            self.ui.visu_step_widget.reset_buffer()
        except Exception as e:
            logger.debug(f"[MainWindow] ignored exception: {e}")

        # On utilise les positions initiales déjà capturées si elles existent,
        # sinon on lit directement le positioner.
        try:
            z0 = self._stepper_return_targets_rel.get("z")
            if z0 is None:
                z0 = float(self.positioner_manager.get_rel_pos("z"))
            self.ui.visu_step_widget.set_initial_position("Z-Vcoil", float(z0))
        except Exception as e:
            logger.debug(f"[MainWindow] ignored exception: {e}")

        try:
            p0 = self._stepper_return_targets_rel.get("p")
            if p0 is None:
                p0 = float(self.positioner_manager.get_rel_pos("p"))
            self.ui.visu_step_widget.set_initial_position("Polarization", float(p0))
        except Exception as e:
            logger.debug(f"[MainWindow] ignored exception: {e}")

        # ---- config FRC depuis ScanWidget / ScanParams dict ----
        try:
            channels = list(scan_parameters.get("active_channels", []))
            self.ui.frc_widget.set_active_channels(channels)
        except Exception as e:
            logger.warning(f"[MainWindow] FRC set_active_channels error: {e}")

        try:
            step_sizes = scan_parameters.get("step_sizes", {})
            pix_x = 0.0
            pix_y = 0.0

            for axis_name, step_um in step_sizes.items():
                if str(axis_name).startswith("X-"):
                    pix_x = float(step_um or 0.0)
                elif str(axis_name).startswith("Y-"):
                    pix_y = float(step_um or 0.0)

            if pix_x > 0 and pix_y > 0:
                self.ui.frc_widget.set_pixel_size_um(0.5 * (pix_x + pix_y))
            elif pix_x > 0:
                self.ui.frc_widget.set_pixel_size_um(pix_x)
            elif pix_y > 0:
                self.ui.frc_widget.set_pixel_size_um(pix_y)
        except Exception as e:
            logger.warning(f"[MainWindow] FRC set_pixel_size_um error: {e}")

        scan_kind = str(scan_parameters.get("scan_kind", "laser") or "laser")

        if scan_kind == "sample":
            try:
                total_pixels = int(scan_parameters.get("total_pixels", 0) or 0)
            except Exception:
                total_pixels = 0

            if total_pixels <= 0:
                total_pixels = 1
                for row in scan_parameters.get("rows", []):
                    if row.get("axis") != "None":
                        total_pixels *= int(row.get("pixels", 1) or 1)

            dwell_s = float(scan_parameters.get("dwell_time", 0.0) or 0.0)
            settle_s = float(scan_parameters.get("sample_settle_time_s", 0.0) or 0.0)
            reps = int(scan_parameters.get("repetitions", 1) or 1)
            delay_s = float(scan_parameters.get("delay_between_rep", 0.0) or 0.0)

            rows = [row for row in scan_parameters.get("rows", []) if row.get("axis") != "None"]
            xy_pixels = 1
            for row in rows[:2]:
                xy_pixels *= int(row.get("pixels", 1) or 1)

            extra_frames = 1
            if mode == "acquisition":
                for row in rows[2:]:
                    extra_frames *= int(row.get("pixels", 1) or 1)
            else:
                reps = 1

            effective_pixel_s = max(0.0, dwell_s) + max(0.0, settle_s)
            total_ms = (effective_pixel_s * total_pixels * reps + max(0, reps - 1) * delay_s) * 1e3

            try:
                self.ui.visu_step_widget.set_run_total_ms(total_ms)
                self.ui.analog_out_widget.set_run_total_ms(total_ms)
            except Exception as e:
                logger.debug(f"[MainWindow] ignored exception: {e}")

            # IMPORTANT:
            # en mode sample scan, on ne pousse pas de ExecutionPlan galvo.
            # On ne fait PAS set_execution_plan(None), sinon le microscope mock
            # repasse ses buffers image à 1x1 via configure_execution_plan(None).

            try:
                self.scan_manager.stop_stream()
            except Exception as e:
                logger.debug(f"[MainWindow] ignored exception: {e}")

            try:
                self._visualizer_flush_timer.stop()
            except Exception as e:
                logger.debug(f"[MainWindow] ignored exception: {e}")

        else:
            self.scan_manager.prepare_run(scan_parameters, mode=mode)

            total_ms = None

            try:
                plan = self.scan_manager.get_last_execution_plan()
                if plan is not None and float(plan.sample_rate_hz) > 0:
                    total_ms = (float(plan.total_samples) / float(plan.sample_rate_hz)) * 1e3
                    self.ui.visu_step_widget.set_run_total_ms(total_ms)
                    self.ui.analog_out_widget.set_run_total_ms(total_ms)
            except Exception as e:
                logger.debug(f"[MainWindow] ignored exception: {e}")

            try:
                self.acquisition_manager.set_execution_plan(self.scan_manager.get_last_execution_plan())
            except Exception as e:
                logger.warning(f"[MainWindow] set_execution_plan error: {e}")

            self._visualizer_flush_timer.start()
    
    @Slot(int)
    def on_samples_progress(self, delta_samples: int):
        """
        Horloge réelle du run: appelée à partir du thread d'acquisition.
        """
        try:
            scan_kind = str(self.ui.scan_widget.get_scan_kind())
        except Exception:
            scan_kind = "laser"

        if scan_kind == "sample":
            return

        try:
            self.scan_manager.consume_samples(int(delta_samples))
        except Exception as e:
            logger.warning(f"[MainWindow] consume_samples error: {e}")
    
    @Slot(dict)
    def on_sample_status_updated(self, info: dict):
        try:
            if not isinstance(info, dict):
                return

            if str(info.get("mode", "")) != "sample":
                return

            if bool(info.get("done", False)):
                elapsed_s = float(info.get("elapsed_s", 0.0) or 0.0)
                px_s = float(info.get("pixels_per_s", 0.0) or 0.0)
                total_px = int(info.get("pixel_total", 0) or 0)

                self.statusBar().showMessage(
                    f"Sample scan done - {total_px} px in {elapsed_s:.2f} s ({px_s:.1f} px/s)",
                    5000
                )
                return

            line_index = int(info.get("line_index", 0) or 0)
            line_count = int(info.get("line_count", 0) or 0)
            pixel_done = int(info.get("pixel_done", 0) or 0)
            pixel_total = int(info.get("pixel_total", 0) or 0)
            x_um = float(info.get("x_um", 0.0) or 0.0)
            y_um = float(info.get("y_um", 0.0) or 0.0)

            if pixel_total > 0:
                msg = (
                    f"Sample scan - line {line_index + 1}/{line_count} | "
                    f"{pixel_done}/{pixel_total} px | "
                    f"X={x_um:.2f} µm, Y={y_um:.2f} µm"
                )
            else:
                msg = (
                    f"Sample scan - line {line_index + 1}/{line_count} | "
                    f"X={x_um:.2f} µm, Y={y_um:.2f} µm"
                )

            self.statusBar().showMessage(msg)

        except Exception as e:
            logger.warning(f"[MainWindow] on_sample_status_updated error: {e}")
    
    @Slot()
    def _flush_visualizers(self):
        try:
            self.scan_manager.flush_pending_buffers()
        except Exception as e:
            logger.warning(f"[MainWindow] flush_visualizers error: {e}")
    
    @Slot(str, str, str, str)
    def on_save_clicked(self, folder: str, filename: str, file_format: str, comment: str):
        # images affichées = self.last_images (déjà transposées => exactement ce que tu vois)
        channels = list(self.ui.im_widgets.keys()) or ["default"]

        images = {}
        for ch in channels:
            img = self.last_images.get(ch)
            if img is not None:
                images[ch] = np.asarray(img)

        if not images:
            logger.warning("[Save] No image to save (empty last_images).")
            return

        scan_params = self.ui.scan_widget.get_scan_parameters()
        scan_params = self._attach_detector_specs(scan_params)

        try:
            path = self.save_manager.save_current_view(
                folder=folder,
                filename=filename,
                fmt=file_format,
                comment=comment,
                images_by_channel=images,
                scan_params=scan_params
            )
            logger.info(f"[Save] Saved view to: {path}")
        except Exception as e:
            logger.error(f"[Save] ERROR: {e}")
    
    def _get_display_axes_from_scan_params(self, scan_parameters: dict):
        """
        Retourne les deux axes réellement affichés dans l'image.
        """
        rows = [row for row in scan_parameters.get("rows", []) if row.get("axis") != "None"]

        if len(rows) >= 2:
            return rows[0], rows[1]

        if len(rows) == 1:
            return rows[0], None

        return None, None
    
    def _extract_view_geometry(self, scan_parameters: dict):
        row_x, row_y = self._get_display_axes_from_scan_params(scan_parameters)

        if row_x is not None and row_y is not None:
            pix_x = int(row_x.get("pixels", 1) or 1)
            pix_y = int(row_y.get("pixels", 1) or 1)

            width_um = float(row_x.get("size_um", 1.0) or 1.0)
            height_um = float(row_y.get("size_um", 1.0) or 1.0)
        else:
            pix_x = 1
            pix_y = 1
            width_um = 1.0
            height_um = 1.0

        return pix_x, pix_y, width_um, height_um

    def _apply_physical_scale(self, im_widget, image, scan_parameters: dict):
        row_x, row_y = self._get_display_axes_from_scan_params(scan_parameters)

        if row_x is not None:
            width_um = float(row_x.get("size_um", 1.0) or 1.0)
        else:
            width_um = 1.0

        if row_y is not None:
            height_um = float(row_y.get("size_um", 1.0) or 1.0)
        else:
            height_um = 1.0

        img_h, img_w = image.shape[:2]

        scale_x = width_um / float(img_w) if img_w > 0 else 1.0
        scale_y = height_um / float(img_h) if img_h > 0 else 1.0

        img_item = im_widget.getImageItem()
        img_item.setTransform(pg.QtGui.QTransform.fromScale(scale_x, scale_y))
        img_item.setPos(0, 0)

    @Slot(object)
    def on_view_update_requested(self, params):
        params = self._attach_detector_specs(params)
        channels = list(params.get("active_channels", [])) or ["default"]
        params = self._attach_initial_relative_positions(params)

        self._update_estimated_stack_size()
        self.acquisition_manager.set_scan_parameters(params)
        self.start_scan_outputs(params, mode="preview_single")

        pix_x, pix_y, width_um, height_um = self._extract_view_geometry(params)
        empty = np.zeros((pix_y, pix_x), dtype=np.float32)

        self.ui.currentImage = empty
        self.ui.update_scan_layout(channels)

        self.im_widgets = self.ui.im_widgets

        for ch in channels:
            im_widget = self.ui.im_widgets.get(ch) or self.ui.im_widgets.get("default")
            if im_widget is None:
                continue

            im_widget.setImage(empty, autoLevels=False, autoRange=False, autoHistogramRange=False)
            self._apply_physical_scale(im_widget, empty, params)

            autoscale = bool(getattr(self.ui, "channel_autoscale", {}).get(ch, True))
            if autoscale:
                im_widget.autoLevels()

            lock_checked = bool(getattr(self.ui, "channel_lock", {}).get(ch, True))
            im_widget.getView().setAspectLocked(lock_checked)

            self.last_images[ch] = empty

        self._mouse_move_proxies.clear()
        self.connect_image_mouse_tracking()
    
    def _update_controls_enabled(self, running: bool):
        # running=True => on bloque les autres actions
        self.ui.pushButton_previewSingle.setEnabled(not running)
        self.ui.pushButton_previewcontinuous.setEnabled(not running)
        self.ui.pushButton_acquisitionStart.setEnabled(not running)

        # mais on garde Stop toujours actif
        self.ui.pushButton_stop.setEnabled(True)
        # shutter: tu veux pouvoir le fermer pendant l’acq => on le laisse activé
        self.ui.pushButton_shutter.setEnabled(True)
    
    def _update_estimated_stack_size(self):
        sp = self.ui.scan_widget.get_scan_parameters()
        channels = list(self.ui.detector_widget.detectors) or ["default"]

        rows = [row for row in sp.get("rows", []) if row.get("axis") != "None"]

        if len(rows) >= 2:
            dim_x = int(rows[0].get("pixels", 1) or 1)
            dim_y = int(rows[1].get("pixels", 1) or 1)
        else:
            dim_x = 1
            dim_y = 1

        # axes 3/4
        n3 = int(sp["pixel_values"][2]) if len(sp.get("active_axes", [])) >= 3 else 1
        n4 = int(sp["pixel_values"][3]) if len(sp.get("active_axes", [])) >= 4 else 1

        reps = int(sp.get("repetitions", 1))
        C = max(1, len(channels))

        # Hypothèse d’écriture (on sauve float32) : 4 bytes/pixel
        bytes_per_pixel = 4

        # stack = reps * n3 * n4 * C * (shownY * shownX)
        # shown shape = (dim_x, dim_y)
        total_bytes = reps * n3 * n4 * C * dim_x * dim_y * bytes_per_pixel

        def fmt_bytes(n: int) -> str:
            if n < 1024:
                return f"{n} B"
            if n < 1024**2:
                return f"{n/1024:.1f} KB"
            if n < 1024**3:
                return f"{n/1024**2:.1f} MB"
            return f"{n/1024**3:.2f} GB"

        txt = f"{fmt_bytes(total_bytes)}"  #(C={C}, reps={reps}, a3={n3}, a4={n4}, XY={dim_x}×{dim_y})"
        self.ui.save_widget.set_estimated_size_text(txt)
    
    @Slot(int, tuple, object)
    def on_rec_frame(self, rep: int, idx_tuple: tuple, images_by_channel: dict):
        if not self._rec_saving_active:
            return
        try:
            self.save_manager.append_rec_frame(rep, idx_tuple, images_by_channel)
        except Exception as e:
            logger.error(f"[REC] append frame error: {e}")
    
    @Slot(object)
    def on_acquisition_done(self, data):
        logger.debug(f"ACQ DONE. Stored reps: {len(data)}")
        
    @Slot(bool)
    def on_shutter_requested(self, open_: bool):
        """
        Demande venant de l'acquisition (logique auto).
        Si l'utilisateur a forcé un état, on ne l'écrase pas.
        """
        if self.user_shutter_override is not None:
            # L'utilisateur a la main pendant l'acquisition
            open_ = self.user_shutter_override

        self.set_shutter_state(open_)

    def set_shutter_state(self, open_: bool):
        btn = self.ui.pushButton_shutter
        btn.blockSignals(True)
        btn.setChecked(open_)
        btn.blockSignals(False)

        if open_:
            btn.setIcon(QIcon("./gui/Icons/laser_icon_open.svg"))
        else:
            btn.setIcon(QIcon(None))

    @Slot(bool)
    def shutterButtonClicked(self, checked: bool):
        """
        Commande utilisateur.
        Si acquisition en cours -> override.
        Sinon -> commande normale.
        """
        if self.acquisition_manager.is_running:
            self.user_shutter_override = checked
        else:
            self.user_shutter_override = None

        # Appliquer immédiatement (UI + hardware plus tard)
        self.set_shutter_state(checked)
        self.hardware.set_shutter(checked)

    @Slot()
    def on_acquisition_started(self):
        """Gère le démarrage d'un run acquisition/preview."""
        try:
            self.ui.visu_step_widget.set_running(True)
        except Exception as e:
            logger.debug(f"[MainWindow] ignored exception: {e}")

        self.user_shutter_override = None
        self._update_controls_enabled(True)

    @Slot()
    def on_acquisition_stopped(self):
        """Gère l'arrêt de l'acquisition."""
        try:
            self.scan_manager.flush_pending_buffers()
        except Exception as e:
            logger.warning(f"[MainWindow] final flush error: {e}")

        try:
            self._visualizer_flush_timer.stop()
        except Exception as e:
            logger.debug(f"[MainWindow] ignored exception: {e}")

        try:
            self.ui.visu_step_widget.set_running(False)
        except Exception as e:
            logger.debug(f"[MainWindow] ignored exception: {e}")

        try:
            self.scan_manager.stop_stream()
        except Exception as e:
            logger.debug(f"[MainWindow] ignored exception: {e}")

        self.user_shutter_override = None

        self.ui.pushButton_previewSingle.blockSignals(True)
        self.ui.pushButton_previewSingle.setChecked(False)
        self.ui.pushButton_previewSingle.blockSignals(False)

        self.ui.pushButton_previewcontinuous.blockSignals(True)
        self.ui.pushButton_previewcontinuous.setChecked(False)
        self.ui.pushButton_previewcontinuous.blockSignals(False)

        self.ui.pushButton_acquisitionStart.blockSignals(True)
        self.ui.pushButton_acquisitionStart.setChecked(False)
        self.ui.pushButton_acquisitionStart.blockSignals(False)

        self._update_controls_enabled(False)

        if getattr(self, "_rec_saving_active", False):
            self.save_manager.finish_rec_session()
            self._rec_saving_active = False

        # Don't return to pre-scan XY position when stitching is managing the stage.
        if not self.stitching_manager.is_running():
            self._return_xy_to_pre_scan_position()

    def _return_xy_to_pre_scan_position(self):
        x0 = self._stepper_return_targets_rel.get("x")
        y0 = self._stepper_return_targets_rel.get("y")
        if x0 is None and y0 is None:
            return
        try:
            speed = max(0.01, float(self.positioner_manager.get_max_speed("x")))
            x_target = float(x0) if x0 is not None else self.positioner_manager.get_rel_pos("x")
            y_target = float(y0) if y0 is not None else self.positioner_manager.get_rel_pos("y")
            self.positioner_manager.move_xy_to_rel(x_target, y_target, speed, speed)
        except Exception as e:
            logger.warning(f"[MainWindow] XY return after scan failed: {e}")

    @Slot(str, np.ndarray)
    def update_image_views_with_data(self, channel, image_data):
        shown = np.asarray(image_data, dtype=np.float32)
        self.last_images[channel] = shown

        im_widget = self.ui.im_widgets.get(channel)

        if im_widget is None:
            im_widget = self.ui.im_widgets.get("default")
            if im_widget is None:
                return

        autoscale = bool(getattr(self.ui, "channel_autoscale", {}).get(channel, True))

        # On désactive l'autoscale implicite de pyqtgraph ici
        # pour garder une logique unique et cohérente via les helpers UI.
        im_widget.setImage(
            shown,
            autoLevels=False,
            autoRange=False,
            autoHistogramRange=False
        )

        scan_parameters = self.ui.scan_widget.get_scan_parameters()
        self._apply_physical_scale(im_widget, shown, scan_parameters)

        if autoscale:
            self.ui.autoscale_channel_levels(channel)
        else:
            self.ui.sync_channel_lut_axis_from_current_levels(channel)

        # Histogram refresh (uniquement si ROI active et visible)
        try:
            hw = self.ui.histogram_widget
            current_hist_channel = hw.channel_combo.currentText()

            if (
                hw.toggle_btn.isChecked()
                and hw.rect_roi is not None
                and hw.rect_roi.isVisible()
                and str(current_hist_channel) == str(channel)
            ):
                hw.update_histogram()
        except Exception as e:
            logger.warning(f"[MainWindow] histogram refresh error: {e}")

        # Line profile refresh (si activé sur ce canal)
        try:
            lpw = self.ui.line_profile_widget
            current_lp_channel = lpw.channel_combo.currentText()

            if (
                lpw.toggle_btn.isChecked()
                and str(current_lp_channel) == str(channel)
            ):
                lpw.update_profile()
        except Exception as e:
            logger.warning(f"[MainWindow] line profile refresh error: {e}")

        lock_checked = bool(getattr(self.ui, "channel_lock", {}).get(channel, True))
        im_widget.getView().setAspectLocked(lock_checked)

    @Slot(str, np.ndarray)
    def _forward_image_to_frc(self, channel: str, image_data: np.ndarray):
        """
        Relais vers le widget FRC.
        On lui envoie exactement l'image affichée dans les ImageView.
        """
        try:
            if getattr(self.ui, "frc_widget", None) is None:
                return

            shown = np.asarray(image_data, dtype=np.float32)
            self.ui.frc_widget.on_new_image(channel, shown)
        except Exception as e:
            logger.warning(f"[MainWindow] FRC forward error: {e}")
    
    def _update_frc_channels(self):
        """
        Met à jour la combobox Channel du widget FRC
        à partir des détecteurs actuellement actifs.
        """
        try:
            channels = list(self.ui.detector_widget.detectors) or []
            self.ui.frc_widget.set_active_channels(channels)
        except Exception as e:
            logger.warning(f"[MainWindow] _update_frc_channels error: {e}")

    @Slot(str)
    def on_frc_request_single_frame(self, channel: str):
        """
        Lance un single pour fournir une image fraîche au FRC widget.
        """
        scan_parameters = self.ui.scan_widget.get_scan_parameters()

        # mettre à jour la pixel size transmise au FRC
        try:
            step_sizes = scan_parameters.get("step_sizes", {})
            pix_x = 0.0
            pix_y = 0.0

            for axis_name, step_um in step_sizes.items():
                if str(axis_name).startswith("X-"):
                    pix_x = float(step_um or 0.0)
                elif str(axis_name).startswith("Y-"):
                    pix_y = float(step_um or 0.0)

            if pix_x > 0 and pix_y > 0:
                self.ui.frc_widget.set_pixel_size_um(0.5 * (pix_x + pix_y))
            elif pix_x > 0:
                self.ui.frc_widget.set_pixel_size_um(pix_x)
            elif pix_y > 0:
                self.ui.frc_widget.set_pixel_size_um(pix_y)
        except Exception as e:
            logger.debug(f"[MainWindow] ignored exception: {e}")

        # lancer ton single normal
        self.previewsingleButtonClicked()

    @Slot()
    def previewsingleButtonClicked(self, checked: bool = False):
        """Démarre une acquisition en mode preview single."""
        self._capture_stepper_return_targets()
        scan_parameters = self.ui.scan_widget.get_scan_parameters()
        scan_parameters = self._attach_detector_specs(scan_parameters)
        scan_parameters = self._attach_initial_relative_positions(scan_parameters)
        self.acquisition_manager.set_scan_parameters(scan_parameters)
        self.start_scan_outputs(scan_parameters, mode="preview_single")
        self._mouse_move_proxies.clear()
        self.connect_image_mouse_tracking()
        self.acquisition_manager.start_preview_single()

    @Slot(bool)
    def previewcontinuousButtonClicked(self, checked: bool):
        """ preview button clicked event """
        if checked:
            self._capture_stepper_return_targets()
            scan_parameters = self.ui.scan_widget.get_scan_parameters()
            scan_parameters = self._attach_detector_specs(scan_parameters)
            scan_parameters = self._attach_initial_relative_positions(scan_parameters)
            self.acquisition_manager.set_scan_parameters(scan_parameters)
            self.start_scan_outputs(scan_parameters, mode="preview_continuous")
            self._mouse_move_proxies.clear()
            self.connect_image_mouse_tracking()

            self.acquisition_manager.start_preview_continuous()
        else:
            self.acquisition_manager.stop_acquisition()

    @Slot(str, object)
    def on_image_mouse_moved(self, channel: str, pos):
        im_widget = self.ui.im_widgets.get(channel)
        if im_widget is None:
            return

        img = self.last_images.get(channel)
        if img is None:
            return

        img_item = im_widget.getImageItem()
        if img_item is None:
            return

        view = im_widget.getView()
        vb = view.vb

        if not vb.sceneBoundingRect().contains(pos):
            lbl = self.ui.im_status_labels.get(channel)
            if lbl is not None:
                unit = self._channel_unit_label(channel)
                if unit in ("V", "V·µs"):
                    lbl.setText("x: -  y: -  V: -")
                elif unit == "counts":
                    lbl.setText("x: -  y: -  counts: -")
                else:
                    lbl.setText("x: -  y: -  value: -")
            return

        # Coordonnées dans le repère affiché de l'image (donc en µm, comme le line profile)
        p_view = vb.mapSceneToView(pos)
        x_um = float(p_view.x())
        y_um = float(p_view.y())

        # Conversion vers indices pixel pour lire la valeur dans le tableau numpy
        tr = img_item.transform()
        scale_x = tr.m11() if tr.m11() != 0 else 1.0
        scale_y = tr.m22() if tr.m22() != 0 else 1.0

        x_px = int(np.floor(x_um / scale_x))
        y_px = int(np.floor(y_um / scale_y))

        unit = self._channel_unit_label(channel)

        if 0 <= x_px < img.shape[1] and 0 <= y_px < img.shape[0]:
            value = float(img[y_px, x_px])

            if unit == "V":
                text = f"x: {x_um:7.2f} µm  y: {y_um:7.2f} µm  {unit}: {value:.4f}"
            elif unit == "counts":
                text = f"x: {x_um:7.2f} µm  y: {y_um:7.2f} µm  counts: {value:.0f}"
            else:
                text = f"x: {x_um:7.2f} µm  y: {y_um:7.2f} µm  value: {value:.4f}"
        else:
            if unit == "V":
                text = "x: -  y: -  V: -"
            elif unit == "counts":
                text = "x: -  y: -  counts: -"
            else:
                text = "x: -  y: -  value: -"

        lbl = self.ui.im_status_labels.get(channel)
        if lbl is not None:
            lbl.setText(text)

    def connect_image_mouse_tracking(self):
        """Connecte sigMouseMoved de chaque ImageView vers le slot MainWindow."""
        # structure: channel -> (scene, proxy)
        if not isinstance(self._mouse_move_proxies, dict):
            self._mouse_move_proxies = {}

        for ch, im in self.ui.im_widgets.items():
            try:
                scene = im.getView().scene()
            except RuntimeError:
                # widget déjà détruit
                continue

            # si déjà connecté sur la même scene, rien à faire
            old = self._mouse_move_proxies.get(ch)
            if old is not None:
                old_scene, old_proxy = old
                if old_scene is scene:
                    continue
                # sinon, déconnecter l'ancien
                try:
                    old_scene.sigMouseMoved.disconnect(old_proxy)
                except Exception as e:
                    logger.debug(f"[MainWindow] ignored exception: {e}")

            proxy = lambda pos, c=ch: self.on_image_mouse_moved(c, pos)
            self._mouse_move_proxies[ch] = (scene, proxy)

            try:
                scene.sigMouseMoved.connect(proxy)
            except RuntimeError:
                # scene invalid / détruite pendant l'opération
                self._mouse_move_proxies.pop(ch, None)
                continue

    @Slot()
    def RecButtonClicked(self):      
        scan_parameters = self.ui.scan_widget.get_scan_parameters()
        scan_parameters = self._attach_detector_specs(scan_parameters)
        channels = list(scan_parameters.get("active_channels", [])) or ["default"]

        # sécurité: interdit P + rep>1
        if "Polarization" in scan_parameters.get("active_axes", []) and int(scan_parameters.get("repetitions", 1)) != 1:
            logger.error("ERROR: Polarization requires Repetitions = 1.")
            return

        # check 2 axes minimum
        if len(scan_parameters.get("active_axes", [])) < 2:
            logger.error("ERROR: Need at least 2 active axes for acquisition.")
            return

        self._capture_stepper_return_targets()
        scan_parameters = self._attach_initial_relative_positions(scan_parameters)

        # Récupère dossier + filename + comment du SaveWidget
        rec_fmt = self.ui.save_widget.get_rec_format()
        folder = self.ui.save_widget.folder_line_edit.text().strip()
        filename = self.ui.save_widget.filename_line_edit.text().strip()
        comment = self.ui.save_widget.comment_text_edit.toPlainText().strip()

        ok = self.save_manager.start_rec_session(
            fmt=rec_fmt,
            folder=folder,
            filename=filename,      # vide => REC_HHMMSS auto
            scan_parameters=scan_parameters,
            comment=comment,
            channels=channels
        )

        self._rec_saving_active = bool(ok)

        self._mouse_move_proxies.clear()
        self.connect_image_mouse_tracking()

        try:
            self.ui.positioner_widget.set_keyboard_shortcuts_locked(True)
        except Exception as e:
            logger.debug(f"[MainWindow] ignored exception: {e}")
        
        self.start_scan_outputs(scan_parameters, mode="acquisition")
        self.acquisition_manager.start_acquisition(scan_parameters)
    
    @Slot()
    def stopButtonClicked(self):
        """Arrête l'acquisition en cours."""
        
        self.acquisition_manager.stop_acquisition()

        try:
            self.stitching_manager.stop_run()
        except Exception as e:
            logger.debug(f"[MainWindow] ignored exception: {e}")

        try:
            self.positioner_manager.stop_all()
        except Exception as e:
            logger.debug(f"[MainWindow] ignored exception: {e}")

    def _connect_laser_controls(self):
        lw = self.ui.laser_widget
        lw.laser_power_changed.connect(self.laser_command_manager.enqueue_power)
        lw.laser_power_toggled.connect(self._on_laser_power_toggled)
        lw.laser_gdd_changed.connect(self.laser_command_manager.enqueue_gdd)
        lw.laser_rep_rate_changed.connect(self.laser_command_manager.enqueue_rep_rate)
        lw.alcor_connect_requested.connect(self.laser_command_manager.request_alcor_connect)

        self.laser_command_manager.command_finished.connect(self._on_laser_command_finished)
        self.laser_command_manager.command_failed.connect(self._on_laser_command_failed)

        self.laser_command_manager.gdd_finished.connect(self._on_laser_gdd_finished)
        self.laser_command_manager.gdd_failed.connect(self._on_laser_gdd_failed)

        self.laser_command_manager.rep_rate_finished.connect(self._on_laser_rep_rate_finished)
        self.laser_command_manager.rep_rate_failed.connect(self._on_laser_rep_rate_failed)

        self.laser_command_manager.alcor_connect_finished.connect(self._on_alcor_connect_finished)

    @Slot(str, int)
    def _on_laser_command_finished(self, laser_name: str, value: int):
        try:
            self.statusBar().showMessage(f"{laser_name} set to {int(value)}%", 1500)
        except Exception as e:
            logger.debug(f"[MainWindow] ignored exception: {e}")

    @Slot(str, int, str)
    def _on_laser_command_failed(self, laser_name: str, value: int, message: str):
        logger.error(f"[MainWindow] laser command failed for {laser_name}={value}%: {message}")
        try:
            self.statusBar().showMessage(f"Laser error ({laser_name}): {message}", 5000)
        except Exception as e:
            logger.debug(f"[MainWindow] ignored exception: {e}")

    @Slot(str, float)
    def _on_laser_gdd_finished(self, laser_name: str, gdd_fs2: float):
        try:
            self.statusBar().showMessage(f"{laser_name} GDD set to {gdd_fs2:.0f} fs²", 1500)
        except Exception as e:
            logger.debug(f"[MainWindow] ignored exception: {e}")


    @Slot(str, float, str)
    def _on_laser_gdd_failed(self, laser_name: str, gdd_fs2: float, message: str):
        logger.error(f"[MainWindow] laser GDD command failed for {laser_name}={gdd_fs2} fs^2: {message}")
        try:
            self.statusBar().showMessage(f"Laser GDD error ({laser_name}): {message}", 5000)
        except Exception as e:
            logger.debug(f"[MainWindow] ignored exception: {e}")


    @Slot(str, float)
    def _on_laser_rep_rate_finished(self, laser_name: str, rep_rate_khz: float):
        try:
            self.statusBar().showMessage(
                f"{laser_name} rep rate set to {rep_rate_khz:.3f} kHz",
                1500,
            )
        except Exception as e:
            logger.debug(f"[MainWindow] ignored exception: {e}")


    @Slot(bool, str)
    def _on_alcor_connect_finished(self, success: bool, message: str):
        self.ui.laser_widget.set_alcor_connected(success, message)

    @Slot(str, float, str)
    def _on_laser_rep_rate_failed(self, laser_name: str, rep_rate_khz: float, message: str):
        logger.error(f"[MainWindow] laser rep-rate command failed for {laser_name}={rep_rate_khz} kHz: {message}")
        try:
            self.statusBar().showMessage(f"Laser rep-rate error ({laser_name}): {message}", 5000)
        except Exception as e:
            logger.debug(f"[MainWindow] ignored exception: {e}")

    def _on_laser_power_toggled(self, laser_name: str, enabled: bool):
        laser_name = str(laser_name)

        try:
            # Si tu utilises le Laser_Manager.py asynchrone
            if hasattr(self, "laser_command_manager"):
                self.laser_command_manager.enqueue_enabled(laser_name, bool(enabled))
                return

            # Fallback si tu appelles directement le manager matériel
            self.laser_manager.set_enabled(laser_name, bool(enabled))

        except Exception as e:
            logger.error(f"[MainWindow] laser ON/OFF command failed for {laser_name}={enabled}: {e}")

            try:
                current = self.laser_manager.get_enabled(laser_name)
                self.ui.laser_widget.set_laser_enabled(laser_name, bool(current))
            except Exception:
                self.ui.laser_widget.set_laser_enabled(laser_name, False)