import numpy as np
from .Scan_Types import FrameReconstructionPlan

class FrameBuilder:
    """
    Reconstruit une frame XY à partir d'un flux de samples.

    Hypothèses :
        - 1 sample = 1 pixel si samples_per_pixel = 1
        - support de samples_per_pixel >= 1
        - le raster acquis est défini par (dim_fast, dim_slow)
        - l'image reconstruite garde toujours un repère logique stable [y, x]
    """

    def __init__(
        self,
        arrays: dict[str, np.ndarray],
        channels: list[str],
        reconstruction_plan: FrameReconstructionPlan,
    ):
        self.arrays = arrays
        self.channels = list(channels)
        self.plan = reconstruction_plan

        self.dim_image_x = int(self.plan.dim_x)          # largeur image logique
        self.dim_image_y = int(self.plan.dim_y)          # hauteur image logique
        self.dim_fast = int(self.plan.dim_fast)    # nb pixels sur l'axe rapide
        self.dim_slow = int(self.plan.dim_slow)    # nb pixels sur l'axe lent

        self.samples_per_pixel = int(self.plan.samples_per_pixel)
        self.bidirectional = bool(self.plan.bidirectional)
        self.bidirectional_shift_px = int(self.plan.bidirectional_shift_px)
        self.turnback_offset_px = int(self.plan.turnback_offset_px)
        self.fast_axis_is_image_x = bool(self.plan.fast_axis_is_image_x)

        if self.samples_per_pixel <= 0:
            raise ValueError("samples_per_pixel must be >= 1")

        self.total_pixels = int(self.dim_fast * self.dim_slow)

        self.line_useful_samples = int(self.dim_fast * self.samples_per_pixel)
        self.line_turnback_samples = int(self.turnback_offset_px * self.samples_per_pixel)
        self.line_total_samples = int(self.line_useful_samples + self.line_turnback_samples)

        self.total_samples_needed = int(self.line_total_samples * self.dim_slow)

        self.written_pixels = 0
        self.raw_samples_consumed = 0
        self._reset_partial_accu()

    def _reset_partial_accu(self):
        self.partial_accu = {ch: 0.0 for ch in self.channels}
        self.partial_count = 0
    
    def _map_pixel_to_image_coords(self, flat_index: int) -> tuple[int, int]:
        """
        Convertit l'index raster acquis en coordonnées image (y, x).

        Raster acquis :
        - slow change lentement
        - fast change à chaque pixel dans une ligne

        Mapping image :
        - si fast_axis_is_image_x = True :
            fast -> x image
            slow -> y image
          cas classique XY
        - si fast_axis_is_image_x = False :
            fast -> y image
            slow -> x image
          cas YX

        En bidirectionnel :
        - une ligne slow sur deux est acquise dans le sens inverse
        - bidirectional_shift_px s'applique sur l'axe fast reconstruit
        """
        slow_idx = flat_index // self.dim_fast
        fast_idx_acquired = flat_index % self.dim_fast

        reverse_line = self.bidirectional and (slow_idx % 2 == 1)

        if reverse_line:
            fast_idx_image = (self.dim_fast - 1) - fast_idx_acquired
            fast_idx_image -= self.bidirectional_shift_px
        else:
            fast_idx_image = fast_idx_acquired

        # shift bidirectionnel hors image
        if fast_idx_image < 0 or fast_idx_image >= self.dim_fast:
            return -1, -1

        if self.fast_axis_is_image_x:
            x_img = fast_idx_image
            y_img = slow_idx
        else:
            x_img = slow_idx
            y_img = fast_idx_image

        if x_img < 0 or x_img >= self.dim_image_x or y_img < 0 or y_img >= self.dim_image_y:
            return -1, -1

        return y_img, x_img

    def reset(self):
        for ch in self.channels:
            self.arrays[ch].fill(0.0)
        self.written_pixels = 0
        self.raw_samples_consumed = 0
        self._reset_partial_accu()

    def remaining_pixels(self) -> int:
        return self.total_pixels - self.written_pixels

    def remaining_samples(self) -> int:
        return self.total_samples_needed - self.raw_samples_consumed

    def is_complete(self) -> bool:
        return self.raw_samples_consumed >= self.total_samples_needed

    def _is_turnback_sample(self, raw_sample_index: int) -> bool:
        """
        Indique si un sample brut appartient à la zone de turnback/flyback
        en fin de ligne.
        """
        if self.line_turnback_samples <= 0:
            return False

        pos_in_line = int(raw_sample_index % self.line_total_samples)
        return pos_in_line >= self.line_useful_samples

    def consume_samples(self, samples_by_channel: dict[str, np.ndarray]) -> int:
        """
        Consomme un bloc de samples et remplit les pixels au fur et à mesure.
        - les samples utiles construisent les pixels
        - les samples de turnback sont ignorés pour l'image
        """
        if not samples_by_channel or self.is_complete():
            return 0

        first_channel = self.channels[0]
        n_samples = int(len(samples_by_channel[first_channel]))
        pixels_written_now = 0

        for i in range(n_samples):
            if self.is_complete():
                break

            raw_idx = self.raw_samples_consumed

            # sample de flyback / turnback : on le consomme temporellement mais il ne doit pas entrer dans l'image
            if self._is_turnback_sample(raw_idx):
                self.raw_samples_consumed += 1
                continue

            for ch in self.channels:
                self.partial_accu[ch] += float(samples_by_channel[ch][i])

            self.partial_count += 1
            self.raw_samples_consumed += 1

            if self.partial_count >= self.samples_per_pixel:
                flat_pixel_index = self.written_pixels
                y_img, x_img = self._map_pixel_to_image_coords(flat_pixel_index)

                if y_img >= 0 and x_img >= 0:
                    for ch in self.channels:
                        self.arrays[ch][y_img, x_img] = self.partial_accu[ch] / float(self.partial_count)

                self.written_pixels += 1
                pixels_written_now += 1
                self._reset_partial_accu()

        return pixels_written_now

    def get_shown_images(self) -> dict[str, np.ndarray]:
        return {ch: self.arrays[ch].copy() for ch in self.channels}