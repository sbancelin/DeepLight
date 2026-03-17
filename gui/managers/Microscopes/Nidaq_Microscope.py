from __future__ import annotations

import time
import numpy as np
from PySide6.QtCore import Slot

from .Microscope_Backend_Base import MicroscopeBackendBase

from ..Scan_Types import ExecutionPlan, FrameReconstructionPlan, SampleFramePlan
from ..Frame_Builder import FrameBuilder
from ..Sample_Scan_Manager import SampleScanManager


class NidaqMicroscope(MicroscopeBackendBase):
    """
    Backend "hardware-like" de transition.

    Objectif architectural :
    - en mode laser : exécuter l'ExecutionPlan produit par ScanManager
    - en mode sample : exécuter la logique SampleScanManager
    - n'inventer que les intensités détecteur
    - garder des signaux compatibles avec AcquisitionManager / MainWindow
    """

    def __init__(self, scan_parameters=None):
        super().__init__(scan_parameters=None)

        self.sample_scan_manager = SampleScanManager()

        self.scan_kind = "laser"
        self.pixel_source_kind = "analog_integrating"

        self.pixel_values = [64, 64, 1, 1]
        self.axis_order = ["X-Galvo", "Y-Galvo", "None", "None"]
        self.active_axes = []

        self.dim_fast = 1
        self.dim_slow = 1
        self.dim_image_x = 1
        self.dim_image_y = 1
        self.fast_axis_is_image_x = True

        self.dwell_time = 0.0
        self.samples_per_pixel = 1
        self.repetitions = 1
        self.delay_between_rep = 0.0
        self.bidirectional_scan = False
        self.bidirectional_shift_px = 0
        self.turnback_offset_px = 0

        self._step_event_cursor = 0

        self.configure(scan_parameters or {})

    def _log(self, msg: str):
        print(f"[NidaqMicroscope] {msg}")

    def configure(self, scan_parameters: dict):
        super().configure(scan_parameters)

        self.pixel_values = [int(v) for v in self.scan_parameters.get("pixel_values", [64, 64, 1, 1])]
        while len(self.pixel_values) < 4:
            self.pixel_values.append(1)

        self.axis_order = list(
            self.scan_parameters.get("axis_order", ["X-Galvo", "Y-Galvo", "None", "None"])
        )
        while len(self.axis_order) < 4:
            self.axis_order.append("None")

        self.scan_kind = str(self.scan_parameters.get("scan_kind", "laser") or "laser")
        self.pixel_source_kind = str(
            self.scan_parameters.get("pixel_source_kind", "analog_integrating") or "analog_integrating"
        )

        self.active_axes = [ax for ax in self.axis_order if ax != "None"]

        self.dwell_time = float(self.scan_parameters.get("dwell_time", 0.0) or 0.0)
        self.samples_per_pixel = max(1, int(self.scan_parameters.get("samples_per_pixel", 1) or 1))
        self.repetitions = max(1, int(self.scan_parameters.get("repetitions", 1) or 1))
        self.delay_between_rep = float(self.scan_parameters.get("delay_between_rep", 0.0) or 0.0)
        self.laser_off_between_rep = bool(
            self.scan_parameters.get("laser_off_between_rep", self.scan_parameters.get("turn_off_laser_between_rep", False))
        )

        self.channels = list(self.scan_parameters.get("active_channels") or ["default"])

        self.bidirectional_scan = bool(self.scan_parameters.get("bidirectional_scan", False))
        self.bidirectional_shift_px = int(self.scan_parameters.get("bidirectional_shift_px", 0) or 0)

        turnback_offset = self.scan_parameters.get("turnback_offset", {})
        fast_axis_name = self.active_axes[0] if len(self.active_axes) > 0 else None
        self.turnback_offset_px = int(turnback_offset.get(fast_axis_name, 0) or 0) if fast_axis_name else 0

        if self.scan_kind == "sample":
            self.sample_scan_manager.configure(self.scan_parameters)
            self.dim_image_y, self.dim_image_x = self.sample_scan_manager.image_shape()
            self.dim_fast = self.dim_image_x
            self.dim_slow = self.dim_image_y
            self.fast_axis_is_image_x = True

        elif len(self.active_axes) >= 2:
            fast_axis = self.active_axes[0]
            slow_axis = self.active_axes[1]

            axis_row_map = {
                axis_name: i
                for i, axis_name in enumerate(self.axis_order)
                if axis_name != "None"
            }

            row_fast = axis_row_map[fast_axis]
            row_slow = axis_row_map[slow_axis]

            self.dim_fast = max(1, int(self.pixel_values[row_fast]))
            self.dim_slow = max(1, int(self.pixel_values[row_slow]))

            # Fallback de preview si aucun plan n'est injecté.
            self.dim_image_x = self.dim_fast
            self.dim_image_y = self.dim_slow
            self.fast_axis_is_image_x = True

        else:
            self.dim_fast = max(1, int(self.pixel_values[0]))
            self.dim_slow = max(1, int(self.pixel_values[1]))
            self.dim_image_x = self.dim_fast
            self.dim_image_y = self.dim_slow
            self.fast_axis_is_image_x = True

        self._recreate_shared_image()
        self.acquired = {}
        self._log(
            f"Configured scan_kind={self.scan_kind} "
            f"image={self.dim_image_x}x{self.dim_image_y} "
            f"fast={self.dim_fast} slow={self.dim_slow}"
        )

    def configure_execution_plan(self, plan: ExecutionPlan | None):
        self.execution_plan = plan
        self._step_event_cursor = 0

        if plan is not None:
            md = dict(plan.metadata or {})

            self.dim_fast = int(md.get("pix_fast", md.get("pix_x", 1)) or 1)
            self.dim_slow = int(md.get("pix_slow", md.get("pix_y", 1)) or 1)

            self.dim_image_x = int(md.get("pix_image_x", md.get("pix_x", 1)) or 1)
            self.dim_image_y = int(md.get("pix_image_y", md.get("pix_y", 1)) or 1)

            fast_axis = md.get("fast_axis")
            image_x_axis = md.get("image_x_axis")

            self.fast_axis_is_image_x = (fast_axis == image_x_axis)
            self.turnback_offset_px = int(md.get("turnback_offset_px", 0) or 0)

            self._log(
                f"ExecutionPlan loaded image={self.dim_image_x}x{self.dim_image_y} "
                f"fast={self.dim_fast} slow={self.dim_slow} "
                f"spp={int(plan.samples_per_pixel)} sr={float(plan.sample_rate_hz):.3f} Hz"
            )

        self._recreate_shared_image()

    def _recreate_shared_image(self):
        self.shared_images = {
            ch: np.zeros((self.dim_image_y, self.dim_image_x), dtype=np.float64)
            for ch in self.channels
        }

    def _emit_step_events_up_to(self, sample_stop: int):
        if self.execution_plan is None:
            return

        events = self.execution_plan.step_events
        sr = float(self.execution_plan.sample_rate_hz)

        while self._step_event_cursor < len(events):
            ev = events[self._step_event_cursor]
            if int(ev.sample_index) > int(sample_stop):
                break

            t_sched_ms = 0.0
            if sr > 0:
                t_sched_ms = (float(ev.sample_index) / sr) * 1e3

            self.stepper_move_requested.emit(
                str(ev.axis_name),
                float(ev.target_rel),
                float(ev.velocity_um_s),
                float(ev.acceleration_um_s2),
                float(ev.jerk_um_s3),
                float(t_sched_ms),
                str(ev.reason),
            )
            self._step_event_cursor += 1

    def _sleep_delay_samples(self, delay_samples: int, sample_rate_hz: float):
        if delay_samples <= 0 or sample_rate_hz <= 0:
            return

        remaining_samples = int(delay_samples)
        chunk_samples = max(1, int(round(float(sample_rate_hz) * 0.050)))

        while remaining_samples > 0:
            if self.acquisition_stop_event.is_set():
                break

            take = min(chunk_samples, remaining_samples)
            dt_s = float(take) / float(sample_rate_hz)

            time.sleep(dt_s)
            self.samples_progress.emit(int(take))
            remaining_samples -= take

    def _build_frame_reconstruction_plan(self, samples_per_pixel: int) -> FrameReconstructionPlan:
        return FrameReconstructionPlan(
            dim_x=self.dim_image_x,
            dim_y=self.dim_image_y,
            dim_fast=self.dim_fast,
            dim_slow=self.dim_slow,
            samples_per_pixel=max(1, int(samples_per_pixel)),
            bidirectional=bool(self.bidirectional_scan),
            bidirectional_shift_px=int(self.bidirectional_shift_px),
            turnback_offset_px=int(self.turnback_offset_px),
            fast_axis_is_image_x=bool(self.fast_axis_is_image_x),
        )

    def _make_synthetic_samples(
        self,
        *,
        sample_start: int,
        n_samples: int,
        frame_useful_samples: int,
        samples_per_pixel: int,
        rep_index: int = 0,
        axis3_index: int = 0,
        axis4_index: int = 0,
    ) -> dict[str, np.ndarray]:
        """
        Génère un signal détecteur synthétique 1D, en respectant la timeline du plan.
        Les samples "hors zone utile" (turnback, padding) valent 0.
        """
        n_samples = max(0, int(n_samples))
        start = int(sample_start)
        spp = max(1, int(samples_per_pixel))
        useful = max(0, int(frame_useful_samples))

        out = {ch: np.zeros((n_samples,), dtype=np.float64) for ch in self.channels}
        if n_samples <= 0 or useful <= 0:
            return out

        x_max = max(1, int(self.dim_image_x) - 1)
        y_max = max(1, int(self.dim_image_y) - 1)

        for i in range(n_samples):
            s = start + i
            if s < 0 or s >= useful:
                val = 0.0
            else:
                pixel_index = s // spp
                row = pixel_index // max(1, int(self.dim_fast))
                col = pixel_index % max(1, int(self.dim_fast))

                # Coordonnées image logiques
                if self.fast_axis_is_image_x:
                    x = col
                    y = row
                else:
                    x = row
                    y = col

                x = max(0, min(int(x), int(self.dim_image_x) - 1))
                y = max(0, min(int(y), int(self.dim_image_y) - 1))

                gx = float(x) / float(x_max) if x_max > 0 else 0.0
                gy = float(y) / float(y_max) if y_max > 0 else 0.0

                # gradient déterministe + légère signature rep/axes
                val = (
                    40.0
                    + 120.0 * gx
                    + 80.0 * gy
                    + 7.0 * float(rep_index)
                    + 3.0 * float(axis3_index)
                    + 1.5 * float(axis4_index)
                )

            for ch_index, ch in enumerate(self.channels):
                out[ch][i] = val + 5.0 * float(ch_index)

        return out

    def _acquire_plan_frame(
        self,
        arrays: dict[str, np.ndarray],
        *,
        fs,
        plan: ExecutionPlan,
    ) -> dict[str, np.ndarray]:
        """
        Exécute UNE frame laser à partir du plan.
        Le scan n'est pas inventé ici : on suit frame_slices + sample_rate + step_events.
        """
        spp = max(1, int(plan.samples_per_pixel))
        sr = float(plan.sample_rate_hz)
        frame_samples = int(fs.sample_stop) - int(fs.sample_start)
        frame_useful_samples = int((plan.metadata or {}).get(
            "frame_useful_samples",
            self.dim_fast * self.dim_slow * spp
        ))

        reconstruction_plan = self._build_frame_reconstruction_plan(spp)

        builder = FrameBuilder(
            arrays=arrays,
            channels=self.channels,
            reconstruction_plan=reconstruction_plan,
        )
        builder.reset()

        self._emit_step_events_up_to(int(fs.sample_start))

        remaining = frame_samples
        current_sample = 0

        chunk_samples = max(1, min(4096, spp * max(1, int(self.dim_fast // 8 or 1))))

        next_t = time.perf_counter_ns()
        dt_ns = int((1.0 / sr) * 1e9) if sr > 0 else 0

        while remaining > 0 and not self.acquisition_stop_event.is_set():
            take = min(chunk_samples, remaining)

            samples_by_channel = self._make_synthetic_samples(
                sample_start=current_sample,
                n_samples=take,
                frame_useful_samples=frame_useful_samples,
                samples_per_pixel=spp,
                rep_index=int(fs.rep_index),
                axis3_index=int(fs.axis3_index),
                axis4_index=int(fs.axis4_index),
            )

            builder.consume_samples(samples_by_channel)

            self.samples_progress.emit(int(take))

            if dt_ns > 0:
                target_t = next_t + take * dt_ns
                while time.perf_counter_ns() < target_t:
                    if self.acquisition_stop_event.is_set():
                        break
                    time.sleep(0)
                next_t = target_t

            current_sample += take
            remaining -= take

        self._emit_step_events_up_to(int(fs.sample_stop))

        return builder.get_shown_images()

    def _run_acquisition_from_plan(self):
        plan = self.execution_plan
        if plan is None:
            raise RuntimeError("run_acquisition requires an ExecutionPlan")

        arrays = {
            ch: self.shared_images[ch]
            for ch in self.channels
        }

        current_rep = None
        prev_frame_stop = 0

        self.acquired = {}
        self._step_event_cursor = 0

        for fs in plan.frame_slices:
            if self.acquisition_stop_event.is_set():
                break

            rep = int(fs.rep_index)
            if rep != current_rep:
                if current_rep is not None:
                    self.rep_finished.emit(int(current_rep))

                gap_samples = int(fs.sample_start) - int(prev_frame_stop)
                if gap_samples > 0:
                    self._sleep_delay_samples(gap_samples, float(plan.sample_rate_hz))

                self.rep_started.emit(int(rep))
                self.acquired.setdefault(int(rep), {})
                current_rep = rep
            else:
                gap_samples = int(fs.sample_start) - int(prev_frame_stop)
                if gap_samples > 0:
                    self._sleep_delay_samples(gap_samples, float(plan.sample_rate_hz))

            idx_tuple = ()
            if fs.axis3_value is not None:
                idx_tuple += (int(fs.axis3_index),)
            if fs.axis4_value is not None:
                idx_tuple += (int(fs.axis4_index),)

            self.acquired[int(rep)].setdefault(idx_tuple, {})

            for arr in arrays.values():
                arr.fill(0.0)

            shown_images = self._acquire_plan_frame(arrays, fs=fs, plan=plan)

            if self.acquisition_stop_event.is_set():
                break

            for ch in self.channels:
                self.acquired[int(rep)][idx_tuple][ch] = shown_images[ch].copy()

            self.frame_ready.emit(int(rep), idx_tuple, shown_images)
            prev_frame_stop = int(fs.sample_stop)

        if current_rep is not None and not self.acquisition_stop_event.is_set():
            self.rep_finished.emit(int(current_rep))

        if not self.acquisition_stop_event.is_set():
            tail_gap = int(plan.total_samples) - int(prev_frame_stop)
            if tail_gap > 0:
                self._sleep_delay_samples(tail_gap, float(plan.sample_rate_hz))
                self._emit_step_events_up_to(int(plan.total_samples))

    def _run_preview_from_plan(self):
        """
        Preview laser :
        on suit aussi le plan s'il existe, mais on n'accumule pas self.acquired.
        """
        plan = self.execution_plan
        if plan is None:
            self._log("No execution plan for laser preview -> skipping")
            return

        arrays = {
            ch: self.shared_images[ch]
            for ch in self.channels
        }

        self._step_event_cursor = 0

        if not plan.frame_slices:
            return

        fs = plan.frame_slices[0]

        for arr in arrays.values():
            arr.fill(0.0)

        shown_images = self._acquire_plan_frame(arrays, fs=fs, plan=plan)

        if not self.acquisition_stop_event.is_set():
            self.frame_ready.emit(0, tuple(), shown_images)

    def _sample_pixel_value(
        self,
        *,
        ix: int,
        iy: int,
        rep_index: int = 0,
        axis3_index: int = 0,
        axis4_index: int = 0,
        channel_index: int = 0,
    ) -> float:
        x_max = max(1, int(self.dim_image_x) - 1)
        y_max = max(1, int(self.dim_image_y) - 1)

        gx = float(ix) / float(x_max) if x_max > 0 else 0.0
        gy = float(iy) / float(y_max) if y_max > 0 else 0.0

        return (
            40.0
            + 120.0 * gx
            + 80.0 * gy
            + 7.0 * float(rep_index)
            + 3.0 * float(axis3_index)
            + 1.5 * float(axis4_index)
            + 5.0 * float(channel_index)
        )

    def _emit_sample_axis_move(self, axis_name: str, target_rel: float, reason: str, t_sched_ms: float):
        self.stepper_move_requested.emit(
            str(axis_name),
            float(target_rel),
            0.0, 0.0, 0.0,
            float(t_sched_ms),
            str(reason),
        )

    def _run_sample_frame(self, rep_index: int = 0, frame_plan: SampleFramePlan | None = None):
        if frame_plan is None:
            frame_plan = SampleFramePlan()

        arrays = {
            ch: self.shared_images[ch]
            for ch in self.channels
        }

        for arr in arrays.values():
            arr.fill(0.0)

        ny, _ = self.sample_scan_manager.image_shape()

        if frame_plan.axis4_name is not None and frame_plan.axis4_value is not None:
            self._emit_sample_axis_move(frame_plan.axis4_name, float(frame_plan.axis4_value), "sample_frame_axis4", 0.0)
        if frame_plan.axis3_name is not None and frame_plan.axis3_value is not None:
            self._emit_sample_axis_move(frame_plan.axis3_name, float(frame_plan.axis3_value), "sample_frame_axis3", 0.0)

        dwell_s = max(0.0, float(self.sample_scan_manager.dwell_time_s))
        settle_s = max(0.0, float(self.sample_scan_manager.sample_settle_time_s))
        spp = max(1, int(self.sample_scan_manager.samples_per_pixel))

        progress_accum = 0
        flush_accum = 0
        flush_chunk = 16
        pixel_done = 0

        for event in self.sample_scan_manager.iter_pixel_events():
            if self.acquisition_stop_event.is_set():
                break

            t_sched_ms = (
                (dwell_s * float(pixel_done))
                + (settle_s * float(pixel_done))
            ) * 1e3

            self.sample_status_updated.emit({
                "mode": "sample",
                "line_index": int(event.iy),
                "line_count": int(ny),
                "x_index_start": int(event.ix),
                "x_index_stop": int(event.ix),
                "x_um": float(event.x_target_rel_um),
                "y_um": float(event.y_target_rel_um),
                "scheduled_ms": float(t_sched_ms),
            })

            self.stepper_move_requested.emit(
                str(event.x_axis_name),
                float(event.x_target_rel_um),
                0.0, 0.0, 0.0,
                float(t_sched_ms),
                "sample_pixel_x",
            )
            self.stepper_move_requested.emit(
                str(event.y_axis_name),
                float(event.y_target_rel_um),
                0.0, 0.0, 0.0,
                float(t_sched_ms),
                "sample_pixel_y",
            )

            for ch_index, ch in enumerate(self.channels):
                arrays[ch][int(event.iy), int(event.ix)] = self._sample_pixel_value(
                    ix=event.ix,
                    iy=event.iy,
                    rep_index=rep_index,
                    axis3_index=frame_plan.axis3_index,
                    axis4_index=frame_plan.axis4_index,
                    channel_index=ch_index,
                )

            pixel_dt = dwell_s
            if pixel_dt > 0:
                time.sleep(pixel_dt)

            if settle_s > 0:
                time.sleep(settle_s)

            progress_accum += spp
            flush_accum += 1
            pixel_done += 1

            if progress_accum > 0:
                self.samples_progress.emit(int(progress_accum))
                progress_accum = 0

            if flush_accum >= flush_chunk:
                self.sample_image_flush_requested.emit()
                flush_accum = 0

        self.sample_image_flush_requested.emit()

        return {ch: arr.copy() for ch, arr in arrays.items()}

    def _run_sample_acquisition(self):
        self.acquired = {}

        frame_plans = list(self.sample_scan_manager.iter_frame_plans())
        if not frame_plans:
            frame_plans = [SampleFramePlan()]

        n_reps = max(1, int(self.repetitions))
        
        for rep in range(n_reps):
            if self.acquisition_stop_event.is_set():
                break

            self.rep_started.emit(int(rep))

            for frame_plan in frame_plans:
                if self.acquisition_stop_event.is_set():
                    break

                shown_images = self._run_sample_frame(rep_index=int(rep), frame_plan=frame_plan)

                if self.acquisition_stop_event.is_set():
                    break

                idx_tuple = tuple(frame_plan.idx_tuple())
                self.acquired.setdefault(int(rep), {})
                self.acquired[int(rep)].setdefault(idx_tuple, {})
                for ch in self.channels:
                    self.acquired[int(rep)][idx_tuple][ch] = shown_images[ch].copy()

                self.frame_ready.emit(int(rep), idx_tuple, shown_images)

            self.rep_finished.emit(int(rep))

            if rep < n_reps - 1:
                delay_s = max(0.0, float(self.delay_between_rep))
                if delay_s > 0:
                    t_end = time.perf_counter() + delay_s
                    while time.perf_counter() < t_end:
                        if self.acquisition_stop_event.is_set():
                            break
                        time.sleep(0.01)

        for axis_name in (self.sample_scan_manager.axis3_name, self.sample_scan_manager.axis4_name):
            if axis_name is None:
                continue
            base = float(self.scan_parameters.get("initial_relative_positions", {}).get(axis_name, 0.0))
            self._emit_sample_axis_move(axis_name, base, "sample_return_to_base", 0.0)

    @Slot()
    def run_single(self):
        self.acquisition_stop_event.clear()

        if self.scan_kind == "sample":
            shown_images = self._run_sample_frame(rep_index=0)
            if not self.acquisition_stop_event.is_set():
                self.frame_ready.emit(0, tuple(), shown_images)
            self.acquisition_finished.emit()
            return

        self._run_preview_from_plan()
        self.acquisition_finished.emit()

    @Slot()
    def run_continuous(self):
        self.acquisition_stop_event.clear()

        if self.scan_kind == "sample":
            while not self.acquisition_stop_event.is_set():
                shown_images = self._run_sample_frame(rep_index=0)
                if not self.acquisition_stop_event.is_set():
                    self.frame_ready.emit(0, tuple(), shown_images)
                time.sleep(0)
            return

        while not self.acquisition_stop_event.is_set():
            self._run_preview_from_plan()
            time.sleep(0)

    @Slot()
    def run_acquisition(self):
        self.acquisition_stop_event.clear()

        if self.scan_kind == "sample":
            self._run_sample_acquisition()
            self.acquisition_finished.emit()
            return

        if self.execution_plan is None:
            raise RuntimeError("run_acquisition requires an ExecutionPlan")

        self._run_acquisition_from_plan()
        self.acquisition_finished.emit()

    @Slot()
    def stop(self):
        self._log("stop")
        super().stop()