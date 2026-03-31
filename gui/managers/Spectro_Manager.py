import numpy as np
import time
from PySide6.QtCore import QObject, Signal, QTimer


class SpectroManager(QObject):

    sigImageUpdate = Signal(object)
    sigSpectrumUpdate = Signal(object)
    sigStatusMessage = Signal(str)

    sigFinished = Signal(object)

    def __init__(self, hardware_manager=None):

        super().__init__()

        self.hardware = hardware_manager

        self.running = False

        self.timer = QTimer()
        self.timer.timeout.connect(self._acquire_next_pixel)

        self.dataset = None
        self.pixel_list = []
        self.current_pixel_index = 0

    # ==========================================================
    # Public API
    # ==========================================================

    def start_mapping(
        self,
        scan_parameters,
        modes,
        brillouin_params,
        raman_params,
    ):

        if self.running:
            return

        self.running = True

        pixels_x = scan_parameters["pixels_x"]
        pixels_y = scan_parameters["pixels_y"]

        self.pixel_list = self._build_serpentine_grid(pixels_x, pixels_y)

        self.current_pixel_index = 0

        self.dataset = self._create_dataset(
            scan_parameters,
            modes,
            brillouin_params,
            raman_params,
        )

        self.sigStatusMessage.emit("Spectro acquisition started")

        self.timer.start(1)

    def stop(self):

        if not self.running:
            return

        self.running = False
        self.timer.stop()

        self.sigStatusMessage.emit("Spectro acquisition stopped")

        if self.dataset is not None:
            self.sigFinished.emit(self.dataset)

    # ==========================================================
    # Grid
    # ==========================================================

    def _build_serpentine_grid(self, pixels_x, pixels_y):

        grid = []

        for y in range(pixels_y):

            xs = list(range(pixels_x))

            if y % 2 == 1:
                xs.reverse()

            for x in xs:
                grid.append((x, y))

        return grid

    # ==========================================================
    # Dataset
    # ==========================================================

    def _create_dataset(
        self,
        scan_parameters,
        modes,
        brillouin_params,
        raman_params,
    ):

        px = scan_parameters["pixels_x"]
        py = scan_parameters["pixels_y"]

        dataset = {}

        dataset["grid_shape"] = (py, px)

        dataset["positions"] = np.zeros((px * py, 2))

        dataset["metadata"] = {
            "acquisition_parameters": scan_parameters,
            "brillouin_parameters": brillouin_params,
            "raman_parameters": raman_params,
        }

        if modes.get("brillouin", False):

            dataset["brillouin_images"] = np.zeros(
                (py, px, 64, 64), dtype=np.float32
            )

        if modes.get("raman", False):

            n_lambda = 1024

            dataset["raman_wavelengths"] = np.linspace(
                raman_params["center_nm"] - raman_params["span_nm"] / 2,
                raman_params["center_nm"] + raman_params["span_nm"] / 2,
                n_lambda,
            )

            dataset["raman_spectra"] = np.zeros(
                (py, px, n_lambda), dtype=np.float32
            )

        return dataset

    # ==========================================================
    # Acquisition loop
    # ==========================================================

    def _acquire_next_pixel(self):

        if not self.running:
            return

        if self.current_pixel_index >= len(self.pixel_list):

            self.stop()
            return

        x, y = self.pixel_list[self.current_pixel_index]

        self.sigStatusMessage.emit(
            f"Pixel {self.current_pixel_index + 1}/{len(self.pixel_list)}"
        )

        # ======================================================
        # MOCK move stage
        # ======================================================

        time.sleep(0.005)

        # ======================================================
        # MOCK Brillouin
        # ======================================================

        if "brillouin_images" in self.dataset:

            image = np.random.random((64, 64))

            self.dataset["brillouin_images"][y, x] = image

            self.sigImageUpdate.emit(image)

        # ======================================================
        # MOCK Raman
        # ======================================================

        if "raman_spectra" in self.dataset:

            spectrum = self._mock_raman()

            self.dataset["raman_spectra"][y, x] = spectrum

            self.sigSpectrumUpdate.emit(spectrum)

        self.current_pixel_index += 1

    # ==========================================================
    # Mock generators
    # ==========================================================

    def _mock_raman(self):

        x = np.linspace(0, 10, 1024)

        peak = np.exp(-(x - 5) ** 2)

        noise = np.random.normal(0, 0.02, x.size)

        return peak + noise