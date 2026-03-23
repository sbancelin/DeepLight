from PySide6.QtWidgets import QMainWindow
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
        self._connect_laser_controls()
        self.save_manager = SaveManager()
        self.positioner_manager = self.hardware.create_positioner_manager(parent=self)
        self.scan_manager = ScanManager(self)
        self.ui.positioner_widget.set_manager(self.positioner_manager)

        self._rec_saving_active = False
        self._stepper_return_targets_rel = {"z": None, "p": None}
        self._update_estimated_stack_size()

        # Initialisation de l'onglet Camera
        """self.im_widget_plot_item_camera = pg.PlotItem()
        self.im_widget_plot_item_camera.setLabel("left", "y (pixels)")
        self.im_widget_plot_item_camera.setLabel("bottom", "x (pixels)")
        self.im_widget_camera = pg.ImageView(
            parent=self.ui.tab_camera,
            view=self.im_widget_plot_item_camera
        )
        self.ui.gridLayout_im_camera.addWidget(self.im_widget_camera, 0, 0, 1, 1)
        #self.im_widget_camera.show()
        self.im_widget_camera.getView().showGrid(True, True)
        self.im_widget_camera.setPredefinedGradient("viridis")  # Choisissez un gradient adapté pour les images RGB"""

        # Masquer les éléments intégrés de ImageView
        #self.im_widget_camera.ui.roiBtn.hide()

        # Connection des signaux
        scan_parameters = self._attach_initial_relative_positions(self.ui.scan_widget.get_scan_parameters())
        self.ui.frc_widget.set_current_image_getter(
            lambda channel: self.last_images.get(channel)
        )
        self.acquisition_manager = AcquisitionManager(
            scan_parameters=scan_parameters,
            microscope_backend=self.microscope_backend,
        )
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
        self.ui.stitch_widget.spin_tiles_x.valueChanged.connect(self.refresh_stitching_preview_grid)
        self.ui.stitch_widget.spin_tiles_y.valueChanged.connect(self.refresh_stitching_preview_grid)
        self.ui.stitch_widget.spin_overlap.valueChanged.connect(self.refresh_stitching_preview_grid)
        self.ui.stitch_widget.cb_show_layout.toggled.connect(self.refresh_stitching_preview_grid)

        QTimer.singleShot(0, self.refresh_stitching_preview_grid)
        QTimer.singleShot(50, self.refresh_stitching_preview_grid)

        self._visualizer_flush_timer = QTimer(self)
        self._visualizer_flush_timer.setInterval(250)
        self._visualizer_flush_timer.timeout.connect(self._flush_visualizers)
                
        self.init_ready = True  # Marque l'initialisation comme terminée     
    
    def _on_laser_power_changed(self, laser_name: str, value: int):
        cfg = self.settings_manager.get_laser_settings(laser_name)

        speed = int(cfg.get("speed", 429410))
        steps_per_degree = float(cfg.get("steps_per_degree", 1919.14))

        self.hardware.set_laser_power_percent(
            laser_name,
            float(value),
            speed=speed,
            steps_per_degree=steps_per_degree,
        )
    
    @Slot()
    def on_stitch_acquire_clicked(self):
        if self.stitching_manager.is_running():
            return

        scan_params = self.ui.scan_widget.get_scan_parameters()
        channels = list(self.ui.detector_widget.detectors) or ["default"]
        scan_params["active_channels"] = channels
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
        except Exception:
            pass

        try:
            self.positioner_manager.stop_all()
        except Exception:
            pass

    @Slot(object)
    def _on_stitch_mosaic_updated(self, mosaic):
        w_um, h_um = self._stitching_geom_um
        self.ui.stitch_widget.set_image(mosaic, width_um=w_um, height_um=h_um)

    def refresh_stitching_preview_grid(self):
        try:
            scan_params = self.ui.scan_widget.get_scan_parameters()
            self.ui.stitch_widget.set_mosaic_layout_preview(scan_params)
        except Exception:
            pass

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
        Mémorise la position relative initiale de Z et P
        au début d'une acquisition.
        """
        try:
            self._stepper_return_targets_rel["z"] = float(self.positioner_manager.get_rel_pos("z"))
        except Exception:
            self._stepper_return_targets_rel["z"] = None

        try:
            self._stepper_return_targets_rel["p"] = float(self.positioner_manager.get_rel_pos("p"))
        except Exception:
            self._stepper_return_targets_rel["p"] = None
    
    
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
    
    def start_scan_outputs(self, scan_parameters: dict, mode: str):
        """Prépare le plan analog/stepper; le temps réel viendra de l'acquisition."""
        self.ui.analog_out_widget.reset_buffer()
        try:
            self.ui.visu_step_widget.reset_buffer()
        except Exception:
            pass

        # On utilise les positions initiales déjà capturées si elles existent,
        # sinon on lit directement le positioner.
        try:
            z0 = self._stepper_return_targets_rel.get("z")
            if z0 is None:
                z0 = float(self.positioner_manager.get_rel_pos("z"))
            self.ui.visu_step_widget.set_initial_position("Z-Vcoil", float(z0))
        except Exception:
            pass

        try:
            p0 = self._stepper_return_targets_rel.get("p")
            if p0 is None:
                p0 = float(self.positioner_manager.get_rel_pos("p"))
            self.ui.visu_step_widget.set_initial_position("Polarization", float(p0))
        except Exception:
            pass

        # ---- config FRC depuis ScanWidget / ScanParams dict ----
        try:
            channels = list(scan_parameters.get("active_channels", []))
            self.ui.frc_widget.set_active_channels(channels)
        except Exception as e:
            print("[MainWindow] FRC set_active_channels error:", e)

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
            print("[MainWindow] FRC set_pixel_size_um error:", e)

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
            except Exception:
                pass

            # IMPORTANT:
            # en mode sample scan, on ne pousse pas de ExecutionPlan galvo.
            # On ne fait PAS set_execution_plan(None), sinon le microscope mock
            # repasse ses buffers image à 1x1 via configure_execution_plan(None).

            try:
                self.scan_manager.stop_stream()
            except Exception:
                pass

            try:
                self._visualizer_flush_timer.stop()
            except Exception:
                pass

        else:
            self.scan_manager.prepare_run(scan_parameters, mode=mode)

            total_ms = None

            try:
                plan = self.scan_manager.get_last_execution_plan()
                if plan is not None and float(plan.sample_rate_hz) > 0:
                    total_ms = (float(plan.total_samples) / float(plan.sample_rate_hz)) * 1e3
                    self.ui.visu_step_widget.set_run_total_ms(total_ms)
                    self.ui.analog_out_widget.set_run_total_ms(total_ms)
            except Exception:
                pass

            try:
                self.acquisition_manager.set_execution_plan(self.scan_manager.get_last_execution_plan())
            except Exception as e:
                print("[MainWindow] set_execution_plan error:", e)

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
            print("[MainWindow] consume_samples error:", e)
    
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
            print("[MainWindow] on_sample_status_updated error:", e)
    
    @Slot()
    def _flush_visualizers(self):
        try:
            self.scan_manager.flush_pending_buffers()
        except Exception as e:
            print("[MainWindow] flush_visualizers error:", e)
    
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
            print("[Save] No image to save (empty last_images).")
            return

        scan_params = self.ui.scan_widget.get_scan_parameters()
        scan_params["active_channels"] = channels

        try:
            path = self.save_manager.save_current_view(
                folder=folder,
                filename=filename,
                fmt=file_format,
                comment=comment,
                images_by_channel=images,
                scan_params=scan_params
            )
            print("[Save] Saved view to:", path)
        except Exception as e:
            print("[Save] ERROR:", e)
    
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
        channels = list(self.ui.detector_widget.detectors) or ["default"]
        params["active_channels"] = channels
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
            im_widget.setLevels(0, 1)

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
            print("[REC] append frame error:", e)
    
    @Slot(object)
    def on_acquisition_done(self, data):
        print("ACQ DONE. Stored reps:", len(data))
        
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
        print(f"[MainWindow] shutterButtonClicked({checked})")
        if self.acquisition_manager.is_running:
            self.user_shutter_override = checked
        else:
            self.user_shutter_override = None

        # Appliquer immédiatement (UI + hardware plus tard)
        self.set_shutter_state(checked)
        self.hardware.set_shutter(checked)

    @Slot()
    def on_acquisition_started(self):
        """Gère le démarrage de l'acquisition."""
        try:
            self.ui.visu_step_widget.set_running(True)
        except Exception:
            pass
        self.user_shutter_override = None
        self._update_controls_enabled(True)

    @Slot()
    def on_acquisition_stopped(self):
        """Gère l'arrêt de l'acquisition."""
        try:
            self.scan_manager.flush_pending_buffers()
        except Exception as e:
            print("[MainWindow] final flush error:", e)

        try:
            self._visualizer_flush_timer.stop()
        except Exception:
            pass

        try:
            self.ui.visu_step_widget.set_running(False)
        except Exception:
            pass

        try:
            self.scan_manager.stop_stream()
        except Exception:
            pass

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

        im_widget.setImage(
            shown,
            autoLevels=autoscale,
            autoRange=False,
            autoHistogramRange=False
        )

        scan_parameters = self.ui.scan_widget.get_scan_parameters()
        self._apply_physical_scale(im_widget, shown, scan_parameters)

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
            print("[MainWindow] FRC forward error:", e)
    
    def _update_frc_channels(self):
        """
        Met à jour la combobox Channel du widget FRC
        à partir des détecteurs actuellement actifs.
        """
        try:
            channels = list(self.ui.detector_widget.detectors) or []
            self.ui.frc_widget.set_active_channels(channels)
        except Exception as e:
            print("[MainWindow] _update_frc_channels error:", e)

    @Slot(str)
    def on_frc_request_single_frame(self, channel: str):
        """
        Lance un single pour fournir une image fraîche au FRC widget.
        """
        scan_parameters = self.ui.scan_widget.get_scan_parameters()

        # mettre à jour la pixel size transmise au FRC
        try:
            pixel_size_um = scan_parameters["X Amplitude"] / scan_parameters["X Pixel"]
            self.ui.frc_widget.set_pixel_size_um(pixel_size_um)
        except Exception:
            pass

        # lancer ton single normal
        self.previewsingleButtonClicked()

    @Slot()
    def previewsingleButtonClicked(self, checked: bool = False):
        """Démarre une acquisition en mode preview single."""
        scan_parameters = self._attach_initial_relative_positions(self.ui.scan_widget.get_scan_parameters())
        scan_parameters["active_channels"] = list(self.ui.detector_widget.detectors)
        self.acquisition_manager.set_scan_parameters(scan_parameters)
        self.start_scan_outputs(scan_parameters, mode="preview_single")
        self._mouse_move_proxies.clear()
        self.connect_image_mouse_tracking()
        self.acquisition_manager.start_preview_single()

    @Slot(bool)
    def previewcontinuousButtonClicked(self, checked: bool):
        """ preview button clicked event """
        if checked:
            scan_parameters = self._attach_initial_relative_positions(self.ui.scan_widget.get_scan_parameters())
            scan_parameters["active_channels"] = list(self.ui.detector_widget.detectors) or ["default"]
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

        vb = im_widget.getView().getViewBox()
        mouse_point = vb.mapSceneToView(pos)

        x = int(mouse_point.x())
        y = int(mouse_point.y())

        if 0 <= x < img.shape[1] and 0 <= y < img.shape[0]:
            intensity = img[y, x]
            text = f"x: {x:4d}  y: {y:4d}  I: {float(intensity):.2f}"
        else:
            text = "x: -  y: -  I: -"

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
                except Exception:
                    pass

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
        channels = list(self.ui.detector_widget.detectors) or ["default"]
        scan_parameters["active_channels"] = channels

        # sécurité: interdit P + rep>1
        if "Polarization" in scan_parameters.get("active_axes", []) and int(scan_parameters.get("repetitions", 1)) != 1:
            print("ERROR: Polarization requires Repetitions = 1.")
            return

        # check 2 axes minimum
        if len(scan_parameters.get("active_axes", [])) < 2:
            print("ERROR: Need at least 2 active axes for acquisition.")
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

        self.start_scan_outputs(scan_parameters, mode="acquisition")
        self.acquisition_manager.start_acquisition(scan_parameters)
    
    @Slot()
    def stopButtonClicked(self):
        """Arrête l'acquisition en cours."""
        self.acquisition_manager.stop_acquisition()

        try:
            self.stitching_manager.stop_run()
        except Exception:
            pass

        try:
            self.positioner_manager.stop_all()
        except Exception:
            pass

    def _connect_laser_controls(self):
        lw = self.ui.laser_widget

        for laser_name in ("Mira 900", "Tumecs"):
            controls = lw.laser_controls.get(laser_name)
            if not controls:
                continue

            slider = controls["slider"]
            spin = controls["spin"]
            button = controls["button"]

            self.ui.laser_widget.laser_power_changed.connect(self._on_laser_power_changed)

            # bouton ignoré pour Mira/Tumecs
            button.setChecked(True)
            button.setEnabled(False)

    def _on_laser_power_changed(self, laser_name: str, value: int):
        cfg = self.settings_manager.get_laser_settings(laser_name)

        if "speed" not in cfg or "steps_per_degree" not in cfg or "offset_deg" not in cfg:
            raise RuntimeError(f"Missing laser settings for {laser_name!r}")

        self.hardware.set_laser_power_percent(
            laser_name,
            float(value),
            speed=int(cfg["speed"]),
            steps_per_degree=float(cfg["steps_per_degree"]),
            offset_deg=float(cfg["offset_deg"]),
        )