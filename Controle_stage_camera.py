import serial
import struct
import time
import os
import threading

# ==========================================
# 1. CONFIGURATION
# ==========================================
PORT = 'COM8'
DEVICE_ID = 0
CALIBRATION_FACTOR = 0.625 

DOSSIER_SORTIE = r"C:\Users\NanoBio-user\Desktop\TEST_BEAD_24_03_1-10"
NOM_EXPERIENCE = "Scan_Safe_Spyder"

# Géométrie
NB_LIGNES_Y   = 30
NB_POINTS_X   = 30
PAS_X_MM      = 0.0008
PAS_Y_MM      = 0.0008

# Paramètres Moteur (Sécurité)
VITESSE_CIBLE = 230000  # 2.3 mm/s
ACCELERATION  = 100000   # Doux

# Temps
TEMPS_POSE_CAMERA = 0 #simule la présence de la caméra quand elle n'est pas détecté pour tester le temps de mesure.
TEMPS_STABILISATION = 0.005

UTILISER_CAMERA = True
MM_PATH = r"C:\Program Files\Micro-Manager-2.0gamma"
MM_CFG  = r"C:\Program Files\Micro-Manager-2.0gamma\kuro_gamma.cfg"
EXPOSITION_MS = 500.0

# ==========================================
# 2. FONCTIONS MOTEUR
# ==========================================
def cobs_encode(data):
    read_index = 0; write_index = 1; code_index = 0; code = 1
    encoded = bytearray(len(data) + len(data)//254 + 2)
    while read_index < len(data):
        byte = data[read_index]; read_index += 1
        if byte == 0:
            encoded[code_index] = code; code = 1; code_index = write_index; write_index += 1
        else:
            encoded[write_index] = byte; write_index += 1; code += 1
            if code == 0xFF:
                encoded[code_index] = code; code = 1; code_index = write_index; write_index += 1
    encoded[code_index] = code
    return encoded[:write_index] + b'\x00'

def configurer_vitesse(ser):
    ser.write(cobs_encode(struct.pack('<BBBBBI', 0xAA, 0x02, 0x05, DEVICE_ID, 0, int(VITESSE_CIBLE))))
    ser.read_until(b'\x00')
    ser.write(cobs_encode(struct.pack('<BBBBBI', 0xAA, 0x02, 0x05, DEVICE_ID, 1, int(VITESSE_CIBLE))))
    ser.read_until(b'\x00')
    ser.write(cobs_encode(struct.pack('<BBBBBI', 0xAA, 0x02, 0x06, DEVICE_ID, 0, int(ACCELERATION))))
    ser.read_until(b'\x00')
    ser.write(cobs_encode(struct.pack('<BBBBBI', 0xAA, 0x02, 0x06, DEVICE_ID, 1, int(ACCELERATION))))
    ser.read_until(b'\x00')
    print(f"Moteur configuré : Vitesse={VITESSE_CIBLE/100:.0f} um/s, Accel={ACCELERATION/100:.0f} um/s²")

def deplacer_axe(ser, axe_id, distance_mm):
    if distance_mm == 0: return
    raw_units = int(distance_mm * 1000 * 100)
    corrected_units = int(raw_units * CALIBRATION_FACTOR)
    packet = struct.pack('<BBBBBi', 0xAA, 0x02, 0x02, DEVICE_ID, axe_id, corrected_units)
    ser.write(cobs_encode(packet))
    ser.read_until(b'\x00') 
    
    # Temps d'attente optimisé
    temps_estime = 0.05 + (abs(distance_mm) * 0.5)
    time.sleep(temps_estime)

# ==========================================
# 3. CAMERA & SAUVEGARDE
# ==========================================
def init_camera():
    if not UTILISER_CAMERA: return None
    try:
        import pymmcore
        mmc = pymmcore.CMMCore()
        mmc.setDeviceAdapterSearchPaths([MM_PATH])
        mmc.loadSystemConfiguration(MM_CFG)
        mmc.setExposure(EXPOSITION_MS)
        return mmc
    except Exception:
        return None

def _sauvegarder_disque(img_data, h, w, filepath):
    try:
        import tifffile
        img_2d = img_data.reshape((h, w))
        tifffile.imwrite(filepath, img_2d)
    except Exception as e:
        print(f"Erreur save: {e}")

def prendre_photo_rapide(mmc, filepath, liste_threads_actifs):
    """
    On passe 'liste_threads_actifs' en argument pour garder une trace
    de nos propres tâches.
    """
    if UTILISER_CAMERA and mmc:
        mmc.snapImage()
        img = mmc.getImage() 
        h = mmc.getImageHeight()
        w = mmc.getImageWidth()
        
        # On lance le thread et on l'ajoute à NOTRE liste
        t = threading.Thread(target=_sauvegarder_disque, args=(img, h, w, filepath))
        t.start()
        liste_threads_actifs.append(t)
    else:
        time.sleep(TEMPS_POSE_CAMERA)

# ==========================================
# 4. MAIN
# ==========================================
def run_scan():
    if not os.path.exists(DOSSIER_SORTIE): os.makedirs(DOSSIER_SORTIE)
    mmc = init_camera()
    
    # --- LISTE POUR SUIVRE NOS THREADS ---
    nos_threads = [] 

    try:
        ser = serial.Serial(PORT, baudrate=9600, timeout=1)
        ser.reset_input_buffer()
        time.sleep(1)
        
        configurer_vitesse(ser)
        
        debut_total = time.time()
        print("\n--- DÉBUT SCAN RAPIDE (FIX SPYDER) ---")
        
        for y in range(NB_LIGNES_Y):
            if y % 2 == 0:
                direction = 1; liste_points_x = range(NB_POINTS_X)
            else:
                direction = -1; liste_points_x = range(NB_POINTS_X - 1, -1, -1)

            print(f"Ligne {y}...")

            for i, x in enumerate(liste_points_x):
                time.sleep(TEMPS_STABILISATION)
                
                filename = f"{NOM_EXPERIENCE}_L{y:03d}_X{x:03d}.tif"
                filepath = os.path.join(DOSSIER_SORTIE, filename)
                
                # On passe la liste 'nos_threads' ici
                prendre_photo_rapide(mmc, filepath, nos_threads)

                if i < NB_POINTS_X - 1:
                    deplacer_axe(ser, 0, PAS_X_MM * direction)

            if y < NB_LIGNES_Y - 1:
                deplacer_axe(ser, 1, -PAS_Y_MM)

        print("Mouvements terminés. Attente fin des sauvegardes...")


        for t in nos_threads:
            t.join()

        duree = time.time() - debut_total
        
        heures = int(duree // 3600)
        minutes = int((duree % 3600) // 60)
        secondes = int(duree % 60)
        
        print("\n" + "="*30)
        print(f" TERMINÉ en {heures}h {minutes}m {secondes}s")
        print("="*30)

    except Exception as e:
        print(f"Erreur: {e}")
    finally:
        if 'ser' in locals() and ser.is_open: ser.close()

if __name__ == "__main__":
    run_scan()