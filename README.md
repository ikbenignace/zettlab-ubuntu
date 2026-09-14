# Zettlab D6/D8 Ultra – Ubuntu 26.04 Guide

> Community-driven documentation for running Ubuntu 26.04 on Zettlab D6/D8 Ultra NAS devices.

## Overview

This guide provides step-by-step instructions for installing and configuring Ubuntu 26.04 on Zettlab D6/D8 Ultra NAS devices. It covers everything from initial installation to advanced storage pool configuration and data protection.

**⚠ Disclaimer:** All information is provided as-is. Test thoroughly before applying to production systems. Always backup data before making system changes.

## Credits & Acknowledgements

- **Community Testing & Feedback**: Speedster and Daisan on the Zettlab Discord
- **Fan Control Kernel Module** (`zettlab-d8-fans`): Developed by [haveacry](https://github.com/haveacry) — [zettlab-d8-fans](https://github.com/haveacry/zettlab-d8-fans). The module is provided without an explicit license and is used here with attribution.

---

## Hardware Specifications

This table reflects the **Zettlab D6 Ultra** and **D8 Ultra** models (tested on Ubuntu 26.04).

| Component     | D6 Ultra                          | D8 Ultra                          | Notes |
|---------------|-----------------------------------|-----------------------------------|-------|
| **CPU**       | Intel Core Ultra 5 125H           | Intel Core Ultra 5 125H           | PL1/PL2 locked to 45 W / 93 W |
| **Bays**      | 6× 3.5" HDD bays                  | 8× 3.5" HDD bays                  | Hot-swappable |
| **Front LCD** | 3.49-inch, 640×172 (`eDP-1`)      | 3.49-inch, 640×172 (`eDP-1`)      | Disabled during boot with `video=eDP-1:d` |
| **Audio**     | Intel Meteor Lake iGPU DSP        | Intel Meteor Lake iGPU DSP        | Fixed with `snd_intel_dspcfg.dsp_driver=1` |
| **Fans**      | 3× PWM fans                       | 3× PWM fans                       | Controlled via `zettlab_d8_fans` DKMS module (0–183 range) |
| **Networking**| 2× 10GbE (Realtek RTL8127)        | 2× 10GbE (Realtek RTL8127)        | Onboard NIC unstable — using USB-C Ethernet adapter instead |
| **RGB/LED**   | USB-controlled                    | USB-controlled                    | See [RGB/LED Control](rgb-led-control.md) — protocol via `/dev/ttyACM0` |
| **Buttons**   | Front COPY + rear RESET           | Front COPY + rear RESET           | See [Chassis Buttons](hardware-buttons.md) — `zettlab_gpio_keys` |

---

## Table of Contents

| Guide | Description |
|-------|-------------|
| [Migrating from ZettOS](migrating-from-zettos.md) | ZettOS disk layout, reading your old data, choosing a stack |
| [Ubuntu Installation](ubuntu-installation.md) | Installing Ubuntu 26.04 Server |
| [Kernel Parameters](kernel-parameters.md) | Centralized list of all recommended kernel parameters |
| [Network Driver](networking-r8127.md) | Realtek r8127 status (now using USB Ethernet adapter) |
| [Fan Control](hardware-fan-control.md) | Dynamic temperature-based fan control |
| [Chassis Buttons](hardware-buttons.md) | COPY / RESET keys + custom action hook |
| [BIOS Graphics Configuration](graphics-BIOS.md) | Intel Arc iGPU BIOS settings for AI workloads |
| [Graphics Driver](graphics-iGPU.md) | Intel Arc iGPU compute/media stack |
| [LLM Inference](llm-inference.md) | llama.cpp on Arc iGPU (SYCL preferred, Vulkan fallback) |
| [Audio Configuration](audio-HDA-driver.md) | Fixing "Dummy Output" issue |
| [Storage Pool](storage-mergerfs-snapraid.md) | mergerfs + SnapRAID configuration |
| [Storage Pool (ZFS)](storage-zfs.md) | ZFS mirror alternative — continuous parity, checksums, snapshots |
| [Btrfs Data Replication](btrfs-data-replication.md) | Dedicated `/data` subvolume + btrbk snapshot replication to parity disk |
| [RGB/LED Control](rgb-led-control.md) | USB RGB controller protocol (`/dev/ttyACM0`, VID:0x5759 PID:0x4358) |
| [Front LCD Control](front-lcd-control.md) | Driving the 3.49" `eDP-1` panel: backlight, live stats, framebuffer |
| [Samba Shares](samba-shares.md) | Home + /data + mergerfs pool Samba shares |

---

## Prerequisites

- HDMI display and USB keyboard
- USB flash drive (≥ 8 GB)
- Ubuntu Server 26.04 ISO
- Recommended: Secondary NVMe SSD (preserves original ZettOS)

---

## Quick Reference

### Required Kernel Parameters

All kernel parameters are now documented in one place:

→ **[Kernel Parameters Reference](kernel-parameters.md)**

### Helper Scripts

| Script | Purpose |
|---|---|
| [`zettlab-tui.py`](zettlab-tui.py) | Curses control panel — fans, backlight, LEDs |
| [`lcd-stats`](lcd-stats) | Live system stats on the front panel (systemd service) |
| [`lcd-power`](lcd-power) | Front panel backlight: `on` / `off` / `toggle` / `full` / `0-100` |
| [`lcd-grab`](lcd-grab) | Screenshot the front panel to a PNG |
| [`rgb_control.py`](rgb_control.py) | RGB LED control (reference protocol implementation) |
| [`rgb-raw.py`](rgb-raw.py) | RGB frame sender with an explicit speed byte — for protocol testing |
| [`cpu-fan-curve.sh`](cpu-fan-curve.sh) · [`hdd-fan-curve.sh`](hdd-fan-curve.sh) | Temperature-driven fan curves |

### Terminal Control Panel

`zettlab-tui.py` is a curses interface for the three chassis fans, the front LCD
backlight, and the RGB LEDs:

```bash
sudo ./zettlab-tui.py
```

Stdlib only, no dependencies. It auto-detects either fan driver — `zettlab_d8_fans`
(the DKMS module) or `zettos_pwm_fan` (stock ZettOS) — and adapts to their different
sysfs attribute names. Any section whose hardware is absent is simply skipped.

| Key | Action |
|-----|--------|
| `j`/`k` or arrows | move between controls |
| `h`/`l`, `-`/`+` | adjust by 1 |
| `J`/`L` | adjust by 10 |
| `1`/`2`/`3` | jump to a fan |
| `b` / `m` / `c` | jump to backlight / LED mode / LED colour |
| `0` | display and LEDs off (fans untouched) |
| `q` | quit |

> Manual PWM writes are overridden by `cpu-fan-curve.service` and
> `hdd-fan-curve.service` within seconds. The TUI warns when they are active; stop
> them first if you want manual control to stick.

### DKMS Modules

| Module | Purpose |
|--------|---------|
| `zettlab-d8-fans` | Fan control |
| `zettlab-gpio-keys` | Chassis COPY / RESET buttons (input device) |

> **Note:** Both DKMS modules auto-rebuild when a new kernel is installed via `apt upgrade`. If you ever see the services fail after a kernel update, run `sudo dkms build -m <module> -v <version>` and `sudo modprobe <module_name>` to load them.