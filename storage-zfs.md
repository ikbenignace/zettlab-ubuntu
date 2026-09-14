# Storage Pool Setup: ZFS Mirror

> Builds a ZFS mirror on Zettlab D6/D8 Ultra as an alternative to [mergerfs + SnapRAID](storage-mergerfs-snapraid.md). Tuned for a mixed workload: media files, Docker application data, databases, and Time Machine.

> **⚠ Not yet hardware-verified.** The commands are standard ZFS and hardware-independent, but this exact setup has not been run on a D6/D8 Ultra. The mergerfs guide is the tested path.

## Why ZFS Instead of mergerfs + SnapRAID

| | mergerfs + SnapRAID | ZFS mirror |
|---|---|---|
| Parity | current as of last sync | continuous |
| Corruption detection | none | end-to-end checksums |
| Corruption repair | none | automatic from the mirror |
| Snapshots | via btrbk on Btrfs | native, near-free |
| Mismatched disk sizes | excellent | poor — mirrors want equal pairs |
| Disk spin-down | per disk | whole vdev spins up |
| Known issues | SMB rename failures, long-term segfaults (see that guide's own notes) | RAM appetite, needs `zfs_arc_max` tuning |

**Choose mergerfs + SnapRAID** for a bulk media archive on mismatched disks that rarely changes.

**Choose ZFS** when you run databases or containers on the pool. SnapRAID's sync-window and mergerfs' FUSE layer are both poor fits for live application state.

### Why not Btrfs RAID1

Btrfs RAID1 is a legitimate alternative and handles mismatched disk sizes far better. It loses on one specific point: Btrfs fragments badly on database files, and the standard fix (`chattr +C` / `nodatacow`) **also disables checksums** on those files. ZFS solves the same problem with `recordsize=16K` while keeping checksums. If you run no databases, reconsider Btrfs RAID1.

Btrfs RAID5/6 remains unsafe for production. Do not use it for parity.

## Prerequisites

- Ubuntu 26.04 Server installed, per [ubuntu-installation.md](ubuntu-installation.md)
- Fan control running — [hardware-fan-control.md](hardware-fan-control.md). Pool creation and the first bulk copy work every disk hard.
- Two disks of the same size for the mirror
- **Complete, verified backup of anything currently on those disks**

```bash
sudo apt update
sudo apt install -y zfsutils-linux
zfs version
```

Ubuntu ships ZFS in `main` — no DKMS, no third-party repo. This is one area where Ubuntu is materially easier than Debian.

## Step 1: Identify Disks by ID

> Device letters (`/dev/sda`) shift between boots. Always build ZFS pools from `/dev/disk/by-id/` paths, which are tied to the drive's serial number. Using `/dev/sdX` will eventually confuse the pool about which disk is which.

```bash
lsblk -o NAME,SIZE,MODEL,SERIAL
ls -l /dev/disk/by-id/ | grep -v part
```

Note the two `by-id` paths for your mirror pair. Prefer the `ata-` or `nvme-` names over `wwn-` — they are readable.

## Step 2: Wipe the Disks

> **This destroys everything on the listed disks and cannot be undone.** Run `lsblk` one final time and confirm you are naming the correct devices. Do not paste these commands from a scrollback buffer — the device names may have changed since you wrote them down.

```bash
DISK1=/dev/disk/by-id/ata-YOUR_DISK_1
DISK2=/dev/disk/by-id/ata-YOUR_DISK_2

# Confirm what you are about to erase
lsblk "$(readlink -f $DISK1)" "$(readlink -f $DISK2)"
```

If the disks were part of an old mdraid array, stop it first or ZFS will refuse:

```bash
sudo mdadm --stop /dev/md0 /dev/md1 2>/dev/null
sudo mdadm --zero-superblock "$(readlink -f $DISK1)"2 2>/dev/null
sudo wipefs -af "$(readlink -f $DISK1)" "$(readlink -f $DISK2)"
```

## Step 3: Create the Pool

```bash
sudo zpool create -f \
  -o ashift=12 \
  -O compression=lz4 \
  -O atime=off \
  -O relatime=on \
  -O xattr=sa \
  -O acltype=posixacl \
  -O dnodesize=auto \
  -O normalization=formD \
  -O mountpoint=/tank \
  tank mirror "$DISK1" "$DISK2"
```

What each option buys you:

| Option | Why |
|---|---|
| `ashift=12` | 4 KiB sectors. Wrong at creation time is permanent — you cannot change it later. |
| `compression=lz4` | Nearly free, often a net speed gain. Use `zstd` if you want more ratio on text-heavy data. |
| `atime=off` | Stops a metadata write on every read. |
| `xattr=sa`, `acltype=posixacl` | **Required for Samba** to store ACLs and macOS metadata properly. |
| `normalization=formD` | Unicode filename normalisation — prevents macOS/SMB filename mismatches. Implies `utf8only=on`. |

Verify:

```bash
zpool status tank
zfs list
```

## Step 4: Datasets

Datasets are free. Make one per workload so you can tune, snapshot, and quota them independently.

```bash
# Bulk media — large sequential files
sudo zfs create -o recordsize=1M tank/media

# Databases and container state — small random I/O
sudo zfs create -o recordsize=16K tank/appdata

# General SMB shares
sudo zfs create tank/shares

# Time Machine target, capped so it cannot eat the pool
sudo zfs create -o quota=2T tank/timemachine
```

### Why recordsize matters

ZFS reads and writes a whole record at a time. A 16 KiB Postgres page write against a 128 KiB record causes a read-modify-write of the full record — write amplification of 8×. Matching `recordsize` to the workload removes that.

| Workload | recordsize |
|---|---|
| Photos, video, backups, ISOs | `1M` |
| Postgres, MariaDB, SQLite | `16K` |
| General mixed files | `128K` (default) |

`recordsize` applies to **newly written** files. Set it before you copy data in.

### Leave Docker's own storage off the pool

Keep `/var/lib/docker` on the NVMe root filesystem. Images and layers are disposable, benefit from NVMe speed, and running Docker's storage driver on ZFS adds complexity for no gain.

Put only **persistent data** on the pool, via bind mounts:

```yaml
services:
  postgres:
    volumes:
      - /tank/appdata/postgres:/var/lib/postgresql/data
```

## Step 5: Cap the ARC

ZFS' cache will otherwise expand into most of your free RAM. It releases memory under pressure, but on a box also running containers and LLM inference you want a hard ceiling so a model load never races the cache.

```bash
# 8 GiB — adjust to roughly a quarter of installed RAM
echo "options zfs zfs_arc_max=8589934592" | sudo tee /etc/modprobe.d/zfs.conf
sudo update-initramfs -u
sudo reboot
```

Verify after reboot:

```bash
awk '/^c_max/ {print $1, $3/1024/1024/1024 " GiB"}' /proc/spl/kstat/zfs/arcstats
```

> **ECC RAM is not a ZFS requirement.** ZFS is no more sensitive to bad memory than ext4 or Btrfs, and the "scrub of death" story is a myth. ECC is good practice on any always-on server, not a precondition for using ZFS.

## Step 6: Ownership

```bash
sudo chown -R $USER:$USER /tank/media /tank/shares /tank/appdata
sudo chmod 770 /tank/timemachine
```

## Step 7: Snapshots

Snapshots are not backups — they live on the same disks — but they cover the common cases: deleted the wrong folder, ransomware, bad container upgrade.

```bash
sudo apt install -y sanoid
sudo nano /etc/sanoid/sanoid.conf
```

```ini
[tank/media]
        use_template = production
        recursive = yes

[tank/appdata]
        use_template = production
        recursive = yes

[tank/shares]
        use_template = production
        recursive = yes

[template_production]
        frequently = 0
        hourly = 24
        daily = 14
        monthly = 6
        yearly = 0
        autosnap = yes
        autoprune = yes
```

`tank/timemachine` is deliberately excluded — Time Machine manages its own history and snapshotting it doubles the space for nothing.

```bash
sudo systemctl enable --now sanoid.timer
systemctl list-timers | grep sanoid
```

Browse and restore:

```bash
zfs list -t snapshot
ls /tank/media/.zfs/snapshot/                    # snapshots are browsable directly
zfs rollback tank/media@autosnap_2026-09-14_00:00:00   # destroys newer changes
```

> `zfs rollback` discards every change made after that snapshot, irreversibly. To recover a single file, copy it out of `.zfs/snapshot/` instead.

## Step 8: Scrubs and Alerting

Ubuntu's `zfsutils-linux` installs a monthly scrub timer. Confirm it:

```bash
systemctl list-timers | grep zfs
sudo zpool scrub tank      # run one now
zpool status tank
```

Enable email on pool faults:

```bash
sudo nano /etc/zfs/zed.d/zed.rc
```

```conf
ZED_EMAIL_ADDR="you@example.com"
ZED_NOTIFY_VERBOSE=0
```

```bash
sudo systemctl restart zfs-zed
```

A degraded mirror you do not notice is the same as no mirror at all. Set this up.

## Step 9: Samba, Including Time Machine

Follow [samba-shares.md](samba-shares.md) for the global section, then use these share definitions:

```conf
[media]
   path = /tank/media
   browseable = yes
   read only = no
   valid users = %U
   oplocks = no
   level2 oplocks = no

[timemachine]
   path = /tank/timemachine
   browseable = yes
   read only = no
   valid users = %U
   vfs objects = catia fruit streams_xattr
   fruit:time machine = yes
   fruit:time machine max size = 2T
   fruit:metadata = stream
   fruit:posix_rename = yes
   fruit:veto_appledouble = no
```

```bash
sudo testparm -s
sudo systemctl restart smbd
```

The `fruit:time machine max size` value should match the dataset quota from Step 4, so macOS sees the real ceiling instead of filling the pool and failing late.

## Expansion

**Add capacity** — attach a second mirror vdev. Capacity adds up; both vdevs stripe:

```bash
sudo zpool add tank mirror /dev/disk/by-id/ata-DISK3 /dev/disk/by-id/ata-DISK4
```

**Replace with larger disks** — swap one at a time, resilvering between:

```bash
sudo zpool set autoexpand=on tank
sudo zpool replace tank /dev/disk/by-id/ata-OLD /dev/disk/by-id/ata-NEW
zpool status tank      # wait for resilver to finish before the second disk
```

The pool grows once both disks are replaced.

> Add vdevs in matching pairs. A single-disk vdev added to a mirrored pool has no redundancy, and the pool as a whole then dies with that one disk. ZFS will warn you; do not override it with `-f`.

## Troubleshooting

| Symptom | Check |
|---|---|
| `zpool create` fails: device busy | Old mdraid still assembled — `sudo mdadm --stop /dev/mdN`, then `--zero-superblock` |
| Pool does not import at boot | `sudo systemctl enable zfs-import-cache zfs-mount zfs.target` |
| `DEGRADED` in `zpool status` | Identify the disk via the `by-id` name shown, then `zpool replace` |
| Samba permission errors | Pool missing `acltype=posixacl` — set with `zfs set acltype=posixacl tank` |
| Time Machine fails partway | Quota reached, or `fruit:time machine max size` disagrees with the dataset quota |
| Memory pressure / swapping | `zfs_arc_max` unset or too high — see Step 5 |
| Slow database performance | Wrong `recordsize`; it only applies to newly written data, so recreate the dataset and copy back |

## Related Guides

- [storage-mergerfs-snapraid.md](storage-mergerfs-snapraid.md) — the tested alternative
- [samba-shares.md](samba-shares.md) — global Samba config
- [hardware-fan-control.md](hardware-fan-control.md) — set this up before the first bulk copy
- [llm-inference.md](llm-inference.md) — competes with ARC for RAM
