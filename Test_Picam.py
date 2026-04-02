from pylablib import par
import os

PICAM_PATH = r"C:\Program Files\Princeton Instruments\PICam\Runtime"
par['devices/dlls/picam'] = PICAM_PATH
os.chdir(PICAM_PATH)

from pylablib.devices import PrincetonInstruments

cams = PrincetonInstruments.list_cameras()
print("Detected cameras:", cams)