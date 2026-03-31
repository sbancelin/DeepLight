import time
import numpy as np
import nidaqmx

from nidaqmx.constants import (
    AcquisitionType,
    Edge,
    Level,
    TaskMode
)

# -----------------------------
# Paramètres du test
# -----------------------------
DEVICE = "Dev1"
COUNT_COUNTER = f"{DEVICE}/ctr2"      # ctr2 -> source par défaut sur PFI0
CLOCK_COUNTER = f"{DEVICE}/ctr0"      # ctr0 génère le sample clock interne
COUNT_SOURCE = f"/{DEVICE}/PFI0"      # pulses du H16721
SAMPLE_CLOCK_SOURCE = f"/{DEVICE}/Ctr0InternalOutput"

SAMPLE_RATE = 100_000                 # 100 kHz = 10 µs
N_SAMPLES = 5000                      # 50 ms de données

# -----------------------------
# Tâche 1 : génération du sample clock
# -----------------------------
clock_task = nidaqmx.Task()
clock_task.co_channels.add_co_pulse_chan_freq(
    counter=CLOCK_COUNTER,
    freq=SAMPLE_RATE,
    duty_cycle=0.5
)
clock_task.timing.cfg_implicit_timing(
    sample_mode=AcquisitionType.CONTINUOUS
)

# -----------------------------
# Tâche 2 : comptage bufferisé
# -----------------------------
count_task = nidaqmx.Task()
chan = count_task.ci_channels.add_ci_count_edges_chan(
    counter=COUNT_COUNTER,
    edge=Edge.RISING
)

# Force explicitement la source de comptage sur PFI0
chan.ci_count_edges_term = COUNT_SOURCE

# Comptage bufferisé échantillonné par le clock interne de ctr0
count_task.timing.cfg_samp_clk_timing(
    rate=SAMPLE_RATE,
    source=SAMPLE_CLOCK_SOURCE,
    active_edge=Edge.RISING,
    sample_mode=AcquisitionType.FINITE,
    samps_per_chan=N_SAMPLES
)

try:
    # Important : armer d'abord la tâche de comptage
    count_task.start()
    clock_task.start()

    # Lire les valeurs cumulées du compteur
    raw_counts = np.array(
        count_task.read(number_of_samples_per_channel=N_SAMPLES, timeout=5.0),
        dtype=np.uint32
    )

finally:
    # Arrêt / fermeture propre
    try:
        clock_task.stop()
    except Exception:
        pass
    try:
        count_task.stop()
    except Exception:
        pass

    clock_task.close()
    count_task.close()

# -----------------------------
# Conversion en counts / 10 µs
# -----------------------------
counts_per_bin = np.diff(raw_counts, prepend=raw_counts[0])

# Le premier point est souvent peu informatif
counts_per_bin = counts_per_bin[1:]

print(f"Nb bins lus : {len(counts_per_bin)}")
print(f"Dwell time : {1e6 / SAMPLE_RATE:.1f} µs")
print(f"Counts/bin min  : {counts_per_bin.min()}")
print(f"Counts/bin max  : {counts_per_bin.max()}")
print(f"Counts/bin mean : {counts_per_bin.mean():.3f}")

estimated_cps = counts_per_bin.mean() * SAMPLE_RATE
print(f"Flux estimé     : {estimated_cps:.1f} counts/s")

print("Premiers bins :", counts_per_bin[:30].tolist())