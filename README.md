# Pdf2Md

[English](README.md) | [简体中文](README.zh-CN.md)

[Changelog](CHANGELOG.md)

Version: **0.1.0a5** (candidate). Convert native electronic PDFs to `manual.md + assets/`, with source locations for verification. Supports GPU and CPU execution.

## Quick Start

Give your Agent this prompt:

> Use the Pdf2Md Skill to convert inputs/manual.pdf.

On first use the Agent checks readiness, presents the installation plan and asks for approval once. It then prepares the user runtime and continues conversion. For manual initialization, run from this directory:

```bash
python3 scripts/pdf2md.py setup
```

Windows / WSL2, without Windows Python:

```powershell
.\scripts\pdf2md.cmd setup
```

After setup, run `python3 scripts/pdf2md.py convert --input ../manual.pdf`, or `.\scripts\pdf2md.cmd convert --input "D:\Documents\manual.pdf"` on Windows. No environment activation is needed. Results are saved to `manual_pdf2md/` beside the PDF; keep `assets/` alongside `manual.md`.

Requires Linux/WSL2 and Python 3.12 with venv support. Setup does not install Python or change system settings. Online setup obtains locked packages and bound models; [offline installation and prerequisites](docs/CONFIGURATION.md#environment-setup) are also supported. Conversion remains offline.

Conversion automatically runs environment checks, PDF preflight and output integrity checks. Separate `doctor`, `preflight` and `verify` commands are optional.

GPU is selected when ready; otherwise a prepared CPU backend is used. CPU-only setup does not require CUDA or MinerU. Use `--compute cpu` or `--compute cuda` to select explicitly.

## Source Lookup

```bash
python3 scripts/pdf2md.py trace --output-dir ../manual_pdf2md --line 25
```

`source_map.json` indexes final output objects. Queries return JSON with the output excerpt, original PDF page/region, source text and association basis. Optional screenshots are generated only when requested; no viewer or full-document screenshot export is created. See [source mapping](docs/SOURCE_MAPPING.md).

## Organization And Local Revision

Organize an existing complete conversion without rerunning the parser:

```bash
python3 scripts/pdf2md.py organize --from-result ../manual_pdf2md --workspace ../organized
python3 scripts/pdf2md.py scopes --workspace ../organized
python3 scripts/pdf2md.py search --workspace ../organized --query DATA_STATUS --match exact
```

Persistent Units and explicit/inferred/virtual Scopes support source lookup, bounded edits, atomic publication and rollback. Original conversions remain unchanged. See [organization commands](docs/ORGANIZATION.md). Windows uses `.\scripts\pdf2md.cmd`.

## Common Commands

```bash
# Choose an output directory
python3 scripts/pdf2md.py convert --input ../manual.pdf --output-dir ../result

# Continue after an interruption
python3 scripts/pdf2md.py convert --input ../manual.pdf --resume

# Convert a directory, including subdirectories
python3 scripts/pdf2md.py batch --input-dir ../pdfs --recursive

# Show all options
python3 scripts/pdf2md.py convert --help
```

## Defaults

| Setting | Default |
| --- | --- |
| GPU | First visible GPU; detect device nodes automatically |
| Compute | `auto`: ready GPU first, otherwise a prepared CPU backend |
| Backend Python | User runtime saved by setup; current Python for manual deployments |
| Models | Verified user-managed bundle after setup |
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
