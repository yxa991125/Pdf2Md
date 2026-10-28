# Organization And Local Revision

[中文完整说明](ORGANIZATION.zh-CN.md) | [README](../README.md)

D1 (`d1.2`) organizes a complete sealed conversion without parser execution, GPU/model setup or changes to historical inputs. Use the existing coordinator environment. The adaptive packaging workflow below targets Linux; this version does not extend Windows launcher support for its new arguments.

```bash
python3 scripts/pdf2md.py organize --from-result /data/result --workspace /data/organized --input /data/manual.pdf
python3 scripts/pdf2md.py scopes --workspace /data/organized
python3 scripts/pdf2md.py search --workspace /data/organized --scope scope-ID --query DATA_STATUS --match exact
python3 scripts/pdf2md.py search --workspace /data/organized --pages 20:30 --kind table --query ADCRANGE
python3 scripts/pdf2md.py trace --workspace /data/organized --unit-id u-ID --input /data/manual.pdf --render-dir /data/new-evidence
python3 scripts/pdf2md.py verify --workspace /data/organized
```

Original PDF is optional except for new crops and visibility inspection. Old sealed results without a map remain usable with explicit unmapped provenance. Stable UUID Units are independent of content/line numbers. Explicit, inferred and virtual Scopes do not require a table of contents. Complete tables, math, fences and confirmed atomic groups take precedence over `--soft-chars` (default 12000). Unknown material, comments and whitespace remain recoverable exactly.

Canonical content is under `revisions/rev-000001/content/`, including `content/assets/`. Asset bytes and original relative URLs are preserved. Stable anchors, reversible reference relocation, indexes and navigation are derived. `HEAD.json` selects one immutable published snapshot; imports and proposals retain persistent identity and edit history.

Filters intersect before matching. Exact search preserves case/separators and original spans, allowing only reversible Markdown escapes. `substring` and `loose` are labelled alternatives. Page mode defaults to `contained`; explicit `intersect` lists cross-boundary pages. Unknown pages are excluded. Out-of-scope context is referenced only unless `--expand-context` is given. `--revision` selects a published historical snapshot.

```bash
python3 scripts/pdf2md.py revise propose --workspace /data/organized --unit-id u-ID \
  --operation replace --text-file /data/corrected.txt --reason "Restore source expression" --proposer editor
python3 scripts/pdf2md.py revise check --workspace /data/organized --proposal /data/proposal.json
python3 scripts/pdf2md.py revise apply --workspace /data/organized --proposal /data/proposal.json --expected-revision rev-000001
python3 scripts/pdf2md.py revise history --workspace /data/organized
python3 scripts/pdf2md.py revise rollback --workspace /data/organized --to-revision rev-000001 \
  --expected-revision rev-000002 --reason "Undo revision"
python3 scripts/pdf2md.py export --workspace /data/organized --format split --output-dir /data/export
```

Use the actual proposal path returned by `propose`. Candidates bind workspace/document, expected revision and old hash, before/after content, reason, proposer, judgment method and optional source evidence. `--source-evidence` reads a JSON array with PDF hash, page and bbox matching retained candidates. Publication authorization is not human verification.

`--operation move --target-scope scope-ID` changes membership without editing body text. `--operation annotate --text-file /data/note.txt` adds a separately labelled derived note; dependency changes make it stale. Split/merge/type/heading-level changes are rejected, not hidden behind an old ID. Title changes preserve target identities and update navigation.

One writer builds and verifies the entire new snapshot before atomic HEAD replacement. Failures retain evidence and the old readable version. Rollback is a new event, never history deletion. Direct snapshot edits cause verification/query failures. `--format single` derives a whole-document export from canonical content, not an independently maintained copy.

Source regions use 1-based physical pages, unrotated top-left PDF points and Unicode offsets. Ambiguous candidates remain visible. `ASSOCIATED_NATIVE_LAYOUT` does not prove content correctness. `source-visibility --workspace ... --input ...` reports geometric text-block association, not semantic coverage. No external service, knowledge graph or new human review queue is involved.

## Adaptive Reading Files

Generate a separate reading copy first. This does not change the workspace, HEAD or historical revisions:

```bash
python3 scripts/pdf2md.py export --workspace /data/organized --format reading \
  --input /data/manual.pdf --output-dir /data/new-reading
python3 scripts/pdf2md.py verify-reading --output-dir /data/new-reading
```

The PDF is optional and hash-checked when supplied. Source lines, coordinates, font and outline evidence can support joining a fragmented display heading, without deleting or changing the original Units. Ambiguous matches remain unresolved. Missing source mappings remain missing; structure evidence is not automatically a new bbox association or a content-correctness certificate.

The export contains `content/reading-NNN.md`, assets, navigation, Unit/Scope/reference indexes, `source_map.json`, `reading_plan.json`, diagnostics and a manifest. Multiple related Scopes can share one file, and a long Scope can span several files. Whole objects and source-confirmed major boundaries outrank the adaptive soft size target. `--target-chars` optionally pins a soft target, not an exact cut size. Raising the original import's `--soft-chars` does not merge existing heading files.

After checking the reading result, explicitly publish its plan as a new structural revision:

```bash
python3 scripts/pdf2md.py revise repackage --workspace /data/organized \
  --reading-export /data/new-reading --expected-revision rev-000001 \
  --reason "Adopt checked reading layout"
python3 scripts/pdf2md.py verify --workspace /data/organized
```

Use the actual current revision returned by `verify`, not an assumed `rev-000001`. A plan from another or changed source revision is rejected. Use an independent workspace copy for a trial. Repackaging does not rewrite old revision directories or change content hashes/content revisions. It updates structure revisions, physical file intervals, links and indexes through the same writer lock and atomic publication as ordinary revisions.

Search and trace continue using Scope/Unit IDs, never file names as query boundaries. Historical Scope IDs keep their original member sets when a composite parent is added. Source-confirmed level disagreements are retained separately rather than silently rewriting historical ancestry. Later authorized moves explicitly change membership. Local edits preserve the packing policy; edits to a composed title invalidate stale display evidence instead of reusing the old title. Rollback can restore either a packed or legacy layout while retaining all published versions.
