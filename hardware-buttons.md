# Chassis Buttons (COPY / RESET)

> Enables the physical chassis buttons on Zettlab D6/D8 Ultra under Ubuntu 26.04 and lets you bind custom actions (for example scripts or systemd units).

## Overview

Under stock firmware the **COPY** button starts a one-touch media copy flow. On a custom Ubuntu install those buttons are silent until a small kernel module exposes them as a normal Linux input device.

| Control | Location | Input event | Typical use on Ubuntu |
|---------|----------|-------------|------------------------|
| **COPY** | Front fascia (visible button) | `KEY_1` | Run any user command / script |
| **RESET** | Rear of chassis | `KEY_2` | Optional long-press action (default: none) |

This guide installs:

1. `zettlab_gpio_keys` — kernel module that creates input device `zettlab-gpio-keys`
2. `button-hook.py` — optional userspace helper that runs a shell command on press

## Prerequisites

- Ubuntu 26.04 Server on Zettlab D6 Ultra or D8 Ultra
- `sudo` access
- Build tools and kernel headers for the running kernel

```bash
sudo apt update
sudo apt install -y build-essential linux-headers-generic
```

## Hardware Notes

Buttons are read from the Intel Meteor Lake pinctrl GPIO community (`INTC1083`):

| Item | Value |
|------|--------|
| MMIO base | `0xE0D20000` (`/proc/iomem` lists `INTC1083:00`) |
| COPY pad | offset `0x6C0` → `KEY_1` (active-low) |
| RESET pad | offset `0x6B0` → `KEY_2` (active-low) |

Always match the input device **by name** (`zettlab-gpio-keys`). The `eventN` number can change across boots.

## Step 1: Build and Load the Kernel Module

Module sources live in `zettlab-gpio-keys/` next to this guide:

```bash
cd /path/to/zettlab-ubuntu/zettlab-gpio-keys

# Build against the running kernel
make

# Load
sudo insmod ./zettlab_gpio_keys.ko

# Confirm
lsmod | grep zettlab_gpio
grep -A6 'zettlab-gpio-keys' /proc/bus/input/devices
```

Expected fragment:

```
N: Name="zettlab-gpio-keys"
H: Handlers=kbd eventXX
B: KEY=c
```

`KEY=c` means both `KEY_1` and `KEY_2` are advertised.

### Optional: load on every boot

```bash
cd /path/to/zettlab-ubuntu/zettlab-gpio-keys
sudo mkdir -p /lib/modules/$(uname -r)/extra
sudo cp zettlab_gpio_keys.ko /lib/modules/$(uname -r)/extra/
sudo depmod -a
echo zettlab_gpio_keys | sudo tee /etc/modules-load.d/zettlab_gpio_keys.conf
```

After a kernel upgrade, rebuild in `zettlab-gpio-keys/` for the new headers (same pattern as other out-of-tree modules).

## Step 2: Verify Button Events

```bash
sudo apt install -y evtest
# List devices, pick the number for "zettlab-gpio-keys"
sudo evtest
```

Press the front **COPY** button — you should see `KEY_1` press/release.  
Press the rear **RESET** button — you should see `KEY_2`.

Find the event node by name:

```bash
for e in /sys/class/input/event*/device/name; do
  echo "$(basename $(dirname $(dirname "$e"))): $(cat "$e")"
done | grep zettlab
```

## Step 3: Bind Custom Actions

### Quick test

```bash
# Needs permission to open the event node (root, or user in group "input")
sudo python3 button-hook.py \
  --cmd 'logger -t zettlab-copy "COPY pressed"; date -Is >> /var/log/zettlab-copy.log'
```

Press COPY, then check:

```bash
tail /var/log/zettlab-copy.log
journalctl -t zettlab-copy -n 5
```

### Example: useful COPY actions

```bash
# Notify only
--cmd 'logger -t zettlab-copy "COPY pressed"'

# Run a backup / import script
--cmd '/usr/local/bin/import-usb.sh'

# Trigger a snapraid or rsync job (ensure the script is safe to re-enter)
--cmd 'systemctl start zettlab-copy-job.service'
```

### Optional RESET long-press

Only set this if you intentionally want a chassis long-press action. Leaving it unset does nothing destructive.

```bash
sudo python3 button-hook.py \
  --cmd '/usr/local/bin/import-usb.sh' \
  --reset-cmd 'logger -t zettlab-reset "RESET long-press"' \
  --reset-hold 5
```

## Step 4: Systemd Service (recommended)

Install the hook and a unit that starts after multi-user:

```bash
sudo install -m 755 button-hook.py /usr/local/sbin/button-hook.py
```

Create `/etc/systemd/system/zettlab-buttons.service`:

```ini
[Unit]
Description=Zettlab chassis button hook
After=multi-user.target
# If you load the module via modules-load.d, it is already present by this point.

[Service]
Type=simple
ExecStart=/usr/local/sbin/button-hook.py --cmd /usr/local/bin/my-copy-action.sh
Restart=always
RestartSec=2
# Optional: run as a dedicated user in group "input" instead of root
# User=henry
# Group=input

[Install]
WantedBy=multi-user.target
```

Enable:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now zettlab-buttons.service
systemctl status zettlab-buttons.service
```

Edit `ExecStart=` whenever you change the bound command, then `sudo systemctl restart zettlab-buttons.service`.

### Group access without running the hook as root

```bash
sudo usermod -aG input $USER
# re-login, then run the hook / service as that user
```

## Files in This Repository

| File | Purpose |
|------|---------|
| `zettlab-gpio-keys/zettlab_gpio_keys.c` | Kernel module source |
| `zettlab-gpio-keys/Makefile` | Out-of-tree module build |
| `button-hook.py` | Userspace command runner |
| `hardware-buttons.md` | This guide |

## Troubleshooting

| Symptom | Check |
|---------|--------|
| `insmod` fails with `Invalid module format` | Rebuild with headers matching `uname -r` |
| No `zettlab-gpio-keys` in `/proc/bus/input/devices` | Module not loaded: `lsmod \| grep zettlab_gpio` |
| `evtest` shows no KEY_1 on COPY | Confirm you are testing the `zettlab-gpio-keys` device, not a USB keyboard |
| Hook says device not found | Load the module first; match `--device-name zettlab-gpio-keys` |
| Permission denied opening `/dev/input/event*` | Use `sudo` or add user to group `input` |
| COPY works once then not again | Ensure your `--cmd` script exits; the hook only fires on press edges |

Unload module:

```bash
sudo rmmod zettlab_gpio_keys
```

## Safety Notes

- The module only generates input events. It does **not** wipe disks or factory-reset Ubuntu by itself.
- Any destructive action must be something **you** put in `--cmd` / `--reset-cmd`.
- Prefer short, idempotent scripts for COPY (users will press it more than once).

## Related Guides

- [Fan Control](hardware-fan-control.md) — out-of-tree module pattern / kernel updates
- [RGB/LED Control](rgb-led-control.md) — separate USB front lighting (`/dev/ttyACM0`)
- [Samba Shares](samba-shares.md) — common target for copy-import scripts
