"""Layout definition for the DeepLight main window.

This module is hand-written and hand-maintained: there is no ``.ui`` source
file anywhere in the repository, and it composes the project's own widgets
directly. It deliberately does *not* use the Qt Designer / ``pyside6-uic``
naming convention, so nothing here suggests the file can be regenerated —
doing so would discard the custom widgets and stylesheets defined below.

``MainWindowLayout.build(window)`` creates the widgets and attaches them to
the window. Connections to the window's own slots are made in
``MainWindow._connect_actions()``, not here, so that this module never
depends on the window's method names.
"""

from PySide6.QtCore import (QSize, Qt)
from PySide6.QtGui import (QIcon, QTransform, QShortcut, QKeySequence)
from PySide6.QtWidgets import (QApplication, QLineEdit, QCheckBox, QDockWidget, QGridLayout, QGroupBox, QSplitter, QDialog, QDialogButtonBox, QFormLayout, QDoubleSpinBox,
                                QHBoxLayout, QVBoxLayout, QLabel, QPushButton, QSizePolicy, QSpacerItem, QTabWidget, QWidget, QMessageBox)
from .resources import icon_path
from .managers.Scan_Types import image_rows_by_axis
from .widgets.Scan_Widget import ScanWidget
from .widgets.Laser_Widget import LaserWidget
from .widgets.Detector_Widget import DetectorWidget
from .widgets.Positioner_Widget import PositionerWidget
from .widgets.Save_Widget import SaveWidget
from .widgets.Analog_visualizer_widget import AnalogOutVisualizerWidget
from .widgets.Stepper_visualizer_widget import StepperVisualizerWidget
from .widgets.Nyquist_widget import NyquistWidget
from .widgets.Depth_Compensation_Widget import DepthCompensationWidget
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
    """Open a small dialog to enter the min/max LUT levels."""
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

def allow_narrow(widget, minimum: int = 0):
    """Let a widget shrink horizontally below its natural minimum width.

    QMainWindow gives the central widget priority over the docks, so while the
    central area claims a large minimum width it cannot be narrowed and the side
    panels cannot be widened on a small screen. The floor came from the widest
    tab page, not from the image itself. Ignoring the horizontal size hint is
    what makes the split adjustable.
    """
    widget.setMinimumWidth(minimum)
    policy = widget.sizePolicy()
    policy.setHorizontalPolicy(QSizePolicy.Policy.Ignored)
    widget.setSizePolicy(policy)


class MainWindowLayout:
    """Construction de l'interface principale DeepLight et de ses widgets centraux."""

    def build(self, window):
        """Create the main window's widgets and attach them to ``window``."""
        if not window.objectName():
            window.setObjectName("MainWindow")
        window.resize(1692, 1596)

        #####↓ Initialisation   #####
        self.im_status_labels = {}
        self._zoom_roi = None
        self._zoom_roi_view = None

        # Création du widget central
        self.centralwidget = QWidget(window)
        self.centralwidget.setObjectName(u"centralwidget")

        # Layout principal du widget central
        self.gridLayout_2 = QGridLayout(self.centralwidget)
        self.gridLayout_2.setObjectName(u"gridLayout_2")
        
        # Layout secondaire (pour organiser les éléments)
        self.gridLayout_11 = QGridLayout()
        self.gridLayout_11.setObjectName(u"gridLayout_11")

################# Barre d'action ###################
        window.setCentralWidget(self.centralwidget)
        self.dockWidget_preview = QDockWidget(window)
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
       
        icon1 = QIcon(icon_path("play.svg"))
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
        icon2 = QIcon(icon_path("stop.svg"))
        self.pushButton_stop.setIcon(icon2)
        self.pushButton_stop.setIconSize(QSize(32, 32))
        self.pushButton_stop.setFlat(True)

        self.gridLayout_81.addWidget(self.pushButton_stop, 0, 4, 1, 1)

        self.pushButton_previewcontinuous = QPushButton(self.groupBox_11)
        self.pushButton_previewcontinuous.setObjectName(u"pushButton_previewcontinuous")
        self.pushButton_previewcontinuous.setStyleSheet(TRANSPARENT_ICON_BUTTON_CHECKABLE_STYLE)
        icon3 = QIcon(icon_path("repeat.svg"))
        self.pushButton_previewcontinuous.setIcon(icon3)
        self.pushButton_previewcontinuous.setIconSize(QSize(32, 32))
        self.pushButton_previewcontinuous.setFlat(True)
        self.pushButton_previewcontinuous.setCheckable(True)

        self.gridLayout_81.addWidget(self.pushButton_previewcontinuous, 0, 2, 1, 1)

        self.pushButton_acquisitionStart = QPushButton(self.groupBox_11)
        self.pushButton_acquisitionStart.setObjectName(u"pushButton_acquisitionStart")
        self.pushButton_acquisitionStart.setStyleSheet(TRANSPARENT_ICON_BUTTON_CHECKABLE_STYLE)

        icon4 = QIcon(icon_path("REC.svg"))
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
        window.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, self.dockWidget_preview)

##################  Left panel dock ####################
        self.left_panel_dock = PanelDock("", window)

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

        window.addDockWidget(Qt.LeftDockWidgetArea, self.left_panel_dock)

##################  Right panel dock ####################
        self.right_panel_dock = PanelDock("Helpers", window)

        self.analog_out_widget = AnalogOutVisualizerWidget()
        self.visu_step_widget = StepperVisualizerWidget()
        self.nyquist_widget = NyquistWidget()
        self.depth_comp_widget = DepthCompensationWidget()
        self.line_profile_widget = LineProfileWidget()
        self.histogram_widget = HistogramWidget()
        self.frc_widget = FRCWidget()
        self.log_widget = LogWidget()

        self.analog_panel = CollapsiblePanel("Analog Visualizer", self.analog_out_widget, collapsed=True, preferred_content_height=400, parent=self.right_panel_dock.container)
        self.stepper_panel = CollapsiblePanel("Stepper Visualizer", self.visu_step_widget, collapsed=True, preferred_content_height=300, parent=self.right_panel_dock.container)
        self.nyquist_panel = CollapsiblePanel("Nyquist", self.nyquist_widget, collapsed=True, parent=self.right_panel_dock.container)
        self.depth_comp_panel = CollapsiblePanel("Depth Power", self.depth_comp_widget, collapsed=True, parent=self.right_panel_dock.container)
        self.line_profile_panel = CollapsiblePanel("Line Profile", self.line_profile_widget, collapsed=True, preferred_content_height=300, parent=self.right_panel_dock.container)
        self.histogram_panel = CollapsiblePanel("Histogram", self.histogram_widget, collapsed=True, preferred_content_height=300, parent=self.right_panel_dock.container)
        self.frc_panel = CollapsiblePanel("FRC", self.frc_widget, collapsed=True, preferred_content_height=300, parent=self.right_panel_dock.container)
        self.log_panel = CollapsiblePanel("Logs", self.log_widget, collapsed=False, preferred_content_height=200, parent=self.right_panel_dock.container)

        self.right_panel_dock.add_panel(self.analog_panel)
        self.right_panel_dock.add_panel(self.stepper_panel)
        self.right_panel_dock.add_panel(self.nyquist_panel)
        self.right_panel_dock.add_panel(self.depth_comp_panel)
        self.right_panel_dock.add_panel(self.line_profile_panel)
        self.right_panel_dock.add_panel(self.histogram_panel)
        self.right_panel_dock.add_panel(self.frc_panel)
        self.right_panel_dock.add_panel(self.log_panel)

        window.addDockWidget(Qt.RightDockWidgetArea, self.right_panel_dock)

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

        # Image d'attente : les mêmes axes que ceux du premier scan, dans le
        # sens où il sera affiché. Prendre les lignes de scan dans l'ordre
        # donnerait une image transposée sur un champ non carré, et elle
        # changerait de forme à la première frame reçue.
        scan_parameters = self.scan_widget.get_scan_parameters()
        row_x, row_y = image_rows_by_axis(scan_parameters)

        default_pix_x = int(row_x["pixels"]) if row_x else 1
        default_pix_y = int(row_y["pixels"]) if row_y else 1
        default_width_um = float(row_x["size_um"]) if row_x else 1.0
        default_height_um = float(row_y["size_um"]) if row_y else 1.0

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
        allow_narrow(self.im_widget)

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

        # ---- Largeur de la zone centrale ----
        # Sans ceci, le minimum du plus large onglet impose un plancher à toute
        # la zone centrale et les bandeaux ne peuvent plus être élargis.
        for _w in (self.centralwidget, self.tabWidget, self.splitter,
                   self.tab_preview, self.stitch_widget, self.camera_widget,
                   self.spectro_widget):
            allow_narrow(_w)
        self.splitter.setChildrenCollapsible(True)

        ################# Barre de progression globale (bas du GUI) ######################
        self.global_progress_widget = GlobalProgressWidget(window)
        window.statusBar().addPermanentWidget(self.global_progress_widget, 1)

        # ---- Bascules d'affichage des bandeaux latéraux ----
        # Placées dans la barre de statut et non dans la barre d'action : celle-ci
        # partage sa colonne avec le bandeau gauche, donc tout bouton ajouté là
        # élargirait le bandeau (+68 px mesurés pour ces deux-là).
        self.pushButton_togglePanelLeft = QPushButton(window)
        self.pushButton_togglePanelLeft.setObjectName(u"pushButton_togglePanelLeft")
        self.pushButton_togglePanelLeft.setStyleSheet(TRANSPARENT_ICON_BUTTON_CHECKABLE_STYLE)
        self.pushButton_togglePanelLeft.setIcon(QIcon(icon_path("panel_left.svg")))
        self.pushButton_togglePanelLeft.setIconSize(QSize(18, 18))
        self.pushButton_togglePanelLeft.setFixedSize(24, 22)
        self.pushButton_togglePanelLeft.setFlat(True)
        self.pushButton_togglePanelLeft.setCheckable(True)
        self.pushButton_togglePanelLeft.setChecked(True)

        self.pushButton_togglePanelRight = QPushButton(window)
        self.pushButton_togglePanelRight.setObjectName(u"pushButton_togglePanelRight")
        self.pushButton_togglePanelRight.setStyleSheet(TRANSPARENT_ICON_BUTTON_CHECKABLE_STYLE)
        self.pushButton_togglePanelRight.setIcon(QIcon(icon_path("panel_right.svg")))
        self.pushButton_togglePanelRight.setIconSize(QSize(18, 18))
        self.pushButton_togglePanelRight.setFixedSize(24, 22)
        self.pushButton_togglePanelRight.setFlat(True)
        self.pushButton_togglePanelRight.setCheckable(True)
        self.pushButton_togglePanelRight.setChecked(True)

        window.statusBar().addPermanentWidget(self.pushButton_togglePanelLeft, 0)
        window.statusBar().addPermanentWidget(self.pushButton_togglePanelRight, 0)

        ##################  Helpers   ##################
        self._apply_texts(window)

        # The action-bar buttons are connected to the window's slots by
        # MainWindow._connect_actions(); this module must not depend on the
        # window's method names.

        # ---------------- Global shortcuts ----------------
        self.shortcut_preview_single = QShortcut(QKeySequence(Qt.Key_Space), window)
        self.shortcut_preview_single.setContext(Qt.ApplicationShortcut)
        self.shortcut_preview_single.activated.connect(self._shortcut_preview_single)

        self.shortcut_preview_continuous = QShortcut(QKeySequence("Ctrl+Space"), window)
        self.shortcut_preview_continuous.setContext(Qt.ApplicationShortcut)
        self.shortcut_preview_continuous.activated.connect(self._shortcut_preview_continuous)

        self.shortcut_stop = QShortcut(QKeySequence(Qt.Key_Escape), window)
        self.shortcut_stop.setContext(Qt.ApplicationShortcut)
        self.shortcut_stop.activated.connect(self._shortcut_stop)

        # ---- Contraste : F1 cale le point noir, F2 le point blanc ----
        self.shortcut_levels_min = QShortcut(QKeySequence(Qt.Key_F1), window)
        self.shortcut_levels_min.setContext(Qt.ApplicationShortcut)
        self.shortcut_levels_min.activated.connect(
            lambda: self.pull_levels_to_image_extreme("min")
        )

        self.shortcut_levels_max = QShortcut(QKeySequence(Qt.Key_F2), window)
        self.shortcut_levels_max.setContext(Qt.ApplicationShortcut)
        self.shortcut_levels_max.activated.connect(
            lambda: self.pull_levels_to_image_extreme("max")
        )

        # ---- Snapshot : Ctrl+P écrit un PNG, Ctrl+Maj+C copie ----
        # Branchés dans MainWindow._connect_actions() : le rendu a besoin des
        # paramètres de scan pour la barre d'échelle, que ce module ignore.
        self.shortcut_snapshot_png = QShortcut(QKeySequence("Ctrl+P"), window)
        self.shortcut_snapshot_png.setContext(Qt.ApplicationShortcut)

        self.shortcut_snapshot_clipboard = QShortcut(QKeySequence("Ctrl+Shift+C"), window)
        self.shortcut_snapshot_clipboard.setContext(Qt.ApplicationShortcut)

        # ---- ROI de zoom : Ctrl+R dessine, Ctrl+Entrée applique ----
        # L'application est branchée dans MainWindow._connect_actions() : elle
        # a besoin des paramètres de scan, que ce module ne connaît pas.
        self.shortcut_zoom_roi = QShortcut(QKeySequence("Ctrl+R"), window)
        self.shortcut_zoom_roi.setContext(Qt.ApplicationShortcut)
        self.shortcut_zoom_roi.activated.connect(self.toggle_zoom_roi)

        self.shortcut_zoom_roi_apply = QShortcut(QKeySequence("Ctrl+Return"), window)
        self.shortcut_zoom_roi_apply.setContext(Qt.ApplicationShortcut)

        # ---- Bandeaux latéraux : bascules + raccourcis ----
        # Câblé ici plutôt que dans MainWindow : cela ne touche que des widgets
        # de ce module, aucun slot de la fenêtre n'est nommé.
        self._window = window
        self.pushButton_togglePanelLeft.toggled.connect(self._set_left_panel_visible)
        self.pushButton_togglePanelRight.toggled.connect(self.right_panel_dock.setVisible)

        self.shortcut_toggle_panel_left = QShortcut(QKeySequence(Qt.Key_F9), window)
        self.shortcut_toggle_panel_left.setContext(Qt.ApplicationShortcut)
        self.shortcut_toggle_panel_left.activated.connect(self.pushButton_togglePanelLeft.toggle)

        self.shortcut_toggle_panel_right = QShortcut(QKeySequence(Qt.Key_F10), window)
        self.shortcut_toggle_panel_right.setContext(Qt.ApplicationShortcut)
        self.shortcut_toggle_panel_right.activated.connect(self.pushButton_togglePanelRight.toggle)

        self.shortcut_shutter = QShortcut(QKeySequence("Ctrl+Q"), window)
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

    # ---- ROI de zoom -----------------------------------------------------

    def _roi_host(self):
        """The image the ROI belongs to: the hovered one, else the first."""
        items = [(ch, im) for ch, im in self.im_widgets.items() if im is not None]
        if not items:
            return None, None
        for ch, im in items:
            if im.underMouse():
                return ch, im
        return items[0]

    def toggle_zoom_roi(self):
        """Show or hide the rectangle used to pick the next field of view."""
        if self._zoom_roi is not None:
            self.clear_zoom_roi()
            return

        channel, im = self._roi_host()
        if im is None:
            return

        view = im.getView()
        (x0, x1), (y0, y1) = view.viewRange()
        w = (x1 - x0) / 3.0
        h = (y1 - y0) / 3.0

        roi = pg.RectROI(
            pos=(x0 + (x1 - x0 - w) / 2.0, y0 + (y1 - y0 - h) / 2.0),
            size=(w, h),
            pen=pg.mkPen("#9CFF9C", width=2),
            rotatable=False,
        )
        roi.addScaleHandle([0, 0], [1, 1])
        roi.addScaleHandle([1, 1], [0, 0])
        roi.setZValue(20)
        view.addItem(roi)

        self._zoom_roi = roi
        self._zoom_roi_view = view

    def clear_zoom_roi(self):
        if self._zoom_roi is None:
            return
        try:
            self._zoom_roi_view.removeItem(self._zoom_roi)
        except Exception as e:
            logger.debug(f"[ROI] could not remove the zoom rectangle: {e}")
        self._zoom_roi = None
        self._zoom_roi_view = None

    def get_zoom_roi_region(self):
        """Return the ROI as (x0, y0, width, height) in image µm, or None."""
        if self._zoom_roi is None:
            return None
        pos = self._zoom_roi.pos()
        size = self._zoom_roi.size()
        w, h = float(size.x()), float(size.y())
        if w <= 0.0 or h <= 0.0:
            return None
        return float(pos.x()), float(pos.y()), w, h

    def _levels_targets(self):
        """Which images a contrast shortcut acts on.

        The one under the cursor when there is one, so a shortcut does what the
        user is looking at; otherwise every displayed channel.
        """
        hovered = [
            (ch, im) for ch, im in self.im_widgets.items()
            if im is not None and im.underMouse()
        ]
        return hovered or [
            (ch, im) for ch, im in self.im_widgets.items() if im is not None
        ]

    def _current_levels(self, im):
        try:
            lo, hi = im.getLevels()
            return float(lo), float(hi)
        except Exception:
            return None

    def pull_levels_to_image_extreme(self, which: str):
        """Pull one LUT bound onto the image's own minimum or maximum.

        F1 brings the black point down to the darkest pixel, F2 the white point
        up to the brightest, each leaving the other bound where the user put it.
        """
        for channel, im in self._levels_targets():
            lo_img, hi_img = self._get_image_minmax_from_widget(im, channel=channel)

            current = self._current_levels(im)
            lo, hi = current if current is not None else (lo_img, hi_img)

            if which == "min":
                lo = lo_img
            else:
                hi = hi_img

            self.apply_levels_to_channel(channel, lo, hi)

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
    
    def _set_left_panel_visible(self, visible: bool):
        """Fold or unfold the left panel, giving its width back to the centre.

        The action bar shares the left dock column with this panel, and Qt keeps
        a column at the width it had. Without the resize below, folding the panel
        would hide its content without freeing a single pixel: the action bar
        would simply hold the column open at the panel's old width.
        """
        self.left_panel_dock.setVisible(visible)
        if not visible and getattr(self, "_window", None) is not None:
            self._window.resizeDocks(
                [self.dockWidget_preview],
                [self.dockWidget_preview.minimumSizeHint().width()],
                Qt.Horizontal,
            )

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
    
    def _apply_texts(self, window):
        """Set the window/tab titles and the action-bar tooltips."""
        window.setWindowTitle("DeepLight")
        self.tabWidget.setTabText(self.tabWidget.indexOf(self.tab_preview), "Scan")
        self.dockWidget_preview.setWindowTitle("Commands")
        self.groupBox_11.setTitle("")
        self.pushButton_acquisitionStart.setToolTip("Start the scanning, saving data")
        self.pushButton_acquisitionStart.setText("")
        self.pushButton_previewSingle.setToolTip("Start the scanning without storing data")
        self.pushButton_previewSingle.setText("")
        self.pushButton_stop.setToolTip("Stop the scan")
        self.pushButton_stop.setText("")
        self.pushButton_shutter.setToolTip("Toggle shutter open/closed")
        self.pushButton_shutter.setText("")
        self.pushButton_togglePanelLeft.setToolTip("Show/hide the left panel (F9)")
        self.pushButton_togglePanelLeft.setText("")
        self.pushButton_togglePanelRight.setToolTip("Show/hide the Helpers panel (F10)")
        self.pushButton_togglePanelRight.setText("")

    def get_scan_tab_references(self):
        """Return the references to the ImageView widgets and to the QSplitter."""
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
        allow_narrow(grid_host)
        grid = QGridLayout(grid_host)
        grid.setContentsMargins(6, 6, 6, 6)
        grid.setSpacing(8)

        for index, channel in enumerate(channels):
            # Container d’un canal
            container = QWidget()
            allow_narrow(container)
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
            allow_narrow(im)
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