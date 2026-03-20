import numpy as np
from .Scan_Types import FrameReconstructionPlan


class FrameBuilder:
    """
    Reconstruit une frame XY à partir d'un flux de samples.

    Objectif:
    - garder exactement le même contrat externe que la version précédente
    - supprimer la boucle Python sample-par-sample
    - conserver un remplissage progressif de l'image pendant l'acquisition

    Hypothèses :
        - le flux brut est ordonné ligne par ligne
        - chaque ligne brute contient :
              [samples utiles] + [samples de turnback éventuels]
        - les samples utiles d'une ligne correspondent à dim_fast pixels
        - chaque pixel = moyenne de samples_per_pixel samples consécutifs
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

        self.dim_image_x = int(self.plan.dim_x)
        self.dim_image_y = int(self.plan.dim_y)

        self.dim_fast = int(self.plan.dim_fast)
        self.dim_slow = int(self.plan.dim_slow)

        self.samples_per_pixel = int(self.plan.samples_per_pixel)
        self.bidirectional = bool(self.plan.bidirectional)
        self.bidirectional_shift_px = int(self.plan.bidirectional_shift_px)
        self.turnback_offset_px = int(self.plan.turnback_offset_px)
        self.fast_axis_is_image_x = bool(self.plan.fast_axis_is_image_x)

        if self.samples_per_pixel <= 0:
            raise ValueError("samples_per_pixel must be >= 1")

        self.n_channels = len(self.channels)

        self.total_pixels = int(self.dim_fast * self.dim_slow)

        self.line_useful_samples = int(self.dim_fast * self.samples_per_pixel)
        self.line_turnback_samples = int(self.turnback_offset_px * self.samples_per_pixel)
        self.line_total_samples = int(self.line_useful_samples + self.line_turnback_samples)

        self.total_samples_needed = int(self.line_total_samples * self.dim_slow)

        # Buffer ligne utile (sans turnback), partagé pour tous les canaux
        # float32 pour rester cohérent avec l'affichage / image finale
        self._line_buffer = np.empty(
            (self.n_channels, self.line_useful_samples),
            dtype=np.float32,
        )

        # Cache d'indices pour éviter de recréer des arange tout le temps
        self._pixel_index_cache = np.arange(self.dim_fast, dtype=np.int32)

        self.reset()

    def reset(self):
        for ch in self.channels:
            self.arrays[ch].fill(0.0)

        self.written_pixels = 0
        self.raw_samples_consumed = 0

        # Position courante dans le raster brut
        self._slow_idx = 0
        self._raw_pos_in_line = 0  # [0 .. line_total_samples)

        # État de la ligne utile en cours
        self._useful_samples_filled = 0
        self._written_pixels_in_line = 0

    def remaining_pixels(self) -> int:
        return self.total_pixels - self.written_pixels

    def remaining_samples(self) -> int:
        return self.total_samples_needed - self.raw_samples_consumed

    def is_complete(self) -> bool:
        return self.raw_samples_consumed >= self.total_samples_needed

    def _reverse_line(self, slow_idx: int) -> bool:
        return self.bidirectional and (slow_idx % 2 == 1)

    def _write_new_pixels_in_current_line(self):
        """
        Écrit dans les arrays uniquement les pixels nouvellement complets
        de la ligne courante.
        """
        completed_pixels = self._useful_samples_filled // self.samples_per_pixel
        if completed_pixels <= self._written_pixels_in_line:
            return 0

        start_pix = self._written_pixels_in_line
        stop_pix = completed_pixels
        count = stop_pix - start_pix

        # (C, count, spp) -> moyenne sur spp -> (C, count)
        start_s = start_pix * self.samples_per_pixel
        stop_s = stop_pix * self.samples_per_pixel

        block = self._line_buffer[:, start_s:stop_s]
        block = block.reshape(self.n_channels, count, self.samples_per_pixel).mean(axis=2)

        acquired_fast_idx = self._pixel_index_cache[start_pix:stop_pix]

        if self._reverse_line(self._slow_idx):
            image_fast_idx = (self.dim_fast - 1) - acquired_fast_idx
            image_fast_idx = image_fast_idx - self.bidirectional_shift_px
        else:
            image_fast_idx = acquired_fast_idx

        valid = (image_fast_idx >= 0) & (image_fast_idx < self.dim_fast)

        if np.any(valid):
            valid_img_fast = image_fast_idx[valid]
            valid_block = block[:, valid]

            if self.fast_axis_is_image_x:
                y = self._slow_idx
                for ci, ch in enumerate(self.channels):
                    self.arrays[ch][y, valid_img_fast] = valid_block[ci]
            else:
                x = self._slow_idx
                for ci, ch in enumerate(self.channels):
                    self.arrays[ch][valid_img_fast, x] = valid_block[ci]

        self._written_pixels_in_line = completed_pixels
        self.written_pixels += count
        return count

    def _finish_current_line_if_needed(self):
        """
        Si on a consommé toute la ligne brute (utile + turnback),
        on passe à la ligne suivante.
        """
        if self._raw_pos_in_line < self.line_total_samples:
            return

        self._slow_idx += 1
        self._raw_pos_in_line = 0
        self._useful_samples_filled = 0
        self._written_pixels_in_line = 0

    def consume_samples(self, samples_by_channel: dict[str, np.ndarray]) -> int:
        """
        Consomme un bloc de samples et remplit les pixels au fur et à mesure.

        Retour :
            nombre de pixels nouvellement reconstruits.
        """
        if not samples_by_channel or self.is_complete():
            return 0

        first_channel = self.channels[0]
        n_samples = int(len(samples_by_channel[first_channel]))
        if n_samples <= 0:
            return 0

        # Vérification légère de cohérence
        for ch in self.channels:
            if len(samples_by_channel[ch]) != n_samples:
                raise ValueError("All channels must provide the same number of samples")

        src_pos = 0
        pixels_written_now = 0

        while src_pos < n_samples and not self.is_complete():
            if self._slow_idx >= self.dim_slow:
                # sécurité
                remaining = n_samples - src_pos
                self.raw_samples_consumed += remaining
                break

            remaining_raw_in_line = self.line_total_samples - self._raw_pos_in_line
            take = min(remaining_raw_in_line, n_samples - src_pos)

            seg_raw_start = self._raw_pos_in_line
            seg_raw_stop = self._raw_pos_in_line + take

            # Portion utile de ce segment brut
            useful_start_in_seg = 0
            useful_stop_in_seg = min(seg_raw_stop, self.line_useful_samples) - seg_raw_start

            if useful_stop_in_seg > useful_start_in_seg:
                useful_take = useful_stop_in_seg - useful_start_in_seg

                dst0 = self._useful_samples_filled
                dst1 = dst0 + useful_take

                src0 = src_pos + useful_start_in_seg
                src1 = src0 + useful_take

                for ci, ch in enumerate(self.channels):
                    self._line_buffer[ci, dst0:dst1] = np.asarray(
                        samples_by_channel[ch][src0:src1],
                        dtype=np.float32,
                    )

                self._useful_samples_filled = dst1
                pixels_written_now += self._write_new_pixels_in_current_line()

            self._raw_pos_in_line += take
            self.raw_samples_consumed += take
            src_pos += take

            self._finish_current_line_if_needed()

        # Ne jamais dépasser le budget théorique
        if self.raw_samples_consumed > self.total_samples_needed:
            self.raw_samples_consumed = self.total_samples_needed

        if self.written_pixels > self.total_pixels:
            self.written_pixels = self.total_pixels

        return pixels_written_now

    def get_shown_images(self) -> dict[str, np.ndarray]:
        return {ch: self.arrays[ch].copy() for ch in self.channels}