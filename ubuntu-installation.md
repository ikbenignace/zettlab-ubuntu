# Installing Ubuntu 26.04 Server on Zettlab D6/D8 Ultra

> Install Ubuntu 26.04 Server on Zettlab D6/D8 Ultra NAS while preserving the original ZettOS installation as a fallback.

## Overview

**Reference:** [Zettlab Third-party System Flashing Guide](https://wiki.zettlab.com/guide/FAQ/Third-party%20system%20flashing.html)

**Hardware Note:** The Zettlab D6/D8 Ultra has an internal M.2 NVMe system drive plus support for additional M.2 slots.

## Prerequisites

- HDMI display and USB keyboard (required for BIOS and first boot)
- USB flash drive ≥ 8 GB
- Ubuntu Server 26.04 ISO
- Recommended: Secondary NVMe SSD for Ubuntu installation

## BIOS Configuration

### Step 1: Enter BIOS Setup

Power on and press **F2** to enter BIOS setup.

### Step 2: Configure BIOS Settings

1. **Disable Watchdog Timer (WDT)** — prevents random reboots during installation
2. Disable **Secure Boot**
3. Enable **HDD power on sequence** (or "HDD power up")
4. Save settings and exit (F10)

## Installation Procedure

### Step 1: Create Bootable USB

Download Ubuntu Server 26.04 ISO and write to USB using Rufus, balenaEtcher, or similar tool.

### Step 2: Boot Installer with Front LCD Fix

1. Insert USB drive and power on
2. Press **F12** to open boot menu; select USB drive
3. At GRUB menu, highlight Ubuntu Server entry and press **E**
4. Add the `video=eDP-1:d` parameter at the end of the `linux` line

See the full list of recommended kernel parameters in **[Kernel Parameters Reference](kernel-parameters.md)**.

### Step 3: Install Ubuntu (Btrfs recommended)

**Strongly recommended:** Format the root filesystem (`/`) as **Btrfs**. This enables native subvolumes, snapshots, and incremental replication.

During the installer:
- Select **Guided storage layout**
- Choose **Btrfs** as the filesystem for the root partition
- Install Ubuntu on a different drive than the original ZettOS NVMe (recommended: secondary NVMe SSD)
- Create user account
- Complete installation and reboot

Remove USB drive after reboot.

### Step 4: Initial System Configuration

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install lm-sensors smartmontools curl git btrbk -y
```

### Recommended: Btrfs Data Protection

After installation, create a dedicated `/data` subvolume and set up automatic snapshot replication to your Btrfs parity disk.

→ Follow the **[Btrfs Data Replication Guide](btrfs-data-replication.md)** for full instructions.

## Installer Gotchas Hit in Practice

Recorded from a real install on 2026-09-14 (BIOS `WY120V016`, Ubuntu 26.04.1).

### The storage probe fails if the stock array is still assembled

subiquity reports *"Sorry, there was a problem examining the storage devices on this
system"* when the vendor's `mdraid → bcache → btrfs` stack is live. Choose **Switch to
a shell** and deactivate it — this is non-destructive, the superblocks stay put:

```bash
mdadm --stop --scan
lsblk                      # sdX should show only its own partitions now
```

Then `exit` back (twice if you ran `sudo -i`) and press **Continue**. If the error
screen only offers Continue, that is enough — the re-probe happens on the way through.

### Device letters move between environments

The same disks enumerate differently under the vendor OS, the installer, and the
installed system. On one machine, across three boots:

| Disk | Vendor OS | Installer | Installed Ubuntu |
|---|---|---|---|
| 1 TB HDD | `sdb` | `sda` | `sda` |
| 12 TB HDD | `sda` | `sdc` | `sdb` |
| 12 TB HDD | `sdc` | `sdd` | `sdc` |
| Ubuntu NVMe | — | `nvme0n1` | `nvme1n1` |
| Vendor NVMe | `nvme1n1` | `nvme1n1` | `nvme0n1` |

**Identify the install target by size and model, never by letter.** Confirm on the
`Storage configuration` summary that only the intended disk appears under `USED
DEVICES` before typing `Continue`.

### Tick "Install OpenSSH server"

Easy to skip, and painful afterwards — the front panel mirrors the console at 640×172,
which is readable but miserable to type into. If you did miss it, you can still recover
over IPv6: `sudo apt install -y openssh-server`.

### The installer may not configure IPv4

If the network step is passed while the link is still negotiating, subiquity can write a
config with only `accept-ra: true` and no `dhcp4`. The result is an IPv6-only host that
never appears in an IPv4 scan. Check `/etc/netplan/00-installer-config.yaml` and add
either `dhcp4: true` or a static block.

### BIOS: `Igfx Gsm2 = 4GB` can halt the firmware

On BIOS `WY120V016` — where the stock value is `0` — raising `Igfx Gsm2` to 4 GB
produced `ASSERT_EFI_ERROR` at power-on and the machine would not boot. Returning it to
`0` cleared it. See [graphics-BIOS.md](graphics-BIOS.md); this costs nothing for LLM
work, because Meteor Lake is UMA and the driver allocates from system RAM regardless.

### Disabling Secure Boot may trigger a vendor lock screen

Turning Secure Boot off produced a red *"Enable Secure Boot is required"* screen. It
comes from the **vendor bootloader on the untouched system NVMe**, not from the
firmware: Ubuntu itself installs and boots fine with Secure Boot disabled, and
`mokutil --sb-state` on the installed system reports `SecureBoot disabled`. If you meet
that screen, it is the vendor OS complaining, not a block on your install.

## Known Hardware Support in Ubuntu 26.04

| Component   | Status                                      |
|-------------|---------------------------------------------|
| Fans        | Fully supported via `zettlab_d8_fans` DKMS module |
| Front LCD   | Connected as `eDP-1`; disabled during live boot with kernel parameter. Enabling causes HDMI resolution to be forced to 640x172 — issue unresolved. |
| Networking  | Realtek RTL8127 — use USB-C Ethernet adapter (onboard NIC unstable) |
| CPU         | Intel Core Ultra 5 125H. PL2 93 W; PL1 reads 200 W under Ubuntu against 45 W under the stock firmware — sustained loads run hotter and faster here |