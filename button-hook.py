#!/usr/bin/env python3
"""
Listen for Zettlab D6/D8 Ultra chassis buttons and run user commands.

Uses the input device created by zettlab_gpio_keys.ko (name "zettlab-gpio-keys"):
  KEY_1  = COPY (front button)
  KEY_2  = RESET (rear button; optional long-press handler)

Usage:
  python3 button-hook.py --cmd 'echo COPY pressed'
  python3 button-hook.py --cmd '...' --reset-cmd '...' --reset-hold 5

Optional fallback (needs root): poll hardware registers via /dev/mem with --mmio.
"""
from __future__ import annotations

import argparse
import os
import select
import struct
import subprocess
import sys
import time
from pathlib import Path

EV_KEY = 0x01
KEY_1 = 2   # COPY
KEY_2 = 3   # RESET
KEY_DOWN = 1
KEY_UP = 0
KEY_REPEAT = 2

# input_event: timeval (16 on 64-bit) + type u16 + code u16 + value s32 = 24
EVENT_FMT = "llHHi"
EVENT_SIZE = struct.calcsize(EVENT_FMT)

MMIO_BASE = 0xE0D20000
MMIO_SIZE = 0xF000
OFF_COPY = 0x6C0
OFF_RESET = 0x6B0


def find_input_by_name(name: str) -> Path | None:
    base = Path("/sys/class/input")
    if not base.exists():
        return None
    for ent in base.iterdir():
        if not ent.name.startswith("event"):
            continue
        npath = ent / "device" / "name"
        try:
            if npath.read_text().strip() == name:
                return Path("/dev/input") / ent.name
        except OSError:
            continue
    return None


def run_cmd(cmd: str) -> None:
    print(f"[hook] run: {cmd}", flush=True)
    try:
        subprocess.Popen(cmd, shell=True)
    except Exception as e:
        print(f"[hook] failed: {e}", file=sys.stderr, flush=True)


def listen_evdev(dev: Path, copy_cmd: str, reset_cmd: str | None, reset_hold: float) -> None:
    print(f"[hook] listening on {dev}", flush=True)
    fd = os.open(dev, os.O_RDONLY | os.O_NONBLOCK)
    reset_t0 = None
    try:
        while True:
            r, _, _ = select.select([fd], [], [], 1.0)
            if not r:
                continue
            data = os.read(fd, EVENT_SIZE * 32)
            for off in range(0, len(data) // EVENT_SIZE * EVENT_SIZE, EVENT_SIZE):
                _s, _us, etype, code, value = struct.unpack_from(EVENT_FMT, data, off)
                if etype != EV_KEY:
                    continue
                if code == KEY_1 and value == KEY_DOWN:
                    print("[hook] COPY pressed", flush=True)
                    if copy_cmd:
                        run_cmd(copy_cmd)
                elif code == KEY_2:
                    if value == KEY_DOWN:
                        reset_t0 = time.monotonic()
                        print("[hook] RESET down", flush=True)
                    elif value == KEY_UP:
                        held = (time.monotonic() - reset_t0) if reset_t0 else 0
                        reset_t0 = None
                        print(f"[hook] RESET up after {held:.1f}s", flush=True)
                        if reset_cmd and held >= reset_hold:
                            print("[hook] RESET long-press", flush=True)
                            run_cmd(reset_cmd)
                    elif value == KEY_REPEAT and reset_t0 and reset_cmd:
                        held = time.monotonic() - reset_t0
                        if held >= reset_hold:
                            print("[hook] RESET long-press", flush=True)
                            run_cmd(reset_cmd)
                            reset_t0 = None
    finally:
        os.close(fd)


def listen_mmio(copy_cmd: str, reset_cmd: str | None, reset_hold: float, poll_ms: int) -> None:
    import mmap

    print(
        f"[hook] MMIO poll @ 0x{MMIO_BASE:x} "
        f"(COPY +0x{OFF_COPY:x}, RESET +0x{OFF_RESET:x})",
        flush=True,
    )
    fd = os.open("/dev/mem", os.O_RDONLY | os.O_SYNC)
    mm = mmap.mmap(fd, MMIO_SIZE, mmap.MAP_SHARED, mmap.PROT_READ, offset=MMIO_BASE)

    def pressed(off: int) -> bool:
        v = struct.unpack_from("<I", mm, off)[0]
        return ((v >> 1) & 1) == 0

    prev_c, prev_r = pressed(OFF_COPY), pressed(OFF_RESET)
    reset_t0 = None
    try:
        while True:
            c, r = pressed(OFF_COPY), pressed(OFF_RESET)
            if c and not prev_c:
                print("[hook] COPY pressed", flush=True)
                if copy_cmd:
                    run_cmd(copy_cmd)
            if r and not prev_r:
                reset_t0 = time.monotonic()
                print("[hook] RESET down", flush=True)
            if (not r) and prev_r:
                held = (time.monotonic() - reset_t0) if reset_t0 else 0
                reset_t0 = None
                print(f"[hook] RESET up after {held:.1f}s", flush=True)
                if reset_cmd and held >= reset_hold:
                    run_cmd(reset_cmd)
            if r and reset_t0 and reset_cmd and (time.monotonic() - reset_t0) >= reset_hold:
                print("[hook] RESET long-press", flush=True)
                run_cmd(reset_cmd)
                reset_t0 = None
            prev_c, prev_r = c, r
            time.sleep(poll_ms / 1000.0)
    finally:
        mm.close()
        os.close(fd)


def main() -> int:
    ap = argparse.ArgumentParser(description="Zettlab chassis button hook")
    ap.add_argument("--cmd", default="", help="Shell command on COPY press")
    ap.add_argument("--reset-cmd", default=None, help="Shell command on RESET long-press")
    ap.add_argument("--reset-hold", type=float, default=5.0, help="RESET hold seconds (default 5)")
    ap.add_argument("--mmio", action="store_true", help="Force /dev/mem path (needs root)")
    ap.add_argument("--poll-ms", type=int, default=20)
    ap.add_argument("--device-name", default="zettlab-gpio-keys")
    args = ap.parse_args()

    if not args.cmd and not args.reset_cmd:
        print("Provide --cmd and/or --reset-cmd", file=sys.stderr)
        return 2

    if not args.mmio:
        dev = find_input_by_name(args.device_name)
        if dev:
            listen_evdev(dev, args.cmd, args.reset_cmd, args.reset_hold)
            return 0
        print(f"[hook] input device '{args.device_name}' not found; trying MMIO", flush=True)

    if not os.path.exists("/dev/mem"):
        print("No input device and no /dev/mem. Load zettlab_gpio_keys.ko first.", file=sys.stderr)
        return 1
    try:
        listen_mmio(args.cmd, args.reset_cmd, args.reset_hold, args.poll_ms)
    except PermissionError:
        print(
            "MMIO needs root (sudo), or load the kernel module and re-run without --mmio.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
