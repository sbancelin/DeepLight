import serial
import time

with serial.Serial("COM14", 115200, timeout=1, write_timeout=1) as s:
    tests = [
        b"\x02PB1?\r",
        b"\x02PB1?\n",
        b"\x02PB1?\r\n",
        b"\x02STATUS?\r",
        b"\x02P?\r",
        b"\x02ID?\r",
        b"\x02VER?\r",
    ]

    for cmd in tests:
        s.reset_input_buffer()
        s.write(cmd)
        s.flush()
        time.sleep(0.2)
        ans = s.read(128)
        print(repr(cmd), "->", repr(ans))