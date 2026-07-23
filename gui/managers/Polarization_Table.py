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

# Azimut (°) -> (λ/2 °, λ/4 °). 140° volontairement absent : interpolé.
_DEFAULT_TABLE = {
    0:   (57.32, -97.38),
    10:  (52.29, -87.21),
    20:  (47.53, -77.09),
    30:  (43.22, -66.84),
    40:  (41.17, -56.32),
    50:  (31.00, -46.50),
    60:  (21.91, -37.81),
    70:  (18.47, -27.52),
    80:  (13.87, -17.55),
    90:  (8.92,  -7.53),
    100: (3.86,  2.45),
    110: (-1.47, 12.37),
    120: (-7.41, 22.35),
    130: (-15.58, 32.56),
    # 140: absent -> interpolé (≈ -14.96, 42.14)
    150: (-14.34, 51.72),
    160: (-21.57, 61.96),
    170: (-27.25, 72.21),
    180: (-32.52, 82.38),
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