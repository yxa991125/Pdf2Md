# Pdf2Md

[English](README.md) | [简体中文](README.zh-CN.md)

[Changelog](CHANGELOG.md)

Version: **0.1.0a4**. Convert native electronic PDFs to `manual.md + assets/`, with source locations for verification. Supports GPU and CPU execution.

## Quick Start

Complete the one-time [environment setup](docs/CONFIGURATION.md#environment-setup), activate your Python environment, and run from this directory:

```bash
python scripts/pdf2md.py convert --input ../manual.pdf
```

On Windows, use PowerShell with WSL2 after the one-time WSL environment setup. Windows Python is not needed. The CMD launcher works with the usual Restricted PowerShell execution policy, without changing user or system policy:

```powershell
.\scripts\pdf2md.cmd convert --input "D:\Documents\manual.pdf"
.\scripts\pdf2md.cmd batch --input-dir "D:\Documents\pdfs" --recursive
```

Results are saved to `manual_pdf2md/` beside the PDF. Open `manual.md` and keep `assets/` alongside it when moving the result.

Conversion automatically runs environment checks, PDF preflight and output integrity checks. Separate `doctor`, `preflight` and `verify` commands are optional.

GPU is selected when ready; otherwise a prepared CPU backend is used. CPU-only setup does not require CUDA or MinerU. Use `--compute cpu` or `--compute cuda` to select explicitly.

## Source Lookup

```bash
python scripts/pdf2md.py trace --output-dir ../manual_pdf2md --line 25
```

`source_map.json` indexes final output objects. Queries return JSON with the output excerpt, original PDF page/region, source text and association basis. Optional screenshots are generated only when requested; no viewer or full-document screenshot export is created. See [source mapping](docs/SOURCE_MAPPING.md).

## Common Commands

```bash
# Choose an output directory
python scripts/pdf2md.py convert --input ../manual.pdf --output-dir ../result

# Continue after an interruption
python scripts/pdf2md.py convert --input ../manual.pdf --resume

# Convert a directory, including subdirectories
python scripts/pdf2md.py batch --input-dir ../pdfs --recursive

# Show all options
python scripts/pdf2md.py convert --help
```

## Defaults

| Setting | Default |
| --- | --- |
| GPU | First visible GPU; detect device nodes automatically |
| Compute | `auto`: ready GPU first, otherwise a prepared CPU backend |
| Backend Python | Current Python environment |
| Models | `models/` beside the source directory |
| Output | `<file-stem-or-directory-name>_pdf2md/` beside the input |
| Network | Offline; `--offline` is optional |
| Chunks and retries | 50 pages per chunk; retry once on failure |
| Config file | Discover automatically; creating one is optional |

For inputs inside the source directory, output goes under `pdf2md-output/` beside the source directory to keep results separate. Existing outputs are preserved. Resume with `--resume` while keeping inputs and environment unchanged.

For custom model paths, separate backend environments, multiple GPUs or MIG, see [configuration and troubleshooting](docs/CONFIGURATION.md).

## Supported Inputs

Linux or WSL2, Python 3.12 and bubblewrap. Windows runs through WSL2, not native Windows inference. Native electronic PDFs, including vector-only pages, are supported; scanned text and OCR are not. GPU uses MinerU 4.0.12; CPU uses PyMuPDF4LLM/Layout 1.28.2. Results may differ by backend. WSL GPU execution supports one NVIDIA GPU; MIG and multi-GPU selection remain native-Linux-only.

## Check Results

`SUCCESS_WITH_WARNINGS` indicates that output integrity checks passed with quality warnings; see `quality_report.json` for details. When reviewing results, focus on formula operators, numbers and signs, table column and condition associations, rotated labels and omitted text. See [output notes](docs/QUALITY.md).

## Tests

```bash
PYTHONPATH=src python -m unittest discover -s tests -v
```

## Licensing

Deployment and distribution require checking the applicable MinerU, PyMuPDF, model and source-document licenses. See [licensing notes](docs/QUALITY.md#licensing).
