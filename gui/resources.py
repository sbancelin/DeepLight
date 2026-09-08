"""Paths to bundled resources, resolved relative to the package.

Icon paths used to be written relative to the process working directory, which
only worked because __main__.py chdir'd into the package before building the
window. Resolving them from ``__file__`` instead means DeepLight can be started
from any directory, and imported without the import changing the process's
working directory.
"""

from pathlib import Path

GUI_DIR = Path(__file__).resolve().parent
ICONS_DIR = GUI_DIR / "Icons"


def icon_path(name: str) -> str:
    """Absolute path to ``gui/Icons/<name>``, as a string for QIcon()."""
    return str(ICONS_DIR / name)
