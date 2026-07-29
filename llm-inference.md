# LLM Inference on Intel Arc iGPU (SYCL & Vulkan)

> Local LLMs with llama.cpp on Zettlab D6/D8 Ultra (Meteor Lake Arc iGPU, Core Ultra 5 125H).

## Overview

D6/D8 Ultra uses **unified memory (UMA)**: the Arc iGPU and CPU share system RAM. There is no separate discrete VRAM pool like a desktop GPU. How large a model you can run depends mainly on **installed RAM**, BIOS graphics memory (`Igfx Gsm2`), and quant size — not on a fixed “GPU VRAM” number.

| Backend | When to use |
|---------|-------------|
| **SYCL** (Level Zero) | **Preferred** on Intel Arc — oneAPI path, F16 + oneDNN, usually best performance |
| **Vulkan** (Mesa ANV) | Easier dependency-wise; good fallback or A/B compare |

Both backends need:

- BIOS settings in `graphics-BIOS.md` (especially **Igfx Gsm2 = 4GB** and RC1p disabled)
- Driver stack in `graphics-iGPU.md`

## Memory planning (pick models for *your* RAM)

Rough budget for a single llama.cpp server with full offload (`-ngl 99`):

```text
need ≈ model_weights + KV_cache + OS/services + headroom
```

| System RAM (typical) | Comfortable class (examples) | Tight / avoid |
|----------------------|------------------------------|---------------|
| ~16–24 GiB | 7B–14B dense Q4; small MoE | 30B+ dense Q4 |
| ~32 GiB | 14B–32B Q4; light 30B-class MoE Q4 at modest context | Large Q5/Q6 + long context |
| ~64 GiB+ | 30B–35B MoE Q4 (e.g. Qwen3.x-35B-A3B), longer context | Huge dense 70B Q4 may still struggle |
| Higher | Larger quants / longer context / multi-slot | — |

Always leave headroom for the OS, Samba, SnapRAID/mergerfs, browsers, etc. Check before loading:

```bash
free -h
./build/bin/llama-server --list-devices   # UMA budget reported per backend
```

If swap starts climbing under load, use a smaller quant, lower `-c` / `-np`, or fewer GPU layers.

## Prerequisites

- BIOS configured per `graphics-BIOS.md` (4GB `Igfx Gsm2` recommended)
- Ubuntu 26.04 with iGPU compute stack (`graphics-iGPU.md`)
- User in the `render` group
- DRM nodes present (`ls /dev/dri/` — expect `card*` and `renderD128`; card index varies)
- Intel oneAPI Base Toolkit installed (default path `/opt/intel/oneapi`) for SYCL
- Level Zero **headers** when building llama.cpp with SYCL:

```bash
sudo apt install -y libze-dev
```

## Option 1: SYCL backend (recommended)

### Environment

```bash
source /opt/intel/oneapi/setvars.sh --force
sycl-ls
```

Expect something like:

```text
[level_zero:gpu][level_zero:0] ... Intel(R) Arc(TM) Graphics ...
```

### Build llama.cpp (SYCL + Vulkan + MKL)

```bash
source /opt/intel/oneapi/setvars.sh --force
git clone https://github.com/ggml-org/llama.cpp.git
cd llama.cpp

cmake -B build \
  -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_C_COMPILER=icx \
  -DCMAKE_CXX_COMPILER=icpx \
  -DGGML_NATIVE=ON \
  -DGGML_OPENMP=ON \
  -DGGML_SYCL=ON \
  -DGGML_SYCL_F16=ON \
  -DGGML_SYCL_DNN=ON \
  -DGGML_SYCL_GRAPH=ON \
  -DGGML_VULKAN=ON \
  -DGGML_BLAS=ON \
  -DGGML_BLAS_VENDOR=Intel10_64lp

cmake --build build --config Release -j"$(nproc)"
```

- **MKL** (`GGML_BLAS`) needs oneAPI MKL installed; if configure fails, drop the two `GGML_BLAS*` lines and rebuild.
- **Vulkan** in the same binary is optional but convenient for fallback without a second build.

Check devices:

```bash
./build/bin/llama-server --list-devices
./build/bin/llama-ls-sycl-device
```

Example:

```text
SYCL0: Intel(R) Arc(TM) Graphics (... MiB)
Vulkan0: Intel(R) Arc(tm) Graphics (MTL) (... MiB)
BLAS: MKL
```

On MTL, SYCL often reports a larger UMA budget than Vulkan.

### Run (llama-server example)

```bash
source /opt/intel/oneapi/setvars.sh --force
export ONEAPI_DEVICE_SELECTOR=level_zero:gpu
export ZES_ENABLE_SYSMAN=1
export UR_L0_ENABLE_RELAXED_ALLOCATION_LIMITS=1

# Threads: start with physical cores (not every hyperthread / LP-E core).
# Example on Ultra 5 125H: try -t 12 (P+E), adjust with $(nproc) if unsure.

./build/bin/llama-server \
  -m /path/to/model.gguf \
  --device SYCL0 \
  -ngl 99 \
  -c 8192 \
  -np 1 \
  -t 12 -tb 12 \
  -b 2048 -ub 512 \
  -fa on \
  -ctk q8_0 -ctv q8_0 \
  --host 0.0.0.0 --port 8080 \
  --alias local-llm
```

Useful knobs:

| Flag | Guidance |
|------|----------|
| `-c` | Context length. Start **8k–16k**; raise only if RAM allows. Model may support much more natively (no YaRN required if within trained length). |
| `-np` | Parallel slots; each slot needs its own KV — `1` is safest on limited RAM. |
| `-ctk` / `-ctv` | `q8_0` saves KV memory vs `f16`; quality impact is usually small. |
| `-fa on` | Flash attention — lower memory, often faster. |
| `--spec-type draft-mtp` | Only if the GGUF is an **MTP** build; then add `--spec-draft-n-max 3` or `4`. |

OpenAI-compatible API: `http://127.0.0.1:8080/v1/chat/completions` · health: `/health`.

## Option 2: Vulkan backend (fallback)

### Packages

```bash
sudo apt install -y mesa-vulkan-drivers vulkan-tools vulkan-validationlayers
vulkaninfo --summary   # expect Intel Arc (MTL), not only llvmpipe
```

Avoid **llvmpipe** (CPU). Prefer explicit device selection:

```bash
./build/bin/llama-server \
  -m /path/to/model.gguf \
  --device Vulkan0 \
  -ngl 99 \
  -c 8192 \
  -np 1 \
  -fa on \
  -ctk q8_0 -ctv q8_0 \
  --host 0.0.0.0 --port 8080
```

If the wrong GPU is chosen:

```bash
export VK_ICD_FILENAMES=/usr/share/vulkan/icd.d/intel_icd.x86_64.json
# optional: MESA_VK_DEVICE_SELECTOR=8086:7d55   # MTL Arc PCI id on Ultra 5 125H
```

## Recommended quantizations

Rule of thumb: **weights must fit in free RAM with room for KV + OS**.

| Quant | Relative size | When |
|-------|---------------|------|
| Q4_K_M / UD-Q4_K_* | Baseline | Default balance for most users |
| Q5_K_M / UD-Q5_* | Larger | More RAM / want quality |
| Q3_K_M / IQ* | Smaller | Memory pressure |
| Q6_K / Q8_0 | Much larger | Only with plenty of free RAM |

**MoE models** (e.g. Qwen3.x-30B/35B-A3B): total weight is large but **active** params are smaller, so they often run better than a dense model of the same download size — still size the **file** against your RAM.

Prefer **MTP** GGUFs only when you enable `--spec-type draft-mtp`.

## Performance expectations

- Package power on D6/D8 Ultra is NAS-oriented (see README PL1/PL2 notes). Sustained tok/s will trail a desktop Arc dGPU or discrete NVIDIA card.
- First load of a multi‑GB GGUF can take minutes (mmap + iGPU residency).
- Cold short prompts look worse than longer warm generations; MTP helps when draft tokens accept.
- Monitor with `intel_gpu_top` and `free -h` under load.

## Optional: helper scripts

Some installs keep convenience launchers next to the build, for example:

- `run-*-sycl.sh` / `run-*-vulkan.sh` — wrap `setvars`, device env, model path, `-c` / `-np`
- `rebuild-llama-cpp.sh` — re-run the cmake line above + `git pull`

Those paths are **local conventions**, not required. The commands in this guide are enough.

Suggested env-style overrides if you write your own wrappers:

| Variable | Meaning |
|----------|---------|
| `CTX` | Context (`-c`) |
| `NP` | Slots (`-np`) |
| `THREADS` | `-t` / `-tb` |
| `MODEL` | Path to `.gguf` |
| `PORT` | Listen port |

## Troubleshooting

| Symptom | Check |
|---------|--------|
| `No device of requested type` (SYCL) | `source setvars.sh`; `ONEAPI_DEVICE_SELECTOR=level_zero:gpu`; `sycl-ls` |
| Build: Level Zero support disabled | `sudo apt install libze-dev`, reconfigure cmake |
| Vulkan picks llvmpipe | `--device Vulkan0`; Intel `VK_ICD_FILENAMES` |
| Permission denied on `/dev/dri/renderD*` | User in `render`; re-login after `usermod` |
| OOM / heavy swap | Smaller quant; lower `-c` / `-np`; confirm BIOS `Igfx Gsm2`; close other services |
| Throttling on long runs | BIOS RC1p off (`graphics-BIOS.md`); fans (`hardware-fan-control.md`) |

## Related guides

- [graphics-BIOS.md](graphics-BIOS.md) — Igfx Gsm2, RC1p, IGFX primary
- [graphics-iGPU.md](graphics-iGPU.md) — Level Zero, OpenCL, VA-API packages
- [kernel-parameters.md](kernel-parameters.md) — GRUB cmdline (not LLM-specific)
