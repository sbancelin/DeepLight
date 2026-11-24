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
"""

from __future__ import annotations

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import QPointF, Qt, QTimer, Slot
from PySide6.QtWidgets import (
    QCheckBox, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QSpinBox, QVBoxLayout, QWidget,
)

from ._pg_common import DoubleClickAxis, DoubleClickViewBox
from .Log_Widget import logger
from .Spectro_Widget import _BUTTON_STYLE, _BUTTON_STYLE_TOGGLE, _CHECKBOX_STYLE, _apply_spinbox_palette

_PROFILE_COLOR = "#FF7700"
_LINE_COLOR = (60, 200, 255)

#: Ends of the two diagonals, as fractions of (width, height) in image pixels.
#: The image is shown with y upwards, so (0, 0) is its bottom-left corner.
DIAGONAL = ((0.0, 0.0), (1.0, 1.0))        # bottom-left -> top-right on screen
ANTI_DIAGONAL = ((0.0, 1.0), (1.0, 0.0))   # top-left -> bottom-right on screen


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
        row.addStretch()
        layout.addLayout(row)

        self.plot = pg.PlotWidget(
            viewBox=DoubleClickViewBox(self.reset_view),
            axisItems={
                "left": DoubleClickAxis("left", lambda _o: self.reset_view()),
                "bottom": DoubleClickAxis("bottom", lambda _o: self.reset_view()),
            },
        )
        self.plot.setBackground("#2b2b2b")
        self.plot.showGrid(x=True, y=True, alpha=0.3)
        self.plot.setLabel("left", "Intensity", color="w")
        self.plot.setLabel("bottom", "Along the cut", units="px", color="w")
        for axis_name in ("left", "bottom"):
            axis = self.plot.getAxis(axis_name)
            axis.setTextPen(pg.mkPen("w"))
            axis.setPen(pg.mkPen("w"))
        self.plot.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.plot.setMinimumHeight(120)
        self._reference_curve = self.plot.plot(pen=pg.mkPen((170, 170, 170), width=1, style=Qt.DashLine))
        self._curve = self.plot.plot(pen=pg.mkPen(_PROFILE_COLOR, width=1.5))
        layout.addWidget(self.plot, stretch=1)

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
        self.label_info.setText(
            f"length {length:.0f} px · max {float(np.max(finite)):.0f} · "
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
