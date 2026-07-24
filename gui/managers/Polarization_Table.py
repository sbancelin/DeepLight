"""
Table de calibration de la polarisation.

Pour un azimut de polarisation donné (en °), donne les positions (relatives, en °)
des deux lames d'onde nécessaires pour obtenir cette polarisation :
    azimut -> (position λ/2, position λ/4)

- Les valeurs par défaut ci-dessous sont issues de la calibration du setup.
- La table est ÉDITABLE sans toucher au code : un fichier CSV
  `polarization_table.csv` (colonnes: azimuth_deg, lambda2_deg, lambda4_deg)
  placé à côté de ce module est chargé au démarrage s'il existe ; sinon il est
  écrit avec les valeurs par défaut pour servir de base d'édition.
- Toute valeur d'azimut absente (ex. 140° dans la calibration d'origine) est
  obtenue par interpolation linéaire entre les points connus.
"""

from __future__ import annotations

import csv
import os

from ..widgets.Log_Widget import logger

# Azimut (°) -> (λ/2 °, λ/4 °). Calibration complète (0..180, pas 10°).
_DEFAULT_TABLE = {
    0:   (-14.81, 82.18),
    10:  (-7.30,  72.44),
    20:  (-1.88,  62.52),
    30:  (3.28,   52.58),
    40:  (8.36,   42.62),
    50:  (13.36,  32.73),
    60:  (18.49,  22.78),
    70:  (23.74,  12.91),
    80:  (30.00,  3.02),
    90:  (30.89,  -7.23),
    100: (37.43,  -17.16),
    110: (42.91,  -27.21),
    120: (48.23,  -37.32),
    130: (53.45,  -47.52),
    140: (58.71,  -57.66),
    150: (64.02,  -67.74),
    160: (69.42,  -77.81),
    170: (76.07,  -87.66),
    180: (76.31,  -97.97),
}

_CSV_PATH = os.path.join(os.path.dirname(__file__), "polarization_table.csv")


class PolarizationTable:
    """Table azimut -> (λ/2, λ/4) avec interpolation linéaire."""

    def __init__(self, points: dict[float, tuple[float, float]] | None = None):
        pts = dict(points if points is not None else _DEFAULT_TABLE)
        self._set_points(pts)

    def _set_points(self, pts: dict[float, tuple[float, float]]):
        # trié par azimut croissant pour l'interpolation
        items = sorted((float(a), (float(l2), float(l4))) for a, (l2, l4) in pts.items())
        self._az = [a for a, _ in items]
        self._l2 = [v[0] for _, v in items]
        self._l4 = [v[1] for _, v in items]

    @staticmethod
    def _interp(x: float, xs: list[float], ys: list[float]) -> float:
        if not xs:
            return 0.0
        if x <= xs[0]:
            return ys[0]
        if x >= xs[-1]:
            return ys[-1]
        # recherche du segment encadrant
        for i in range(1, len(xs)):
            if x <= xs[i]:
                x0, x1 = xs[i - 1], xs[i]
                y0, y1 = ys[i - 1], ys[i]
                if x1 == x0:
                    return y0
                t = (x - x0) / (x1 - x0)
                return y0 + t * (y1 - y0)
        return ys[-1]

    def lookup(self, azimuth_deg: float) -> tuple[float, float]:
        """Retourne (position λ/2, position λ/4) pour un azimut donné (interpolé)."""
        a = float(azimuth_deg)
        return self._interp(a, self._az, self._l2), self._interp(a, self._az, self._l4)

    # ------------------------------------------------------------------
    # Persistance CSV (édition sans toucher au code)
    # ------------------------------------------------------------------
    def load_csv(self, path: str = _CSV_PATH) -> bool:
        if not os.path.isfile(path):
            return False
        pts = {}
        try:
            with open(path, "r", newline="", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    try:
                        az = float(row["azimuth_deg"])
                        l2 = float(row["lambda2_deg"])
                        l4 = float(row["lambda4_deg"])
                    except (KeyError, ValueError, TypeError):
                        continue
                    pts[az] = (l2, l4)
        except Exception as e:
            logger.warning(f"[PolarizationTable] lecture CSV échouée ({path}): {e}")
            return False

        if not pts:
            return False
        self._set_points(pts)
        logger.info(f"[PolarizationTable] table chargée depuis {path} ({len(pts)} points)")
        return True

    def save_csv(self, path: str = _CSV_PATH) -> bool:
        try:
            with open(path, "w", newline="", encoding="utf-8") as f:
                w = csv.writer(f)
                w.writerow(["azimuth_deg", "lambda2_deg", "lambda4_deg"])
                for a, l2, l4 in zip(self._az, self._l2, self._l4):
                    w.writerow([a, l2, l4])
            return True
        except Exception as e:
            logger.warning(f"[PolarizationTable] écriture CSV échouée ({path}): {e}")
            return False


# Singleton partagé (chargé depuis le CSV s'il existe, sinon défaut + écriture).
_TABLE: PolarizationTable | None = None


def get_polarization_table() -> PolarizationTable:
    global _TABLE
    if _TABLE is None:
        _TABLE = PolarizationTable()
        if not _TABLE.load_csv():
            # Première exécution : dépose le CSV par défaut, éditable ensuite.
            _TABLE.save_csv()
    return _TABLE