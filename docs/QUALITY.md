# Output Notes

## Status And Checks

- `SUCCESS_WITH_WARNINGS`: complete page sequence and no unresolved items in the implemented source-preservation checks; other quality findings remain in `quality_report.json`.
- `CONTENT_REVIEW_REQUIRED` (exit 2): all pages can be processed and the files sealed, while a source table has only a visual fallback or a figure region is unresolved. Both conversion and `verify` report this state.
- `PARTIAL`: available output is retained, but some pages or chunks are incomplete.
- `FAILED`: the attempt failed; inspect `run_status.json` and the attempt logs.

`verify` checks the output seal, page sequence, image readability and the source-preservation evidence. A seal records file integrity, independently of the content-review state.

## Source Content Preservation

`page_processing.coverage_percent` counts processed physical pages only.
`content_preservation` reports source object checks and unresolved states, with no
whole-document content percentage. These are separate measurements.

Before technical checks, the converter inventories native ruled grids and explicit
bold Figure/Fig captions. Source cells are reconstructed from grid boundaries and
native glyph geometry, preserving merged cells and supported scripts. Output tables
are associated by native table serialization and geometry when available, or by
textual labels, not expected numerical values. Duplicate tables use occurrence
order only when the complete native table sequence matches and native indices
are unique and ordered. Otherwise duplicates remain unresolved. A uniquely
associated damaged table is replaced by source-derived HTML; an ambiguous or
unsupported table keeps its existing output and gains a source-region image marked
as a visual reference. Independently bounded figure panels are also retained as
source images, including lettered panels such as Figure 11i. Captions outside
frames use independently extracted drawing clusters and native layout locations;
label lines and captions are retained, while competing regions or unassigned
detached graphics keep the figure unresolved.

`source_preservation.json` records each region, state, original output, replacement
and image hash. Version 2 also records exact table-object spans before prose repairs
and translates them into final source mappings. Repeated identical text can thus
retain distinct locations. Final source-map entries point to the same PDF regions. New results
require this report; old results remain verifiable without adding files. The
inventory covers these recognizable source structures rather than all possible
PDF content. Source images preserve visual content; table recovery produces
editable cells. No text OCR is enabled by this stage.

## Processing And Records

F1 adds separate technical counts: checked, matched, mismatched, repaired, rejected,
unresolved and unchecked. `SOURCE_MATCH` applies only to the attributes listed in
that record. `verify` checks their final offsets, source-map IDs, hashes, summary
and original-version recovery evidence. `trace` includes matching technical
records and repairs. See [F1 coverage and commands](FIDELITY.md).

The GPU MinerU backend performs layout and mathematical recognition. The CPU PyMuPDF4LLM/Layout backend uses native text and layout without mathematical recognition. Both disable scan/text OCR. Their outputs may differ. Automatic prose repairs require a unique source-text match; ambiguous cases remain recorded for inspection. Source images preserve pixels rather than editable text.

The intermediate sequence is `worker_manual.md` -> `native_before_source_text.md` -> `native_manual.md` -> `manual.md`. Source additions and repairs are recorded in `source_glyphs.json`, `source_text_pages.json`, `source_preservation.json` and `change_log.json`. `provenance.json` records input and model hashes, page mapping and processing history. Attempt logs remain under `_work/`.

For normal reading, keep `manual.md` and `assets/` together. Keep the full result directory for verification, diagnosis and resume.

New outputs include `source_map.json` for object-level source lookup. `trace` returns the original page/region, source and output excerpts, association basis and ambiguity state. This reduces source-location work during verification; a located object can still contain a wrong number or operator. See [source mapping](SOURCE_MAPPING.md). No visual review page or additional risk queue is generated.

## Licensing

This repository contains the engineering prototype's source, tests and usage documentation. It does not include model weights, Python environments or source PDFs. No project-level open-source license has been specified.

MinerU, PyMuPDF and the model files have their own licensing terms, including applicable MinerU conditions and PyMuPDF's AGPL/commercial licensing options. Check these terms for your deployment and distribution. Rights to source PDFs and derived Markdown or images depend on the source documents. Package and model hashes identify files; they do not grant redistribution rights. Retain applicable upstream notices.
