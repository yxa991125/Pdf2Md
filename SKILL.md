---
name: pdf2md
description: Convert native electronic PDFs to Markdown and assets offline on Linux or single-GPU WSL2, including a Windows PowerShell launcher. Not for scanned PDFs or OCR.
---

# Pdf2Md

Windows uses WSL2, not Windows Python. After one-time setup, run `scripts/pdf2md.ps1 convert --input "D:\Documents\manual.pdf"` or `scripts/pdf2md.ps1 batch --input-dir "D:\Documents\pdfs" --recursive` from PowerShell. The default WSL distro is used; `--wsl-distro` and `--wsl-python` override it. See configuration docs for interpreter selection and paths. WSL supports one NVIDIA GPU through `/dev/dxg`, not MIG or multi-GPU. Both coordinator and worker use WSL driver libraries; the worker retains a fresh PID namespace and `/proc`. `--bwrap`, `PDF2MD_BWRAP` or `bubblewrap_path` selects a user-local bubblewrap without mounting its parent directory. Native Linux device/MIG configuration remains available below.

Use this source distribution on Linux with Python 3.12, an existing compatible MinerU 4.0.12 environment and the offline model bundle. Read [README.md](README.md) or [中文说明](README.zh-CN.md) for setup. Routine use needs only `convert --input /path/manual.pdf`; offline execution, environment/preflight/integrity checks and output naming are automatic. The current Python is also the default backend; models default to `models/` beside this distribution. Config files are optional and discovered automatically. See [configuration](docs/CONFIGURATION.md) for overrides. GPU selection defaults to visible index 0, respecting `CUDA_VISIBLE_DEVICES`, with automatic device-node detection. MIG requires an explicit UUID and device nodes.

Run with the deployment's coordinator Python from this distribution:

```bash
python scripts/pdf2md.py doctor --config /absolute/runtime.json
python scripts/pdf2md.py preflight --input /absolute/manual.pdf
python scripts/pdf2md.py convert --config /absolute/runtime.json --input /absolute/manual.pdf --output-dir /absolute/new-output --offline
python scripts/pdf2md.py verify --output-dir /absolute/new-output
```

Conversion runs the same environment checks as doctor before inference; doctor can also be run separately for diagnosis. It never installs dependencies, downloads models or selects substitute devices after a failed check. No CPU/native-Windows inference or unsandboxed fallback is provided. Linux namespaces deny network access; only the resolved device nodes are mounted. Runtime must not read Gold, scorer answers, review records or historical experiments. No S1/S2 or newly added OCR workflow is enabled; existing layout/math recognition remains.

For multiple local PDFs, `batch --config /absolute/runtime.json --input-dir /absolute/pdfs --output-dir /absolute/new-batch --offline` runs the same real entrypoint sequentially. Inspect `batch_summary.json` and per-attempt logs, including failures; do not treat it as a quality score. Use `--resume` only for identical inputs/code/config/environment and a confirmed stopped job; do not restart an observed live conversion. Changed test sets need new batch directories.

Deliver `manual.md` and `assets/` together. Read exit codes, `quality_report.json`, source-bound checks and `run_status.json`. `SUCCESS_WITH_WARNINGS` and integrity verification are not technical-semantic certification. Retain ambiguous formula, numeric, table, caption and footnote relationships as unresolved; never guess register values, operators or units. Source images do not establish information gain or editable text. Known limitations and reversible stage records are in [output notes](docs/QUALITY.md).

Preserve partial outputs and failed attempts. Do not overwrite sealed runs. `--native-cache` is an explicit compatible-own-chunk CPU reprocessing option, not new inference or an alternative source of answers. Organization license/model/source-distribution approvals are external and cannot be inferred from a successful conversion.
