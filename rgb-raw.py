#!/usr/bin/env python3
"""
rgb-raw - send RGB frames to the Zettlab LED controller with full control
over the raw speed byte.

rgb_control.py negates the speed value before sending it (`-(speed) & 0xFF`),
which makes the relationship between the number you type and what the hardware
does non-obvious. This tool sends the byte you ask for, unmodified, so you can
map the behaviour yourself.

Examples:
    rgb-raw.py 6                         solid red
    rgb-raw.py 1 --speed 20              breathe, speed byte 20
    rgb-raw.py 1 --start 0,255,0 --speed 200
    rgb-raw.py 4 --start 255,0,0 --end 0,0,255 --speed 60
    rgb-raw.py 1 --sweep 1,20,60,128,200,253 --hold 8
    rgb-raw.py 0                         off

Modes: 0 OFF, 1 BREATHE, 2 FLOW, 3 FOUNTAIN, 4 GRADIENT, 5 FLICKER, 6 LIGHT
"""
import os
import sys
import termios
import time

DEVICE = "/dev/ttyACM0"
MODES = {0: "OFF", 1: "BREATHE", 2: "FLOW", 3: "FOUNTAIN",
         4: "GRADIENT", 5: "FLICKER", 6: "LIGHT"}


def crc8_table():
    """CRC-8/SAE J1850, polynomial 0x07."""
    table = []
    for i in range(256):
        crc = i
        for _ in range(8):
            crc = ((crc << 1) ^ 0x07) & 0xFF if crc & 0x80 else (crc << 1) & 0xFF
        table.append(crc)
    return table


CRC8 = crc8_table()


def build(mode, start, end, speed_byte):
    payload = bytes([mode, *start, *end, speed_byte & 0xFF])
    crc = 0
    for b in payload:
        crc = CRC8[crc ^ b]
    return b"\xff\xff" + payload + bytes([crc])


def send(frame):
    fd = os.open(DEVICE, os.O_RDWR | os.O_NOCTTY)
    try:
        attrs = list(termios.tcgetattr(fd))
        attrs[0] = attrs[1] = attrs[3] = 0
        attrs[2] = termios.CS8 | termios.CREAD | termios.CLOCAL
        attrs[4] = attrs[5] = termios.B115200
        termios.tcsetattr(fd, termios.TCSANOW, attrs)
        os.write(fd, frame)
    finally:
        os.close(fd)


def rgb(text):
    parts = [int(x) for x in text.split(",")]
    if len(parts) != 3 or not all(0 <= p <= 255 for p in parts):
        raise ValueError(f"expected R,G,B with values 0-255, got {text!r}")
    return tuple(parts)


def main():
    args = sys.argv[1:]
    if not args or args[0] in ("-h", "--help"):
        print(__doc__.strip())
        return 0

    mode = int(args[0])
    if mode not in MODES:
        print(f"mode must be 0-6, got {mode}", file=sys.stderr)
        return 1

    start, end = (255, 0, 0), None
    speed, sweep, hold = 128, None, 6.0

    i = 1
    while i < len(args):
        a = args[i]
        if a == "--start":
            start = rgb(args[i + 1]); i += 2
        elif a == "--end":
            end = rgb(args[i + 1]); i += 2
        elif a == "--speed":
            speed = int(args[i + 1]) & 0xFF; i += 2
        elif a == "--sweep":
            sweep = [int(x) & 0xFF for x in args[i + 1].split(",")]; i += 2
        elif a == "--hold":
            hold = float(args[i + 1]); i += 2
        else:
            print(f"unknown option {a!r}", file=sys.stderr)
            return 1

    if end is None:
        end = start if mode != 1 else (0, 0, 0)

    steps = sweep if sweep else [speed]
    for n, sp in enumerate(steps):
        frame = build(mode, start, end, sp)
        send(frame)
        print(f"{MODES[mode]:<9} speed_byte={sp:>3} (0x{sp:02X})  "
              f"start={start} end={end}  frame={frame.hex()}", flush=True)
        if len(steps) > 1 and n < len(steps) - 1:
            time.sleep(hold)
    return 0


if __name__ == "__main__":
    sys.exit(main())
