from PySide6.QtWidgets import (QWidget, QStyle, QStyleOptionSlider, QHBoxLayout, QVBoxLayout, QGroupBox, QGridLayout, QLabel,
                               QSlider, QLineEdit, QComboBox, QRadioButton, QSizePolicy, QMessageBox)
from PySide6.QtCore import Qt
from PySide6.QtGui import QPainter, QPen, QColor
from math import sqrt

class SliderWithNyquistLine(QWidget):
    def __init__(self, nyquist_value=2.1, parent=None):
        super().__init__(parent)

        self.nyquist_value = nyquist_value

        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(10, 100)   # 0.5 -> 5.0 avec step de 0.05
        self.slider.setValue(42)        # 2.1 par défaut
        self.slider.setTickPosition(QSlider.TicksBelow)
        self.slider.setTickInterval(15)
        self.slider.setStyleSheet("""
            QSlider::groove:horizontal {
                border: 1px solid #444;
                height: 8px;
                background: #333;
                border-radius: 4px;
            }
            QSlider::handle:horizontal {
                background: #777;
                border: 1px solid #444;
                width: 16px;
                margin: -4px 0;
                border-radius: 8px;
            }
        """)

        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.slider)

    def _sampling_to_slider(self, value: float) -> int:
        return int(round(value / 0.05))

    def paintEvent(self, event):
        super().paintEvent(event)

        opt = QStyleOptionSlider()
        self.slider.initStyleOption(opt)

        opt.sliderPosition = self._sampling_to_slider(self.nyquist_value)
        handle_rect = self.slider.style().subControlRect(
            QStyle.CC_Slider, opt, QStyle.SC_SliderHandle, self.slider
        )
        line_x = handle_rect.center().x()

        slider_geo = self.slider.geometry()

        painter = QPainter(self)
        painter.setPen(QPen(QColor("#FF7700"), 2, Qt.SolidLine))
        painter.drawLine(line_x, slider_geo.top(), line_x, slider_geo.top() + 18)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.update()

class NyquistWidget(QWidget):
    """DockWidget for the Nyquist parameters."""

    def __init__(self, parent=None):
        super().__init__(parent)

        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)

        self.main_layout = QVBoxLayout(self)
        self.main_layout.setContentsMargins(2, 2, 2, 2)
        self.main_layout.setSpacing(4)

        content_widget = QWidget()
        content_widget.setMinimumWidth(0)
        content_widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)

        self.nyquist_layout = QVBoxLayout(content_widget)
        self.nyquist_layout.setContentsMargins(2, 2, 2, 2)
        self.nyquist_layout.setSpacing(4)

        # Ajout des contrôles pour Nyquist
        self._add_nyquist_controls()

        self.main_layout.addWidget(content_widget)
        self.main_layout.addStretch()

    def _add_nyquist_controls(self):
        """Add the controls for the Nyquist parameters."""

        # GroupBox pour les paramètres de Nyquist
        nyquist_group = QGroupBox()
        nyquist_group.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        nyquist_group.setStyleSheet("""
            QGroupBox {
                background-color: #2a2a2a;
                border: 1px solid #444;
                border-radius: 4px;
                margin-top: 2px;
                margin-bottom: 2px;
                padding: 0px;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                color: white;
                font-weight: bold;
                font-size: 14px;
                padding: 0 5px;
            }
        """)

        # Layout principal pour les paramètres de Nyquist
        main_grid_layout = QGridLayout()
        main_grid_layout.setHorizontalSpacing(6)
        main_grid_layout.setVerticalSpacing(4)
        main_grid_layout.setContentsMargins(6, 6, 6, 6)

        main_grid_layout.setColumnMinimumWidth(0, 20)
        main_grid_layout.setColumnMinimumWidth(1, 20)
        main_grid_layout.setColumnMinimumWidth(2, 20)
        main_grid_layout.setColumnMinimumWidth(3, 20)

        main_grid_layout.setColumnStretch(0, 0)
        main_grid_layout.setColumnStretch(1, 1)
        main_grid_layout.setColumnStretch(2, 0)
        main_grid_layout.setColumnStretch(3, 1)

        # Ligne 0: Menu déroulant pour l'objectif
        objective_label = QLabel("Objective:")
        objective_label.setStyleSheet("color: white;")

        self.objective_combo = QComboBox()
        self.objective_combo.addItems(["Nikon CFI Plan Apo 25XC W 1300", "Nikon CFI Plan Fluor 10X W", "Custom"])
        self.objective_combo.setStyleSheet("""
            QComboBox {
                background-color: #333;
                color: white;
                border: 1px solid #555;
                border-radius: 3px;
                padding: 2px;
                min-height: 20px;
            }
        """)

        main_grid_layout.addWidget(objective_label, 0, 0)
        self.objective_combo.setMinimumWidth(0)
        self.objective_combo.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        main_grid_layout.addWidget(self.objective_combo, 0, 1, 1, 3)

        # Ligne 1: Wavelength et Refractive Index
        wavelength_label = QLabel("Wavelength (nm):")
        wavelength_label.setStyleSheet("color: white;")

        self.wavelength_edit = QLineEdit("920")
        self.wavelength_edit.setStyleSheet("""
            QLineEdit {
                background-color: #333;
                color: white;
                border: 1px solid #555;
                border-radius: 3px;
                padding: 2px;
                min-height: 20px;
            }
        """)
        self.wavelength_edit.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        ref_index_label = QLabel("Ref Index:")
        ref_index_label.setStyleSheet("color: white;")

        self.ref_index_edit = QLineEdit()
        self.ref_index_edit.setStyleSheet("""
            QLineEdit {
                background-color: #333;
                color: white;
                border: 1px solid #555;
                border-radius: 3px;
                padding: 2px;
                min-height: 20px;
            }
        """)
        self.ref_index_edit.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        main_grid_layout.addWidget(wavelength_label, 1, 0)
        main_grid_layout.addWidget(self.wavelength_edit, 1, 1)
        main_grid_layout.addWidget(ref_index_label, 1, 2)
        main_grid_layout.addWidget(self.ref_index_edit, 1, 3)

        na_label = QLabel("NA:")
        na_label.setStyleSheet("color: white;")

        self.na_edit = QLineEdit()
        self.na_edit.setStyleSheet("""
            QLineEdit {
                background-color: #333;
                color: white;
                border: 1px solid #555;
                border-radius: 3px;
                padding: 2px;
                min-height: 20px;
            }
        """)
        self.na_edit.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        main_grid_layout.addWidget(na_label, 2, 0)
        main_grid_layout.addWidget(self.na_edit, 2, 1)

        # Ligne 3: Sélection de l'ordre du processus (1P, 2P, 3P)
        order_label = QLabel("Order:")
        order_label.setStyleSheet("color: white;")
        main_grid_layout.addWidget(order_label, 2, 2)

        radio_style = """
            QRadioButton {
                color: white;
                spacing: 5px;
            }
            QRadioButton::indicator {
                width: 12px;
                height: 12px;
                border-radius: 6px;
                border: 1px solid #555;
                background-color: #333;
            }
            QRadioButton::indicator:checked {
                background-color: #2E8B57;
                border: 1px solid #555;
            }
            QRadioButton::indicator:unchecked:hover {
                background-color: #444;
                border: 1px solid #777;
            }
            QRadioButton::indicator:checked:hover {
                background-color: #3AB16F;
                border: 1px solid #777;
            }
        """

        self.order_1p_radio = QRadioButton("1P")
        self.order_1p_radio.setStyleSheet(radio_style)
        self.order_1p_radio.setChecked(True)

        self.order_2p_radio = QRadioButton("2P")
        self.order_2p_radio.setStyleSheet(radio_style)

        self.order_3p_radio = QRadioButton("3P")
        self.order_3p_radio.setStyleSheet(radio_style)

        order_layout = QHBoxLayout()
        order_layout.setContentsMargins(0, 0, 0, 0)
        order_layout.setSpacing(8)
        order_layout.addWidget(self.order_1p_radio)
        order_layout.addWidget(self.order_2p_radio)
        order_layout.addWidget(self.order_3p_radio)
        order_layout.addStretch()

        order_widget = QWidget()
        order_widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        order_widget.setLayout(order_layout)
        main_grid_layout.addWidget(order_widget, 2, 3)

        # Ligne 4: Résolutions XY et Z
        xy_res_label = QLabel("XY Res (nm):")
        xy_res_label.setStyleSheet("color: white;")

        self.xy_res_edit = QLineEdit()
        self.xy_res_edit.setStyleSheet("""
            QLineEdit {
                background-color: #252525;
                color: #888;
                border: 1px solid #444;
                border-radius: 3px;
                padding: 2px;
                min-height: 20px;
            }
        """)
        self.xy_res_edit.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.xy_res_edit.setReadOnly(True)

        z_res_label = QLabel("Z Res (nm):")
        z_res_label.setStyleSheet("color: white;")

        self.z_res_edit = QLineEdit()
        self.z_res_edit.setStyleSheet("""
            QLineEdit {
                background-color: #252525;
                color: #888;
                border: 1px solid #444;
                border-radius: 3px;
                padding: 2px;
                min-height: 20px;
            }
        """)
        self.z_res_edit.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.z_res_edit.setReadOnly(True)

        main_grid_layout.addWidget(xy_res_label, 4, 0)
        main_grid_layout.addWidget(self.xy_res_edit, 4, 1)
        main_grid_layout.addWidget(z_res_label, 4, 2)
        main_grid_layout.addWidget(self.z_res_edit, 4, 3)

        # Ligne 5: En-tête sampling
        sampling_label = QLabel("Sampling:")
        sampling_label.setStyleSheet("color: white;")
        main_grid_layout.addWidget(sampling_label, 5, 0)

        under_label = QLabel("Under")
        under_label.setStyleSheet("color: white;")

        over_label = QLabel("Over")
        over_label.setStyleSheet("color: white;")
        over_label.setAlignment(Qt.AlignRight)

        main_grid_layout.addWidget(under_label, 5, 1)
        main_grid_layout.addWidget(over_label, 5, 3)

        # Styles communs
        editable_style = """
            QLineEdit {
                background-color: #333;
                color: white;
                border: 1px solid #555;
                border-radius: 3px;
                padding: 2px;
                min-height: 20px;
            }
        """

        # Ligne 6: XY sampling
        xy_sampling_label = QLabel("XY Nyquist:")
        xy_sampling_label.setStyleSheet("color: white;")
        main_grid_layout.addWidget(xy_sampling_label, 6, 0)

        self.sampling_xy_edit = QLineEdit("2.10")
        self.sampling_xy_edit.setStyleSheet(editable_style)
        self.sampling_xy_edit.setMinimumWidth(0)
        self.sampling_xy_edit.setMaximumWidth(60)
        self.sampling_xy_edit.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        main_grid_layout.addWidget(self.sampling_xy_edit, 6, 1)

        self.slider_xy_with_line = SliderWithNyquistLine(nyquist_value=2.1)
        self.slider_xy = self.slider_xy_with_line.slider
        self.slider_xy_with_line.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        main_grid_layout.addWidget(self.slider_xy_with_line, 6, 2, 1, 2)

        # Ligne 7: Pixel Size
        pixel_size_label = QLabel("Pixel Size (nm):")
        pixel_size_label.setStyleSheet("color: white;")
        main_grid_layout.addWidget(pixel_size_label, 7, 0)

        self.pixel_size_edit = QLineEdit()
        self.pixel_size_edit.setStyleSheet(editable_style)
        self.pixel_size_edit.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        main_grid_layout.addWidget(self.pixel_size_edit, 7, 1, 1, 3)

        # Ligne 8: Z sampling
        z_sampling_label = QLabel("Z Nyquist:")
        z_sampling_label.setStyleSheet("color: white;")
        main_grid_layout.addWidget(z_sampling_label, 8, 0)

        self.sampling_z_edit = QLineEdit("2.10")
        self.sampling_z_edit.setStyleSheet(editable_style)
        self.sampling_z_edit.setMinimumWidth(0)
        self.sampling_z_edit.setMaximumWidth(60)
        self.sampling_z_edit.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        main_grid_layout.addWidget(self.sampling_z_edit, 8, 1)

        self.slider_z_with_line = SliderWithNyquistLine(nyquist_value=2.1)
        self.slider_z = self.slider_z_with_line.slider
        self.slider_z_with_line.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        main_grid_layout.addWidget(self.slider_z_with_line, 8, 2, 1, 2)

        # Ligne 9: Z Step
        z_step_label = QLabel("Z Step (nm):")
        z_step_label.setStyleSheet("color: white;")
        main_grid_layout.addWidget(z_step_label, 9, 0)

        self.z_step_edit = QLineEdit()
        self.z_step_edit.setStyleSheet(editable_style)
        self.z_step_edit.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        main_grid_layout.addWidget(self.z_step_edit, 9, 1, 1, 3)

        nyquist_group.setLayout(main_grid_layout)
        self.nyquist_layout.addWidget(nyquist_group)

        # Connexions
        self.objective_combo.currentTextChanged.connect(self.update_values)
        self.wavelength_edit.editingFinished.connect(self.update_values)
        self.ref_index_edit.editingFinished.connect(self.update_values)
        self.na_edit.editingFinished.connect(self.update_values)
        self.order_1p_radio.toggled.connect(self.update_values)
        self.order_2p_radio.toggled.connect(self.update_values)
        self.order_3p_radio.toggled.connect(self.update_values)

        self.slider_xy.valueChanged.connect(self.update_xy_slider_value)
        self.slider_z.valueChanged.connect(self.update_z_slider_value)

        self.sampling_xy_edit.editingFinished.connect(self.update_xy_from_sampling)
        self.sampling_z_edit.editingFinished.connect(self.update_z_from_sampling)

        self.pixel_size_edit.editingFinished.connect(self.update_xy_from_pixel_size)
        self.z_step_edit.editingFinished.connect(self.update_z_from_z_step)

        # Mise à jour initiale des valeurs
        self.update_values()

    def get_optics(self) -> dict:
        """Objective and the optical parameters it implies, for the saved data.

        These are the numbers one needs to re-derive a resolution from a file
        months later; they live in this panel, so it is this panel that reports
        them rather than the save manager guessing.
        """
        order = 1
        if self.order_2p_radio.isChecked():
            order = 2
        elif self.order_3p_radio.isChecked():
            order = 3

        return {
            "objective": self.objective_combo.currentText(),
            "numerical_aperture": self._safe_float(self.na_edit),
            "refractive_index": self._safe_float(self.ref_index_edit),
            "wavelength_nm": self._safe_float(self.wavelength_edit),
            "process_order": order,
            "xy_resolution_nm": self._safe_float(self.xy_res_edit),
            "z_resolution_nm": self._safe_float(self.z_res_edit),
            "nyquist_sampling_xy": self._safe_float(self.sampling_xy_edit),
            "nyquist_sampling_z": self._safe_float(self.sampling_z_edit),
        }

    def apply_optics(self, optics: dict):
        """Restore the panel from what get_optics() reported.

        The objective goes in first: choosing one imposes its NA and index, so
        writing them before would be overwritten. For a Custom objective they
        are the user's own values and are restored after.
        """
        optics = dict(optics or {})

        objective = str(optics.get("objective", "") or "")
        if objective and self.objective_combo.findText(objective) >= 0:
            self.objective_combo.setCurrentText(objective)

        if optics.get("wavelength_nm") is not None:
            self.wavelength_edit.setText(f"{float(optics['wavelength_nm']):g}")

        order = int(optics.get("process_order", 1) or 1)
        {1: self.order_1p_radio, 2: self.order_2p_radio,
         3: self.order_3p_radio}.get(order, self.order_1p_radio).setChecked(True)

        if objective == "Custom":
            if optics.get("numerical_aperture") is not None:
                self.na_edit.setText(f"{float(optics['numerical_aperture']):g}")
            if optics.get("refractive_index") is not None:
                self.ref_index_edit.setText(f"{float(optics['refractive_index']):g}")

        for key, edit in (("nyquist_sampling_xy", self.sampling_xy_edit),
                          ("nyquist_sampling_z", self.sampling_z_edit)):
            if optics.get(key) is not None:
                edit.setText(f"{float(optics[key]):.2f}")

        # Recalcule résolutions, pixel size et pas Z depuis ce qui vient d'être posé.
        self.update_values()

    def _set_editable(self, le: QLineEdit, editable: bool):
        le.setReadOnly(not editable)
        if editable:
            le.setStyleSheet("""
                QLineEdit {
                    background-color: #333;
                    color: white;
                    border: 1px solid #555;
                    border-radius: 3px;
                    padding: 2px;
                    min-height: 20px;
                }
            """)
        else:
            le.setStyleSheet("""
                QLineEdit {
                    background-color: #252525;
                    color: #888;
                    border: 1px solid #444;
                    border-radius: 3px;
                    padding: 2px;
                    min-height: 20px;
                }
            """)
    
    def _slider_to_sampling(self, slider_value: int) -> float:
        return slider_value * 0.05

    def _sampling_to_slider(self, sampling: float) -> int:
        value = int(round(sampling / 0.05))
        return max(10, min(100, value))

    def _safe_float(self, line_edit: QLineEdit):
        try:
            return float(line_edit.text().strip().replace(",", "."))
        except ValueError:
            return None

    def update_xy_slider_value(self, value):
        sampling_xy = self._slider_to_sampling(value)
        self.sampling_xy_edit.blockSignals(True)
        self.sampling_xy_edit.setText(f"{sampling_xy:.2f}")
        self.sampling_xy_edit.blockSignals(False)
        self.update_xy_from_sampling()

    def update_z_slider_value(self, value):
        sampling_z = self._slider_to_sampling(value)
        self.sampling_z_edit.blockSignals(True)
        self.sampling_z_edit.setText(f"{sampling_z:.2f}")
        self.sampling_z_edit.blockSignals(False)
        self.update_z_from_sampling()

    def update_xy_from_sampling(self):
        try:
            xy_res = float(self.xy_res_edit.text())
        except ValueError:
            return

        sampling_xy = self._safe_float(self.sampling_xy_edit)
        if sampling_xy is None or sampling_xy <= 0:
            return

        pixel_size = round(xy_res / sampling_xy)

        self.pixel_size_edit.blockSignals(True)
        self.pixel_size_edit.setText(f"{pixel_size}")
        self.pixel_size_edit.blockSignals(False)

        self.slider_xy.blockSignals(True)
        self.slider_xy.setValue(self._sampling_to_slider(sampling_xy))
        self.slider_xy.blockSignals(False)

    def update_z_from_sampling(self):
        try:
            z_res = float(self.z_res_edit.text())
        except ValueError:
            return

        sampling_z = self._safe_float(self.sampling_z_edit)
        if sampling_z is None or sampling_z <= 0:
            return

        z_step = round(z_res / sampling_z)

        self.z_step_edit.blockSignals(True)
        self.z_step_edit.setText(f"{z_step}")
        self.z_step_edit.blockSignals(False)

        self.slider_z.blockSignals(True)
        self.slider_z.setValue(self._sampling_to_slider(sampling_z))
        self.slider_z.blockSignals(False)

    def update_xy_from_pixel_size(self):
        try:
            xy_res = float(self.xy_res_edit.text())
        except ValueError:
            return

        pixel_size = self._safe_float(self.pixel_size_edit)
        if pixel_size is None or pixel_size <= 0:
            return

        sampling_xy = xy_res / pixel_size

        self.sampling_xy_edit.blockSignals(True)
        self.sampling_xy_edit.setText(f"{sampling_xy:.2f}")
        self.sampling_xy_edit.blockSignals(False)

        self.slider_xy.blockSignals(True)
        self.slider_xy.setValue(self._sampling_to_slider(sampling_xy))
        self.slider_xy.blockSignals(False)

    def update_z_from_z_step(self):
        try:
            z_res = float(self.z_res_edit.text())
        except ValueError:
            return

        z_step = self._safe_float(self.z_step_edit)
        if z_step is None or z_step <= 0:
            return

        sampling_z = z_res / z_step

        self.sampling_z_edit.blockSignals(True)
        self.sampling_z_edit.setText(f"{sampling_z:.2f}")
        self.sampling_z_edit.blockSignals(False)

        self.slider_z.blockSignals(True)
        self.slider_z.setValue(self._sampling_to_slider(sampling_z))
        self.slider_z.blockSignals(False)

    def update_values(self):
        """Update NA/RefIndex from the objective, then compute res/pixel."""
        objective = self.objective_combo.currentText()

        if objective == "Custom":
            # Mode manuel : l'utilisateur saisit NA / ref index
            self._set_editable(self.na_edit, True)
            self._set_editable(self.ref_index_edit, True)

            # (optionnel) valeurs par défaut si vides
            if not self.na_edit.text().strip():
                self.na_edit.setText("1.0")
            if not self.ref_index_edit.text().strip():
                self.ref_index_edit.setText("1.33")
        else:
            # Mode auto : valeurs imposées par l'objectif
            self._set_editable(self.na_edit, False)
            self._set_editable(self.ref_index_edit, False)

            if objective == "Nikon CFI Plan Apo 25XC W 1300":
                self.na_edit.setText("1.1")
                self.ref_index_edit.setText("1.33")
            elif objective == "Nikon CFI Plan Fluor 10X W":
                self.na_edit.setText("0.3")
                self.ref_index_edit.setText("1.33")

        # Déterminer l'ordre sélectionné
        order = 1
        if self.order_2p_radio.isChecked():
            order = 2
        elif self.order_3p_radio.isChecked():
            order = 3

        # Calcul des résolutions XY et Z
        try:
            wavelength = float(self.wavelength_edit.text())
            ref_index = float(self.ref_index_edit.text())
            na = float(self.na_edit.text())

            if na <= 0 or ref_index <= 0 or na > ref_index:
                QMessageBox.warning(
                    self,
                    "Invalid Optical Parameters",
                    "Invalid NA / Refractive Index combination.\n\n"
                    "Conditions:\n"
                    "NA must be > 0\n"
                    "Refractive index must be > 0\n"
                    "NA must be ≤ refractive index"
                )
                return

            xy_res = round(0.514 * wavelength / (na * sqrt(order)))
            z_res = round(
                0.88 * wavelength /
                (sqrt(order) * (ref_index - sqrt(ref_index**2 - na**2)))
            )

            self.xy_res_edit.setText(f"{xy_res}")
            self.z_res_edit.setText(f"{z_res}")

            # Recalcule les champs dépendants à partir des Nyquist XY/Z
            self.update_xy_from_sampling()
            self.update_z_from_sampling()

        except ValueError:
            self.xy_res_edit.setText("")
            self.z_res_edit.setText("")
            self.pixel_size_edit.setText("")
            self.z_step_edit.setText("")