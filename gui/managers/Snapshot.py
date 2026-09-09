"""Turn what is on screen into a figure one can paste into a notebook.

A screenshot of the window carries axes, histogram and buttons, and its scale
depends on how large the window happened to be. What is wanted instead is the
image itself, at its own resolution, with the contrast and colour map currently
applied, and a scale bar burnt in -- so the micrometres survive the copy even
though a PNG has nowhere to record them.

The whole frame is exported, not the part currently zoomed into: a figure has
to be reproducible from the acquisition, and one output pixel per acquired
pixel is the only rendering that does not depend on the state of the window.

The bar length is chosen rather than configured: the largest of 1, 2 or 5 per
decade that stays near a fifth of the field. Nothing is drawn when the pixel
size is unknown or when even the smallest round length would span half the
image, because a scale bar that is wrong is worse than none.

Only QtGui here, no widgets: the caller passes a QImage and a pixel size, which
keeps this testable and keeps the drawing out of the window.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPainterPath, QPen

#: Lengths worth writing under a bar, in µm: 1, 2 and 5 per decade.
NICE_LENGTHS_UM = (
    0.01, 0.02, 0.05, 0.1, 0.2, 0.5, 1.0, 2.0, 5.0,
    10.0, 20.0, 50.0, 100.0, 200.0, 500.0,
    1000.0, 2000.0, 5000.0, 10000.0,
)

#: Fraction of the image width the bar aims for.
TARGET_FRACTION = 0.18

#: Past this, the bar dominates the picture instead of measuring it.
MAX_FRACTION = 0.5

#: Below this, a native-resolution snapshot is too small for the annotation to
#: be legible, so it is enlarged by a whole factor with nearest-neighbour --
#: honest magnification, no invented pixels.
MIN_SIDE_PX = 512


def choose_scalebar_length_um(width_px: int, um_per_pixel: float) -> float | None:
    """Round bar length for an image `width_px` wide, or None if none fits."""
    if not width_px or not um_per_pixel or um_per_pixel <= 0.0:
        return None

    field_um = float(width_px) * float(um_per_pixel)
    if field_um <= 0.0:
        return None

    target = field_um * TARGET_FRACTION
    below = [v for v in NICE_LENGTHS_UM if v <= target]
    length = below[-1] if below else NICE_LENGTHS_UM[0]

    if length > field_um * MAX_FRACTION:
        return None
    return length


def format_scalebar_label(length_um: float) -> str:
    """Label for a bar, in the unit that keeps the number readable."""
    if length_um < 1.0:
        return f"{length_um * 1000.0:g} nm"
    if length_um < 1000.0:
        return f"{length_um:g} µm"
    return f"{length_um / 1000.0:g} mm"


def upscale_factor(width: int, height: int, min_side: int = MIN_SIDE_PX) -> int:
    """Whole magnification bringing the short side up to `min_side`."""
    short = min(int(width), int(height))
    if short <= 0 or short >= min_side:
        return 1
    return max(1, -(-min_side // short))          # ceil division


def draw_scalebar(image: QImage, um_per_pixel: float) -> float | None:
    """Burn a scale bar into `image` in place; return its length in µm.

    White on a black outline rather than a single colour: the bar has to stay
    readable over a bright colour map as well as a dark one.
    """
    length_um = choose_scalebar_length_um(image.width(), um_per_pixel)
    if length_um is None:
        return None

    width = image.width()
    height = image.height()

    bar_px = length_um / float(um_per_pixel)
    bar_h = max(4.0, round(height * 0.015))
    margin_x = max(8.0, round(width * 0.04))
    margin_y = max(8.0, round(height * 0.04))

    x1 = width - margin_x
    x0 = x1 - bar_px
    y1 = height - margin_y
    y0 = y1 - bar_h

    font = QFont()
    font.setPixelSize(int(max(11, round(height * 0.045))))
    font.setBold(True)

    label = format_scalebar_label(length_um)

    path = QPainterPath()
    path.addRect(x0, y0, bar_px, bar_h)

    text = QPainterPath()
    text.addText(0.0, 0.0, font, label)
    bounds = text.boundingRect()
    # Centred over the bar, sitting just above it.
    text.translate(
        (x0 + x1) / 2.0 - bounds.center().x(),
        y0 - max(4.0, height * 0.012) - bounds.bottom(),
    )
    path.addPath(text)

    painter = QPainter(image)
    painter.setRenderHint(QPainter.Antialiasing, True)

    # The black goes on first as a halo drawn behind, then the white fills over
    # it. Stroking and filling in one pass would let the outline eat a thin bar
    # from both sides and leave it grey instead of white.
    painter.setPen(QPen(QColor(0, 0, 0), max(3.0, round(height * 0.008)),
                        Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    painter.setBrush(Qt.NoBrush)
    painter.drawPath(path)

    painter.setPen(Qt.NoPen)
    painter.setBrush(QColor(255, 255, 255))
    painter.drawPath(path)
    painter.end()

    return length_um


def render_snapshot(source: QImage, um_per_pixel: float | None):
    """Snapshot of `source`, scale bar included: (QImage, bar length in µm).

    `um_per_pixel` is the sampling interval of the displayed image; pass None
    when it is unknown and the picture comes back without a bar, the returned
    length then being None as well.
    """
    if source is None or source.isNull():
        raise ValueError("no image to snapshot")

    factor = upscale_factor(source.width(), source.height())
    if factor > 1:
        source = source.scaled(
            source.width() * factor,
            source.height() * factor,
            Qt.IgnoreAspectRatio,
            Qt.FastTransformation,          # nearest neighbour: no smoothing
        )

    out = source.convertToFormat(QImage.Format_RGB32).copy()

    length_um = None
    if um_per_pixel:
        length_um = draw_scalebar(out, float(um_per_pixel) / factor)

    return out, length_um
