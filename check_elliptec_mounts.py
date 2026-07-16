#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
check_elliptec_mounts.py

Petit utilitaire de vérification des montures rotatives Thorlabs Elliptec
branchées sur un bus (hub ELLB partagé par plusieurs montures).

Contexte DeepLight :
- 1re monture : ELL14 pilotant la lame demi-onde du laser Cobolt (adresse "0",
  COM15 par défaut, cf. Hardware_Manager.COBOLT_ELL14_*).
- 2e monture : nouvellement ajoutée via un hub ELLB -> même port série, mais une
  ADRESSE de bus différente (0..F). Destinée à être pilotée par le positioner P.

Le script :
  1. liste les ports série disponibles,
  2. sonde les adresses 0..F du bus Elliptec sur le port choisi (commande "in"),
  3. identifie chaque monture (modèle, n° de série, course, counts/tour),
  4. lit sa position courante,
  5. (option --move) fait un petit aller-retour pour prouver le pilotage.

Ne dépend QUE de pyserial : aucune importation du Hardware_Manager (qui tire
Kinesis / PI / etc.), pour un diagnostic léger et sans effet de bord.

Exemples :
    python check_elliptec_mounts.py
    python check_elliptec_mounts.py --port COM15
    python check_elliptec_mounts.py --port COM15 --move
    python check_elliptec_mounts.py --scan-ports        # sonde tous les ports
"""

from __future__ import annotations

import argparse
import sys
import time

try:
    import serial
    from serial.tools import list_ports
except ImportError:
    print("ERREUR : pyserial est requis (pip install pyserial).")
    sys.exit(1)


# Adresse de la lame demi-onde du Cobolt (à ne pas bouger sans précaution :
# cela modifie la puissance laser). On l'affiche pour information.
COBOLT_HWP_ADDRESS = "0"

HEX_ADDRESSES = "0123456789ABCDEF"


class ElliptecBusDevice:
    """Accès minimal à un périphérique Elliptec à une adresse donnée d'un bus."""

    def __init__(self, ser: serial.Serial, address: str):
        self.ser = ser
        self.address = str(address).upper()
        # Rempli par identify()
        self.model = None
        self.serial_no = None
        self.year = None
        self.firmware = None
        self.travel = None          # 360 (rotatif, deg) ou course en mm (linéaire)
        self.pulses_per_unit = None  # counts/tour (rotatif) ou counts/mm (linéaire)
        self.is_rotary = None

    # ---- communication bas niveau -------------------------------------
    def _command(self, payload: str, timeout_s: float = 1.5) -> str:
        msg = f"{self.address}{payload}\r".encode("ascii")
        try:
            self.ser.reset_input_buffer()
        except Exception:
            pass
        self.ser.write(msg)
        self.ser.flush()

        chunks = []
        t0 = time.time()
        while time.time() - t0 < float(timeout_s):
            n = self.ser.in_waiting
            if n:
                chunks.append(self.ser.read(n))
                time.sleep(0.02)
                continue
            time.sleep(0.02)
            if chunks and self.ser.in_waiting <= 0:
                break
        return b"".join(chunks).decode("ascii", errors="replace").strip()

    # ---- identification -----------------------------------------------
    def identify(self) -> bool:
        """Envoie 'in' et parse la réponse. Retourne True si une monture répond."""
        resp = self._command("in", timeout_s=1.0)
        idx = resp.find("IN")
        if idx < 0 or len(resp) < idx + 2 + 30:
            return False

        body = resp[idx + 2:]
        try:
            type_code = int(body[0:2], 16)
            self.model = f"ELL{type_code}"
            self.serial_no = body[2:10]
            self.year = body[10:14]
            self.firmware = body[14:16]
            # body[16:18] = hardware release
            self.travel = int(body[18:22], 16)
            self.pulses_per_unit = int(body[22:30], 16)
        except (ValueError, IndexError):
            return False

        # Une course de 360 (deg) indique une monture rotative.
        self.is_rotary = (self.travel == 360)
        return True

    # ---- position ------------------------------------------------------
    @staticmethod
    def _hex32_to_counts(hex_value: str) -> int:
        value = int(str(hex_value), 16)
        if value & 0x80000000:
            value -= 0x100000000
        return int(value)

    def get_position_counts(self):
        resp = self._command("gp", timeout_s=1.0)
        idx = resp.find("PO")
        if idx < 0 or len(resp) < idx + 10:
            return None
        try:
            return self._hex32_to_counts(resp[idx + 2: idx + 10])
        except ValueError:
            return None

    def counts_to_units(self, counts: int) -> float:
        if not self.pulses_per_unit:
            return float(counts)
        if self.is_rotary:
            return counts * 360.0 / self.pulses_per_unit   # degrés
        return counts / self.pulses_per_unit               # mm

    def units_label(self) -> str:
        return "°" if self.is_rotary else "mm"

    def get_position_units(self):
        counts = self.get_position_counts()
        if counts is None:
            return None
        return self.counts_to_units(counts)

    # ---- mouvement (test de pilotage) ---------------------------------
    def move_relative_counts(self, counts: int, timeout_s: float = 8.0) -> str:
        hex_counts = f"{int(counts) & 0xFFFFFFFF:08X}"
        return self._command(f"mr{hex_counts}", timeout_s=timeout_s)

    def jog_test(self, delta_units: float = 5.0) -> bool:
        """Aller-retour de delta_units puis retour, en vérifiant le déplacement.

        Retourne True si la position a bougé puis est revenue près du départ."""
        start = self.get_position_counts()
        if start is None:
            print("      -> lecture de position impossible, test annulé.")
            return False

        if self.is_rotary:
            delta_counts = int(round(delta_units * self.pulses_per_unit / 360.0))
        else:
            delta_counts = int(round(delta_units * self.pulses_per_unit))

        self.move_relative_counts(delta_counts)
        time.sleep(0.3)
        moved = self.get_position_counts()

        self.move_relative_counts(-delta_counts)
        time.sleep(0.3)
        back = self.get_position_counts()

        if moved is None or back is None:
            print("      -> lecture de position pendant le test impossible.")
            return False

        u = self.units_label()
        print(f"      départ={self.counts_to_units(start):.3f}{u} "
              f"-> +{delta_units}{u}={self.counts_to_units(moved):.3f}{u} "
              f"-> retour={self.counts_to_units(back):.3f}{u}")

        did_move = abs(moved - start) > max(1, abs(delta_counts) // 4)
        came_back = abs(back - start) <= max(2, abs(delta_counts) // 10)
        return bool(did_move and came_back)


def list_serial_ports():
    ports = list(list_ports.comports())
    print("=== Ports série disponibles ===")
    if not ports:
        print("  (aucun port détecté)")
    for p in ports:
        print(f"  {p.device:8s} : {p.description}")
    print()
    return [p.device for p in ports]


def probe_port(port: str, baudrate: int, addresses: str, do_move: bool,
               move_deg: float) -> int:
    print(f"=== Sondage du bus Elliptec sur {port} "
          f"(adresses {addresses[0]}..{addresses[-1]}) ===")
    try:
        ser = serial.Serial(
            port,
            baudrate=baudrate,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
            timeout=1.0,
            write_timeout=1.0,
        )
    except Exception as e:
        print(f"  Impossible d'ouvrir {port} : {e}\n")
        return 0

    found = 0
    try:
        try:
            ser.reset_input_buffer()
            ser.reset_output_buffer()
        except Exception:
            pass

        for addr in addresses:
            dev = ElliptecBusDevice(ser, addr)
            if not dev.identify():
                continue

            found += 1
            unit = dev.units_label()
            pos = dev.get_position_units()
            pos_txt = f"{pos:.3f}{unit}" if pos is not None else "lecture échouée"
            kind = "rotatif" if dev.is_rotary else "linéaire"
            tag = "  [Cobolt HWP]" if addr == COBOLT_HWP_ADDRESS else ""

            print(f"  Adresse {addr} : {dev.model} ({kind}){tag}")
            print(f"      SN={dev.serial_no}  firmware={dev.firmware}  "
                  f"année={dev.year}")
            print(f"      course={dev.travel}{unit}  "
                  f"{dev.pulses_per_unit} counts/{'tour' if dev.is_rotary else 'mm'}  "
                  f"position={pos_txt}")

            if do_move:
                if addr == COBOLT_HWP_ADDRESS:
                    print("      /!\\ Adresse 0 = lame demi-onde du Cobolt : "
                          "un mouvement modifie la puissance laser. Test ignoré.")
                    print("          (utilisez --move-hwp pour forcer si le laser "
                          "est éteint/obturé.)")
                else:
                    ok = dev.jog_test(delta_units=move_deg)
                    print(f"      pilotage : {'OK' if ok else 'ECHEC'}")

    finally:
        try:
            ser.close()
        except Exception:
            pass

    print(f"  -> {found} monture(s) Elliptec détectée(s) sur {port}.\n")
    return found


def main():
    parser = argparse.ArgumentParser(
        description="Vérifie la détection et le pilotage des montures Elliptec "
                    "(bus/hub ELLB)."
    )
    parser.add_argument("--port", default="COM15",
                        help="Port série du bus Elliptec (défaut: COM15).")
    parser.add_argument("--baud", type=int, default=9600,
                        help="Débit série (défaut: 9600).")
    parser.add_argument("--scan-ports", action="store_true",
                        help="Sonde TOUS les ports série disponibles.")
    parser.add_argument("--move", action="store_true",
                        help="Effectue un petit aller-retour de test sur chaque "
                             "monture (sauf l'adresse 0 = HWP Cobolt).")
    parser.add_argument("--move-hwp", action="store_true",
                        help="Autorise aussi le test de mouvement sur l'adresse 0 "
                             "(lame demi-onde Cobolt). A n'utiliser que laser "
                             "éteint/obturé.")
    parser.add_argument("--move-deg", type=float, default=5.0,
                        help="Amplitude de l'aller-retour de test en degrés "
                             "(défaut: 5).")
    args = parser.parse_args()

    if args.move_hwp:
        # Autorise le mouvement sur l'adresse 0 en la retirant de la liste des
        # adresses "protégées".
        global COBOLT_HWP_ADDRESS
        COBOLT_HWP_ADDRESS = "__none__"

    available = list_serial_ports()

    ports = available if args.scan_ports else [args.port]

    total = 0
    for port in ports:
        total += probe_port(
            port=port,
            baudrate=args.baud,
            addresses=HEX_ADDRESSES,
            do_move=(args.move or args.move_hwp),
            move_deg=args.move_deg,
        )

    print("======================================")
    if total == 0:
        print("Aucune monture Elliptec détectée. Vérifiez le port et le câblage "
              "du hub ELLB.")
    else:
        print(f"Total : {total} monture(s) Elliptec détectée(s).")
        if total < 2:
            print("Une seule monture répond : si vous en attendez 2, vérifiez que "
                  "la seconde a bien une adresse de bus DIFFÉRENTE (0..F).")


if __name__ == "__main__":
    main()
