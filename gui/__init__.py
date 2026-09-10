"""DeepLight's Qt layer: the window, its widgets and the managers behind them.

Deliberately empty of imports. Re-exporting MainWindow here meant that reaching
for any submodule -- including the ones that only need numpy, like Scan_Types --
pulled in PySide6, pyqtgraph, OpenCV and every driver module behind them. That
made the numeric parts untestable without a full GUI stack, and undid the care
taken to keep `import DeepLight` free of Qt.

Import what you actually need:

    from DeepLight.gui.main_window import MainWindow
"""
