from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional
import time
import numpy as np


@dataclass
class CameraParameters:
    exposure_ms: float = 10.0
    pixel_format: str = "Mono8"
    binning: str = "1x1"
    auto_exposure: bool = False


class CameraBackendBase:
    """
    Contrat minimal commun aux caméras DeepLight.
    Le backend ne dépend pas de Qt.
    """

    def __init__(self):
        self.connected = False
        self.live_running = False
        self.params = CameraParameters()

    # ---------- lifecycle ----------
    def connect(self) -> None:
        raise NotImplementedError

    def disconnect(self) -> None:
        raise NotImplementedError

    # ---------- capabilities ----------
    def list_binning(self) -> Iterable[str]:
        return ["1x1"]

    def list_pixel_formats(self) -> Iterable[str]:
        return ["Mono8"]

    # ---------- params ----------
    def set_parameters(self, params: CameraParameters) -> None:
        self.params = params

    def get_parameters(self) -> CameraParameters:
        return self.params

    # ---------- acquisition ----------
    def snap(self) -> np.ndarray:
        raise NotImplementedError

    def start_live(self) -> None:
        self.live_running = True

    def stop_live(self) -> None:
        self.live_running = False

    def get_frame(self) -> np.ndarray:
        """
        Doit renvoyer rapidement une image 2D numpy.
        En live, sera appelé périodiquement par le controller.
        """
        return self.snap()