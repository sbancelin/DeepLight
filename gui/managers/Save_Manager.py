import os
import time
import json
import csv
import numpy as np
from datetime import datetime
from threading import Lock

import tifffile
import zarr

from ..widgets.Log_Widget import logger

NGFF_AXES_TCZYX = [
    {"name": "t", "type": "time"},
    {"name": "c", "type": "channel"},
    {"name": "z", "type": "space"},
    {"name": "y", "type": "space"},
    {"name": "x", "type": "space"},
]

def make_unique_path(
    folder: str,
    name: str,
    ext: str,
    default_stem: str,
) -> str:
    """
    Construit un chemin unique dans 'folder' en garantissant l'extension 'ext'.
    - si name vide -> default_stem + HHMMSS + ext
    - si name sans extension -> ajoute ext
    - si name a déjà une extension:
        - si ext est fourni et différent, on remplace par ext (comportement contrôlé)
        - sinon on garde l'extension existante
    - si le fichier existe -> ajoute _001, _002, ... avant l'extension
    """
    os.makedirs(folder, exist_ok=True)

    name = (name or "").strip()
    if not name:
        name = f"{default_stem}_{time.strftime('%H%M%S')}{ext}"

    base, e = os.path.splitext(name)

    if not e:
        # pas d'ext -> on force ext
        e = ext
    else:
        # déjà une ext: si ext demandé explicitement, on force ext
        if ext and e.lower() != ext.lower():
            e = ext

    if not base:
        base = f"{default_stem}_{time.strftime('%H%M%S')}"

    path = os.path.join(folder, base + e)
    if not os.path.exists(path):
        return path

    k = 1
    while True:
        cand = os.path.join(folder, f"{base}_{k:03d}{e}")
        if not os.path.exists(cand):
            return cand
        k += 1

class SaveManager:
    """
    Gestion des sauvegardes manuelles et des sessions REC.

    Save current view:
      - OME-TIFF (CYX)
      - OME-Zarr (TCZYX avec T=1, Z=1)

    REC:
      - OME-TIFF : buffer RAM puis écriture finale
      - OME-Zarr : écriture streaming au fil de l'eau (TCZYX)
    """

    def __init__(self):
        self._lock = Lock()

        # session REC
        self._rec_active = False
        self._rec_path = None
        self._rec_comment = ""
        self._rec_channels = []

        # OME-Zarr streaming state
        self._rec_root = None
        self._rec_arr0 = None
        self._rec_n2 = 1
        self._rec_n3 = 1
        self._rec_z = 1

        # OME-TIFF REC (écriture à la fin)
        self._rec_mode = None          # "ome-zarr" | "ome-tiff"
        self._rec_stack = None         # np.ndarray (T,C,Z,Y,X) float32
        self._rec_scan_params = {}

    def _norm_fmt(self, fmt: str) -> str:
        f = (fmt or "").strip().upper().replace("_", "-").replace(" ", "")
        if f in ("OME-TIFF", "OMETIFF"):
            return "OME-TIFF"
        if f in ("OME-ZARR", "OMEZARR", "ZARR"):
            return "OME-ZARR"
        return (fmt or "").strip().upper()

    def _make_omero_metadata(self, name: str, channels: list[str]) -> dict:
        return {
            "name": str(name),
            "channels": [{"label": str(c)} for c in channels],
        }

    def _now_iso(self) -> str:
        return datetime.now().isoformat(timespec="seconds")
    
    def _write_json(self, path: str, payload: dict) -> None:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, default=str)

    def _write_positions_csv(self, path: str, dataset: dict) -> None:
        order = np.asarray(dataset.get("acquisition_order_indices", []), dtype=np.int32)
        pos_um = np.asarray(dataset.get("acquisition_order_positions_um", []), dtype=np.float32)

        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["linear_index", "t_index", "z_index", "y_index", "x_index", "x_um", "y_um", "z_um"])

            n = min(len(order), len(pos_um))
            for i in range(n):
                row_idx = order[i]
                if len(row_idx) == 4:
                    t_idx, z_idx, y_idx, x_idx = int(row_idx[0]), int(row_idx[1]), int(row_idx[2]), int(row_idx[3])
                else:
                    t_idx, z_idx, y_idx, x_idx = 0, int(row_idx[0]), int(row_idx[1]), int(row_idx[2])
                x_um, y_um, z_um = [float(v) for v in pos_um[i]]
                writer.writerow([i, t_idx, z_idx, y_idx, x_idx, x_um, y_um, z_um])

    # ---------- manual save (what you see) ----------

    def save_current_view(
        self,
        folder: str,
        filename: str,
        fmt: str,
        comment: str,
        images_by_channel: dict,
        scan_params: dict,
    ) -> str:
        """
        images_by_channel: channel -> 2D array déjà transposé (exactement ce que tu vois)
        """
        os.makedirs(folder, exist_ok=True)
        fmt = self._norm_fmt(fmt)

        if fmt == "OME-TIFF":
            return self.save_current_view_ome_tiff(folder, filename, comment, images_by_channel, scan_params)

        if fmt == "OME-ZARR":
            return self.save_current_view_ome_zarr(folder, filename, comment, images_by_channel, scan_params)

        raise ValueError(f"Unsupported format: {fmt}")

    def save_current_view_ome_tiff(
        self,
        folder: str,
        filename: str,
        comment: str,
        images_by_channel: dict,
        scan_params: dict,
    ) -> str:
        """
        1 fichier OME-TIFF multicanal : axes CYX.
        -> Fiji Bio-Formats OK, Napari OK.
        """
        path = make_unique_path(folder, filename, ext=".ome.tif", default_stem="VIEW")

        channels = list(images_by_channel.keys())
        stack = np.stack([np.asarray(images_by_channel[ch]) for ch in channels], axis=0).astype(np.float32, copy=False)
        # stack: (C, Y, X)

        ome_metadata = {
            "axes": "CYX",
            "Channel": [{"Name": str(ch)} for ch in channels],
        }

        with self._lock:
            tifffile.imwrite(
                path,
                stack,
                photometric="minisblack",
                metadata=ome_metadata,     # tifffile génère l’OME-XML
            )

            # sidecar JSON pour scan_params + commentaire (robuste)
            sidecar = os.path.splitext(path)[0] + ".json"
            payload = {
                "created": self._now_iso(),
                "comment": comment or "",
                "channels": [str(c) for c in channels],
                "scan_params": scan_params,
            }
            self._write_json(sidecar, payload)

        return path

    def save_current_view_ome_zarr(
        self,
        folder: str,
        filename: str,
        comment: str,
        images_by_channel: dict,
        scan_params: dict,
    ) -> str:
        """
        Ecrit un OME-Zarr "simple" pour la vue courante:
        data shape = (T=1, C, Z=1, Y, X) avec axes "tczyx"
        """
        os.makedirs(folder, exist_ok=True)

        zarr_path = make_unique_path(folder, filename, ext=".zarr", default_stem="VIEW")

        channels = list(images_by_channel.keys())
        stack_cyx = np.stack([np.asarray(images_by_channel[ch], dtype=np.float32) for ch in channels], axis=0)  # (C,Y,X)
        data = stack_cyx[None, :, None, :, :]  # (1, C, 1, Y, X)

        # store zarr (v2 par défaut) + metadata NGFF minimal
        root = zarr.open_group(zarr_path, mode="w")

        arr0 = root.create_dataset(
            "0",
            shape=data.shape,
            data=data,
            chunks=(1, 1, 1, min(256, data.shape[-2]), min(256, data.shape[-1])),
            dtype=np.float32,
            overwrite=True,
        )

        root.attrs["multiscales"] = [{
            "version": "0.4",
            "datasets": [{"path": "0"}],
            "axes": NGFF_AXES_TCZYX,
        }]
        root.attrs["omero"] = self._make_omero_metadata(os.path.basename(zarr_path), channels)

        root.attrs["comment"] = comment or ""
        root.attrs["created"] = self._now_iso()
        root.attrs["scan_params"] = json.loads(json.dumps(scan_params or {}, default=str))

        return zarr_path

    def save_mosaic(
        self,
        folder: str,
        filename: str,
        comment: str,
        mosaic,
        scan_params: dict,
        mosaic_params: dict | None = None,
    ) -> str:
        """
        Sauvegarde une mosaïque de stitching en OME-TIFF.

        - mosaïque 2D  -> axes "YX"
        - mosaïque 3D  -> axes "ZYX" (un plan par index d'axe stack Z/P)

        Un sidecar JSON accompagne le fichier (params scan + mosaïque + commentaire).
        """
        os.makedirs(folder, exist_ok=True)

        arr = np.asarray(mosaic, dtype=np.float32)
        if arr.ndim == 3 and arr.shape[0] == 1:
            arr = arr[0]

        axes = "ZYX" if arr.ndim == 3 else "YX"

        path = make_unique_path(folder, filename, ext=".ome.tif", default_stem="MOSAIC")

        with self._lock:
            tifffile.imwrite(
                path,
                arr,
                photometric="minisblack",
                metadata={"axes": axes},
            )

            sidecar = os.path.splitext(path)[0] + ".json"
            payload = {
                "created": self._now_iso(),
                "comment": comment or "",
                "axes": axes,
                "shape": list(arr.shape),
                "mosaic_params": mosaic_params or {},
                "scan_params": scan_params or {},
            }
            self._write_json(sidecar, payload)

        return path

    # ---------- REC ----------

    def start_rec_session(
        self,
        fmt: str,
        folder: str,
        filename: str,
        scan_parameters: dict,
        comment: str = "",
        channels=None,
    ) -> bool:
        """
        REC session:
        - OME-TIFF : buffer en RAM (T,C,Z,Y,X) puis écriture à finish_rec_session()
        - OME-Zarr : streaming (écrit au fil de l'eau)
        """
        self.finish_rec_session()

        fmt_u = self._norm_fmt(fmt)
        os.makedirs(folder, exist_ok=True)

        # Nom auto si vide
        if not (filename or "").strip():
            filename = "REC_" + time.strftime("%H%M%S")

        # Channels
        if channels is None:
            channels = list(scan_parameters.get("active_channels") or ["default"])
        self._rec_channels = [str(c) for c in channels]

        # Dims
        reps = int(scan_parameters.get("repetitions", 1))
        pix = list(scan_parameters.get("pixel_values", [256, 256, 1, 1]))

        dim_x = int(pix[0])
        dim_y = int(pix[1])
        Y = dim_y   # car affichage = .T
        X = dim_x

        active_axes = list(scan_parameters.get("active_axes") or [])
        self._rec_n2 = int(pix[2]) if len(active_axes) >= 3 else 1
        self._rec_n3 = int(pix[3]) if len(active_axes) >= 4 else 1
        self._rec_z = self._rec_n2 * self._rec_n3

        self._rec_comment = comment or ""
        self._rec_scan_params = json.loads(json.dumps(scan_parameters, default=str))

        # -------- OME-TIFF (écrit à la fin) --------
        if fmt_u == "OME-TIFF":
            path = make_unique_path(folder, filename, ext=".ome.tif", default_stem="REC")
            self._rec_path = path

            # buffer en RAM
            try:
                self._rec_stack = np.zeros(
                    (reps, len(self._rec_channels), self._rec_z, Y, X),
                    dtype=np.float32
                )
                self._rec_active = True
                self._rec_mode = "ome-tiff"
                return True
            except Exception as e:
                logger.error(f"[REC] Could not allocate OME-TIFF buffer: {e}")
                self.finish_rec_session()
                return False

        # -------- OME-ZARR (streaming) --------
        if fmt_u == "OME-ZARR":
            try:
                path = make_unique_path(folder, filename, ext=".zarr", default_stem="REC")
                self._rec_path = path

                # zarr group
                root = zarr.open_group(path, mode="w")

                arr0 = root.create_dataset(
                    "0",
                    shape=(reps, len(self._rec_channels), self._rec_z, Y, X),
                    chunks=(1, 1, 1, min(256, Y), min(256, X)),
                    dtype=np.float32,
                    overwrite=True,
                )

                # ---- NGFF minimal metadata ----
                root.attrs["multiscales"] = [{
                    "version": "0.4",
                    "datasets": [{"path": "0"}],
                    "axes": NGFF_AXES_TCZYX,
                }]
                root.attrs["omero"] = self._make_omero_metadata(os.path.basename(path), self._rec_channels)

                root.attrs["comment"] = self._rec_comment
                root.attrs["created"] = self._now_iso()
                root.attrs["channels"] = self._rec_channels
                root.attrs["scan_params"] = self._rec_scan_params

                self._rec_root = root
                self._rec_arr0 = arr0
                self._rec_active = True
                self._rec_mode = "ome-zarr"

                return True

            except Exception as e:
                logger.error(f"[REC] Could not start OME-Zarr session: {e}")
                self.finish_rec_session()
                return False

        logger.error(f"[REC] Unsupported REC format: {fmt}")
        self.finish_rec_session()
        return False

    def _index_tuple_to_z(self, idx_tuple: tuple) -> int:
        i2 = int(idx_tuple[0]) if len(idx_tuple) >= 1 else 0
        i3 = int(idx_tuple[1]) if len(idx_tuple) >= 2 else 0
        return i2 * self._rec_n3 + i3
    
    def append_rec_frame(self, rep: int, idx_tuple: tuple, images_by_channel: dict):
        """
        Streaming uniquement (OME-Zarr):
          écrit arr0[rep, c, z, :, :]
        idx_tuple: () or (i2,) or (i2,i3)
        """
        if not self._rec_active:
            return

        z = self._index_tuple_to_z(idx_tuple)

        # -------- OME-TIFF buffer --------
        if self._rec_mode == "ome-tiff":
            if self._rec_stack is None:
                return
            with self._lock:
                for ci, ch in enumerate(self._rec_channels):
                    img = images_by_channel.get(ch)
                    if img is None:
                        continue
                    self._rec_stack[rep, ci, z, :, :] = np.asarray(img, dtype=np.float32, order="C")
            return

        # -------- OME-Zarr streaming --------
        if self._rec_mode != "ome-zarr" or self._rec_arr0 is None:
            return

        with self._lock:
            for ci, ch in enumerate(self._rec_channels):
                img = images_by_channel.get(ch)
                if img is None:
                    continue
                img = np.asarray(img, dtype=np.float32, order="C")
                self._rec_arr0[rep, ci, z, :, :] = img
    
    def finish_rec_session(self):
        """
        - OME-TIFF: écrit le fichier à la fin (stack TCZYX)
        - OME-Zarr: rien à faire, on reset l'état
        """
        with self._lock:
            mode = self._rec_mode
            path = self._rec_path
            stack = self._rec_stack
            channels = list(self._rec_channels)
            comment = self._rec_comment
            scan_params = dict(self._rec_scan_params)

            # reset état (toujours)
            self._rec_active = False
            self._rec_mode = None
            self._rec_path = None
            self._rec_root = None
            self._rec_arr0 = None
            self._rec_channels = []
            self._rec_n2 = 1
            self._rec_n3 = 1
            self._rec_z = 1
            self._rec_stack = None
            self._rec_comment = ""
            self._rec_scan_params = {}

        # écriture OME-TIFF hors du lock (I/O)
        if mode == "ome-tiff" and path and stack is not None:
            ome_metadata = {
                "axes": "TCZYX",
                "Channel": [{"Name": str(ch)} for ch in channels],
            }

            tifffile.imwrite(
                path,
                stack,  # (T,C,Z,Y,X)
                photometric="minisblack",
                metadata=ome_metadata,
            )

            # sidecar json (robuste, simple)
            sidecar = os.path.splitext(path)[0] + ".json"
            payload = {
                "created": self._now_iso(),
                "comment": comment or "",
                "channels": channels,
                "scan_params": scan_params,
                "axes_numpy": "TCZYX",
            }
            self._write_json(sidecar, payload)

    # ---------- Spectro dataset ----------
    def save_spectro_dataset(
        self,
        folder: str,
        filename: str,
        comment: str,
        dataset: dict,
        fmt: str = "OME-TIFF",
    ) -> str:
        """
        Sauvegarde d'un dataset spectro en format clair :

        OME-TIFF:
            <root>/
                metadata.json
                positions.csv
                brillouin.ome.tif   (si présent)
                raman.ome.tif       (si présent)

        OME-ZARR:
            <root>/
                metadata.json
                positions.csv
                brillouin.zarr      (si présent)
                raman.zarr          (si présent)
                raman_wavelengths.npy (si Raman présent)
        """
        os.makedirs(folder, exist_ok=True)
        fmt = self._norm_fmt(fmt)

        if fmt not in ("OME-TIFF", "OME-ZARR"):
            raise ValueError(f"Unsupported spectro format: {fmt}")

        root_path = make_unique_path(folder, filename, ext="", default_stem="SPECTRO")
        os.makedirs(root_path, exist_ok=True)

        metadata_block = dict(dataset.get("metadata", {}) or {})

        metadata = {
            "created": self._now_iso(),
            "comment": comment or "",
            "format_version": str(dataset.get("version", "spectro_mock")),
            "grid_shape": list(dataset.get("grid_shape", ())),
            "axis_order": list(dataset.get("axis_order", ())),
            "serpentine": bool(dataset.get("serpentine", False)),
            "modalities": dict(metadata_block.get("modalities", {}) or {}),
            "acquisition_parameters": dict(metadata_block.get("acquisition_parameters", {}) or {}),
            "brillouin_parameters": dict(metadata_block.get("brillouin_parameters", {}) or {}),
            "raman_parameters": dict(metadata_block.get("raman_parameters", {}) or {}),
            "save_parameters": dict(metadata_block.get("save_parameters", {}) or {}),
            "raman_wavelengths_nm": (
                np.asarray(dataset.get("raman_wavelengths", []), dtype=np.float32).tolist()
                if "raman_wavelengths" in dataset else []
            ),
            "data_files": {},
            "axis_interpretation": {
                "brillouin_ome_tiff": {
                    "axes": "TCZYX",
                    "T": "repetition index (currently always 1 in spectro mock)",
                    "C": "linear scan index within each XY serpentine plane",
                    "Z": "mapping z index",
                    "Y": "camera image y",
                    "X": "camera image x",
                },
                "raman_ome_tiff": {
                    "axes": "ZYXS",
                    "Z": "mapping z index",
                    "Y": "mapping y index",
                    "X": "mapping x index",
                    "S": "spectral samples",
                },
            },
        }

        # Un seul fichier de positions dans tous les cas
        positions_csv_name = "positions.csv"
        self._write_positions_csv(os.path.join(root_path, positions_csv_name), dataset)
        metadata["data_files"]["positions"] = positions_csv_name

        # -------------------------
        # Brillouin
        # dataset["brillouin_images"] attendu en (Z, Y, X, H, W)
        # -------------------------
        if "brillouin_images" in dataset:
            arr = np.asarray(dataset["brillouin_images"], dtype=np.float32)
            # Handle legacy (pz, py, px, h, w) — prepend t=1 dimension
            if arr.ndim == 5:
                arr = arr[np.newaxis]
            pt, pz, py, px, h, w = arr.shape
            n_channels = py * px

            if fmt == "OME-TIFF":
                stack = np.zeros((pt, n_channels, pz, h, w), dtype=np.float32)

                order = np.asarray(dataset.get("acquisition_order_indices", []), dtype=np.int32)

                for linear_idx in range(len(order)):
                    row = order[linear_idx]
                    if len(row) == 4:
                        t_idx, z_idx, y_idx, x_idx = int(row[0]), int(row[1]), int(row[2]), int(row[3])
                    else:
                        t_idx, z_idx, y_idx, x_idx = 0, int(row[0]), int(row[1]), int(row[2])

                    if not (0 <= t_idx < pt and 0 <= z_idx < pz and 0 <= y_idx < py and 0 <= x_idx < px):
                        continue

                    c_idx = int(y_idx * px + x_idx)
                    stack[t_idx, c_idx, z_idx, :, :] = arr[t_idx, z_idx, y_idx, x_idx, :, :]

                out_name = "brillouin.ome.tif"
                tifffile.imwrite(
                    os.path.join(root_path, out_name),
                    stack,
                    photometric="minisblack",
                    metadata={
                        "axes": "TCZYX",
                        "Channel": [
                            {"Name": f"scan_idx_{i:04d}"}
                            for i in range(n_channels)
                        ],
                    },
                )
            else:
                out_name = "brillouin.zarr"
                zarr_path = os.path.join(root_path, out_name)
                root = zarr.open_group(zarr_path, mode="w")
                root.create_dataset(
                    "0",
                    data=arr,
                    shape=arr.shape,
                    chunks=(1, 1, 1, 1, min(256, arr.shape[-2]), min(256, arr.shape[-1])),
                    dtype=np.float32,
                    overwrite=True,
                )
                root.attrs["multiscales"] = [{
                    "version": "0.4",
                    "datasets": [{"path": "0"}],
                    "axes": [
                        {"name": "t", "type": "time"},
                        {"name": "z", "type": "space"},
                        {"name": "y", "type": "space"},
                        {"name": "x", "type": "space"},
                        {"name": "cam_y", "type": "space"},
                        {"name": "cam_x", "type": "space"},
                    ],
                }]
                root.attrs["comment"] = comment or ""
                root.attrs["raman_wavelengths_nm"] = (
                    np.asarray(dataset.get("raman_wavelengths", []), dtype=np.float32).tolist()
                    if "raman_wavelengths" in dataset else []
                )

            metadata["data_files"]["brillouin"] = out_name

        # -------------------------
        # Raman
        # dataset["raman_spectra"] attendu en (Z, Y, X, L)
        # -------------------------
        if "raman_spectra" in dataset:
            arr = np.asarray(dataset["raman_spectra"], dtype=np.float32)
            # Handle legacy (pz, py, px, L) — prepend t=1 dimension
            if arr.ndim == 4:
                arr = arr[np.newaxis]

            if fmt == "OME-TIFF":
                out_name = "raman.ome.tif"
                tifffile.imwrite(
                    os.path.join(root_path, out_name),
                    arr,
                    photometric="minisblack",
                    metadata={"axes": "TZYXS"},
                )
            else:
                out_name = "raman.zarr"
                zarr_path = os.path.join(root_path, out_name)
                root = zarr.open_group(zarr_path, mode="w")
                root.create_dataset(
                    "0",
                    data=arr,
                    shape=arr.shape,
                    chunks=(1, 1, 1, 1, min(1024, arr.shape[-1])),
                    dtype=np.float32,
                    overwrite=True,
                )
                root.attrs["multiscales"] = [{
                    "version": "0.4",
                    "datasets": [{"path": "0"}],
                    "axes": [
                        {"name": "t", "type": "time"},
                        {"name": "z", "type": "space"},
                        {"name": "y", "type": "space"},
                        {"name": "x", "type": "space"},
                        {"name": "lambda", "type": "spectral"},
                    ],
                }]
                root.attrs["comment"] = comment or ""

            metadata["data_files"]["raman"] = out_name

        self._write_json(os.path.join(root_path, "metadata.json"), metadata)

        return root_path