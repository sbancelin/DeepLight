import sys
import os
import time
import numpy as np
import tifffile
from PyQt5.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, 
                             QHBoxLayout, QPushButton, QLabel, QSpinBox, QFileDialog, 
                             QMessageBox, QCheckBox, QGroupBox, QDoubleSpinBox, 
                             QGraphicsRectItem, QComboBox)
from PyQt5.QtGui import QPen, QColor
from PyQt5.QtCore import QTimer, Qt, QThread, pyqtSignal
import pyqtgraph as pg

# --- IMPORTS MATÉRIEL ---
from picam_manager import PiCamKuroManager

try:
    from libspecto import SpectoBIPD
    BIPD_AVAILABLE = True
except ImportError:
    BIPD_AVAILABLE = False


# ==========================================
# THREAD DÉDIÉ AU MODE LIVE (ANTI-LAG)
# ==========================================
class LiveWorker(QThread):
    frame_ready = pyqtSignal(np.ndarray)

    def __init__(self, cam, cam_params):
        super().__init__()
        self.cam = cam
        self.cam_params = cam_params
        self.running = False
        self.ready_for_next = True

    def run(self):
        self.running = True
        while self.running:
            if self.ready_for_next:
                try:
                    self.ready_for_next = False
                    img = self.cam.snap(self.cam_params)
                    if img is not None and self.running:
                        self.frame_ready.emit(img)
                except Exception as e:
                    print(f"Erreur pendant le Live : {e}")
                    self.ready_for_next = True
                    self.msleep(100)
            self.msleep(10)

    def stop(self):
        self.running = False
        self.wait()


class CameraInterface(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Contrôle Caméra & Specto BIPD - Séquence Sécurisée")
        self.resize(1350, 950)

        # Variables Caméra & Affichage
        self.cam = None
        self.live_running = False
        self.live_worker = None
        self.roi_rect = None
        self.inner_rect = None 
        self._updating_roi_ui = False
        self.cam_params = {"exposure_ms": 100.0, "roi_enabled": False, "binning": "1x1"}
        self.current_img_w = 0
        self.current_img_h = 0

        # Variables BIPD
        self.bipd = None
        self.lc_level = 0.0
        self.rot_angle = 0.0
        self.last_lc_direction = 5.0
        self.LIMITE_VOLTAGE_MV = 4500.0 # SÉCURITÉ ABSOLUE

        # Variables Séquence & Chronométrage
        self.seq_timer = QTimer()
        self.seq_timer.timeout.connect(self.sequence_step)
        self.seq_folder = ""
        self.seq_filepath = ""
        self.seq_count = 0
        self.seq_total = 0
        self.ref_intensity = 0.0
        self.last_lc_check_time = 0.0
        
        self.seq_start_time = 0.0
        self.total_correction_time = 0.0
        self.correction_count = 0
        self.estimated_str = ""

        self.init_camera()
        self.setup_ui()
        self.update_estimated_duration()

    def init_camera(self):
        try:
            self.cam = PiCamKuroManager()
            self.cam.connect()
            self.cam.apply_parameters(self.cam_params)
        except Exception as e:
            print(f"Erreur Caméra PICam : {e}")
            self.cam = None

    def setup_ui(self):
        main_widget = QWidget()
        self.setCentralWidget(main_widget)
        layout = QVBoxLayout(main_widget)

        # ==========================================
        # 1. CONTROLES CAMERA (Haut)
        # ==========================================
        top_layout = QHBoxLayout()
        self.btn_live = QPushButton("▶ Live"); self.btn_live.clicked.connect(self.toggle_live)
        self.btn_snap = QPushButton("📷 Snap & Save"); self.btn_snap.clicked.connect(self.snap_and_save)
        
        self.spin_exp = QSpinBox(); self.spin_exp.setRange(1, 10000); self.spin_exp.setValue(100)
        self.spin_exp.setKeyboardTracking(False)
        self.spin_exp.valueChanged.connect(self.change_exposure)
        
        self.chk_autoscale = QCheckBox("Auto-Scale Niveaux"); self.chk_autoscale.setChecked(True)
        self.btn_reset_levels = QPushButton("🔄 Reset Niveaux"); self.btn_reset_levels.clicked.connect(self.reset_levels)

        top_layout.addWidget(self.btn_live); top_layout.addWidget(self.btn_snap)
        top_layout.addWidget(QLabel("  Exp (ms):")); top_layout.addWidget(self.spin_exp)
        top_layout.addSpacing(20)
        top_layout.addWidget(self.chk_autoscale); top_layout.addWidget(self.btn_reset_levels)
        top_layout.addStretch()
        layout.addLayout(top_layout)

        # ==========================================
        # 2. CONTROLES ROI (Milieu)
        # ==========================================
        roi_layout = QHBoxLayout()
        self.btn_draw_roi = QPushButton("📐 Dessiner ROI Externe"); self.btn_draw_roi.clicked.connect(self.draw_roi)
        self.btn_apply_roi = QPushButton("✂ Appliquer ROI"); self.btn_apply_roi.clicked.connect(self.apply_roi); self.btn_apply_roi.setEnabled(False)
        self.btn_reset_roi = QPushButton("❌ Enlever ROI"); self.btn_reset_roi.clicked.connect(self.reset_roi)

        roi_layout.addWidget(self.btn_draw_roi); roi_layout.addWidget(self.btn_apply_roi); roi_layout.addWidget(self.btn_reset_roi)
        roi_layout.addSpacing(20)

        self.spin_roi_x = QSpinBox(); self.spin_roi_y = QSpinBox()
        self.spin_roi_w = QSpinBox(); self.spin_roi_h = QSpinBox()
        for spin in [self.spin_roi_x, self.spin_roi_y, self.spin_roi_w, self.spin_roi_h]:
            spin.setRange(0, 4000); spin.setEnabled(False)

        roi_layout.addWidget(QLabel("X:")); roi_layout.addWidget(self.spin_roi_x)
        roi_layout.addWidget(QLabel("Y:")); roi_layout.addWidget(self.spin_roi_y)
        roi_layout.addWidget(QLabel("L:")); roi_layout.addWidget(self.spin_roi_w)
        roi_layout.addWidget(QLabel("H:")); roi_layout.addWidget(self.spin_roi_h)
        
        for spin in [self.spin_roi_x, self.spin_roi_y, self.spin_roi_w, self.spin_roi_h]:
            spin.valueChanged.connect(self.update_roi_from_spinboxes)

        roi_layout.addStretch()
        layout.addLayout(roi_layout)

        # ==========================================
        # 3. AFFICHAGE IMAGE & CARRE INTERNE
        # ==========================================
        self.imv = pg.ImageView()
        self.imv.ui.roiBtn.hide(); self.imv.ui.menuBtn.hide()
        self.imv.getImageItem().setAutoDownsample(True)
        layout.addWidget(self.imv)
        
        self.inner_rect = QGraphicsRectItem()
        pen = QPen(QColor(255, 0, 0)); pen.setWidth(2); pen.setStyle(Qt.DashLine) 
        self.inner_rect.setPen(pen)
        self.imv.getView().addItem(self.inner_rect)

        # ==========================================
        # 4. PANNEAU SEQUENCE & AUTO-LC
        # ==========================================
        seq_group = QGroupBox("Séquence Automatique & Auto-Correction LC")
        seq_group.setStyleSheet("QGroupBox { font-weight: bold; color: darkblue; }")
        seq_layout_main = QVBoxLayout(seq_group)
        
        # --- LIGNE 1 : Dossier, Paramètres d'acquisition et Durée ---
        seq_row1 = QHBoxLayout()
        self.btn_folder = QPushButton("📂 Dossier Stack"); self.btn_folder.clicked.connect(self.select_folder)
        self.lbl_folder = QLabel("Aucun dossier")
        seq_row1.addWidget(self.btn_folder); seq_row1.addWidget(self.lbl_folder)
        seq_row1.addSpacing(15)
        
        seq_row1.addWidget(QLabel("Nb Images :"))
        self.spin_seq_n = QSpinBox(); self.spin_seq_n.setRange(1, 100000); self.spin_seq_n.setValue(10)
        self.spin_seq_n.valueChanged.connect(self.update_estimated_duration)
        seq_row1.addWidget(self.spin_seq_n)
        
        seq_row1.addWidget(QLabel("Intervalle (s):"))
        self.spin_seq_interval = QDoubleSpinBox(); self.spin_seq_interval.setRange(0, 3600); self.spin_seq_interval.setValue(1.0)
        self.spin_seq_interval.valueChanged.connect(self.update_estimated_duration)
        seq_row1.addWidget(self.spin_seq_interval)
        
        seq_row1.addSpacing(15)
        self.lbl_duration = QLabel("⏱ Estimé : 10 s")
        self.lbl_duration.setStyleSheet("color: #0056b3; font-weight: bold; background-color: #e6f2ff; padding: 4px 8px; border-radius: 4px;")
        seq_row1.addWidget(self.lbl_duration)
        seq_row1.addSpacing(15)
        
        seq_row1.addWidget(QLabel("Marge (px):"))
        self.spin_margin = QSpinBox(); self.spin_margin.setRange(0, 2000); self.spin_margin.setValue(20) 
        self.spin_margin.valueChanged.connect(self.update_inner_rect_display) 
        seq_row1.addWidget(self.spin_margin)
        seq_row1.addStretch()
        seq_layout_main.addLayout(seq_row1)
        
        # --- LIGNE 2 : Outil de Mesure, Paramètres Auto-LC et Démarrage ---
        seq_row2 = QHBoxLayout()
        
        self.btn_measure = QPushButton("📏 Mesurer Intensité (Cadre/Bordure)")
        self.btn_measure.setStyleSheet("background-color: #fff3cd; font-weight: bold;")
        self.btn_measure.clicked.connect(self.measure_intensity)
        self.lbl_measure = QLabel("---")
        self.lbl_measure.setStyleSheet("font-size: 16px; color: #b22222;")
        seq_row2.addWidget(self.btn_measure); seq_row2.addWidget(self.lbl_measure)
        
        seq_row2.addSpacing(20)
        
        self.chk_auto_lc = QCheckBox("⚡ Auto-LC")
        seq_row2.addWidget(self.chk_auto_lc)
        
        seq_row2.addWidget(QLabel("Seuil (x Réf):"))
        self.spin_seuil = QDoubleSpinBox()
        self.spin_seuil.setDecimals(2)
        self.spin_seuil.setRange(1.01, 50.0)
        self.spin_seuil.setSingleStep(0.01)
        self.spin_seuil.setValue(3.00)
        seq_row2.addWidget(self.spin_seuil)

        seq_row2.addSpacing(15)

        seq_row2.addWidget(QLabel("Vérif. tous les :"))
        self.spin_check_val = QSpinBox()
        self.spin_check_val.setRange(1, 100000)
        self.spin_check_val.setValue(60)
        seq_row2.addWidget(self.spin_check_val)

        self.combo_check_mode = QComboBox()
        self.combo_check_mode.addItems(["Secondes", "Images"])
        seq_row2.addWidget(self.combo_check_mode)
        
        seq_row2.addStretch()
        
        self.btn_start_seq = QPushButton("▶ Démarrer Séquence")
        self.btn_start_seq.setStyleSheet("font-weight: bold; background-color: #d4edda; padding: 10px;")
        self.btn_start_seq.clicked.connect(self.start_sequence)
        
        self.btn_stop_seq = QPushButton("⏹ Stop")
        self.btn_stop_seq.setStyleSheet("font-weight: bold; background-color: #f8d7da; padding: 10px;")
        self.btn_stop_seq.setEnabled(False)
        self.btn_stop_seq.clicked.connect(self.stop_sequence)

        seq_row2.addWidget(self.btn_start_seq); seq_row2.addWidget(self.btn_stop_seq)
        seq_layout_main.addLayout(seq_row2)

        layout.addWidget(seq_group)

        # ==========================================
        # 5. PANNEAU SPECTO BIPD (MANUEL)
        # ==========================================
        bipd_group = QGroupBox("Contrôles Manuels Specto BIPD")
        bipd_layout = QVBoxLayout(bipd_group)

        bipd_top = QHBoxLayout()
        self.btn_bipd_connect = QPushButton("🔗 Connect"); self.btn_bipd_connect.clicked.connect(self.connect_bipd)
        self.chk_bipd_temp = QCheckBox("Stabilize temperature"); self.chk_bipd_temp.setEnabled(False)
        self.chk_bipd_temp.toggled.connect(self.toggle_temperature)
        bipd_top.addWidget(self.btn_bipd_connect); bipd_top.addSpacing(20); bipd_top.addWidget(self.chk_bipd_temp); bipd_top.addStretch()
        bipd_layout.addLayout(bipd_top)

        bipd_modules = QHBoxLayout()
        
        lc_group = QGroupBox("Liquid Crystal"); lc_layout = QHBoxLayout(lc_group)
        lc_btn_left = QVBoxLayout()
        btn_lc_ll = QPushButton("<<"); btn_lc_ll.clicked.connect(lambda: self.change_lc(False, False))
        btn_lc_l = QPushButton("<"); btn_lc_l.clicked.connect(lambda: self.change_lc(True, False))
        lc_btn_left.addWidget(btn_lc_l); lc_btn_left.addWidget(btn_lc_ll)
        
        self.lbl_lc_val = QLabel("0.0 mV"); self.lbl_lc_val.setAlignment(Qt.AlignCenter)
        self.lbl_lc_val.setStyleSheet("font-size: 24px; background-color: #333; color: white; padding: 10px; border-radius: 5px;")
        
        lc_btn_right = QVBoxLayout()
        btn_lc_r = QPushButton(">"); btn_lc_r.clicked.connect(lambda: self.change_lc(True, True))
        btn_lc_rr = QPushButton(">>"); btn_lc_rr.clicked.connect(lambda: self.change_lc(False, True))
        lc_btn_right.addWidget(btn_lc_r); lc_btn_right.addWidget(btn_lc_rr)

        lc_steps = QVBoxLayout()
        lc_steps.addWidget(QLabel("Step size [mV]"))
        self.spin_lc_small = QDoubleSpinBox(); self.spin_lc_small.setValue(1.0)
        self.spin_lc_large = QDoubleSpinBox(); self.spin_lc_large.setRange(0, 1000); self.spin_lc_large.setValue(50.0)
        lc_steps.addWidget(self.spin_lc_small); lc_steps.addWidget(self.spin_lc_large)
        lc_layout.addLayout(lc_btn_left); lc_layout.addWidget(self.lbl_lc_val); lc_layout.addLayout(lc_btn_right); lc_layout.addLayout(lc_steps)
        
        rot_group = QGroupBox("Rotator"); rot_layout = QHBoxLayout(rot_group)
        rot_btn_left = QVBoxLayout()
        btn_rot_ll = QPushButton("<<"); btn_rot_ll.clicked.connect(lambda: self.change_rot(False, False))
        btn_rot_l = QPushButton("<"); btn_rot_l.clicked.connect(lambda: self.change_rot(True, False))
        rot_btn_left.addWidget(btn_rot_l); rot_btn_left.addWidget(btn_rot_ll)

        rot_center = QVBoxLayout()
        btn_rot_home = QPushButton("🏠 Home"); btn_rot_home.clicked.connect(self.home_rot)
        self.lbl_rot_val = QLabel("0.00°"); self.lbl_rot_val.setAlignment(Qt.AlignCenter)
        self.lbl_rot_val.setStyleSheet("font-size: 24px; background-color: #333; color: white; padding: 10px; border-radius: 5px;")
        rot_center.addWidget(btn_rot_home); rot_center.addWidget(self.lbl_rot_val)

        rot_btn_right = QVBoxLayout()
        btn_rot_r = QPushButton(">"); btn_rot_r.clicked.connect(lambda: self.change_rot(True, True))
        btn_rot_rr = QPushButton(">>"); btn_rot_rr.clicked.connect(lambda: self.change_rot(False, True))
        rot_btn_right.addWidget(btn_rot_r); rot_btn_right.addWidget(btn_rot_rr)

        rot_steps = QVBoxLayout()
        rot_steps.addWidget(QLabel("Step size [°]"))
        self.spin_rot_small = QDoubleSpinBox(); self.spin_rot_small.setValue(1.0)
        self.spin_rot_large = QDoubleSpinBox(); self.spin_rot_large.setRange(0, 360); self.spin_rot_large.setValue(30.0)
        rot_steps.addWidget(self.spin_rot_small); rot_steps.addWidget(self.spin_rot_large)
        rot_layout.addLayout(rot_btn_left); rot_layout.addLayout(rot_center); rot_layout.addLayout(rot_btn_right); rot_layout.addLayout(rot_steps)

        bipd_modules.addWidget(lc_group); bipd_modules.addWidget(rot_group)
        bipd_layout.addLayout(bipd_modules)
        
        layout.addWidget(bipd_group)

        if self.cam:
            self.take_single_preview()

    # ==========================================
    # GESTION DU TEMPS ET FORMATAGE
    # ==========================================
    def format_seconds(self, total_sec):
        total_sec = int(round(total_sec))
        heures = total_sec // 3600
        minutes = (total_sec % 3600) // 60
        secondes = total_sec % 60
        
        if heures > 0:
            return f"{heures}h {minutes:02d}m {secondes:02d}s"
        elif minutes > 0:
            return f"{minutes} min {secondes:02d} s"
        else:
            return f"{secondes} s"

    def update_estimated_duration(self):
        nb_images = self.spin_seq_n.value()
        interval_s = self.spin_seq_interval.value()
        exp_s = self.spin_exp.value() / 1000.0
        
        pas_effectif_s = max(interval_s, exp_s)
        total_sec = nb_images * pas_effectif_s
        
        self.estimated_str = self.format_seconds(total_sec)
        self.lbl_duration.setText(f"⏱ Estimé : {self.estimated_str}")

    # ==========================================
    # LOGIQUE BIPD MANUEL (AVEC SÉCURITÉ)
    # ==========================================
    def change_lc(self, is_small, is_positive):
        step = self.spin_lc_small.value() if is_small else self.spin_lc_large.value()
        if not is_positive: step = -step
        
        test_val = self.lc_level + step
        if test_val < 0: test_val = 0.0 
        if test_val > self.LIMITE_VOLTAGE_MV: test_val = self.LIMITE_VOLTAGE_MV

        self.lc_level = test_val
        self.lbl_lc_val.setText(f"{self.lc_level:.1f} mV")
        if self.bipd:
            self.bipd.lc_set_enabled(True)
            self.bipd.lc_set_level(self.lc_level)

    def change_rot(self, is_small, is_positive):
        step = self.spin_rot_small.value() if is_small else self.spin_rot_large.value()
        if not is_positive: step = -step
        self.rot_angle += step
        self.lbl_rot_val.setText(f"{self.rot_angle:.2f}°")
        if self.bipd:
            self.bipd.rotator_move_absolute(self.rot_angle)

    def home_rot(self):
        self.rot_angle = 0.0
        self.lbl_rot_val.setText("0.00°")
        if self.bipd:
            self.bipd.rotator_move_home()

    # ==========================================
    # LOGIQUE SÉQUENCE, PILE TIFF & AUTO-LC
    # ==========================================
    def update_inner_rect_display(self):
        if self.current_img_w == 0 or self.current_img_h == 0: return
        w, h = self.current_img_w, self.current_img_h
        margin = self.spin_margin.value()
        margin_x = min(margin, w // 2 - 1)
        margin_y = min(margin, h // 2 - 1)
        self.inner_rect.setRect(margin_x, margin_y, w - (2 * margin_x), h - (2 * margin_y))

    def get_border_intensity(self, img):
        h, w = img.shape
        margin = self.spin_margin.value()
        margin_x = min(margin, w // 2 - 1)
        margin_y = min(margin, h // 2 - 1)
        
        if margin_x <= 0 or margin_y <= 0:
            return float(np.mean(img, dtype=np.float64))
            
        inner_img = img[margin_y:h-margin_y, margin_x:w-margin_x]
        
        somme_totale = np.sum(img, dtype=np.float64)
        somme_interne = np.sum(inner_img, dtype=np.float64)
        somme_cadre = somme_totale - somme_interne
        
        pixels_totaux = img.size
        pixels_internes = inner_img.size
        pixels_cadre = pixels_totaux - pixels_internes
        
        if pixels_cadre == 0:
            return float(np.mean(img, dtype=np.float64))
            
        return float(somme_cadre / pixels_cadre)

    def measure_intensity(self):
        if not self.cam: return
        was_live = self.live_running
        if was_live: self.toggle_live()
        try:
            img = self.cam.snap(self.cam_params)
            val = self.get_border_intensity(img)
            self.lbl_measure.setText(f"{val:.2f}")
            self.display_image(img, auto_levels=self.chk_autoscale.isChecked())
        except Exception as e:
            print(f"Erreur mesure: {e}")
            self.lbl_measure.setText("Erreur")
        if was_live: self.toggle_live()

    def select_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Sélectionner le dossier de sauvegarde")
        if folder:
            self.seq_folder = folder
            self.lbl_folder.setText(os.path.basename(folder))

    def start_sequence(self):
        if not self.seq_folder:
            QMessageBox.warning(self, "Attention", "Veuillez choisir un dossier de sauvegarde.")
            return

        if self.live_running: self.toggle_live()

        self.seq_total = self.spin_seq_n.value()
        self.seq_count = 0
        self.total_correction_time = 0.0
        self.correction_count = 0
        self.update_estimated_duration()

        timestamp = time.strftime("%Y%m%d_%H%M%S")
        self.seq_filepath = os.path.join(self.seq_folder, f"sequence_stack_{timestamp}.tif")

        img = self.cam.snap(self.cam_params)
        self.ref_intensity = self.get_border_intensity(img)
        
        self.seq_start_time = time.time()
        self.last_lc_check_time = self.seq_start_time

        print(f"\n--- DÉBUT SÉQUENCE ---")
        print(f"Fichier Stack TIFF : {self.seq_filepath}")
        print(f"Intensité de référence (BORDURE) : {self.ref_intensity:.2f}")
        print(f"Durée théorique estimée : {self.estimated_str}")

        self.btn_start_seq.setEnabled(False)
        self.btn_bipd_connect.setEnabled(False)
        self.btn_stop_seq.setEnabled(True)

        interval_ms = int(self.spin_seq_interval.value() * 1000)
        self.seq_timer.start(max(10, interval_ms))

    def stop_sequence(self):
        if self.seq_timer.isActive():
            self.seq_timer.stop()
        self._finish_sequence(message="--- SÉQUENCE INTERROMPUE PAR L'UTILISATEUR ---")

    def _finish_sequence(self, message="--- FIN SÉQUENCE ---"):
        elapsed_real = time.time() - self.seq_start_time
        real_str = self.format_seconds(elapsed_real)
        corr_str = self.format_seconds(self.total_correction_time)

        self.lbl_duration.setText(
            f"⏱ Estimé : {self.estimated_str} | ✅ Réel : {real_str} (dont {corr_str} en corr.)"
        )

        self.btn_start_seq.setText("▶ Démarrer Séquence")
        self.btn_start_seq.setEnabled(True)
        self.btn_bipd_connect.setEnabled(True)
        self.btn_stop_seq.setEnabled(False)
        
        print(message)
        print(f"⏱ Durée théorique estimée : {self.estimated_str}")
        print(f"✅ Durée réelle totale     : {real_str} ({elapsed_real:.1f} s)")
        print(f"🔧 Temps perdu en Auto-LC  : {corr_str} ({self.total_correction_time:.1f} s sur {self.correction_count} correction(s))")

    def sequence_step(self):
        img = self.cam.snap(self.cam_params)
        
        if self.chk_auto_lc.isChecked() and self.bipd:
            mode = self.combo_check_mode.currentText()
            val_verif = self.spin_check_val.value()
            do_check = False

            if mode == "Secondes":
                if (time.time() - self.last_lc_check_time) >= float(val_verif):
                    do_check = True
            else:
                if (self.seq_count + 1) % val_verif == 0:
                    do_check = True

            if do_check:
                current_intensity = self.get_border_intensity(img)
                seuil_mult = self.spin_seuil.value()
                
                if current_intensity > (seuil_mult * self.ref_intensity):
                    print(f"⚠️ Alerte (BORDURE) ! Intensité ({current_intensity:.2f}) > Seuil ({seuil_mult:.2f}x). Recherche du minimum...")
                    self.seq_timer.stop()
                    
                    t_corr_start = time.time()
                    self.auto_correct_lc(current_intensity)
                    img = self.cam.snap(self.cam_params)
                    self.total_correction_time += (time.time() - t_corr_start)
                    self.correction_count += 1
                    
                    self.seq_timer.start()
                
                self.last_lc_check_time = time.time()

        tifffile.imwrite(self.seq_filepath, img, append=True)
        
        self.display_image(img, auto_levels=self.chk_autoscale.isChecked())
        QApplication.processEvents() 
        
        self.seq_count += 1
        self.btn_start_seq.setText(f"En cours... ({self.seq_count}/{self.seq_total})")

        if self.seq_count >= self.seq_total:
            self.seq_timer.stop()
            self._finish_sequence()

    def auto_correct_lc(self, initial_intensity):
        """Recherche du minimum local par pas de 5 mV"""
        delai_stabilisation_lc = 0.5 # Modifié à 500 ms (Le manuel requiert 200 ms minimum pour la sécurité du LC)
        best_intensity = initial_intensity
        best_voltage = self.lc_level
        
        test_v = best_voltage + self.last_lc_direction
        self.set_lc_voltage(test_v)
        QApplication.processEvents()
        time.sleep(delai_stabilisation_lc) 
        
        img_test = self.cam.snap(self.cam_params)
        self.display_image(img_test, auto_levels=self.chk_autoscale.isChecked())
        QApplication.processEvents()
        i_test = self.get_border_intensity(img_test)
        
        if i_test < best_intensity:
            best_intensity = i_test
            best_voltage = self.lc_level
        else:
            self.last_lc_direction = -self.last_lc_direction
            test_v = best_voltage + self.last_lc_direction
            self.set_lc_voltage(test_v)
            QApplication.processEvents()
            time.sleep(delai_stabilisation_lc)
            
            img_test = self.cam.snap(self.cam_params)
            self.display_image(img_test, auto_levels=self.chk_autoscale.isChecked())
            QApplication.processEvents()
            i_test = self.get_border_intensity(img_test)
            
            if i_test < best_intensity:
                best_intensity = i_test
                best_voltage = self.lc_level
            else:
                self.set_lc_voltage(best_voltage)
                QApplication.processEvents()
                time.sleep(delai_stabilisation_lc)
                print(f"✅ Déjà au minimum ({best_intensity:.2f}). Voltage maintenu à : {self.lc_level:.1f} mV")
                return

        essais = 0
        while essais < 30:
            if (self.last_lc_direction > 0 and self.lc_level >= self.LIMITE_VOLTAGE_MV) or \
               (self.last_lc_direction < 0 and self.lc_level <= 0.0):
                break

            test_v = self.lc_level + self.last_lc_direction
            self.set_lc_voltage(test_v)
            QApplication.processEvents()
            time.sleep(delai_stabilisation_lc)
            
            img_test = self.cam.snap(self.cam_params)
            nouvelle_intensite = self.get_border_intensity(img_test)
            
            self.display_image(img_test, auto_levels=self.chk_autoscale.isChecked())
            QApplication.processEvents() 
            
            if nouvelle_intensite < best_intensity:
                best_intensity = nouvelle_intensite
                best_voltage = self.lc_level
                essais += 1
            else:
                self.set_lc_voltage(best_voltage)
                QApplication.processEvents()
                time.sleep(delai_stabilisation_lc)
                break

        print(f"✅ Minimum atteint (Intensité : {best_intensity:.2f}). Nouveau Voltage : {self.lc_level:.1f} mV")

    def set_lc_voltage(self, voltage):
        if voltage < 0: voltage = 0.0
        if voltage > self.LIMITE_VOLTAGE_MV:
            voltage = self.LIMITE_VOLTAGE_MV
            print(f"⚠️ AVERTISSEMENT : Limite de sécurité atteinte ({self.LIMITE_VOLTAGE_MV} mV) !")
            
        self.lc_level = voltage
        self.lbl_lc_val.setText(f"{self.lc_level:.1f} mV")
        if self.bipd:
            self.bipd.lc_set_level(self.lc_level)

    # ==========================================
    # LOGIQUE CAMERA, LIVE THREAD ET ROI
    # ==========================================
    def change_exposure(self):
        was_live = self.live_running
        if was_live: self.toggle_live()
        self.cam_params["exposure_ms"] = float(self.spin_exp.value())
        if self.cam:
            try:
                self.cam.apply_parameters(self.cam_params)
            except Exception: pass
        self.update_estimated_duration()
        if was_live: self.toggle_live()

    def toggle_live(self):
        if not self.cam: return
        if self.live_running:
            if self.live_worker:
                self.live_worker.stop()
                self.live_worker = None
            self.btn_live.setText("▶ Live")
            self.live_running = False
        else:
            self.live_worker = LiveWorker(self.cam, self.cam_params)
            self.live_worker.frame_ready.connect(self.on_live_frame)
            self.live_worker.start()
            self.btn_live.setText("⏸ Stop Live")
            self.live_running = True

    def on_live_frame(self, img):
        self.display_image(img, auto_levels=self.chk_autoscale.isChecked())
        if self.live_worker:
            self.live_worker.ready_for_next = True

    def take_single_preview(self):
        if not self.cam: return
        try:
            img = self.cam.snap(self.cam_params)
            self.display_image(img, auto_levels=self.chk_autoscale.isChecked())
        except Exception: pass

    def snap_and_save(self):
        if not self.cam: return
        was_live = self.live_running
        if was_live: self.toggle_live()
        try:
            img = self.cam.snap(self.cam_params)
            self.display_image(img, auto_levels=self.chk_autoscale.isChecked())
            filepath, _ = QFileDialog.getSaveFileName(self, "Save", "image.tif", "TIFF (*.tif)")
            if filepath: tifffile.imwrite(filepath, img)
        except Exception: pass
        if was_live: self.toggle_live()

    def display_image(self, img, auto_levels):
        h, w = img.shape
        size_changed = (h != self.current_img_h or w != self.current_img_w)
        self.current_img_h, self.current_img_w = h, w
        
        self.imv.setImage(img.T, autoRange=False, autoLevels=auto_levels, autoHistogramRange=False)
        
        if size_changed:
            self.update_inner_rect_display()

    def reset_levels(self):
        self.imv.autoLevels()
        self.imv.autoHistogramRange()

    def draw_roi(self):
        if not self.cam or self.imv.image is None: return
        if self.roi_rect: self.imv.removeItem(self.roi_rect)
        h, w = self.imv.image.shape[1], self.imv.image.shape[0]
        self.roi_rect = pg.RectROI([w//4, h//4], [w//2, h//2], pen='y')
        self.roi_rect.addScaleHandle([1, 1], [0, 0])
        self.roi_rect.sigRegionChanged.connect(self.update_spinboxes_from_roi)
        self.imv.addItem(self.roi_rect)
        self.btn_apply_roi.setEnabled(True)
        for spin in [self.spin_roi_x, self.spin_roi_y, self.spin_roi_w, self.spin_roi_h]: spin.setEnabled(True)
        self.update_spinboxes_from_roi()

    def update_spinboxes_from_roi(self):
        if not self.roi_rect: return
        self._updating_roi_ui = True
        pos, size = self.roi_rect.pos(), self.roi_rect.size()
        self.spin_roi_x.setValue(int(max(0, pos.x()))); self.spin_roi_y.setValue(int(max(0, pos.y())))
        self.spin_roi_w.setValue(int(max(1, size.x()))); self.spin_roi_h.setValue(int(max(1, size.y())))
        self._updating_roi_ui = False

    def update_roi_from_spinboxes(self):
        if self._updating_roi_ui or not self.roi_rect: return
        self.roi_rect.setPos([self.spin_roi_x.value(), self.spin_roi_y.value()])
        self.roi_rect.setSize([self.spin_roi_w.value(), self.spin_roi_h.value()])

    def apply_roi(self):
        if not self.roi_rect or not self.cam: return
        was_live = self.live_running
        if was_live: self.toggle_live()
        
        offset_x = self.cam_params.get("roi_x", 0) if self.cam_params.get("roi_enabled") else 0
        offset_y = self.cam_params.get("roi_y", 0) if self.cam_params.get("roi_enabled") else 0
        self.cam_params["roi_enabled"] = True
        self.cam_params["roi_x"] = int(self.spin_roi_x.value()) + offset_x
        self.cam_params["roi_y"] = int(self.spin_roi_y.value()) + offset_y
        self.cam_params["roi_width"] = int(self.spin_roi_w.value())
        self.cam_params["roi_height"] = int(self.spin_roi_h.value())

        try:
            self.cam.apply_parameters(self.cam_params)
            self.imv.removeItem(self.roi_rect)
            self.roi_rect = None
            self.btn_apply_roi.setEnabled(False)
            for spin in [self.spin_roi_x, self.spin_roi_y, self.spin_roi_w, self.spin_roi_h]: spin.setEnabled(False)
            self.take_single_preview()
            self.imv.autoRange()
        except Exception: self.reset_roi()
        if was_live: self.toggle_live()

    def reset_roi(self):
        if not self.cam: return
        was_live = self.live_running
        if was_live: self.toggle_live()
        try:
            self.cam_params["roi_enabled"] = False
            self.cam.apply_parameters(self.cam_params)
            if self.roi_rect: self.imv.removeItem(self.roi_rect); self.roi_rect = None
            self.btn_apply_roi.setEnabled(False)
            self._updating_roi_ui = True
            for spin in [self.spin_roi_x, self.spin_roi_y, self.spin_roi_w, self.spin_roi_h]: spin.setValue(0); spin.setEnabled(False)
            self._updating_roi_ui = False
            self.take_single_preview()
            self.imv.autoRange()
        except Exception: pass
        if was_live: self.toggle_live()

    def connect_bipd(self):
        if not BIPD_AVAILABLE: return
        try:
            if self.bipd is None:
                self.bipd = SpectoBIPD(init_rotator_move_home=False)
                self.btn_bipd_connect.setText("🔗 Disconnect")
                self.chk_bipd_temp.setEnabled(True)
                self.lc_level = self.bipd.lc_get_level()
                self.lbl_lc_val.setText(f"{self.lc_level:.1f} mV")
                self.rot_angle = self.bipd.rotator_get_position()
                self.lbl_rot_val.setText(f"{self.rot_angle:.2f}°")
            else:
                self.bipd.close(); self.bipd = None
                self.btn_bipd_connect.setText("🔗 Connect")
                self.chk_bipd_temp.setEnabled(False)
        except Exception: pass

    def toggle_temperature(self, checked):
        if self.bipd: self.bipd.heater_set_enabled(checked)

    def closeEvent(self, event):
        if self.live_running and self.live_worker:
            self.live_worker.stop()
        if self.seq_timer.isActive(): self.seq_timer.stop()
        if self.cam: self.cam.disconnect()
        if self.bipd: self.bipd.close()
        event.accept()

if __name__ == '__main__':
    app = QApplication.instance()
    if app is None: app = QApplication(sys.argv)
    window = CameraInterface()
    window.show()
    app.exec_()