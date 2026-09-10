"""Run an acquisition without the GUI.

    from DeepLight import Axis, Recipe, run

    result = run(Recipe(
        axes=[Axis("X-Galvo", 512, 100.0), Axis("Y-Galvo", 512, 100.0)],
        dwell_us=4.0, detectors=["PMT-Vis"],
        folder=r"D:\\data", filename="field",
    ), backend="nidaq")

The window is one caller of the manager layer; this is another. Nothing here
duplicates the acquisition itself -- the same ScanManager builds the plan, the
same AcquisitionManager drives the backend, the same SaveManager writes the
file with the same provenance. What the window contributes is the wiring
between them and a place to put the pixels, and that is what this module
provides instead, in about fifty lines.

Qt is still underneath: the managers are QObjects, the backend runs in its own
QThread, and moves are timed by the event loop. So a session creates a
QCoreApplication if none exists and spins that loop while it waits. No widget
is ever built, nothing is shown, and a script sees only blocking calls.
"""

from __future__ import annotations

import sys
import time
from dataclasses import dataclass, field

import numpy as np
from PySide6.QtCore import QCoreApplication, QEventLoop, QTimer

from .gui.managers.Acquisition_Manager import AcquisitionManager
from .gui.managers.Hardware_Manager import HardwareManager
from .gui.managers.Microscopes import create_microscope_backend
from .gui.managers.Save_Manager import SaveManager
from .gui.managers.Scan_manager import ScanManager
from .gui.managers.Settings_Manager import SettingsManager
from .gui.widgets.Log_Widget import logger, open_session_log
from .recipe import Axis, Recipe

__all__ = ["Axis", "Recipe", "RunResult", "Session", "run"]

#: How much longer than its own estimate a run is allowed to take before the
#: wait gives up. Generous on purpose: the point is to catch a wedged backend,
#: not to police a slow stage.
TIMEOUT_MARGIN = 3.0
TIMEOUT_FLOOR_S = 60.0


@dataclass
class RunResult:
    """What a finished acquisition gives back.

    `images` holds the last frame of each channel, which for a single XY scan
    is the whole acquisition and for a stack is its final plane -- the file is
    where the complete stack lives.
    """

    path: str | None
    channels: list[str]
    images: dict = field(default_factory=dict)
    frames: int = 0
    duration_s: float = 0.0
    scan_parameters: dict = field(default_factory=dict)


class Session:
    """An open microscope, driven from a script.

    Holds the hardware and the managers for as long as it is alive, so a batch
    of runs pays for the connection once. Use it as a context manager, or call
    close() -- a real backend keeps serial ports open until you do.
    """

    def __init__(self, backend: str = "mock", session_log: bool = True):
        self._app = QCoreApplication.instance()
        self._owns_app = self._app is None
        if self._owns_app:
            self._app = QCoreApplication(sys.argv[:1] or ["deeplight"])

        # A script has no log panel to watch, so the file is the only trace it
        # leaves. Skipped when a window already opened one, and on request for
        # a caller that manages its own logging.
        self._owns_session_log = False
        if session_log and self._owns_app:
            self._owns_session_log = open_session_log() is not None

        self.backend_name = str(backend or "mock").lower()
        self.settings = SettingsManager()
        self.hardware = HardwareManager(backend_name=self.backend_name,
                                        settings_manager=self.settings)
        self.positioner = self.hardware.create_positioner_manager()
        self.scan_manager = ScanManager()
        self.save_manager = SaveManager()

        self.acquisition = AcquisitionManager(
            microscope_backend=create_microscope_backend(self.backend_name),
        )
        self.acquisition.microscope.positioner_manager = self.positioner

        self._last_images: dict = {}
        self._frames = 0
        self._saving = False
        self._finished = False
        self._rec_path: str | None = None
        self._closed = False

        self._connect()
        logger.info(f"[API] headless session open on the {self.backend_name} backend")

    # ---- wiring -------------------------------------------------------

    def _connect(self):
        """The connections the window makes that an acquisition actually needs.

        Deliberately not all of them: samples_progress only feeds the waveform
        visualisers, and subscribing to it here would accumulate plot buffers
        nobody ever drains.
        """
        acq = self.acquisition
        acq.shutter_requested.connect(self.hardware.set_shutter)
        acq.stepper_move_requested.connect(self.positioner.move_from_scan)
        acq.image_updated.connect(self._on_image)
        acq.acquisition_frame.connect(self._on_frame)
        acq.acquisition_stopped.connect(self._on_stopped)

    def _on_image(self, channel: str, image: np.ndarray):
        self._last_images[str(channel)] = np.asarray(image)

    def _on_frame(self, rep: int, idx_tuple: tuple, images_by_channel: dict):
        self._frames += 1
        if not self._saving:
            return
        try:
            self.save_manager.append_rec_frame(rep, idx_tuple, images_by_channel)
        except Exception as e:
            logger.error(f"[API] could not append frame {idx_tuple}: {e}")

    def _on_stopped(self):
        try:
            self.scan_manager.stop_stream()
        except Exception as e:
            logger.debug(f"[API] stop_stream: {e}")

        if self._saving:
            try:
                self._rec_path = self.save_manager.finish_rec_session()
            except Exception as e:
                logger.error(f"[API] could not close the save session: {e}")
            self._saving = False

        self._finished = True

    # ---- waiting ------------------------------------------------------

    def _wait_until(self, predicate, timeout_s: float, what: str, poll_ms: int = 20):
        """Spin the event loop until `predicate` holds, or give up.

        Polled rather than waiting on a signal: the condition is checked once
        before the loop is entered too, so nothing is missed if it already
        became true while we were setting up.
        """
        if predicate():
            return

        loop = QEventLoop()
        deadline = time.monotonic() + float(timeout_s)
        timed_out = []

        def tick():
            if predicate():
                loop.quit()
            elif time.monotonic() > deadline:
                timed_out.append(True)
                loop.quit()

        timer = QTimer()
        timer.setInterval(int(poll_ms))
        timer.timeout.connect(tick)
        timer.start()
        loop.exec()
        timer.stop()

        if timed_out:
            raise TimeoutError(f"{what} did not finish within {timeout_s:.0f} s")

    # ---- stage --------------------------------------------------------

    def positions(self) -> dict:
        """Relative position of every axis, in µm (degrees for Polarization)."""
        out = {}
        for axis in self.positioner.axes:
            try:
                out[axis] = float(self.positioner.get_rel_pos(axis))
            except Exception:
                pass
        return out

    def move(self, axis: str, position_um: float, speed_um_s: float | None = None,
             timeout_s: float = 120.0) -> float:
        """Move one axis and return once it is there.

        Takes either the positioner's short name ("z") or the scan name
        ("Z-Vcoil"). Blocking, because a script that starts a scan before the
        stage has arrived images the wrong place.
        """
        name = self.positioner.axis_from_scan_name(axis) or str(axis)
        if not self.positioner.has_axis(name):
            raise ValueError(f"Unknown axis {axis!r}; known axes are {list(self.positioner.axes)}")

        target = float(position_um)
        speed = float(speed_um_s) if speed_um_s else float(self.positioner.get_max_speed(name))
        tolerance = max(float(self.positioner.get_tolerance(name)), 1e-9)

        self.positioner.move_to_rel(name, target, speed)
        self._wait_until(
            lambda: abs(float(self.positioner.get_rel_pos(name)) - target) <= tolerance,
            timeout_s, f"move of {name} to {target:g}",
        )
        return float(self.positioner.get_rel_pos(name))

    # ---- acquisition --------------------------------------------------

    def _default_timeout_s(self, scan_parameters: dict) -> float:
        """The run's own estimate, tripled: a wait, not a deadline."""
        estimate = 0.0
        try:
            plan = self.scan_manager.get_last_execution_plan()
            if plan is not None and float(plan.sample_rate_hz) > 0:
                estimate = float(plan.total_samples) / float(plan.sample_rate_hz)
        except Exception:
            estimate = 0.0

        if estimate <= 0.0:
            dwell = float(scan_parameters.get("dwell_time", 0.0) or 0.0)
            pixels = int(scan_parameters.get("total_pixels", 0) or 0)
            settle = float(scan_parameters.get("sample_settle_time_s", 0.0) or 0.0)
            estimate = (dwell + settle) * pixels

        reps = max(1, int(scan_parameters.get("repetitions", 1) or 1))
        return max(TIMEOUT_FLOOR_S, estimate * reps * TIMEOUT_MARGIN)

    def run(self, recipe: Recipe, timeout_s: float | None = None) -> RunResult:
        """Run one acquisition to completion and return what it produced.

        A recipe with a folder is written exactly as the window would write it,
        provenance included; one without is kept in memory only.
        """
        if self._closed:
            raise RuntimeError("This session is closed")
        if self.acquisition.is_running:
            raise RuntimeError("An acquisition is already running")

        scan_parameters = recipe.to_scan_parameters(positions_um=self.positions())

        # On real hardware this refuses a scan that would drive a stage past
        # its limits -- the check the scan panel does before enabling its
        # button, which a script would otherwise skip.
        self.hardware.validate_scan_positions(self.positioner, scan_parameters)

        self._last_images = {}
        self._frames = 0
        self._finished = False
        self._rec_path = None

        self.save_manager.set_context(optics=recipe.optics, lasers={})

        self._saving = False
        if recipe.folder:
            self._saving = bool(self.save_manager.start_rec_session(
                fmt=recipe.fmt,
                folder=recipe.folder,
                filename=recipe.filename,
                scan_parameters=scan_parameters,
                comment=recipe.comment,
                channels=list(scan_parameters["active_channels"]),
            ))
            if not self._saving:
                raise IOError(f"Could not open a save session in {recipe.folder!r}")

        if str(scan_parameters.get("scan_kind")) == "laser":
            self.scan_manager.prepare_run(scan_parameters, mode="acquisition")
            self.acquisition.set_execution_plan(self.scan_manager.get_last_execution_plan())
        else:
            self.scan_manager.stop_stream()

        if timeout_s is None:
            timeout_s = self._default_timeout_s(scan_parameters)

        started = time.monotonic()
        self.acquisition.start_acquisition(scan_parameters)

        try:
            self._wait_until(lambda: self._finished, timeout_s, "the acquisition")
        except TimeoutError:
            self.acquisition.stop_acquisition()
            self._wait_until(lambda: self._finished, 10.0, "the acquisition stopping")
            raise

        return RunResult(
            path=self._rec_path,
            channels=list(scan_parameters["active_channels"]),
            images=dict(self._last_images),
            frames=self._frames,
            duration_s=time.monotonic() - started,
            scan_parameters=scan_parameters,
        )

    def stop(self):
        """Interrupt a running acquisition, as the Stop button does."""
        self.acquisition.stop_acquisition()
        try:
            self.positioner.stop_all()
        except Exception as e:
            logger.debug(f"[API] stop_all: {e}")

    # ---- lifetime -----------------------------------------------------

    def close(self):
        if self._closed:
            return
        self._closed = True

        try:
            self.acquisition.close()
        except Exception as e:
            logger.error(f"[API] closing the acquisition failed: {e}")

        try:
            self.hardware.close()
        except Exception as e:
            logger.error(f"[API] closing the hardware failed: {e}")

        logger.info("[API] headless session closed")

        if self._owns_session_log:
            logger.close_files()
            self._owns_session_log = False

    def __enter__(self) -> "Session":
        return self

    def __exit__(self, *exc_info):
        self.close()
        return False


def run(recipe: Recipe, backend: str = "mock", timeout_s: float | None = None) -> RunResult:
    """Run one acquisition on a session opened just for it.

    The convenient form for a single scan. A sweep or a batch should open a
    Session once and call its run() repeatedly, so the hardware is connected
    once rather than per acquisition.
    """
    with Session(backend=backend) as session:
        return session.run(recipe, timeout_s=timeout_s)
