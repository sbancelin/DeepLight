import os
import time
import json
import csv
import numpy as np
from datetime import datetime
from threading import Lock

import h5py
import tifffile
import zarr

from ..widgets.Log_Widget import logger
from .Provenance import acquisition_provenance, physical_pixel_size_um
from .Scan_Types import infer_image_axes

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
    Build a unique path inside 'folder', guaranteeing the extension 'ext'.
    - empty name -> default_stem + HHMMSS + ext
    - name without an extension -> ext is appended
    - name that already has an extension:
        - if ext is given and differs, it is replaced by ext (controlled behaviour)
        - otherwise the existing extension is kept
    - if the file exists -> _001, _002, ... is inserted before the extension
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
    Handles manual saves and REC sessions.

    Save current view:
      - OME-TIFF (CYX)
      - OME-Zarr (TCZYX with T=1, Z=1)

    REC:
      - OME-TIFF: buffered in RAM, then written at the end
      - OME-Zarr: streamed as it goes (TCZYX)
    """

    def __init__(self):
        self._lock = Lock()

        # session REC
        self._rec_active = False
        self._rec_path = None
        self._rec_log_path = None
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
        if f in ("HDF5-BLS", "HDF5BLS", "H5-BLS", "H5BLS", "HD5F-BLS"):
            return "HDF5-BLS"
        return (fmt or "").strip().upper()

    #: A mosaic below this stops being worth a pyramid; the overhead of the
    #: extra levels outweighs what a viewer saves by not decoding it whole.
    PYRAMID_MIN_SIDE_PX = 2048

    @staticmethod
    def _pyramid_levels(height: int, width: int, arr=None):
        """Successive half-resolution copies, largest first.

        Returns an empty list for an image small enough that a viewer opens it
        whole anyway, so a modest mosaic is not padded with useless levels.
        """
        if arr is None:
            return []
        levels = []
        current = arr
        while (min(current.shape[-2], current.shape[-1]) > SaveManager.PYRAMID_MIN_SIDE_PX):
            current = current[..., ::2, ::2]
            levels.append(current)
        return levels

    def _make_omero_metadata(self, name: str, channels: list[str]) -> dict:
        return {
            "name": str(name),
            "channels": [{"label": str(c)} for c in channels],
        }

    def set_context(self, optics: dict | None = None, lasers: dict | None = None,
                    corrections: dict | None = None):
        """Optics, running lasers and dark/flat state for the next saves.

        Pushed in by MainWindow, which owns the panels, so this manager keeps
        knowing nothing about widgets.
        """
        self._optics = dict(optics or {})
        self._lasers = dict(lasers or {})
        self._corrections = dict(corrections or {})

    def _provenance(self, scan_params: dict, comment: str) -> dict:
        return acquisition_provenance(
            scan_params=scan_params or {},
            optics=getattr(self, "_optics", {}),
            lasers=getattr(self, "_lasers", {}),
            comment=comment,
            corrections=getattr(self, "_corrections", {}),
        )

    def _ome_physical_size(self, scan_params: dict) -> dict:
        """OME PhysicalSize fields, omitting any axis whose spacing is unknown.

        Stating a wrong size would be worse than stating none: a viewer trusts
        it and scale bars silently lie.
        """
        x, y, z = physical_pixel_size_um(scan_params)
        meta = {}
        if x:
            meta["PhysicalSizeX"] = float(x)
            meta["PhysicalSizeXUnit"] = "µm"
        if y:
            meta["PhysicalSizeY"] = float(y)
            meta["PhysicalSizeYUnit"] = "µm"
        if z:
            meta["PhysicalSizeZ"] = float(z)
            meta["PhysicalSizeZUnit"] = "µm"
        return meta

    def _ngff_scale_tczyx(self, scan_params: dict) -> list[dict]:
        """NGFF coordinateTransformations for a TCZYX dataset.

        Required by the spec for a multiscales entry, and the only place a
        Zarr reader looks for the voxel size. Unknown axes get 1.0, which is
        what NGFF means by "no scaling stated".
        """
        x, y, z = physical_pixel_size_um(scan_params)
        return [{
            "type": "scale",
            "scale": [1.0, 1.0, float(z or 1.0), float(y or 1.0), float(x or 1.0)],
        }]

    def _write_sidecar(self, path: str, comment: str, scan_params: dict,
                       channels=None, extra: dict | None = None) -> str:
        """Text record beside a TIFF: readable first, exhaustive second.

        The provenance block comes first and answers the questions one actually
        asks of an old file; the raw scan parameters stay underneath it so
        nothing is lost, rather than being the whole document as before.
        """
        sidecar = os.path.splitext(path)[0] + ".json"
        payload = {
            "provenance": self._provenance(scan_params, comment),
            "channels": [str(c) for c in (channels or [])],
            "scan_params": scan_params or {},
        }
        if extra:
            payload.update(extra)
        self._write_json(sidecar, payload)
        return sidecar

    def _start_run_log(self, path: str, fmt: str, reps: int, height: int, width: int):
        """Begin the log that travels with this acquisition.

        Beside the data and sharing its name, like the JSON sidecar: what the
        provenance states about the run, the log says about how it went --
        every warning, every retry, every device that answered slowly. Written
        as it goes, so it survives a run that never reaches its last frame.
        """
        self._rec_log_path = logger.open_run_file(os.path.splitext(path)[0] + ".log")
        logger.info(
            f"[REC] {fmt} session: {os.path.basename(path)} — "
            f"{reps} rep(s) x {self._rec_z} plane(s) x {len(self._rec_channels)} channel(s), "
            f"{height}x{width} px, channels {self._rec_channels}"
        )

    def _rec_frame_shape(self, scan_parameters: dict) -> tuple[int, int]:
        """(rows, columns) of one frame, as the backend actually builds it.

        Not pixel_values[1], pixel_values[0]: the fast galvo does not sweep the
        image's x axis. X-Galvo scans the sample's y direction, so a frame comes
        out transposed with respect to the order the scan rows are listed in,
        and a REC buffer shaped from that order refuses every frame written into
        it. Only a square field hid it. Derived here the same way the execution
        plan derives it, so the buffer and the frames cannot disagree.
        """
        pixel_values = list(scan_parameters.get("pixel_values", []) or [])
        axis_order = list(scan_parameters.get("axis_order", []) or [])
        active = [a for a in axis_order if a != "None"]

        if len(active) >= 2:
            rows = {name: i for i, name in enumerate(axis_order) if name != "None"}
            image_x_axis, image_y_axis = infer_image_axes(active[0], active[1])
            try:
                return (max(1, int(pixel_values[rows[image_y_axis]])),
                        max(1, int(pixel_values[rows[image_x_axis]])))
            except (KeyError, IndexError, TypeError, ValueError) as e:
                logger.warning(f"[REC] could not infer the frame shape ({e}); using the row order")

        dim_x = int(pixel_values[0]) if len(pixel_values) > 0 else 1
        dim_y = int(pixel_values[1]) if len(pixel_values) > 1 else 1
        return max(1, dim_y), max(1, dim_x)

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
        images_by_channel: channel -> 2D array, already transposed (exactly what is on screen)
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
        One multi-channel OME-TIFF file: axes CYX.
        -> works with Fiji Bio-Formats and with Napari.
        """
        path = make_unique_path(folder, filename, ext=".ome.tif", default_stem="VIEW")

        channels = list(images_by_channel.keys())
        stack = np.stack([np.asarray(images_by_channel[ch]) for ch in channels], axis=0).astype(np.float32, copy=False)
        # stack: (C, Y, X)

        ome_metadata = {
            "axes": "CYX",
            "Channel": [{"Name": str(ch)} for ch in channels],
        }
        # Sans PhysicalSize, Fiji et Napari ouvrent l'image en pixels et les
        # micromètres sont perdus : l'échelle doit être dans l'OME, pas
        # seulement dans le sidecar.
        ome_metadata.update(self._ome_physical_size(scan_params))

        with self._lock:
            tifffile.imwrite(
                path,
                stack,
                photometric="minisblack",
                metadata=ome_metadata,     # tifffile génère l’OME-XML
            )

            self._write_sidecar(path, comment, scan_params, channels=channels)

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
        Write a "simple" OME-Zarr for the current view:
        data shape = (T=1, C, Z=1, Y, X) with axes "tczyx"
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
            "datasets": [{
                "path": "0",
                # Requis par NGFF, et seul endroit où un lecteur Zarr trouve la
                # taille du voxel.
                "coordinateTransformations": self._ngff_scale_tczyx(scan_params),
            }],
            "axes": NGFF_AXES_TCZYX,
        }]
        root.attrs["omero"] = self._make_omero_metadata(os.path.basename(zarr_path), channels)

        root.attrs["comment"] = comment or ""
        root.attrs["created"] = self._now_iso()
        root.attrs["provenance"] = json.loads(
            json.dumps(self._provenance(scan_params, comment), default=str)
        )
        root.attrs["scan_params"] = json.loads(json.dumps(scan_params or {}, default=str))

        return zarr_path

    def save_snapshot_png(
        self,
        folder: str,
        filename: str,
        image,
        comment: str = "",
        scan_params: dict | None = None,
        channel: str | None = None,
        scalebar_um: float | None = None,
    ) -> str:
        """Write a displayed image as PNG, with the record it cannot hold.

        A PNG is a picture, not data: no pixel size, no acquisition parameters.
        The same JSON sidecar as the TIFF saves goes next to it, so a figure
        pasted into a notebook can still be traced back to its acquisition.
        """
        stem = (filename or "").strip()
        if channel and stem:
            stem = f"{stem}_{channel}"
        elif channel:
            stem = str(channel)

        path = make_unique_path(folder, stem, ext=".png", default_stem="SNAP")

        with self._lock:
            if not image.save(path, "PNG"):
                raise IOError(f"could not write {path}")

            extra = {"snapshot": {
                "width_px": int(image.width()),
                "height_px": int(image.height()),
                "channel": str(channel) if channel else None,
                "scalebar_um": scalebar_um,
            }}
            self._write_sidecar(
                path, comment, scan_params or {},
                channels=[channel] if channel else None,
                extra=extra,
            )

        return path

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
        Save a stitching mosaic as OME-TIFF.

        - 2D mosaic -> axes "YX"
        - 3D mosaic -> axes "ZYX" (one plane per Z/P stack-axis index)

        A JSON sidecar accompanies the file (scan params + mosaic + comment).
        """
        os.makedirs(folder, exist_ok=True)

        arr = np.asarray(mosaic, dtype=np.float32)
        if arr.ndim == 3 and arr.shape[0] == 1:
            arr = arr[0]

        axes = "ZYX" if arr.ndim == 3 else "YX"

        path = make_unique_path(folder, filename, ext=".ome.tif", default_stem="MOSAIC")

        levels = self._pyramid_levels(arr.shape[-2], arr.shape[-1], arr)

        with self._lock:
            # BigTIFF unconditionally: a mosaic is exactly the thing that walks
            # past the 4 GB the classic format can address, and finding that out
            # is a failed write at the end of a long run.
            mosaic_metadata = {"axes": axes}
            mosaic_metadata.update(self._ome_physical_size(scan_params))

            with tifffile.TiffWriter(path, bigtiff=True, ome=True) as tif:
                tif.write(
                    arr,
                    photometric="minisblack",
                    metadata=mosaic_metadata,
                    subifds=len(levels),
                    tile=(256, 256),
                )
                # Halved copies, so a viewer can pan a large mosaic without
                # decoding it whole.
                for level in levels:
                    tif.write(
                        level,
                        photometric="minisblack",
                        subfiletype=1,
                        tile=(256, 256),
                    )

            self._write_sidecar(
                path, comment, scan_params,
                extra={
                    "axes": axes,
                    "shape": list(arr.shape),
                    "mosaic_params": mosaic_params or {},
                },
            )

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
        - OME-TIFF: buffered in RAM (T,C,Z,Y,X), written at finish_rec_session()
        - OME-Zarr: streamed (written as it goes)
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

        Y, X = self._rec_frame_shape(scan_parameters)

        active_axes = list(scan_parameters.get("active_axes") or [])
        self._rec_n2 = int(pix[2]) if len(active_axes) >= 3 else 1
        self._rec_n3 = int(pix[3]) if len(active_axes) >= 4 else 1
        self._rec_z = self._rec_n2 * self._rec_n3

        self._rec_comment = comment or ""
        self._rec_scan_params = json.loads(json.dumps(scan_parameters, default=str))
        self._rec_log_path = None

        # -------- OME-TIFF (écrit à la fin) --------
        if fmt_u == "OME-TIFF":
            path = make_unique_path(folder, filename, ext=".ome.tif", default_stem="REC")
            self._rec_path = path
            self._start_run_log(path, fmt_u, reps, Y, X)

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
                self._start_run_log(path, fmt_u, reps, Y, X)

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
                    "datasets": [{
                        "path": "0",
                        "coordinateTransformations":
                            self._ngff_scale_tczyx(self._rec_scan_params),
                    }],
                    "axes": NGFF_AXES_TCZYX,
                }]
                root.attrs["omero"] = self._make_omero_metadata(os.path.basename(path), self._rec_channels)

                root.attrs["comment"] = self._rec_comment
                root.attrs["created"] = self._now_iso()
                root.attrs["channels"] = self._rec_channels
                root.attrs["provenance"] = json.loads(json.dumps(
                    self._provenance(self._rec_scan_params, self._rec_comment), default=str
                ))
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
        Streaming only (OME-Zarr):
          writes arr0[rep, c, z, :, :]
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
        - OME-TIFF: writes the file at the end (TCZYX stack)
        - OME-Zarr: nothing to do, the state is just reset

        Returns the path written, or None when no session was running: a script
        driving an acquisition has no status bar to read it from.
        """
        with self._lock:
            mode = self._rec_mode
            path = self._rec_path
            stack = self._rec_stack
            channels = list(self._rec_channels)
            comment = self._rec_comment
            scan_params = dict(self._rec_scan_params)
            had_log = self._rec_log_path is not None

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
            ome_metadata.update(self._ome_physical_size(scan_params))

            tifffile.imwrite(
                path,
                stack,  # (T,C,Z,Y,X)
                photometric="minisblack",
                metadata=ome_metadata,
            )

            self._write_sidecar(
                path, comment, scan_params,
                channels=channels,
                extra={"axes_numpy": "TCZYX"},
            )

        # After the write, so the log records whether the file actually landed,
        # and closed whatever happened -- an aborted run keeps the log of why.
        if had_log:
            if path:
                logger.info(f"[REC] session closed: {os.path.basename(path)}")
            logger.close_run_file()
            self._rec_log_path = None

        return path

    # ---------- Spectro dataset ----------
    #: Format version, matching HDF5_BLS_Version in the HDF5_BLS package.
    HDF5_BLS_VERSION = "1.0"

    def _write_hdf5_bls(self, path: str, dataset: dict, metadata: dict, comment: str) -> str:
        """Write the Brillouin dataset as a single HDF5-BLS 1.0 file.

        The layout follows the HDF5_BLS reference package (v1.0.1), read from
        its own source rather than guessed: a root "Brillouin" group carrying
        the format version, measure groups below it, and datasets whose meaning
        is given by a "Brillouin_type" attribute drawn from a fixed vocabulary.

            /Brillouin                     Brillouin_type="Root", HDF5_BLS_version
            /Brillouin/Measure             Brillouin_type="Measure" + metadata attrs
            /Brillouin/Measure/Raw data    Brillouin_type="Raw_data"
            /Brillouin/Measure/Frequency   Brillouin_type="Abscissa_5_5" + Unit
            /Brillouin/Measure/Positions   Brillouin_type="Other"

        The file is written with h5py directly rather than through the package:
        HDF5_BLS 1.0.1 cannot be imported on this project's Python because its
        wrapper uses f-strings with nested same-type quotes, which is 3.12+
        syntax, while the acquisition environment runs 3.10. Writing the same
        structure ourselves keeps the format without pinning the whole project
        to a newer interpreter.
        """
        with h5py.File(path, "w") as h5:
            root = h5.create_group("Brillouin")
            root.attrs["Brillouin_type"] = "Root"
            root.attrs["HDF5_BLS_version"] = self.HDF5_BLS_VERSION

            measure = root.create_group("Measure")
            measure.attrs["Brillouin_type"] = "Measure"

            if "brillouin_images" in dataset:
                arr = np.asarray(dataset["brillouin_images"], dtype=np.float32)
                if arr.ndim == 5:                    # (Z, Y, X, H, W) -> prepend T
                    arr = arr[np.newaxis]
                raw = measure.create_dataset(
                    "Raw data", data=arr, compression="gzip", compression_opts=4
                )
                raw.attrs["Brillouin_type"] = "Raw_data"
                raw.attrs["Dimensions"] = "T, Z, Y, X, camera_y, camera_x"

                wavelengths = np.asarray(
                    dataset.get("raman_wavelengths", []), dtype=np.float32
                )
                if wavelengths.size == arr.shape[-1]:
                    # Abscissa_<dim_start>_<dim_end>: the spectral axis is the
                    # last dimension of the raw data, hence 5 to 5.
                    axis = measure.create_dataset("Frequency", data=wavelengths)
                    axis.attrs["Brillouin_type"] = "Abscissa_5_5"
                    axis.attrs["Unit"] = "nm"

            positions = self._positions_array(dataset)
            if positions.size:
                pos = measure.create_dataset("Positions", data=positions)
                pos.attrs["Brillouin_type"] = "Other"
                pos.attrs["Dimensions"] = "point, (x, y, z)"
                pos.attrs["Unit"] = "um"

            # The reference implementation stores every attribute as text.
            measure.attrs["Name"] = os.path.basename(path)
            measure.attrs["Created"] = self._now_iso()
            measure.attrs["Comment"] = str(comment or "")
            measure.attrs["Producer"] = "DeepLight"
            measure.attrs["MEASURE.Grid_shape"] = str(metadata.get("grid_shape", []))
            measure.attrs["MEASURE.Axis_order"] = str(metadata.get("axis_order", []))
            measure.attrs["MEASURE.Serpentine"] = str(bool(metadata.get("serpentine", False)))

            for section in ("acquisition_parameters", "brillouin_parameters",
                            "raman_parameters", "save_parameters", "modalities"):
                self._write_h5_attrs(measure, section, metadata.get(section, {}))

            # Same provenance as the other formats, flattened into the attribute
            # naming HDF5-BLS uses, so a .h5 is as traceable as a .tif sidecar.
            self._write_h5_attrs(measure, "PROVENANCE", self._provenance({}, comment))

        return path

    @staticmethod
    def _write_h5_attrs(group, section: str, values: dict):
        """Flatten one metadata block into HDF5 attributes.

        Stored as text, as the reference implementation does, so anything
        nested keeps its JSON form rather than being dropped.
        """
        for key, value in dict(values or {}).items():
            name = f"{section}.{key}"
            if isinstance(value, (int, float, bool, str)):
                group.attrs[name] = str(value)
            else:
                group.attrs[name] = json.dumps(value, default=str)

    @staticmethod
    def _positions_array(dataset: dict) -> np.ndarray:
        """Stage position of each acquired point, in acquisition order.

        Same source as positions.csv, so the two formats describe the same
        points: each row is (x, y, z) in µm.
        """
        # No `or []` here: positions arrive as a numpy array, whose truth value
        # is ambiguous and raises rather than falling back.
        positions = dataset.get("acquisition_order_positions_um")
        if positions is None:
            return np.zeros((0,), dtype=np.float32)
        try:
            return np.asarray(positions, dtype=np.float32)
        except (TypeError, ValueError):
            return np.zeros((0,), dtype=np.float32)

    def save_spectro_dataset(
        self,
        folder: str,
        filename: str,
        comment: str,
        dataset: dict,
        fmt: str = "OME-TIFF",
    ) -> str:
        """
        Save a spectro dataset in a plain layout:

        OME-TIFF:
            <root>/
                metadata.json
                positions.csv
                brillouin.ome.tif   (when present)
                raman.ome.tif       (when present)

        OME-ZARR:
            <root>/
                metadata.json
                positions.csv
                brillouin.zarr      (when present)
                raman.zarr          (when present)
                raman_wavelengths.npy (when Raman is present)
        """
        os.makedirs(folder, exist_ok=True)
        fmt = self._norm_fmt(fmt)

        if fmt not in ("OME-TIFF", "OME-ZARR", "HDF5-BLS"):
            raise ValueError(f"Unsupported spectro format: {fmt}")

        if fmt == "HDF5-BLS":
            # One self-describing file rather than a folder of side-car files.
            path = make_unique_path(folder, filename, ext=".h5", default_stem="SPECTRO")
            block = dict(dataset.get("metadata", {}) or {})
            block.update({
                "grid_shape": list(dataset.get("grid_shape", ())),
                "axis_order": list(dataset.get("axis_order", ())),
                "serpentine": bool(dataset.get("serpentine", False)),
            })
            return self._write_hdf5_bls(path, dataset, block, comment)

        root_path = make_unique_path(folder, filename, ext="", default_stem="SPECTRO")
        os.makedirs(root_path, exist_ok=True)

        metadata_block = dict(dataset.get("metadata", {}) or {})

        metadata = {
            # The spectro grid has its own geometry below, so the provenance
            # here is the part that does not depend on it: build, optics, lasers.
            "provenance": self._provenance({}, comment),
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