from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, Iterator, List, Optional
import numpy as np

from .Scan_Types import SampleFramePlan


@dataclass
class SamplePixelEvent:
    ix: int
    iy: int
    x_axis_name: str
    y_axis_name: str
    x_target_rel_um: float
    y_target_rel_um: float


class SampleScanManager:
    """
    Planificateur sample-scanning point par point.

    Rôle:
    - choisir les 2 axes image (XY)
    - déduire les positions discrètes des axes supplémentaires (axis3/axis4)
    - générer les positions relatives pixel par pixel

    Important:
    - toutes les positions générées sont *relatives* au zéro courant du positioner
    - les offsets consommés ici sont donc les `relative_offset_um` issus du ScanWidget
    - l'ordre des frames suit la même convention que le chemin laser:
      axis4 extérieur, axis3 intérieur
    """

    def __init__(self):
        self.scan_parameters: Dict = {}
        self.rows_by_axis: Dict[str, Dict] = {}
        self.axis_order: List[str] = []
        self.x_axis_name: str = "X-Stage"
        self.y_axis_name: str = "Y-Stage"
        self.nx: int = 128
        self.ny: int = 128
        self.size_x_um: float = 100.0
        self.size_y_um: float = 100.0
        self.offset_x_um: float = 0.0
        self.offset_y_um: float = 0.0
        self.step_x_um: float = 0.0
        self.step_y_um: float = 0.0
        self.axis3_name: Optional[str] = None
        self.axis4_name: Optional[str] = None
        self.axis3_positions: List[Optional[float]] = [None]
        self.axis4_positions: List[Optional[float]] = [None]
        self.dwell_time_s: float = 0.010
        self.samples_per_pixel: int = 1
        self.sample_settle_time_s: float = 0.0
        self.bidirectional: bool = False

    def configure(self, scan_parameters: Dict):
        self.scan_parameters = dict(scan_parameters or {})
        rows: List[Dict] = list(self.scan_parameters.get("rows", []))
        self.rows_by_axis = {
            str(r.get("axis")): r for r in rows if str(r.get("axis", "None")) != "None"
        }
        self.axis_order = [
            str(axis_name)
            for axis_name in self.scan_parameters.get("axis_order", [])
            if str(axis_name) != "None"
        ]

        x_row = self._find_axis_row(preferred=("X-Stage", "X-Galvo"))
        y_row = self._find_axis_row(
            preferred=("Y-Stage", "Y-Galvo"),
            exclude_axis=x_row.get("axis") if x_row else None,
        )

        if x_row is None or y_row is None:
            raise ValueError("SampleScanManager requires two active scan axes.")

        self.x_axis_name = str(x_row.get("axis", "X-Stage"))
        self.y_axis_name = str(y_row.get("axis", "Y-Stage"))

        self.nx = max(1, int(x_row.get("pixels", 128) or 128))
        self.ny = max(1, int(y_row.get("pixels", 128) or 128))

        self.size_x_um = float(x_row.get("size_um", 100.0) or 100.0)
        self.size_y_um = float(y_row.get("size_um", 100.0) or 100.0)

        self.offset_x_um = float(x_row.get("relative_offset_um", 0.0) or 0.0)
        self.offset_y_um = float(y_row.get("relative_offset_um", 0.0) or 0.0)

        self.step_x_um = self._compute_step_um(self.size_x_um, self.nx)
        self.step_y_um = self._compute_step_um(self.size_y_um, self.ny)

        self.axis3_name = None
        self.axis4_name = None
        self.axis3_positions = [None]
        self.axis4_positions = [None]

        if len(self.axis_order) >= 3:
            candidate = str(self.axis_order[2])
            if candidate != "None":
                self.axis3_name = candidate
                self.axis3_positions = self._build_axis_positions(candidate)

        if len(self.axis_order) >= 4:
            candidate = str(self.axis_order[3])
            if candidate != "None":
                self.axis4_name = candidate
                self.axis4_positions = self._build_axis_positions(candidate)

        self.dwell_time_s = float(self.scan_parameters.get("dwell_time", 0.010) or 0.010)
        self.samples_per_pixel = max(1, int(self.scan_parameters.get("samples_per_pixel", 1) or 1))
        self.sample_settle_time_s = max(0.0, float(self.scan_parameters.get("sample_settle_time_s", 0.0) or 0.0))
        self.bidirectional = bool(self.scan_parameters.get("bidirectional_scan", False))

    def _find_axis_row(self, preferred: Iterable[str], exclude_axis: Optional[str] = None) -> Optional[Dict]:
        for axis in preferred:
            row = self.rows_by_axis.get(axis)
            if row is not None and axis != exclude_axis:
                return row
            
        for axis_name in self.axis_order:
            if axis_name == exclude_axis:
                continue
            row = self.rows_by_axis.get(axis_name)
            if row is not None:
                return row

        for axis_name, row in self.rows_by_axis.items():
            if axis_name != exclude_axis:
                return row
        return None

    @staticmethod
    def _compute_step_um(size_um: float, pixels: int) -> float:
        if pixels <= 1:
            return 0.0
        return float(size_um) / float(pixels - 1)
    
    @staticmethod
    def _compute_axis_positions_from_row(row: Dict) -> List[float]:
        pixels = max(1, int(row.get("pixels", 1) or 1))
        size_um = float(row.get("size_um", 0.0) or 0.0)
        offset_rel = float(row.get("relative_offset_um", 0.0) or 0.0)

        if pixels == 1:
            return [offset_rel]

        start = offset_rel - size_um / 2.0
        stop = offset_rel + size_um / 2.0
        return list(np.linspace(start, stop, pixels))

    def _build_axis_positions(self, axis_name: str) -> List[float]:
        row = self.rows_by_axis.get(axis_name)
        if row is None:
            return [0.0]
        return self._compute_axis_positions_from_row(row)

    def image_shape(self) -> tuple[int, int]:
        return self.ny, self.nx
    
    def frame_count(self) -> int:
        return max(1, len(self.axis3_positions)) * max(1, len(self.axis4_positions))

    def iter_frame_plans(self) -> Iterator[SampleFramePlan]:
        for i4, pos4 in enumerate(self.axis4_positions):
            for i3, pos3 in enumerate(self.axis3_positions):
                yield SampleFramePlan(
                    axis3_index=i3,
                    axis4_index=i4,
                    axis3_name=self.axis3_name,
                    axis4_name=self.axis4_name,
                    axis3_value=pos3,
                    axis4_value=pos4,
                )

    def iter_pixel_events(self) -> Iterator[SamplePixelEvent]:
        if self.nx <= 0 or self.ny <= 0:
            return

        x0 = self.offset_x_um - self.size_x_um / 2.0
        y0 = self.offset_y_um - self.size_y_um / 2.0

        for iy in range(self.ny):
            if self.bidirectional and (iy % 2 == 1):
                x_iter = range(self.nx - 1, -1, -1)
            else:
                x_iter = range(self.nx)

            y_um = y0 + iy * self.step_y_um if self.ny > 1 else self.offset_y_um

            for ix in x_iter:
                x_um = x0 + ix * self.step_x_um if self.nx > 1 else self.offset_x_um
                yield SamplePixelEvent(
                    ix=ix,
                    iy=iy,
                    x_axis_name=self.x_axis_name,
                    y_axis_name=self.y_axis_name,
                    x_target_rel_um=float(x_um),
                    y_target_rel_um=float(y_um),
                )