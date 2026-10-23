# Output Notes

## Status And Checks

- `SUCCESS_WITH_WARNINGS`: complete page sequence and local assets under the implemented integrity checks, with quality warnings available in `quality_report.json`.
- `PARTIAL`: available output is retained, but some pages or chunks are incomplete.
- `FAILED`: the attempt failed; inspect `run_status.json` and the attempt logs.

`verify` checks the output seal, page sequence and image readability. Review technical content separately, especially formula operators, signed numbers, table column and condition associations, rotated labels and omitted text. Current limitations include errors in these areas.

## Processing And Records

The offline MinerU backend performs layout and mathematical recognition. Neural text recognition and scan OCR are disabled by the runtime policy. Automatic prose repairs require a unique source-text match; ambiguous cases remain recorded for inspection. Source images preserve pixels rather than editable text.

The intermediate sequence is `worker_manual.md` -> `native_before_source_text.md` -> `native_manual.md` -> `manual.md`. Source additions and repairs are recorded in `source_glyphs.json`, `source_text_pages.json` and `change_log.json`. `provenance.json` records input and model hashes, page mapping and processing history. Attempt logs remain under `_work/`.

For normal reading, keep `manual.md` and `assets/` together. Keep the full result directory for verification, diagnosis and resume.

## Licensing

This repository contains the engineering prototype's source, tests and usage documentation. It does not include model weights, Python environments or source PDFs. No project-level open-source license has been specified.

MinerU, PyMuPDF and the model files have their own licensing terms, including applicable MinerU conditions and PyMuPDF's AGPL/commercial licensing options. Check these terms for your deployment and distribution. Rights to source PDFs and derived Markdown or images depend on the source documents. Package and model hashes identify files; they do not grant redistribution rights. Retain applicable upstream notices.
