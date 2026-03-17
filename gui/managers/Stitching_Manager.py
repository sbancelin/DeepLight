# DeepLight/gui/managers/Stitching_Manager.py

from __future__ import annotations
from dataclasses import dataclass
from PySide6.QtCore import QObject, Signal, Slot, QTimer
import numpy as np


@dataclass
class MosaicRunConfig:
    tiles_x: int
    tiles_y: int
    overlap_px: int
    channel: str
    tile_width_px: int
    tile_height_px: int
    tile_width_um: float
    tile_height_um: float
    step_x_um: float
    step_y_um: float
    start_x_rel_um: float
    start_y_rel_um: float
    stage_speed_x_mm_s: float
    stage_speed_y_mm_s: float
    tol_x_um: float
    tol_y_um: float


class StitchingManager(QObject):
    """
    Orchestrateur de mosaïque XY non bloquant.

    Philosophie :
    - ne bloque jamais l'UI
    - s'appuie sur les managers existants
    - avance par événements : move terminé -> acquisition -> collage -> move suivant
    - compatible future backend hardware tant que l'API stable est conservée
    """

    mosaic_updated = Signal(object)         # np.ndarray
    status_changed = Signal(str)
    run_started = Signal()
    run_finished = Signal()
    run_failed = Signal(str)
    run_progress = Signal(int, int)         # done, total

    request_preview_single = Signal(dict)   # scan_parameters
    request_global_stop = Signal()

    def __init__(self, positioner_manager, acquisition_manager, image_getter=None, parent=None):
        super().__init__(parent)

        self.positioner_manager = positioner_manager
        self.acquisition_manager = acquisition_manager
        self.image_getter = image_getter

        self._running = False
        self._stop_requested = False
        self._waiting_for_move = False
        self._waiting_for_acq = False
        self._returning_home = False

        self._cfg: MosaicRunConfig | None = None
        self._scan_params: dict | None = None

        self._mosaic = None
        self._tile_sequence: list[tuple[int, int]] = []
        self._tile_index = -1

        self._target_x_rel = None
        self._target_y_rel = None

        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(40)
        self._poll_timer.timeout.connect(self._check_motion_completion)

        self.acquisition_manager.acquisition_done.connect(self._on_acquisition_done)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def is_running(self) -> bool:
        return bool(self._running)

    @Slot(dict, dict)
    def start_run(self, mosaic_params: dict, scan_params: dict):
        if self._running:
            self.run_failed.emit("A stitching run is already active.")
            return

        try:
            cfg = self._build_config(mosaic_params, scan_params)
        except Exception as e:
            self.run_failed.emit(str(e))
            return

        self._cfg = cfg
        self._scan_params = dict(scan_params or {})
        self._stop_requested = False
        self._running = True
        self._waiting_for_move = False
        self._waiting_for_acq = False
        self._returning_home = False

        try:
            self._mosaic = np.zeros(
                (
                    cfg.tiles_y * cfg.tile_height_px - max(0, cfg.tiles_y - 1) * cfg.overlap_px,
                    cfg.tiles_x * cfg.tile_width_px - max(0, cfg.tiles_x - 1) * cfg.overlap_px,
                ),
                dtype=np.float32
            )
        except Exception as e:
            self._running = False
            self.run_failed.emit(f"Unable to allocate mosaic image: {e}")
            return

        self._mosaic_weight = np.zeros_like(self._mosaic, dtype=np.float32)

        self._tile_sequence = self._build_serpentine_sequence(cfg.tiles_x, cfg.tiles_y)
        self._tile_index = -1

        self.status_changed.emit(
            f"Start mosaic {cfg.tiles_x}x{cfg.tiles_y} on channel '{cfg.channel}'"
        )
        self.mosaic_updated.emit(self._mosaic.copy())
        self.run_started.emit()

        self._advance_to_next_tile()

    @Slot()
    def stop_run(self):
        if not self._running:
            return

        self._stop_requested = True
        self.status_changed.emit("Stopping mosaic...")
        self.request_global_stop.emit()

        try:
            self.positioner_manager.stop_all()
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Config
    # ------------------------------------------------------------------

    def _build_config(self, mosaic_params: dict, scan_params: dict) -> MosaicRunConfig:
        tiles_x = max(1, int(mosaic_params.get("tiles_x", 1)))
        tiles_y = max(1, int(mosaic_params.get("tiles_y", 1)))
        overlap_px = max(0, int(mosaic_params.get("overlap_px", 0)))
        channel = str(mosaic_params.get("channel", "")).strip()
        if not channel:
            raise ValueError("No channel selected for stitching.")

        rows = [row for row in scan_params.get("rows", []) if row.get("axis") != "None"]
        if len(rows) < 2:
            raise ValueError("Stitching requires at least 2 active scan axes.")

        row_x = None
        row_y = None
        for row in rows:
            axis = str(row.get("axis", ""))
            if axis.startswith("X") and row_x is None:
                row_x = row
            elif axis.startswith("Y") and row_y is None:
                row_y = row

        if row_x is None or row_y is None:
            raise ValueError("Stitching v1 requires XY scan axes.")

        tile_width_px = max(1, int(row_x.get("pixels", 1) or 1))
        tile_height_px = max(1, int(row_y.get("pixels", 1) or 1))
        tile_width_um = float(row_x.get("size_um", 1.0) or 1.0)
        tile_height_um = float(row_y.get("size_um", 1.0) or 1.0)

        if overlap_px >= tile_width_px or overlap_px >= tile_height_px:
            raise ValueError("Overlap must be smaller than tile width and height.")

        step_x_um = tile_width_um * (tile_width_px - overlap_px) / float(tile_width_px)
        step_y_um = tile_height_um * (tile_height_px - overlap_px) / float(tile_height_px)

        start_x_rel_um = float(self.positioner_manager.get_rel_pos("x"))
        start_y_rel_um = float(self.positioner_manager.get_rel_pos("y"))

        speed_x = float(self.positioner_manager.get_max_speed("x"))
        speed_y = float(self.positioner_manager.get_max_speed("y"))
        tol_x = float(self.positioner_manager.get_tolerance("x"))
        tol_y = float(self.positioner_manager.get_tolerance("y"))

        return MosaicRunConfig(
            tiles_x=tiles_x,
            tiles_y=tiles_y,
            overlap_px=overlap_px,
            channel=channel,
            tile_width_px=tile_width_px,
            tile_height_px=tile_height_px,
            tile_width_um=tile_width_um,
            tile_height_um=tile_height_um,
            step_x_um=step_x_um,
            step_y_um=step_y_um,
            start_x_rel_um=start_x_rel_um,
            start_y_rel_um=start_y_rel_um,
            stage_speed_x_mm_s=speed_x,
            stage_speed_y_mm_s=speed_y,
            tol_x_um=tol_x,
            tol_y_um=tol_y,
        )

    def _build_serpentine_sequence(self, tiles_x: int, tiles_y: int) -> list[tuple[int, int]]:
        seq = []
        for iy in range(tiles_y):
            if iy % 2 == 0:
                xs = range(tiles_x)
            else:
                xs = range(tiles_x - 1, -1, -1)
            for ix in xs:
                seq.append((ix, iy))
        return seq

    # ------------------------------------------------------------------
    # Run engine
    # ------------------------------------------------------------------

    def _advance_to_next_tile(self):
        if not self._running or self._cfg is None:
            return

        if self._stop_requested:
            self._begin_return_home()
            return

        self._tile_index += 1
        total = len(self._tile_sequence)

        if self._tile_index >= total:
            self.status_changed.emit("Mosaic complete. Returning to start...")
            self._begin_return_home()
            return

        ix, iy = self._tile_sequence[self._tile_index]
        self.run_progress.emit(self._tile_index, total)

        x_target = self._cfg.start_x_rel_um + ix * self._cfg.step_x_um
        y_target = self._cfg.start_y_rel_um + iy * self._cfg.step_y_um

        self.status_changed.emit(
            f"Tile {self._tile_index + 1}/{total} - move to X={x_target:.2f} µm, Y={y_target:.2f} µm"
        )
        self._move_to(x_target, y_target)

    def _move_to(self, x_rel_um: float, y_rel_um: float):
        self._target_x_rel = float(x_rel_um)
        self._target_y_rel = float(y_rel_um)
        self._waiting_for_move = True
        self._waiting_for_acq = False

        try:
            self.positioner_manager.move_to_rel("x", self._target_x_rel, self._cfg.stage_speed_x_mm_s)
            self.positioner_manager.move_to_rel("y", self._target_y_rel, self._cfg.stage_speed_y_mm_s)
        except Exception as e:
            self._fail_and_stop(f"Move error: {e}")
            return

        self._poll_timer.start()

    @Slot()
    def _check_motion_completion(self):
        if not self._running or not self._waiting_for_move or self._cfg is None:
            self._poll_timer.stop()
            return

        try:
            cur_x = float(self.positioner_manager.get_rel_pos("x"))
            cur_y = float(self.positioner_manager.get_rel_pos("y"))
        except Exception as e:
            self._poll_timer.stop()
            self._fail_and_stop(f"Unable to read stepper position: {e}")
            return

        done_x = abs(cur_x - self._target_x_rel) <= max(self._cfg.tol_x_um, 1e-6)
        done_y = abs(cur_y - self._target_y_rel) <= max(self._cfg.tol_y_um, 1e-6)

        if not (done_x and done_y):
            return

        self._poll_timer.stop()
        self._waiting_for_move = False

        if self._returning_home:
            self._finalize_run()
            return

        self._launch_single_preview()

    def _launch_single_preview(self):
        if not self._running:
            return

        params = dict(self._scan_params or {})
        params["repetitions"] = 1

        self._waiting_for_acq = True
        self.status_changed.emit(
            f"Tile {self._tile_index + 1}/{len(self._tile_sequence)} - acquiring..."
        )
        self.request_preview_single.emit(params)

    @Slot(object)
    def _on_acquisition_done(self, acquired):
        if not self._running or not self._waiting_for_acq or self._cfg is None:
            return

        self._waiting_for_acq = False

        if self._stop_requested:
            self._begin_return_home()
            return

        img = self._extract_channel_image(acquired, self._cfg.channel)
        if img is None:
            self._fail_and_stop(f"Channel '{self._cfg.channel}' not found in acquired data.")
            return

        try:
            self._paste_tile(img)
        except Exception as e:
            self._fail_and_stop(f"Unable to paste tile into mosaic: {e}")
            return

        self.mosaic_updated.emit(self._mosaic.copy())
        self.run_progress.emit(self._tile_index + 1, len(self._tile_sequence))
        self._advance_to_next_tile()

    def _extract_channel_image(self, acquired, channel: str):
        """
        Pour la v1, la source de vérité est l'image 2D effectivement affichée
        et stockée côté MainWindow dans last_images.
        """
        if callable(self.image_getter):
            try:
                img = self.image_getter(channel)
                if img is not None:
                    return np.asarray(img, dtype=np.float32)
            except Exception:
                pass

        # fallback très permissif si jamais le getter ne donne rien
        if isinstance(acquired, dict):
            if channel in acquired:
                try:
                    arr = np.asarray(acquired[channel], dtype=np.float32)
                    if arr.ndim == 2:
                        return arr
                except Exception:
                    pass

            for _, value in acquired.items():
                try:
                    arr = np.asarray(value, dtype=np.float32)
                    if arr.ndim == 2:
                        return arr
                except Exception:
                    continue

        return None
    
    def _paste_tile(self, img):
        if self._cfg is None or self._mosaic is None:
            return

        arr = np.asarray(img, dtype=np.float32)
        if arr.ndim != 2:
            raise ValueError(f"Expected 2D tile image, got shape={arr.shape}")

        if arr.shape != (self._cfg.tile_height_px, self._cfg.tile_width_px):
            raise ValueError(
                f"Unexpected tile shape {arr.shape}, expected "
                f"({self._cfg.tile_height_px}, {self._cfg.tile_width_px})"
            )

        ix, iy = self._tile_sequence[self._tile_index]
        x0 = ix * (self._cfg.tile_width_px - self._cfg.overlap_px)
        y0 = iy * (self._cfg.tile_height_px - self._cfg.overlap_px)
        x1 = x0 + self._cfg.tile_width_px
        y1 = y0 + self._cfg.tile_height_px

        # pas d'overlap -> collage direct
        if self._cfg.overlap_px <= 0:
            self._mosaic[y0:y1, x0:x1] = arr
            self._mosaic_weight[y0:y1, x0:x1] = 1.0
            return

        tile_weight = self._build_tile_weight(
            tile_h=self._cfg.tile_height_px,
            tile_w=self._cfg.tile_width_px,
            overlap=self._cfg.overlap_px,
            ix=ix,
            iy=iy,
            max_ix=self._cfg.tiles_x - 1,
            max_iy=self._cfg.tiles_y - 1,
        )

        mosaic_slice = self._mosaic[y0:y1, x0:x1]
        weight_slice = self._mosaic_weight[y0:y1, x0:x1]

        new_weight = weight_slice + tile_weight
        safe_weight = np.where(new_weight > 0, new_weight, 1.0)

        blended = (mosaic_slice * weight_slice + arr * tile_weight) / safe_weight

        self._mosaic[y0:y1, x0:x1] = blended
        self._mosaic_weight[y0:y1, x0:x1] = new_weight

    def _build_tile_weight(self, tile_h, tile_w, overlap, ix, iy, max_ix, max_iy):
        """
        Construit une carte de poids 2D pour faire un fondu dans les zones de recouvrement.
        Le poids reste à 1 au centre et décroît linéairement vers les bords qui recouvrent
        une tuile voisine.
        """
        wx = np.ones(tile_w, dtype=np.float32)
        wy = np.ones(tile_h, dtype=np.float32)

        ov = int(max(0, overlap))
        if ov <= 0:
            return np.outer(wy, wx)

        ramp_up = np.linspace(0.0, 1.0, ov, endpoint=True, dtype=np.float32)
        ramp_down = np.linspace(1.0, 0.0, ov, endpoint=True, dtype=np.float32)

        # En X
        if ix > 0:
            wx[:ov] = np.minimum(wx[:ov], ramp_up)
        if ix < max_ix:
            wx[-ov:] = np.minimum(wx[-ov:], ramp_down)

        # En Y
        if iy > 0:
            wy[:ov] = np.minimum(wy[:ov], ramp_up)
        if iy < max_iy:
            wy[-ov:] = np.minimum(wy[-ov:], ramp_down)

        w2d = np.outer(wy, wx)

        # éviter des poids strictement nuls partout sur un bord
        w2d = np.clip(w2d, 1e-6, None)
        return w2d
        
    def _begin_return_home(self):
        if not self._running or self._cfg is None:
            return

        self._returning_home = True
        self.status_changed.emit("Returning to initial XY stage position...")
        self._move_to(self._cfg.start_x_rel_um, self._cfg.start_y_rel_um)

    def _fail_and_stop(self, message: str):
        self._stop_requested = True
        self.status_changed.emit(message)

        try:
            self.request_global_stop.emit()
        except Exception:
            pass

        try:
            self.positioner_manager.stop_all()
        except Exception:
            pass

        self._running = False
        self._waiting_for_move = False
        self._waiting_for_acq = False
        self._poll_timer.stop()
        self.run_failed.emit(message)

    def _finalize_run(self):
        self._running = False
        self._waiting_for_move = False
        self._waiting_for_acq = False
        self._returning_home = False
        self._poll_timer.stop()

        total = len(self._tile_sequence)
        self.run_progress.emit(total, total)
        self.status_changed.emit("Mosaic finished.")
        self.run_finished.emit()