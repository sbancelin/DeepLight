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
    NI_CI_DEFAULT_SAMPLE_CLOCK,
    NI_CI_DEFAULT_START_TRIGGER,
)

from ..Scan_Types import ExecutionPlan, FrameReconstructionPlan, SampleFramePlan, infer_image_axes
from ..Frame_Builder import FrameBuilder
from ..Sample_Scan_Manager import SampleScanManager
from ..PMT_Digital_Manager import PMTDigitalManager
import warnings

DAQ_SAMPLE_RATE_HZ = 500_000.0
DAQ_SAMPLE_PERIOD_S = 1.0 / DAQ_SAMPLE_RATE_HZ
DAQ_SAMPLE_PERIOD_US = DAQ_SAMPLE_PERIOD_S * 1e6

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
except Exception as _exc:
    nidaqmx = None
    AcquisitionType = None
    TerminalConfiguration = None
    WAIT_INFINITELY = None
    AnalogMultiChannelWriter = None
    AnalogMultiChannelReader = None
    _HAS_NIDAQ = False
    # Lazy import, as in _log(), to keep this guard free of import-order concerns.
    from ...widgets.Log_Widget import logger as _logger
    _logger.warning(
        f"[NidaqMicroscope] nidaqmx unavailable ({type(_exc).__name__}: {_exc}). "
        "The nidaq backend cannot acquire; use --backend mock."
    )
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
        self.digital_manager = PMTDigitalManager()
        self.channel_specs = []
        self.channel_kind_map = {}
        self.analog_channels = []
        self.digital_channels = []

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
        from ...widgets.Log_Widget import logger
        logger.debug(f"[NidaqMicroscope] {msg}")

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
            self.scan_parameters.get("laser_off_between_rep", False)
        )

        # Keep the channel names from the UI if present.
        # If none are supplied, fall back to the two physical PMTs.
        self.channels = list(self.scan_parameters.get("active_channels") or ["PMT-Vis", "PMT-IR"])

        self.channel_specs = list(self.scan_parameters.get("detector_channels") or [])

        if not self.channel_specs:
            # No structured specs supplied (the detector widget can legitimately
            # return an empty list): describe the channels ourselves.
            self.channel_specs = []
            for i, ch in enumerate(self.channels):
                self.channel_specs.append({
                    "name": ch,
                    "kind": "analog" if i < 2 else "digital",
                    "enabled": True,
                    "digital_mode": "counts",
                })

        self.channels = [
            str(c.get("name"))
            for c in self.channel_specs
            if bool(c.get("enabled", True))
        ] or ["default"]

        self.channel_kind_map = {
            str(c.get("name")): str(c.get("kind", "analog"))
            for c in self.channel_specs
            if bool(c.get("enabled", True))
        }

        self.analog_channels = [
            ch for ch in self.channels
            if self.channel_kind_map.get(ch, "analog") == "analog"
        ]

        self.digital_channels = [
            ch for ch in self.channels
            if self.channel_kind_map.get(ch, "analog") == "digital"
        ]

        self.digital_manager.configure(
            channel_specs=self.channel_specs,
            scan_parameters=self.scan_parameters,
        )


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

            image_x_axis, image_y_axis = infer_image_axes(fast_axis, slow_axis)
            self.fast_axis_is_image_x = (fast_axis == image_x_axis)
            self.dim_image_x = max(1, int(self.pixel_values[axis_row_map[image_x_axis]]))
            self.dim_image_y = max(1, int(self.pixel_values[axis_row_map[image_y_axis]]))

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

    def _write_ao_idle_offset(self):
        """
        Immediately force the X/Y AO outputs to the voltage matching the
        offsets defined in the ScanWidget.
        If offset = 0 µm the galvos receive 0 V.
        Used at the end of a run so the galvos are not left on the last
        value of the finite waveform.
        """
        self._require_nidaq()

        ao_min, ao_max = self._ao_voltage_limits()

        offsets = self.scan_parameters.get("offsets", {})
        convs   = self.scan_parameters.get("conversion_factors", {})

        conv_x = max(float(convs.get("X-Galvo", 100.0)), 1e-9)
        conv_y = max(float(convs.get("Y-Galvo", 100.0)), 1e-9)

        x_v = float(offsets.get("X-Galvo", 0.0)) / conv_x
        y_v = float(offsets.get("Y-Galvo", 0.0)) / conv_y

        # Clamp dans les limites AO configurées
        x_v = max(ao_min, min(ao_max, x_v))
        y_v = max(ao_min, min(ao_max, y_v))

        with nidaqmx.Task("DL_AO_IdleOffset") as ao_task:
            ao_task.ao_channels.add_ao_voltage_chan(NI_AO_X, min_val=ao_min, max_val=ao_max)
            ao_task.ao_channels.add_ao_voltage_chan(NI_AO_Y, min_val=ao_min, max_val=ao_max)

            # écriture software-timed d'un sample par canal
            ao_task.write([x_v, y_v], auto_start=True)
    
    def _active_ai_count(self) -> int:
        return min(2, len(self.analog_channels))

    def _ordered_analog_channels_for_ni(self) -> list[str]:
        ordered = []

        if "PMT-Vis" in self.analog_channels:
            ordered.append("PMT-Vis")
        if "PMT-IR" in self.analog_channels:
            ordered.append("PMT-IR")

        for ch in self.analog_channels:
            if ch not in ordered:
                ordered.append(ch)

        return ordered
    
    def _map_ai_to_analog_channels(self, ai_data: np.ndarray, n_samples: int) -> dict[str, np.ndarray]:
        out = {}
        n_ai = int(ai_data.shape[0]) if ai_data.ndim == 2 else 1

        ordered_channels = self._ordered_analog_channels_for_ni()

        for ch_idx, ch in enumerate(ordered_channels):
            if ch_idx < n_ai:
                out[ch] = np.asarray(ai_data[ch_idx], dtype=np.float64)
            else:
                out[ch] = np.zeros((n_samples,), dtype=np.float64)

        return out

    def _next_step_event_sample_after(self, sample_cursor: int, frame_stop: int) -> int | None:
        """
        Return the sample_index of the next step event strictly after sample_cursor
        and no later than this frame. None otherwise.
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
            channel_kinds=self.channel_kind_map,
            dwell_time_s=float(plan.dwell_time_s),
        )
        builder.reset(clear_arrays=clear_arrays)

        collected = {ch: [] for ch in self.channels}
        remaining = int(n_samples)
        chunk_samples = max(1, int(chunk_samples))
        sample_cursor = int(sample_start)
        frame_stop = int(sample_start) + int(n_samples)

        digital_started = False
        if self.digital_channels:
            self.digital_manager.start_frame(
                total_gates=int(n_samples),
                sample_clock_source=self.scan_parameters.get("digital_sample_clock_source", NI_CI_DEFAULT_SAMPLE_CLOCK),
                sample_rate_hz=sr,
                start_trigger_source=self.scan_parameters.get("digital_start_trigger_source", NI_CI_DEFAULT_START_TRIGGER),
            )
            digital_started = True

        try:
            if ai_count <= 0:
                with nidaqmx.Task("DL_AO_Frame") as ao_task:
                    self._set_active_tasks(ao_task=ao_task, ai_task=None)
                    try:
                        ao_task.ao_channels.add_ao_voltage_chan(NI_AO_X, min_val=ao_min, max_val=ao_max)
                        ao_task.ao_channels.add_ao_voltage_chan(NI_AO_Y, min_val=ao_min, max_val=ao_max)

                        ao_task.timing.cfg_samp_clk_timing(
                            rate=sr,
                            sample_mode=AcquisitionType.FINITE,
                            samps_per_chan=int(n_samples),
                        )

                        writer = AnalogMultiChannelWriter(ao_task.out_stream, auto_start=False)
                        writer.write_many_sample(ao_block)

                        self._emit_step_events_up_to(int(sample_start))
                        ao_task.start()

                        while remaining > 0 and not self.acquisition_stop_event.is_set():
                            take = min(chunk_samples, remaining)

                            next_step_sample = self._next_step_event_sample_after(
                                sample_cursor=int(sample_cursor),
                                frame_stop=int(frame_stop),
                            )
                            if next_step_sample is not None:
                                take = min(take, max(1, int(next_step_sample) - int(sample_cursor)))

                            mapped = self.digital_manager.read_pixel_chunk(
                                n_samples=int(take),
                                sample_rate_hz=sr,
                            )

                            for ch in self.channels:
                                if ch not in mapped:
                                    mapped[ch] = np.zeros((int(take),), dtype=np.float64)

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
                    ch: np.concatenate(collected[ch]).astype(np.float64, copy=False)
                    if collected[ch] else np.zeros((0,), dtype=np.float64)
                    for ch in self.channels
                }

            with nidaqmx.Task("DL_AO_Frame") as ao_task, nidaqmx.Task("DL_AI_Frame") as ai_task:
                self._set_active_tasks(ao_task=ao_task, ai_task=ai_task)
                try:
                    ao_task.ao_channels.add_ao_voltage_chan(NI_AO_X, min_val=ao_min, max_val=ao_max)
                    ao_task.ao_channels.add_ao_voltage_chan(NI_AO_Y, min_val=ao_min, max_val=ao_max)

                    ordered_analog_channels = self._ordered_analog_channels_for_ni()

                    for ch in ordered_analog_channels:
                        if ch == "PMT-Vis":
                            physical_ai = NI_AI_VIS
                        elif ch == "PMT-IR":
                            physical_ai = NI_AI_IR
                        else:
                            raise ValueError(f"Unknown analog channel mapping for {ch!r}")

                        ai_task.ai_channels.add_ai_voltage_chan(
                            physical_ai,
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

                        read_timeout = max(
                            0.5,
                            (float(take) / float(sr)) * 2.0,
                        )

                        try:
                            reader.read_many_sample(
                                ai_chunk,
                                number_of_samples_per_channel=int(take),
                                timeout=read_timeout,
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

                        mapped = {}

                        analog_mapped = self._map_ai_to_analog_channels(ai_chunk, int(take))
                        mapped.update(analog_mapped)

                        digital_mapped = self.digital_manager.read_pixel_chunk(
                            n_samples=int(take),
                            sample_rate_hz=sr,
                        )
                        mapped.update(digital_mapped)

                        for ch in self.channels:
                            if ch not in mapped:
                                mapped[ch] = np.zeros((int(take),), dtype=np.float64)

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
                ch: np.concatenate(collected[ch]).astype(np.float64, copy=False)
                if collected[ch] else np.zeros((0,), dtype=np.float64)
                for ch in self.channels
            }

        finally:
            if digital_started:
                try:
                    self.digital_manager.stop_frame()
                except Exception as e:
                    self._log(f"stopping the digital frame failed: {e}")

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

        if dwell_s < DAQ_SAMPLE_PERIOD_S - 1e-15:
            raise RuntimeError(
                f"Dwell time too short for fixed DAQ sampling.\n"
                f"Requested dwell = {dwell_s * 1e6:.3f} µs\n"
                f"DAQ sampling = {DAQ_SAMPLE_RATE_HZ / 1e6:.3f} MHz "
                f"({DAQ_SAMPLE_PERIOD_US:.3f} µs/sample)\n"
                f"Minimum dwell time is {DAQ_SAMPLE_PERIOD_US:.3f} µs."
            )

        rate = DAQ_SAMPLE_RATE_HZ
        hw_spp = max(2, int(np.ceil(dwell_s * rate - 1e-12)))
        ai_count = self._active_ai_count()

        values = {}

        if ai_count > 0:
            ai_result = np.zeros((ai_count, hw_spp), dtype=np.float64)

            with nidaqmx.Task("DL_AI_Pixel") as ai_task:
                ordered_analog_channels = self._ordered_analog_channels_for_ni()

                for ch in ordered_analog_channels:
                    if ch == "PMT-Vis":
                        physical_ai = NI_AI_VIS
                    elif ch == "PMT-IR":
                        physical_ai = NI_AI_IR
                    else:
                        raise ValueError(f"Unknown analog channel mapping for {ch!r}")

                    ai_task.ai_channels.add_ai_voltage_chan(
                        physical_ai,
                        min_val=NI_AI_MIN_V,
                        max_val=NI_AI_MAX_V,
                        terminal_config=self._terminal_config(),
                    )

                ai_task.timing.cfg_samp_clk_timing(
                    rate=rate,
                    sample_mode=AcquisitionType.FINITE,
                    samps_per_chan=hw_spp,
                )

                reader = AnalogMultiChannelReader(ai_task.in_stream)
                ai_task.start()
                reader.read_many_sample(
                    ai_result,
                    number_of_samples_per_channel=hw_spp,
                    timeout=max(1.0, dwell_s * 5.0),
                )

            analog_mapped = self._map_ai_to_analog_channels(ai_result, hw_spp)
            for ch in self.analog_channels:
                arr = analog_mapped.get(ch)
                if arr is not None and arr.size > 0:
                    values[ch] = float(np.mean(arr) * dwell_s * 1e6)
                else:
                    values[ch] = 0.0
        else:
            for ch in self.analog_channels:
                values[ch] = 0.0

        if self.digital_channels:
            digital_values = self.digital_manager.acquire_software_timed_counts(
                n_gates=1,
                dwell_time_s=dwell_s,
            )
            for ch in self.digital_channels:
                arr = digital_values.get(ch)
                if arr is not None and arr.size > 0:
                    values[ch] = float(arr[0])
                else:
                    values[ch] = 0.0
        else:
            for ch in self.digital_channels:
                values[ch] = 0.0

        return values

    def _emit_sample_axis_move(self, axis_name: str, target_rel: float, reason: str, t_sched_ms: float):
        self.stepper_move_requested.emit(
            str(axis_name),
            float(target_rel),
            0.0,
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
            self._emit_sample_axis_move(
                frame_plan.axis4_name,
                float(frame_plan.axis4_value),
                "sample_frame_axis4",
                0.0,
            )
        if frame_plan.axis3_name is not None and frame_plan.axis3_value is not None:
            self._emit_sample_axis_move(
                frame_plan.axis3_name,
                float(frame_plan.axis3_value),
                "sample_frame_axis3",
                0.0,
            )

        dwell_s = max(1e-4, float(self.sample_scan_manager.dwell_time_s))
        settle_s = max(0.0, float(self.sample_scan_manager.sample_settle_time_s))
        spp = max(1, int(self.sample_scan_manager.samples_per_pixel))

        # Nombre de samples hardware par pixel (déterminé par le dwell time et la fréquence DAQ)
        hw_spp = max(2, int(np.ceil(dwell_s * DAQ_SAMPLE_RATE_HZ - 1e-12)))
        ai_count = self._active_ai_count()
        read_timeout = max(1.0, dwell_s * 5.0)

        progress_accum = 0
        flush_accum = 0
        flush_chunk = 16
        pixel_done = 0

        channels = self.channels
        emit_status = self.sample_status_updated.emit
        emit_progress = self.samples_progress.emit
        emit_flush = self.sample_image_flush_requested.emit
        stop_event = self.acquisition_stop_event
        iter_events = self.sample_scan_manager.iter_pixel_events

        pm = getattr(self, "positioner_manager", None)
        move_xy_blocking = getattr(pm, "move_xy_to_rel_blocking", None) if pm is not None else None

        speed_x = 1.0
        speed_y = 1.0
        if callable(move_xy_blocking):
            try:
                speed_x = max(0.01, float(pm.get_max_speed("x")))
            except Exception as e:
                # Keeps the previously chosen speed rather than the stage's own.
                self._log(f"max speed X unavailable, keeping {speed_x}: {e}")
            try:
                speed_y = max(0.01, float(pm.get_max_speed("y")))
            except Exception as e:
                self._log(f"max speed Y unavailable, keeping {speed_y}: {e}")

        fallback_wait_xy = False
        if pm is not None and not callable(move_xy_blocking):
            fallback_wait_xy = True

        # --- Pré-créer UNE tâche AI pour toute la frame ---
        # Évite ~15-50 ms d'overhead NI-DAQ par pixel (création/destruction de tâche).
        # On utilise le pattern start/stop sur la même tâche configurée.
        ai_task = None
        ai_reader = None
        ai_result_buf = None

        if _HAS_NIDAQ and ai_count > 0:
            try:
                ai_task = nidaqmx.Task("DL_AI_SampleFrame")
                for ch in self._ordered_analog_channels_for_ni():
                    if ch == "PMT-Vis":
                        physical_ai = NI_AI_VIS
                    elif ch == "PMT-IR":
                        physical_ai = NI_AI_IR
                    else:
                        raise ValueError(f"Unknown analog channel mapping for {ch!r}")
                    ai_task.ai_channels.add_ai_voltage_chan(
                        physical_ai,
                        min_val=NI_AI_MIN_V,
                        max_val=NI_AI_MAX_V,
                        terminal_config=self._terminal_config(),
                    )
                ai_task.timing.cfg_samp_clk_timing(
                    rate=DAQ_SAMPLE_RATE_HZ,
                    sample_mode=AcquisitionType.FINITE,
                    samps_per_chan=hw_spp,
                )
                ai_reader = AnalogMultiChannelReader(ai_task.in_stream)
                ai_result_buf = np.zeros((ai_count, hw_spp), dtype=np.float64)
            except Exception as e:
                self._log(f"AI sample task pre-creation failed: {e} — falling back to per-pixel")
                if ai_task is not None:
                    try:
                        ai_task.close()
                    except Exception as e:
                        self._log(f"closing the pre-created AI task failed: {e}")
                ai_task = None
                ai_reader = None
                ai_result_buf = None

        try:
            for event in iter_events():
                if stop_event.is_set():
                    break

                x_target = float(event.x_target_rel_um)
                y_target = float(event.y_target_rel_um)
                iy = int(event.iy)
                ix = int(event.ix)

                # Pré-positionnement backlash : déplacer sans acquérir
                if event.is_backlash:
                    if callable(move_xy_blocking):
                        move_xy_blocking(x_target, y_target, float(speed_x), float(speed_y), timeout_s=30.0)
                    else:
                        self._emit_sample_axis_move(event.x_axis_name, x_target, "sample_pixel_x", 0.0)
                        self._emit_sample_axis_move(event.y_axis_name, y_target, "sample_pixel_y", 0.0)
                    continue

                t_sched_ms = ((dwell_s + settle_s) * float(pixel_done)) * 1e3

                emit_status({
                    "mode": "sample",
                    "line_index": iy,
                    "line_count": int(ny),
                    "x_index_start": ix,
                    "x_index_stop": ix,
                    "x_um": x_target,
                    "y_um": y_target,
                    "scheduled_ms": float(t_sched_ms),
                })

                if callable(move_xy_blocking):
                    move_xy_blocking(
                        x_target,
                        y_target,
                        float(speed_x),
                        float(speed_y),
                        timeout_s=max(5.0, dwell_s + settle_s + 5.0),
                    )
                else:
                    self._emit_sample_axis_move(
                        event.x_axis_name,
                        x_target,
                        "sample_pixel_x",
                        float(t_sched_ms),
                    )
                    self._emit_sample_axis_move(
                        event.y_axis_name,
                        y_target,
                        "sample_pixel_y",
                        float(t_sched_ms),
                    )

                    if fallback_wait_xy:
                        axis_x = pm.axis_from_scan_name(event.x_axis_name)
                        axis_y = pm.axis_from_scan_name(event.y_axis_name)

                        tol_x = pm.get_tolerance(axis_x)
                        tol_y = pm.get_tolerance(axis_y)

                        while True:
                            cur_x = pm.get_rel_pos(axis_x)
                            cur_y = pm.get_rel_pos(axis_y)

                            if (
                                abs(cur_x - x_target) <= tol_x
                                and abs(cur_y - y_target) <= tol_y
                            ):
                                break

                            if stop_event.is_set():
                                break

                            time.sleep(0.001)

                if settle_s > 0:
                    time.sleep(settle_s)

                # --- Acquisition pixel ---
                pixel_values = {}

                if ai_task is not None:
                    # Tâche pré-créée : start/stop sans recréer (économise ~20-50 ms/pixel)
                    ai_result_buf.fill(0.0)
                    try:
                        ai_task.start()
                        ai_reader.read_many_sample(
                            ai_result_buf,
                            number_of_samples_per_channel=hw_spp,
                            timeout=read_timeout,
                        )
                        ai_task.stop()
                    except DaqWarning as w:
                        try:
                            ai_task.stop()
                        except Exception:
                            pass
                        if not (stop_event.is_set() and getattr(w, "error_code", None) == 200010):
                            raise
                    except DaqError:
                        try:
                            ai_task.stop()
                        except Exception:
                            pass
                        if not stop_event.is_set():
                            raise
                        break

                    analog_mapped = self._map_ai_to_analog_channels(ai_result_buf, hw_spp)
                    for ch in self.analog_channels:
                        arr = analog_mapped.get(ch)
                        pixel_values[ch] = float(np.mean(arr) * dwell_s * 1e6) if arr is not None and arr.size > 0 else 0.0

                elif ai_count > 0:
                    # Fallback : la pré-création a échoué, on recrée par pixel
                    fallback = self._read_single_pixel_from_ai(dwell_s=dwell_s, samples_per_pixel=spp)
                    pixel_values.update(fallback)

                if self.digital_channels:
                    digital_values = self.digital_manager.acquire_software_timed_counts(
                        n_gates=1,
                        dwell_time_s=dwell_s,
                    )
                    for ch in self.digital_channels:
                        arr = digital_values.get(ch)
                        pixel_values[ch] = float(arr[0]) if arr is not None and arr.size > 0 else 0.0

                for ch in channels:
                    arrays[ch][iy, ix] = float(pixel_values.get(ch, 0.0))

                progress_accum += spp
                flush_accum += 1
                pixel_done += 1

                if progress_accum > 0:
                    emit_progress(int(progress_accum))
                    progress_accum = 0

                if flush_accum >= flush_chunk:
                    emit_flush()
                    flush_accum = 0

        finally:
            if ai_task is not None:
                # Once per frame, not per pixel: safe to report.
                try:
                    ai_task.stop()
                except Exception as e:
                    self._log(f"stopping the AI task failed: {e}")
                try:
                    ai_task.close()
                except Exception as e:
                    self._log(f"closing the AI task failed: {e}")

        emit_flush()
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
                    self._write_ao_idle_offset()
                except Exception as e:
                    self._log(f"AO idle offset failed after single: {e}")

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
                # 200010: finite acquisition stopped early -- expected here.
                if getattr(w, "error_code", None) != 200010:
                    raise
            except DaqError as e:
                self._log(f"stopping a DAQ task failed: {e}")
            except Exception as e:
                self._log(f"unexpected error stopping a DAQ task: {e}")