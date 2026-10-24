# Source Mapping

[简体中文](SOURCE_MAPPING.zh-CN.md) | [README](../README.md)

New conversions include `source_map.json` to reduce the work of locating original evidence during testing and verification. It is an index, not a semantic document model or an automatic approval decision.

## Query

```bash
python scripts/pdf2md.py trace --output-dir ../result --line 25
python scripts/pdf2md.py trace --output-dir ../result --object-id OBJECT_ID
```

Output is JSON. Each object contains `id`, `kind`, `text`, `start`, `end`, `line_start`, `line_end`, `page`, `status` and `sources`. Character intervals are half-open Unicode code point offsets into the exact final `manual.md`, not UTF-8 bytes or UTF-16 code units. Lines and physical PDF pages are 1-based.

Each source has a `bbox` in PDF points, top-left origin on the unrotated page, source text and association basis. Page dimensions and original rotation are in `pages`. Multiple sources are candidates; do not assume the first one is correct.

| Status | Meaning |
| --- | --- |
| `MATCHED_SOURCE_TEXT` | Unique output occurrence matched an independently read PDF text block |
| `ASSOCIATED_NATIVE_LAYOUT` | Unique native layout or recorded source-operation association; content may still be incorrect |
| `AMBIGUOUS` | Multiple candidates, repeated output occurrences, or conflicting location evidence |
| `UNMAPPED` | No reliable source region found |

Objects cover headings, paragraphs, list items, whole tables, represented formulas and images. A formula serialized as plain prose remains a paragraph; image-based content is not converted to editable symbols by this index. Mapping is not cell-level, symbol-level or whole-document completeness certification.

## Requested Evidence

```bash
python scripts/pdf2md.py trace --output-dir ../result --line 25 \
  --input ../manual.pdf --render-dir ../evidence-line25
```

The exact PDF hash must match. The new evidence directory must be outside the sealed result and product, and must not contain the source PDF. Screenshots use unrotated source coordinates and list their hashes in `evidence.json`. Ambiguous objects export all available candidates without selecting a winner; unmapped objects have no crop. Existing evidence directories are not overwritten.

Conversion does not copy the PDF or generate screenshots. Queries verify the sealed output and mapping bindings before returning evidence. `verify` checks index coverage, intervals, IDs, coordinates and asset hashes, not technical correctness. Older outputs without a map remain verifiable but cannot be queried with `trace`; they are not modified.
