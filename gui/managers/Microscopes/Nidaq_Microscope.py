from __future__ import annotations

import time
import numpy as np
from PySide6.QtCore import Slot

from .Microscope_Backend_Base import MicroscopeBackendBase
from ..Hardware_Manager import (
    NI_DEVICE_NAME,
    NI_AO_X,
    NI_AO_Y,
    NI_AI_IR,
    NI_AI_VIS,
    NI_AI_MIN_V,
    NI_AI_MAX_V,
    NI_AI_TERMINAL_MODE,
)

from ..Scan_Types import ExecutionPlan, FrameReconstructionPlan, SampleFramePlan
from ..Frame_Builder import FrameBuilder
from ..Sample_Scan_Manager import SampleScanManager
import warnings


try:
    import nidaqmx
    from nidaqmx.constants import (
        AcquisitionType,
        TerminalConfiguration,
        WAIT_INFINITELY,
    )
    from nidaqmx.stream_writers import AnalogMultiChannelWriter
    from nidaqmx.stream_readers import AnalogMultiChannelReader
    from nidaqmx.errors import DaqError, DaqWarning
    _HAS_NIDAQ = True
except Exception:
    nidaqmx = None
    AcquisitionType = None
    TerminalConfiguration = None
    WAIT_INFINITELY = None
    AnalogMultiChannelWriter = None
    AnalogMultiChannelReader = None
    _HAS_NIDAQ = False
if _HAS_NIDAQ:
    warnings.filterwarnings(
        "ignore",
        message=".*Finite acquisition or generation has been stopped before the requested number of samples were acquired or generated.*",
        category=DaqWarning,
    )


class NidaqMicroscope(MicroscopeBackendBase):
    """
    Real NI-DAQ backend V1 for DeepLight.

    Implemented in this V1:
    - laser scan:
        * AO0 -> X galvo
        * AO1 -> Y galvo
        * AI0 -> PMT IR
        * AI1 -> PMT Vis
        * execution strictly driven by ExecutionPlan
    - sample scan:
        * software-timed NI reads per pixel
        * stepper moves still emitted through stepper_move_requested

    Important:
    - no X/Y stage hardware was specified, so sample scan using X-Stage / Y-Stage
      will only be fully real once those axes also get real controllers
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
        
        self.leading_skip_px = 0
        self.trailing_skip_px = 0

        self._active_ao_task = None
        self._active_ai_task = None

        self._step_event_cursor = 0

        self.configure(scan_parameters or {})

    def _set_active_tasks(self, ao_task=None, ai_task=None):
        self._active_ao_task = ao_task
        self._active_ai_task = ai_task

    def _clear_active_tasks(self):
        self._active_ao_task = None
        self._active_ai_task = None
    
    def _log(self, msg: str):
        print(f"[NidaqMicroscope] {msg}")

    def _require_nidaq(self):
        if not _HAS_NIDAQ:
            raise RuntimeError(
                "nidaqmx is not installed or NI-DAQmx is unavailable. "
                "Install nidaqmx and the NI-DAQmx driver."
            )

    def _terminal_config(self):
        mode = str(NI_AI_TERMINAL_MODE).upper()

        if mode == "DIFF":
            return TerminalConfiguration.DIFF
        if mode == "NRSE":
            return TerminalConfiguration.NRSE
        if mode == "RSE":
            return TerminalConfiguration.RSE
        if mode in {"PSEUDO_DIFF", "PSEUDODIFF"}:
            return TerminalConfiguration.PSEUDO_DIFF

        raise ValueError(f"Unsupported NI_AI_TERMINAL_MODE: {NI_AI_TERMINAL_MODE!r}")

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

        # Keep the channel names from the UI if present.
        # If none are supplied, fall back to the two physical PMTs.
        self.channels = list(self.scan_parameters.get("active_channels") or ["PMT_IR", "PMT_Vis"])

        self.bidirectional_scan = bool(self.scan_parameters.get("bidirectional_scan", False))
        self.bidirectional_shift_px = int(self.scan_parameters.get("bidirectional_shift_px", 0) or 0)

        self.overscan_fraction = float(self.scan_parameters.get("overscan_fraction", 0.0) or 0.0)
        self.overscan_fraction = max(0.0, min(0.30, self.overscan_fraction))

        if self.scan_kind == "sample":
            self.sample_scan_manager.configure(self.scan_parameters)
            self.dim_image_y, self.dim_image_x = self.sample_scan_manager.image_shape()
            self.dim_fast = self.dim_image_x
            self.dim_slow = self.dim_image_y
            self.fast_axis_is_image_x = True

            self.leading_skip_px = 0
            self.trailing_skip_px = 0

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
            self.dim_image_x = self.dim_fast
            self.dim_image_y = self.dim_slow
            self.fast_axis_is_image_x = True

            self.leading_skip_px = int(round(self.overscan_fraction * self.dim_fast))
            self.trailing_skip_px = int(round(self.overscan_fraction * self.dim_fast))

        else:
            self.dim_fast = max(1, int(self.pixel_values[0]))
            self.dim_slow = max(1, int(self.pixel_values[1]))
            self.dim_image_x = self.dim_fast
            self.dim_image_y = self.dim_slow
            self.fast_axis_is_image_x = True

            self.leading_skip_px = 0
            self.trailing_skip_px = 0

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
            self.leading_skip_px = int(md.get("leading_skip_px", 0) or 0)
            self.trailing_skip_px = int(md.get("trailing_skip_px", 0) or 0)
            self.overscan_fraction = float(md.get("overscan_fraction", 0.0) or 0.0)

            self._log(
                f"ExecutionPlan loaded image={self.dim_image_x}x{self.dim_image_y} "
                f"fast={self.dim_fast} slow={self.dim_slow} "
                f"spp={int(plan.samples_per_pixel)} sr={float(plan.sample_rate_hz):.3f} Hz"
            )
        else:
            self.leading_skip_px = 0
            self.trailing_skip_px = 0

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
            leading_skip_px=int(self.leading_skip_px),
            trailing_skip_px=int(self.trailing_skip_px),
            fast_axis_is_image_x=bool(self.fast_axis_is_image_x),
        )

    def _ao_voltage_limits(self):
        mins = self.scan_parameters.get("min_voltages", {})
        maxs = self.scan_parameters.get("max_voltages", {})

        x_min = float(mins.get("X-Galvo", -5.0))
        x_max = float(maxs.get("X-Galvo", 5.0))
        y_min = float(mins.get("Y-Galvo", -5.0))
        y_max = float(maxs.get("Y-Galvo", 5.0))

        ao_min = min(x_min, y_min)
        ao_max = max(x_max, y_max)
        return ao_min, ao_max

    def _write_ao_idle_zero(self):
        """
        Force immédiatement les sorties AO X/Y à 0 V.
        Utilisé en fin de run pour éviter de laisser les galvos
        sur la dernière valeur du waveform fini.
        """
        self._require_nidaq()

        ao_min, ao_max = self._ao_voltage_limits()

        with nidaqmx.Task("DL_AO_IdleZero") as ao_task:
            ao_task.ao_channels.add_ao_voltage_chan(NI_AO_X, min_val=ao_min, max_val=ao_max)
            ao_task.ao_channels.add_ao_voltage_chan(NI_AO_Y, min_val=ao_min, max_val=ao_max)

            # écriture software-timed d'un sample par canal
            ao_task.write([0.0, 0.0], auto_start=True)
    
    def _active_ai_count(self) -> int:
        return min(2, max(1, len(self.channels)))

    def _map_ai_to_channels(self, ai_data: np.ndarray, n_samples: int) -> dict[str, np.ndarray]:
        out = {}
        n_ai = int(ai_data.shape[0]) if ai_data.ndim == 2 else 1

        for ch_idx, ch in enumerate(self.channels):
            if ch_idx < n_ai:
                out[ch] = np.asarray(ai_data[ch_idx], dtype=np.float64)
            else:
                out[ch] = np.zeros((n_samples,), dtype=np.float64)
        return out

    def _next_step_event_sample_after(self, sample_cursor: int, frame_stop: int) -> int | None:
        """
        Retourne le sample_index du prochain step event strictement après sample_cursor
        et au plus tard dans cette frame. Sinon None.
        """
        if self.execution_plan is None:
            return None

        events = self.execution_plan.step_events
        i = int(self._step_event_cursor)

        while i < len(events):
            ev_sample = int(events[i].sample_index)

            if ev_sample <= int(sample_cursor):
                i += 1
                continue

            if ev_sample < int(frame_stop):
                return ev_sample

            break

        return None
    
    def _read_frame_from_ni(
        self,
        *,
        sample_start: int,
        n_samples: int,
        ao_x: np.ndarray,
        ao_y: np.ndarray,
        arrays: dict[str, np.ndarray],
        plan: ExecutionPlan,
        frame_useful_samples: int,
        chunk_samples: int = 4096,
        clear_arrays: bool = True,
    ) -> dict[str, np.ndarray]:
        self._require_nidaq()

        if n_samples <= 0:
            return {ch: np.zeros((0,), dtype=np.float64) for ch in self.channels}

        ao_min, ao_max = self._ao_voltage_limits()
        sr = float(self.execution_plan.sample_rate_hz)

        ao_block = np.vstack([ao_x, ao_y]).astype(np.float64, copy=False)
        ai_count = self._active_ai_count()

        reconstruction_plan = self._build_frame_reconstruction_plan(int(plan.samples_per_pixel))
        builder = FrameBuilder(
            arrays=arrays,
            channels=self.channels,
            reconstruction_plan=reconstruction_plan,
        )
        builder.reset(clear_arrays=clear_arrays)

        collected = {ch: [] for ch in self.channels}
        remaining = int(n_samples)
        chunk_samples = max(1, int(chunk_samples))
        sample_cursor = int(sample_start)
        frame_stop = int(sample_start) + int(n_samples)

        with nidaqmx.Task("DL_AO_Frame") as ao_task, nidaqmx.Task("DL_AI_Frame") as ai_task:
            self._set_active_tasks(ao_task=ao_task, ai_task=ai_task)
            try:
                ao_task.ao_channels.add_ao_voltage_chan(NI_AO_X, min_val=ao_min, max_val=ao_max)
                ao_task.ao_channels.add_ao_voltage_chan(NI_AO_Y, min_val=ao_min, max_val=ao_max)

                ai_task.ai_channels.add_ai_voltage_chan(
                    NI_AI_IR,
                    min_val=NI_AI_MIN_V,
                    max_val=NI_AI_MAX_V,
                    terminal_config=self._terminal_config(),
                )
                if ai_count >= 2:
                    ai_task.ai_channels.add_ai_voltage_chan(
                        NI_AI_VIS,
                        min_val=NI_AI_MIN_V,
                        max_val=NI_AI_MAX_V,
                        terminal_config=self._terminal_config(),
                    )

                ao_task.timing.cfg_samp_clk_timing(
                    rate=sr,
                    sample_mode=AcquisitionType.FINITE,
                    samps_per_chan=int(n_samples),
                )

                ai_task.timing.cfg_samp_clk_timing(
                    rate=sr,
                    source=f"/{NI_DEVICE_NAME}/ao/SampleClock",
                    sample_mode=AcquisitionType.FINITE,
                    samps_per_chan=int(n_samples),
                )
                ai_task.triggers.start_trigger.cfg_dig_edge_start_trig(f"/{NI_DEVICE_NAME}/ao/StartTrigger")

                writer = AnalogMultiChannelWriter(ao_task.out_stream, auto_start=False)
                reader = AnalogMultiChannelReader(ai_task.in_stream)

                writer.write_many_sample(ao_block)

                self._emit_step_events_up_to(int(sample_start))

                ai_task.start()
                ao_task.start()

                while remaining > 0 and not self.acquisition_stop_event.is_set():
                    take = min(chunk_samples, remaining)

                    next_step_sample = self._next_step_event_sample_after(
                        sample_cursor=int(sample_cursor),
                        frame_stop=int(frame_stop),
                    )
                    if next_step_sample is not None:
                        take = min(take, max(1, int(next_step_sample) - int(sample_cursor)))
                    ai_chunk = np.zeros((ai_count, int(take)), dtype=np.float64)

                    try:
                        reader.read_many_sample(
                            ai_chunk,
                            number_of_samples_per_channel=int(take),
                            timeout=0.1,
                        )
                    except DaqWarning as w:
                        if self.acquisition_stop_event.is_set() and getattr(w, "error_code", None) == 200010:
                            break
                        raise
                    except DaqError:
                        if self.acquisition_stop_event.is_set():
                            break
                        raise
                    except Exception:
                        if self.acquisition_stop_event.is_set():
                            break
                        raise

                    mapped = self._map_ai_to_channels(ai_chunk, int(take))

                    for ch in self.channels:
                        collected[ch].append(mapped[ch])

                    builder.consume_samples(mapped)

                    self.samples_progress.emit(int(take))
                    sample_cursor += int(take)
                    self._emit_step_events_up_to(int(sample_cursor))
                    remaining -= int(take)

                if not self.acquisition_stop_event.is_set():
                    try:
                        ao_task.wait_until_done(WAIT_INFINITELY)
                    except DaqWarning as w:
                        if not (self.acquisition_stop_event.is_set() and getattr(w, "error_code", None) == 200010):
                            raise
                    except DaqError:
                        if not self.acquisition_stop_event.is_set():
                            raise
            finally: 
                self._clear_active_tasks()

        return {
            ch: np.concatenate(collected[ch]).astype(np.float64, copy=False) if collected[ch] else np.zeros((0,), dtype=np.float64)
            for ch in self.channels
        }

    def _acquire_plan_frame(
        self,
        arrays: dict[str, np.ndarray],
        *,
        fs,
        plan: ExecutionPlan,
        clear_arrays: bool = True,
    ) -> dict[str, np.ndarray]:
        frame_start = int(fs.sample_start)
        frame_stop = int(fs.sample_stop)
        n_samples = frame_stop - frame_start

        ao_x = np.asarray(plan.ao_x[frame_start:frame_stop], dtype=np.float64)
        ao_y = np.asarray(plan.ao_y[frame_start:frame_stop], dtype=np.float64)

        frame_useful_samples = int((plan.metadata or {}).get(
            "frame_useful_samples",
            self.dim_image_x * self.dim_image_y * max(1, int(plan.samples_per_pixel))
        ))

        self._read_frame_from_ni(
            sample_start=frame_start,
            n_samples=n_samples,
            ao_x=ao_x,
            ao_y=ao_y,
            arrays=arrays,
            plan=plan,
            frame_useful_samples=frame_useful_samples,
            chunk_samples=4096,
            clear_arrays=clear_arrays,
        )

        return {ch: arrays[ch].copy() for ch in self.channels}

    def _run_acquisition_from_plan(self):
        plan = self.execution_plan
        if plan is None:
            raise RuntimeError("run_acquisition requires an ExecutionPlan")

        arrays = {ch: self.shared_images[ch] for ch in self.channels}

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

    def _run_preview_from_plan(self, clear_arrays: bool = True):
        plan = self.execution_plan
        if plan is None:
            self._log("No execution plan for laser preview -> skipping")
            return

        arrays = {ch: self.shared_images[ch] for ch in self.channels}
        self._step_event_cursor = 0

        if not plan.frame_slices:
            return

        fs = plan.frame_slices[0]

        if clear_arrays:
            for arr in arrays.values():
                arr.fill(0.0)

        shown_images = self._acquire_plan_frame(
            arrays,
            fs=fs,
            plan=plan,
            clear_arrays=clear_arrays,
        )

        if not self.acquisition_stop_event.is_set():
            self.frame_ready.emit(0, tuple(), shown_images)

    def _read_single_pixel_from_ai(self, dwell_s: float, samples_per_pixel: int) -> dict[str, float]:
        self._require_nidaq()

        dwell_s = max(1e-6, float(dwell_s))
        spp = max(1, int(samples_per_pixel))
        rate = max(1000.0, float(spp) / dwell_s)
        ai_count = self._active_ai_count()

        ai_result = np.zeros((ai_count, spp), dtype=np.float64)

        with nidaqmx.Task("DL_AI_Pixel") as ai_task:
            ai_task.ai_channels.add_ai_voltage_chan(
                NI_AI_IR,
                min_val=NI_AI_MIN_V,
                max_val=NI_AI_MAX_V,
                terminal_config=self._terminal_config(),
            )
            if ai_count >= 2:
                ai_task.ai_channels.add_ai_voltage_chan(
                    NI_AI_VIS,
                    min_val=NI_AI_MIN_V,
                    max_val=NI_AI_MAX_V,
                    terminal_config=self._terminal_config(),
                )

            ai_task.timing.cfg_samp_clk_timing(
                rate=rate,
                sample_mode=AcquisitionType.FINITE,
                samps_per_chan=spp,
            )

            reader = AnalogMultiChannelReader(ai_task.in_stream)
            ai_task.start()
            reader.read_many_sample(
                ai_result,
                number_of_samples_per_channel=spp,
                timeout=max(1.0, dwell_s * 5.0),
            )

        values = {}
        for ch_idx, ch in enumerate(self.channels):
            if ch_idx < ai_result.shape[0]:
                values[ch] = float(np.mean(ai_result[ch_idx]))
            else:
                values[ch] = 0.0
        return values

    def _emit_sample_axis_move(self, axis_name: str, target_rel: float, reason: str, t_sched_ms: float):
        self.stepper_move_requested.emit(
            str(axis_name),
            float(target_rel),
            float(t_sched_ms),
            str(reason),
        )

    def _run_sample_frame(self, rep_index: int = 0, frame_plan: SampleFramePlan | None = None):
        if frame_plan is None:
            frame_plan = SampleFramePlan()

        arrays = {ch: self.shared_images[ch] for ch in self.channels}

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
                float(t_sched_ms),
                "sample_pixel_x",
            )
            self.stepper_move_requested.emit(
                str(event.y_axis_name),
                float(event.y_target_rel_um),
                float(t_sched_ms),
                "sample_pixel_y",
            )

            if settle_s > 0:
                time.sleep(settle_s)

            pixel_values = self._read_single_pixel_from_ai(dwell_s=dwell_s if dwell_s > 0 else 1e-4, samples_per_pixel=spp)

            for ch in self.channels:
                arrays[ch][int(event.iy), int(event.ix)] = float(pixel_values.get(ch, 0.0))

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

        try:
            if self.scan_kind == "sample":
                shown_images = self._run_sample_frame(rep_index=0)
                if not self.acquisition_stop_event.is_set():
                    self.frame_ready.emit(0, tuple(), shown_images)
                return

            self._run_preview_from_plan(clear_arrays=True)

        finally:
            if self.scan_kind == "laser":
                try:
                    self._write_ao_idle_zero()
                except Exception as e:
                    self._log(f"AO idle zero failed after single: {e}")

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
            self._run_preview_from_plan(clear_arrays=False)
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

        for task in (self._active_ai_task, self._active_ao_task):
            if task is None:
                continue
            try:
                task.stop()
            except DaqWarning as w:
                if getattr(w, "error_code", None) != 200010:
                    raise
            except DaqError:
                pass
            except Exception:
                pass