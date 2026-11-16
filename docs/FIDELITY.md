# Technical Checks And Recovery

[简体中文](FIDELITY.zh-CN.md) | [README](../README.md)

Normal `convert` and `batch` run F1 after native conversion. `--fidelity repair`
is the default; `check` records checks without edits, and `off` retains the
original path. No document-organization workspace or extra model is needed.

## Existing Results

```bash
python3 scripts/pdf2md.py fidelity --input /path/manual.pdf \
  --result /path/R0 --mode repair --output-dir /path/R1
python3 scripts/pdf2md.py verify --output-dir /path/R1
python3 scripts/pdf2md.py trace --output-dir /path/R1 --line 25 \
  --input /path/manual.pdf --render-dir /path/new-evidence
python3 scripts/pdf2md.py fidelity --result /path/R1 \
  --mode rollback --output-dir /path/restored-R0
```

The source PDF must match the original hash. Publication uses a new directory;
R0 is not edited. Add `--resume` only for the same input, implementation, mode and
runtime. Whole-result rollback reconstructs the original sealed bytes, not just
the original Markdown.

## Checked Properties

- Quantities: signed scalar/range values, scientific notation and literal units,
  paired within unique source lines or complete source blocks/output parents.
- Formulas: supported assignments, operands, arithmetic precedence, one simple
  geometric fraction and literal scripts. Explicit equation numbers and
  `When MODE=0: X=A-B` / `X=A-B; when MODE=0` annotations are compared when present
  in both source and output.
- Source-side formula discovery records missing editable formulas as well as
  existing output. Images, unsupported representations and ambiguous objects
  remain explicitly undecided.
- Automatic repair: a unique native linear formula with demonstrated subtraction
  loss and an inserted underscore artifact. A separate verifier re-reads PDF
  glyphs and checks the complete replacement. It does not reuse the candidate
  generator's arithmetic parser. Fraction/annotation repairs are not enabled.

Special-font encodings, rotated formulas, nested unsupported scripts, implicit
algebra and complex table-header ownership retain unresolved/unchecked states.
A spaced leading list dash is not certified as a negative sign. Formula
association and checked properties are separate from whole-object correctness.

## Result Records

`fidelity_assessment.json` lists source/output locations, specific differences,
checked attributes and one of `SOURCE_MATCH`, `SOURCE_MISMATCH`,
`ALIGNMENT_AMBIGUOUS`, `SOURCE_INSUFFICIENT` or `NOT_CHECKED`. The quality report
separately summarizes checked, matched, mismatched, repaired, rejected, unresolved
and unchecked records. These are record counts, not whole-document accuracy.

Candidates and independent decisions are in `fidelity_candidates.json` and
`fidelity_repair_log.json`; `fidelity.diff` records exact content changes. Source
map offsets and hashes address the final Markdown. `trace` returns the queried
object's technical records and repairs alongside its source evidence.

Overwritten original metadata and the original manifest are kept under
`fidelity/parents/`. Unchanged assets/native evidence are shared within the result,
so rollback does not require a second full asset copy. During standard conversion
the complete raw version and publication logs are also preserved in a sibling
`.<result-name>.fidelity-convert/` transaction.

F1 failure returns nonzero and leaves a coherent raw result with
`fidelity_pending=true`. Resume the original command after addressing the cause.
Completed results are verified and reused; repairs are not applied twice.
Keep the complete result for `verify` and rollback.
