"""Shared setup for the test suite.

Two things every test needs: to find the package, and to be able to build Qt
objects without a screen.
"""

import os
import sys
from pathlib import Path

import pytest

# The repository root *is* the DeepLight package (see pyproject.toml), so the
# import path is its parent. Added here rather than relying on an install, so a
# fresh clone can run the tests straight away.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

# Must be set before Qt is imported: it lets widgets be created on a machine
# with no display, which is what a CI runner is.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="session")
def qapp():
    """One QApplication for the whole session; Qt allows no more than one."""
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([sys.argv[0] if sys.argv else "tests"])
    yield app


@pytest.fixture
def scan_parameters():
    """A plain two-galvo scan, as the panel would produce it."""
    from DeepLight.recipe import Axis, Recipe

    return Recipe(
        axes=[Axis("X-Galvo", pixels=64, size_um=20.0),
              Axis("Y-Galvo", pixels=48, size_um=15.0)],
        dwell_us=4.0,
        detectors=["PMT-Vis"],
    ).to_scan_parameters()
