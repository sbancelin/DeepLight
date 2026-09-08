import time
import numpy as np
from PySide6.QtCore import QObject, Signal, QTimer, QThread, Slot

class _BrillouinLiveWorker(QObject):
    sigFrameReady = Signal(object)
    sigFailed = Signal(str)
    sigFinished = Signal()

    def __init__(self, hardware, params=None):
        super().__init__()
        self.hardware = hardware
        self.params = params if params is not None else {}
        self._running = False

    @Slot()
    def run(self):
        self._running = True

        try:
            while self._running:
                try:
                    if self.hardware is None:
                        raise RuntimeError("No hardware manager available for Brillouin live.")

                    acquire_fn = getattr(self.hardware, "acquire_brillouin_image", None)
                    if not callable(acquire_fn):
                        raise RuntimeError("Hardware manager has no acquire_brillouin_image() method.")

                    image = acquire_fn(self.params)
                    if image is None:
                        raise RuntimeError("Hardware returned no Brillouin image.")

                    self.sigFrameReady.emit(np.asarray(image, dtype=np.float32))

                except Exception as e:
                    self.sigFailed.emit(str(e))
                    break

        finally:
            self._running = False
            self.sigFinished.emit()

    def stop(self):
        self._running = False

class _SpectroMappingWorker(QObject):
    sigImageUpdate = Signal(object)
    sigSpectrumUpdate = Signal(object)
    sigStatusMessage = Signal(str)
    sigProgress = Signal(int, int)
    sigEta = Signal(float, float)
    sigFinished = Signal(object)
    sigFailed = Signal(str)

    def __init__(
        self,
        hardware,
        positioner_manager,
        scan_parameters,
        modes,
        brillouin_params,
        raman_params,
        save_params,
        pixel_list,
        dataset,
        mapping_started_t0,
        parent=None,
    ):
        super().__init__(parent)
        self.hardware = hardware
        self.positioner_manager = positioner_manager
        self.scan_parameters = dict(scan_parameters or {})
        self.modes = dict(modes or {})
        self.brillouin_params = dict(brillouin_params or {})
        self.raman_params = dict(raman_params or {})
        self.save_params = dict(save_params or {})
        self.pixel_list = list(pixel_list or [])
        self.dataset = dataset
        self.mapping_started_t0 = mapping_started_t0
        self._running = False
        self._BRILLOUIN_SHAPE = (1200, 1200)
        self._RAMAN_POINTS = 1024

        # throttling des updates UI (l'émission de chaque image 1200x1200
        # float32 à chaque pixel charge inutilement le thread GUI)
        self._ui_period_s = 0.5
        self._last_img_emit_t = 0.0
        self._last_spec_emit_t = 0.0

    def stop(self):
        self._running = False

    def _wait_axis_rel_target(self, axis: str, target_rel: float, timeout_s: float = 30.0):
        pm = self.positioner_manager
        if pm is None:
            raise RuntimeError("No positioner manager available.")

        t0 = time.time()
        tol = max(float(pm.get_tolerance(axis)), 0.1)

        while self._running:
            cur = float(pm.get_rel_pos(axis))
            if abs(cur - float(target_rel)) <= tol:
                return

            if time.time() - t0 > float(timeout_s):
                raise RuntimeError(
                    f"Timeout while waiting for axis {axis}: "
                    f"target={float(target_rel):.3f} current={cur:.3f}"
                )

            time.sleep(0.01)

        raise RuntimeError("Spectro mapping stopped.")

    def _move_stage_to_pixel_blocking(self, x_um: float, y_um: float, z_um: float,
                                      tolerance_um: float | None = None):
        pm = self.positioner_manager
        if pm is None:
            return

        move_xy_blocking = getattr(pm, "move_xy_to_rel_blocking", None)
        if callable(move_xy_blocking):
            speed_x = max(0.01, float(pm.get_max_speed("x")))
            speed_y = max(0.01, float(pm.get_max_speed("y")))
            move_xy_blocking(
                float(x_um),
                float(y_um),
                float(speed_x),
                float(speed_y),
                timeout_s=30.0,
                tolerance_um=tolerance_um,
            )
        else:
            # This positioner manager has no combined blocking XY move: drive
            # the two axes one after the other and wait for each target.
            speed_x = max(0.01, float(pm.get_max_speed("x")))
            speed_y = max(0.01, float(pm.get_max_speed("y")))
            pm.move_to_rel("x", float(x_um), float(speed_x))
            pm.move_to_rel("y", float(y_um), float(speed_y))
            self._wait_axis_rel_target("x", float(x_um), timeout_s=30.0)
            self._wait_axis_rel_target("y", float(y_um), timeout_s=30.0)

        if not self._running:
            raise RuntimeError("Spectro mapping stopped.")

        try:
            speed_z = max(0.001, float(pm.get_max_speed("z")))
            cur_z = float(pm.get_rel_pos("z"))
            tol_z = max(float(pm.get_tolerance("z")), 0.1)
            if abs(cur_z - float(z_um)) > tol_z:
                pm.move_to_rel("z", float(z_um), float(speed_z))
                self._wait_axis_rel_target("z", float(z_um), timeout_s=30.0)
        except Exception:
            pass

    def _try_acquire_brillouin_from_hardware(self, params):
        if self.hardware is None:
            return None
        acquire_fn = getattr(self.hardware, "acquire_brillouin_image", None)
        if not callable(acquire_fn):
            return None
        image = acquire_fn(params or {})
        if image is None:
            return None
        return np.asarray(image, dtype=np.float32)

    def _build_raman_wavelengths(self, raman_params):
        center = float(raman_params.get("center_nm", 700.0) or 700.0)
        span = max(1e-6, float(raman_params.get("span_nm", 100.0) or 100.0))
        return np.linspace(center - 0.5 * span, center + 0.5 * span, self._RAMAN_POINTS)

    def _parse_binning_factor(self, params) -> int:
        text = str((params or {}).get("binning", "1x1"))
        try:
            bx = int(text.lower().split("x")[0])
            return max(1, bx)
        except Exception:
            return 1

    def _get_binned_full_shape(self, params):
        full_h, full_w = self._BRILLOUIN_SHAPE
        binning = self._parse_binning_factor(params)
        return max(1, full_h // binning), max(1, full_w // binning)

    def _get_effective_brillouin_roi(self, params):
        params = dict(params or {})
        full_h, full_w = self._get_binned_full_shape(params)
        enabled = bool(params.get("roi_enabled", False))

        if not enabled:
            return {
                "enabled": False,
                "x": 0,
                "y": 0,
                "width": full_w,
                "height": full_h,
                "full_width": full_w,
                "full_height": full_h,
            }

        x = int(params.get("roi_x", 0) or 0)
        y = int(params.get("roi_y", 0) or 0)
        w = int(params.get("roi_width", full_w) or full_w)
        h = int(params.get("roi_height", full_h) or full_h)

        x = max(0, min(x, full_w - 1))
        y = max(0, min(y, full_h - 1))
        w = max(1, min(w, full_w - x))
        h = max(1, min(h, full_h - y))

        return {
            "enabled": True,
            "x": x,
            "y": y,
            "width": w,
            "height": h,
            "full_width": full_w,
            "full_height": full_h,
        }

    def _mock_brillouin_image(self, x_idx, y_idx, z_idx, t_index, params):
        full_h, full_w = self._get_binned_full_shape(params)
        yy, xx = np.mgrid[0:full_h, 0:full_w].astype(np.float32)

        cx = full_w * (0.50 + 0.12 * np.sin(0.17 * x_idx + 0.11 * t_index))
        cy = full_h * (0.50 + 0.12 * np.cos(0.19 * y_idx + 0.07 * t_index))

        sigma_x = 6.0 + 0.3 * (x_idx % 7)
        sigma_y = 6.5 + 0.25 * (y_idx % 5)

        binning = self._parse_binning_factor(params)
        sigma_x = sigma_x / max(1.0, 0.65 * binning)
        sigma_y = sigma_y / max(1.0, 0.65 * binning)

        g1 = np.exp(-(((xx - cx) ** 2) / (2.0 * sigma_x ** 2) + ((yy - cy) ** 2) / (2.0 * sigma_y ** 2)))
        g2 = 0.55 * np.exp(
            -(((xx - (full_w - cx)) ** 2) / (2.0 * (sigma_x * 1.4) ** 2)
              + ((yy - cy) ** 2) / (2.0 * (sigma_y * 1.2) ** 2))
        )

        rings_r = np.sqrt((xx - full_w / 2.0) ** 2 + (yy - full_h / 2.0) ** 2)
        rings = 0.08 * (1.0 + np.cos(0.55 * rings_r - 0.35 * z_idx))

        gradient = (
            0.15 * (xx / max(1.0, float(full_w - 1)))
            + 0.10 * (yy / max(1.0, float(full_h - 1)))
        )

        exposure_ms = float(params.get("exposure_ms", 10.0) or 10.0)
        gain = float(params.get("gain", 0.0) or 0.0)

        scale = 200.0 + 2.5 * exposure_ms + 8.0 * gain
        image = scale * (g1 + g2 + rings + gradient)

        rng = np.random.default_rng(
            seed=(1000003 + 97 * x_idx + 193 * y_idx + 389 * z_idx + 17 * t_index)
        )
        noise = rng.normal(loc=0.0, scale=max(2.0, 0.015 * scale), size=(full_h, full_w))
        image = np.clip(image + noise, 0.0, None).astype(np.float32, copy=False)

        roi = self._get_effective_brillouin_roi(params)
        x0 = int(roi["x"])
        y0 = int(roi["y"])
        w = int(roi["width"])
        h = int(roi["height"])
        return image[y0:y0 + h, x0:x0 + w]

    def _mock_raman_spectrum(self, x_idx, y_idx, z_idx, t_index, params, wavelengths):
        center = float(params.get("center_nm", 700.0) or 700.0)
        span = max(1e-6, float(params.get("span_nm", 100.0) or 100.0))
        exposure_ms = float(params.get("exposure_ms", 100.0) or 100.0)
        averages = max(1, int(params.get("averages", 1) or 1))

        x = np.asarray(wavelengths, dtype=np.float32)

        p1 = center - 0.18 * span + 0.015 * span * np.sin(0.20 * x_idx + 0.07 * t_index)
        p2 = center + 0.08 * span + 0.020 * span * np.cos(0.17 * y_idx + 0.05 * t_index)
        p3 = center + 0.24 * span + 0.010 * span * np.sin(0.31 * z_idx + 0.03 * t_index)

        s1 = 0.025 * span
        s2 = 0.040 * span
        s3 = 0.030 * span

        g1 = 1.00 * np.exp(-0.5 * ((x - p1) / max(s1, 1e-6)) ** 2)
        g2 = 0.65 * np.exp(-0.5 * ((x - p2) / max(s2, 1e-6)) ** 2)
        g3 = 0.40 * np.exp(-0.5 * ((x - p3) / max(s3, 1e-6)) ** 2)

        baseline = 0.08 + 0.04 * np.sin((x - center) / max(span, 1e-6) * 2.5 * np.pi)
        amp = max(50.0, 0.9 * exposure_ms * np.sqrt(averages))

        spectrum = amp * (g1 + g2 + g3 + baseline)

        rng = np.random.default_rng(seed=(2000003 + 53 * x_idx + 101 * y_idx + 211 * z_idx + 13 * t_index))
        noise_sigma = max(0.5, 0.035 * amp / np.sqrt(averages))
        spectrum = np.clip(
            spectrum + rng.normal(loc=0.0, scale=noise_sigma, size=x.shape),
            0.0,
            None,
        )
        return spectrum.astype(np.float32, copy=False)

    @Slot()
    def run(self):
        self._running = True
        try:
            total = len(self.pixel_list)
            # --- Compensation backlash inter-lignes (mécanisme 1/2) ---
            # Dans un serpentin, les lignes aller sont parcourues en +X et les
            # lignes retour en -X. Avec des consignes identiques, la position
            # *réelle* diffère du jeu mécanique B (~1.2 µm) entre les deux sens
            # -> décalage constant d'une ligne sur deux dans l'image.
            # Correction (stratégie "offset de consigne", sans mouvement en
            # plus) : sur les lignes retour (y impair) on commande X + offset,
            # tout en enregistrant la position NOMINALE dans le dataset, de
            # sorte que les positions physiques des deux sens coïncident.
            # Offset signé, calibré (cf. origin_overshoot_um pour le mécanisme
            # 2/2 = retour à l'origine, dans le bloc finally).
            line_offset_x_um = float(self.scan_parameters.get("line_offset_x_um", 0.0) or 0.0)
            n_repeats = max(1, int(self.scan_parameters.get("n_repeats", 1) or 1))
            repeat_delay_s = max(0.0, float(self.scan_parameters.get("repeat_delay_s", 0.0) or 0.0))
            settle_ms = float(self.scan_parameters.get("settle_ms", 0.0) or 0.0)
            exposure_ms = float(self.scan_parameters.get("exposure_ms", 0.0) or 0.0)
            pixel_done = 0

            if "brillouin_images" in self.dataset:
                prepare_fn = getattr(self.hardware, "prepare_brillouin_for_scan", None)
                if prepare_fn is not None:
                    prepare_fn(self.brillouin_params)

            for info in self.pixel_list:
                if not self._running:
                    break

                t = int(info.get("t_index", 0))
                x = int(info["x_index"])
                y = int(info["y_index"])
                z = int(info["z_index"])

                lin = int(info["linear_index"])

                if info.get("is_repeat_start", False) and repeat_delay_s > 0:
                    self.sigStatusMessage.emit(
                        f"Time lapse: attente {int(repeat_delay_s)} s avant répétition {t + 1}/{n_repeats}..."
                    )
                    t_end = time.perf_counter() + repeat_delay_s
                    while self._running and time.perf_counter() < t_end:
                        time.sleep(0.05)
                    if not self._running:
                        break

                pos_um = self.dataset["positions_um"][t, z, y, x]
                x_um = float(pos_um[0])
                y_um = float(pos_um[1])
                z_um = float(pos_um[2])

                # X commandée = nominale + offset de backlash sur les lignes
                # retour (y impair), nominale sur les lignes aller. La position
                # enregistrée plus bas (dataset) reste toujours nominale.
                # Précédence Python : (x_um + line_offset_x_um) if ... else x_um.
                x_cmd = x_um + line_offset_x_um if (y % 2 == 1) else x_um

                repeat_str = f"T={t + 1}/{n_repeats} | " if n_repeats > 1 else ""
                self.sigStatusMessage.emit(
                    f"{repeat_str}Spectro {lin + 1}/{total} | "
                    f"X={x + 1}/{self.scan_parameters.get('pixels_x', 1)} "
                    f"Y={y + 1}/{self.scan_parameters.get('pixels_y', 1)} "
                    f"Z={z + 1}/{self.scan_parameters.get('pixels_z', 1)}"
                )

                self._move_stage_to_pixel_blocking(x_cmd, y_um, z_um)

                if settle_ms > 0:
                    t_end = time.perf_counter() + settle_ms / 1000.0
                    while self._running and time.perf_counter() < t_end:
                        time.sleep(0.01)

                acq_t0 = time.perf_counter()

                self.dataset["linear_index_map"][t, z, y, x] = lin
                self.dataset["acquisition_order_indices"][lin] = (t, z, y, x)
                self.dataset["acquisition_order_positions_um"][lin] = (x_um, y_um, z_um)
                self.dataset["positions"][lin] = (x_um, y_um, z_um)
                self.dataset["timestamps_s"][lin] = (
                    time.perf_counter() - self.mapping_started_t0
                    if self.mapping_started_t0 is not None else np.nan
                )

                is_last_pixel = (lin + 1 >= total)

                if "brillouin_images" in self.dataset:
                    image = self._try_acquire_brillouin_from_hardware(self.brillouin_params)
                    if image is None:
                        image = self._mock_brillouin_image(
                            x_idx=x, y_idx=y, z_idx=z, t_index=lin, params=self.brillouin_params
                        )
                    self.dataset["brillouin_images"][t, z, y, x] = image

                    now = time.perf_counter()
                    if is_last_pixel or now - self._last_img_emit_t >= self._ui_period_s:
                        self._last_img_emit_t = now
                        self.sigImageUpdate.emit(image)

                if "raman_spectra" in self.dataset:
                    wavelengths = self.dataset["raman_wavelengths"]
                    spectrum = self._mock_raman_spectrum(
                        x_idx=x, y_idx=y, z_idx=z, t_index=lin,
                        params=self.raman_params, wavelengths=wavelengths
                    )
                    self.dataset["raman_spectra"][t, z, y, x] = spectrum

                    now = time.perf_counter()
                    if is_last_pixel or now - self._last_spec_emit_t >= self._ui_period_s:
                        self._last_spec_emit_t = now
                        self.sigSpectrumUpdate.emit(spectrum)

                self.dataset["pixel_valid"][t, z, y, x] = True
                pixel_done += 1
                self.sigProgress.emit(pixel_done, total)

                elapsed_loop_s = time.perf_counter() - self.mapping_started_t0 if self.mapping_started_t0 is not None else 0.0
                if pixel_done > 0 and total > pixel_done:
                    avg_s = elapsed_loop_s / pixel_done
                    remaining_eta_s = avg_s * (total - pixel_done)
                    self.sigEta.emit(elapsed_loop_s, remaining_eta_s)

                elapsed_acq_s = time.perf_counter() - acq_t0
                remaining_s = max(0.0, exposure_ms / 1000.0 - elapsed_acq_s)
                if remaining_s > 0:
                    t_end = time.perf_counter() + remaining_s
                    while self._running and time.perf_counter() < t_end:
                        time.sleep(0.01)

            self.sigFinished.emit(self.dataset)

        except Exception as e:
            self.sigFailed.emit(f"Spectro mapping failed: {e}")

        finally:
            origin = (self.dataset or {}).get("origin_um")
            if origin:
                try:
                    self.sigStatusMessage.emit("Retour à la position initiale...")
                    self._running = True
                    ox = float(origin["x"])
                    oy = float(origin["y"])
                    oz = float(origin["z"])
                    # --- Anti-backlash du retour à l'origine (mécanisme 2/2) ---
                    # Symptôme : une image galvo prise AVANT puis APRÈS un scan
                    # platine est décalée de ~2 µm. Cause : le retour direct à
                    # l'origine inverse le sens, la platine cale dans son jeu
                    # mécanique, et move_xy_to_rel_blocking accepte une erreur
                    # résiduelle jusqu'à _XY_FINAL_ACCEPT_UM (2 µm) au lieu de
                    # forcer la cible -> la platine se gare hors origine.
                    # Correctif : aborder TOUJOURS le centre en venant de la
                    # gauche (déplacement final en +X), ce qui rattrape le jeu
                    # de façon déterministe et atteint la vraie origine.
                    #   - déjà à gauche du centre : approche directe ;
                    #   - à droite (cas normal, fin de scan en bas à droite) :
                    #     passer d'abord à gauche (overshoot) puis approcher.
                    # Au plus UN déplacement supplémentaire, sur X uniquement
                    # (Y/Z amenés directement). Valeur en abs : c'est une
                    # distance à gauche du centre, le sens est fixe.
                    overshoot = abs(float(
                        self.scan_parameters.get("origin_overshoot_um", 0.0) or 0.0
                    ))
                    pm = self.positioner_manager
                    cur_x = None
                    if pm is not None and pm.has_axis("x"):
                        try:
                            cur_x = float(pm.get_rel_pos("x"))
                        except Exception:
                            cur_x = None
                    if overshoot > 0.0 and (cur_x is None or cur_x > ox):
                        self._move_stage_to_pixel_blocking(ox - overshoot, oy, oz)
                    self._move_stage_to_pixel_blocking(ox, oy, oz)
                except Exception:
                    pass
                finally:
                    self._running = False

class SpectroManager(QObject):
    """
    Manager spectro mock V2.

    Rôles :
    - snap / live Brillouin
    - snap / live Raman
    - mapping serpentin XYZ piloté depuis SpectroPanelWidget
    - production d'un dataset reconstruction-friendly
    - API compatible avec ce que MainWindow appelle déjà
    """

    sigImageUpdate = Signal(object)                     # np.ndarray 2D
    sigSpectrumUpdate = Signal(object)                  # np.ndarray 1D
    sigStatusMessage = Signal(str)
    sigProgress = Signal(int, int)
    sigEta = Signal(float, float)
    sigFinished = Signal(object)                        # dataset
    sigFailed = Signal(str)
    sigBrillouinLiveRunningChanged = Signal(bool)
    sigRamanLiveRunningChanged = Signal(bool)

    _BRILLOUIN_SHAPE = (1200, 1200)
    _RAMAN_POINTS = 1024

    def __init__(self, hardware_manager=None, positioner_manager=None, parent=None):
        super().__init__(parent)

        self.hardware = hardware_manager
        self.positioner_manager = positioner_manager

        # --- état mapping
        self.running = False
        self.dataset = None
        self.pixel_list = []
        self.current_pixel_index = 0
        self._mapping_started_t0 = None

        self._scan_parameters = {}
        self._save_parameters = {}
        self._modes = {}
        self._brillouin_params = {}
        self._raman_params = {}

        # --- throttling affichage UI ---
        self._ui_update_period_s = 1.0   # 1 image / spectre par seconde max
        self._last_brillouin_ui_emit_t = 0.0
        self._last_raman_ui_emit_t = 0.0

        self._mapping_thread = None
        self._mapping_worker = None

        # --- live Brillouin
        self._brillouin_live_running = False
        self._brillouin_live_params = {}
        self._brillouin_live_counter = 0
        self.brillouin_live_timer = QTimer(self)
        self.brillouin_live_timer.timeout.connect(self._on_brillouin_live_timeout)
        self._brillouin_live_thread = None
        self._brillouin_live_worker = None

        # --- live Raman
        self._raman_live_running = False
        self._raman_live_params = {}
        self._raman_live_counter = 0
        self.raman_live_timer = QTimer(self)
        self.raman_live_timer.timeout.connect(self._on_raman_live_timeout)

    # ==========================================================
    # API publique demandée par MainWindow
    # ==========================================================
    def is_running(self) -> bool:
        return bool(self.running)

    def is_brillouin_live_running(self) -> bool:
        return bool(self._brillouin_live_running)

    def update_brillouin_live_params(self, params: dict):
        if self._brillouin_live_running and self._brillouin_live_params is not None:
            self._brillouin_live_params.update(params)

    def is_raman_live_running(self) -> bool:
        return bool(self._raman_live_running)

    @Slot(object)
    def _on_mapping_worker_finished(self, dataset):
        self.dataset = dataset
        self.running = False

        try:
            if self._mapping_thread is not None:
                self._mapping_thread.quit()
                self._mapping_thread.wait(3000)
        except Exception:
            pass

        self._mapping_worker = None
        self._mapping_thread = None

        self.sigFinished.emit(dataset)

    @Slot(str)
    def _on_mapping_worker_failed(self, message: str):
        self.running = False

        try:
            if self._mapping_thread is not None:
                self._mapping_thread.quit()
                self._mapping_thread.wait(3000)
        except Exception:
            pass

        self._mapping_worker = None
        self._mapping_thread = None

        self.sigFailed.emit(message)
    
    def stop_mapping(self):
        if self._mapping_worker is not None:
            try:
                self._mapping_worker.stop()
            except Exception:
                pass

        if self._mapping_thread is not None:
            try:
                self._mapping_thread.quit()
                self._mapping_thread.wait(3000)
            except Exception:
                pass

        self._mapping_worker = None
        self._mapping_thread = None

        self.running = False
        self.sigStatusMessage.emit("Spectro acquisition stopped")

    def stop_all_live(self):
        self.stop_live_brillouin()
        self.stop_live_raman()

    def _try_acquire_brillouin_from_hardware(self, params):
        """
        Ask HardwareManager for a Brillouin image if a real backend is available.
        Returns:
            np.ndarray if hardware acquisition succeeded
            None if no hardware image is available and caller should use mock
        """
        if self.hardware is None:
            return None

        acquire_fn = getattr(self.hardware, "acquire_brillouin_image", None)
        if not callable(acquire_fn):
            return None

        image = acquire_fn(params or {})
        if image is None:
            return None

        return np.asarray(image, dtype=np.float32)
    
    def _wait_axis_rel_target(self, axis: str, target_rel: float, timeout_s: float = 30.0):
        pm = self.positioner_manager
        if pm is None:
            raise RuntimeError("No positioner manager available.")

        t0 = time.time()
        tol = max(float(pm.get_tolerance(axis)), 0.1)

        while True:
            cur = float(pm.get_rel_pos(axis))
            if abs(cur - float(target_rel)) <= tol:
                return

            if time.time() - t0 > float(timeout_s):
                raise RuntimeError(
                    f"Timeout while waiting for axis {axis}: "
                    f"target={float(target_rel):.3f} current={cur:.3f}"
                )

            time.sleep(0.01)

    def _move_stage_to_pixel_blocking(self, x_um: float, y_um: float, z_um: float):
        pm = self.positioner_manager
        if pm is None:
            return

        # XY bloquant si disponible
        move_xy_blocking = getattr(pm, "move_xy_to_rel_blocking", None)
        if callable(move_xy_blocking):
            speed_x = max(0.01, float(pm.get_max_speed("x")))
            speed_y = max(0.01, float(pm.get_max_speed("y")))
            move_xy_blocking(
                float(x_um),
                float(y_um),
                float(speed_x),
                float(speed_y),
                timeout_s=30.0,
            )
        else:
            # This positioner manager has no combined blocking XY move: drive
            # the two axes one after the other and wait for each target.
            speed_x = max(0.01, float(pm.get_max_speed("x")))
            speed_y = max(0.01, float(pm.get_max_speed("y")))
            pm.move_to_rel("x", float(x_um), float(speed_x))
            pm.move_to_rel("y", float(y_um), float(speed_y))
            self._wait_axis_rel_target("x", float(x_um), timeout_s=30.0)
            self._wait_axis_rel_target("y", float(y_um), timeout_s=30.0)

        # Z point par point si demandé — skip si déjà en position
        try:
            speed_z = max(0.001, float(pm.get_max_speed("z")))
            cur_z = float(pm.get_rel_pos("z"))
            tol_z = max(float(pm.get_tolerance("z")), 0.1)
            if abs(cur_z - float(z_um)) > tol_z:
                pm.move_to_rel("z", float(z_um), float(speed_z))
                self._wait_axis_rel_target("z", float(z_um), timeout_s=30.0)
        except Exception:
            # Si z n'est pas présent ou pas utilisable, on n'empêche pas XY
            pass
    
    # --------------------------
    # Mapping
    # --------------------------
    def start_mapping(
        self,
        scan_parameters,
        modes,
        brillouin_params,
        raman_params,
        save_params=None,
    ):
        if self.running:
            return

        if not bool(modes.get("brillouin", False)) and not bool(modes.get("raman", False)):
            self.sigFailed.emit("Select at least one spectro mode.")
            return

        self.stop_all_live()

        self._scan_parameters = dict(scan_parameters or {})
        self._save_parameters = dict(save_params or {})
        self._modes = dict(modes or {})
        self._brillouin_params = dict(brillouin_params or {})
        self._raman_params = dict(raman_params or {})

        px = max(1, int(self._scan_parameters.get("pixels_x", 1) or 1))
        py = max(1, int(self._scan_parameters.get("pixels_y", 1) or 1))
        pz = max(1, int(self._scan_parameters.get("pixels_z", 1) or 1))
        pt = max(1, int(self._scan_parameters.get("n_repeats", 1) or 1))

        self.pixel_list = self._build_serpentine_grid(px, py, pz, n_repeats=pt)
        self.current_pixel_index = 0
        self.dataset = self._create_dataset(
            self._scan_parameters,
            self._modes,
            self._brillouin_params,
            self._raman_params,
            self._save_parameters,
        )

        real_pixels = len(self.pixel_list)
        now = time.perf_counter()
        self._last_brillouin_ui_emit_t = now - self._ui_update_period_s
        self._last_raman_ui_emit_t = now - self._ui_update_period_s
        self.running = True
        self._mapping_started_t0 = time.perf_counter()

        self.sigStatusMessage.emit("Spectro acquisition started")
        self.sigProgress.emit(0, real_pixels)

        self._mapping_thread = QThread(self)
        self._mapping_worker = _SpectroMappingWorker(
            hardware=self.hardware,
            positioner_manager=self.positioner_manager,
            scan_parameters=self._scan_parameters,
            modes=self._modes,
            brillouin_params=self._brillouin_params,
            raman_params=self._raman_params,
            save_params=self._save_parameters,
            pixel_list=self.pixel_list,
            dataset=self.dataset,
            mapping_started_t0=self._mapping_started_t0,
        )
        self._mapping_worker.moveToThread(self._mapping_thread)

        self._mapping_thread.started.connect(self._mapping_worker.run)
        self._mapping_worker.sigImageUpdate.connect(self.sigImageUpdate)
        self._mapping_worker.sigSpectrumUpdate.connect(self.sigSpectrumUpdate)
        self._mapping_worker.sigStatusMessage.connect(self.sigStatusMessage)
        self._mapping_worker.sigProgress.connect(self.sigProgress)
        self._mapping_worker.sigEta.connect(self.sigEta)
        self._mapping_worker.sigFinished.connect(self._on_mapping_worker_finished)
        self._mapping_worker.sigFailed.connect(self._on_mapping_worker_failed)

        self._mapping_thread.start()

    # --------------------------
    # Snap / Live Brillouin
    # --------------------------
    def snap_brillouin(self, params=None):
        try:
            params = dict(params or {})

            image = self._try_acquire_brillouin_from_hardware(params)
            if image is None:
                image = self._mock_brillouin_image(
                    x_idx=0,
                    y_idx=0,
                    z_idx=0,
                    t_index=int(time.time() * 10) % 100000,
                    params=params,
                )

            self.sigImageUpdate.emit(image)
            self.sigStatusMessage.emit("Brillouin snap done")
        except Exception as e:
            self.sigFailed.emit(f"Brillouin snap failed: {e}")

    def start_live_brillouin(self, params=None):
        if self._brillouin_live_running:
            return

        self._brillouin_live_params = dict(params or {})
        self._brillouin_live_counter = 0

        # sécurité : on coupe le live Raman si besoin
        if self._raman_live_running:
            self.stop_live_raman()

        self._brillouin_live_thread = QThread(self)
        self._brillouin_live_worker = _BrillouinLiveWorker(
            hardware=self.hardware,
            params=self._brillouin_live_params,
        )
        self._brillouin_live_worker.moveToThread(self._brillouin_live_thread)

        self._brillouin_live_thread.started.connect(self._brillouin_live_worker.run)
        self._brillouin_live_worker.sigFrameReady.connect(self._on_brillouin_live_frame_ready)
        self._brillouin_live_worker.sigFailed.connect(self._on_brillouin_live_worker_failed)
        self._brillouin_live_worker.sigFinished.connect(self._on_brillouin_live_worker_finished)

        self._brillouin_live_running = True
        self.sigBrillouinLiveRunningChanged.emit(True)
        self.sigStatusMessage.emit("Brillouin live started")

        self._brillouin_live_thread.start()

    def stop_live_brillouin(self):
        if not self._brillouin_live_running:
            return

        self._brillouin_live_running = False

        if self._brillouin_live_worker is not None:
            try:
                self._brillouin_live_worker.stop()
            except Exception:
                pass

        if self._brillouin_live_thread is not None:
            try:
                self._brillouin_live_thread.quit()
                self._brillouin_live_thread.wait(3000)
            except Exception:
                pass

        self._brillouin_live_worker = None
        self._brillouin_live_thread = None

        self.sigBrillouinLiveRunningChanged.emit(False)
        self.sigStatusMessage.emit("Brillouin live stopped")

    @Slot(object)
    def _on_brillouin_live_frame_ready(self, image):
        try:
            if not self._brillouin_live_running:
                return

            self._brillouin_live_counter += 1
            self.sigImageUpdate.emit(np.asarray(image, dtype=np.float32))
        except Exception as e:
            self.stop_live_brillouin()
            self.sigFailed.emit(f"Brillouin live display failed: {e}")


    @Slot(str)
    def _on_brillouin_live_worker_failed(self, message: str):
        if self._brillouin_live_running:
            self.stop_live_brillouin()
        self.sigFailed.emit(f"Brillouin live failed: {message}")


    @Slot()
    def _on_brillouin_live_worker_finished(self):
        try:
            if self._brillouin_live_thread is not None:
                self._brillouin_live_thread.deleteLater()
        except Exception:
            pass

    # --------------------------
    # Snap / Live Raman
    # --------------------------
    def snap_raman(self, params=None):
        try:
            params = dict(params or {})
            wavelengths = self._build_raman_wavelengths(params)
            spectrum = self._mock_raman_spectrum(
                x_idx=0,
                y_idx=0,
                z_idx=0,
                t_index=int(time.time() * 10) % 100000,
                params=params,
                wavelengths=wavelengths,
            )
            # pour que MainWindow puisse relire les wavelengths même hors mapping
            self.dataset = self.dataset or {}
            self.dataset["raman_wavelengths"] = wavelengths
            self.sigSpectrumUpdate.emit(spectrum)
            self.sigStatusMessage.emit("Raman snap done")
        except Exception as e:
            self.sigFailed.emit(f"Raman snap failed: {e}")

    def start_live_raman(self, params=None):
        if self._raman_live_running:
            return

        self._raman_live_params = dict(params or {})
        self._raman_live_counter = 0
        self._raman_live_running = True

        interval_ms = self._compute_raman_live_interval_ms(self._raman_live_params)
        self.raman_live_timer.start(interval_ms)
        self.sigRamanLiveRunningChanged.emit(True)
        self.sigStatusMessage.emit("Raman live started")

    def stop_live_raman(self):
        if not self._raman_live_running:
            return

        self.raman_live_timer.stop()
        self._raman_live_running = False
        self.sigRamanLiveRunningChanged.emit(False)
        self.sigStatusMessage.emit("Raman live stopped")

    # ==========================================================
    # Timers live
    # ==========================================================
    def _on_brillouin_live_timeout(self):
        # ancien chemin timer synchrone non utilisé
        pass

    def _on_raman_live_timeout(self):
        try:
            wavelengths = self._build_raman_wavelengths(self._raman_live_params)
            spectrum = self._mock_raman_spectrum(
                x_idx=0,
                y_idx=0,
                z_idx=0,
                t_index=self._raman_live_counter,
                params=self._raman_live_params,
                wavelengths=wavelengths,
            )
            self._raman_live_counter += 1
            self.dataset = self.dataset or {}
            self.dataset["raman_wavelengths"] = wavelengths
            self.sigSpectrumUpdate.emit(spectrum)
        except Exception as e:
            self.stop_live_raman()
            self.sigFailed.emit(f"Raman live failed: {e}")

    # ==========================================================
    # Grid / ordre serpentin
    # ==========================================================
    def _build_serpentine_grid(self, pixels_x, pixels_y, pixels_z=1, n_repeats=1):
        # Ordre serpentin pur : lignes paires gauche->droite, impaires
        # droite->gauche. Un seul déplacement par pixel, vers des pixels
        # adjacents. Aucun waypoint d'overshoot n'est inséré ici : la
        # compensation du jeu mécanique inter-lignes se fait par décalage de
        # la X commandée dans run() (cf. line_offset_x_um), pas par un
        # mouvement supplémentaire.
        grid = []
        linear = 0
        for t in range(max(1, int(n_repeats))):
            for z in range(int(pixels_z)):
                for y in range(int(pixels_y)):
                    xs = list(range(int(pixels_x)))
                    if y % 2 == 1:
                        xs.reverse()
                    for x in xs:
                        grid.append({
                            "linear_index": int(linear),
                            "t_index": int(t),
                            "x_index": int(x),
                            "y_index": int(y),
                            "z_index": int(z),
                            "is_repeat_start": bool(t > 0 and z == 0 and y == 0 and x == xs[0]),
                        })
                        linear += 1
        return grid

    # ==========================================================
    # Dataset
    # ==========================================================
    def _create_dataset(
        self,
        scan_parameters,
        modes,
        brillouin_params,
        raman_params,
        save_params,
    ):
        px = max(1, int(scan_parameters.get("pixels_x", 1) or 1))
        py = max(1, int(scan_parameters.get("pixels_y", 1) or 1))
        pz = max(1, int(scan_parameters.get("pixels_z", 1) or 1))
        pt = max(1, int(scan_parameters.get("n_repeats", 1) or 1))

        sx = float(scan_parameters.get("size_x_um", 0.0) or 0.0)
        sy = float(scan_parameters.get("size_y_um", 0.0) or 0.0)
        sz = float(scan_parameters.get("size_z_um", 0.0) or 0.0)
        stepx = float(scan_parameters.get("step_x_um", 0.0) or 0.0)
        stepy = float(scan_parameters.get("step_y_um", 0.0) or 0.0)
        stepz = float(scan_parameters.get("step_z_um", 0.0) or 0.0)

        pm = self.positioner_manager
        x_origin_um = float(pm.get_rel_pos("x")) if pm is not None and pm.has_axis("x") else 0.0
        y_origin_um = float(pm.get_rel_pos("y")) if pm is not None and pm.has_axis("y") else 0.0
        z_origin_um = float(pm.get_rel_pos("z")) if pm is not None and pm.has_axis("z") else 0.0

        total_pixels = pt * px * py * pz

        dataset = {
            "version": "spectro_mock_v2",
            "grid_shape": (pt, pz, py, px),
            "axis_order": ("t", "z", "y", "x"),
            "serpentine": bool(scan_parameters.get("serpentine", True)),
            "positions_um": np.zeros((pt, pz, py, px, 3), dtype=np.float32),
            "linear_index_map": np.full((pt, pz, py, px), -1, dtype=np.int32),
            "acquisition_order_indices": np.full((total_pixels, 4), -1, dtype=np.int32),
            "acquisition_order_positions_um": np.zeros((total_pixels, 3), dtype=np.float32),
            "timestamps_s": np.full((total_pixels,), np.nan, dtype=np.float64),
            "pixel_valid": np.zeros((pt, pz, py, px), dtype=bool),
            "metadata": {
                "acquisition_parameters": dict(scan_parameters or {}),
                "brillouin_parameters": dict(brillouin_params or {}),
                "raman_parameters": dict(raman_params or {}),
                "save_parameters": dict(save_params or {}),
                "modalities": {
                    "brillouin": bool(modes.get("brillouin", False)),
                    "raman": bool(modes.get("raman", False)),
                },
                "reconstruction_hint": {
                    "positions_key": "positions_um",
                    "order_key": "acquisition_order_indices",
                    "timestamp_key": "timestamps_s",
                },
            },
        }

        dataset["positions"] = np.zeros((total_pixels, 3), dtype=np.float32)
        dataset["origin_um"] = {"x": x_origin_um, "y": y_origin_um, "z": z_origin_um}

        for t in range(pt):
            for z in range(pz):
                z_um = z_origin_um if pz <= 1 else z_origin_um - sz / 2.0 + z * stepz
                for y in range(py):
                    y_um = y_origin_um if py <= 1 else y_origin_um - sy / 2.0 + y * stepy
                    for x in range(px):
                        x_um = x_origin_um if px <= 1 else x_origin_um - sx / 2.0 + x * stepx
                        dataset["positions_um"][t, z, y, x] = (x_um, y_um, z_um)

        if modes.get("brillouin", False):
            roi = self._get_effective_brillouin_roi(brillouin_params)
            h = int(roi["height"])
            w = int(roi["width"])
            dataset["brillouin_images"] = np.zeros((pt, pz, py, px, h, w), dtype=np.float32)

            dataset["metadata"]["brillouin_roi"] = {
                "enabled": bool(roi["enabled"]),
                "x": int(roi["x"]),
                "y": int(roi["y"]),
                "width": int(roi["width"]),
                "height": int(roi["height"]),
                "full_width": int(roi["full_width"]),
                "full_height": int(roi["full_height"]),
            }

        if modes.get("raman", False):
            wavelengths = self._build_raman_wavelengths(raman_params)
            dataset["raman_wavelengths"] = wavelengths.astype(np.float32, copy=False)
            dataset["raman_spectra"] = np.zeros((pt, pz, py, px, wavelengths.size), dtype=np.float32)

        return dataset

    # ==========================================================
    # Boucle mapping
    # ==========================================================
    def _schedule_next_pixel(self, delay_ms: int):
        if not self.running:
            return
        self.mapping_timer.start(max(0, int(delay_ms)))

    def _stop_mapping_internal(self, emit_finished: bool, status_text: str):
        if not self.running and self.mapping_timer.isActive():
            self.mapping_timer.stop()

        was_running = self.running
        self.running = False
        self.mapping_timer.stop()

        if was_running:
            self.sigStatusMessage.emit(status_text)

        if emit_finished and self.dataset is not None:
            try:
                if "brillouin_images" in self.dataset and self.current_pixel_index > 0:
                    last_idx = min(self.current_pixel_index - 1, len(self.pixel_list) - 1)
                    info = self.pixel_list[last_idx]
                    t = int(info.get("t_index", 0))
                    z = int(info["z_index"])
                    y = int(info["y_index"])
                    x = int(info["x_index"])
                    self.sigImageUpdate.emit(self.dataset["brillouin_images"][t, z, y, x])

                if "raman_spectra" in self.dataset and self.current_pixel_index > 0:
                    last_idx = min(self.current_pixel_index - 1, len(self.pixel_list) - 1)
                    info = self.pixel_list[last_idx]
                    t = int(info.get("t_index", 0))
                    z = int(info["z_index"])
                    y = int(info["y_index"])
                    x = int(info["x_index"])
                    self.sigSpectrumUpdate.emit(self.dataset["raman_spectra"][t, z, y, x])
            except Exception:
                pass

            self.sigFinished.emit(self.dataset)

    def _acquire_next_pixel(self):
        if not self.running:
            return

        if self.current_pixel_index >= len(self.pixel_list):
            self._stop_mapping_internal(
                emit_finished=True,
                status_text="Spectro acquisition finished",
            )
            return

        try:
            info = self.pixel_list[self.current_pixel_index]
            x = int(info["x_index"])
            y = int(info["y_index"])
            z = int(info["z_index"])

            if info.get("is_backlash", False):
                pos_um = self.dataset["positions_um"][z, y, x]
                backlash_x_um = float(self._scan_parameters.get("backlash_x_um", 0.0) or 0.0)
                backlash_x_forward_um = float(self._scan_parameters.get("backlash_x_forward_um", 0.0) or 0.0)
                offset = backlash_x_um if y % 2 == 1 else backlash_x_forward_um
                x_overshoot = float(pos_um[0]) + offset
                self._move_stage_to_pixel_blocking(x_overshoot, float(pos_um[1]), float(pos_um[2]))
                self.current_pixel_index += 1
                self._schedule_next_pixel(delay_ms=0)
                return

            lin = int(info["linear_index"])

            real_total = sum(1 for p in self.pixel_list if not p.get("is_backlash", False))

            pos_um = self.dataset["positions_um"][z, y, x]
            x_um = float(pos_um[0])
            y_um = float(pos_um[1])
            z_um = float(pos_um[2])

            # -------------------------------------------------
            # Déplacement réel point par point
            # -------------------------------------------------
            self._move_stage_to_pixel_blocking(x_um, y_um, z_um)

            settle_ms = float(self._scan_parameters.get("settle_ms", 0.0) or 0.0)
            if settle_ms > 0:
                time.sleep(settle_ms / 1000.0)

            acq_t0 = time.perf_counter()

            self.sigStatusMessage.emit(
                f"Spectro {lin + 1}/{real_total} | X={x + 1}/{self._scan_parameters.get('pixels_x', 1)} "
                f"Y={y + 1}/{self._scan_parameters.get('pixels_y', 1)} "
                f"Z={z + 1}/{self._scan_parameters.get('pixels_z', 1)}"
            )

            # ---- en mock on ne bouge pas encore le hardware, mais on remplit
            #      la logique dataset comme si le move était réel
            self.dataset["linear_index_map"][z, y, x] = lin
            self.dataset["acquisition_order_indices"][lin] = (z, y, x)
            self.dataset["acquisition_order_positions_um"][lin] = (x_um, y_um, z_um)
            self.dataset["positions"][lin] = (x_um, y_um, z_um)
            self.dataset["timestamps_s"][lin] = (
                time.perf_counter() - self._mapping_started_t0
                if self._mapping_started_t0 is not None else np.nan
            )

            # ---- Brillouin
            if "brillouin_images" in self.dataset:
                image = self._try_acquire_brillouin_from_hardware(self._brillouin_params)
                if image is None:
                    image = self._mock_brillouin_image(
                        x_idx=x,
                        y_idx=y,
                        z_idx=z,
                        t_index=lin,
                        params=self._brillouin_params,
                    )

                self.dataset["brillouin_images"][z, y, x] = image
                self.sigImageUpdate.emit(image)

            # ---- Raman
            if "raman_spectra" in self.dataset:
                wavelengths = self.dataset["raman_wavelengths"]
                spectrum = self._mock_raman_spectrum(
                    x_idx=x,
                    y_idx=y,
                    z_idx=z,
                    t_index=lin,
                    params=self._raman_params,
                    wavelengths=wavelengths,
                )
                self.dataset["raman_spectra"][z, y, x] = spectrum
                self.sigSpectrumUpdate.emit(spectrum)

            self.dataset["pixel_valid"][z, y, x] = True

            self.current_pixel_index += 1
            self.sigProgress.emit(lin + 1, real_total)

            # cadence point par point :
            # move -> settle -> acquisition
            # si l'acquisition mock est plus rapide que exposure_ms,
            # on complète le temps restant ici.
            exposure_ms = float(self._scan_parameters.get("exposure_ms", 0.0) or 0.0)
            elapsed_acq_s = time.perf_counter() - acq_t0
            remaining_s = max(0.0, exposure_ms / 1000.0 - elapsed_acq_s)

            if remaining_s > 0:
                time.sleep(remaining_s)

            self._schedule_next_pixel(delay_ms=0)

        except Exception as e:
            self.running = False
            self.mapping_timer.stop()
            self.sigFailed.emit(f"Spectro mapping failed: {e}")

    # ==========================================================
    # Générateurs mock
    # ==========================================================
    def _parse_binning_factor(self, params) -> int:
        text = str((params or {}).get("binning", "1x1"))
        try:
            bx = int(text.lower().split("x")[0])
            return max(1, bx)
        except Exception:
            return 1

    def _get_brillouin_sensor_shape(self):
        return int(self._BRILLOUIN_SHAPE[0]), int(self._BRILLOUIN_SHAPE[1])

    def _get_binned_full_shape(self, params):
        full_h, full_w = self._get_brillouin_sensor_shape()
        binning = self._parse_binning_factor(params)
        return max(1, full_h // binning), max(1, full_w // binning)

    def _get_effective_brillouin_roi(self, params):
        params = dict(params or {})
        full_h, full_w = self._get_binned_full_shape(params)

        enabled = bool(params.get("roi_enabled", False))

        if not enabled:
            return {
                "enabled": False,
                "x": 0,
                "y": 0,
                "width": full_w,
                "height": full_h,
                "full_width": full_w,
                "full_height": full_h,
            }

        x = int(params.get("roi_x", 0) or 0)
        y = int(params.get("roi_y", 0) or 0)
        w = int(params.get("roi_width", full_w) or full_w)
        h = int(params.get("roi_height", full_h) or full_h)

        x = max(0, min(x, full_w - 1))
        y = max(0, min(y, full_h - 1))
        w = max(1, min(w, full_w - x))
        h = max(1, min(h, full_h - y))

        return {
            "enabled": True,
            "x": x,
            "y": y,
            "width": w,
            "height": h,
            "full_width": full_w,
            "full_height": full_h,
        }
    
    def _build_raman_wavelengths(self, raman_params):
        center = float(raman_params.get("center_nm", 700.0) or 700.0)
        span = max(1e-6, float(raman_params.get("span_nm", 100.0) or 100.0))
        return np.linspace(center - 0.5 * span, center + 0.5 * span, self._RAMAN_POINTS)

    def _compute_brillouin_live_interval_ms(self, params):
        fps = float(params.get("fps", 10.0) or 10.0)
        exposure_ms = float(params.get("exposure_ms", 10.0) or 10.0)

        if fps > 0:
            frame_ms = 1000.0 / fps
            return max(1, int(round(max(frame_ms, exposure_ms))))
        return max(1, int(round(exposure_ms)))

    def _compute_raman_live_interval_ms(self, params):
        exposure_ms = float(params.get("exposure_ms", 100.0) or 100.0)
        averages = max(1, int(params.get("averages", 1) or 1))
        return max(1, int(round(exposure_ms * averages)))

    def _mock_brillouin_image(self, x_idx, y_idx, z_idx, t_index, params):
        full_h, full_w = self._get_binned_full_shape(params)
        yy, xx = np.mgrid[0:full_h, 0:full_w].astype(np.float32)

        cx = full_w * (0.50 + 0.12 * np.sin(0.17 * x_idx + 0.11 * t_index))
        cy = full_h * (0.50 + 0.12 * np.cos(0.19 * y_idx + 0.07 * t_index))

        sigma_x = 6.0 + 0.3 * (x_idx % 7)
        sigma_y = 6.5 + 0.25 * (y_idx % 5)

        binning = self._parse_binning_factor(params)
        sigma_x = sigma_x / max(1.0, 0.65 * binning)
        sigma_y = sigma_y / max(1.0, 0.65 * binning)

        g1 = np.exp(-(((xx - cx) ** 2) / (2.0 * sigma_x ** 2) + ((yy - cy) ** 2) / (2.0 * sigma_y ** 2)))
        g2 = 0.55 * np.exp(
            -(((xx - (full_w - cx)) ** 2) / (2.0 * (sigma_x * 1.4) ** 2)
              + ((yy - cy) ** 2) / (2.0 * (sigma_y * 1.2) ** 2))
        )

        rings_r = np.sqrt((xx - full_w / 2.0) ** 2 + (yy - full_h / 2.0) ** 2)
        rings = 0.08 * (1.0 + np.cos(0.55 * rings_r - 0.35 * z_idx))

        gradient = (
            0.15 * (xx / max(1.0, float(full_w - 1)))
            + 0.10 * (yy / max(1.0, float(full_h - 1)))
        )

        exposure_ms = float(params.get("exposure_ms", 10.0) or 10.0)
        gain = float(params.get("gain", 0.0) or 0.0)

        scale = 200.0 + 2.5 * exposure_ms + 8.0 * gain
        image = scale * (g1 + g2 + rings + gradient)

        rng = np.random.default_rng(
            seed=(1000003 + 97 * x_idx + 193 * y_idx + 389 * z_idx + 17 * t_index)
        )
        noise = rng.normal(loc=0.0, scale=max(2.0, 0.015 * scale), size=(full_h, full_w))

        image = image + noise
        image = np.clip(image, 0.0, None)
        image = image.astype(np.float32, copy=False)

        roi = self._get_effective_brillouin_roi(params)
        x0 = int(roi["x"])
        y0 = int(roi["y"])
        w = int(roi["width"])
        h = int(roi["height"])

        return image[y0:y0 + h, x0:x0 + w]

    def _mock_raman_spectrum(self, x_idx, y_idx, z_idx, t_index, params, wavelengths):
        center = float(params.get("center_nm", 700.0) or 700.0)
        span = max(1e-6, float(params.get("span_nm", 100.0) or 100.0))
        exposure_ms = float(params.get("exposure_ms", 100.0) or 100.0)
        averages = max(1, int(params.get("averages", 1) or 1))

        x = np.asarray(wavelengths, dtype=np.float32)

        p1 = center - 0.18 * span + 0.015 * span * np.sin(0.20 * x_idx + 0.07 * t_index)
        p2 = center + 0.08 * span + 0.020 * span * np.cos(0.17 * y_idx + 0.05 * t_index)
        p3 = center + 0.24 * span + 0.010 * span * np.sin(0.31 * z_idx + 0.03 * t_index)

        s1 = 0.025 * span
        s2 = 0.040 * span
        s3 = 0.030 * span

        g1 = 1.00 * np.exp(-0.5 * ((x - p1) / max(s1, 1e-6)) ** 2)
        g2 = 0.65 * np.exp(-0.5 * ((x - p2) / max(s2, 1e-6)) ** 2)
        g3 = 0.40 * np.exp(-0.5 * ((x - p3) / max(s3, 1e-6)) ** 2)

        baseline = 0.08 + 0.04 * np.sin((x - center) / max(span, 1e-6) * 2.5 * np.pi)
        amp = max(50.0, 0.9 * exposure_ms * np.sqrt(averages))

        spectrum = amp * (g1 + g2 + g3 + baseline)

        rng = np.random.default_rng(seed=(2000003 + 53 * x_idx + 101 * y_idx + 211 * z_idx + 13 * t_index))
        noise_sigma = max(0.5, 0.035 * amp / np.sqrt(averages))
        spectrum = spectrum + rng.normal(loc=0.0, scale=noise_sigma, size=x.shape)

        spectrum = np.clip(spectrum, 0.0, None)
        return spectrum.astype(np.float32, copy=False)