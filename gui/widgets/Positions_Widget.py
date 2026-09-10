"""A list of places on the sample, to come back to.

Finding a field again an hour later means writing four numbers on paper and
typing them back in. This keeps them, named, and drives the stage back to one
on a double-click.

Positions are stored in the relative frame -- the same one a scan's offsets are
expressed in, and the one the *Set 0* buttons define -- so a saved position
means the same thing after the stage has been re-homed.

The widget owns no hardware. It asks for the current position through a getter
and asks for a move through a signal, so it can be built and tested with no
positioner at all.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView, QHBoxLayout, QHeaderView, QLabel, QPushButton,
    QSizePolicy, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from .Log_Widget import logger

#: Axes a position remembers, in the order they are shown.
POSITION_AXES = ("x", "y", "z", "p")

_BUTTON_STYLE = """
    QPushButton {
        background-color: #333;
        color: white;
        border: 1px solid #555;
        border-radius: 3px;
        padding: 2px 6px;
        min-height: 20px;
    }
    QPushButton:hover { background-color: #444; }
    QPushButton:disabled { color: #666; border-color: #444; }
"""

_TABLE_STYLE = """
    QTableWidget {
        background-color: #252525;
        color: #ddd;
        border: 1px solid #444;
        gridline-color: #3a3a3a;
        font-size: 11px;
    }
    QHeaderView::section {
        background-color: #333;
        color: #bbb;
        border: none;
        padding: 2px;
        font-size: 11px;
    }
    QTableWidget::item:selected { background-color: #2E8B57; color: white; }
"""


class PositionsWidget(QWidget):
    """Named stage positions: capture, revisit, and keep with the preset."""

    #: Asked for when a row is double-clicked or Go is pressed.
    sigGoToPosition = Signal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)

        #: Injected by MainWindow: returns {axis: relative position}.
        self._position_getter = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(4)

        self.table = QTableWidget(0, 1 + len(POSITION_AXES))
        self.table.setHorizontalHeaderLabels(
            ["Name"] + [axis.upper() for axis in POSITION_AXES]
        )
        self.table.setStyleSheet(_TABLE_STYLE)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(20)
        self.table.setMinimumHeight(0)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        for column in range(1, 1 + len(POSITION_AXES)):
            self.table.horizontalHeader().setSectionResizeMode(column, QHeaderView.ResizeToContents)
        # Only the name is worth editing by hand; the coordinates come from the
        # stage, and typing one in would be a position that was never visited.
        self.table.itemDoubleClicked.connect(self._on_double_click)
        layout.addWidget(self.table)

        buttons = QHBoxLayout()
        buttons.setContentsMargins(0, 0, 0, 0)
        buttons.setSpacing(4)

        self.button_add = QPushButton("Add")
        self.button_add.setToolTip("Remember where the stage is now")
        self.button_go = QPushButton("Go")
        self.button_go.setToolTip("Drive the stage back to the selected position")
        self.button_update = QPushButton("Update")
        self.button_update.setToolTip("Replace the selected position with the current one")
        self.button_remove = QPushButton("Remove")
        self.button_clear = QPushButton("Clear")

        for button in (self.button_add, self.button_go, self.button_update,
                       self.button_remove, self.button_clear):
            button.setStyleSheet(_BUTTON_STYLE)
            buttons.addWidget(button)

        layout.addLayout(buttons)

        self.status_label = QLabel("No positions saved.")
        self.status_label.setStyleSheet("color: #888; font-size: 11px;")
        layout.addWidget(self.status_label)

        self.button_add.clicked.connect(self.add_current)
        self.button_go.clicked.connect(self.go_to_selected)
        self.button_update.clicked.connect(self.update_selected)
        self.button_remove.clicked.connect(self.remove_selected)
        self.button_clear.clicked.connect(self.clear)
        self.table.itemSelectionChanged.connect(self._refresh_buttons)

        self._refresh_buttons()

    # ---- wiring -------------------------------------------------------

    def set_position_getter(self, getter):
        """Give the widget a way to read where the stage is."""
        self._position_getter = getter
        self._refresh_buttons()

    def _current_position(self) -> dict | None:
        if self._position_getter is None:
            return None
        try:
            reading = self._position_getter() or {}
        except Exception as e:
            logger.error(f"[Positions] could not read the stage: {e}")
            return None
        return {axis: float(reading.get(axis, 0.0)) for axis in POSITION_AXES}

    # ---- the list -----------------------------------------------------

    def add_current(self):
        position = self._current_position()
        if position is None:
            self.status_label.setText("No positioner: nothing to capture.")
            return

        name = f"Pos {self.table.rowCount() + 1}"
        self._append_row(name, position)
        self.status_label.setText(f"{name} saved.")
        logger.info(f"[Positions] {name}: " + self._format(position))

    def update_selected(self):
        row = self.table.currentRow()
        position = self._current_position()
        if row < 0 or position is None:
            return

        for column, axis in enumerate(POSITION_AXES, start=1):
            self.table.item(row, column).setText(f"{position[axis]:.2f}")
        self.status_label.setText(f"{self.table.item(row, 0).text()} updated.")

    def go_to_selected(self):
        row = self.table.currentRow()
        if row < 0:
            return

        position = self._row_position(row)
        name = self.table.item(row, 0).text()
        self.status_label.setText(f"Going to {name}…")
        logger.info(f"[Positions] going to {name}: " + self._format(position))
        self.sigGoToPosition.emit(position)

    def remove_selected(self):
        row = self.table.currentRow()
        if row < 0:
            return
        name = self.table.item(row, 0).text()
        self.table.removeRow(row)
        self.status_label.setText(f"{name} removed.")
        self._refresh_buttons()

    def clear(self):
        self.table.setRowCount(0)
        self.status_label.setText("No positions saved.")
        self._refresh_buttons()

    # ---- as data ------------------------------------------------------

    def positions(self) -> list:
        """Every saved position, as plain data -- for the preset, or a script."""
        return [
            {"name": self.table.item(row, 0).text(), **self._row_position(row)}
            for row in range(self.table.rowCount())
        ]

    def set_positions(self, positions) -> list:
        """Replace the list; returns the entries that could not be read."""
        self.table.setRowCount(0)
        rejected = []

        for entry in (positions or []):
            try:
                name = str(entry.get("name", f"Pos {self.table.rowCount() + 1}"))
                coordinates = {axis: float(entry.get(axis, 0.0)) for axis in POSITION_AXES}
            except (AttributeError, TypeError, ValueError) as e:
                rejected.append(f"{entry!r} ({e})")
                continue
            self._append_row(name, coordinates)

        count = self.table.rowCount()
        self.status_label.setText(
            f"{count} position{'s' if count != 1 else ''} loaded."
            if count else "No positions saved."
        )
        self._refresh_buttons()
        return rejected

    # ---- internals ----------------------------------------------------

    def _append_row(self, name: str, position: dict):
        row = self.table.rowCount()
        self.table.insertRow(row)

        name_item = QTableWidgetItem(str(name))
        self.table.setItem(row, 0, name_item)

        for column, axis in enumerate(POSITION_AXES, start=1):
            item = QTableWidgetItem(f"{float(position.get(axis, 0.0)):.2f}")
            item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            item.setFlags(item.flags() & ~Qt.ItemIsEditable)
            self.table.setItem(row, column, item)

        self.table.selectRow(row)
        self._refresh_buttons()

    def _row_position(self, row: int) -> dict:
        position = {}
        for column, axis in enumerate(POSITION_AXES, start=1):
            item = self.table.item(row, column)
            try:
                position[axis] = float(item.text()) if item else 0.0
            except ValueError:
                position[axis] = 0.0
        return position

    @staticmethod
    def _format(position: dict) -> str:
        return "  ".join(f"{axis.upper()}={position.get(axis, 0.0):.2f}"
                         for axis in POSITION_AXES)

    def _on_double_click(self, item):
        # Double-clicking the name edits it; double-clicking a coordinate goes
        # there, which is the gesture people try first.
        if item.column() != 0:
            self.go_to_selected()

    def _refresh_buttons(self):
        has_rows = self.table.rowCount() > 0
        has_selection = self.table.currentRow() >= 0 and has_rows
        has_stage = self._position_getter is not None

        self.button_add.setEnabled(has_stage)
        self.button_update.setEnabled(has_stage and has_selection)
        self.button_go.setEnabled(has_selection)
        self.button_remove.setEnabled(has_selection)
        self.button_clear.setEnabled(has_rows)
