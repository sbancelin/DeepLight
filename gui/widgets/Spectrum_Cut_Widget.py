"""Right-hand panel of the Spectro tab: the Brillouin spectrum, cut out of the image.

In the VIPA spectrometer the spectrum runs along the diagonal of the camera
frame, so a cut along it *is* the spectrum. This panel draws that cut on the
Brillouin image -- a line, on the diagonal by default, whose ends can be dragged
onto the actual orders -- and plots the profile under it for every frame, live
included, which is what makes the extinction adjustable by eye.

- the profile can be averaged over a few pixels across the line (Width),
- shown on a log scale, where a residual elastic peak and the Brillouin peaks
  fit on one plot,
- and frozen as a reference (Hold) to compare a setting with the previous one.

The line's ends are kept as fractions of the frame, so a change of crop or
binning keeps it on the same part of the spectrum.

Distances are measured on the plot: in Measure mode a drag draws a segment,
labelled with its spacing along the spectrum -- in GHz once the scale is set,
typed in or calibrated from a segment between two peaks a known spacing apart
(two orders of the elastic peak, one free spectral range apart). The scale is
kept per camera pixel, so one calibration holds at every binning.
"""

from __future__ import annotations

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import QLocale, QPointF, Qt, QTimer, Slot
from PySide6.QtWidgets import (
    QCheckBox, QDoubleSpinBox, QHBoxLayout, QInputDialog, QLabel, QPushButton, QSizePolicy,
    QSpinBox, QVBoxLayout, QWidget,
)

from ._pg_common import DoubleClickAxis, DoubleClickViewBox
from .Log_Widget import logger
from .Spectro_Widget import _BUTTON_STYLE, _BUTTON_STYLE_TOGGLE, _CHECKBOX_STYLE, _apply_spinbox_palette

_PROFILE_COLOR = "#FF7700"
_LINE_COLOR = (60, 200, 255)
#: Measuring segments take these in turn, to tell them apart.
_SEGMENT_COLORS = ((255, 225, 70), (120, 235, 120), (255, 120, 230), (150, 190, 255))

#: Ends of the two diagonals, as fractions of (width, height) in image pixels.
#: The image is shown with y upwards, so (0, 0) is its bottom-left corner.
DIAGONAL = ((0.0, 0.0), (1.0, 1.0))        # bottom-left -> top-right on screen
ANTI_DIAGONAL = ((0.0, 1.0), (1.0, 0.0))   # top-left -> bottom-right on screen


def binning_factor(binning) -> int:
    """'2x2' (or 2) -> 2: how many camera pixels a frame pixel spans, each way."""
    try:
        return max(1, int(str(binning).lower().split("x")[0]))
    except (TypeError, ValueError):
        return 1


def line_profile(image, p0, p1, width: int = 1):
    """Sample ``image`` along the segment p0 -> p1, in image pixel coordinates.

    Coordinates are those of a pyqtgraph ImageItem: pixel (row r, column c)
    covers x in [c, c+1), y in [r, r+1). One sample per pixel of length; with
    ``width`` > 1, each sample is the mean of ``width`` samples spread across
    the line, one pixel apart. Samples falling outside the frame are ignored.

    Returns (distance_px, values), values NaN where nothing was inside.
    """
    img = np.asarray(image)
    if img.ndim != 2 or img.size == 0:
        raise ValueError(f"line_profile needs a 2D image, got shape {img.shape}")
    h, w = img.shape
    x0, y0 = float(p0[0]), float(p0[1])
    x1, y1 = float(p1[0]), float(p1[1])
    dx, dy = x1 - x0, y1 - y0
    length = float(np.hypot(dx, dy))
    n = max(2, int(np.ceil(length)) + 1)
    t = np.linspace(0.0, 1.0, n)
    xs = x0 + t * dx
    ys = y0 + t * dy

    width = max(1, int(width))
    if width > 1 and length > 0:
        nx, ny = -dy / length, dx / length          # unit normal to the line
        offsets = np.arange(width, dtype=float) - (width - 1) / 2.0
        xs = xs[None, :] + offsets[:, None] * nx
        ys = ys[None, :] + offsets[:, None] * ny
    else:
        xs = xs[None, :]
        ys = ys[None, :]

    cols = np.floor(xs).astype(np.int64)
    rows = np.floor(ys).astype(np.int64)
    inside = (cols >= 0) & (cols < w) & (rows >= 0) & (rows < h)
    samples = np.full(cols.shape, np.nan, dtype=np.float64)
    samples[inside] = img[rows[inside], cols[inside]]

    with np.errstate(invalid="ignore"):
        counts = inside.sum(axis=0)
        sums = np.nansum(samples, axis=0)
        values = np.where(counts > 0, sums / np.maximum(counts, 1), np.nan)
    return t * length, values


class _NoWheelSpinBox(QDoubleSpinBox):
    """A value the mouse wheel only changes once the field has been clicked into:
    scrolling the panel past the scale must not recalibrate it on the way."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFocusPolicy(Qt.StrongFocus)          # the wheel gives no focus

    def wheelEvent(self, event):
        if self.hasFocus():
            super().wheelEvent(event)
        else:
            event.ignore()                           # scroll the panel instead


class _MeasureViewBox(DoubleClickViewBox):
    """While ``measuring`` is on, a left drag draws a segment instead of panning.

    ``on_draw(phase, start, end)`` gets the drag in view coordinates, phase
    being "start", "move" or "finish".
    """

    def __init__(self, on_double_click=None, on_draw=None):
        super().__init__(on_double_click)
        self.measuring = False
        self._on_draw = on_draw

    def mouseDragEvent(self, ev, axis=None):
        if (not self.measuring or axis is not None or self._on_draw is None
                or ev.button() != Qt.LeftButton):
            super().mouseDragEvent(ev, axis)
            return
        ev.accept()
        start = self.mapSceneToView(ev.buttonDownScenePos())
        end = self.mapSceneToView(ev.scenePos())
        phase = "start" if ev.isStart() else "finish" if ev.isFinish() else "move"
        self._on_draw(phase, start, end)


class _Segment:
    """A measuring segment on the plot, and the label giving its spacing."""

    def __init__(self, roi, label, color):
        self.roi = roi
        self.label = label
        self.color = color

    def ends(self):
        """Its two ends, in the plot's (view) coordinates."""
        return [self.roi.mapToParent(h.pos()) for h in self.roi.getHandles()[:2]]

    def dx(self) -> float:
        """Its extent along the spectrum, in frame pixels."""
        p0, p1 = self.ends()
        return abs(float(p1.x()) - float(p0.x()))


class SpectrumCutWidget(QWidget):
    #: Most frequent replot; the live view can run faster than that.
    REFRESH_MS = 50

    def __init__(self, parent=None):
        super().__init__(parent)
        self._image_view = None
        self._roi = None
        self._fractions = DIAGONAL
        self._frame_shape = None
        self._last_frame = None
        self._dirty = False
        self._profile = None          # (distance, values) last plotted
        self._reference = None        # (distance, values) held by Hold

        # Frequency scale. Per camera pixel, so a change of binning keeps it.
        self._ghz_per_camera_px = 0.0  # 0: not set, the axis stays in pixels
        self._binning = 1
        self._calibration_ghz = 0.0    # last known spacing typed in, offered again
        self.settings_manager = None

        self._segments: list[_Segment] = []
        self._current_segment = None   # the one Calibrate uses: last drawn or moved
        self._drawing = None           # the segment a drag is drawing
        self._segment_count = 0

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(self.REFRESH_MS)
        self._timer.timeout.connect(self._refresh)

        self._build()

    # ------------------------------------------------------------------
    # layout
    # ------------------------------------------------------------------
    def _build(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(4)

        row = QHBoxLayout()
        row.setSpacing(4)
        self.button_show = QPushButton("Cut")
        self.button_show.setCheckable(True)
        self.button_show.setStyleSheet(_BUTTON_STYLE_TOGGLE)
        self.button_show.setToolTip(
            "Draw the cut on the Brillouin image and plot the profile under it,\n"
            "for every frame (snap, live, map). Drag its ends onto the spectrum."
        )
        self.button_show.toggled.connect(self._on_show_toggled)
        row.addWidget(self.button_show)

        self.button_diagonal = QPushButton("╱")
        self.button_diagonal.setStyleSheet(_BUTTON_STYLE)
        self.button_diagonal.setFixedWidth(28)
        self.button_diagonal.setToolTip("Put the cut back on the diagonal, bottom-left to top-right.")
        self.button_diagonal.clicked.connect(lambda: self.set_cut_fractions(*DIAGONAL))
        row.addWidget(self.button_diagonal)

        self.button_anti = QPushButton("╲")
        self.button_anti.setStyleSheet(_BUTTON_STYLE)
        self.button_anti.setFixedWidth(28)
        self.button_anti.setToolTip("Put the cut on the other diagonal, top-left to bottom-right.")
        self.button_anti.clicked.connect(lambda: self.set_cut_fractions(*ANTI_DIAGONAL))
        row.addWidget(self.button_anti)

        row.addWidget(QLabel("W"))
        self.spin_width = QSpinBox()
        self.spin_width.setRange(1, 51)
        self.spin_width.setValue(3)
        self.spin_width.setToolTip("Width of the cut (px): each point is the mean across it.")
        _apply_spinbox_palette(self.spin_width)
        self.spin_width.valueChanged.connect(lambda _v: self._schedule())
        row.addWidget(self.spin_width)
        row.addStretch()
        layout.addLayout(row)

        row = QHBoxLayout()
        row.setSpacing(6)
        self.cb_log = QCheckBox("Log")
        self.cb_log.setStyleSheet(_CHECKBOX_STYLE)
        self.cb_log.setToolTip("Logarithmic intensity: a residual elastic peak and the Brillouin peaks on one plot.")
        self.cb_log.toggled.connect(self._on_log_toggled)
        row.addWidget(self.cb_log)

        self.button_hold = QPushButton("Hold")
        self.button_hold.setCheckable(True)
        self.button_hold.setStyleSheet(_BUTTON_STYLE_TOGGLE)
        self.button_hold.setToolTip("Freeze the current profile as a dashed reference; click again to drop it.")
        self.button_hold.toggled.connect(self._on_hold_toggled)
        row.addWidget(self.button_hold)

        self.button_measure = QPushButton("Measure")
        self.button_measure.setCheckable(True)
        self.button_measure.setStyleSheet(_BUTTON_STYLE_TOGGLE)
        self.button_measure.setToolTip(
            "Drag on the plot to draw a segment: its label is its spacing along\n"
            "the spectrum (along x only), in GHz once the scale is set.\n"
            "Segments stay; drag one, or its ends, to adjust it, right-click it\n"
            "to remove it. Turn Measure off to pan the plot by dragging again."
        )
        self.button_measure.toggled.connect(self._on_measure_toggled)
        row.addWidget(self.button_measure)

        self.button_clear = QPushButton("Clear")
        self.button_clear.setStyleSheet(_BUTTON_STYLE)
        self.button_clear.setToolTip("Remove every measuring segment.")
        self.button_clear.clicked.connect(self.clear_segments)
        row.addWidget(self.button_clear)
        row.addStretch()
        layout.addLayout(row)

        row = QHBoxLayout()
        row.setSpacing(4)
        row.addWidget(QLabel("Scale"))
        self.spin_scale = _NoWheelSpinBox()
        self.spin_scale.setLocale(QLocale.c())
        self.spin_scale.setDecimals(6)
        self.spin_scale.setRange(0.0, 100.0)
        self.spin_scale.setSingleStep(0.001)
        self.spin_scale.setSuffix(" GHz/px")
        self.spin_scale.setSpecialValueText("not set (px)")     # shown at 0
        self.spin_scale.setKeyboardTracking(False)
        self.spin_scale.setButtonSymbols(QDoubleSpinBox.NoButtons)
        self.spin_scale.setMinimumWidth(110)
        self.spin_scale.setToolTip(
            "Frequency per camera pixel along the cut. Per camera pixel, not per\n"
            "frame pixel: the same value holds at every binning.\n"
            "Type it and press Enter, or calibrate it from a segment. 0: not set,\n"
            "the axis stays in pixels.\n"
            "It holds for a cut at the angle it was calibrated at; and a VIPA's\n"
            "dispersion is not quite linear, so it is exact near the peaks used."
        )
        _apply_spinbox_palette(self.spin_scale)
        self.spin_scale.valueChanged.connect(self._on_scale_edited)
        row.addWidget(self.spin_scale)

        self.button_calibrate = QPushButton("Calibrate…")
        self.button_calibrate.setStyleSheet(_BUTTON_STYLE)
        self.button_calibrate.setToolTip(
            "Set the scale from the last segment drawn or moved: draw it between\n"
            "two peaks a known spacing apart (two orders of the elastic peak are\n"
            "one free spectral range apart), then give that spacing in GHz."
        )
        self.button_calibrate.clicked.connect(self._on_calibrate_clicked)
        row.addWidget(self.button_calibrate)
        row.addStretch()
        layout.addLayout(row)

        self._view_box = _MeasureViewBox(self.reset_view, self._on_measure_drag)
        self.plot = pg.PlotWidget(
            viewBox=self._view_box,
            axisItems={
                "left": DoubleClickAxis("left", lambda _o: self.reset_view()),
                "bottom": DoubleClickAxis("bottom", lambda _o: self.reset_view()),
            },
        )
        self.plot.setBackground("#2b2b2b")
        self.plot.showGrid(x=True, y=True, alpha=0.3)
        self.plot.setLabel("left", "Intensity", color="w")
        for axis_name in ("left", "bottom"):
            axis = self.plot.getAxis(axis_name)
            axis.setTextPen(pg.mkPen("w"))
            axis.setPen(pg.mkPen("w"))
        # GHz stay GHz: no "mGHz" when zoomed in on a peak.
        self.plot.getAxis("bottom").enableAutoSIPrefix(False)
        self.plot.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.plot.setMinimumHeight(120)
        self._reference_curve = self.plot.plot(pen=pg.mkPen((170, 170, 170), width=1, style=Qt.DashLine))
        self._curve = self.plot.plot(pen=pg.mkPen(_PROFILE_COLOR, width=1.5))
        layout.addWidget(self.plot, stretch=1)
        self._apply_scale()

        self.label_info = QLabel("Turn the cut on, then Snap or Live in the Spectro tab.")
        self.label_info.setStyleSheet("color: #b0b0b0;")
        self.label_info.setWordWrap(True)
        layout.addWidget(self.label_info)

    def reset_view(self):
        item = self.plot.getPlotItem()
        item.enableAutoRange(axis="x", enable=True)
        item.enableAutoRange(axis="y", enable=True)
        item.vb.autoRange()

    # ------------------------------------------------------------------
    # the line on the image
    # ------------------------------------------------------------------
    def set_image_view(self, image_view):
        """The (Brillouin) ImageView the cut is drawn on and read from."""
        self._remove_roi()
        self._image_view = image_view
        if self.button_show.isChecked():
            self._place_roi()

    def set_cut_fractions(self, p0, p1):
        """Put the cut's ends at these fractions of the frame's (width, height)."""
        self._fractions = ((float(p0[0]), float(p0[1])), (float(p1[0]), float(p1[1])))
        if self.button_show.isChecked():
            self._place_roi()
            self._schedule()

    def _frame_size(self):
        if self._frame_shape is not None:
            return self._frame_shape
        if self._image_view is not None:
            img = self._image_view.getImageItem().image
            if img is not None and np.ndim(img) == 2:
                return tuple(np.shape(img))
        return None

    def _remove_roi(self):
        if self._roi is not None and self._image_view is not None:
            try:
                self._image_view.getView().removeItem(self._roi)
            except Exception as e:
                logger.debug(f"[SpectrumCut] line not removed: {e}")
        self._roi = None

    def _place_roi(self):
        """(Re)draw the line at the stored fractions of the current frame."""
        self._remove_roi()
        shape = self._frame_size()
        if self._image_view is None or shape is None:
            return
        h, w = shape
        item = self._image_view.getImageItem()
        # The line lives beside the image, in the view box's child group. Map
        # item to item rather than through the scene: that does not depend on
        # the view's zoom, nor on the view having a size yet.
        parent = self._image_view.getView().getViewBox().childGroup
        ends = [item.mapToItem(parent, QPointF(fx * w, fy * h)) for fx, fy in self._fractions]
        self._roi = pg.LineSegmentROI(
            positions=[(ends[0].x(), ends[0].y()), (ends[1].x(), ends[1].y())],
            pen=pg.mkPen(_LINE_COLOR, width=2),
            hoverPen=pg.mkPen((140, 230, 255), width=3),
            movable=True,
        )
        self._roi.setZValue(30)
        self._image_view.getView().addItem(self._roi)
        self._roi.sigRegionChanged.connect(self._schedule)
        self._roi.sigRegionChangeFinished.connect(self._remember_fractions)

    def _ends_px(self):
        """The line's ends in image pixel coordinates."""
        item = self._image_view.getImageItem()
        ends = []
        for handle in self._roi.getHandles()[:2]:
            p = item.mapFromItem(self._roi, handle.pos())
            ends.append((p.x(), p.y()))
        return ends

    def _remember_fractions(self, *_args):
        shape = self._frame_size()
        if self._roi is None or shape is None:
            return
        h, w = shape
        (x0, y0), (x1, y1) = self._ends_px()
        self._fractions = ((x0 / w, y0 / h), (x1 / w, y1 / h))

    # ------------------------------------------------------------------
    # frames
    # ------------------------------------------------------------------
    @Slot(object)
    def on_frame(self, image):
        """Every Brillouin frame. Cheap when the cut is off: the frame is kept, nothing more."""
        self._last_frame = image
        self._schedule()

    def _schedule(self, *_args):
        if self.button_show.isChecked() and not self._timer.isActive():
            self._timer.start()

    @Slot()
    def _refresh(self):
        image = self._last_frame
        if image is None or self._image_view is None or not self.button_show.isChecked():
            return
        try:
            img = np.asarray(image, dtype=np.float32)
            if img.ndim != 2 or img.size == 0:
                return
            if img.shape != self._frame_shape:
                self._frame_shape = img.shape
                self._place_roi()            # same fractions, new frame size
            if self._roi is None:
                self._place_roi()
            if self._roi is None:
                return
            p0, p1 = self._ends_px()
            distance, values = line_profile(img, p0, p1, self.spin_width.value())
            self._profile = (distance, values)
            self._curve.setData(distance, self._for_display(values))
            self._update_info(distance, values)
        except Exception as e:
            logger.debug(f"[SpectrumCut] profile not computed: {e}")

    def _for_display(self, values):
        values = np.asarray(values, dtype=np.float64)
        # Log scale: a dark-subtracted frame has zeros and negatives.
        return np.clip(values, 1.0, None) if self.cb_log.isChecked() else values

    def _update_info(self, distance, values):
        finite = values[np.isfinite(values)]
        if finite.size == 0:
            self.label_info.setText("The cut lies outside the frame.")
            return
        length = float(distance[-1]) if len(distance) else 0.0
        scale = self.ghz_per_frame_px()
        span = "" if scale is None else f" = {length * scale:.1f} GHz"
        self.label_info.setText(
            f"length {length:.0f} px{span} · max {float(np.max(finite)):.0f} · "
            f"min {float(np.min(finite)):.0f}"
        )

    # ------------------------------------------------------------------
    # toggles
    # ------------------------------------------------------------------
    @Slot(bool)
    def _on_show_toggled(self, checked: bool):
        if checked:
            self._place_roi()
            self._schedule()
        else:
            self._remove_roi()

    @Slot(bool)
    def _on_log_toggled(self, checked: bool):
        self.plot.setLogMode(y=bool(checked))
        self._segments_to_log(bool(checked))
        if self._profile is not None:
            self._curve.setData(self._profile[0], self._for_display(self._profile[1]))
        if self.button_hold.isChecked() and self._reference is not None:
            self._reference_curve.setData(self._reference[0], self._for_display(self._reference[1]))
        self.reset_view()

    @Slot(bool)
    def _on_hold_toggled(self, checked: bool):
        if checked and self._profile is not None:
            self._reference = (np.array(self._profile[0]), np.array(self._profile[1]))
            self._reference_curve.setData(self._reference[0], self._for_display(self._reference[1]))
        else:
            self._reference = None
            self._reference_curve.setData([], [])
            if checked:                      # nothing to hold yet
                self.button_hold.blockSignals(True)
                self.button_hold.setChecked(False)
                self.button_hold.blockSignals(False)

    # ------------------------------------------------------------------
    # frequency scale
    # ------------------------------------------------------------------
    def set_settings_manager(self, manager):
        """Restore the saved scale, and the spacing last calibrated against."""
        self.settings_manager = manager
        saved = manager.get_spectrum_cut_settings() if manager is not None else {}
        try:
            self._calibration_ghz = max(0.0, float(saved.get("calibration_ghz", 0.0)))
        except (TypeError, ValueError):
            self._calibration_ghz = 0.0
        try:
            scale = float(saved.get("ghz_per_px", 0.0))
        except (TypeError, ValueError):
            scale = 0.0
        self.spin_scale.blockSignals(True)
        self.spin_scale.setValue(scale)             # clamped to the field's range
        self.spin_scale.blockSignals(False)
        self._ghz_per_camera_px = float(self.spin_scale.value())
        self._apply_scale()

    def set_binning(self, binning):
        """The Brillouin camera's binning ("2x2", or 2): a frame pixel spans that many camera pixels."""
        self._binning = binning_factor(binning)
        self._apply_scale()

    def ghz_per_frame_px(self) -> float | None:
        """GHz per pixel of the frames shown, or None while the scale is not set."""
        if self._ghz_per_camera_px <= 0:
            return None
        return self._ghz_per_camera_px * self._binning

    def format_span(self, dx_px: float) -> str:
        """A segment's label: its extent along the spectrum, ``dx_px`` frame pixels."""
        scale = self.ghz_per_frame_px()
        if scale is None:
            return f"Δ {dx_px:.1f} px"
        return f"Δ {dx_px * scale:.2f} GHz"

    def calibrate(self, known_ghz: float, segment=None) -> bool:
        """Set the scale so that ``segment`` (by default the current one) spans ``known_ghz``."""
        segment = segment if segment is not None else self._current_segment
        if segment is None or known_ghz <= 0:
            return False
        dx = segment.dx()
        if dx <= 0:
            return False
        self._calibration_ghz = float(known_ghz)
        self.spin_scale.blockSignals(True)
        self.spin_scale.setValue(known_ghz / (dx * self._binning))
        self.spin_scale.blockSignals(False)
        # Called directly: an unchanged value would not emit valueChanged, and
        # the spacing calibrated against must be saved all the same.
        self._on_scale_edited(self.spin_scale.value())
        logger.info(
            f"[SpectrumCut] scale {self._ghz_per_camera_px:.6f} GHz per camera pixel, "
            f"from a segment of {dx:.1f} px (binning {self._binning}) taken as {known_ghz:g} GHz"
        )
        return True

    @Slot(float)
    def _on_scale_edited(self, value: float):
        self._ghz_per_camera_px = max(0.0, float(value))
        self._apply_scale()
        self._save_settings()

    def _apply_scale(self):
        """Axis, segment labels and length readout, in GHz or in pixels."""
        scale = self.ghz_per_frame_px()
        # The data stay in frame pixels; the axis only draws its ticks scaled.
        # Curves, the held reference and the segments then never move when the
        # scale or the binning changes.
        self.plot.getAxis("bottom").setScale(1.0 if scale is None else scale)
        self.plot.setLabel("bottom", "Along the cut", units="px" if scale is None else "GHz", color="w")
        for segment in self._segments:
            self._update_segment_label(segment)
        if self._profile is not None:
            self._update_info(*self._profile)

    def _save_settings(self):
        if self.settings_manager is None:
            return
        self.settings_manager.update_spectrum_cut_settings(
            ghz_per_px=self._ghz_per_camera_px,
            calibration_ghz=self._calibration_ghz,
        )
        try:
            self.settings_manager.save_spectrum_cut_settings()
        except OSError as e:
            logger.warning(f"[SpectrumCut] scale not saved: {e}")

    @Slot()
    def _on_calibrate_clicked(self):
        segment = self._current_segment
        if segment is None or segment.dx() <= 0:
            self.label_info.setText(
                "To calibrate, first draw a segment (Measure) between two peaks "
                "a known spacing apart, e.g. two orders of the elastic peak."
            )
            return
        dx = segment.dx()
        value, ok = QInputDialog.getDouble(
            self, "Calibrate the frequency axis",
            f"The segment spans {dx:.1f} px.\n"
            "Its actual spacing in GHz (for two orders of the elastic peak,\n"
            "the VIPA's free spectral range):",
            self._calibration_ghz if self._calibration_ghz > 0 else 30.0,
            0.001, 10000.0, 3,
        )
        if ok:
            self.calibrate(value, segment)

    # ------------------------------------------------------------------
    # measuring segments
    # ------------------------------------------------------------------
    @Slot(bool)
    def _on_measure_toggled(self, checked: bool):
        self._view_box.measuring = bool(checked)
        if checked:
            self.plot.setCursor(Qt.CrossCursor)
        else:
            self.plot.unsetCursor()

    def add_segment(self, p0, p1) -> _Segment:
        """A measuring segment from p0 to p1, in the plot's coordinates; it becomes the current one."""
        color = _SEGMENT_COLORS[self._segment_count % len(_SEGMENT_COLORS)]
        self._segment_count += 1
        p0, p1 = pg.Point(p0), pg.Point(p1)
        roi = pg.LineSegmentROI(
            positions=[(p0.x(), p0.y()), (p1.x(), p1.y())],
            pen=pg.mkPen(color, width=2),
            hoverPen=pg.mkPen(color, width=3),
            movable=True,
            removable=True,
        )
        roi.setZValue(20)
        label = pg.TextItem(color=color, anchor=(0.5, 1.0), fill=pg.mkBrush(43, 43, 43, 190))
        label.setZValue(21)
        # Out of the auto-range: a segment must not rescale the plot.
        self._view_box.addItem(roi, ignoreBounds=True)
        self._view_box.addItem(label, ignoreBounds=True)

        segment = _Segment(roi, label, color)
        self._segments.append(segment)
        roi.sigRegionChanged.connect(lambda _roi, s=segment: self._update_segment_label(s))
        roi.sigRegionChangeStarted.connect(lambda _roi, s=segment: self._set_current_segment(s))
        roi.sigRemoveRequested.connect(lambda _roi, s=segment: self.remove_segment(s))
        self._update_segment_label(segment)
        self._set_current_segment(segment)
        return segment

    def remove_segment(self, segment: _Segment):
        if segment not in self._segments:
            return
        self._segments.remove(segment)
        for item in (segment.roi, segment.label):
            try:
                self._view_box.removeItem(item)
            except RuntimeError as e:                 # already deleted with the plot
                logger.debug(f"[SpectrumCut] segment not removed: {e}")
        if self._current_segment is segment:
            self._set_current_segment(self._segments[-1] if self._segments else None)
        if self._drawing is segment:
            self._drawing = None

    @Slot()
    def clear_segments(self):
        for segment in list(self._segments):
            self.remove_segment(segment)

    def segments(self) -> list[_Segment]:
        return list(self._segments)

    def _set_current_segment(self, segment):
        """The current segment -- the one Calibrate uses -- is drawn thicker."""
        self._current_segment = segment
        for s in self._segments:
            s.roi.setPen(pg.mkPen(s.color, width=3 if s is segment else 1.5))

    def _update_segment_label(self, segment: _Segment):
        p0, p1 = segment.ends()
        segment.label.setText(self.format_span(segment.dx()))
        segment.label.setPos((p0.x() + p1.x()) / 2.0, max(p0.y(), p1.y()))

    def _on_measure_drag(self, phase: str, start, end):
        if phase == "start":
            self._drawing = self.add_segment(start, end)
            return
        segment = self._drawing
        if segment is None:
            return
        segment.roi.movePoint(segment.roi.getHandles()[1], QPointF(end), finish=False)
        if phase == "finish":
            self._drawing = None
            # A twitch of the mouse, not a segment.
            a = self._view_box.mapViewToScene(QPointF(start))
            b = self._view_box.mapViewToScene(QPointF(end))
            if (b - a).manhattanLength() < 4:
                self.remove_segment(segment)

    def _segments_to_log(self, log: bool):
        """Keep the segments on the same intensities when the y axis turns log or linear.

        Only their x matters to the measurement, but a segment left at y = 2000
        would vanish far above a log plot, whose view coordinates are log10.
        """
        for segment in self._segments:
            for handle, p in zip(segment.roi.getHandles()[:2], segment.ends()):
                y = float(p.y())
                y = float(np.log10(max(y, 1.0))) if log else float(10.0 ** min(y, 300.0))
                segment.roi.movePoint(handle, QPointF(p.x(), y), finish=False)
