from __future__ import annotations

import ctypes as ct
import os
import time
from pathlib import Path

import numpy as np
from PySide6.QtCore import QObject


class PMTDigitalManager(QObject):
    """
    Hamamatsu C8855-01 manager for DeepLight.

    V1 scope:
    - one physical C8855 used
    - one active digital channel really connected
    - samples_per_pixel must be 1
    - 1 pixel = 1 gate = 1 raw count
    - streaming read in repeated C8855ReadData() calls
    """

    C8855_GATETIME_50US = 0x02
    C8855_GATETIME_100US = 0x03
    C8855_GATETIME_200US = 0x04
    C8855_GATETIME_500US = 0x05
    C8855_GATETIME_1MS = 0x06
    C8855_GATETIME_2MS = 0x07
    C8855_GATETIME_5MS = 0x08
    C8855_GATETIME_10MS = 0x09
    C8855_GATETIME_20MS = 0x0A
    C8855_GATETIME_50MS = 0x0B
    C8855_GATETIME_100MS = 0x0C
    C8855_GATETIME_200MS = 0x0D
    C8855_GATETIME_500MS = 0x0E
    C8855_GATETIME_1S = 0x0F
    C8855_GATETIME_2S = 0x10
    C8855_GATETIME_5S = 0x11
    C8855_GATETIME_10S = 0x12

    C8855_SOFTWARE_TRIGGER = 0
    C8855_EXTERNAL_TRIGGER = 1

    C8855_BLOCK_TRANSFER = 2

    C8855_PMT_POWER_OFF = 0
    C8855_PMT_POWER_ON = 1

    C8855_ERROR_TRANSFER = 0xFF

    C8855_SET_FALL_EDGE = 0
    C8855_SET_RISE_EDGE = 1

    # Manual/API: NumberOfGate is 1..1024 per transfer chunk.
    MAX_TRANSFER_GATES = 1024

    _GATE_TABLE = [
        (50e-6, C8855_GATETIME_50US),
        (100e-6, C8855_GATETIME_100US),
        (200e-6, C8855_GATETIME_200US),
        (500e-6, C8855_GATETIME_500US),
        (1e-3, C8855_GATETIME_1MS),
        (2e-3, C8855_GATETIME_2MS),
        (5e-3, C8855_GATETIME_5MS),
        (10e-3, C8855_GATETIME_10MS),
        (20e-3, C8855_GATETIME_20MS),
        (50e-3, C8855_GATETIME_50MS),
        (100e-3, C8855_GATETIME_100MS),
        (200e-3, C8855_GATETIME_200MS),
        (500e-3, C8855_GATETIME_500MS),
        (1.0, C8855_GATETIME_1S),
        (2.0, C8855_GATETIME_2S),
        (5.0, C8855_GATETIME_5S),
        (10.0, C8855_GATETIME_10S),
    ]

    def __init__(self, parent=None):
        super().__init__(parent)

        self.channel_specs = []
        self.enabled_channels: list[str] = []
        self.digital_mode_by_channel: dict[str, str] = {}

        self.samples_per_pixel = 1
        self.requested_dwell_time_s = 0.0

        self.primary_channel: str | None = None
        self.extra_channels: list[str] = []

        self._dll = None
        self._handle = None
        self._dll_dir_handle = None

        self._gate_code: int | None = None
        self._gate_time_s: float = 0.0

        self._frame_running = False
        self._frame_expected_gates = 0
        self._frame_gates_read = 0

        self._transfer_gates = self.MAX_TRANSFER_GATES
        self._pending_counts = np.zeros((0,), dtype=np.float64)

    # ------------------------------------------------------------------
    # Logging
    # ------------------------------------------------------------------

    def _log(self, msg: str):
        print(f"[PMTDigitalManager] {msg}")

    # ------------------------------------------------------------------
    # Config helpers
    # ------------------------------------------------------------------

    def _resolve_gate_code(self, dwell_time_s: float) -> tuple[int, float]:
        dwell_time_s = float(dwell_time_s)

        for gate_s, gate_code in self._GATE_TABLE:
            if abs(dwell_time_s - gate_s) <= max(1e-9, gate_s * 1e-6):
                return gate_code, gate_s

        raise ValueError(
            "C8855 requires a dwell time equal to a native gate time: "
            "50, 100, 200, 500 µs; 1, 2, 5, 10, 20, 50, 100, 200, 500 ms; 1, 2, 5, 10 s."
        )

    def _require_supported_config(self):
        if not self.enabled_channels:
            return

        if int(self.samples_per_pixel) != 1:
            raise ValueError("Photon counting V1 requires samples_per_pixel == 1.")

        gate_code, gate_time_s = self._resolve_gate_code(self.requested_dwell_time_s)
        self._gate_code = gate_code
        self._gate_time_s = gate_time_s

    def _recommended_transfer_gates(self) -> int:
        gt = float(self._gate_time_s)

        # Manual: for short gate times, NumberOfGate should be large enough
        # otherwise USB transfer cannot keep pace and C8855_ERROR_TRANSFER occurs.
        if gt <= 1e-3:
            return 1024
        if gt <= 10e-3:
            return 512
        if gt <= 100e-3:
            return 128
        if gt <= 1.0:
            return 32
        return 8

    def _candidate_dll_paths(self) -> list[Path]:
        module_dir = Path(__file__).resolve().parent
        gui_dir = module_dir.parent
        dll_dir = gui_dir / "DLLs"

        candidates = [
            dll_dir / "C8855-01api.dll",
            module_dir / "C8855-01api.dll",
            Path.cwd() / "C8855-01api.dll",
        ]

        env_path = os.environ.get("DEEPLIGHT_C8855_DLL", "").strip()
        if env_path:
            candidates.insert(0, Path(env_path))

        uniq = []
        seen = set()
        for p in candidates:
            s = str(p)
            if s not in seen:
                uniq.append(p)
                seen.add(s)
        return uniq

    # ------------------------------------------------------------------
    # DLL / device open
    # ------------------------------------------------------------------

    def _load_dll(self):
        if self._dll is not None:
            return

        dll_path = None
        for p in self._candidate_dll_paths():
            if p.exists():
                dll_path = p
                break

        if dll_path is None:
            raise FileNotFoundError("C8855-01api.dll not found.")

        if os.name == "nt" and hasattr(os, "add_dll_directory"):
            self._dll_dir_handle = os.add_dll_directory(str(dll_path.parent))

        dll = ct.WinDLL(str(dll_path))

        HANDLE = ct.c_void_p
        BYTE = ct.c_ubyte
        WORD = ct.c_ushort
        DWORD = ct.c_uint32
        BOOL = ct.c_int

        dll.C8855Open.argtypes = []
        dll.C8855Open.restype = HANDLE

        dll.C8855Close.argtypes = [HANDLE]
        dll.C8855Close.restype = BOOL

        dll.C8855Reset.argtypes = [HANDLE]
        dll.C8855Reset.restype = BOOL

        dll.C8855CountStart.argtypes = [HANDLE, BYTE]
        dll.C8855CountStart.restype = BOOL

        dll.C8855CountStop.argtypes = [HANDLE]
        dll.C8855CountStop.restype = BOOL

        dll.C8855Setup.argtypes = [HANDLE, BYTE, BYTE, WORD]
        dll.C8855Setup.restype = BOOL

        dll.C8855SetupEx.argtypes = [HANDLE, BYTE, BYTE, WORD, BYTE]
        dll.C8855SetupEx.restype = BOOL

        dll.C8855ReadData.argtypes = [HANDLE, ct.POINTER(DWORD), ct.POINTER(BYTE)]
        dll.C8855ReadData.restype = BOOL

        dll.C8855SetPmtPower.argtypes = [HANDLE, BYTE]
        dll.C8855SetPmtPower.restype = BOOL

        dll.C8855ReadId.argtypes = [HANDLE, ct.POINTER(BYTE)]
        dll.C8855ReadId.restype = BOOL

        self._dll = dll
        self._log(f"Loaded {dll_path}")

    def _open_if_needed(self):
        if self._handle:
            return

        self._load_dll()
        handle = self._dll.C8855Open()
        if not handle:
            raise RuntimeError("C8855Open failed.")

        self._handle = handle

        try:
            self._dll.C8855SetPmtPower(self._handle, self.C8855_PMT_POWER_ON)
        except Exception:
            pass

        unit_id = self.read_unit_id()
        self._log(f"C8855 opened, ID={unit_id}")

    def read_unit_id(self) -> int | None:
        if self._dll is None or self._handle is None:
            return None

        BYTE = ct.c_ubyte
        data = BYTE(0)

        ok = bool(self._dll.C8855ReadId(self._handle, ct.byref(data)))
        if not ok:
            return None

        return int(data.value)
    
    def close(self):
        self.stop_frame()

        if self._handle and self._dll is not None:
            try:
                self._dll.C8855Close(self._handle)
            except Exception:
                pass

        self._handle = None
        self._dll = None

        try:
            if self._dll_dir_handle is not None:
                self._dll_dir_handle.close()
        except Exception:
            pass
        self._dll_dir_handle = None

    # ------------------------------------------------------------------
    # Channel mapping
    # ------------------------------------------------------------------

    def _map_single_stream_to_channels(self, counts: np.ndarray) -> dict[str, np.ndarray]:
        counts = np.asarray(counts, dtype=np.float64)
        out = {}

        if self.primary_channel is not None:
            out[self.primary_channel] = counts.copy()

        for ch in self.extra_channels:
            out[ch] = np.zeros_like(counts, dtype=np.float64)

        return out

    def _zeros_for_enabled(self, n: int) -> dict[str, np.ndarray]:
        return {
            ch: np.zeros((int(n),), dtype=np.float64)
            for ch in self.enabled_channels
        }

    # ------------------------------------------------------------------
    # Public config
    # ------------------------------------------------------------------

    def configure(self, channel_specs: list[dict] | None, scan_parameters: dict | None):
        self.channel_specs = list(channel_specs or [])

        self.enabled_channels = [
            str(c.get("name"))
            for c in self.channel_specs
            if bool(c.get("enabled", True)) and str(c.get("kind", "analog")) == "digital"
        ]

        self.digital_mode_by_channel = {
            str(c.get("name")): str(c.get("digital_mode", "counts"))
            for c in self.channel_specs
            if bool(c.get("enabled", True)) and str(c.get("kind", "analog")) == "digital"
        }

        sp = dict(scan_parameters or {})
        self.samples_per_pixel = max(1, int(sp.get("samples_per_pixel", 1) or 1))
        self.requested_dwell_time_s = float(sp.get("dwell_time", 0.0) or 0.0)

        self.primary_channel = self.enabled_channels[0] if self.enabled_channels else None
        self.extra_channels = self.enabled_channels[1:] if len(self.enabled_channels) > 1 else []

        if self.extra_channels:
            self._log(
                f"V1: only one physical C8855 is used. "
                f"Primary={self.primary_channel}, forced-to-zero={self.extra_channels}"
            )

        if self.enabled_channels:
            self._require_supported_config()
            self._transfer_gates = self._recommended_transfer_gates()

    # ------------------------------------------------------------------
    # Low-level transfer
    # ------------------------------------------------------------------

    def _setup_transfer(self, transfer_gates: int):
        if not self._dll.C8855Reset(self._handle):
            raise RuntimeError("C8855Reset failed.")

        # V1: software trigger only -> use the simpler Hamamatsu API
        ok = self._dll.C8855Setup(
            self._handle,
            int(self._gate_code),
            self.C8855_BLOCK_TRANSFER,
            int(transfer_gates),
        )
        if not ok:
            raise RuntimeError(
                f"C8855Setup failed for transfer_gates={transfer_gates}, "
                f"gate_time={self._gate_time_s * 1e6:.1f} µs."
            )

    def _count_start(self):
        ok = self._dll.C8855CountStart(self._handle, self.C8855_SOFTWARE_TRIGGER)
        if not ok:
            raise RuntimeError("C8855CountStart failed.")

    def _count_stop(self):
        try:
            self._dll.C8855CountStop(self._handle)
        except Exception:
            pass

    def _read_one_transfer(self, transfer_gates: int) -> np.ndarray:
        DWORD = ct.c_uint32
        BYTE = ct.c_ubyte

        transfer_gates = int(transfer_gates)
        buf = (DWORD * transfer_gates)()
        result = BYTE(0)

        # ReadData is synchronous according to the manual.
        timeout_s = max(1.0, float(transfer_gates) * max(self._gate_time_s, 1e-6) * 4.0 + 0.1)
        t0 = time.perf_counter()

        while True:
            ok = bool(self._dll.C8855ReadData(self._handle, buf, ct.byref(result)))
            if ok:
                if int(result.value) == self.C8855_ERROR_TRANSFER:
                    raise RuntimeError(
                        f"C8855ReadData reported transfer error "
                        f"(transfer_gates={transfer_gates}, gate_time={self._gate_time_s * 1e6:.1f} µs)."
                    )

                return np.ctypeslib.as_array(buf).astype(np.float64, copy=True)

            if time.perf_counter() - t0 > timeout_s:
                raise TimeoutError(
                    f"C8855ReadData timeout for transfer_gates={transfer_gates}, "
                    f"gate_time={self._gate_time_s * 1e6:.1f} µs."
                )

            time.sleep(min(0.002, max(0.0001, self._gate_time_s * 0.25)))

    # ------------------------------------------------------------------
    # Frame streaming API
    # ------------------------------------------------------------------

    def start_frame(self, total_gates: int):
        if not self.enabled_channels:
            return

        self._require_supported_config()
        self._open_if_needed()

        total_gates = int(total_gates)
        if total_gates <= 0:
            raise ValueError("total_gates must be > 0.")

        self._frame_expected_gates = total_gates
        self._frame_gates_read = 0
        self._pending_counts = np.zeros((0,), dtype=np.float64)
        self._frame_running = True

        transfer_gates = int(self._transfer_gates)

        self._setup_transfer(transfer_gates)
        self._count_start()

        self._log(
            f"Frame started total_gates={total_gates}, "
            f"gate={self._gate_time_s * 1e6:.1f} µs, "
            f"transfer_gates={transfer_gates}"
        )

    def read_pixel_chunk(self, n_samples: int, sample_rate_hz: float) -> dict[str, np.ndarray]:
        if not self.enabled_channels:
            return {}

        if not self._frame_running:
            return self._zeros_for_enabled(n_samples)

        need = max(0, int(n_samples))
        if need == 0:
            return self._zeros_for_enabled(0)

        out_parts = []

        while need > 0:
            # d'abord consommer ce qui est déjà en buffer local
            if self._pending_counts.size > 0:
                take = min(need, int(self._pending_counts.size))
                out_parts.append(self._pending_counts[:take])
                self._pending_counts = self._pending_counts[take:]
                self._frame_gates_read += int(take)
                need -= int(take)
                continue

            remaining_total = int(self._frame_expected_gates) - int(self._frame_gates_read)
            if remaining_total <= 0:
                if out_parts:
                    out = np.concatenate(out_parts).astype(np.float64, copy=False)
                else:
                    out = np.zeros((0,), dtype=np.float64)

                if out.size < int(n_samples):
                    pad = np.zeros((int(n_samples) - int(out.size),), dtype=np.float64)
                    out = np.concatenate([out, pad])

                return self._map_single_stream_to_channels(out)

            # IMPORTANT:
            # le C8855 a déjà été configuré et démarré dans start_frame()
            # ici on ne fait plus que lire les paquets successifs
            block = self._read_one_transfer(int(self._transfer_gates))
            self._pending_counts = block

        out = np.concatenate(out_parts).astype(np.float64, copy=False)
        return self._map_single_stream_to_channels(out)

    def stop_frame(self):
        if self._frame_running:
            self._count_stop()

        self._frame_running = False
        self._frame_expected_gates = 0
        self._frame_gates_read = 0
        self._pending_counts = np.zeros((0,), dtype=np.float64)

    # ------------------------------------------------------------------
    # Sample-scan one-shot helper
    # ------------------------------------------------------------------

    def acquire_software_timed_counts(self, n_gates: int, dwell_time_s: float) -> dict[str, np.ndarray]:
        if not self.enabled_channels:
            return {}

        if self._frame_running:
            raise RuntimeError("Cannot acquire one-shot counts while frame is running.")

        n_gates = max(0, int(n_gates))
        if n_gates == 0:
            return self._zeros_for_enabled(0)

        gate_code, gate_time_s = self._resolve_gate_code(float(dwell_time_s))

        self._open_if_needed()

        parts = []
        remaining = n_gates

        while remaining > 0:
            take = min(self.MAX_TRANSFER_GATES, remaining)

            if not self._dll.C8855Reset(self._handle):
                raise RuntimeError("C8855Reset failed.")

            ok = self._dll.C8855SetupEx(
                self._handle,
                int(gate_code),
                self.C8855_BLOCK_TRANSFER,
                int(take),
                self.C8855_SET_RISE_EDGE,
            )
            if not ok:
                raise RuntimeError(f"C8855SetupEx failed for one-shot block {take}.")

            ok = self._dll.C8855CountStart(self._handle, self.C8855_SOFTWARE_TRIGGER)
            if not ok:
                raise RuntimeError("C8855CountStart failed for one-shot.")

            try:
                block = self._read_one_transfer(take)
            finally:
                self._count_stop()

            parts.append(block)
            remaining -= take

        counts = np.concatenate(parts).astype(np.float64, copy=False)
        return self._map_single_stream_to_channels(counts)

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass