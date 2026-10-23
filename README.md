# Pdf2Md

[English](README.md) | [简体中文](README.zh-CN.md)

Version: **0.1.0a1**. Convert native electronic PDFs to `manual.md + assets/` through a local, offline command-line workflow.

## Quick Start

Complete the one-time [environment setup](docs/CONFIGURATION.md#environment-setup), activate your Python environment, and run from this directory:

```bash
python scripts/pdf2md.py convert --input ../manual.pdf
```

Results are saved to `manual_pdf2md/` beside the PDF. Open `manual.md` and keep `assets/` alongside it when moving the result.

Conversion automatically runs environment checks, PDF preflight and output integrity checks. Separate `doctor`, `preflight` and `verify` commands are optional.

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
| Backend Python | Current Python environment |
| Models | `models/` beside the source directory |
| Output | `<file-stem-or-directory-name>_pdf2md/` beside the input |
| Network | Offline; `--offline` is optional |
| Chunks and retries | 50 pages per chunk; retry once on failure |
| Config file | Discover automatically; creating one is optional |

For inputs inside the source directory, output goes under `pdf2md-output/` beside the source directory to keep results separate. Existing outputs are preserved. Resume with `--resume` while keeping inputs and environment unchanged.

For custom model paths, separate backend environments, multiple GPUs or MIG, see [configuration and troubleshooting](docs/CONFIGURATION.md).

## Supported Inputs

Linux, Python 3.12, NVIDIA GPU and bubblewrap. Native electronic PDFs, including vector-only pages, are supported; scanned text and OCR are not. The backend is MinerU 4.0.12 with the pinned offline model bundle.

## Check Results

`SUCCESS_WITH_WARNINGS` indicates that output integrity checks passed with quality warnings; see `quality_report.json` for details. When reviewing results, focus on formula operators, numbers and signs, table column and condition associations, rotated labels and omitted text. See [output notes](docs/QUALITY.md).

## Tests

```bash
PYTHONPATH=src python -m unittest discover -s tests -v
```

## Licensing

Deployment and distribution require checking the applicable MinerU, PyMuPDF, model and source-document licenses. See [licensing notes](docs/QUALITY.md#licensing).
