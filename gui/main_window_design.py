"""Layout definition for the DeepLight main window.

NOTE: this module is **hand-written and hand-maintained**. It follows the
Qt Designer naming convention (``setupUi`` / ``retranslateUi``) but it is not
generated: there is no ``.ui`` source file for it anywhere in the repository,
and it composes the project's own widgets directly. Edit this file by hand —
do not attempt to regenerate it with ``pyside6-uic``, which would discard the
custom widgets and stylesheets defined here.
"""

from PySide6.QtCore import (QCoreApplication, QSize, Qt)
from PySide6.QtGui import (QIcon, QTransform, QShortcut, QKeySequence)
from PySide6.QtWidgets import (QApplication, QLineEdit, QCheckBox, QDockWidget, QGridLayout, QGroupBox, QSplitter, QDialog, QDialogButtonBox, QFormLayout, QDoubleSpinBox,
                                QHBoxLayout, QVBoxLayout, QLabel, QPushButton, QSizePolicy, QSpacerItem, QTabWidget, QWidget, QMessageBox)
from .widgets.Scan_Widget import ScanWidget
from .widgets.Laser_Widget import LaserWidget
from .widgets.Detector_Widget import DetectorWidget
from .widgets.Positioner_Widget import PositionerWidget
from .widgets.Save_Widget import SaveWidget
from .widgets.Analog_visualizer_widget import AnalogOutVisualizerWidget
from .widgets.Stepper_visualizer_widget import StepperVisualizerWidget
from .widgets.Nyquist_widget import NyquistWidget
from .widgets.Line_profile_widget import LineProfileWidget
from .widgets.Histogram_widget import HistogramWidget
from .widgets.FRC_Widget import FRCWidget
from .widgets.Stitching_Widget import StitchingWidget
from .widgets.Camera_Widget import CameraWidget
from .widgets.Spectro_Widget import SpectroWidget
from .widgets.Spectro_Panel_Widget import SpectroPanelWidget
from .widgets.Panel_Dock import PanelDock
from .widgets.Collapsible_Panel import CollapsiblePanel
from .widgets.Log_Widget import LogWidget
from .widgets.Global_Progress_Widget import GlobalProgressWidget

import numpy as np
import pyqtgraph as pg

CHECKBOX_STYLE = """
        QCheckBox::indicator {
                width: 12px;
                height: 12px;
                background-color: #333;
                border: 1px solid #555;
                border-radius: 3px;
        }
        QCheckBox::indicator:checked {
                background-color: #2E8B57;
                border: 1px solid #555;
                border-radius: 3px;
        }
        QCheckBox::indicator:checked:hover {
                border: 1px solid #777;
                background-color: #3AB16F;
        }
        QCheckBox::indicator:unchecked:hover {
                background-color: #444;
                border: 1px solid #777;
        }
        """

TRANSPARENT_ICON_BUTTON_CHECKABLE_STYLE = """
        QPushButton {
        border: none;
        background-color: transparent;
        }
        QPushButton:checked {
        background-color: #2E8B57;
        border-radius: 6px;
        }
        QPushButton:disabled {
        qproperty-iconOpacity: 0.05;
        }
        QPushButton:hover {
        background-color: #444;
        border-radius: 6px;
        }
        QPushButton:checked:hover {
        background-color: #3AB16F;
        border-radius: 6px;
        }
"""

TRANSPARENT_ICON_BUTTON_STYLE = """
        QPushButton {
        border: none;
        background-color: transparent;
        }
        QPushButton:hover {
        background-color: #444;
        border-radius: 6px;
        }
"""

SHUTTER_BUTTON_STYLE = """
        QPushButton {
        border: 1px solid white;
        border-radius: 6px;
        background-color: transparent;
        }

        QPushButton:checked {
        border: 2px solid #FF7700;
        border-radius: 6px;
        background-color: transparent;
        }

        QPushButton:disabled {
        qproperty-iconOpacity: 0.05;
        }

        QPushButton:hover {
        background-color: #444;
        border-radius: 6px;
        }

        QPushButton:checked:hover {
        background-color: #444;
        border-radius: 6px;
        }
"""

def ask_levels_min_max(parent=None, title="LUT Levels", lo0=0.0, hi0=255.0):
    """Ouvre un petit dialogue pour saisir les niveaux min/max de LUT."""
    dlg = QDialog(parent)
    dlg.setWindowTitle(title)

    form = QFormLayout(dlg)

    sp_lo = QDoubleSpinBox(dlg)
    sp_lo.setDecimals(6)
    sp_lo.setRange(-1e12, 1e12)
    sp_lo.setValue(float(lo0))

    sp_hi = QDoubleSpinBox(dlg)
    sp_hi.setDecimals(6)
    sp_hi.setRange(-1e12, 1e12)
    sp_hi.setValue(float(hi0))

    form.addRow("Min:", sp_lo)
    form.addRow("Max:", sp_hi)

    buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, parent=dlg)
    form.addRow(buttons)

    buttons.accepted.connect(dlg.accept)
    buttons.rejected.connect(dlg.reject)

    if dlg.exec() != QDialog.Accepted:
        return None

    lo = float(sp_lo.value())
    hi = float(sp_hi.value())
    if hi <= lo:
        return None
    return lo, hi

class Ui_MainWindowDesign:
    """Construction de l'interface principale DeepLight et de ses widgets centraux."""

    def setupUi(self, MainWindowDesign):
        if not MainWindowDesign.objectName():
            MainWindowDesign.setObjectName(u"MainWindowDesign")
        MainWindowDesign.resize(1692, 1596)

        #####↓ Initialisation   #####
        self.im_status_labels = {}

        # Création du widget central
        self.centralwidget = QWidget(MainWindowDesign)
        self.centralwidget.setObjectName(u"centralwidget")

        # Layout principal du widget central
        self.gridLayout_2 = QGridLayout(self.centralwidget)
        self.gridLayout_2.setObjectName(u"gridLayout_2")
        
        # Layout secondaire (pour organiser les éléments)
        self.gridLayout_11 = QGridLayout()
        self.gridLayout_11.setObjectName(u"gridLayout_11")

################# Barre d'action ###################
        MainWindowDesign.setCentralWidget(self.centralwidget)
        self.dockWidget_preview = QDockWidget(MainWindowDesign)
        self.dockWidget_preview.setObjectName(u"dockWidget_preview")
        self.dockWidget_preview.setFeatures(QDockWidget.DockWidgetFeature.DockWidgetFloatable|QDockWidget.DockWidgetFeature.DockWidgetMovable)
        self.dockWidget_preview.setTitleBarWidget(QWidget())  # Supprime la barre de titre
        self.dockWidget_preview.setFixedHeight(80)

        self.dockWidgetContents_8 = QWidget(self.dockWidget_preview)
        self.dockWidgetContents_8.setObjectName(u"dockWidgetContents_8")
        self.dockWidgetContents_8.setMinimumWidth(0)
        self.gridLayout_15 = QGridLayout(self.dockWidgetContents_8)
        self.gridLayout_15.setObjectName(u"gridLayout_15")
        self.groupBox_11 = QGroupBox(self.dockWidgetContents_8)
        self.groupBox_11.setObjectName(u"groupBox_11")
        self.groupBox_11.setMinimumWidth(0)
        self.gridLayout_81 = QGridLayout(self.groupBox_11)
        self.gridLayout_81.setObjectName(u"gridLayout_81")
        
        self.pushButton_previewSingle = QPushButton(self.groupBox_11)
        self.pushButton_previewSingle.setObjectName(u"pushButton_previewSingle")
        self.pushButton_previewSingle.setStyleSheet(TRANSPARENT_ICON_BUTTON_CHECKABLE_STYLE)
       
        icon1 = QIcon("gui/Icons/play.svg")
        self.pushButton_previewSingle.setIcon(icon1)
        self.pushButton_previewSingle.setIconSize(QSize(32, 32))
        self.pushButton_previewSingle.setFlat(True)
        self.pushButton_previewSingle.setCheckable(True)

        self.gridLayout_81.addWidget(self.pushButton_previewSingle, 0, 1, 1, 1)

        self.pushButton_stop = QPushButton(self.groupBox_11)
        self.pushButton_stop.setObjectName(u"pushButton_stop")
        sizePolicy2 = QSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)
        sizePolicy2.setHorizontalStretch(0)
        sizePolicy2.setVerticalStretch(0)
        sizePolicy2.setHeightForWidth(self.pushButton_stop.sizePolicy().hasHeightForWidth())
        self.pushButton_stop.setSizePolicy(sizePolicy2)
        self.pushButton_stop.setStyleSheet(TRANSPARENT_ICON_BUTTON_STYLE)
        icon2 = QIcon("gui/Icons/stop.svg")
        self.pushButton_stop.setIcon(icon2)
        self.pushButton_stop.setIconSize(QSize(32, 32))
        self.pushButton_stop.setFlat(True)

        self.gridLayout_81.addWidget(self.pushButton_stop, 0, 4, 1, 1)

        self.pushButton_previewcontinuous = QPushButton(self.groupBox_11)
        self.pushButton_previewcontinuous.setObjectName(u"pushButton_previewcontinuous")
        self.pushButton_previewcontinuous.setStyleSheet(TRANSPARENT_ICON_BUTTON_CHECKABLE_STYLE)
        icon3 = QIcon("gui/Icons/repeat.svg")
        self.pushButton_previewcontinuous.setIcon(icon3)
        self.pushButton_previewcontinuous.setIconSize(QSize(32, 32))
        self.pushButton_previewcontinuous.setFlat(True)
        self.pushButton_previewcontinuous.setCheckable(True)

        self.gridLayout_81.addWidget(self.pushButton_previewcontinuous, 0, 2, 1, 1)

        self.pushButton_acquisitionStart = QPushButton(self.groupBox_11)
        self.pushButton_acquisitionStart.setObjectName(u"pushButton_acquisitionStart")
        self.pushButton_acquisitionStart.setStyleSheet(TRANSPARENT_ICON_BUTTON_CHECKABLE_STYLE)

        icon4 = QIcon("gui/Icons/REC.svg")
        self.pushButton_acquisitionStart.setIcon(icon4)
        self.pushButton_acquisitionStart.setIconSize(QSize(32, 32))
        self.pushButton_acquisitionStart.setFlat(True)
        self.pushButton_acquisitionStart.setCheckable(True)

        self.gridLayout_81.addWidget(self.pushButton_acquisitionStart, 0, 3, 1, 1)

        # Bouton d'état du Shutter
        self.pushButton_shutter = QPushButton(self.groupBox_11)
        self.pushButton_shutter.setObjectName(u"pushButton_shutter")
        self.pushButton_shutter.setFixedHeight(35)  # Fixation de la hauteur à 40 pixels
        self.pushButton_shutter.setFixedWidth(50)  # Fixation de la hauteur à 40 pixels
        self.pushButton_shutter.setStyleSheet(SHUTTER_BUTTON_STYLE)

        self.pushButton_shutter.setIcon(QIcon(None))
        self.pushButton_shutter.setIconSize(QSize(32, 32))
        self.pushButton_shutter.setFlat(True)
        self.pushButton_shutter.setCheckable(True)  # Permet de basculer entre ON/OFF   

        self.gridLayout_81.addWidget(self.pushButton_shutter, 0, 5, 1, 1)

        self.gridLayout_15.addWidget(self.groupBox_11, 1, 0, 1, 1)

        self.dockWidget_preview.setWidget(self.dockWidgetContents_8)
        MainWindowDesign.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, self.dockWidget_preview)

##################  Left panel dock ####################
        self.left_panel_dock = PanelDock("", MainWindowDesign)

        self.scan_widget = ScanWidget()
        self.detector_widget = DetectorWidget()
        self.save_widget = SaveWidget()
        self.laser_widget = LaserWidget()
        self.positioner_widget = PositionerWidget()
        self.spectro_panel_widget = SpectroPanelWidget()

        self.scan_widget.embed_save_section(self.save_widget)

        self.scan_panel = CollapsiblePanel(
            "Scan",
            self.scan_widget,
            collapsed=False,
            settings_callback=self.scan_widget.open_settings_dialog,
            parent=self.left_panel_dock.container
        )
        self.spectro_panel = CollapsiblePanel(
            "Spectro",
            self.spectro_panel_widget,
            collapsed=True,
            settings_callback=self.spectro_panel_widget.open_settings_dialog,
            parent=self.left_panel_dock.container
        )
        self.detector_panel = CollapsiblePanel(
            title="Detectors",
            content_widget=self.detector_widget,
            collapsed=False,
            parent=self.left_panel_dock.container
        )
        self.laser_panel = CollapsiblePanel(
            title="Lasers",
            content_widget=self.laser_widget,
            collapsed=False,
            settings_callback=self.laser_widget.open_settings_dialog,
            parent=self.left_panel_dock.container
        )
        self.positioner_panel = CollapsiblePanel(
            "Positioners",
            self.positioner_widget,
            collapsed=False,
            settings_callback=self.positioner_widget.open_settings_dialog,
            parent=self.left_panel_dock.container
        )

        self.left_panel_dock.add_panel(self.scan_panel)
        self.left_panel_dock.add_panel(self.spectro_panel)
        self.left_panel_dock.add_panel(self.positioner_panel)
        self.left_panel_dock.add_panel(self.detector_panel)
        self.left_panel_dock.add_panel(self.laser_panel)

        MainWindowDesign.addDockWidget(Qt.LeftDockWidgetArea, self.left_panel_dock)

##################  Right panel dock ####################
        self.right_panel_dock = PanelDock("Helpers", MainWindowDesign)

        self.analog_out_widget = AnalogOutVisualizerWidget()
        self.visu_step_widget = StepperVisualizerWidget()
        self.nyquist_widget = NyquistWidget()
        self.line_profile_widget = LineProfileWidget()
        self.histogram_widget = HistogramWidget()
        self.frc_widget = FRCWidget()
        self.log_widget = LogWidget()

        self.analog_panel = CollapsiblePanel("Analog Visualizer", self.analog_out_widget, collapsed=True, preferred_content_height=400, parent=self.right_panel_dock.container)
        self.stepper_panel = CollapsiblePanel("Stepper Visualizer", self.visu_step_widget, collapsed=True, preferred_content_height=300, parent=self.right_panel_dock.container)
        self.nyquist_panel = CollapsiblePanel("Nyquist", self.nyquist_widget, collapsed=True, parent=self.right_panel_dock.container)
        self.line_profile_panel = CollapsiblePanel("Line Profile", self.line_profile_widget, collapsed=True, preferred_content_height=300, parent=self.right_panel_dock.container)
        self.histogram_panel = CollapsiblePanel("Histogram", self.histogram_widget, collapsed=True, preferred_content_height=300, parent=self.right_panel_dock.container)
        self.frc_panel = CollapsiblePanel("FRC", self.frc_widget, collapsed=True, preferred_content_height=300, parent=self.right_panel_dock.container)
        self.log_panel = CollapsiblePanel("Logs", self.log_widget, collapsed=False, preferred_content_height=200, parent=self.right_panel_dock.container)

        self.right_panel_dock.add_panel(self.analog_panel)
        self.right_panel_dock.add_panel(self.stepper_panel)
        self.right_panel_dock.add_panel(self.nyquist_panel)
        self.right_panel_dock.add_panel(self.line_profile_panel)
        self.right_panel_dock.add_panel(self.histogram_panel)
        self.right_panel_dock.add_panel(self.frc_panel)
        self.right_panel_dock.add_panel(self.log_panel)

        MainWindowDesign.addDockWidget(Qt.RightDockWidgetArea, self.right_panel_dock)

################# Onglet Scan ######################
        self.tab_preview = QWidget()
        self.tab_preview.setObjectName(u"tab_preview")
        
        # Layout principal de l'onglet
        self.gridLayout_im = QGridLayout(self.tab_preview)
        self.gridLayout_im.setObjectName(u"gridLayout_32")

        # Initialiser un QSplitter pour les images
        self.splitter = QSplitter(Qt.Horizontal)
        self.gridLayout_im.addWidget(self.splitter, 0, 0, 1, 1)

        # Initialiser les ImageView pour chaque canal
        self.im_widgets = {}
        self.im_status_labels.clear()
        self.im_widget_plot_items = {}
        self.channel_autoscale = {}   # channel -> bool
        self.channel_lock = {}        # channel -> bool
        self.channel_grid = {}        # channel -> bool
        self.channel_controls = {}    # channel -> {"autoscale": QCheckBox, "grid": QCheckBox}
        self.channel_hist_luts = {}   # channel -> HistogramLUTItem

        # Récupérer les valeurs par défaut de #Pix X et #Pix Y
        scan_parameters = self.scan_widget.get_scan_parameters()
        default_pix_x = scan_parameters["pixel_values"][0]
        default_pix_y = scan_parameters["pixel_values"][1]

        rows = scan_parameters["rows"]
        default_width_um = float(rows[0]["size_um"]) if len(rows) > 0 else 1.0
        default_height_um = float(rows[1]["size_um"]) if len(rows) > 1 else 1.0

        # Créer une ImageView par défaut avec une image de taille (default_pix_y, default_pix_x)
        self.currentImage = np.zeros((default_pix_y, default_pix_x))
        self.im_widget_plot_item = pg.PlotItem()
        self.im_widget_plot_item.setLabel("left", "y (um)")
        self.im_widget_plot_item.setLabel("bottom", "x (um)")


        self.im_widget = pg.ImageView(parent=self.tab_preview, view=self.im_widget_plot_item)
        self.im_widget.setPredefinedGradient("inferno")
        self.im_widget.setImage(self.currentImage)

        img_h, img_w = self.currentImage.shape[:2]
        scale_x = default_width_um / float(img_w) if img_w > 0 else 1.0
        scale_y = default_height_um / float(img_h) if img_h > 0 else 1.0

        img_item = self.im_widget.getImageItem()
        img_item.setTransform(QTransform.fromScale(scale_x, scale_y))
        img_item.setPos(0, 0)

        self.im_widget.getView().setAspectLocked(True)
        self.im_widget.getView().autoRange()

        self.im_widgets["default"] = self.im_widget
        self.splitter.addWidget(self.im_widgets["default"])

        # Ajoute l'onglet au QTabWidget avec un titre
        self.tabWidget = QTabWidget(self.centralwidget)
        self.tabWidget.setObjectName(u"tabWidget")
        self.tabWidget.addTab(self.tab_preview, "Scan")
        self.gridLayout_2.addWidget(self.tabWidget)

        ################# Onglet Stitching ######################
        self.stitch_widget = StitchingWidget(self.centralwidget)
        self.tabWidget.addTab(self.stitch_widget, "Stitching")

        ################# Onglet Stitching ######################
        self.camera_widget = CameraWidget(self.centralwidget)
        self.tabWidget.addTab(self.camera_widget, "Camera")

        ################# Onglet Spectro ######################
        self.spectro_widget = SpectroWidget(self.centralwidget)
        self.tabWidget.addTab(self.spectro_widget, "Spectro")

        ################# Barre de progression globale (bas du GUI) ######################
        self.global_progress_widget = GlobalProgressWidget(MainWindowDesign)
        MainWindowDesign.statusBar().addPermanentWidget(self.global_progress_widget, 1)

        ##################  Helpers   ##################
        self.retranslateUi(MainWindowDesign)

##################   Connect signals ####################
        self.pushButton_previewSingle.clicked.connect(MainWindowDesign.previewsingleButtonClicked)
        self.pushButton_previewcontinuous.clicked.connect(MainWindowDesign.previewcontinuousButtonClicked)
        self.pushButton_acquisitionStart.clicked.connect(MainWindowDesign.RecButtonClicked)
        self.pushButton_stop.clicked.connect(MainWindowDesign.stopButtonClicked)
        self.pushButton_shutter.toggled.connect(MainWindowDesign.shutterButtonClicked)

        # ---------------- Global shortcuts ----------------
        self.shortcut_preview_single = QShortcut(QKeySequence(Qt.Key_Space), MainWindowDesign)
        self.shortcut_preview_single.setContext(Qt.ApplicationShortcut)
        self.shortcut_preview_single.activated.connect(self._shortcut_preview_single)

        self.shortcut_preview_continuous = QShortcut(QKeySequence("Ctrl+Space"), MainWindowDesign)
        self.shortcut_preview_continuous.setContext(Qt.ApplicationShortcut)
        self.shortcut_preview_continuous.activated.connect(self._shortcut_preview_continuous)

        self.shortcut_stop = QShortcut(QKeySequence(Qt.Key_Escape), MainWindowDesign)
        self.shortcut_stop.setContext(Qt.ApplicationShortcut)
        self.shortcut_stop.activated.connect(self._shortcut_stop)

        self.shortcut_shutter = QShortcut(QKeySequence("Ctrl+Q"), MainWindowDesign)
        self.shortcut_shutter.setContext(Qt.ApplicationShortcut)
        self.shortcut_shutter.activated.connect(self._shortcut_toggle_shutter)

        self.detector_widget.detectors_changed.connect(self.update_scan_layout)
        active_channels = self.detector_widget.detectors
        self.update_scan_layout(active_channels)
        self.stitch_widget.set_channel_list(active_channels)

    def _update_lut_axis(self, hist_lut, lo, hi, n_ticks=5):
        if hist_lut is None:
            return

        lo = float(lo)
        hi = float(hi)

        if not np.isfinite(lo):
            lo = 0.0
        if not np.isfinite(hi):
            hi = lo + 1.0

        if hi <= lo:
            hi = lo + 1.0

        span = float(hi - lo)
        pad = max(1e-12, 0.02 * span)

        view_lo = lo - pad
        view_hi = hi + pad

        try:
            hist_lut.item.vb.setYRange(view_lo, view_hi, padding=0)
        except Exception:
            pass

        raw_ticks = np.linspace(lo, hi, n_ticks)

        # Affichage "entier" seulement pour les comptes,
        # sinon on garde un affichage float compact.
        integer_like = (
            abs(lo - round(lo)) < 1e-9
            and abs(hi - round(hi)) < 1e-9
            and max(abs(lo), abs(hi)) >= 10
        )

        tick_labels = []
        used = set()

        for val in raw_ticks:
            if integer_like:
                pos = float(int(round(val)))
                label = str(int(round(val)))
                key = label
            else:
                pos = float(val)
                label = f"{val:.4g}"
                key = label

            if key in used:
                continue
            used.add(key)
            tick_labels.append((pos, label))

        try:
            hist_lut.item.axis.setTicks([tick_labels, []])
        except Exception:
            pass

    def _apply_levels(self, im, hist_lut, lo, hi):
        lo = float(lo)
        hi = float(hi)

        if not np.isfinite(lo):
            lo = 0.0
        if not np.isfinite(hi):
            hi = lo + 1.0

        if hi <= lo:
            hi = lo + 1.0

        try:
            im.setLevels(lo, hi)
        except Exception:
            pass

        try:
            im.ui.histogram.region.setRegion((lo, hi))
        except Exception:
            try:
                im.ui.histogram.setLevels(lo, hi)
            except Exception:
                pass

        self._update_lut_axis(hist_lut, lo, hi, n_ticks=5)

    def _get_image_minmax_from_widget(self, im, channel=None):
        img = getattr(im, "image", None)

        def _default_range_for_channel(ch):
            ch = str(ch or "")
            if ch in ("Ch 0", "Ch 1"):
                # default counts range for digital channels
                return 0.0, 20000.0
            # default voltage range for analog channels
            return 0.0, 10.0

        if img is None:
            return _default_range_for_channel(channel)

        arr = np.asarray(img, dtype=np.float64)
        finite = arr[np.isfinite(arr)]

        if finite.size == 0:
            return _default_range_for_channel(channel)

        lo = float(np.min(finite))
        hi = float(np.max(finite))

        # image plate: on garde une plage pertinente selon le type de canal
        if hi <= lo:
            return _default_range_for_channel(channel)

        return lo, hi

    def apply_levels_to_channel(self, channel, lo, hi):
        im = self.im_widgets.get(channel)
        if im is None:
            im = self.im_widgets.get("default")
        if im is None:
            return

        hist_lut = self.channel_hist_luts.get(channel)
        if hist_lut is None and "default" in self.channel_hist_luts:
            hist_lut = self.channel_hist_luts["default"]

        self._apply_levels(im, hist_lut, lo, hi)

    def autoscale_channel_levels(self, channel):
        im = self.im_widgets.get(channel)
        if im is None:
            im = self.im_widgets.get("default")
        if im is None:
            return

        hist_lut = self.channel_hist_luts.get(channel)
        if hist_lut is None and "default" in self.channel_hist_luts:
            hist_lut = self.channel_hist_luts["default"]

        lo, hi = self._get_image_minmax_from_widget(im, channel=channel)
        self._apply_levels(im, hist_lut, lo, hi)

    def sync_channel_lut_axis_from_current_levels(self, channel):
        im = self.im_widgets.get(channel)
        if im is None:
            im = self.im_widgets.get("default")
        if im is None:
            return

        hist_lut = self.channel_hist_luts.get(channel)
        if hist_lut is None and "default" in self.channel_hist_luts:
            hist_lut = self.channel_hist_luts["default"]

        try:
            levels = im.getLevels()
        except Exception:
            levels = None

        if levels is None:
            lo, hi = self._get_image_minmax_from_widget(im, channel=channel)
        else:
            lo, hi = levels

        self._update_lut_axis(hist_lut, lo, hi, n_ticks=5)
    
    def _focused_widget_blocks_shortcuts(self):
        fw = QApplication.focusWidget()
        return isinstance(fw, QLineEdit)

    def _shortcut_preview_single(self):
        if self._focused_widget_blocks_shortcuts():
            return

        try:
            self.pushButton_previewSingle.click()
        except Exception:
            pass

    def _shortcut_preview_continuous(self):
        if self._focused_widget_blocks_shortcuts():
            return

        try:
            if not self.pushButton_previewcontinuous.isChecked():
                self.pushButton_previewcontinuous.click()
        except Exception:
            pass

    def _shortcut_stop(self):
        if self._focused_widget_blocks_shortcuts():
            return

        try:
            self.pushButton_stop.click()
        except Exception:
            pass

    def _shortcut_toggle_shutter(self):
        if self._focused_widget_blocks_shortcuts():
            return

        try:
            self.pushButton_shutter.toggle()
        except Exception:
            pass
    
    def retranslateUi(self, MainWindowDesign):
        MainWindowDesign.setWindowTitle(QCoreApplication.translate("MainWindowDesign", u"MainWindow", None))
        self.tabWidget.setTabText(self.tabWidget.indexOf(self.tab_preview), QCoreApplication.translate("MainWindowDesign", u"Scan", None))
        self.dockWidget_preview.setWindowTitle(QCoreApplication.translate("MainWindowDesign", u"Commands", None))
        self.groupBox_11.setTitle("")
        self.pushButton_acquisitionStart.setToolTip(QCoreApplication.translate("MainWindowDesign", u"<html><head/><body><p>Start the scanning saving data</p></body></html>", None))
        self.pushButton_acquisitionStart.setText("")
        self.pushButton_previewSingle.setToolTip(QCoreApplication.translate("MainWindowDesign", u"<html><head/><body><p>Start the scanning without storing data</p></body></html>", None))
        self.pushButton_previewSingle.setText("")
        self.pushButton_stop.setToolTip(QCoreApplication.translate("MainWindowDesign", u"<html><head/><body><p>Stop the scan</p></body></html>", None))
        self.pushButton_stop.setText("")
        self.pushButton_shutter.setToolTip(QCoreApplication.translate("MainWindowDesign", u"<html><head/><body><p>Toggle shutter open/closed</p></body></html>", None))
        self.pushButton_shutter.setText("")

    def get_scan_tab_references(self):
        """Retourne les références aux ImageView et au QSplitter."""
        return self.im_widgets, self.splitter

    def update_scan_layout(self, active_channels):
        """
        Disposition voulue:
        ch0  ch2
        ch1  ch3
        """
        # Nettoyer le splitter
        while self.splitter.count():
            w = self.splitter.widget(0)
            w.setParent(None)
            w.deleteLater()

        # Garder la même référence (important pour MainWindow)
        self.im_widgets.clear()
        self.im_status_labels.clear()
        self.channel_hist_luts.clear()
        scan_parameters = self.scan_widget.get_scan_parameters()
        rows = scan_parameters["rows"]

        width_um = float(rows[0]["size_um"]) if len(rows) > 0 else 1.0
        height_um = float(rows[1]["size_um"]) if len(rows) > 1 else 1.0

        channels = list(active_channels) if active_channels else ["default"]

        # On fait un seul widget "grid" qu'on met dans le splitter (comme avant),
        # mais en version propre et stable.
        grid_host = QWidget()
        grid = QGridLayout(grid_host)
        grid.setContentsMargins(6, 6, 6, 6)
        grid.setSpacing(8)

        for index, channel in enumerate(channels):
            # Container d’un canal
            container = QWidget()
            v = QVBoxLayout(container)
            v.setContentsMargins(4, 4, 4, 4)
            v.setSpacing(4)

            title = QLabel(str(channel))
            title.setAlignment(Qt.AlignCenter)
            v.addWidget(title)

            # ---------- Footer: status + controls ----------
            autoscale_default = self.channel_autoscale.get(channel, True)
            lock_default = self.channel_lock.get(channel, True)
            grid_default = self.channel_grid.get(channel, True)

            plot_item = pg.PlotItem()
            plot_item.setLabel("left", "y (um)")
            plot_item.setLabel("bottom", "x (um)")

            im = pg.ImageView(parent=self.tab_preview, view=plot_item)
            im.setPredefinedGradient("inferno")
            im.setImage(self.currentImage, autoLevels=False, autoRange=False)

            img_h, img_w = self.currentImage.shape[:2]
            scale_x = width_um / float(img_w) if img_w > 0 else 1.0
            scale_y = height_um / float(img_h) if img_h > 0 else 1.0

            img_item = im.getImageItem()
            img_item.setTransform(QTransform.fromScale(scale_x, scale_y))
            img_item.setPos(0, 0)

            im.getView().setAspectLocked(lock_default)
            im.getView().autoRange()

            # Masquer les boutons ROI
            im.ui.roiBtn.hide()

            # Accéder à l'objet HistogramLUTItem
            hist_lut = im.ui.histogram
            self.channel_hist_luts[channel] = hist_lut

            # Afficher la barre de LUT
            hist_lut.gradient.show()

            # Masquer l'histogramme, garder la LUT + l'axe
            if hasattr(hist_lut.item, "plot"):
                hist_lut.item.plot.hide()

            # Réduire fortement la zone histogramme
            hist_lut.item.vb.setMaximumWidth(1)
            hist_lut.item.vb.setMinimumWidth(1)

            # Largeur LUT / axe
            hist_lut.setFixedWidth(80)
            hist_lut.gradient.setFixedWidth(15)
            hist_lut.item.axis.setWidth(45)
            hist_lut.item.axis.setStyle(tickTextOffset=4)

            # Adapter la largeur du bouton Menu
            menu_width = hist_lut.gradient.width() + hist_lut.item.axis.width() + 10
            im.ui.menuBtn.setFixedWidth(menu_width)

            # Ajouter l'ImageView au layout
            v.addWidget(im, stretch=1)

            # Stocker la référence pour les updates
            self.im_widgets[channel] = im

            status = QLabel("x: -, y: -, Value: -")
            status.setStyleSheet("color: #aaa; padding: 2px;")
            status.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
            status.setMinimumWidth(220)
            status.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

            cb_autoscale = QCheckBox("Autoscale")
            cb_autoscale.setChecked(autoscale_default)
            cb_autoscale.setStyleSheet(CHECKBOX_STYLE)

            cb_lock = QCheckBox("Lock")
            cb_lock.setChecked(lock_default)
            cb_lock.setStyleSheet(CHECKBOX_STYLE)

            cb_grid = QCheckBox("Grid")
            cb_grid.setChecked(grid_default)
            cb_grid.setStyleSheet(CHECKBOX_STYLE)

            btn_levels = QPushButton("Set Levels")
            btn_levels.setFixedHeight(22)

            btn_reset_levels = QPushButton("Reset Levels")
            btn_reset_levels.setFixedHeight(22)

            footer = QWidget()
            h = QHBoxLayout(footer)
            h.setContentsMargins(0, 0, 0, 0)
            h.setSpacing(8)

            h.addWidget(status, stretch=1)
            h.addWidget(cb_autoscale)
            h.addWidget(cb_lock)
            h.addWidget(cb_grid)
            h.addWidget(btn_levels)
            h.addWidget(btn_reset_levels)

            v.addWidget(footer)
            self.im_status_labels[channel] = status

            # ---------- Fonctions LUT / niveaux ----------
            def _set_levels(_=False, _im=im, ch=channel):
                self.channel_autoscale[ch] = False
                cb_autoscale.blockSignals(True)
                cb_autoscale.setChecked(False)
                cb_autoscale.blockSignals(False)

                try:
                    lo0, hi0 = _im.getLevels()
                except Exception:
                    lo0, hi0 = self._get_image_minmax_from_widget(_im, channel=ch)

                res = ask_levels_min_max(
                    parent=None,
                    title=f"{ch} - LUT",
                    lo0=lo0,
                    hi0=hi0
                )
                if res is None:
                    return

                lo, hi = res
                if hi <= lo:
                    QMessageBox.warning(None, "Invalid LUT values", "Max must be greater than Min.")
                    return

                self.apply_levels_to_channel(ch, float(lo), float(hi))

            def _reset_levels(_=False, ch=channel):
                self.channel_autoscale[ch] = False
                cb_autoscale.blockSignals(True)
                cb_autoscale.setChecked(False)
                cb_autoscale.blockSignals(False)

                self.autoscale_channel_levels(ch)

            def _on_autoscale_toggled(checked, ch=channel):
                self.channel_autoscale[ch] = bool(checked)
                if checked:
                    self.autoscale_channel_levels(ch)

            def _on_lock_toggled(checked, _im=im, ch=channel):
                self.channel_lock[ch] = bool(checked)
                _im.getView().setAspectLocked(bool(checked))

            # ---------- Initialisation LUT ----------
            self.autoscale_channel_levels(channel)

            # ---------- Grid ON/OFF ----------
            im.getView().showGrid(cb_grid.isChecked(), cb_grid.isChecked())
            cb_grid.toggled.connect(lambda checked, _im=im: _im.getView().showGrid(checked, checked))

            # ---------- Connexions ----------
            cb_autoscale.toggled.connect(_on_autoscale_toggled)
            cb_lock.toggled.connect(_on_lock_toggled)
            cb_grid.toggled.connect(lambda checked, ch=channel: self.channel_grid.__setitem__(ch, bool(checked)))
            btn_levels.clicked.connect(_set_levels)
            btn_reset_levels.clicked.connect(_reset_levels)

            # ---------- Sauvegarder états ----------
            self.channel_controls[channel] = {
                    "autoscale": cb_autoscale,
                    "lock": cb_lock,
                    "grid": cb_grid,
                    "Levels": btn_levels,
                    "ResetLevels": btn_reset_levels,
            }

            # Placement en grille 2 lignes
            row = index % 2
            col = index // 2
            grid.addWidget(container, row, col)

        # --- LineProfileDock: mise à jour des canaux / ImageView ---
        _im_with_cam = dict(self.im_widgets)
        if hasattr(self, "camera_widget") and self.camera_widget is not None:
            _im_with_cam["Camera"] = self.camera_widget.image_view

        if hasattr(self, "line_profile_widget") and self.line_profile_widget is not None:
            self.line_profile_widget.set_im_widgets(_im_with_cam)

        if hasattr(self, "histogram_widget") and self.histogram_widget is not None:
            self.histogram_widget.set_im_widgets(_im_with_cam)
        
        # Ajouter au splitter
        self.splitter.addWidget(grid_host)
        self.splitter.setStretchFactor(0, 1)

        if hasattr(self, "stitch_widget"):
            self.stitch_widget.set_channel_list(active_channels)