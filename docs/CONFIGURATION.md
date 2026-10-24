# Setup And Configuration

[简体中文](CONFIGURATION.zh-CN.md) | [Back to README](../README.md)

## Environment Setup

Use Linux/WSL2, Python 3.12 and bubblewrap (`bwrap`) with user/network namespace support. GPU execution additionally needs NVIDIA CUDA support. Windows uses the PowerShell WSL2 launcher.

### CPU-Only Setup

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-cpu.txt
python scripts/pdf2md.py convert --input ../manual.pdf
```

This environment includes the coordinator and the pinned CPU backend. CUDA, MinerU and a separate GPU model directory are not required. The pinned Layout wheel includes its model resources; conversion does not download weights. For offline installation, add `--no-index` and `--find-links /path/to/wheelhouse` to the installation command.

### GPU Setup

The coordinator and MinerU backend can share one Python environment, or you can reuse a separate backend environment. Coordinator dependencies are in `requirements-coordinator.txt`; the reference backend inventory is `config/s1-mineru-requirements.lock.txt`. Backend version checks include MinerU 4.0.12, torch 2.14.1, torchvision 0.29.1 and pypdfium2 5.14.0. Use packages compatible with your GPU and driver.

Example coordinator installation from a local wheelhouse:

```bash
python3.12 -m venv ../env-coordinator
source ../env-coordinator/bin/activate
python -m pip install --no-index --find-links /path/to/wheelhouse -r requirements-coordinator.txt
python -m pip check
```

This installs only coordinator dependencies; prepare MinerU and its dependencies separately. Place the model files bound in `config/baseline_bindings.json` as follows. Hashes are checked at runtime.

```text
workspace/
  Pdf2Md/
  models/
    MinerU-4_models_torch/
      ...
```

Conversion never downloads dependencies or models. After setup, routine conversion needs only an input PDF.

## Windows And WSL2

Prepare Python 3.12, the pinned dependencies, models and bubblewrap inside WSL2 once. Use the Windows NVIDIA driver with WSL CUDA support; do not install a Linux display driver in WSL. This profile supports a single NVIDIA GPU; WSL multi-GPU and MIG are rejected. Native Linux keeps its existing device/MIG settings.

```powershell
.\scripts\pdf2md.ps1 convert --input "D:\Documents\manual.pdf"
.\scripts\pdf2md.ps1 verify --output-dir "D:\Documents\manual_pdf2md"
.\scripts\pdf2md.ps1 doctor --wsl-distro Ubuntu-24.04 --wsl-python /path/to/venv/bin/python
```

The launcher uses the default WSL distro unless `--wsl-distro` is supplied. Python selection: `--wsl-python`, `PDF2MD_WSL_PYTHON`, the selected/discovered config's `coordinator_python`, the project's `.venv/bin/python`, then `python3`. A project `.venv` with both coordinator and backend dependencies, and sibling `models/`, needs no config file. Alternatively, save custom paths once with `init-config` using the chosen WSL Python. Windows Python is never invoked.

Drive paths, Windows relative paths and WSL absolute paths are accepted for input, output, config, models, reports and cache. Relative CLI paths use the PowerShell current directory. Spaces, Unicode and new output directories are supported. UNC paths and drive-relative paths such as `D:manual.pdf` are rejected; use `D:\manual.pdf` instead. Options accept both `--key value` and `--key=value`. Exit codes and native logs are preserved; successful conversions display a Windows-openable output path. If local script execution is blocked by policy, invoke `powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\pdf2md.ps1 --help` for this invocation only.

Bubblewrap lookup: `--bwrap`, config `bubblewrap_path`, `PDF2MD_BWRAP`, then PATH. A user-local executable is mounted read-only into the nested sandbox, not its parent directory. WSL uses `/dev/dxg` and `/usr/lib/wsl/lib`; missing devices, permissions or driver libraries fail explicitly. The worker keeps `--unshare-all` and fresh `/proc`. Reports bind platform, driver inventory, GPU UUID, bubblewrap bytes/version and WSL libcuda bytes into the environment fingerprint. Changed environment/code/config/input requires a new run; a1 caches are not interchangeable with a2.

## Runtime Overrides

`--compute auto` (default) selects a prepared GPU first, otherwise a prepared CPU backend. `--compute cpu` never uses GPU devices; `--compute cuda` fails rather than falling back. Invalid configuration and binding integrity failures are errors, not fallback reasons. Runtime failures do not switch backends. A batch selects its backend once.

Keep existing GPU environments unchanged. To add CPU support alongside one, prepare a separate environment with `requirements-cpu.txt`, then set `cpu_python` in the runtime config, `PDF2MD_CPU_PYTHON`, or `--cpu-python /path/to/env-cpu/bin/python`. Precedence is CLI > config > environment > coordinator Python. CPU coordinators accept the pinned PyMuPDF 1.28.2; an existing 1.26.0 coordinator can also run a separate 1.28.2 CPU worker. GPU coordinators retain 1.26.0.

`doctor` reports CPU/GPU availability and selection reasons. No available backend triggers installation automatically. Resume and cache reuse retain the original selected backend and bindings; changing compute mode requires a new output. The existing repeated `--device /dev/...` option still authorizes GPU nodes; it is not a compute-mode flag. Windows also translates `--cpu-python` and `--render-dir`, and forwards `PDF2MD_CPU_PYTHON`.

```bash
python scripts/pdf2md.py convert --input ../manual.pdf \
  --models /path/to/models --parser-python /path/to/env-mineru/bin/python
```

`--gpu 1` selects the second visible GPU, respecting `CUDA_VISIBLE_DEVICES`; a full GPU UUID is also accepted. Regular GPU device nodes are detected automatically. For MIG, use `--gpu MIG-UUID` and repeated `--device` options for the physical GPU, `/dev/nvidiactl`, `/dev/nvidia-uvm` and the corresponding GPU-instance/compute-instance capability nodes.

## Save Settings

To reuse custom paths, generate a config file:

```bash
python scripts/pdf2md.py init-config \
  --parser-python /path/to/env-mineru/bin/python --models /path/to/models
```

This creates `runtime.local.json` in the current directory without overwriting existing files. It is read automatically on subsequent runs from that directory. Use `--config /path/to/runtime.json` for an explicit file.

Config discovery order: explicit `--config`, `PDF2MD_CONFIG`, current directory's `runtime.local.json`, the source directory's file of that name, then `~/.config/pdf2md/runtime.json` (respecting `XDG_CONFIG_HOME`). With no file found, built-in defaults apply. A missing explicitly selected file is an error.

Setting precedence: command-line overrides > config file > environment variables > built-in defaults. Supported environment variables are `PDF2MD_MODELS` and `PDF2MD_PARSER_PYTHON`. Relative config paths use the config directory; CLI and environment paths use the current directory. See `config/runtime.example.json` for the schema.

## Checks And Recovery

```bash
# Check the environment without inference
python scripts/pdf2md.py doctor

# Inspect only the input PDF
python scripts/pdf2md.py preflight --input ../manual.pdf

# Recheck a result's seal, page sequence and images
python scripts/pdf2md.py verify --output-dir ../manual_pdf2md

# Resume a batch
python scripts/pdf2md.py batch --input-dir ../pdfs --recursive --resume
```

`convert --native-cache /path/to/previous-result` reuses compatible sealed chunks generated by this tool, skipping their inference. Resume and cache reuse check input, implementation, configuration and environment bindings. Use a new output directory when the input set changes.

Batch jobs run sequentially and continue after individual failures. Results and diagnostics are in `batch_summary.json` and `logs/`; `effective_runtime.json` pins the batch's resolved device and paths. Batches containing failures exit 2.

## Troubleshooting

| Problem | Action |
| --- | --- |
| Missing models or hash mismatch | Check the root passed to `--models` and the binding inventory |
| Missing or mismatched backend dependencies | Activate the correct environment or set `--parser-python` |
| Missing or inaccessible GPU | Check drivers, device permissions and `CUDA_VISIBLE_DEVICES`; specify MIG UUID and nodes explicitly |
| bubblewrap/namespace check fails | Check `bwrap` installation and user/network namespace support |
| Output directory exists | Use `--resume` for a stopped job with unchanged input/config/environment; otherwise choose a new directory |
| Scanned PDF rejected | Use a PDF with native text; this workflow does not perform OCR |

See `python scripts/pdf2md.py --help` and each subcommand's `--help` for all options.
