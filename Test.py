import argparse
import time
from typing import Optional

import serial
from serial.tools import list_ports


# ELL14: 143360 counts / revolution.
# Donc 360° = 143360 counts.
ELL14_COUNTS_PER_REV = 143360
COUNTS_PER_DEGREE = ELL14_COUNTS_PER_REV / 360.0


class ElliptecELL14:
    """
    Petit driver de test pour Thorlabs ELL14 via ELLC.

    Commandes Elliptec utilisées :
      - {addr}in       : device info
      - {addr}ho0      : home
      - {addr}gp       : get position
      - {addr}maXXXXXXXX : move absolute, position en hex signé 32 bits
      - {addr}mrXXXXXXXX : move relative, delta en hex signé 32 bits

    addr est souvent '0' si un seul périphérique est branché.
    """

    def __init__(
        self,
        port: str,
        address: str = "0",
        baudrate: int = 9600,
        timeout: float = 1.0,
        debug: bool = True,
    ):
        self.port = port
        self.address = address
        self.debug = debug

        self.ser = serial.Serial(
            port=port,
            baudrate=baudrate,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
            timeout=timeout,
            write_timeout=timeout,
        )

        # Petit flush au cas où il reste des réponses anciennes
        self.ser.reset_input_buffer()
        self.ser.reset_output_buffer()
        time.sleep(0.1)

    def close(self):
        if self.ser and self.ser.is_open:
            self.ser.close()

    def _write(self, payload: str):
        """
        Les commandes Elliptec sont ASCII.
        Le terminateur CR est généralement accepté par les contrôleurs ELL.
        """
        msg = f"{self.address}{payload}\r"
        if self.debug:
            print(f"TX: {msg.encode().hex(' ')}  ASCII={msg!r}")
        self.ser.write(msg.encode("ascii"))
        self.ser.flush()

    def _read_available(self, wait_s: float = 0.2) -> str:
        """
        Lecture souple : certains firmwares répondent vite, d'autres mettent un peu plus longtemps.
        """
        time.sleep(wait_s)

        chunks = []
        start = time.time()

        while time.time() - start < 2.0:
            n = self.ser.in_waiting
            if n:
                chunks.append(self.ser.read(n))
                time.sleep(0.05)
            else:
                break

        raw = b"".join(chunks)
        text = raw.decode("ascii", errors="replace").strip()

        if self.debug:
            print(f"RX: {raw.hex(' ')}  ASCII={text!r}")

        return text

    def command(self, payload: str, wait_s: float = 0.2) -> str:
        self._write(payload)
        return self._read_available(wait_s=wait_s)

    @staticmethod
    def counts_to_hex32(counts: int) -> str:
        """
        Conversion int signé Python -> hex 32 bits attendu par le contrôleur.
        """
        counts = int(round(counts))
        return f"{counts & 0xFFFFFFFF:08X}"

    @staticmethod
    def hex32_to_counts(hex_value: str) -> int:
        """
        Conversion hex 32 bits signé -> int Python.
        """
        value = int(hex_value, 16)
        if value & 0x80000000:
            value -= 0x100000000
        return value

    @staticmethod
    def deg_to_counts(deg: float) -> int:
        return int(round(deg * COUNTS_PER_DEGREE))

    @staticmethod
    def counts_to_deg(counts: int) -> float:
        return counts / COUNTS_PER_DEGREE

    def info(self) -> str:
        return self.command("in", wait_s=0.3)

    def home(self):
        print("\n--- HOME ---")
        response = self.command("ho0", wait_s=0.5)

        # Le home peut prendre un moment. On attend un peu puis on lit la position.
        time.sleep(3.0)
        print("Position après home :", self.get_position_deg())

        return response

    def get_position_counts(self) -> Optional[int]:
        response = self.command("gp", wait_s=0.2)

        # Réponse typique : 0POXXXXXXXX
        # On cherche le champ après "PO".
        idx = response.find("PO")
        if idx < 0 or len(response) < idx + 10:
            print(f"Impossible de parser la position depuis : {response!r}")
            return None

        hex_pos = response[idx + 2: idx + 10]
        return self.hex32_to_counts(hex_pos)

    def get_position_deg(self) -> Optional[float]:
        counts = self.get_position_counts()
        if counts is None:
            return None
        return self.counts_to_deg(counts)

    def move_absolute_deg(self, deg: float):
        counts = self.deg_to_counts(deg)
        hex_counts = self.counts_to_hex32(counts)

        print(f"\n--- MOVE ABS {deg:.3f}° -> counts={counts}, hex={hex_counts} ---")
        response = self.command(f"ma{hex_counts}", wait_s=0.5)

        # Attente conservative pour un test manuel.
        # À remplacer par une attente plus propre dans DeepLight.
        time.sleep(2.0)
        pos = self.get_position_deg()
        print(f"Position lue : {pos}")

        return response

    def move_relative_deg(self, delta_deg: float):
        counts = self.deg_to_counts(delta_deg)
        hex_counts = self.counts_to_hex32(counts)

        print(f"\n--- MOVE REL {delta_deg:+.3f}° -> counts={counts}, hex={hex_counts} ---")
        response = self.command(f"mr{hex_counts}", wait_s=0.5)

        time.sleep(2.0)
        pos = self.get_position_deg()
        print(f"Position lue : {pos}")

        return response


def list_serial_ports():
    print("Ports série disponibles :")
    for p in list_ports.comports():
        print(f"  {p.device:10s}  {p.description}")


def main():
    parser = argparse.ArgumentParser(description="Test Thorlabs ELL14 via ELLC")
    parser.add_argument("--port", required=False, help="Port série, ex: COM11")
    parser.add_argument("--addr", default="0", help="Adresse Elliptec, souvent 0")
    parser.add_argument("--no-home", action="store_true", help="Ne pas faire de home au démarrage")
    parser.add_argument("--baudrate", type=int, default=9600)
    parser.add_argument("--quiet", action="store_true", help="Désactive les logs TX/RX")
    args = parser.parse_args()

    if not args.port:
        list_serial_ports()
        print("\nRelance avec par exemple :")
        print("  python ell14_test.py --port COM11")
        return

    mount = ElliptecELL14(
        port=args.port,
        address=args.addr,
        baudrate=args.baudrate,
        debug=not args.quiet,
    )

    try:
        print("\n=== INFO ===")
        print(mount.info())

        print("\n=== POSITION INITIALE ===")
        print(mount.get_position_deg())

        if not args.no_home:
            mount.home()

        print("\n=== TEST ROTATION SEQUENCE ===")

        sequence_deg = list(range(0, 91, 5)) + [45, 0]

        for angle in sequence_deg:
            print(f"\n>>> Angle cible : {angle}°")
            mount.move_absolute_deg(angle)
            time.sleep(2.0)

        print("\nSéquence terminée.")

        print("\nTest terminé.")

    finally:
        mount.close()


if __name__ == "__main__":
    main()