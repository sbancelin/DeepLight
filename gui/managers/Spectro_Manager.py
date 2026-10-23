import threading
import time
import numpy as np
from PySide6.QtCore import QCoreApplication, QObject, Qt, Signal, QTimer, QThread, Slot

from ..widgets.Log_Widget import logger
from .Raman_Detector import AcquisitionAborted

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


class _RamanWorker(QObject):
    """Raman spectra off the GUI thread: one (snap) or until stopped (live).

    A Raman exposure lasts seconds, sometimes minutes; on the GUI thread the
    window would freeze for that long. Exposure and averages may change
    between two spectra without restarting the loop.
    """

    sigSpectrum = Signal(object)        # RamanSpectrum
    sigFailed = Signal(str)
    sigFinished = Signal()

    def __init__(self, raman, params=None, single_shot: bool = False):
        super().__init__()
        self._raman = raman
        self._params = dict(params or {})
        self._params_lock = threading.Lock()
        self._single_shot = bool(single_shot)
        self._running = False
        # This worker's own cancel token: set by stop() from the GUI thread,
        # watched by the detector from this one. Never cleared, so a stop
        # cannot be lost to a race with the start of the next exposure.
        self._cancel = threading.Event()
        self.aborted = False

    def update_params(self, params: dict):
        with self._params_lock:
            self._params.update(dict(params or {}))

    def stop(self):
        """Stop now: the exposure in progress is cut short and discarded."""
        self._running = False
        self._cancel.set()

    @Slot()
    def run(self):
        self._running = True
        try:
            while self._running:
                with self._params_lock:
                    params = dict(self._params)
                spectrum = self._raman.acquire_spectrum(
                    float(params.get("exposure_ms", 100.0) or 100.0),
                    int(params.get("averages", 1) or 1),
                    cancel=self._cancel,
                )
                if not self._running:
                    break
                self.sigSpectrum.emit(spectrum)
                if self._single_shot:
                    break
        except AcquisitionAborted:
            # Asked for: not a failure, and nothing to show.
            self.aborted = True
        except Exception as e:
            self.sigFailed.emit(str(e))
        finally:
            self._running = False
            self.sigFinished.emit()


class _RamanSpectrographWorker(QObject):
    """One spectrograph job off the GUI thread: connect, turn the turret, recentre.

    With neither a grating nor a centre it only connects and reads the
    setting back -- what the Raman panel needs to fill its grating list.
    """

    sigDone = Signal(object)            # {"setting": ..., "gratings": [...], "axis": ...}
    sigFailed = Signal(str)
    sigFinished = Signal()

    def __init__(self, raman, grating: int | None = None, center_nm: float | None = None):
        super().__init__()
        self._raman = raman
        self._grating = grating
        self._center_nm = center_nm

    @Slot()
    def run(self):
        try:
            if self._grating is not None:
                self._raman.set_grating(int(self._grating))
            if self._center_nm is not None:
                self._raman.set_center_wavelength_nm(float(self._center_nm))
            self.sigDone.emit({
                "setting": self._raman.setting(),
                "gratings": self._raman.gratings(),
                "axis": self._raman.spectral_axis().to_metadata(),
            })
        except Exception as e:
            self.sigFailed.emit(str(e))
        finally:
            self.sigFinished.emit()


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
        raman=None,
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
        self.raman = raman
        self._running = False
        self._BRILLOUIN_SHAPE = (1200, 1200)
        # Coupe une pose Raman en cours (cf. _RamanWorker). Le Brillouin n'y
        # est pas branché : son acquisition reste celle validée au banc.
        self._cancel = threading.Event()

        # throttling des updates UI (l'émission de chaque image 1200x1200
        # float32 à chaque pixel charge inutilement le thread GUI)
        self._ui_period_s = 0.5
        self._last_img_emit_t = 0.0
        self._last_spec_emit_t = 0.0

    def stop(self):
        self._running = False
        self._cancel.set()

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

    @Slot()
    def run(self):
        self._running = True
        # Overwritten by the try/except below; announced at the very end.
        outcome = ("failed", "Spectro mapping ended unexpectedly")
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
            raman_exposure_ms = float(self.raman_params.get("exposure_ms", 100.0) or 100.0)
            raman_averages = max(1, int(self.raman_params.get("averages", 1) or 1))
            pixel_done = 0

            if "brillouin_images" in self.dataset:
                prepare_fn = getattr(self.hardware, "prepare_brillouin_for_scan", None)
                if prepare_fn is not None:
                    prepare_fn(self.brillouin_params)

            if "raman_spectra" in self.dataset and self.raman is None:
                raise RuntimeError("Raman mode is on but no Raman spectrometer is available.")

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
                        f"Time lapse: waiting {int(repeat_delay_s)} s before repetition {t + 1}/{n_repeats}..."
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
                    try:
                        spectrum = self.raman.acquire_spectrum(
                            raman_exposure_ms, raman_averages, cancel=self._cancel
                        )
                    except AcquisitionAborted:
                        # Stopped mid-exposure: this point is not acquired
                        # (pixel_valid stays False), the map ends here and is
                        # kept as far as it went.
                        break
                    if spectrum.values.size != self.dataset["raman_spectra"].shape[-1]:
                        # The detector geometry changed mid-map (rows or
                        # binning edited in Settings): stop rather than file
                        # spectra that no longer match the recorded axis.
                        raise RuntimeError(
                            f"Raman spectrum of {spectrum.values.size} values, expected "
                            f"{self.dataset['raman_spectra'].shape[-1]}: the detector "
                            "settings changed during the acquisition."
                        )
                    self.dataset["raman_spectra"][t, z, y, x] = spectrum.values

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

            outcome = ("finished", self.dataset)

        except Exception as e:
            if self._cancel.is_set():
                # Stop was asked for: whatever it interrupted -- a stage move
                # cut short, a positioner stopped mid-way -- is its consequence,
                # not a failure. The map is kept as far as it went, exactly as
                # when Stop lands between two points; reporting a failure here
                # used to discard every point already acquired.
                logger.info(f"[Spectro] map stopped during a step ({e}); kept as far as it went")
                outcome = ("finished", self.dataset)
            else:
                outcome = ("failed", f"Spectro mapping failed: {e}")

        finally:
            # The outcome is announced only once the stage is back (end of this
            # block): "finished" used to go out before the return to origin, so
            # the window reported the map done -- and accepted a new one -- while
            # this worker was still driving the stage.
            origin = (self.dataset or {}).get("origin_um")
            if origin:
                try:
                    self.sigStatusMessage.emit("Returning to the initial position...")
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
                except Exception as e:
                    # A second Stop during the return lands here: the stage is
                    # left where it is, as asked.
                    logger.info(f"[Spectro] return to origin not completed ({e})")
                finally:
                    self._running = False

            kind, payload = outcome
            if kind == "finished":
                self.sigFinished.emit(payload)
            else:
                self.sigFailed.emit(payload)

class SpectroManager(QObject):
    """
    Spectro manager: Brillouin and Raman, single shots, live views and maps.

    Roles:
    - Brillouin snap / live
    - Raman snap / live, and the spectrograph's grating and centre
    - XYZ serpentine mapping driven from SpectroPanelWidget
    - production of a reconstruction-friendly dataset
    - API compatible with what MainWindow already calls

    Everything that waits on an instrument -- an exposure, a turret -- runs
    in a worker thread, so the window keeps answering.
    """

    sigImageUpdate = Signal(object)                     # np.ndarray 2D
    sigSpectrumUpdate = Signal(object)                  # RamanSpectrum
    sigStatusMessage = Signal(str)
    sigProgress = Signal(int, int)
    sigEta = Signal(float, float)
    sigFinished = Signal(object)                        # dataset
    sigFailed = Signal(str)
    sigBrillouinLiveRunningChanged = Signal(bool)
    sigRamanLiveRunningChanged = Signal(bool)
    sigRamanSpectrographChanged = Signal(object)        # {"setting", "gratings", "axis"}
    sigRamanSpectrographBusy = Signal(bool)

    _BRILLOUIN_SHAPE = (1200, 1200)

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
        self._mapping_stop_requested = False

        # --- live Brillouin
        self._brillouin_live_running = False
        self._brillouin_live_params = {}
        self._brillouin_live_counter = 0
        self.brillouin_live_timer = QTimer(self)
        self.brillouin_live_timer.timeout.connect(self._on_brillouin_live_timeout)
        self._brillouin_live_thread = None
        self._brillouin_live_worker = None

        # --- Raman : live, snap et mouvements du spectrographe, chacun dans un
        # worker. Les threads restent référencés ici jusqu'à leur fin réelle
        # (cf. _start_worker) : une pose Raman peut durer bien plus longtemps
        # qu'un wait() raisonnable, et détruire un QThread qui tourne encore
        # fait planter Qt.
        self._raman_live_running = False
        self._raman_live_worker = None
        self._raman_snap_running = False
        self._raman_snap_worker = None
        self._raman_spectrograph_busy = False
        self._threads = {}               # QThread -> (worker, on_finished)

    # ==========================================================
    # Threads de travail
    # ==========================================================
    def _start_worker(self, worker: QObject, on_finished=None) -> QThread:
        """Run `worker.run` in its own thread, kept alive until it is done.

        The thread quits when the worker says it has finished, and both are
        released only then -- never by a timed wait that gives up on a long
        exposure and drops a thread that is still running.
        """
        thread = QThread(self)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        # Direct: quit() is thread-safe, and queued to the GUI thread it would
        # never run while that thread waits on this one (see shutdown()).
        worker.sigFinished.connect(thread.quit, Qt.DirectConnection)
        # A bound slot of this manager, not a closure: Qt then queues the call
        # to the GUI thread. A closure would run in the finishing thread itself.
        thread.finished.connect(self._on_worker_thread_finished)
        self._threads[thread] = (worker, on_finished)
        thread.start()
        return thread

    @Slot()
    def _on_worker_thread_finished(self):
        thread = self.sender()
        worker, on_finished = self._threads.pop(thread, (None, None))
        if worker is not None:
            worker.deleteLater()
        if thread is not None:
            thread.deleteLater()
        if on_finished is not None:
            on_finished()

    def has_running_workers(self) -> bool:
        return bool(self._threads)

    def shutdown(self, timeout_s: float = 120.0):
        """Stop everything and wait for the worker threads to end. At exit only.

        A Raman exposure is cut short; a map still returns its stage to the
        starting position before its thread ends, and is saved as far as it
        went when its end is processed below. Waiting is the point: destroying
        a QThread that is still running takes the whole application down. The
        timeout only guards against an instrument that never answers.
        """
        deadline = time.monotonic() + float(timeout_s)

        def remaining_ms() -> int:
            return max(0, int((deadline - time.monotonic()) * 1000))

        self.stop_all_live()

        mapping_thread = self._mapping_thread
        if self.running or mapping_thread is not None:
            if self._mapping_worker is not None:
                self._mapping_worker.stop()
            if mapping_thread is not None:
                mapping_thread.quit()
                if not mapping_thread.wait(remaining_ms()):
                    logger.warning("[Spectro] the mapping thread did not end in time")
            self._mapping_worker = None
            self._mapping_thread = None
            self.running = False

        for thread in list(self._threads):
            if not thread.wait(remaining_ms()):
                logger.warning("[Spectro] a Raman worker did not end in time")

        # Any other thread of ours still running: a mapping thread whose
        # reference stop_mapping() dropped after its 3 s wait, its worker still
        # in the return to origin. Destroying it running ended the process
        # abnormally; waiting for it ends it cleanly.
        for thread in self.findChildren(QThread):
            if thread.isRunning() and not thread.wait(remaining_ms()):
                logger.warning("[Spectro] a spectro thread did not end in time")

        # Their finished() cleanups were queued to this thread: run them now.
        QCoreApplication.sendPostedEvents()
        QCoreApplication.processEvents()

    def _raman(self):
        """The Raman chain, or an error saying why there is none."""
        raman = None
        getter = getattr(self.hardware, "raman_spectrometer", None)
        if callable(getter):
            raman = getter()
        if raman is None:
            reason_fn = getattr(self.hardware, "raman_unavailable_reason", None)
            reason = reason_fn() if callable(reason_fn) else None
            raise RuntimeError(
                "Raman spectrometer unavailable" + (f": {reason}" if reason else "")
            )
        return raman

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
    def _release_mapping(self):
        """Forget the map's worker and thread, once the worker has announced its end.

        No wait: the worker announces its end as the very last thing it does
        (after the return to origin), and its thread quits on that signal by
        itself (see start_mapping). Its deletion is left to Qt for the same
        reason -- deleteLater() runs once the thread has actually finished.
        """
        thread = self._mapping_thread
        self._mapping_worker = None
        self._mapping_thread = None
        self._mapping_stop_requested = False
        self.running = False
        if thread is not None:
            thread.finished.connect(thread.deleteLater)

    @Slot(object)
    def _on_mapping_worker_finished(self, dataset):
        self.dataset = dataset
        self._release_mapping()
        self.sigFinished.emit(dataset)

    @Slot(str)
    def _on_mapping_worker_failed(self, message: str):
        self._release_mapping()
        self.sigFailed.emit(message)

    def is_stopping(self) -> bool:
        """A map was asked to stop and has not finished yet (returning to origin)."""
        return bool(self.running and getattr(self, "_mapping_stop_requested", False))

    def stop_mapping(self):
        """Ask the map to stop; it ends on its own, and announces it.

        Not a wait: the worker finishes the step in progress (a Raman exposure
        is cut short), brings the stage back to its starting position, then
        emits sigFinished with the map as far as it went, which is saved like
        any other. Until then the map is still running -- a new one cannot be
        started while this one is still driving the stage.

        A second call during the return to origin interrupts the return too.
        """
        if self._mapping_worker is None:
            self.running = False
            return
        self._mapping_stop_requested = True
        try:
            self._mapping_worker.stop()
        except Exception as e:
            logger.debug(f"[Spectro] stop request not delivered: {e}")
        self.sigStatusMessage.emit("Stopping: returning the stage to its starting position...")

    def stop_all_live(self):
        self.stop_live_brillouin()
        self.stop_live_raman()
        self.stop_raman_snap()

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

        raman = None
        try:
            if bool(self._modes.get("raman", False)):
                raman = self._raman()
            self.dataset = self._create_dataset(
                self._scan_parameters,
                self._modes,
                self._brillouin_params,
                self._raman_params,
                self._save_parameters,
                raman=raman,
            )
        except Exception as e:
            self.sigFailed.emit(f"Spectro acquisition not started: {e}")
            return

        real_pixels = len(self.pixel_list)
        now = time.perf_counter()
        self._last_brillouin_ui_emit_t = now - self._ui_update_period_s
        self._last_raman_ui_emit_t = now - self._ui_update_period_s
        self.running = True
        self._mapping_stop_requested = False
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
            raman=raman,
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
        # The thread ends when its worker does, directly. stop_mapping() gives
        # up waiting after 3 s and drops its references while the worker may
        # still be returning the stage to origin; the slots above then find no
        # thread to quit, and the idle thread outlived the window -- Python
        # exited with code 127 after every stopped map.
        self._mapping_worker.sigFinished.connect(self._mapping_thread.quit, Qt.DirectConnection)
        self._mapping_worker.sigFailed.connect(self._mapping_thread.quit, Qt.DirectConnection)

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
        """One spectrum, acquired off the GUI thread."""
        if self.running or self._raman_live_running or self._raman_snap_running:
            return
        try:
            raman = self._raman()
        except Exception as e:
            self.sigFailed.emit(f"Raman snap failed: {e}")
            return

        worker = _RamanWorker(raman, params, single_shot=True)
        worker.sigSpectrum.connect(self.sigSpectrumUpdate)
        worker.sigFailed.connect(lambda message: self.sigFailed.emit(f"Raman snap failed: {message}"))

        def _done():
            self._raman_snap_running = False
            self._raman_snap_worker = None
            self.sigStatusMessage.emit("Raman snap stopped" if worker.aborted else "Raman snap done")

        self._raman_snap_running = True
        self._raman_snap_worker = worker
        self.sigStatusMessage.emit("Raman snap...")
        self._start_worker(worker, on_finished=_done)

    def stop_raman_snap(self):
        """Cut a snap in progress short; its spectrum is discarded."""
        worker = getattr(self, "_raman_snap_worker", None)
        if worker is not None:
            worker.stop()

    def start_live_raman(self, params=None):
        if self.running or self._raman_live_running:
            return
        try:
            raman = self._raman()
        except Exception as e:
            self.sigFailed.emit(f"Raman live failed: {e}")
            self.sigRamanLiveRunningChanged.emit(False)
            return

        worker = _RamanWorker(raman, params, single_shot=False)
        worker.sigSpectrum.connect(self._on_raman_live_spectrum)
        worker.sigFailed.connect(self._on_raman_live_failed)
        self._raman_live_worker = worker
        self._raman_live_running = True
        self.sigRamanLiveRunningChanged.emit(True)
        self.sigStatusMessage.emit("Raman live started")
        self._start_worker(worker)

    def stop_live_raman(self):
        """Stop the live view. The exposure in progress is cut short by the
        detector and discarded; the worker's thread ends once the camera has
        confirmed the stop."""
        if not self._raman_live_running:
            return
        self._raman_live_running = False
        worker, self._raman_live_worker = self._raman_live_worker, None
        if worker is not None:
            worker.stop()
            try:
                worker.sigSpectrum.disconnect(self._on_raman_live_spectrum)
            except (RuntimeError, TypeError):
                pass
        self.sigRamanLiveRunningChanged.emit(False)
        self.sigStatusMessage.emit("Raman live stopped")

    def update_raman_live_params(self, params: dict):
        """Exposure or averages changed: the next live spectrum uses them."""
        if self._raman_live_worker is not None:
            self._raman_live_worker.update_params(params)

    @Slot(object)
    def _on_raman_live_spectrum(self, spectrum):
        # Only the current live worker paints: after a stop and a quick
        # restart, the previous one may still deliver its last exposure.
        if self._raman_live_running and self.sender() is self._raman_live_worker:
            self.sigSpectrumUpdate.emit(spectrum)

    @Slot(str)
    def _on_raman_live_failed(self, message: str):
        if self.sender() is not self._raman_live_worker:
            # A worker already stopped, failing on its way out: worth a line
            # in the log, but it must not switch off the live view now running.
            logger.warning(f"[Spectro] stopped Raman live worker failed: {message}")
            return
        self._raman_live_running = False
        self._raman_live_worker = None
        self.sigRamanLiveRunningChanged.emit(False)
        self.sigFailed.emit(f"Raman live failed: {message}")

    # --------------------------
    # Spectrographe Raman
    # --------------------------
    def refresh_raman_spectrograph(self):
        """Connect if needed and report the gratings and the current setting."""
        self.move_raman_spectrograph()

    def move_raman_spectrograph(self, grating: int | None = None, center_nm: float | None = None):
        """Turn the turret and/or recentre, off the GUI thread.

        Refused during a map: the spectral axis recorded at its start must stay
        true for every point. A live view may run meanwhile; its next spectrum
        simply waits for the move.
        """
        moving = grating is not None or center_nm is not None
        if moving and self.running:
            self.sigFailed.emit("The spectrograph cannot move during a spectro acquisition.")
            return
        if self._raman_spectrograph_busy:
            self.sigStatusMessage.emit("Spectrograph busy, request ignored.")
            return
        try:
            raman = self._raman()
        except Exception as e:
            self.sigFailed.emit(str(e))
            return

        worker = _RamanSpectrographWorker(raman, grating=grating, center_nm=center_nm)
        worker.sigDone.connect(self.sigRamanSpectrographChanged)
        worker.sigFailed.connect(lambda message: self.sigFailed.emit(f"Spectrograph: {message}"))

        def _done():
            self._raman_spectrograph_busy = False
            self.sigRamanSpectrographBusy.emit(False)

        self._raman_spectrograph_busy = True
        self.sigRamanSpectrographBusy.emit(True)
        if moving:
            self.sigStatusMessage.emit("Spectrograph moving...")
        self._start_worker(worker, on_finished=_done)

    # ==========================================================
    # Timers live
    # ==========================================================
    def _on_brillouin_live_timeout(self):
        # ancien chemin timer synchrone non utilisé
        pass

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
        raman=None,
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

            # The ROI checkbox is per-session UI state: nothing saves it, so it
            # comes back unticked at every launch and a map then stores the whole
            # sensor per point. That is the difference between a file of a few
            # hundred MB and one of several GB -- past 4 GB tifffile switches to
            # BigTIFF on its own -- and until it is said out loud it is invisible
            # until the file will not open.
            nbytes = int(dataset["brillouin_images"].nbytes)
            crop = (f"ROI {w}x{h} at ({roi['x']},{roi['y']})" if roi["enabled"]
                    else f"no ROI -- the whole {roi['full_width']}x{roi['full_height']} field")
            logger.notice(
                f"[Brillouin] {pt * pz * py * px} point(s) x {w}x{h} px, {crop}, "
                f"{nbytes / 2**30:.2f} GiB in memory and in the file"
            )
            if nbytes > 2 ** 32 - 2 ** 25:
                logger.warning(
                    "[Brillouin] over 4 GiB: the OME-TIFF will be a BigTIFF, which "
                    "ImageJ's own reader does not open (Fiji reads it through "
                    "Bio-Formats). Tick ROI to crop the sensor, raise the binning, "
                    "or save as OME-Zarr."
                )

        if modes.get("raman", False):
            if raman is None:
                raise RuntimeError("Raman mode is on but no Raman spectrometer is available.")

            # The axis and the whole spectrograph/detector state are read once,
            # here: moves are refused during a map, so they hold for every point.
            # The detector is set up for the map first, so its record shows the
            # map's exposure, not the one the live view left behind.
            raman.configure(float(raman_params.get("exposure_ms", 100.0) or 100.0))
            axis = raman.spectral_axis()
            n_spectral = int(axis.values.size)
            dataset["raman_axis"] = np.asarray(axis.values, dtype=np.float64)
            dataset["raman_axis_unit"] = axis.unit
            dataset["raman_spectra"] = np.zeros((pt, pz, py, px, n_spectral), dtype=np.float32)

            state = raman.state()
            excitation_nm = float(raman_params.get("excitation_nm", 0.0) or 0.0)
            state["excitation_nm"] = excitation_nm if excitation_nm > 0.0 else None
            state["exposure_ms"] = float(raman_params.get("exposure_ms", 0.0) or 0.0)
            state["averages"] = max(1, int(raman_params.get("averages", 1) or 1))
            dataset["metadata"]["raman"] = state

            nbytes = int(dataset["raman_spectra"].nbytes)
            logger.notice(
                f"[Raman] {pt * pz * py * px} point(s) x {n_spectral} spectral pixels, "
                f"axis in {axis.unit} ({axis.note}), {nbytes / 2**20:.1f} MiB"
            )

        return dataset

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
    
    def _compute_brillouin_live_interval_ms(self, params):
        fps = float(params.get("fps", 10.0) or 10.0)
        exposure_ms = float(params.get("exposure_ms", 10.0) or 10.0)

        if fps > 0:
            frame_ms = 1000.0 / fps
            return max(1, int(round(max(frame_ms, exposure_ms))))
        return max(1, int(round(exposure_ms)))

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
