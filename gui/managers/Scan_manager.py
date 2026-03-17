# DeepLight/gui/managers/Scan_Manager.py

from __future__ import annotations
import numpy as np
from PySide6.QtCore import QObject, Signal, Slot

from .Scan_Types import ScanParams, ExecutionPlan, FrameSlice, StepEvent, FrameReconstructionPlan


class ScanManager(QObject):
    """
    Génère les signaux analogiques X/Y pour un raster scan (pixel-clocked),
    et les expose via un signal pour visualisation et futur envoi NI-DAQ.
    """
    # ---- Visualizer-friendly stream ----
    # axis_name, t_ms(np.ndarray), pos_um(np.ndarray)
    stepper_waveform_chunk = Signal(str, object, object)

    # streaming oscillo analogique (t_ms, x_v, y_v)
    analog_waveforms_chunk = Signal(object, object, object)

    # waveform complet (t_ms, x_v, y_v)
    analog_waveforms_ready = Signal(object, object, object)

    # request visualizers/hardware to return all axes to 0
    outputs_return_to_zero = Signal()
    xy_frame_duration_ready = Signal(float)

    def __init__(self, parent=None):
        super().__init__(parent)

        # Valeurs par défaut
        self.voltage_min = -5.0
        self.voltage_max = +5.0
        self.default_conv_um_per_v = 20.0  # V = µm / (µm/V)

        self._last_scan_params = {}
        self._last_scan_params_obj = None
        self._last_execution_plan = None
        self._plan_driven_stream = False

        # ---- analog frame courant ----
        self._t_ms = None
        self._x_v = None
        self._y_v = None
        self._i = 0

        # ---- infos frame XY ----
        self._samples_per_xy_frame = None
        self._xy_frame_dt_ms = None
        self._xy_frame_duration_ms = None

        # ---- timeline planifiée ----
        self._sched_ms = 0.0

        # ---- mode de run ----
        self._mode = "preview_single"       # preview_single | preview_continuous | acquisition
        self._loop_xy_forever = False
        self._enable_steppers = False

        self._total_xy_frames = 1
        self._completed_xy_frames = 0

        # ---- état de streaming GUI ----
        self._pending_t = []
        self._pending_x = []
        self._pending_y = []

    def build_frame_reconstruction_plan(self, plan: ExecutionPlan) -> FrameReconstructionPlan:
        md = dict(plan.metadata or {})

        dim_fast = int(md.get("pix_fast", md.get("pix_x", 1)) or 1)
        dim_slow = int(md.get("pix_slow", md.get("pix_y", 1)) or 1)

        dim_x = int(md.get("pix_image_x", md.get("pix_x", 1)) or 1)
        dim_y = int(md.get("pix_image_y", md.get("pix_y", 1)) or 1)

        fast_axis = md.get("fast_axis")
        image_x_axis = md.get("image_x_axis")
        fast_axis_is_image_x = (fast_axis == image_x_axis)

        return FrameReconstructionPlan(
            dim_x=dim_x,
            dim_y=dim_y,
            dim_fast=dim_fast,
            dim_slow=dim_slow,
            samples_per_pixel=max(1, int(plan.samples_per_pixel)),
            bidirectional=bool(md.get("bidirectional_scan", False)),
            bidirectional_shift_px=int(md.get("bidirectional_shift_px", 0) or 0),
            turnback_offset_px=max(0, int(md.get("turnback_offset_px", 0) or 0)),
            fast_axis_is_image_x=bool(fast_axis_is_image_x),
        )
    
    def get_last_execution_plan(self):
        return self._last_execution_plan

    def get_last_scan_params_obj(self):
        return self._last_scan_params_obj

    def get_last_scan_params_dict(self):
        return dict(self._last_scan_params or {})
    
    def to_scan_params(self, scan_params_dict: dict, mode: str = "acquisition") -> ScanParams:
        """
        Convertit le dict historique issu du ScanWidget/UI en dataclass ScanParams.
        Cette méthode n'altère pas encore le reste du pipeline : elle prépare la transition.
        """
        d = dict(scan_params_dict or {})

        axis_order = d.get("axis_order", ["X-Galvo", "Y-Galvo", "None", "None"])
        if not isinstance(axis_order, list) or len(axis_order) != 4:
            axis_order = ["X-Galvo", "Y-Galvo", "None", "None"]

        active_axes = d.get("active_axes")
        if not isinstance(active_axes, list) or len(active_axes) == 0:
            active_axes = [ax for ax in axis_order if ax != "None"]

        pixel_values = d.get("pixel_values", [256, 256, 1, 1])
        if not isinstance(pixel_values, list) or len(pixel_values) != 4:
            pixel_values = [256, 256, 1, 1]

        turnback = d.get("turnback_offset", {})
        turnback_offset_px = 0
        if isinstance(turnback, dict) and len(active_axes) > 0:
            ax0 = active_axes[0]
            turnback_offset_px = int(turnback.get(ax0, 0) or 0)

        spp = int(d.get("samples_per_pixel", 1) or 1)
        if spp < 1:
            spp = 1

        bidirectional_shift_px = int(d.get("bidirectional_shift_px", 0) or 0)

        # --- nouveaux paramètres pour la migration sample scan ---
        scan_kind = str(d.get("scan_kind", "laser") or "laser")
        pixel_source_kind = str(d.get("pixel_source_kind", "analog_integrating") or "analog_integrating")
        sample_settle_time_s = float(d.get("sample_settle_time_s", 0.0) or 0.0)

        return ScanParams(
            mode=str(mode),
            axis_order=list(axis_order),
            active_axes=list(active_axes),
            pixel_values=[int(v) for v in pixel_values],
            sizes=dict(d.get("sizes", {})),
            step_sizes=dict(d.get("step_sizes", {})),
            offsets=dict(d.get("offsets", {})),
            initial_relative_positions=dict(d.get("initial_relative_positions", {})),
            dwell_time_s=float(d.get("dwell_time", 10e-6) or 10e-6),
            bidirectional_scan=bool(d.get("bidirectional_scan", False)),
            bidirectional_shift_px=bidirectional_shift_px,
            turnback_offset_px=turnback_offset_px,
            conversion_factors=dict(d.get("conversion_factors", {})),
            min_voltages=dict(d.get("min_voltages", {})),
            max_voltages=dict(d.get("max_voltages", {})),
            velocity_max=dict(d.get("velocity_max", {})),
            acceleration_max=dict(d.get("acceleration_max", {})),
            jerk=dict(d.get("jerk", {})),
            repetitions=max(1, int(d.get("repetitions", 1) or 1)),
            delay_between_rep_s=max(0.0, float(d.get("delay_between_rep", 0.0) or 0.0)),
            active_channels=list(d.get("active_channels", [])),
            samples_per_pixel=spp,
            scan_kind=scan_kind,
            pixel_source_kind=pixel_source_kind,
            sample_settle_time_s=sample_settle_time_s,
        )
    
    def _get_xy_axes_from_scan_params_obj(self, sp: ScanParams) -> tuple[str, str]:
        """
        Retourne les deux axes analogiques XY à utiliser pour le raster.
        """
        active = [ax for ax in sp.axis_order if ax != "None"]
        if len(active) < 2:
            raise ValueError("At least two active axes are required for XY scan")
        return active[0], active[1]

    def _make_hold_segment(self, n_samples: int, x_value: float = 0.0, y_value: float = 0.0):
        """
        Crée un segment de maintien analogique.
        """
        n_samples = max(int(n_samples), 0)
        if n_samples == 0:
            return (
                np.zeros((0,), dtype=np.float64),
                np.zeros((0,), dtype=np.float64),
            )

        return (
            np.full((n_samples,), float(x_value), dtype=np.float64),
            np.full((n_samples,), float(y_value), dtype=np.float64),
        )

    def _compute_axis_positions(self, n: int, size: float, offset: float):
        """
        Génère les positions relatives pour un axe discret (Z, P...).
        """
        n = max(int(n), 1)

        if n == 1:
            return [offset]

        start = offset - size / 2.0
        stop = offset + size / 2.0

        return list(np.linspace(start, stop, n))
    
    def build_execution_plan(self, sp: ScanParams) -> ExecutionPlan:
        """
        Construit un plan d'exécution maître à partir de ScanParams.

        Étape 4:
        - gère XY
        - gère repetitions
        - gère delay_between_rep
        - pas encore de stepper
        """
        fast_axis, slow_axis = self._get_xy_axes_from_scan_params_obj(sp)
        image_x_axis, image_y_axis = self._infer_image_axes(fast_axis, slow_axis)

        axis_row_map = self._get_axis_row_map(sp)

        row_fast = axis_row_map[fast_axis]
        row_slow = axis_row_map[slow_axis]
        row_img_x = axis_row_map[image_x_axis]
        row_img_y = axis_row_map[image_y_axis]

        pix_fast = max(int(sp.pixel_values[row_fast]), 1)
        pix_slow = max(int(sp.pixel_values[row_slow]), 1)
        pix_image_x = max(int(sp.pixel_values[row_img_x]), 1)
        pix_image_y = max(int(sp.pixel_values[row_img_y]), 1)

        samples_per_pixel = max(int(sp.samples_per_pixel), 1)
        dwell_time_s = float(sp.dwell_time_s)
        pixel_rate_hz = 1.0 / dwell_time_s
        sample_rate_hz = pixel_rate_hz * samples_per_pixel

        size_x_um = float(sp.sizes.get(fast_axis, 100.0))
        size_y_um = float(sp.sizes.get(slow_axis, 100.0))
        off_x_um = float(sp.offsets.get(fast_axis, 0.0))
        off_y_um = float(sp.offsets.get(slow_axis, 0.0))

        conv_x = float(sp.conversion_factors.get(fast_axis, self.default_conv_um_per_v))
        conv_y = float(sp.conversion_factors.get(slow_axis, self.default_conv_um_per_v))

        x_vmin = float(sp.min_voltages.get(fast_axis, -5.0))
        x_vmax = float(sp.max_voltages.get(fast_axis, 5.0))
        y_vmin = float(sp.min_voltages.get(slow_axis, -5.0))
        y_vmax = float(sp.max_voltages.get(slow_axis, 5.0))

        tb = int(sp.turnback_offset_px)
        reps = max(int(sp.repetitions), 1)

        axis3_name = None
        axis3_positions = [None]

        if len(sp.axis_order) >= 3 and sp.axis_order[2] != "None":
            axis3_name = sp.axis_order[2]

        n3 = max(int(sp.pixel_values[2]), 1)
        size3 = float(sp.sizes.get(axis3_name, 0.0))
        off3 = float(sp.offsets.get(axis3_name, 0.0))   # déjà absolu

        axis3_positions = self._compute_axis_positions(n3, size3, off3)

        axis4_name = None
        axis4_positions = [None]

        if len(sp.axis_order) >= 4 and sp.axis_order[3] != "None":
            axis4_name = sp.axis_order[3]

            n4 = max(int(sp.pixel_values[3]), 1)
            size4 = float(sp.sizes.get(axis4_name, 0.0))
            user_off4 = float(sp.offsets.get(axis4_name, 0.0))
            base4 = float(sp.initial_relative_positions.get(axis4_name, 0.0))
            off4 = base4 + user_off4

            axis4_positions = self._compute_axis_positions(n4, size4, off4)

        if axis4_name is not None and reps > 1:
            raise NotImplementedError(
                "axis4 + repetitions > 1 not supported yet (save pipeline is 5D XYZchT)"
            )

        # raster XY de base (une frame)
        t_ms, fast_frame, slow_frame = self.generate_raster(
            pix_x=pix_fast,
            pix_y=pix_slow,
            dwell_time_s=dwell_time_s,
            size_x_um=size_x_um,
            size_y_um=size_y_um,
            offset_x_um=off_x_um,
            offset_y_um=off_y_um,
            conv_x_um_per_v=conv_x,
            conv_y_um_per_v=conv_y,
            x_vmin=x_vmin,
            x_vmax=x_vmax,
            y_vmin=y_vmin,
            y_vmax=y_vmax,
            bidirectional=bool(sp.bidirectional_scan),
            turnback_offset_px=tb,
            samples_per_pixel=samples_per_pixel,
        )

        x_frame, y_frame = self._map_fast_slow_to_galvos(
            fast_axis=fast_axis,
            slow_axis=slow_axis,
            fast_wave=fast_frame,
            slow_wave=slow_frame,
        )

        frame_len = int(x_frame.size)
        delay_samples = int(round(float(sp.delay_between_rep_s) * sample_rate_hz))

        ao_x_parts = []
        ao_y_parts = []
        frame_slices = []
        step_events = []

        cursor = 0

        # Step initial : positionner axis3/axis4 avant la 1ère frame
        if axis3_name is not None and axis3_positions and axis3_positions[0] is not None:
            step_events.append(
                StepEvent(
                    sample_index=0,
                    axis_name=axis3_name,
                    target_rel=float(axis3_positions[0]),
                    velocity_um_s=sp.velocity_max.get(axis3_name, 0.0),
                    acceleration_um_s2=sp.acceleration_max.get(axis3_name, 0.0),
                    jerk_um_s3=sp.jerk.get(axis3_name, 0.0),
                    reason="axis3_init",
                )
            )

        if axis4_name is not None and axis4_positions and axis4_positions[0] is not None:
            step_events.append(
                StepEvent(
                    sample_index=0,
                    axis_name=axis4_name,
                    target_rel=float(axis4_positions[0]),
                    velocity_um_s=sp.velocity_max.get(axis4_name, 0.0),
                    acceleration_um_s2=sp.acceleration_max.get(axis4_name, 0.0),
                    jerk_um_s3=sp.jerk.get(axis4_name, 0.0),
                    reason="axis4_init",
                )
            )

        for rep_i in range(reps):
            # Repositionnement explicite au début de chaque répétition
            if rep_i > 0:
                if axis3_name is not None and axis3_positions and axis3_positions[0] is not None:
                    step_events.append(
                        StepEvent(
                            sample_index=cursor,
                            axis_name=axis3_name,
                            target_rel=float(axis3_positions[0]),
                            velocity_um_s=sp.velocity_max.get(axis3_name, 0.0),
                            acceleration_um_s2=sp.acceleration_max.get(axis3_name, 0.0),
                            jerk_um_s3=sp.jerk.get(axis3_name, 0.0),
                            reason="axis3_init_rep",
                        )
                    )

                if axis4_name is not None and axis4_positions and axis4_positions[0] is not None:
                    step_events.append(
                        StepEvent(
                            sample_index=cursor,
                            axis_name=axis4_name,
                            target_rel=float(axis4_positions[0]),
                            velocity_um_s=sp.velocity_max.get(axis4_name, 0.0),
                            acceleration_um_s2=sp.acceleration_max.get(axis4_name, 0.0),
                            jerk_um_s3=sp.jerk.get(axis4_name, 0.0),
                            reason="axis4_init_rep",
                        )
                    )
            for i4, pos4 in enumerate(axis4_positions):

                for i3, pos3 in enumerate(axis3_positions):

                    ao_x_parts.append(x_frame)
                    ao_y_parts.append(y_frame)

                    frame_slices.append(
                        FrameSlice(
                            sample_start=cursor,
                            sample_stop=cursor + frame_len,
                            rep_index=rep_i,
                            axis3_index=i3,
                            axis4_index=i4,
                            axis3_value=pos3,
                            axis4_value=pos4,
                        )
                    )

                    cursor += frame_len

                                # mouvement axis3 après la frame :
                    # - soit step vers la position suivante
                    # - soit reset au début si axis4 change
                    if axis3_name is not None:
                        if i3 < len(axis3_positions) - 1:
                            next_axis3_target = axis3_positions[i3 + 1]
                            next_axis3_reason = "axis3_step"
                        elif axis4_name is not None and i4 < len(axis4_positions) - 1 and len(axis3_positions) > 1:
                            next_axis3_target = axis3_positions[0]
                            next_axis3_reason = "axis3_reset_for_axis4"
                        else:
                            next_axis3_target = None
                            next_axis3_reason = None

                        if next_axis3_target is not None:
                            step_events.append(
                                StepEvent(
                                    sample_index=cursor,
                                    axis_name=axis3_name,
                                    target_rel=next_axis3_target,
                                    velocity_um_s=sp.velocity_max.get(axis3_name, 0.0),
                                    acceleration_um_s2=sp.acceleration_max.get(axis3_name, 0.0),
                                    jerk_um_s3=sp.jerk.get(axis3_name, 0.0),
                                    reason=next_axis3_reason,
                                )
                            )

                    # mouvement axis4 quand le cycle axis3 est fini
                    if axis4_name is not None and i3 == len(axis3_positions) - 1:
                        if i4 < len(axis4_positions) - 1:
                            step_events.append(
                                StepEvent(
                                    sample_index=cursor,
                                    axis_name=axis4_name,
                                    target_rel=axis4_positions[i4 + 1],
                                    velocity_um_s=sp.velocity_max.get(axis4_name, 0.0),
                                    acceleration_um_s2=sp.acceleration_max.get(axis4_name, 0.0),
                                    jerk_um_s3=sp.jerk.get(axis4_name, 0.0),
                                    reason="axis4_step",
                                )
                            )
                        
            # pause entre répétitions
            if rep_i < reps - 1 and delay_samples > 0:

                x_hold, y_hold = self._make_hold_segment(delay_samples, 0.0, 0.0)

                ao_x_parts.append(x_hold)
                ao_y_parts.append(y_hold)

                cursor += delay_samples

        # Step final : retour à la base initiale à la fin du run
        if axis3_name is not None:
            base3 = float(sp.initial_relative_positions.get(axis3_name, 0.0))
            step_events.append(
                StepEvent(
                    sample_index=cursor,
                    axis_name=axis3_name,
                    target_rel=base3,
                    velocity_um_s=sp.velocity_max.get(axis3_name, 0.0),
                    acceleration_um_s2=sp.acceleration_max.get(axis3_name, 0.0),
                    jerk_um_s3=sp.jerk.get(axis3_name, 0.0),
                    reason="axis3_return_to_base",
                )
            )

        if axis4_name is not None:
            base4 = float(sp.initial_relative_positions.get(axis4_name, 0.0))
            step_events.append(
                StepEvent(
                    sample_index=cursor,
                    axis_name=axis4_name,
                    target_rel=base4,
                    velocity_um_s=sp.velocity_max.get(axis4_name, 0.0),
                    acceleration_um_s2=sp.acceleration_max.get(axis4_name, 0.0),
                    jerk_um_s3=sp.jerk.get(axis4_name, 0.0),
                    reason="axis4_return_to_base",
                )
            )
        
        ao_x = np.concatenate(ao_x_parts).astype(np.float64, copy=False)
        ao_y = np.concatenate(ao_y_parts).astype(np.float64, copy=False)
        total_samples = int(ao_x.size)

        metadata = {
            "fast_axis": fast_axis,
            "slow_axis": slow_axis,
            "image_x_axis": image_x_axis,
            "image_y_axis": image_y_axis,
            # compatibilité temporaire
            "axis_x": fast_axis,
            "axis_y": slow_axis,
            # dimensions raster (matériel)
            "pix_fast": pix_fast,
            "pix_slow": pix_slow,
            # dimensions image (logiques)
            "pix_image_x": pix_image_x,
            "pix_image_y": pix_image_y,
            "pix_x": pix_fast,
            "pix_y": pix_slow,
            "turnback_offset_px": tb,
            "bidirectional_scan": bool(sp.bidirectional_scan),
            "bidirectional_shift_px": int(sp.bidirectional_shift_px),
            "active_channels": list(sp.active_channels),
            "repetitions": reps,
            "delay_between_rep_s": float(sp.delay_between_rep_s),
            "frame_samples": frame_len,
            "frame_useful_samples": int(pix_fast * pix_slow * samples_per_pixel),
            "delay_samples": delay_samples,
            "axis3_name": axis3_name,
            "axis3_positions": axis3_positions,
            "axis4_name": axis4_name,
            "axis4_positions": axis4_positions,
        }

        return ExecutionPlan(
            mode=sp.mode,
            dwell_time_s=dwell_time_s,
            pixel_rate_hz=pixel_rate_hz,
            samples_per_pixel=samples_per_pixel,
            sample_rate_hz=sample_rate_hz,
            total_samples=total_samples,
            ao_x=ao_x,
            ao_y=ao_y,
            step_events=step_events,
            frame_slices=frame_slices,
            metadata=metadata,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _infer_image_axes(self, fast_axis: str, slow_axis: str) -> tuple[str, str]:
        """
        Déduit quels axes correspondent aux axes image X et Y.

        Cas visé ici :
        - X-Galvo / Y-Galvo
        - Y-Galvo / X-Galvo
        - X-Stage / Y-Stage
        - Y-Stage / X-Stage

        L'idée:
        - l'axe dont le nom commence par 'X-' devient l'axe image X
        - l'axe dont le nom commence par 'Y-' devient l'axe image Y
        """
        axes = [fast_axis, slow_axis]

        image_x_axis = next((ax for ax in axes if ax.startswith("X-")), fast_axis)
        image_y_axis = next((ax for ax in axes if ax.startswith("Y-")), slow_axis)

        return image_x_axis, image_y_axis
    
    def _get_axis_row_map(self, sp: ScanParams) -> dict[str, int]:
        """
        Retourne la correspondance :
            nom d'axe -> index de ligne dans l'UI / axis_order / pixel_values

        Exemple:
            axis_order = ["Y-Galvo", "X-Galvo", "Z-Stage", "None"]
            -> {"Y-Galvo": 0, "X-Galvo": 1, "Z-Stage": 2}
        """
        return {
            axis_name: i
            for i, axis_name in enumerate(sp.axis_order)
            if axis_name != "None"
        }
    
    def _map_fast_slow_to_galvos(
        self,
        fast_axis: str,
        slow_axis: str,
        fast_wave: np.ndarray,
        slow_wave: np.ndarray,
    ) -> tuple[np.ndarray, np.ndarray]:
        """
        Convertit des waveforms exprimées dans le repère fast/slow
        vers le repère physique Galvo X / Galvo Y.

        Règle:
        - x_v = tension réellement envoyée au X-Galvo
        - y_v = tension réellement envoyée au Y-Galvo

        fast/slow ne sert qu'à décrire l'ordre de balayage.
        """
        fast_wave = np.asarray(fast_wave, dtype=np.float64)
        slow_wave = np.asarray(slow_wave, dtype=np.float64)

        if fast_wave.shape != slow_wave.shape:
            raise ValueError("fast_wave and slow_wave must have the same shape")

        galvo_x = np.zeros_like(fast_wave, dtype=np.float64)
        galvo_y = np.zeros_like(fast_wave, dtype=np.float64)

        if fast_axis == "X-Galvo":
            galvo_x = fast_wave
        elif slow_axis == "X-Galvo":
            galvo_x = slow_wave

        if fast_axis == "Y-Galvo":
            galvo_y = fast_wave
        elif slow_axis == "Y-Galvo":
            galvo_y = slow_wave

        return galvo_x, galvo_y

    def _clamp(self, arr: np.ndarray, vmin: float, vmax: float) -> np.ndarray:
        return np.clip(arr, float(vmin), float(vmax))

    def _um_to_v(self, pos_um: np.ndarray, conv_um_per_v: float) -> np.ndarray:
        conv = float(conv_um_per_v) if float(conv_um_per_v) > 0 else float(self.default_conv_um_per_v)
        return pos_um / conv

    def _make_ramp(self, start_v: float, stop_v: float, n: int) -> np.ndarray:
        if n <= 0:
            return np.zeros((0,), dtype=np.float64)
        return np.linspace(start_v, stop_v, n, endpoint=False, dtype=np.float64)

    def _get_active_rows_from_params(self, scan_params: dict) -> tuple[list[str], list[int]]:
        axis_order = scan_params.get("axis_order", ["X-Galvo", "Y-Galvo", "None", "None"])
        if not isinstance(axis_order, list) or len(axis_order) != 4:
            axis_order = ["X-Galvo", "Y-Galvo", "None", "None"]

        active_rows = [i for i, name in enumerate(axis_order) if name != "None"]
        return axis_order, active_rows

    def _append_hold_segment(self, duration_ms: float, x_hold: float = 0.0, y_hold: float = 0.0):
        """
        Ajoute un segment "pause" à 0 V (ou autre valeur fixée) dans le buffer analogique.
        Sert à rendre visibles les pauses de step / delay inter-répétition.
        """
        duration_ms = float(duration_ms)
        if duration_ms <= 0:
            return

        t0 = float(self._sched_ms)
        t1 = t0 + duration_ms

        self._pending_t.append(np.array([t0, t1], dtype=np.float64))
        self._pending_x.append(np.array([float(x_hold), float(x_hold)], dtype=np.float64))
        self._pending_y.append(np.array([float(y_hold), float(y_hold)], dtype=np.float64))

        self._sched_ms = t1

    def _infer_mode_from_legacy_call(self, scan_params: dict, repeat: bool) -> str:
        """
        Compatibilité avec ton main_window actuel qui appelle encore prepare_run(..., repeat=...).
        """
        explicit = scan_params.get("_run_mode", None)
        if isinstance(explicit, str) and explicit.strip():
            return explicit.strip()

        if bool(repeat):
            return "preview_continuous"

        # Heuristique raisonnable si l'ancien main_window est encore utilisé.
        repetitions = max(int(scan_params.get("repetitions", 1) or 1), 1)
        delay_s = float(scan_params.get("delay_between_rep", 0.0) or 0.0)

        axis_order, active_rows = self._get_active_rows_from_params(scan_params)

        # Si plus de 2 axes actifs, ou reps > 1, ou delay > 0 -> acquisition
        if len(active_rows) >= 3 or repetitions > 1 or delay_s > 0:
            return "acquisition"

        # Sinon, sans info supplémentaire, on considère que repeat=False = single preview
        return "preview_single"

    def _stairs_from_per_frame(
        self,
        frame_starts_ms: np.ndarray,
        frame_duration_ms: float,
        per_frame_pos: np.ndarray
    ):
        """
        Convertit une position par frame (N,) en deux arrays (x_plot, y_plot) de taille (2N,)
        pour tracer des steps sans 'stepMode' côté visualizer.
        """
        n = int(per_frame_pos.size)
        if n <= 0:
            return np.zeros((0,), dtype=np.float64), np.zeros((0,), dtype=np.float64)

        starts = frame_starts_ms.astype(np.float64, copy=False)
        ends = (starts + float(frame_duration_ms)).astype(np.float64, copy=False)

        x_plot = np.empty((2 * n,), dtype=np.float64)
        y_plot = np.empty((2 * n,), dtype=np.float64)

        x_plot[0::2] = starts
        x_plot[1::2] = ends
        y_plot[0::2] = per_frame_pos
        y_plot[1::2] = per_frame_pos
        return x_plot, y_plot

    def generate_raster(
        self,
        pix_x: int,
        pix_y: int,
        dwell_time_s: float,
        size_x_um: float = 100.0,
        size_y_um: float = 100.0,
        offset_x_um: float = 0.0,
        offset_y_um: float = 0.0,
        conv_x_um_per_v: float | None = None,
        conv_y_um_per_v: float | None = None,
        x_vmin: float = -5.0,
        x_vmax: float = +5.0,
        y_vmin: float = -5.0,
        y_vmax: float = +5.0,
        bidirectional: bool = False,
        turnback_offset_px: int = 0,
        samples_per_pixel: int = 1,
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """
        Retourne (t_ms, x_v, y_v)
        """
        pix_x = max(int(pix_x), 1)
        pix_y = max(int(pix_y), 1)
        dwell_time_s = float(dwell_time_s)
        if dwell_time_s <= 0:
            dwell_time_s = 1e-6

        spp = max(int(samples_per_pixel), 1)
        dt_sample_s = dwell_time_s / float(spp)

        conv_x = self.default_conv_um_per_v if conv_x_um_per_v is None else float(conv_x_um_per_v)
        conv_y = self.default_conv_um_per_v if conv_y_um_per_v is None else float(conv_y_um_per_v)

        x_start_um = offset_x_um - size_x_um / 2.0
        x_stop_um = offset_x_um + size_x_um / 2.0
        y_start_um = offset_y_um - size_y_um / 2.0
        y_stop_um = offset_y_um + size_y_um / 2.0

        x_pixels_um = np.linspace(x_start_um, x_stop_um, pix_x, endpoint=False, dtype=np.float64)
        y_pixels_um = np.linspace(y_start_um, y_stop_um, pix_y, endpoint=False, dtype=np.float64)

        x_pixels_v = self._clamp(self._um_to_v(x_pixels_um, conv_x), x_vmin, x_vmax)
        y_pixels_v = self._clamp(self._um_to_v(y_pixels_um, conv_y), y_vmin, y_vmax)

        tb_px = max(int(turnback_offset_px), 0)
        tb_samples = tb_px * spp

        x_lines = []
        y_lines = []

        for iy in range(pix_y):
            y_line = np.full((pix_x * spp,), float(y_pixels_v[iy]), dtype=np.float64)

            if bidirectional and (iy % 2 == 1):
                x_line_pixels = x_pixels_v[::-1]
                fly_start = float(x_pixels_v[0]) if pix_x > 0 else 0.0
                fly_stop = float(x_pixels_v[-1]) if pix_x > 0 else 0.0
            else:
                x_line_pixels = x_pixels_v
                fly_start = float(x_pixels_v[-1]) if pix_x > 0 else 0.0
                fly_stop = float(x_pixels_v[0]) if pix_x > 0 else 0.0

            x_line = np.repeat(x_line_pixels, spp)

            if tb_samples > 0:
                x_fly = np.linspace(fly_start, fly_stop, tb_samples, endpoint=False, dtype=np.float64)
                y_fly = np.full((tb_samples,), float(y_pixels_v[iy]), dtype=np.float64)

                x_line = np.concatenate([x_line, x_fly], axis=0)
                y_line = np.concatenate([y_line, y_fly], axis=0)

            x_lines.append(x_line)
            y_lines.append(y_line)

        x_v = np.concatenate(x_lines, axis=0).astype(np.float64, copy=False)
        y_v = np.concatenate(y_lines, axis=0).astype(np.float64, copy=False)

        n = x_v.size
        t_ms = (np.arange(n, dtype=np.float64) * dt_sample_s) * 1e3

        return t_ms, x_v, y_v

    def prepare_run(self, scan_params: dict, repeat: bool = True, mode: str | None = None):
        """
        Prépare le plan analog/stepper.

        Compatibilité:
        - ancien code: prepare_run(scan_params, repeat=True/False)
        - nouveau code: prepare_run(scan_params, mode="preview_single"/"preview_continuous"/"acquisition")
        """
        self._last_scan_params = dict(scan_params)

        if mode is None:
            self._mode = self._infer_mode_from_legacy_call(scan_params, repeat)
        else:
            self._mode = str(mode)

        self._plan_driven_stream = False
        # Transition douce vers ScanParams
        self._last_scan_params_obj = self.to_scan_params(scan_params, mode=self._mode)
        
        try:
            self._last_execution_plan = self.build_execution_plan(self._last_scan_params_obj)
        except Exception as e:
            self._last_execution_plan = None
            print("[ScanManager] build_execution_plan failed:", e)

        # En acquisition, les AO viennent directement du plan maître
        if self._mode == "acquisition" and self._last_execution_plan is not None:
            plan = self._last_execution_plan

            self._plan_driven_stream = True

            self._t_ms = (
                np.arange(int(plan.total_samples), dtype=np.float64) / float(plan.sample_rate_hz)
            ) * 1e3
            self._x_v = np.asarray(plan.ao_x, dtype=np.float64)
            self._y_v = np.asarray(plan.ao_y, dtype=np.float64)

            self._i = 0
            self._pending_t.clear()
            self._pending_x.clear()
            self._pending_y.clear()

            self._samples_per_xy_frame = int(plan.metadata.get("frame_samples", len(self._t_ms)))
            self._xy_frame_dt_ms = (1.0 / float(plan.sample_rate_hz)) * 1e3
            self._xy_frame_duration_ms = self._samples_per_xy_frame * self._xy_frame_dt_ms

            # Inform stepper visualizer of the XY frame duration
            try:
                self.xy_frame_duration_ready.emit(float(self._xy_frame_duration_ms))
            except Exception:
                pass

            self._completed_xy_frames = 0
            self._total_xy_frames = len(plan.frame_slices)

            self._loop_xy_forever = False
            self._enable_steppers = False

            # Les steppers sont désormais pilotés par AcquisitionManager pendant l'exécution du plan
            self._ax3 = None
            self._ax4 = None
            self._pos3 = None
            self._pos4 = None

            return

        self._loop_xy_forever = (self._mode == "preview_continuous")
        self._enable_steppers = (self._mode == "acquisition")

        pix_vals = scan_params.get("pixel_values", [256, 256, 1, 1])

        axis_order, active_rows = self._get_active_rows_from_params(scan_params)
        if len(active_rows) < 2:
            return

        row_fast = active_rows[0]
        row_slow = active_rows[1]

        pix_fast = int(pix_vals[row_fast]) if len(pix_vals) == 4 else 256
        pix_slow = int(pix_vals[row_slow]) if len(pix_vals) == 4 else 256
        dwell_time_s = float(scan_params.get("dwell_time", 10e-6))
        samples_per_pixel = max(int(scan_params.get("samples_per_pixel", 1) or 1), 1)

        sizes = scan_params.get("sizes", {})
        offsets = scan_params.get("offsets", {})
        convs = scan_params.get("conversion_factors", {})
        turnback = scan_params.get("turnback_offset", {})
        vmins = scan_params.get("min_voltages", {})
        vmaxs = scan_params.get("max_voltages", {})

        fast_axis = axis_order[row_fast]
        slow_axis = axis_order[row_slow]

        size_fast_um = float(sizes.get(fast_axis, 100.0))
        size_slow_um = float(sizes.get(slow_axis, 100.0))
        off_fast_um = float(offsets.get(fast_axis, 0.0))
        off_slow_um = float(offsets.get(slow_axis, 0.0))
        conv_fast = float(convs.get(fast_axis, self.default_conv_um_per_v))
        conv_slow = float(convs.get(slow_axis, self.default_conv_um_per_v))
        tb = int(turnback.get(fast_axis, 0))

        fast_vmin = float(vmins.get(fast_axis, -5.0))
        fast_vmax = float(vmaxs.get(fast_axis, 5.0))
        slow_vmin = float(vmins.get(slow_axis, -5.0))
        slow_vmax = float(vmaxs.get(slow_axis, 5.0))

        t_ms, fast_v, slow_v = self.generate_raster(
            pix_x=pix_fast,
            pix_y=pix_slow,
            dwell_time_s=dwell_time_s,
            size_x_um=size_fast_um,
            size_y_um=size_slow_um,
            offset_x_um=off_fast_um,
            offset_y_um=off_slow_um,
            conv_x_um_per_v=conv_fast,
            conv_y_um_per_v=conv_slow,
            x_vmin=fast_vmin,
            x_vmax=fast_vmax,
            y_vmin=slow_vmin,
            y_vmax=slow_vmax,
            bidirectional=bool(scan_params.get("bidirectional_scan", False)),
            turnback_offset_px=tb,
            samples_per_pixel=samples_per_pixel,
        )

        x_v, y_v = self._map_fast_slow_to_galvos(
            fast_axis=fast_axis,
            slow_axis=slow_axis,
            fast_wave=fast_v,
            slow_wave=slow_v,
        )

        self._t_ms = t_ms
        self._x_v = x_v
        self._y_v = y_v

        dt_ms = float(t_ms[1] - t_ms[0]) if t_ms.size > 1 else ((float(dwell_time_s) / float(samples_per_pixel)) * 1e3)

        samples_per_line = int((pix_fast + max(tb, 0)) * samples_per_pixel)
        self._samples_per_xy_frame = max(1, samples_per_line * int(pix_slow))
        self._xy_frame_dt_ms = dt_ms
        self._xy_frame_duration_ms = self._samples_per_xy_frame * dt_ms
        try:
            self.xy_frame_duration_ready.emit(float(self._xy_frame_duration_ms))
        except Exception:
            pass

        self._i = 0
        self._pending_t.clear()
        self._pending_x.clear()
        self._pending_y.clear()

        self._completed_xy_frames = 0
        self._sched_ms = 0.0

        self._enable_steppers = False
        self._total_xy_frames = 1

    def consume_samples(self, n_samples: int):
        """
        Avance le plan analogique d'un nombre réel de samples acquis.
        En acquisition planifiée, on lit simplement les AO du plan maître.
        """

        n_samples = int(n_samples)
        if n_samples <= 0:
            return

        if self._t_ms is None or self._x_v is None or self._y_v is None:
            return
        # stream linéaire depuis ExecutionPlan
        if self._plan_driven_stream:
            i0 = int(self._i)
            i1 = min(int(self._i + n_samples), int(self._t_ms.size))

            if i1 <= i0:
                return

            t_chunk = self._t_ms[i0:i1]
            x_chunk = self._x_v[i0:i1]
            y_chunk = self._y_v[i0:i1]

            if t_chunk.size:
                self._pending_t.append(t_chunk)
                self._pending_x.append(x_chunk)
                self._pending_y.append(y_chunk)

            self._i = i1

            if self._xy_frame_dt_ms is not None:
                self._sched_ms = float(self._i) * float(self._xy_frame_dt_ms)
            elif self._t_ms.size > 1:
                dt_ms = float(self._t_ms[1] - self._t_ms[0])
                self._sched_ms = float(self._i) * dt_ms
            elif self._t_ms.size == 1:
                self._sched_ms = float(self._t_ms[0])

            return

        frame_n = int(self._t_ms.size)
        if frame_n <= 0:
            return

        remaining = n_samples

        while remaining > 0:
            i0 = self._i
            take = min(remaining, frame_n - i0)
            i1 = i0 + take

            t_chunk = self._t_ms[i0:i1] + float(self._sched_ms)
            x_chunk = self._x_v[i0:i1]
            y_chunk = self._y_v[i0:i1]

            if t_chunk.size:
                self._pending_t.append(t_chunk)
                self._pending_x.append(x_chunk)
                self._pending_y.append(y_chunk)

            self._i = i1
            remaining -= take

            # fin d'une image XY
            if self._i >= frame_n:
                if self._xy_frame_duration_ms is not None:
                    self._sched_ms += float(self._xy_frame_duration_ms)

                self._completed_xy_frames += 1

                if self._loop_xy_forever:
                    self._i = 0
                else:
                    self._i = frame_n
                    break

    def flush_pending_buffers(self):
        """
        Emet vers les visualizers les points accumulés depuis le dernier refresh GUI.
        """
        if self._pending_t:
            t_ms = np.concatenate(self._pending_t)
            x_v = np.concatenate(self._pending_x)
            y_v = np.concatenate(self._pending_y)

            self._pending_t.clear()
            self._pending_x.clear()
            self._pending_y.clear()

            self.analog_waveforms_chunk.emit(t_ms, x_v, y_v)

    def return_all_axes_to_zero(self):
        """
        Signal de retour explicite des sorties à 0.
        Les steppers sont désormais gérés hors du ScanManager.
        """
        self.outputs_return_to_zero.emit()

    def stop_stream(self):
        self._t_ms = None
        self._x_v = None
        self._y_v = None
        self._i = 0

        self._pending_t.clear()
        self._pending_x.clear()
        self._pending_y.clear()

        self._samples_per_xy_frame = None
        self._xy_frame_duration_ms = None
        self._xy_frame_dt_ms = None

        self._mode = "preview_single"
        self._plan_driven_stream = False
        self._loop_xy_forever = False
        self._enable_steppers = False

        self._sched_ms = 0.0
        self._total_xy_frames = 1
        self._completed_xy_frames = 0

    @Slot(dict)
    def generate_from_scan_parameters(self, scan_params: dict):
        """
        Génération "statique" des waveforms analogiques complets à partir des paramètres de scan.
        """
        pix_vals = scan_params.get("pixel_values", [256, 256])
        active_axes = scan_params.get("active_axes", ["X-Galvo", "Y-Galvo"])

        pix_x = int(pix_vals[0]) if len(pix_vals) > 0 else 256
        pix_y = int(pix_vals[1]) if len(pix_vals) > 1 else 256

        dwell_time_s = float(scan_params.get("dwell_time", 10e-6))
        samples_per_pixel = max(int(scan_params.get("samples_per_pixel", 1) or 1), 1)

        sizes = scan_params.get("sizes", {})
        offsets = scan_params.get("offsets", {})
        convs = scan_params.get("conversion_factors", {})
        turnback = scan_params.get("turnback_offset", {})

        fast_axis = active_axes[0] if len(active_axes) > 0 else "X-Galvo"
        slow_axis = active_axes[1] if len(active_axes) > 1 else "Y-Galvo"

        size_fast_um = float(sizes.get(fast_axis, 100.0))
        size_slow_um = float(sizes.get(slow_axis, 100.0))
        off_fast_um = float(offsets.get(fast_axis, 0.0))
        off_slow_um = float(offsets.get(slow_axis, 0.0))
        conv_fast = float(convs.get(fast_axis, self.default_conv_um_per_v))
        conv_slow = float(convs.get(slow_axis, self.default_conv_um_per_v))
        tb = int(turnback.get(fast_axis, 0))

        bidir = bool(scan_params.get("bidirectional_scan", False))

        vmins = scan_params.get("min_voltages", {})
        vmaxs = scan_params.get("max_voltages", {})
        fast_vmin = float(vmins.get(fast_axis, -5.0))
        fast_vmax = float(vmaxs.get(fast_axis, 5.0))
        slow_vmin = float(vmins.get(slow_axis, -5.0))
        slow_vmax = float(vmaxs.get(slow_axis, 5.0))

        t_ms, fast_v, slow_v = self.generate_raster(
            pix_x=pix_x,
            pix_y=pix_y,
            dwell_time_s=dwell_time_s,
            size_x_um=size_fast_um,
            size_y_um=size_slow_um,
            offset_x_um=off_fast_um,
            offset_y_um=off_slow_um,
            conv_x_um_per_v=conv_fast,
            conv_y_um_per_v=conv_slow,
            x_vmin=fast_vmin,
            x_vmax=fast_vmax,
            y_vmin=slow_vmin,
            y_vmax=slow_vmax,
            bidirectional=bidir,
            turnback_offset_px=tb,
            samples_per_pixel=samples_per_pixel,
        )

        x_v, y_v = self._map_fast_slow_to_galvos(
            fast_axis=fast_axis,
            slow_axis=slow_axis,
            fast_wave=fast_v,
            slow_wave=slow_v,
        )
        
        self.analog_waveforms_ready.emit(t_ms, x_v, y_v)