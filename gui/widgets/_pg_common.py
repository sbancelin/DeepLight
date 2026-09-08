"""Shared pyqtgraph helper items used by several plot widgets.

These subclasses add a double-click hook that pyqtgraph does not expose
directly, so a widget can reset its own view when the user double-clicks an
axis or the plot area.
"""

import pyqtgraph as pg


class DoubleClickAxis(pg.AxisItem):
    """Axis that calls ``on_double_click(orientation)`` when double-clicked."""

    def __init__(self, orientation, on_double_click=None, *args, **kwargs):
        super().__init__(orientation=orientation, *args, **kwargs)
        self.on_double_click = on_double_click

    def mouseDoubleClickEvent(self, ev):
        ev.accept()
        if callable(self.on_double_click):
            self.on_double_click(self.orientation)


class DoubleClickViewBox(pg.ViewBox):
    """ViewBox that calls ``on_double_click()`` when double-clicked."""

    def __init__(self, on_double_click=None, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.on_double_click = on_double_click

    def mouseDoubleClickEvent(self, ev):
        ev.accept()
        if callable(self.on_double_click):
            self.on_double_click()
