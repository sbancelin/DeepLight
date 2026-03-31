from __future__ import annotations

import time
from dataclasses import dataclass

import numpy as np
from PySide6.QtCore import QObject

from .Hardware_Manager import (
    NI_DEVICE_NAME,
    NI_CI_DEFAULT_COUNTER,
    NI_CI_DEFAULT_SOURCE,
    NI_CI_DEFAULT_SAMPLE_CLOCK,
    NI_CI_DEFAULT_START_TRIGGER,
)

try:
    import nidaqmx
    from nidaqmx.constants import AcquisitionType, Edge, TriggerType
    from nidaqmx.stream_readers import CounterReader
    from nidaqmx.errors import DaqError, DaqWarning
    _HAS_NIDAQ = True
except Exception:
    nidaqmx = None
    AcquisitionType = None
    Edge = None
    TriggerType = None
    CounterReader = None
    DaqError = Exception
    DaqWarning = Warning
    _HAS_NIDAQ = False


@dataclass
class _DigitalChannelRuntime:
    name: str
    counter: str
    source_terminal: str
    edge: object
    mode: str
    sample_clock_source: str | None = None
    start_trigger_source: str | None = None
    task: object | None = None
    reader: object | None = None
    prev_count: int = 0
    frame_running: bool = False


class PMTDigitalManager(QObject):
    """
    NI counter-based photon-counting manager for DeepLight.

    Contract kept intentionally close to the previous C8855 manager:
    - configure(channel_specs, scan_parameters)
    - start_frame(...)
    - read_pixel_chunk(...)
    - stop_frame()
    - acquire_software_timed_counts(...)

    Notes:
    - Each enabled digital channel may use its own NI counter + PFI source.
    - Laser scan mode uses a sampled counter task clocked from the AO sample clock.
    - Sample scan mode uses a simple software-timed count-difference measurement.
    """

    def __init__(self, parent=None):
        super().__init__(parent)

        self.channel_specs = []
        self.enabled_channels: list[str] = []
        self.digital_mode_by_channel: dict[str, str] = {}
        self.digital_counter_by_channel: dict[str, str] = {}
        self.digital_source_by_channel: dict[str, str] = {}
        self.digital_edge_by_channel: dict[str, str] = {}

        self.samples_per_pixel = 1
        self.requested_dwell_time_s = 0.0

        self.primary_channel: str | None = None
        self.extra_channels: list[str] = []

        self.sample_clock_source = NI_CI_DEFAULT_SAMPLE_CLOCK
        self.start_trigger_source = NI_CI_DEFAULT_START_TRIGGER

        self._frame_expected_samples = 0
        self._frame_samples_read = 0
        self._channel_runtime: dict[str, _DigitalChannelRuntime] = {}

    def _log(self, msg: str):
        print(f"[PMTDigitalManager] {msg}")

    def _require_nidaq(self):
        if not _HAS_NIDAQ:
            raise RuntimeError(
                "nidaqmx is not installed or NI-DAQmx is unavailable. "
                "Install nidaqmx and the NI-DAQmx driver."
            )

    def _normalize_terminal(self, term: str | None, *, default: str) -> str:
        value = str(term or default).strip()
        if not value:
            value = default

        if value.startswith("/"):
            return value

        if "/" in value:
            return f"/{value}"

        return f"/{NI_DEVICE_NAME}/{value}"

    def _normalize_counter(self, counter: str | None, *, default: str) -> str:
        value = str(counter or default).strip()
        if not value:
            value = default

        if "/" in value:
            return value

        return f"{NI_DEVICE_NAME}/{value}"

    def _resolve_edge(self, edge_name: str | None):
        name = str(edge_name or "rising").strip().lower()
        return Edge.FALLING if name.startswith("fall") else Edge.RISING

    def _as_count_output(self, counts: np.ndarray, mode: str, sample_rate_hz: float) -> np.ndarray:
        out = np.asarray(counts, dtype=np.float64)
        if str(mode) == "count_rate":
            sr = max(0.0, float(sample_rate_hz))
            return out * sr
        return out

    def _zero_output(self, n: int, sample_rate_hz: float) -> dict[str, np.ndarray]:
        zeros = {}
        for ch in self.enabled_channels:
            arr = np.zeros((int(n),), dtype=np.float64)
            zeros[ch] = self._as_count_output(arr, self.digital_mode_by_channel.get(ch, "counts"), sample_rate_hz)
        return zeros

    def close(self):
        self.stop_frame()

    def configure(self, channel_specs: list[dict] | None, scan_parameters: dict | None):
        self.channel_specs = list(channel_specs or [])

        sp = dict(scan_parameters or {})
        self.samples_per_pixel = max(1, int(sp.get("samples_per_pixel", 1) or 1))
        self.requested_dwell_time_s = float(sp.get("dwell_time", 0.0) or 0.0)

        self.sample_clock_source = self._normalize_terminal(
            sp.get("digital_sample_clock_source", NI_CI_DEFAULT_SAMPLE_CLOCK),
            default=NI_CI_DEFAULT_SAMPLE_CLOCK,
        )
        self.start_trigger_source = self._normalize_terminal(
            sp.get("digital_start_trigger_source", NI_CI_DEFAULT_START_TRIGGER),
            default=NI_CI_DEFAULT_START_TRIGGER,
        )

        enabled_digital_specs = [
            dict(c)
            for c in self.channel_specs
            if bool(c.get("enabled", True)) and str(c.get("kind", "analog")) == "digital"
        ]

        self.enabled_channels = [str(c.get("name")) for c in enabled_digital_specs]
        self.digital_mode_by_channel = {
            str(c.get("name")): str(c.get("digital_mode", "counts"))
            for c in enabled_digital_specs
        }
        self.digital_counter_by_channel = {
            str(c.get("name")): self._normalize_counter(c.get("digital_counter", NI_CI_DEFAULT_COUNTER), default=NI_CI_DEFAULT_COUNTER)
            for c in enabled_digital_specs
        }
        self.digital_source_by_channel = {
            str(c.get("name")): self._normalize_terminal(c.get("digital_source", NI_CI_DEFAULT_SOURCE), default=NI_CI_DEFAULT_SOURCE)
            for c in enabled_digital_specs
        }
        self.digital_edge_by_channel = {
            str(c.get("name")): str(c.get("digital_edge", "rising"))
            for c in enabled_digital_specs
        }

        self.primary_channel = self.enabled_channels[0] if self.enabled_channels else None
        self.extra_channels = self.enabled_channels[1:] if len(self.enabled_channels) > 1 else []

        self._channel_runtime = {}
        for ch in self.enabled_channels:
            self._channel_runtime[ch] = _DigitalChannelRuntime(
                name=ch,
                counter=self.digital_counter_by_channel[ch],
                source_terminal=self.digital_source_by_channel[ch],
                edge=self._resolve_edge(self.digital_edge_by_channel.get(ch)),
                mode=self.digital_mode_by_channel.get(ch, "counts"),
                sample_clock_source=self.sample_clock_source,
                start_trigger_source=self.start_trigger_source,
            )

        if self.enabled_channels:
            self._log(
                "Configured digital channels: " + ", ".join(
                    f"{ch}[counter={self.digital_counter_by_channel[ch]}, source={self.digital_source_by_channel[ch]}, mode={self.digital_mode_by_channel[ch]}]"
                    for ch in self.enabled_channels
                )
            )

    def start_frame(
        self,
        total_gates: int,
        *,
        sample_clock_source: str | None = None,
        sample_rate_hz: float | None = None,
        start_trigger_source: str | None = None,
    ):
        if not self.enabled_channels:
            return

        self._require_nidaq()
        self.stop_frame()

        total_samples = max(0, int(total_gates))
        if total_samples <= 0:
            raise ValueError("total_gates must be > 0.")

        clock_source = self._normalize_terminal(sample_clock_source, default=self.sample_clock_source)
        trigger_source = self._normalize_terminal(start_trigger_source, default=self.start_trigger_source)
        sr = None if sample_rate_hz is None else float(sample_rate_hz)

        self._frame_expected_samples = total_samples
        self._frame_samples_read = 0

        for ch, runtime in self._channel_runtime.items():
            task = nidaqmx.Task(f"DL_CI_{ch}")
            task.ci_channels.add_ci_count_edges_chan(
                runtime.counter,
                edge=runtime.edge,
                initial_count=0,
            )

            chan = task.ci_channels[0]
            chan.ci_count_edges_term = runtime.source_terminal

            task.timing.cfg_samp_clk_timing(
                rate=max(1.0, sr or 1.0),
                source=clock_source,
                sample_mode=AcquisitionType.FINITE,
                samps_per_chan=total_samples,
            )

            try:
                task.triggers.start_trigger.cfg_dig_edge_start_trig(trigger_source)
            except Exception:
                pass

            runtime.task = task
            runtime.reader = CounterReader(task.in_stream)
            runtime.prev_count = 0
            runtime.frame_running = True
            runtime.sample_clock_source = clock_source
            runtime.start_trigger_source = trigger_source
            task.start()

        self._log(
            f"Frame started total_samples={total_samples}, sample_clock={clock_source}, trigger={trigger_source}"
        )

    def read_pixel_chunk(self, n_samples: int, sample_rate_hz: float) -> dict[str, np.ndarray]:
        if not self.enabled_channels:
            return {}

        need = max(0, int(n_samples))
        if need == 0:
            return self._zero_output(0, sample_rate_hz)

        if not self._channel_runtime or not any(rt.frame_running for rt in self._channel_runtime.values()):
            return self._zero_output(need, sample_rate_hz)

        remaining_total = max(0, int(self._frame_expected_samples) - int(self._frame_samples_read))
        take = min(need, remaining_total)
        out = self._zero_output(need, sample_rate_hz)

        if take <= 0:
            return out

        for ch, runtime in self._channel_runtime.items():
            if not runtime.frame_running or runtime.reader is None:
                continue

            cumulative = np.zeros((take,), dtype=np.uint32)
            try:
                runtime.reader.read_many_sample_uint32(
                    cumulative,
                    number_of_samples_per_channel=take,
                    timeout=max(1.0, float(take) / max(1.0, float(sample_rate_hz)) * 5.0 + 0.1),
                )
            except Exception as e:
                raise RuntimeError(f"Counter read failed for {ch}: {e}") from e

            diff = cumulative.astype(np.int64)
            if diff.size > 0:
                diff[1:] -= cumulative[:-1].astype(np.int64)
                diff[0] -= int(runtime.prev_count)
                runtime.prev_count = int(cumulative[-1])

            diff = np.clip(diff, 0, None).astype(np.float64, copy=False)
            out[ch][:take] = self._as_count_output(diff, runtime.mode, sample_rate_hz)

        self._frame_samples_read += take
        return out

    def stop_frame(self):
        for runtime in self._channel_runtime.values():
            task = runtime.task
            if task is not None:
                try:
                    task.stop()
                except Exception:
                    pass
                try:
                    task.close()
                except Exception:
                    pass
            runtime.task = None
            runtime.reader = None
            runtime.prev_count = 0
            runtime.frame_running = False

        self._frame_expected_samples = 0
        self._frame_samples_read = 0

    def acquire_software_timed_counts(self, n_gates: int, dwell_time_s: float) -> dict[str, np.ndarray]:
        if not self.enabled_channels:
            return {}

        self._require_nidaq()

        n_gates = max(0, int(n_gates))
        if n_gates == 0:
            return self._zero_output(0, 0.0)

        dwell_time_s = max(0.0, float(dwell_time_s))
        per_gate_s = dwell_time_s / float(max(1, n_gates)) if dwell_time_s > 0 else 0.0

        out = {}
        for ch, runtime in self._channel_runtime.items():
            parts = np.zeros((n_gates,), dtype=np.float64)

            with nidaqmx.Task(f"DL_CI_Single_{ch}") as task:
                task.ci_channels.add_ci_count_edges_chan(
                    runtime.counter,
                    edge=runtime.edge,
                    initial_count=0,
                )
                chan = task.ci_channels[0]
                chan.ci_count_edges_term = runtime.source_terminal
                task.start()

                prev = int(task.read())
                for i in range(n_gates):
                    if per_gate_s > 0:
                        time.sleep(per_gate_s)
                    cur = int(task.read())
                    parts[i] = max(0, cur - prev)
                    prev = cur

            out[ch] = self._as_count_output(parts, runtime.mode, 1.0 / per_gate_s if per_gate_s > 0 else 0.0)

        return out

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass
