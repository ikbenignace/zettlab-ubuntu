#!/usr/bin/env python3
"""Send a raw RGB frame with an explicit speed byte (no negation)."""
import os, sys, termios

def crc8_table():
    t = []
    for i in range(256):
        c = i
        for _ in range(8):
            c = ((c << 1) ^ 0x07) & 0xFF if c & 0x80 else (c << 1) & 0xFF
        t.append(c)
    return t
T = crc8_table()

def send(mode, s, e, raw_speed):
    payload = bytes([mode, *s, *e, raw_speed & 0xFF])
    crc = 0
    for b in payload:
        crc = T[crc ^ b]
    frame = b"\xff\xff" + payload + bytes([crc])
    fd = os.open("/dev/ttyACM0", os.O_RDWR | os.O_NOCTTY)
    a = list(termios.tcgetattr(fd))
    a[0] = a[1] = a[3] = 0
    a[2] = termios.CS8 | termios.CREAD | termios.CLOCAL
    a[4] = a[5] = termios.B115200
    termios.tcsetattr(fd, termios.TCSANOW, a)
    os.write(fd, frame)
    os.close(fd)
    print("mode=%d speed_byte=0x%02X frame=%s" % (mode, raw_speed & 0xFF, frame.hex()))

if __name__ == "__main__":
    m = int(sys.argv[1]); sp = int(sys.argv[2])
    s = tuple(int(x) for x in sys.argv[3:6]) if len(sys.argv) > 6 else (255, 0, 0)
    e = tuple(int(x) for x in sys.argv[6:9]) if len(sys.argv) > 8 else (0, 0, 255)
    send(m, s, e, sp)
