#!/usr/bin/env python3
"""
zettlab-tui - terminal control panel for Zettlab D6/D8 Ultra hardware.

Controls the three chassis fans, the front LCD backlight, and the RGB LED
controller from one curses interface. Stdlib only.

Run as root, or grant access to the individual sysfs nodes and /dev/ttyACM0.
"""

import curses
import glob
import os
import sys
import termios

# ---------------------------------------------------------------- fans

# Two drivers expose these fans with different attribute names:
#   zettlab_d8_fans (community DKMS module) -> pwm1, pwm2, pwm3
#   zettos_pwm_fan  (stock ZettOS kernel)   -> fan1_pwm .. and fan1_input (RPM)
FAN_DRIVERS = ("zettlab_d8_fans", "zettos_pwm_fan")

PWM_MIN, PWM_MAX = 0, 183  # hardware range on this chassis, not 0-255
FAN_LABELS = {1: "Disk fan 1", 2: "Disk fan 2", 3: "CPU fan"}


def find_hwmon(names):
    for d in sorted(glob.glob("/sys/class/hwmon/hwmon*")):
        try:
            with open(os.path.join(d, "name")) as f:
                if f.read().strip() in names:
                    return d
        except OSError:
            continue
    return None


def read_int(path):
    try:
        with open(path) as f:
            return int(f.read().strip())
    except (OSError, ValueError):
        return None


def write_int(path, value):
    try:
        with open(path, "w") as f:
            f.write(str(value))
        return None
    except OSError as e:
        return str(e)


class Fans:
    def __init__(self):
        self.hwmon = find_hwmon(FAN_DRIVERS)
        self.driver = None
        self.style = None
        if not self.hwmon:
            return
        with open(os.path.join(self.hwmon, "name")) as f:
            self.driver = f.read().strip()
        # Detect which attribute naming this driver uses
        if os.path.exists(os.path.join(self.hwmon, "pwm1")):
            self.style = "pwmN"
        elif os.path.exists(os.path.join(self.hwmon, "fan1_pwm")):
            self.style = "fanN_pwm"

    def pwm_path(self, n):
        return os.path.join(
            self.hwmon, f"pwm{n}" if self.style == "pwmN" else f"fan{n}_pwm"
        )

    def rpm_path(self, n):
        return os.path.join(self.hwmon, f"fan{n}_input")

    def available(self):
        return self.hwmon is not None and self.style is not None

    def state(self):
        out = {}
        for n in (1, 2, 3):
            out[n] = (read_int(self.pwm_path(n)), read_int(self.rpm_path(n)))
        return out

    def set_pwm(self, n, value):
        value = max(PWM_MIN, min(PWM_MAX, value))
        # The community module gates manual control behind pwmN_enable
        enable = os.path.join(self.hwmon, f"pwm{n}_enable")
        if os.path.exists(enable):
            write_int(enable, 1)
        return write_int(self.pwm_path(n), value)


def fan_services_running():
    """Fan curve services fight manual PWM writes. Report which are active."""
    active = []
    for svc in ("cpu-fan-curve", "hdd-fan-curve"):
        if os.system(f"systemctl is-active --quiet {svc} 2>/dev/null") == 0:
            active.append(svc)
    return active


# ------------------------------------------------------------- backlight

BACKLIGHT = "/sys/class/backlight/intel_backlight"


class Backlight:
    def __init__(self):
        self.path = BACKLIGHT if os.path.isdir(BACKLIGHT) else None
        self.max = read_int(os.path.join(BACKLIGHT, "max_brightness")) if self.path else None

    def available(self):
        return self.path is not None and self.max

    def get(self):
        return read_int(os.path.join(self.path, "brightness"))

    def set(self, value):
        value = max(0, min(self.max, value))
        return write_int(os.path.join(self.path, "brightness"), value)

    def percent(self):
        cur = self.get()
        return round(100 * cur / self.max) if cur is not None and self.max else 0


# ------------------------------------------------------------------ RGB

RGB_PORT = "/dev/ttyACM0"
RGB_MODES = {
    0: "OFF",
    1: "BREATHE",
    2: "FLOW",
    3: "FOUNTAIN",
    4: "GRADIENT",
    5: "FLICKER",
    6: "LIGHT",
}

# CRC-8/SAE J1850, polynomial 0x07, over payload bytes only
def _crc8_table():
    table = []
    for i in range(256):
        crc = i
        for _ in range(8):
            crc = ((crc << 1) ^ 0x07) & 0xFF if crc & 0x80 else (crc << 1) & 0xFF
        table.append(crc)
    return table


CRC8 = _crc8_table()


class Rgb:
    """Reimplements the protocol from rgb-led-control.md so the TUI stays
    self-contained. rgb_control.py remains the reference implementation."""

    def __init__(self):
        self.port = RGB_PORT if os.path.exists(RGB_PORT) else None
        self.mode = 6
        self.color = (255, 0, 0)
        self.speed = 5

    def available(self):
        return self.port is not None

    def send(self, mode, start, end, speed):
        payload = bytes([mode, *start, *end, (-speed) & 0xFF])
        crc = 0
        for b in payload:
            crc = CRC8[crc ^ b]
        frame = b"\xff\xff" + payload + bytes([crc])
        try:
            fd = os.open(self.port, os.O_RDWR | os.O_NOCTTY)
        except OSError as e:
            return str(e)
        try:
            attrs = termios.tcgetattr(fd)
            attrs[4] = attrs[5] = termios.B115200
            attrs[2] = termios.CS8 | termios.CREAD | termios.CLOCAL
            attrs[0] = attrs[1] = attrs[3] = 0
            termios.tcsetattr(fd, termios.TCSANOW, attrs)
            os.write(fd, frame)
        except OSError as e:
            return str(e)
        finally:
            os.close(fd)
        self.mode, self.color, self.speed = mode, start, speed
        return None


# ------------------------------------------------------------------ UI

HELP = [
    "j/k or arrows  move    h/l or -/+  adjust    J/L  adjust x10",
    "1/2/3 fan  b backlight  m LED mode  c LED colour  0 all off  q quit",
]

COLORS = [
    ("red", (255, 0, 0)),
    ("green", (0, 255, 0)),
    ("blue", (0, 0, 255)),
    ("white", (255, 255, 255)),
    ("dim white", (64, 64, 64)),
    ("orange", (255, 96, 0)),
    ("purple", (160, 0, 255)),
]


class App:
    def __init__(self):
        self.fans = Fans()
        self.backlight = Backlight()
        self.rgb = Rgb()
        self.row = 0
        self.color_idx = 0
        self.status = ""
        self.rows = []

    def build_rows(self):
        self.rows = []
        if self.fans.available():
            for n in (1, 2, 3):
                self.rows.append(("fan", n))
        if self.backlight.available():
            self.rows.append(("backlight", None))
        if self.rgb.available():
            self.rows.append(("mode", None))
            self.rows.append(("colour", None))

    def adjust(self, delta):
        if not self.rows:
            return
        kind, n = self.rows[self.row]
        if kind == "fan":
            cur = read_int(self.fans.pwm_path(n)) or 0
            err = self.fans.set_pwm(n, cur + delta)
            self.status = f"fan{n}: {err}" if err else f"fan{n} -> {cur + delta}"
        elif kind == "backlight":
            step = max(1, self.backlight.max // 100)
            cur = self.backlight.get() or 0
            err = self.backlight.set(cur + delta * step)
            self.status = f"backlight: {err}" if err else f"backlight {self.backlight.percent()}%"
        elif kind == "mode":
            m = (self.rgb.mode + (1 if delta > 0 else -1)) % 7
            err = self.rgb.send(m, self.rgb.color, self.rgb.color, self.rgb.speed)
            self.status = f"LED: {err}" if err else f"LED mode {RGB_MODES[m]}"
        elif kind == "colour":
            self.color_idx = (self.color_idx + (1 if delta > 0 else -1)) % len(COLORS)
            name, rgb = COLORS[self.color_idx]
            err = self.rgb.send(self.rgb.mode, rgb, rgb, self.rgb.speed)
            self.status = f"LED: {err}" if err else f"LED colour {name}"

    def all_off(self):
        if self.backlight.available():
            self.backlight.set(0)
        if self.rgb.available():
            self.rgb.send(0, (0, 0, 0), (0, 0, 0), 5)
        self.status = "display and LEDs off (fans untouched)"

    def draw(self, scr):
        scr.erase()
        h, w = scr.getmaxyx()
        scr.addstr(0, 0, "Zettlab D6/D8 Ultra - hardware control"[: w - 1], curses.A_BOLD)

        y = 2
        if not self.fans.available():
            scr.addstr(y, 2, "Fans: no zettlab_d8_fans / zettos_pwm_fan hwmon device found")
            y += 1
        else:
            scr.addstr(y, 0, f" Fans  [{self.fans.driver}]", curses.A_UNDERLINE)
            y += 1
            svcs = fan_services_running()
            if svcs:
                scr.addstr(y, 2, f"! {' and '.join(svcs)} active - they will override manual PWM",
                           curses.A_DIM)
                y += 1
            state = self.fans.state()
            for n in (1, 2, 3):
                pwm, rpm = state[n]
                sel = self.rows and self.rows[self.row] == ("fan", n)
                bar_w = 24
                filled = int(bar_w * (pwm or 0) / PWM_MAX)
                bar = "#" * filled + "." * (bar_w - filled)
                rpm_txt = f"{rpm:>5} rpm" if rpm is not None else "   -- rpm"
                line = f"  {FAN_LABELS[n]:<12} [{bar}] {str(pwm):>3}/{PWM_MAX}  {rpm_txt}"
                scr.addstr(y, 0, line[: w - 1], curses.A_REVERSE if sel else 0)
                y += 1

        y += 1
        scr.addstr(y, 0, " Front LCD", curses.A_UNDERLINE)
        y += 1
        if not self.backlight.available():
            scr.addstr(y, 2, "no intel_backlight found")
            y += 1
        else:
            sel = self.rows and self.rows[self.row] == ("backlight", None)
            pct = self.backlight.percent()
            bar_w = 24
            filled = int(bar_w * pct / 100)
            bar = "#" * filled + "." * (bar_w - filled)
            line = f"  {'Backlight':<12} [{bar}] {pct:>3}%  ({self.backlight.get()}/{self.backlight.max})"
            scr.addstr(y, 0, line[: w - 1], curses.A_REVERSE if sel else 0)
            y += 1

        y += 1
        scr.addstr(y, 0, " RGB LEDs", curses.A_UNDERLINE)
        y += 1
        if not self.rgb.available():
            scr.addstr(y, 2, f"{RGB_PORT} not present (cdc_acm not bound?)")
            y += 1
        else:
            for kind, label, value in (
                ("mode", "Mode", RGB_MODES[self.rgb.mode]),
                ("colour", "Colour", COLORS[self.color_idx][0]),
            ):
                sel = self.rows and self.rows[self.row] == (kind, None)
                line = f"  {label:<12} {value}"
                scr.addstr(y, 0, line[: w - 1], curses.A_REVERSE if sel else 0)
                y += 1

        if self.status:
            scr.addstr(h - 4, 0, self.status[: w - 1], curses.A_BOLD)
        for i, line in enumerate(HELP):
            scr.addstr(h - 2 + i, 0, line[: w - 1], curses.A_DIM)
        scr.refresh()

    def run(self, scr):
        curses.curs_set(0)
        scr.timeout(1000)  # refresh RPM once per second
        while True:
            self.build_rows()
            self.row = max(0, min(self.row, max(0, len(self.rows) - 1)))
            self.draw(scr)
            try:
                ch = scr.getch()
            except KeyboardInterrupt:
                return
            if ch in (ord("q"), 27):
                return
            elif ch in (ord("j"), curses.KEY_DOWN):
                self.row = (self.row + 1) % max(1, len(self.rows))
            elif ch in (ord("k"), curses.KEY_UP):
                self.row = (self.row - 1) % max(1, len(self.rows))
            elif ch in (ord("l"), ord("+"), ord("="), curses.KEY_RIGHT):
                self.adjust(1)
            elif ch in (ord("h"), ord("-"), curses.KEY_LEFT):
                self.adjust(-1)
            elif ch == ord("L"):
                self.adjust(10)
            elif ch == ord("J"):
                self.adjust(-10)
            elif ch in (ord("1"), ord("2"), ord("3")):
                target = ("fan", ch - ord("0"))
                if target in self.rows:
                    self.row = self.rows.index(target)
            elif ch == ord("b") and ("backlight", None) in self.rows:
                self.row = self.rows.index(("backlight", None))
            elif ch == ord("m") and ("mode", None) in self.rows:
                self.row = self.rows.index(("mode", None))
            elif ch == ord("c") and ("colour", None) in self.rows:
                self.row = self.rows.index(("colour", None))
            elif ch == ord("0"):
                self.all_off()


def main():
    app = App()
    if not (app.fans.available() or app.backlight.available() or app.rgb.available()):
        print("No Zettlab hardware interfaces found.", file=sys.stderr)
        print("Expected at least one of:", file=sys.stderr)
        print("  hwmon named zettlab_d8_fans or zettos_pwm_fan", file=sys.stderr)
        print(f"  {BACKLIGHT}", file=sys.stderr)
        print(f"  {RGB_PORT}", file=sys.stderr)
        return 1
    curses.wrapper(app.run)
    return 0


if __name__ == "__main__":
    sys.exit(main())
