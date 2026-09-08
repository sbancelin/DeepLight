if __name__ == "__main__":
    import sys
    import os
    import ctypes
    import argparse
    from .gui.managers.Microscopes import create_microscope_backend

    from PySide6.QtWidgets import QApplication
    from PySide6.QtCore import Qt, QLocale

    parser = argparse.ArgumentParser(description="Launch DeepLight")
    parser.add_argument(
        "--backend",
        choices=["mock", "nidaq"],
        default="mock",
        help="Microscope backend to use"
    )

    args, qt_args = parser.parse_known_args()

    # No os.chdir() here on purpose: bundled resources are resolved relative to
    # the package (see gui/resources.py), so the working directory is the
    # user's own and relative paths they type keep meaning what they expect.

    if sys.platform == "win32":
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            "DeepLight.Microscope.App.1.0"
        )

    from .gui import MainWindow

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

    from .gui.widgets.Log_Widget import logger
    logger.info("DeepLight started")

    sys.exit(app.exec())