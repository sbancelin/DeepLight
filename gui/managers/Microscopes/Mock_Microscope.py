import numpy as np
import time
from multiprocessing.shared_memory import SharedMemory
from PySide6.QtCore import Slot
from .Microscope_Backend_Base import MicroscopeBackendBase

from ..Scan_Types import ExecutionPlan, FrameReconstructionPlan, SampleFramePlan
from ..Detector_Manager import MockDetectorManager
from ..Frame_Builder import FrameBuilder
from ..Sample_Scan_Manager import SampleScanManager
from ..Sample_Detector_Integrator import SampleDetectorIntegrator


class MockMicroscope(MicroscopeBackendBase):
    """Classe pour simuler un microscope en mode mock."""

    def __init__(self, scan_parameters=None):
        super().__init__(scan_parameters=None)

        self.detector_manager = MockDetectorManager(self)
        self.detector_manager.configure_mock(
            random_mode=False,
            pattern_type="mixed",
            high_level=8.0,
            low_level=0.5,
            noise_on=0.3,
            noise_off=0.2,
        )

        self.sample_scan_manager = SampleScanManager()
        self.sample_detector_integrator = SampleDetectorIntegrator(self.detector_manager)

        self.scan_kind = "laser"
        self.pixel_source_kind = "analog_integrating"

        self._step_event_cursor = 0

        self.configure(scan_parameters or {})

    def configure(self, scan_parameters: dict):
        super().configure(scan_parameters)
        self.pixel_values = [int(v) for v in self.scan_parameters.get("pixel_values", [256, 256, 1, 1])]
        while len(self.pixel_values) < 4:
            self.pixel_values.append(1)

        self.axis_order = list(self.scan_parameters.get(
            "axis_order",
            ["X-Galvo", "Y-Galvo", "None", "None"]
        ))

        self.scan_kind = str(self.scan_parameters.get("scan_kind", "laser") or "laser")
        self.pixel_source_kind = str(
            self.scan_parameters.get("pixel_source_kind", "analog_integrating") or "analog_integrating"
        )

        active_axes = [ax for ax in self.axis_order if ax != "None"]

        if self.scan_kind == "sample":
            self.sample_scan_manager.configure(self.scan_parameters)

            self.dim_image_y, self.dim_image_x = self.sample_scan_manager.image_shape()
            self.dim_fast = self.dim_image_x
            self.dim_slow = self.dim_image_y
            self.fast_axis_is_image_x = True

        elif len(active_axes) >= 2:
            fast_axis = active_axes[0]
            slow_axis = active_axes[1]

            axis_row_map = {
                axis_name: i
                for i, axis_name in enumerate(self.axis_order)
                if axis_name != "None"
            }

            row_fast = axis_row_map[fast_axis]
            row_slow = axis_row_map[slow_axis]

            self.dim_fast = max(1, int(self.pixel_values[row_fast]))
            self.dim_slow = max(1, int(self.pixel_values[row_slow]))

            # les deux premiers axes actifs définissent simplement l'image 2D affichée, sans restriction XY/XZ/YZ-only.
            self.dim_image_x = self.dim_fast
            self.dim_image_y = self.dim_slow
            self.fast_axis_is_image_x = True

        else:
            self.dim_fast = max(1, int(self.pixel_values[0]))
            self.dim_slow = max(1, int(self.pixel_values[1]))
            self.dim_image_x = self.dim_fast
            self.dim_image_y = self.dim_slow
            self.fast_axis_is_image_x = True

        self.dwell_time = float(self.scan_parameters.get("dwell_time", 0.0))
        self.samples_per_pixel = max(1, int(self.scan_parameters.get("samples_per_pixel", 1)))
        self.active_axes = self.scan_parameters.get("active_axes", [])
        self.repetitions = int(self.scan_parameters.get("repetitions", 1))
        self.delay_between_rep = float(self.scan_parameters.get("delay_between_rep", 0.0))
        self.laser_off_between_rep = bool(self.scan_parameters.get("laser_off_between_rep", False))

        self.channels = list(self.scan_parameters.get("active_channels") or ["default"])

        self.bidirectional_scan = bool(self.scan_parameters.get("bidirectional_scan", False))
        self.bidirectional_shift_px = int(self.scan_parameters.get("bidirectional_shift_px", 0) or 0)

        self.overscan_fraction = float(self.scan_parameters.get("overscan_fraction", 0.10) or 0.10)
        self.overscan_fraction = max(0.0, min(0.30, self.overscan_fraction))
        self.frame_flyback_time_s = max(
            0.0,
            float(self.scan_parameters.get("frame_flyback_time_s", 0.0) or 0.0)
        )

        if self.scan_kind != "sample" and len(active_axes) >= 2:
            lead_px = int(round(self.overscan_fraction * self.dim_fast))
            trail_px = lead_px
            self.dim_fast_total = self.dim_fast + lead_px + trail_px
        else:
            lead_px = 0
            trail_px = 0
            self.dim_fast_total = self.dim_fast

        self.leading_skip_px = lead_px
        self.trailing_skip_px = trail_px

        self._recreate_shared_image()
        self.acquired = {}

        self.detector_manager.set_frame_shape(
            dim_image_x=self.dim_image_x,
            dim_image_y=self.dim_image_y,
            dim_fast=self.dim_fast,
            dim_slow=self.dim_slow,
            samples_per_pixel=self.samples_per_pixel,
            fast_axis_is_image_x=self.fast_axis_is_image_x,
            bidirectional=self.bidirectional_scan,
            leading_skip_px=int(self.leading_skip_px),
            trailing_skip_px=int(self.trailing_skip_px),
        )

    def configure_execution_plan(self, plan: ExecutionPlan | None):
        super().configure_execution_plan(plan)
        self._step_event_cursor = 0

        if plan is not None:
            md = plan.metadata or {}

            self.dim_fast = int(md.get("pix_fast", md.get("pix_x", 1)))
            self.dim_slow = int(md.get("pix_slow", md.get("pix_y", 1)))

            self.dim_image_x = int(md.get("pix_image_x", md.get("pix_x", 1)))
            self.dim_image_y = int(md.get("pix_image_y", md.get("pix_y", 1)))

            fast_axis = md.get("fast_axis")
            image_x_axis = md.get("image_x_axis")

            self.fast_axis_is_image_x = (fast_axis == image_x_axis)
            self.leading_skip_px = int(md.get("leading_skip_px", 0) or 0)
            self.trailing_skip_px = int(md.get("trailing_skip_px", 0) or 0)
            self.overscan_fraction = float(md.get("overscan_fraction", 0.0) or 0.0)
            self.frame_flyback_time_s = float(md.get("frame_flyback_time_s", 0.0) or 0.0)

        else:
            self.dim_fast = 1
            self.dim_slow = 1
            self.dim_image_x = 1
            self.dim_image_y = 1
            self.fast_axis_is_image_x = True
            self.leading_skip_px = 0
            self.trailing_skip_px = 0
            self.overscan_fraction = 0.0
            self.frame_flyback_time_s = 0.0

        self._recreate_shared_image()

        self.detector_manager.set_frame_shape(
            dim_image_x=self.dim_image_x,
            dim_image_y=self.dim_image_y,
            samples_per_pixel=self.samples_per_pixel,
            dim_fast=self.dim_fast,
            dim_slow=self.dim_slow,
            fast_axis_is_image_x=self.fast_axis_is_image_x,
            bidirectional=self.bidirectional_scan,
            leading_skip_px=int(self.leading_skip_px),
            trailing_skip_px=int(self.trailing_skip_px),
        )

    def _recreate_shared_image(self):
        for shm in self.shared_images.values():
            try:
                shm.close()
                shm.unlink()
            except Exception:
                pass
        self.shared_images = {}

        size_bytes = self.dim_image_x * self.dim_image_y * 8

        for ch in self.channels:
            shm = SharedMemory(create=True, size=size_bytes)
            self.shared_images[ch] = shm
            arr = np.ndarray((self.dim_image_y, self.dim_image_x), dtype=np.float64, buffer=shm.buf)
            arr.fill(0.0)

    def _emit_step_events_up_to(self, sample_stop: int):
        if self.execution_plan is None:
            return

        events = self.execution_plan.step_events
        while self._step_event_cursor < len(events):
            ev = events[self._step_event_cursor]
            if int(ev.sample_index) > int(sample_stop):
                break

            t_sched_ms = (float(ev.sample_index) / float(self.execution_plan.sample_rate_hz)) * 1e3

            self.stepper_move_requested.emit(
                str(ev.axis_name),
                float(ev.target_rel),
                float(ev.velocity),
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
            leading_skip_px=int(getattr(self, "leading_skip_px", 0)),
            trailing_skip_px=int(getattr(self, "trailing_skip_px", 0)),
            fast_axis_is_image_x=bool(self.fast_axis_is_image_x),
        )

    def _acquire_xy_frame(
        self,
        arrays: dict[str, np.ndarray],
        *,
        dt_ns: int,
        rep_index: int = 0,
        axis3_index: int = 0,
        axis4_index: int = 0,
        axis3_value: float | None = None,
        axis4_value: float | None = None,
        chunk_samples: int = 1024,
        samples_per_pixel: int = 1,
        bidirectional: bool = False,
        bidirectional_shift_px: int = 0,
        clear_arrays: bool = True,
    ):
        reconstruction_plan = self._build_frame_reconstruction_plan(samples_per_pixel)
        reconstruction_plan.bidirectional = bool(bidirectional)
        reconstruction_plan.bidirectional_shift_px = int(bidirectional_shift_px)

        builder = FrameBuilder(
            arrays=arrays,
            channels=self.channels,
            reconstruction_plan=reconstruction_plan,
        )
        builder.reset(clear_arrays=clear_arrays)

        self.detector_manager.start_frame(
            channels=self.channels,
            rep_index=rep_index,
            axis3_index=axis3_index,
            axis4_index=axis4_index,
            axis3_value=axis3_value,
            axis4_value=axis4_value,
        )

        next_t = time.perf_counter_ns()

        while not builder.is_complete():
            if self.acquisition_stop_event.is_set():
                break

            n_to_read = min(int(chunk_samples), builder.remaining_samples())
            if n_to_read <= 0:
                break

            samples_by_channel = self.detector_manager.read_samples(
                n_samples=n_to_read,
                channels=self.channels,
                rep_index=rep_index,
                axis3_index=axis3_index,
                axis4_index=axis4_index,
                axis3_value=axis3_value,
                axis4_value=axis4_value,
            )

            builder.consume_samples(samples_by_channel)

            if n_to_read > 0:
                self.samples_progress.emit(int(n_to_read))

            if dt_ns > 0:
                target_t = next_t + n_to_read * dt_ns
                while time.perf_counter_ns() < target_t:
                    time.sleep(0)
                next_t = target_t

        return builder.get_shown_images()

    def _simulate_acquisition(self, clear_arrays: bool = True):
        if self.acquisition_stop_event.is_set():
            return

        arrays = {
            ch: np.ndarray((self.dim_image_y, self.dim_image_x), dtype=np.float64, buffer=shm.buf)
         
            for ch, shm in self.shared_images.items()
        }

        spp = max(1, int(getattr(self, "samples_per_pixel", 1)))

        if self.dwell_time > 0:
            sample_rate_hz = float(spp) / float(self.dwell_time)
            dt_ns = int((1.0 / sample_rate_hz) * 1e9)
        else:
            dt_ns = 0

        shown_images = self._acquire_xy_frame(
            arrays,
            dt_ns=dt_ns,
            rep_index=0,
            axis3_index=0,
            axis4_index=0,
            axis3_value=None,
            axis4_value=None,
            chunk_samples=1024,
            samples_per_pixel=spp,
            bidirectional=self.bidirectional_scan,
            bidirectional_shift_px=self.bidirectional_shift_px,
            clear_arrays=clear_arrays,
        )

        if not self.acquisition_stop_event.is_set():
            self.frame_ready.emit(0, tuple(), shown_images)

        flyback_s = max(0.0, float(getattr(self, "frame_flyback_time_s", 0.0)))
        if flyback_s > 0 and not self.acquisition_stop_event.is_set():
            t_end = time.perf_counter() + flyback_s
            while time.perf_counter() < t_end:
                if self.acquisition_stop_event.is_set():
                    break
                time.sleep(0)

    def _emit_sample_axis_move(self, axis_name: str, target_rel: float, reason: str, t_sched_ms: float):
        self.stepper_move_requested.emit(
            str(axis_name),
            float(target_rel),
            0.0,
            float(t_sched_ms),
            str(reason),
        )

    def _emit_sample_stage_line_position(self, event, iy: int, ny: int, line_start_ix: int, line_stop_ix: int):
        t_sched_ms = float(iy) * (
            max(0.0, float(self.sample_scan_manager.dwell_time_s)) * max(1, int(self.sample_scan_manager.samples_per_pixel))
            + max(0.0, float(self.sample_scan_manager.sample_settle_time_s))
        ) * max(1, int(self.sample_scan_manager.nx)) * 1e3

        self.sample_status_updated.emit({
            "mode": "sample",
            "line_index": int(iy),
            "line_count": int(ny),
            "x_index_start": int(line_start_ix),
            "x_index_stop": int(line_stop_ix),
            "x_um": float(event.x_target_rel_um),
            "y_um": float(event.y_target_rel_um),
            "scheduled_ms": float(t_sched_ms),
        })

    def _run_sample_frame(self, rep_index: int = 0, frame_plan: SampleFramePlan | None = None):
        if frame_plan is None:
            frame_plan = SampleFramePlan()
        arrays = {
            ch: np.ndarray((self.dim_image_y, self.dim_image_x), dtype=np.float64, buffer=shm.buf)
            for ch, shm in self.shared_images.items()
        }

        for arr in arrays.values():
            arr.fill(0.0)

        ny, nx = self.sample_scan_manager.image_shape()

        for ch, arr in arrays.items():
            if arr.shape != (ny, nx):
                raise RuntimeError(
                    f"Sample scan image shape mismatch for channel {ch}: "
                    f"shared array shape={arr.shape}, expected={(ny, nx)}"
                )

        self.sample_detector_integrator.start_frame(
            channels=self.channels,
            width=nx,
            height=ny,
            rep_index=rep_index,
            axis3_index=int(frame_plan.axis3_index),
            axis4_index=int(frame_plan.axis4_index),
            axis3_value=frame_plan.axis3_value,
            axis4_value=frame_plan.axis4_value,
        )

        dwell_s = max(0.0, float(self.sample_scan_manager.dwell_time_s))
        settle_s = max(0.0, float(self.sample_scan_manager.sample_settle_time_s))
        spp = max(1, int(self.sample_scan_manager.samples_per_pixel))

        if frame_plan.axis4_name is not None and frame_plan.axis4_value is not None:
            self._emit_sample_axis_move(frame_plan.axis4_name, float(frame_plan.axis4_value), "sample_frame_axis4", 0.0)
        if frame_plan.axis3_name is not None and frame_plan.axis3_value is not None:
            self._emit_sample_axis_move(frame_plan.axis3_name, float(frame_plan.axis3_value), "sample_frame_axis3", 0.0)

        t0 = time.perf_counter()
        lines = [[] for _ in range(ny)]
        for event in self.sample_scan_manager.iter_pixel_events():
            lines[int(event.iy)].append(event)

        progress_accum = 0
        progress_chunk = 64
        pixel_done = 0

        flush_accum = 0
        flush_chunk = 16

        for iy, line_events in enumerate(lines):
            if self.acquisition_stop_event.is_set():
                break

            if not line_events:
                continue

            first_event = line_events[0]
            self._emit_sample_stage_line_position(
                first_event,
                iy=int(iy),
                ny=int(ny),
                line_start_ix=int(line_events[0].ix),
                line_stop_ix=int(line_events[-1].ix),
            )

            for event in line_events:
                if self.acquisition_stop_event.is_set():
                    break

                pixel_t0 = time.perf_counter()

                self._emit_sample_axis_move(event.x_axis_name, float(event.x_target_rel_um), "sample_pixel_x", 0.0)
                self._emit_sample_axis_move(event.y_axis_name, float(event.y_target_rel_um), "sample_pixel_y", 0.0)

                dwell_sub_s = dwell_s / float(max(1, spp))

                for ch in self.channels:
                    accum = 0.0
                    for _ in range(spp):
                        accum += float(
                            self.sample_detector_integrator.acquire_scalar(
                                channel=ch,
                                x_index=int(event.ix),
                                y_index=int(event.iy),
                                dwell_time_s=dwell_sub_s,
                                pixel_source_kind=self.pixel_source_kind,
                            )
                        )
                    arrays[ch][int(event.iy), int(event.ix)] = accum / float(spp)

                pixel_done += 1
                progress_accum += spp
                flush_accum += 1

                if flush_accum >= flush_chunk:
                    self.sample_image_flush_requested.emit()
                    flush_accum = 0

                if progress_accum >= progress_chunk:
                    self.samples_progress.emit(int(progress_accum))
                    progress_accum = 0

                self.sample_status_updated.emit({
                    "mode": "sample",
                    "line_index": int(iy),
                    "line_count": int(ny),
                    "pixel_done": int(pixel_done),
                    "pixel_total": int(nx * ny),
                    "progress": float(pixel_done / max(1, nx * ny)),
                    "x_um": float(event.x_target_rel_um),
                    "y_um": float(event.y_target_rel_um),
                    "ix": int(event.ix),
                    "iy": int(event.iy),
                    "rep_index": int(rep_index),
                    "idx_tuple": tuple(frame_plan.idx_tuple()),
                })

                target_t = pixel_t0 + max(0.0, settle_s) + max(0.0, dwell_s)
                remaining = target_t - time.perf_counter()

                if remaining > 0:
                    if remaining > 0.002:
                        coarse_sleep = remaining - 0.001
                        t_end = time.perf_counter() + coarse_sleep
                        while time.perf_counter() < t_end:
                            if self.acquisition_stop_event.is_set():
                                break
                            time.sleep(0.001)

                    while time.perf_counter() < target_t:
                        if self.acquisition_stop_event.is_set():
                            break
                        time.sleep(0)

            # Assure au moins un rafraîchissement par ligne
            self.sample_image_flush_requested.emit()

        if flush_accum > 0:
            self.sample_image_flush_requested.emit()

        if progress_accum > 0:
            self.samples_progress.emit(int(progress_accum))

        elapsed = time.perf_counter() - t0
        total_px = max(1, nx * ny)

        self.sample_status_updated.emit({
            "mode": "sample",
            "done": True,
            "elapsed_s": float(elapsed),
            "pixels_per_s": float(total_px / elapsed) if elapsed > 0 else 0.0,
            "pixel_total": int(total_px),
            "rep_index": int(rep_index),
            "idx_tuple": tuple(frame_plan.idx_tuple()),
        })

        shown_images = {ch: arrays[ch].copy() for ch in self.channels}
        return shown_images

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

        self._simulate_acquisition(clear_arrays=True)
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
            self._simulate_acquisition(clear_arrays=False)
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

    def _run_acquisition_from_plan(self):
        plan = self.execution_plan
        if plan is None:
            return

        arrays = {
            ch: np.ndarray((self.dim_image_y, self.dim_image_x), dtype=np.float64, buffer=shm.buf)
            for ch, shm in self.shared_images.items()
        }

        dt_ns = int((1.0 / float(plan.sample_rate_hz)) * 1e9)
        samples_per_pixel = max(1, int(plan.samples_per_pixel))
        frame_pixel_count = int(self.dim_image_x * self.dim_image_y)
        frame_useful_samples = int(frame_pixel_count * samples_per_pixel)
        
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
                    self.rep_finished.emit(current_rep)

                gap_samples = int(fs.sample_start) - int(prev_frame_stop)
                if gap_samples > 0:
                    self._sleep_delay_samples(gap_samples, float(plan.sample_rate_hz))

                self.rep_started.emit(rep)
                self.acquired.setdefault(rep, {})
                current_rep = rep
            else:
                gap_samples = int(fs.sample_start) - int(prev_frame_stop)
                if gap_samples > 0:
                    self._sleep_delay_samples(gap_samples, float(plan.sample_rate_hz))

            self._emit_step_events_up_to(int(fs.sample_start))

            idx_tuple = ()
            if fs.axis3_value is not None:
                idx_tuple += (int(fs.axis3_index),)
            if fs.axis4_value is not None:
                idx_tuple += (int(fs.axis4_index),)

            self.acquired[rep].setdefault(idx_tuple, {})

            frame_samples = int(fs.sample_stop) - int(fs.sample_start)
            if frame_samples < frame_useful_samples:
                raise ValueError(
                    f"FrameSlice trop court: frame_samples={frame_samples} < frame_useful_samples={frame_useful_samples}"
                )

            shown_images = self._acquire_xy_frame(
                arrays,
                dt_ns=dt_ns,
                rep_index=int(fs.rep_index),
                axis3_index=int(fs.axis3_index),
                axis4_index=int(fs.axis4_index),
                axis3_value=fs.axis3_value,
                axis4_value=fs.axis4_value,
                chunk_samples=1024,
                samples_per_pixel=samples_per_pixel,
                bidirectional=self.bidirectional_scan,
                bidirectional_shift_px=self.bidirectional_shift_px,
            )

            if self.acquisition_stop_event.is_set():
                break

            extra_samples = frame_samples - frame_useful_samples
            if extra_samples > 0:
                next_t = time.perf_counter_ns()
                extra_done = 0

                for _ in range(extra_samples):
                    if self.acquisition_stop_event.is_set():
                        break

                    if dt_ns > 0:
                        next_t += dt_ns
                        while time.perf_counter_ns() < next_t:
                            time.sleep(0)

                    extra_done += 1

                if extra_done > 0:
                    self.samples_progress.emit(int(extra_done))

            self._emit_step_events_up_to(int(fs.sample_stop))

            if self.acquisition_stop_event.is_set():
                break

            for ch in self.channels:
                self.acquired[rep][idx_tuple][ch] = arrays[ch].copy()

            self.frame_ready.emit(rep, idx_tuple, shown_images)
            prev_frame_stop = int(fs.sample_stop)

        self._emit_step_events_up_to(int(plan.total_samples))

        if current_rep is not None:
            self.rep_finished.emit(current_rep)

    @Slot()
    def stop(self):
        self.acquisition_stop_event.set()

    def __del__(self):
        try:
            for shm in self.shared_images.values():
                try:
                    shm.close()
                    shm.unlink()
                except Exception:
                    pass
        except Exception:
            pass