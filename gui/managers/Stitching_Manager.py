# DeepLight/gui/managers/Stitching_Manager.py

from __future__ import annotations
from dataclasses import dataclass
from PySide6.QtCore import QObject, Signal, Slot, QTimer
import numpy as np

# Latences fixes approximatives (calibrables) utilisées par l'ESTIMATION de
# durée. Elles couvrent ce qui n'est pas un temps de trajet pur :
#   _MOVE_OVERHEAD_S : décélération/stabilisation de la platine + détection
#                      d'arrivée (poll) + latence de commande, par mouvement.
#   _ACQ_OVERHEAD_S  : mise en place/arrêt du pipeline d'acquisition et recalage
#                      (cross-corrélation), par tuile acquise.
# Ajuste-les si l'estimation dérive du temps réel observé.
_MOVE_OVERHEAD_S = 0.12
_ACQ_OVERHEAD_S = 0.08


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

    # Ordre de balayage de l'axe stack (Z/P) :
    #   plane_outer=False -> "Z per tile" : pile complète par tuile (défaut)
    #   plane_outer=True  -> "Z per plane" : mosaïque XY complète par plan
    plane_outer: bool = False
    stack_axis: str | None = None            # axe positioner ("z"/"p") ou None
    stack_axis_display: str | None = None     # nom UI ("Z-Vcoil"/"Polarization")
    plane_positions_rel: tuple = ()           # positions relatives par plan
    stack_speed_mm_s: float = 1.0
    stack_tol_um: float = 0.1
    start_stack_rel_um: float = 0.0


class StitchingManager(QObject):
    """
    Non-blocking XY mosaic orchestrator.

    Philosophy:
    - never blocks the UI
    - builds on the existing managers
    - advances by events: move done -> acquisition -> stitch -> next move
    - ready for a future hardware backend as long as the stable API is kept
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
        # Séquence unifiée d'étapes : (plane_index_or_None, ix, iy).
        # plane_index None -> mode Z per tile (pile complète collée d'un coup).
        self._step_sequence: list[tuple] = []
        self._step_index = -1
        self._current_plane = None            # plan stack actuellement positionné
        self._pending_xy_after_stack = None   # (x, y) à atteindre après le move stack
        self._motion_phase = None             # "stack" | "xy" | "home"

        self._target_x_rel = None
        self._target_y_rel = None
        self._target_stack_rel = None

        self._poll_timer = QTimer(self)
        # 20 ms : on veut détecter l'arrivée au plus vite (chaque ms d'attente
        # est multipliée par le nombre de tuiles). Combiné à la relecture forcée
        # de la position (cf. _check_motion_completion), on ne dépend plus du
        # cache du positioner rafraîchi seulement toutes les poll_ms du manager.
        self._poll_timer.setInterval(20)
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

        if cfg.plane_outer:
            # Plans en externe, tuiles en interne : mosaïque XY complète par plan.
            self._step_sequence = [
                (p, ix, iy)
                for p in range(cfg.n_planes)
                for (ix, iy) in self._tile_sequence
            ]
        else:
            # Z per tile : pile complète collée par tuile (plane_index None).
            self._step_sequence = [(None, ix, iy) for (ix, iy) in self._tile_sequence]

        self._step_index = -1
        self._current_plane = None

        order_txt = f"{cfg.stack_axis_display} per plane" if cfg.plane_outer else "stack per tile"
        self.status_changed.emit(
            f"Start mosaic {cfg.tiles_x}x{cfg.tiles_y} on '{cfg.channel}' [{order_txt}]"
        )
        self.mosaic_updated.emit(self._mosaic_nominal_view().copy())
        self.run_started.emit()

        self._advance_to_next_step()

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

        # ------- Ordre de balayage de l'axe stack -------
        # scan_order encode l'axe stack ET l'ordre :
        #   "z_per_tile" / "p_per_tile"  -> pile complète par tuile (défaut)
        #   "z_per_plane" / "p_per_plane" -> mosaïque XY complète par plan, en
        #     déplaçant l'axe stack (Z ou P) une seule fois entre deux plans.
        # Le mode "per plane" nécessite que l'axe demandé (Z ou P) soit le SEUL
        # axe stack actif ; sinon on retombe sur "per tile".
        scan_order = str(mosaic_params.get("scan_order", "z_per_tile"))
        plane_outer = False
        stack_axis = None
        stack_axis_display = None
        plane_positions_rel: tuple = ()
        stack_speed = speed_x
        stack_tol = tol_x
        start_stack_rel = 0.0

        _AXIS_TO_POS = {"Z-Vcoil": "z", "Polarization": "p"}

        plane_outer_requested = scan_order.endswith("_per_plane")
        requested_axis = scan_order.split("_", 1)[0] if "_" in scan_order else None  # "z"/"p"

        if plane_outer_requested and n_planes > 1:
            # Sélectionne parmi les axes stack celui demandé par le scan_order.
            stack_row = next(
                (r for r in extra_rows
                 if _AXIS_TO_POS.get(str(r.get("axis", ""))) == requested_axis),
                None,
            )
            if stack_row is not None:
                stack_axis_display = str(stack_row.get("axis", ""))
                stack_axis = _AXIS_TO_POS.get(stack_axis_display)
                scan_modes = scan_params.get("scan_modes", {}) or {}
                mode = str(scan_modes.get(stack_axis_display, "around"))
                plane_positions_rel = tuple(
                    self._compute_stack_positions(stack_row, mode)
                )
                # len == n_planes garantit que l'axe demandé est le seul axe stack
                # (sinon n_planes est un produit et l'ordre par plan est ambigu).
                if len(plane_positions_rel) == n_planes:
                    plane_outer = True
                    try:
                        stack_speed = float(self.positioner_manager.get_max_speed(stack_axis))
                        stack_tol = float(self.positioner_manager.get_tolerance(stack_axis))
                        start_stack_rel = float(self.positioner_manager.get_rel_pos(stack_axis))
                    except Exception:
                        plane_outer = False

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
            plane_outer=plane_outer,
            stack_axis=stack_axis,
            stack_axis_display=stack_axis_display,
            plane_positions_rel=plane_positions_rel,
            stack_speed_mm_s=stack_speed,
            stack_tol_um=stack_tol,
            start_stack_rel_um=start_stack_rel,
        )

    def _compute_stack_positions(self, row: dict, mode: str) -> list[float]:
        """
        Relative positions of the stack-axis planes (same conventions as
        Scan_manager._compute_axis_positions: Z-Vcoil goes down; around/from).
        """
        n = max(1, int(row.get("pixels", 1) or 1))
        size = float(row.get("size_um", 0.0) or 0.0)
        offset = float(row.get("offset_um", 0.0) or 0.0)  # base relative + offset
        axis = str(row.get("axis", ""))

        if n == 1:
            return [offset]

        descending = (axis == "Z-Vcoil")
        if str(mode) == "from":
            start = offset
            stop = offset - size if descending else offset + size
        else:
            if descending:
                start, stop = offset + size / 2.0, offset - size / 2.0
            else:
                start, stop = offset - size / 2.0, offset + size / 2.0

        return [float(v) for v in np.linspace(start, stop, n)]

    @staticmethod
    def _strip_stack_axis(scan_params: dict, stack_display: str) -> dict:
        """
        Return a copy of scan_params without the stack axis (Z/P), for an XY-only
        acquisition: the axis is removed from axis_order/active_axes/rows and its
        pixel slot set to 1 -> the galvo plan then contains XY only.
        """
        p = dict(scan_params or {})

        axis_order = list(p.get("axis_order", []))
        pix = list(p.get("pixel_values", []))
        for i, ax in enumerate(axis_order):
            if ax == stack_display:
                axis_order[i] = "None"
                if i < len(pix):
                    pix[i] = 1
        p["axis_order"] = axis_order
        p["pixel_values"] = pix
        p["active_axes"] = [ax for ax in p.get("active_axes", []) if ax != stack_display]
        p["rows"] = [r for r in p.get("rows", []) if r.get("axis") != stack_display]
        return p

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
    # Estimation de durée
    # ------------------------------------------------------------------
    @staticmethod
    def estimate_duration_seconds(
        *,
        tiles_x: int,
        tiles_y: int,
        overlap_px: int,
        tile_w_um: float,
        tile_h_um: float,
        tile_w_px: int,
        tile_h_px: int,
        per_acq_full_s: float,
        speed_x_mm_s: float,
        speed_y_mm_s: float,
        n_planes: int = 1,
        plane_outer: bool = False,
        stack_range_um: float = 0.0,
        stack_speed_mm_s: float = 1.0,
    ) -> float:
        """
        Estimated total duration of a mosaic run.

        On top of the tile acquisition time, it accounts for:
        - the XY travel of the stage along the serpentine (distance / speed);
        - in 'per plane' mode: the stack-axis (Z/P) moves and the XY resets
          between planes;
        - a fixed latency per move and per acquisition (see the constants);
        - the return to the starting position.

        `per_acq_full_s` = duration of ONE complete acquisition (Z/P stack
        included), as displayed by the ScanWidget. The total acquisition time is
        n_tiles × per_acq_full_s in both sweep orders (in 'per plane' each
        acquisition covers a single plane, but there are n_planes× more of them).
        """
        tiles_x = max(1, int(tiles_x))
        tiles_y = max(1, int(tiles_y))
        n_planes = max(1, int(n_planes))
        overlap = max(0, int(overlap_px))
        tw = max(1, int(tile_w_px))
        th = max(1, int(tile_h_px))
        n_tiles = tiles_x * tiles_y

        step_x = float(tile_w_um) * (tw - overlap) / tw
        step_y = float(tile_h_um) * (th - overlap) / th
        vx = max(float(speed_x_mm_s), 1e-6) * 1000.0   # µm/s
        vy = max(float(speed_y_mm_s), 1e-6) * 1000.0

        def xy_move_time(i0, j0, i1, j1) -> float:
            dx = abs(i1 - i0) * step_x
            dy = abs(j1 - j0) * step_y
            if dx <= 0.0 and dy <= 0.0:
                return 0.0
            # move_xy_to_rel déplace X et Y simultanément -> max des deux.
            return max(dx / vx, dy / vy) + _MOVE_OVERHEAD_S

        # serpentin (mêmes indices que _build_serpentine_sequence)
        seq = []
        for iy in range(tiles_y):
            xs = range(tiles_x) if iy % 2 == 0 else range(tiles_x - 1, -1, -1)
            for ix in xs:
                seq.append((ix, iy))

        pass_time = sum(
            xy_move_time(seq[k][0], seq[k][1], seq[k + 1][0], seq[k + 1][1])
            for k in range(len(seq) - 1)
        )

        acq_time = n_tiles * max(0.0, float(per_acq_full_s))

        if plane_outer and n_planes > 1:
            n_acq = n_tiles * n_planes
            vs = max(float(stack_speed_mm_s), 1e-6) * 1000.0
            stack_step = abs(float(stack_range_um)) / max(1, n_planes - 1)
            # 1 move stack initial + (n_planes-1) inter-plans
            stack_time = n_planes * (stack_step / vs + _MOVE_OVERHEAD_S)
            reset_time = (n_planes - 1) * xy_move_time(
                seq[-1][0], seq[-1][1], seq[0][0], seq[0][1]
            )
            xy_time = n_planes * pass_time + reset_time + stack_time
            return_time = (
                abs(float(stack_range_um)) / vs + _MOVE_OVERHEAD_S
            ) + xy_move_time(seq[0][0], seq[0][1], 0, 0)
        else:
            n_acq = n_tiles
            xy_time = pass_time
            return_time = xy_move_time(seq[-1][0], seq[-1][1], 0, 0)

        overhead = n_acq * _ACQ_OVERHEAD_S
        return acq_time + xy_time + return_time + overhead

    # ------------------------------------------------------------------
    # Run engine
    # ------------------------------------------------------------------

    def _advance_to_next_step(self):
        if not self._running or self._cfg is None:
            return

        if self._stop_requested:
            self._begin_return_home()
            return

        self._step_index += 1
        total = len(self._step_sequence)

        if self._step_index >= total:
            self.status_changed.emit("Mosaic complete. Returning to start...")
            self._begin_return_home()
            return

        plane, ix, iy = self._step_sequence[self._step_index]
        self.run_progress.emit(self._step_index, total)

        x_target = self._cfg.start_x_rel_um + ix * self._cfg.step_x_um
        y_target = self._cfg.start_y_rel_um + iy * self._cfg.step_y_um

        # Mode Z per plane : quand on change de plan, on déplace d'abord l'axe
        # stack (une seule fois pour tout le balayage XY du plan), puis les XY.
        if plane is not None and plane != self._current_plane:
            self._current_plane = plane
            # Nouveau plan = mosaïque XY repartant de zéro : on réinitialise le
            # poids 2D partagé (sinon le fondu du plan suivant est corrompu par
            # le poids accumulé du plan précédent).
            self._mosaic_weight.fill(0.0)
            stack_target = float(self._cfg.plane_positions_rel[plane])
            self.status_changed.emit(
                f"Plane {plane + 1}/{self._cfg.n_planes} - move {self._cfg.stack_axis_display} "
                f"to {stack_target:.2f} µm"
            )
            self._pending_xy_after_stack = (x_target, y_target)
            self._move_stack_to(stack_target)
            return

        self.status_changed.emit(
            f"Step {self._step_index + 1}/{total} - move to X={x_target:.2f} µm, Y={y_target:.2f} µm"
        )
        self._move_to(x_target, y_target)

    def _move_to(self, x_rel_um: float, y_rel_um: float):
        self._target_x_rel = float(x_rel_um)
        self._target_y_rel = float(y_rel_um)
        self._waiting_for_move = True
        self._waiting_for_acq = False
        self._motion_phase = "xy"

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
                # This positioner manager has no combined XY move: drive the two
                # axes one after the other.
                self.positioner_manager.move_to_rel("x", self._target_x_rel, self._cfg.stage_speed_x_mm_s)
                self.positioner_manager.move_to_rel("y", self._target_y_rel, self._cfg.stage_speed_y_mm_s)
        except Exception as e:
            self._fail_and_stop(f"Move error: {e}")
            return

        self._poll_timer.start()

    def _move_stack_to(self, stack_rel_um: float):
        """Move the stack axis (Z/P) to a relative position (Z per plane mode)."""
        self._target_stack_rel = float(stack_rel_um)
        self._waiting_for_move = True
        self._waiting_for_acq = False
        self._motion_phase = "stack"

        try:
            self.positioner_manager.move_to_rel(
                self._cfg.stack_axis, self._target_stack_rel, self._cfg.stack_speed_mm_s
            )
        except Exception as e:
            self._fail_and_stop(f"Stack axis move error: {e}")
            return

        self._poll_timer.start()

    @Slot()
    def _check_motion_completion(self):
        if not self._running or not self._waiting_for_move or self._cfg is None:
            self._poll_timer.stop()
            return

        # --- Phase déplacement de l'axe stack (Z per plane) ---
        if self._motion_phase == "stack":
            try:
                cur = float(self.positioner_manager.get_rel_pos(self._cfg.stack_axis))
            except Exception as e:
                self._poll_timer.stop()
                self._fail_and_stop(f"Unable to read stack axis position: {e}")
                return

            if abs(cur - self._target_stack_rel) > max(self._cfg.stack_tol_um, 1e-6):
                return

            self._poll_timer.stop()
            self._waiting_for_move = False
            self._motion_phase = None

            xy = self._pending_xy_after_stack
            self._pending_xy_after_stack = None

            # Retour à la base : après le retour de l'axe stack, on ramène XY
            # puis on finalise (ne PAS re-déclencher _begin_return_home -> boucle).
            if self._returning_home:
                if xy is not None:
                    self._move_to(xy[0], xy[1])
                else:
                    self._finalize_run()
                return

            if self._stop_requested:
                self._begin_return_home()
                return

            if xy is not None:
                self._move_to(xy[0], xy[1])
            return

        # --- Phase déplacement XY ---
        # Relecture forcée de la position réelle (une transaction série) : sinon
        # get_rel_pos renvoie le cache du positioner, rafraîchi seulement toutes
        # les poll_ms du manager (~80 ms) -> arrivée détectée avec ce retard sur
        # CHAQUE tuile. No-op si le manager ne fournit pas cette méthode (mock).
        refresh_xy = getattr(self.positioner_manager, "refresh_xy_position", None)
        if callable(refresh_xy):
            refresh_xy()

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
        self._motion_phase = None

        if self._returning_home:
            self._finalize_run()
            return

        self._launch_single_preview()

    def _launch_single_preview(self):
        if not self._running:
            return

        params = dict(self._scan_params or {})
        params["repetitions"] = 1

        # Z per plane : l'axe stack est déjà positionné par le manager, on
        # acquiert donc une image XY SEULE (l'axe stack est retiré du scan pour
        # que le plan ne le redéplace pas).
        if self._cfg.plane_outer and self._cfg.stack_axis_display:
            params = self._strip_stack_axis(params, self._cfg.stack_axis_display)

        self._waiting_for_acq = True
        self.status_changed.emit(
            f"Step {self._step_index + 1}/{len(self._step_sequence)} - acquiring..."
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

        # Z per tile -> plane None -> pile complète collée à partir du plan 0.
        # Z per plane -> plane = index du plan courant -> collage sur ce plan.
        plane, _ix, _iy = self._step_sequence[self._step_index]
        plane_offset = 0 if plane is None else int(plane)

        try:
            self._paste_tile(stack, plane_offset=plane_offset)
        except Exception as e:
            self._fail_and_stop(f"Unable to paste tile into mosaic: {e}")
            return

        self.mosaic_updated.emit(self._mosaic_nominal_view().copy())
        self.run_progress.emit(self._step_index + 1, len(self._step_sequence))
        self._advance_to_next_step()

    def _extract_channel_image(self, acquired, channel: str):
        """
        For v1 the source of truth is the 2D image actually displayed, stored on
        the MainWindow side in last_images.
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
        Build the 3D stack (P, H, W) of one tile for a given channel, from the
        microscope's `acquired` structure: rep -> idx_tuple -> ch -> 2D.
        The planes are ordered by stack-axis index (idx_tuple sort).

        Same orientation as the displayed image (acquired holds a copy of
        shared_images). Returns None when extraction is not possible.
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
        View of the mosaic at its nominal size (without the internal registration
        margin), as displayed by the UI. 2D for a single plane, 3D (P, H, W)
        otherwise.
        """
        m = int(getattr(self, "_reg_margin_px", 0) or 0)
        view = self._mosaic if m <= 0 else self._mosaic[:, m:-m, m:-m]
        if view.shape[0] == 1:
            return view[0]
        return view

    def _paste_tile(self, stack, plane_offset: int = 0):
        if self._cfg is None or self._mosaic is None:
            return

        arr = np.asarray(stack, dtype=np.float32)
        if arr.ndim == 2:
            arr = arr[None, ...]
        if arr.ndim != 3:
            raise ValueError(f"Expected tile stack (P,H,W), got shape={arr.shape}")

        p_mosaic = self._mosaic.shape[0]
        # Nombre de plans collés à partir de plane_offset. En Z per tile,
        # plane_offset=0 et arr contient toute la pile ; en Z per plane,
        # plane_offset=plan courant et arr contient 1 plan.
        p = min(arr.shape[0], p_mosaic - int(plane_offset))
        arr = arr[:p]
        z0 = int(plane_offset)
        z1 = z0 + p

        if arr.shape[1:] != (self._cfg.tile_height_px, self._cfg.tile_width_px):
            raise ValueError(
                f"Unexpected tile shape {arr.shape[1:]}, expected "
                f"({self._cfg.tile_height_px}, {self._cfg.tile_width_px})"
            )

        margin = int(getattr(self, "_reg_margin_px", 0) or 0)
        _plane, ix, iy = self._step_sequence[self._step_index]
        x0 = margin + ix * (self._cfg.tile_width_px - self._cfg.overlap_px)
        y0 = margin + iy * (self._cfg.tile_height_px - self._cfg.overlap_px)

        # Recalage par cross-corrélation, estimé sur le plan de référence
        # (plane_offset) et appliqué à tous les plans collés (même décalage).
        if self._cfg.overlap_px > 0 and (ix > 0 or iy > 0):
            dy, dx = self._estimate_tile_registration(arr[0], x0, y0, ix, iy, ref_plane=z0)
            x0 = max(0, min(x0 + dx, self._mosaic.shape[2] - self._cfg.tile_width_px))
            y0 = max(0, min(y0 + dy, self._mosaic.shape[1] - self._cfg.tile_height_px))

        x1 = x0 + self._cfg.tile_width_px
        y1 = y0 + self._cfg.tile_height_px

        # pas d'overlap -> collage direct
        if self._cfg.overlap_px <= 0:
            self._mosaic[z0:z1, y0:y1, x0:x1] = arr
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

        mosaic_slice = self._mosaic[z0:z1, y0:y1, x0:x1]          # (p, h, w)
        # Fondu par plan avec un poids 2D partagé (broadcast sur les plans).
        blended = (
            mosaic_slice * weight_slice[None, :, :]
            + arr * tile_weight[None, :, :]
        ) / safe_weight[None, :, :]

        self._mosaic[z0:z1, y0:y1, x0:x1] = blended
        self._mosaic_weight[y0:y1, x0:x1] = new_weight

    def _correlate_covered_strip(self, y0, y1, x0, x1, mov, max_shift, ref_plane=0):
        """
        Correlate the mosaic strip [y0:y1, x0:x1] with `mov`, restricted to the
        sub-area actually covered (non-zero weight). Empty bands (a neighbouring
        tile shifted, or not yet placed) corrupt the correlation if they are left
        in the strip.

        Returns (dy, dx), or None when the covered area is too small.
        """
        ref = self._mosaic[int(ref_plane), y0:y1, x0:x1]
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

    def _estimate_tile_registration(self, arr, x0_nom, y0_nom, ix, iy, ref_plane=0):
        """
        Estimate the correction (dy, dx) to apply to the nominal position, by
        cross-correlation over the overlap area with the neighbouring tiles.
        Registration is done on the mosaic's `ref_plane` plane.
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
                    y0_nom, y_end, x_ov_start, x_ov_end, mov, max_shift,
                    ref_plane=ref_plane,
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
                    y0_nom, y_end, x_ov_start, x_ov_end, mov, max_shift,
                    ref_plane=ref_plane,
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
                    y_ov_start, y_ov_end, x0_nom, x_end, mov, max_shift,
                    ref_plane=ref_plane,
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
        Normalised cross-correlation between two strips of equal size.
        Returns integer (dy, dx) bounded to ±max_shift.

        The peak search is restricted to the ±max_shift window BEFORE the argmax:
        previously a distant peak (periodic texture, noise) was clipped to
        ±max_shift, which produced systematic offsets that made the tiles drift.
        A peak that is too weak (unreliable correlation) is rejected as well.
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
        Build a 2D weight map to blend across the overlap regions.
        The weight stays at 1 in the centre and decreases linearly towards the
        edges that overlap a neighbouring tile.
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

        # Z per plane : ramener d'abord l'axe stack à sa position initiale, puis
        # les XY (le retour XY déclenche _finalize_run).
        if self._cfg.plane_outer and self._cfg.stack_axis is not None:
            self.status_changed.emit("Returning stack axis and XY to start...")
            self._pending_xy_after_stack = (
                self._cfg.start_x_rel_um, self._cfg.start_y_rel_um
            )
            self._move_stack_to(self._cfg.start_stack_rel_um)
            return

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

        total = len(self._step_sequence)
        self.run_progress.emit(total, total)
        self.status_changed.emit("Done")
        self.run_finished.emit()