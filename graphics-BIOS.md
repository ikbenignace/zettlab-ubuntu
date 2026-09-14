# BIOS Graphics Configuration

> Optimizes the onboard Intel Arc iGPU (Meteor Lake) for local AI workloads on Zettlab D6/D8 Ultra while maintaining NAS stability.

## Overview

This guide configures the BIOS to prioritize the internal Arc iGPU for compute workloads (LLM inference, etc.). The key change is allocating dedicated graphics memory (`Igfx Gsm2`) without starving system RAM.

## Prerequisites

- Zettlab D6 Ultra or D8 Ultra
- HDMI display and USB keyboard connected
- Access to BIOS (device powered off or rebooting)

## Procedure

### Step 1: Enter BIOS

1. Power on (or reboot) the device.
2. Repeatedly press **F2** during boot to enter BIOS Setup.
3. Navigate to the **Advanced** tab → **Graphics Configuration**.

### Step 2: Apply Recommended Settings

Change only the following options:

| Setting                                      | Value     | Notes |
|----------------------------------------------|-----------|-------|
| **Skip Scanning of External Gfx Card**       | Enabled   | Skip unnecessary PCIe scan |
| **Primary Display**                          | IGFX      | Force internal Arc iGPU as primary |
| **Internal Graphics**                        | Enabled   | Ensure iGPU stays active |
| **Igfx Gsm2**                                | **4GB**   | Dedicated graphics memory. **Verify your firmware accepts it** — see the warning below |
| **GT RC1p Support**                          | Disabled  | Reduces throttling during long inference |
| **Media RC1p Support**                       | Disabled  | Same reason as above |

> **`Igfx Gsm2 = 4GB` halts some firmware.** On BIOS `WY120V016`, where the stock value
> is `0`, setting 4 GB produced `ASSERT_EFI_ERROR` at power-on and the machine would not
> boot. Recovery: hold the power button, re-enter setup, set it back to `0`.
>
> If that happens to you, step up gradually — `512M`, `1GB`, `2GB` — to find your
> ceiling, and do it *after* the OS is installed so a bad value never blocks your
> install.
>
> Leaving it at `0` costs very little. Meteor Lake is UMA: the iGPU and CPU share system
> memory and the driver allocates dynamically. `Igfx Gsm2` only reserves a *guaranteed*
> block up front. See the memory-planning section of [llm-inference.md](llm-inference.md).

### Step 3: Leave Unchanged

- **DVMT Pre-Allocated** — Already at maximum (128M)
- All other settings (VDD Enable, Configure GT/Media, PAVP, etc.) — Leave at defaults

### Step 4: Save and Exit

1. Press **F10**.
2. Select **Yes** to save changes.
3. The system will reboot.

## Verification

After reboot, confirm the iGPU is active:

```bash
# Check iGPU detection
lspci | grep -i "VGA\|Display"
```

For real-time monitoring during AI workloads:

```bash
sudo apt install -y intel-gpu-tools
sudo intel_gpu_top
```

## Notes

- The `Igfx Gsm2 = 4GB` setting provides a good balance between dedicated iGPU memory and available system RAM for 30B–70B class models.
- This configuration is hardware-level and should be done before or alongside OS installation.
- See the companion guide for driver installation: [Intel Arc iGPU Driver Installation](graphics-iGPU.md)