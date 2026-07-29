# LLM Inference on Intel Arc iGPU (SYCL & Vulkan)

> Local LLMs with llama.cpp on Zettlab D6/D8 Ultra (Meteor Lake Arc iGPU, Core Ultra 5 125H).

## Overview

This machine uses **unified memory (UMA)**: the Arc iGPU and CPU share system RAM (~87 GiB). There is no discrete VRAM pool.

| Backend | When to use |
|---------|-------------|
| **SYCL** (Level Zero) | **Default / preferred** on this box — Intel path, F16 + oneDNN, usually best PP/TG |
| **Vulkan** (Mesa ANV) | Fallback or A/B compare if SYCL misbehaves |

Both backends need the BIOS settings in `graphics-BIOS.md` (especially **Igfx Gsm2 = 4GB** and RC1p disabled) and the driver stack in `graphics-iGPU.md`.

Verified on this host (2026-07): Arc `8086:7d55`, Level Zero + OpenCL + Vulkan present, llama.cpp b492 with SYCL+Vulkan+MKL, Qwen3.6-35B-A3B-MTP at **32k** context.

## Prerequisites

- BIOS configured per `graphics-BIOS.md` (4GB `Igfx Gsm2` recommended)
- Ubuntu 26.04 with iGPU compute stack (`graphics-iGPU.md`)
- User in the `render` group
- Device nodes: `/dev/dri/card1` and `/dev/dri/renderD128` (minor number may be `card0` on some boots — use whatever `ls /dev/dri` shows)
- Intel oneAPI Base Toolkit at `/opt/intel/oneapi` (for SYCL build + runtime)
- Level Zero **headers** for building llama.cpp SYCL with L0 API support:

```bash
sudo apt install -y libze-dev
```

## Paths on this machine

| Item | Path |
|------|------|
| llama.cpp source/build | `~/apps/llama.cpp` |
| Rebuild helper | `~/apps/rebuild-llama-cpp.sh` |
| SYCL launcher (preferred) | `~/apps/run-qwen36-sycl.sh` |
| Vulkan launcher (fallback) | `~/apps/run-qwen36-vulkan.sh` |
| Default model (HF cache) | `unsloth/Qwen3.6-35B-A3B-MTP-GGUF` → `Qwen3.6-35B-A3B-UD-Q4_K_XL.gguf` (~22 GiB) |

## Option 1: SYCL backend (recommended)

### Environment

```bash
source /opt/intel/oneapi/setvars.sh --force
sycl-ls
```

Expect a line like:

```text
[level_zero:gpu][level_zero:0] ... Intel(R) Arc(TM) Graphics ...
```

### Build llama.cpp (SYCL + Vulkan + MKL)

From a clean or existing tree:

```bash
# Or: ~/apps/rebuild-llama-cpp.sh --pull
source /opt/intel/oneapi/setvars.sh --force
cd ~/apps/llama.cpp

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

SYCL typically reports a larger UMA budget than Vulkan on this host.

### Run (server, 32k context)

Prefer the launcher (sources oneAPI, pins SYCL0, local GGUF if cached):

```bash
~/apps/run-qwen36-sycl.sh
# overrides: CTX=65536 NP=2 PORT=8081 ~/apps/run-qwen36-sycl.sh
```

Equivalent idea (simplified):

```bash
source /opt/intel/oneapi/setvars.sh --force
export ONEAPI_DEVICE_SELECTOR=level_zero:gpu
export ZES_ENABLE_SYSMAN=1
export UR_L0_ENABLE_RELAXED_ALLOCATION_LIMITS=1

~/apps/llama.cpp/build/bin/llama-server \
  -m /path/to/Qwen3.6-35B-A3B-UD-Q4_K_XL.gguf \
  --device SYCL0 \
  -ngl 99 \
  -c 32768 \
  -np 1 \
  -t 12 -tb 12 \
  -b 2048 -ub 512 \
  -fa on \
  -ctk q8_0 -ctv q8_0 \
  --spec-type draft-mtp \
  --spec-draft-n-max 4 \
  --host 0.0.0.0 --port 8080 \
  --alias qwen36
```

Notes for this CPU (Ultra 5 125H):

- `-t 12` ≈ P+E cores; skip the two low-power E-cores when possible
- Model native context is **262144** — no YaRN needed at 32k/64k
- GQA is tiny (`kv_heads=2`) so 32k KV is cheap (~0.3–1.4 GiB depending on cache type)
- OpenAI API: `http://127.0.0.1:8080/v1/chat/completions` · health: `/health`

## Option 2: Vulkan backend (fallback)

### Packages

```bash
sudo apt install -y mesa-vulkan-drivers vulkan-tools vulkan-validationlayers
vulkaninfo --summary   # expect: Intel(R) Arc(tm) Graphics (MTL)
```

Avoid accidentally selecting **llvmpipe** (CPU). The launcher sets `MESA_VK_DEVICE_SELECTOR` / ICD when needed.

### Run

```bash
~/apps/run-qwen36-vulkan.sh
```

Or:

```bash
~/apps/llama.cpp/build/bin/llama-server \
  -m /path/to/model.gguf \
  --device Vulkan0 \
  -ngl 99 -c 32768 -np 1 \
  -fa on -ctk q8_0 -ctv q8_0 \
  --spec-type draft-mtp --spec-draft-n-max 4 \
  --host 0.0.0.0 --port 8080
```

## Recommended models / quants

For **Qwen3.6-35B-A3B** (MoE, ~3B active) on ~87 GiB UMA:

| Quant | Approx. size | Notes |
|-------|--------------|--------|
| **UD-Q4_K_XL** / Q4_K_M | ~21–22 GiB | Best default balance (what we run) |
| Q5_K_M / UD-Q5_* | ~25 GiB | Higher quality if RAM headroom allows |
| Q3_K_M / IQ* | ~18 GiB or less | Only if under memory pressure |

Prefer **MTP** builds (`*-MTP-GGUF`) when using `--spec-type draft-mtp`.

## Performance expectations

- CPU package power is limited on this NAS-class SKU (see README: PL1/PL2 locked). That caps sustained iGPU throughput vs a desktop Arc dGPU.
- First load of a ~22 GiB GGUF onto the iGPU can take a couple of minutes.
- Short cold prompts look slower than long warm generations; MTP helps when draft tokens are accepted.
- Optional: watch the GPU with `intel_gpu_top` while generating.
- If RAM is tight (swap climbing with the server up), lower quant, lower `CTX`/`NP`, or stop other heavy processes.

## Launcher env overrides

Both `run-qwen36-*.sh` scripts honor:

| Env | Default | Meaning |
|-----|---------|---------|
| `CTX` | `32768` | Context length |
| `NP` | `1` | Server slots (each gets full `CTX`) |
| `NGL` | `99` | GPU layers |
| `THREADS` | `12` | CPU threads |
| `BATCH` / `UBATCH` | `2048` / `512` | Batch sizes |
| `PORT` / `HOST` | `8080` / `0.0.0.0` | Bind address |
| `CTK` / `CTV` | `q8_0` | KV cache types |
| `DRAFT_N` | `4` | MTP draft tokens |
| `MODEL_LOCAL` | HF cache path | Override GGUF path |

## Troubleshooting

| Symptom | Check |
|---------|--------|
| `No device of requested type` (SYCL) | `source setvars.sh`; `ONEAPI_DEVICE_SELECTOR=level_zero:gpu`; `sycl-ls` |
| Build: Level Zero support disabled | `sudo apt install libze-dev`, re-run cmake |
| Vulkan picks llvmpipe | Use `--device Vulkan0`; set `VK_ICD_FILENAMES` to Intel ICD |
| Permission denied on `/dev/dri/renderD*` | `groups` must include `render`; re-login after `usermod` |
| OOM / heavy swap | Smaller quant; `CTX=8192`; `NP=1`; ensure BIOS Gsm2=4GB |
| Throttling on long runs | BIOS RC1p off (`graphics-BIOS.md`); check fans (`hardware-fan-control.md`) |

## Related guides

- [graphics-BIOS.md](graphics-BIOS.md) — Igfx Gsm2, RC1p, IGFX primary
- [graphics-iGPU.md](graphics-iGPU.md) — Level Zero, OpenCL, VA-API packages
- [kernel-parameters.md](kernel-parameters.md) — GRUB cmdline (not LLM-specific)
