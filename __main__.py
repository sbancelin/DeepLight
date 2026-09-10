"""Entry point for the DeepLight application.

Two ways in, both landing on main():

    python -m DeepLight --backend nidaq     # from the folder holding the package
    deeplight --backend nidaq               # after pip install, from anywhere

The body lives in a function rather than under ``if __name__``, because a
console script has to be able to call something.
"""

import argparse
import ctypes
import os
import sys


def parse_args(argv=None):
    """DeepLight's own options; anything else is left for Qt."""
    parser = argparse.ArgumentParser(prog="deeplight", description="Launch DeepLight")
    parser.add_argument(
        "--backend",
        choices=["mock", "nidaq"],
        default="mock",
        help="Microscope backend to use",
    )
    return parser.parse_known_args(argv)


def main(argv=None) -> int:
    """Build the window and run the application; returns its exit code."""
    args, qt_args = parse_args(argv)

    # No os.chdir() here on purpose: bundled resources are resolved relative to
    # the package (see gui/resources.py), so the working directory is the
    # user's own and relative paths they type keep meaning what they expect.

    if sys.platform == "win32":
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            "DeepLight.Microscope.App.1.0"
        )

    # Imported inside main() so that --help, and importing this module at all,
    # cost nothing: pulling in Qt takes seconds and needs a display.
    from PySide6.QtWidgets import QApplication
    from PySide6.QtCore import Qt, QLocale

    from .gui.main_window import MainWindow
    from .gui.managers.Microscopes import create_microscope_backend
    from .gui.widgets.Log_Widget import logger

    os.environ["QT_AUTO_SCREEN_SCALE_FACTOR"] = "1"

    qt_argv = [sys.argv[0]] + qt_args
    if sys.platform == "win32":
        qt_argv += ["-platform", "windows:darkmode=2"]

    QLocale.setDefault(QLocale.c())
    app = QApplication(qt_argv)
    app.setStyle("Fusion")
    app.styleHints().setColorScheme(Qt.ColorScheme.Dark)

    microscope_backend = create_microscope_backend(args.backend)
    window = MainWindow(args, microscope_backend=microscope_backend)
    window.show()

    logger.info("DeepLight started")

    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
