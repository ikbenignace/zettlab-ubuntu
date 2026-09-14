# Migrating from ZettOS

> What the stock ZettOS installation looks like on disk, how to read your existing data from a new Linux install, and which decisions to make before wiping anything.

> Observations here were read from a running ZettOS install on a D6 Ultra (September 2026). Layouts may differ between firmware versions — verify against your own machine rather than trusting these paths blindly.

## Why This Page Exists

The other guides in this repo assume you already decided to wipe the machine. This one covers the step before that: understanding what ZettOS built, confirming your data is safe, and choosing a storage stack.

The single most important point: **installing Linux on a second NVMe does not touch your hard drives.** You can install, boot, verify, and only then decide about the array. Treat those as two separate phases.

## The ZettOS Disk Layout

### System drive (internal M.2 NVMe)

ZettOS uses an A/B slot scheme with encrypted, read-only root filesystems:

| Partition | Label | Contents |
|---|---|---|
| p1 | `zett_boot` | EFI system partition |
| p2 | `misc` | — |
| p3 | `zettroot_a` | LUKS → squashfs root, slot A |
| p4 | `zettroot_b` | LUKS → squashfs root, slot B |
| p5 | `zett_driver` | ext4, mounted at `/usr/lib/modules` |
| p6 | `zett_swap` | swap |
| p7 | `zett_data_enc` | LUKS → Btrfs, mounted at `/zettos/raid` |
| p8 | `zett_overlay_enc` | LUKS → ext4, the writable overlay for `/` |

The root is squashfs with a writable overlay on top, which is why `/` shows as `overlay` in `df`. Kernel command line carries `zettos.kernel_slot=a` and `zettos.rootfs_slot=a`.

> **Leave this drive alone.** It is your rollback. Install Linux to a *different* NVMe and keep ZettOS reachable from the boot menu.

### Data drives

ZettOS stacks four layers on each pool:

```
sdX1  (~30 GB, no filesystem — reserve)
sdX2  ──→ mdraid (RAID1) ──→ bcache ──→ btrfs ──→ /zettos/pool/N
```

Confirm your own layout before touching anything:

```bash
lsblk -o NAME,SIZE,FSTYPE,LABEL,MOUNTPOINT
sudo blkid
cat /proc/mdstat
sudo btrfs filesystem show
```

A single-disk pool still appears as a RAID1 array with one member — `[1/1] [U]`. That means **no redundancy**, despite the RAID label. Check `/proc/mdstat` rather than assuming.

### Where your files actually live

Shares surface under `/zettos/raid/non-snapshot/fileservice/share/Teams/<name>`, which are bind mounts back onto the pools. The real paths are:

```
/zettos/pool/N/teams/<share>/DATA/<share>/...
```

`du -sh` on `/zettos/pool/*/teams/*` gives you the honest breakdown.

## Stock Firmware Reference

Read from a running D6 Ultra, useful when comparing against your own machine:

| | Value |
|---|---|
| Board / BIOS | `WY120` / `WY120V016` (2026-05-09) |
| DMI product | `D6 Ultra`, family `ZettOS`, vendor `Zettlab` |
| Stock kernel | `6.12.48-zettos` |
| RAPL limits | PL1 45 W long term, PL2 93 W short term |
| Fan driver | `zettos_pwm_fan`, a platform driver **built into the stock kernel** |

### Fan interface differences

The stock driver and the community [`zettlab-d8-fans`](hardware-fan-control.md) DKMS
module expose the same three fans under different names:

| | Stock `zettos_pwm_fan` | Community `zettlab_d8_fans` |
|---|---|---|
| PWM | `fan1_pwm` … `fan3_pwm` | `pwm1` … `pwm3` |
| PWM mode | — | `pwm1_enable` … `pwm3_enable` |
| Tachometer | `fan1_input` … `fan3_input` | `fan1_input` … `fan3_input` |
| Labels | — | `fan1_label` … `fan3_label` |

Both drivers report real RPM. Only the PWM attribute names differ, so a tool that
reads tachometers works against either — `zettlab-tui.py` in this repo handles both
spellings.

The community module additionally exposes `fanN_label`, which confirms the mapping
directly on a running system: `fan1` and `fan2` are `Disks 1` / `Disks 2`, `fan3` is
`CPU`.

Neither fan curve script in this repo reads `fanN_input`. They set PWM open-loop and
never check whether the fan actually spun up, so a stalled or disconnected fan goes
unnoticed. The tachometer data is there if you want to add that check.

### Extra temperature sensors

Beyond CPU package and drive temperatures, the platform exposes DDR5 DIMM sensors
through `spd5118` (one hwmon device per module) and Intel DPTF zones (`TCPU`,
`TCPU_PCI`, `x86_pkg_temp`). The fan curve scripts in this repo use none of these —
they are available if you want a more informed curve.

### ACPI tables are firmware-provided

`/sys/firmware/acpi/tables/` including the DSDT comes from firmware, not the OS, so it
reads identically under any distribution. There is no need to extract it before
reinstalling.

## Reading ZettOS Data from Ubuntu

All three layers are in-tree. After installing Ubuntu on a separate drive:

```bash
sudo apt install -y mdadm bcache-tools btrfs-progs
sudo mdadm --assemble --scan
lsblk                       # look for /dev/bcacheN
sudo mkdir -p /mnt/old
sudo mount -o ro /dev/bcache1 /mnt/old
```

> Mount **read-only** (`-o ro`). As long as you only read, the ZettOS pool stays intact and you can retry as often as you like. `bcache-tools` is required — you cannot mount the mdraid device as Btrfs directly, because bcache keeps an 8 KiB superblock ahead of the filesystem.

This is usually faster than copying over the network from a running ZettOS, since it reads at SATA speed locally.

## Before You Wipe: Verify, Do Not Assume

Three traps worth checking explicitly.

### 1. Is your "backup" actually a backup?

Network mounts look exactly like local directories. Check what a path really is:

```bash
findmnt /path/to/your/backup
```

If the source is `nas-ip:/some/export` over NFS or SMB, that folder is a **live window onto the NAS**, not a copy. Wiping the NAS destroys it. This is an easy mistake to make when a backup directory and a mount point sit next to each other.

### 2. Where does an application actually store its data?

For anything running in Docker, ask the container rather than guessing:

```bash
docker inspect -f '{{range .Mounts}}{{.Source}} -> {{.Destination}}{{println}}{{end}}' <container>
```

An app can have a NAS mount available and still write everything locally, or the reverse. The mount list is authoritative.

### 3. Verify a copy by file count, not just size

Sizes matching is weak evidence. Compare counts per subdirectory on both sides:

```bash
find /source/dir -type f | wc -l
find /backup/dir -type f | wc -l
```

For git repositories, confirm nothing exists only locally before discarding a machine:

```bash
for d in ~/repos/*/; do
  [ -d "$d/.git" ] || { echo "NO GIT: $d"; continue; }
  a=$(git -C "$d" rev-list --count HEAD --not --remotes 2>/dev/null)
  [ "${a:-0}" -gt 0 ] && echo "$d: $a commits not on any remote"
done
```

Directories without `.git` and commits missing from every remote are the only things that truly exist nowhere else.

### Time Machine sparsebundles

A `*.sparsebundle` under a share is a macOS Time Machine backup and is often the largest single item on the array. It is a copy of another machine: discarding it costs you backup *history*, not data — provided the source Mac is healthy. Verify that first, then treat the space as reclaimable. This one decision frequently removes the need to buy transfer hardware.

## Choosing a Distribution

| | Verdict |
|---|---|
| **Ubuntu 26.04** | Recommended. The Intel compute stack (`intel-opencl-icd`, `libze-intel-gpu1`, `intel-ocloc`) is in apt, and ZFS ships in `main`. |
| **Debian 13** | Workable, but more manual. `intel-opencl-icd`, `libze-intel-gpu1` and `intel-ocloc` were dropped from trixie and must be installed from Intel's GitHub `.deb` releases. The chassis button module also needs a compat shim, because Debian's 6.12 kernel predates the `timer_container_of()` rename in 6.16. |
| **TrueNAS SCALE** | Strong NAS features, but it owns its own Docker daemon, and this hardware requires a USB Ethernet adapter — a combination TrueNAS handles poorly. Reasonable if you want an appliance and run nothing custom. |

## Choosing a Storage Layout

| Layout | Choose when |
|---|---|
| [mergerfs + SnapRAID](storage-mergerfs-snapraid.md) | Bulk media archive, mismatched disk sizes, data that rarely changes. Parity is only current as of the last sync. |
| [ZFS mirror](storage-zfs.md) | You run databases or containers on the pool. Continuous parity, checksums, self-healing. Wants equal-size pairs. |
| Btrfs RAID1 | Mismatched disk sizes *and* no databases. Handles odd capacities far better than ZFS, but needs `nodatacow` for database files, which disables checksums on exactly those files. Avoid Btrfs RAID5/6 entirely. |

Two persistent myths worth dismissing if they are steering your decision:

- **"ZFS requires ECC RAM."** It does not. ZFS is no more vulnerable to bad memory than ext4 or Btrfs, and the "scrub of death" story is not real. ECC is good practice on any always-on server, not a ZFS prerequisite.
- **"ZFS pool layout is locked in forever."** Outdated. RAIDZ expansion shipped in OpenZFS 2.3 (2025), and mirrors were always easy to grow — add a vdev, or replace both disks and set `autoexpand=on`.

## Suggested Order of Operations

**Phase A — reversible, leaves the array untouched**

1. Verify your backups using the checks above
2. BIOS setup — see [ubuntu-installation.md](ubuntu-installation.md)
3. Install Ubuntu to a **second** NVMe; confirm the ESP lands on that drive, not on the ZettOS system disk
4. Set boot order, confirm ZettOS is still reachable from the boot menu
5. [Kernel parameters](kernel-parameters.md)
6. [Fan control](hardware-fan-control.md) — **early**; fans are unmanaged until those services run
7. Networking, [iGPU](graphics-iGPU.md)
8. Mount the old pool read-only and confirm your data is readable from the new system

**Phase B — destructive, only once Phase A is proven**

9. Build the new array — [ZFS](storage-zfs.md) or [mergerfs + SnapRAID](storage-mergerfs-snapraid.md)
10. Restore data
11. [Samba](samba-shares.md), snapshots, services

## Related Guides

- [ubuntu-installation.md](ubuntu-installation.md)
- [storage-zfs.md](storage-zfs.md) · [storage-mergerfs-snapraid.md](storage-mergerfs-snapraid.md)
- [front-lcd-control.md](front-lcd-control.md) — how ZettOS drives the front panel
- [hardware-fan-control.md](hardware-fan-control.md)
