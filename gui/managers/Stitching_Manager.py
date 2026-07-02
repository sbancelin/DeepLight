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
    n_planes: int
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
        self._reg_margin_px = 0
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

        # Marge interne pour absorber les corrections de recalage des tuiles
        # de bord (sinon les corrections y sont écrêtées par le canvas).
        # La mosaïque émise vers l'UI reste à la taille nominale.
        self._reg_margin_px = max(2, cfg.overlap_px // 3) if cfg.overlap_px > 0 else 0

        mosaic_h = (
            cfg.tiles_y * cfg.tile_height_px - max(0, cfg.tiles_y - 1) * cfg.overlap_px
            + 2 * self._reg_margin_px
        )
        mosaic_w = (
            cfg.tiles_x * cfg.tile_width_px - max(0, cfg.tiles_x - 1) * cfg.overlap_px
            + 2 * self._reg_margin_px
        )

        try:
            # Mosaïque 3D : un plan par index d'axe stack (Z/P). n_planes == 1
            # -> mosaïque 2D classique. Le poids reste 2D (géométrie identique
            # pour tous les plans).
            self._mosaic = np.zeros((cfg.n_planes, mosaic_h, mosaic_w), dtype=np.float32)
        except Exception as e:
            self._running = False
            self.run_failed.emit(f"Unable to allocate mosaic image: {e}")
            return

        self._mosaic_weight = np.zeros((mosaic_h, mosaic_w), dtype=np.float32)

        self._tile_sequence = self._build_serpentine_sequence(cfg.tiles_x, cfg.tiles_y)
        self._tile_index = -1

        self.status_changed.emit(
            f"Start mosaic {cfg.tiles_x}x{cfg.tiles_y} on channel '{cfg.channel}'"
        )
        self.mosaic_updated.emit(self._mosaic_nominal_view().copy())
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
            raise ValueError("Stitching requires XY scan axes.")

        tile_width_px = max(1, int(row_x.get("pixels", 1) or 1))
        tile_height_px = max(1, int(row_y.get("pixels", 1) or 1))
        tile_width_um = float(row_x.get("size_um", 1.0) or 1.0)
        tile_height_um = float(row_y.get("size_um", 1.0) or 1.0)

        # Axes "stack" supplémentaires (Z-Vcoil, Polarization...) : chaque tuile
        # devient une pile de plans. n_planes = produit de leurs # pixels.
        extra_rows = [r for r in rows if r is not row_x and r is not row_y]
        n_planes = 1
        for r in extra_rows:
            n_planes *= max(1, int(r.get("pixels", 1) or 1))

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
            n_planes=n_planes,
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
            move_xy = getattr(self.positioner_manager, "move_xy_to_rel", None)

            if callable(move_xy):
                move_xy(
                    self._target_x_rel,
                    self._target_y_rel,
                    self._cfg.stage_speed_x_mm_s,
                    self._cfg.stage_speed_y_mm_s,
                )
            else:
                # fallback legacy
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

        stack = self._extract_channel_stack(acquired, self._cfg.channel)
        if stack is None:
            img = self._extract_channel_image(acquired, self._cfg.channel)
            if img is None:
                self._fail_and_stop(f"Channel '{self._cfg.channel}' not found in acquired data.")
                return
            stack = np.asarray(img, dtype=np.float32)[None, ...]

        try:
            self._paste_tile(stack)
        except Exception as e:
            self._fail_and_stop(f"Unable to paste tile into mosaic: {e}")
            return

        self.mosaic_updated.emit(self._mosaic_nominal_view().copy())
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
    
    def _extract_channel_stack(self, acquired, channel: str):
        """
        Construit la pile 3D (P, H, W) d'une tuile pour un canal donné, à partir
        de la structure `acquired` du microscope : rep -> idx_tuple -> ch -> 2D.
        Les plans sont ordonnés par index d'axe stack (tri des idx_tuple).

        Même orientation que l'image affichée (acquired stocke une copie de
        shared_images). Retourne None si extraction impossible.
        """
        if not isinstance(acquired, dict) or not acquired:
            return None

        rep_keys = sorted(acquired.keys())
        frames_by_idx = acquired.get(rep_keys[0])
        if not isinstance(frames_by_idx, dict) or not frames_by_idx:
            return None

        planes = []
        for idx in sorted(frames_by_idx.keys()):
            ch_dict = frames_by_idx.get(idx, {})
            img = ch_dict.get(channel)
            if img is None:
                # fallback permissif : première image 2D disponible
                for value in ch_dict.values():
                    a = np.asarray(value, dtype=np.float32)
                    if a.ndim == 2:
                        img = a
                        break
            if img is None:
                return None
            a = np.asarray(img, dtype=np.float32)
            if a.ndim != 2:
                return None
            planes.append(a)

        if not planes:
            return None

        return np.stack(planes, axis=0)

    def _mosaic_nominal_view(self):
        """
        Vue de la mosaïque à la taille nominale (sans la marge interne
        de recalage), telle qu'affichée par l'UI. 2D si un seul plan,
        3D (P, H, W) sinon.
        """
        m = int(getattr(self, "_reg_margin_px", 0) or 0)
        view = self._mosaic if m <= 0 else self._mosaic[:, m:-m, m:-m]
        if view.shape[0] == 1:
            return view[0]
        return view

    def _paste_tile(self, stack):
        if self._cfg is None or self._mosaic is None:
            return

        arr = np.asarray(stack, dtype=np.float32)
        if arr.ndim == 2:
            arr = arr[None, ...]
        if arr.ndim != 3:
            raise ValueError(f"Expected tile stack (P,H,W), got shape={arr.shape}")

        p_mosaic = self._mosaic.shape[0]
        # Ajuste le nombre de plans si l'acquisition en renvoie plus/moins que
        # prévu (robustesse) : on colle les plans communs.
        p = min(arr.shape[0], p_mosaic)
        arr = arr[:p]

        if arr.shape[1:] != (self._cfg.tile_height_px, self._cfg.tile_width_px):
            raise ValueError(
                f"Unexpected tile shape {arr.shape[1:]}, expected "
                f"({self._cfg.tile_height_px}, {self._cfg.tile_width_px})"
            )

        margin = int(getattr(self, "_reg_margin_px", 0) or 0)
        ix, iy = self._tile_sequence[self._tile_index]
        x0 = margin + ix * (self._cfg.tile_width_px - self._cfg.overlap_px)
        y0 = margin + iy * (self._cfg.tile_height_px - self._cfg.overlap_px)

        # Corriger la position par cross-corrélation sur la zone de recouvrement.
        # Le recalage est estimé sur le plan 0 puis appliqué à tous les plans
        # (mêmes tuiles => même décalage), ce qui garde la pile alignée.
        if self._cfg.overlap_px > 0 and (ix > 0 or iy > 0):
            dy, dx = self._estimate_tile_registration(arr[0], x0, y0, ix, iy)
            x0 = max(0, min(x0 + dx, self._mosaic.shape[2] - self._cfg.tile_width_px))
            y0 = max(0, min(y0 + dy, self._mosaic.shape[1] - self._cfg.tile_height_px))

        x1 = x0 + self._cfg.tile_width_px
        y1 = y0 + self._cfg.tile_height_px

        # pas d'overlap -> collage direct (tous les plans)
        if self._cfg.overlap_px <= 0:
            self._mosaic[:p, y0:y1, x0:x1] = arr
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

        weight_slice = self._mosaic_weight[y0:y1, x0:x1]           # (h, w)
        new_weight = weight_slice + tile_weight
        safe_weight = np.where(new_weight > 0, new_weight, 1.0)

        mosaic_slice = self._mosaic[:p, y0:y1, x0:x1]              # (p, h, w)
        # Fondu par plan avec un poids 2D partagé (broadcast sur les plans).
        blended = (
            mosaic_slice * weight_slice[None, :, :]
            + arr * tile_weight[None, :, :]
        ) / safe_weight[None, :, :]

        self._mosaic[:p, y0:y1, x0:x1] = blended
        self._mosaic_weight[y0:y1, x0:x1] = new_weight

    def _correlate_covered_strip(self, y0, y1, x0, x1, mov, max_shift):
        """
        Corrèle la strip mosaïque [y0:y1, x0:x1] avec `mov` en se limitant à
        la sous-zone réellement couverte (poids non nul). Les bandes vides
        (tuile voisine décalée ou pas encore posée) corrompent la corrélation
        si on les laisse dans la strip.

        Retourne (dy, dx) ou None si la zone couverte est insuffisante.
        """
        ref = self._mosaic[0, y0:y1, x0:x1]
        w = self._mosaic_weight[y0:y1, x0:x1]
        if ref.size == 0 or ref.shape != mov.shape:
            return None

        cov = w > 1e-3

        # rogner d'abord les lignes mal couvertes (pied du fondu, zone vide),
        # puis évaluer les colonnes dans les lignes restantes
        rows = np.where(cov.mean(axis=1) >= 0.9)[0]
        if rows.size < 8:
            return None
        r0, r1 = int(rows[0]), int(rows[-1]) + 1

        cols = np.where(cov[r0:r1].mean(axis=0) >= 0.9)[0]
        if cols.size < 8:
            return None
        c0, c1 = int(cols[0]), int(cols[-1]) + 1

        if cov[r0:r1, c0:c1].mean() < 0.95:
            return None

        return self._cross_correlate(ref[r0:r1, c0:c1], mov[r0:r1, c0:c1], max_shift)

    def _estimate_tile_registration(self, arr, x0_nom, y0_nom, ix, iy):
        """
        Estime la correction (dy, dx) à appliquer à la position nominale
        par cross-corrélation dans la zone de recouvrement avec les tuiles voisines.
        """
        ov = self._cfg.overlap_px
        max_shift = max(2, ov // 3)
        th = self._cfg.tile_height_px
        tw = self._cfg.tile_width_px
        mh, mw = self._mosaic.shape[-2], self._mosaic.shape[-1]

        shifts_y, shifts_x = [], []

        # Voisin gauche (lignes paires du serpentin : acquisition de gauche à droite)
        if ix > 0 and x0_nom > 0:
            x_ov_start = x0_nom
            x_ov_end = min(x_ov_start + ov, mw)
            y_end = min(y0_nom + th, mh)
            if x_ov_end > x_ov_start and y_end > y0_nom:
                mov = arr[:y_end - y0_nom, :x_ov_end - x_ov_start]
                shift = self._correlate_covered_strip(
                    y0_nom, y_end, x_ov_start, x_ov_end, mov, max_shift
                )
                if shift is not None:
                    shifts_y.append(shift[0])
                    shifts_x.append(shift[1])

        # Voisin droit (lignes impaires du serpentin : acquisition de droite à
        # gauche, la tuile déjà posée est à droite)
        if ix < self._cfg.tiles_x - 1:
            x_ov_start = x0_nom + tw - ov
            x_ov_end = min(x0_nom + tw, mw)
            y_end = min(y0_nom + th, mh)
            if 0 <= x_ov_start < x_ov_end and y_end > y0_nom:
                mov = arr[:y_end - y0_nom, (x_ov_start - x0_nom):(x_ov_end - x0_nom)]
                shift = self._correlate_covered_strip(
                    y0_nom, y_end, x_ov_start, x_ov_end, mov, max_shift
                )
                if shift is not None:
                    shifts_y.append(shift[0])
                    shifts_x.append(shift[1])

        # Voisin du dessus (vertical)
        if iy > 0 and y0_nom > 0:
            y_ov_start = y0_nom
            y_ov_end = min(y_ov_start + ov, mh)
            x_end = min(x0_nom + tw, mw)
            if y_ov_end > y_ov_start and x_end > x0_nom:
                mov = arr[:y_ov_end - y_ov_start, :x_end - x0_nom]
                shift = self._correlate_covered_strip(
                    y_ov_start, y_ov_end, x0_nom, x_end, mov, max_shift
                )
                if shift is not None:
                    shifts_y.append(shift[0])
                    shifts_x.append(shift[1])

        dy = int(round(float(np.mean(shifts_y)))) if shifts_y else 0
        dx = int(round(float(np.mean(shifts_x)))) if shifts_x else 0
        return dy, dx

    @staticmethod
    def _cross_correlate(ref, mov, max_shift):
        """
        Cross-corrélation normalisée entre deux strips de même taille.
        Retourne (dy, dx) entiers bornés à ±max_shift.

        La recherche du pic est restreinte à la fenêtre ±max_shift AVANT
        l'argmax : auparavant un pic lointain (texture périodique, bruit)
        était écrêté à ±max_shift, ce qui produisait des décalages
        systématiques faisant dériver les tuiles. Un pic trop faible
        (corrélation non fiable) est également rejeté.
        """
        r_std = float(ref.std())
        m_std = float(mov.std())
        if r_std < 1e-6 or m_std < 1e-6:
            return 0, 0

        ref_n = (ref.astype(np.float32) - ref.mean()) / r_std
        mov_n = (mov.astype(np.float32) - mov.mean()) / m_std

        R = np.fft.rfft2(ref_n) * np.conj(np.fft.rfft2(mov_n))
        cc = np.fft.irfft2(R, s=ref_n.shape)

        H, W = cc.shape
        ms_y = int(min(max_shift, H // 2))
        ms_x = int(min(max_shift, W // 2))

        # fenêtre de décalages plausibles (coins de la carte, wrap-around FFT)
        mask = np.zeros((H, W), dtype=bool)
        mask[:ms_y + 1, :ms_x + 1] = True
        if ms_x > 0:
            mask[:ms_y + 1, W - ms_x:] = True
        if ms_y > 0:
            mask[H - ms_y:, :ms_x + 1] = True
        if ms_y > 0 and ms_x > 0:
            mask[H - ms_y:, W - ms_x:] = True

        cc_search = np.where(mask, cc, -np.inf)
        y_peak, x_peak = np.unravel_index(int(np.argmax(cc_search)), cc.shape)

        # corrélation normalisée du pic (≈ coefficient de Pearson) :
        # en dessous de ce seuil l'estimation n'est pas fiable, on garde
        # la position nominale.
        peak_corr = float(cc[y_peak, x_peak]) / float(ref_n.size)
        if peak_corr < 0.25:
            return 0, 0

        dy = y_peak if y_peak <= H // 2 else y_peak - H
        dx = x_peak if x_peak <= W // 2 else x_peak - W

        dy = max(-max_shift, min(max_shift, dy))
        dx = max(-max_shift, min(max_shift, dx))
        return int(dy), int(dx)

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
        self.status_changed.emit("Done")
        self.run_finished.emit()