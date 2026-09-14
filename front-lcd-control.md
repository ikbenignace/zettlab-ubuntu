# Front LCD Control (eDP-1)

> How ZettOS drives the 3.49" front panel, and how to reproduce it on Ubuntu. Includes backlight control, rendering options, and an explanation of the HDMI resolution conflict documented elsewhere in this repo.

> Findings below were read directly from a running ZettOS install on a D6 Ultra (2026-09-14). The ZettOS observations are verified; the Ubuntu procedures are derived from them and not yet hardware-tested.

## Panel Facts

| Property | Value |
|---|---|
| Connector | `card0-eDP-1` |
| Native resolution | **172×640 portrait** — not 640×172 |
| Colour depth | 32 bpp |
| fbdev stride | 704 bytes/row (176 px padded) |
| fbdev name | `i915drmfb` (`/dev/fb0`) |
| Backlight | `/sys/class/backlight/intel_backlight` |
| Backlight range | 0 – **192000** |

The panel is physically mounted rotated. Content must be rotated 90° to read normally.

## How ZettOS Does It

```
weston.service            Wayland compositor, drm-backend.so, on tty7
  └─ zettos-lcd-display   LVGL application, renders via lv_wayland
```

Relevant details from `/lib/systemd/system/zettos-lcd-display.service`:

```ini
After=weston.service
Wants=weston.service

Environment="WAYLAND_DISPLAY=wayland-1"
Environment="XDG_RUNTIME_DIR=/run"
Environment="LD_LIBRARY_PATH=/zettos/emmc/lib"
ExecStart=/zettos/emmc/bin/zettos-lcd-display
```

The binary is an [LVGL](https://lvgl.io/) app (`_lv_wayland_flush`, `lv_font_d_din_pro_*`) that reads its brightness setting from `/zettos/emmc/config/lcd_brightness` and writes to:

```
/sys/devices/pci0000:00/0000:00:02.0/drm/card0/card0-eDP-1/intel_backlight/brightness
```

which is the same file as `/sys/class/backlight/intel_backlight/brightness`.

ZettOS' `weston.ini` uses `backend=drm-backend.so` with no `[output]` section — Weston drives each connector at its own native mode.

## Why HDMI Gets Forced to the LCD Resolution

[kernel-parameters.md](kernel-parameters.md) and [ubuntu-installation.md](ubuntu-installation.md) record this as unresolved: enabling `eDP-1` forces HDMI to 640×172. Here is the mechanism.

i915 exposes **one** fbdev emulation device (`/dev/fb0`) for the whole card, shared by every enabled connector. `fbcon` must pick a single mode that fits them all, so a connected 172×640 panel drags the console down to something that fits both.

**This limitation belongs to fbdev/fbcon, not to the hardware.** Any KMS-aware client — Weston, X, or a direct DRM program — sets modes per connector independently and is unaffected.

ZettOS proves it. Its kernel command line contains **no `video=` parameter at all**:

```
i915.enable_guc=3 i915.max_vfs=7 i915.force_probe=* i915.request_timeout_ms=60000
quiet systemd.show_status=false vt.global_cursor_default=0 console=tty2
snd_intel_dspcfg.dsp_driver=1 nvme_core.default_ps_max_latency_us=0 ...
```

`eDP-1` is enabled and HDMI works, because Weston owns the display and the console was moved out of the way with `console=tty2`.

### What this means for Ubuntu

`video=eDP-1:d` **disables the connector at kernel level**. Nothing can then use the LCD — not Weston, not a DRM client, nothing.

| Goal | Kernel command line |
|---|---|
| HDMI console readable, LCD unused | keep `video=eDP-1:d` (current repo default) |
| Use the LCD | **remove** `video=eDP-1:d` |

If you remove it, the tty on HDMI may look wrong. On a headless NAS that is cosmetic — you reach it over SSH. Borrow ZettOS' mitigation if it bothers you:

```bash
GRUB_CMDLINE_LINUX_DEFAULT="quiet console=tty2 vt.global_cursor_default=0 modprobe.blacklist=r8169 snd_intel_dspcfg.dsp_driver=1"
```

> Keep `video=eDP-1:d` during installation regardless. The installer runs on the console, which is exactly the thing this conflict breaks. Remove it afterwards.

## Backlight Control

This works with no extra software and is the whole answer to "turn the display on and off".

```bash
cat /sys/class/backlight/intel_backlight/max_brightness    # 192000
cat /sys/class/backlight/intel_backlight/brightness

# Off
echo 0      | sudo tee /sys/class/backlight/intel_backlight/brightness

# 50%
echo 96000  | sudo tee /sys/class/backlight/intel_backlight/brightness

# Full
echo 192000 | sudo tee /sys/class/backlight/intel_backlight/brightness
```

> On a freshly booted ZettOS the value is `0` — the panel is dark by default on a headless unit. If you see nothing on the LCD, check this before assuming your rendering is broken.

### Without sudo

```bash
sudo tee /etc/udev/rules.d/90-backlight.rules > /dev/null << 'EOF'
ACTION=="add", SUBSYSTEM=="backlight", KERNEL=="intel_backlight", RUN+="/bin/chgrp video /sys/class/backlight/%k/brightness", RUN+="/bin/chmod g+w /sys/class/backlight/%k/brightness"
EOF

sudo usermod -aG video $USER
sudo udevadm control --reload-rules && sudo udevadm trigger
# log out and back in
```

## Rendering Options

### Option 1: Direct framebuffer (simplest)

No compositor needed. Write pixels straight to `/dev/fb0`.

```python
#!/usr/bin/env python3
"""Fill the front LCD with a solid colour."""
import mmap

WIDTH, HEIGHT = 172, 640
BPP, STRIDE = 4, 704          # 176 px padded * 4 bytes

def fill(r, g, b):
    pixel = bytes((b, g, r, 0))          # i915 fbdev is XRGB8888 little-endian
    row = pixel * WIDTH
    with open("/dev/fb0", "r+b") as f:
        fb = mmap.mmap(f.fileno(), STRIDE * HEIGHT)
        for y in range(HEIGHT):
            off = y * STRIDE
            fb[off:off + WIDTH * BPP] = row
        fb.flush()
        fb.close()

if __name__ == "__main__":
    fill(0, 40, 90)
```

Run it as root (or add yourself to the `video` group), and raise the backlight first or you will see nothing.

> `/dev/fb0` is shared with `fbcon`. If a console is active on the same device it will overwrite your output on the next redraw. Move the console away with `console=tty2`, or use Option 2.

For text and images, `pygame` on the `fbcon`/`directfb` driver or Pillow rendering into the same buffer both work. Remember to rotate 90°.

### Option 2: Weston + a Wayland client (what ZettOS does)

```bash
sudo apt install -y weston
sudo nano /etc/xdg/weston/weston.ini
```

```ini
[core]
backend=drm-backend.so
require-input=false
idle-time=0

[shell]
panel-position=none
startup-animation=none
locking=false
background-color=0xff000000

[output]
name=eDP-1
mode=172x640
transform=90
```

`transform=90` is the piece ZettOS handles inside its LVGL app — setting it in Weston means your client can draw in normal landscape orientation and Weston rotates it.

Run Weston on a spare VT, then launch any Wayland client against it:

```bash
WAYLAND_DISPLAY=wayland-1 XDG_RUNTIME_DIR=/run your-client
```

### Option 3: cage + a browser (most flexible)

For a dashboard — disk usage, temperatures, container status — render it as HTML and point a kiosk browser at it:

```bash
sudo apt install -y cage
cage -- chromium --kiosk --app=http://localhost:8080/lcd
```

This is the easiest route to "show whatever I want", since you design the panel in HTML/CSS and can rotate with a CSS transform.

### Option 4: LVGL

If you want the ZettOS look specifically, `zettos-lcd-display` is an LVGL app using the `lv_wayland` driver. LVGL is open source; the fonts it uses are D-DIN Pro. This is the most work and the least reason to bother unless you are targeting a very constrained redraw budget.

## Suggested systemd Service

```ini
[Unit]
Description=Front LCD display
After=multi-user.target

[Service]
Type=simple
ExecStartPre=/bin/sh -c 'echo 120000 > /sys/class/backlight/intel_backlight/brightness'
ExecStart=/usr/local/sbin/lcd-display.py
ExecStopPost=/bin/sh -c 'echo 0 > /sys/class/backlight/intel_backlight/brightness'
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

`ExecStopPost` blanks the panel when the service stops, so a crash leaves a dark screen rather than a frozen frame.

## RGB LEDs — Already Solved

The LED protocol is fully documented in [rgb-led-control.md](rgb-led-control.md). Verified against ZettOS on 2026-09-14:

- Device present: `Bus 003 Device 002: ID 5759:4358 ZettLab ZettOS_RGB`
- `cdc_acm` is loaded
- The mode names inside ZettOS' own binaries — `BREATHE_MODE`, `FLOW_MODE`, `FOUNTAIN_MODE`, `GRADIENT_MODE`, `FLICKER_MODE`, `LIGHT_MODE` — match the documented modes 0–6 exactly

Nothing further to reverse-engineer. Use `rgb_control.py` from this repo.

> ZettOS itself does not expose `/dev/ttyACM0`; its own services talk to the device without the tty layer. Under Ubuntu the `cdc_acm` driver claims it normally and the serial node appears, which is what `rgb_control.py` expects.

## Related Guides

- [rgb-led-control.md](rgb-led-control.md) — LED protocol and control script
- [kernel-parameters.md](kernel-parameters.md) — where `video=eDP-1:d` is set
- [ubuntu-installation.md](ubuntu-installation.md) — keep the parameter during install
