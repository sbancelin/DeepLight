"""
Diagnostic du bus Elliptec (hub ELLB) : qui répond, à quelle adresse ?

Usage (sur le PC du microscope, DeepLight fermé pour libérer le port) :

    python scan_elliptec_bus.py                 # scanne COM15, adresses 0..F
    python scan_elliptec_bus.py --port COM15
    python scan_elliptec_bus.py --addresses 012
    python scan_elliptec_bus.py --move 0 --deg 5   # test de rotation +5 deg

Le script n'utilise QUE pyserial : il reproduit exactement ce que fait
_ElliptecBus dans Hardware_Manager.py, mais en affichant les octets bruts.
"""

from __future__ import annotations

import argparse
import time

import serial

ELL_TYPES = {
    "05": "ELL5 (rotation mount)",
    "06": "ELL6 (dual position slider)",
    "07": "ELL7 (linear stage)",
    "08": "ELL8 (rotation stage)",
    "09": "ELL9 (four position slider)",
    "0E": "ELL14 (rotation mount)",
    "0F": "ELL15",
    "11": "ELL17/20 (linear stage)",
    "12": "ELL18",
    "14": "ELL20",
}


def open_port(port: str, baudrate: int, timeout_s: float) -> serial.Serial:
    ser = serial.Serial(
        port,
        baudrate=baudrate,
        bytesize=serial.EIGHTBITS,
        parity=serial.PARITY_NONE,
        stopbits=serial.STOPBITS_ONE,
        timeout=timeout_s,
        write_timeout=timeout_s,
    )
    # Laisse le convertisseur USB-série se stabiliser : la toute première
    # trame émise juste après l'ouverture est régulièrement perdue.
    time.sleep(0.25)
    ser.reset_input_buffer()
    ser.reset_output_buffer()
    return ser


def command(ser: serial.Serial, address: str, payload: str, timeout_s: float = 1.0) -> str:
    msg = f"{address}{payload}\r".encode("ascii")
    ser.reset_input_buffer()
    prev = ser.timeout
    try:
        ser.timeout = float(timeout_s)
        ser.write(msg)
        ser.flush()
        raw = ser.read_until(b"\n")
    finally:
        ser.timeout = prev
    return raw.decode("ascii", errors="replace").strip()


def decode_info(resp: str) -> str:
    """Décode une réponse 'IN' : <a>IN <type2><serial8><year4><fw2><hw2><travel4><pulses8>."""
    idx = resp.find("IN")
    if idx < 0 or len(resp) < idx + 25:
        return ""
    body = resp[idx + 2:]
    dev_type = body[0:2]
    serial_no = body[2:10]
    year = body[10:14]
    fw = body[14:16]
    hw = body[16:18]
    travel = body[18:22]
    pulses = body[22:30]
    try:
        pulses_dec = str(int(pulses, 16))
    except ValueError:
        pulses_dec = "?"
    try:
        travel_dec = str(int(travel, 16))
    except ValueError:
        travel_dec = "?"
    label = ELL_TYPES.get(dev_type.upper(), f"type 0x{dev_type}")
    return (f"{label} | serial={serial_no} | année={year} | fw={fw} hw={hw} "
            f"| course={travel_dec} | counts/unité={pulses_dec}")


def decode_position(resp: str) -> str:
    idx = resp.find("PO")
    if idx < 0 or len(resp) < idx + 10:
        return ""
    try:
        counts = int(resp[idx + 2: idx + 10], 16)
    except ValueError:
        return ""
    if counts & 0x80000000:
        counts -= 0x100000000
    deg = counts / (143360 / 360.0)
    return f"{counts} counts = {deg:.3f} deg (si 143360 counts/tour)"


def main():
    ap = argparse.ArgumentParser(description="Scan du bus Elliptec / ELLB")
    ap.add_argument("--port", default="COM15")
    ap.add_argument("--baudrate", type=int, default=9600)
    ap.add_argument("--timeout", type=float, default=1.0)
    ap.add_argument("--addresses", default="0123456789ABCDEF")
    ap.add_argument("--retries", type=int, default=2,
                    help="Nombre d'essais par commande (défaut 2)")
    ap.add_argument("--move", default=None,
                    help="Adresse à faire tourner après le scan (ex: 0)")
    ap.add_argument("--deg", type=float, default=5.0,
                    help="Rotation relative en degrés pour --move")
    args = ap.parse_args()

    print(f"Ouverture de {args.port} @ {args.baudrate} bauds...")
    ser = open_port(args.port, args.baudrate, args.timeout)
    print("Port ouvert.\n")

    found = []
    try:
        for addr in args.addresses:
            resp = ""
            for attempt in range(args.retries):
                resp = command(ser, addr, "in", timeout_s=args.timeout)
                if resp:
                    break
                time.sleep(0.05)

            if not resp:
                print(f"  adresse {addr} : (pas de réponse)")
                continue

            info = decode_info(resp)
            print(f"  adresse {addr} : {resp!r}")
            if info:
                print(f"              -> {info}")
            found.append(addr)

            pos = command(ser, addr, "gp", timeout_s=args.timeout)
            print(f"              gp -> {pos!r}  {decode_position(pos)}")
            status = command(ser, addr, "gs", timeout_s=args.timeout)
            print(f"              gs -> {status!r}")

        print()
        if found:
            print(f"Adresses actives : {', '.join(found)}")
        else:
            print("AUCUNE monture n'a répondu : vérifier alimentation du hub ELLB, "
                  "câblage, port COM et vitesse (9600 bauds par défaut).")

        if args.move is not None:
            addr = args.move
            print(f"\nTest de rotation sur l'adresse {addr} : {args.deg:+.3f} deg")
            counts = int(round(args.deg * (143360 / 360.0)))
            hex_counts = f"{counts & 0xFFFFFFFF:08X}"
            before = command(ser, addr, "gp", timeout_s=args.timeout)
            print(f"  avant : {before!r}  {decode_position(before)}")
            resp = command(ser, addr, f"mr{hex_counts}", timeout_s=15.0)
            print(f"  mr    : {resp!r}  {decode_position(resp)}")
            after = command(ser, addr, "gp", timeout_s=args.timeout)
            print(f"  après : {after!r}  {decode_position(after)}")
    finally:
        ser.close()
        print("\nPort fermé.")


if __name__ == "__main__":
    main()
