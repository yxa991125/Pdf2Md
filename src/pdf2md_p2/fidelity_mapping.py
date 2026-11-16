"""Rebind final Markdown offsets without parsing the PDF or Worker again."""

from collections import Counter
import copy

import pymupdf

from .assets import references
from .common import digest, save, sha
from .source_map import objects, validate


def rebind(pdf, output, original_mapping, log, candidates):
    text = (output / 'manual.md').read_text(encoding='utf-8')
    if digest(text) != log['after_sha256'] or original_mapping['markdown_sha256'] != log['before_sha256']:
        raise ValueError('Mapping or repair version differs')
    if sha(pdf) != original_mapping['input_sha256']:
        raise ValueError('Mapping PDF differs')
    planned = {c['candidate_id']: c for c in candidates}
    edits = sorted(log['operations'], key=lambda r: r['original_span'])

    def translate(position, end=False):
        delta = 0
        for edit in edits:
            a, b = edit['original_span']
            if a < position < b:
                raise ValueError('A final output boundary falls inside a repaired expression')
            if position >= b:
                delta += len(edit['after'])-len(edit['before'])
        return position+delta

    original = {(r['page'], r['kind'], translate(r['start']), translate(r['end'], True)): r
                for r in original_mapping['objects']}
    records = objects(text)
    with pymupdf.open(pdf) as document:
        for row in records:
            key = row['page'], row['kind'], row['start'], row['end']
            previous = original.get(key)
            if previous is None:
                raise ValueError('Repair changed output object boundaries or kind unexpectedly')
            applied = [edit for edit in edits if row['start'] <= edit['output_start']
                       and edit['output_start']+len(edit['after']) <= row['end']]
            sources = copy.deepcopy(previous['sources'])
            status = previous['status']
            if applied:
                sources = []
                for edit in applied:
                    source = planned[edit['candidate_id']]['source_evidence']
                    page = document[source['physical_page']-1]
                    page.set_rotation(0)
                    sources.append({'page': source['physical_page'], 'bbox': source['source_region'],
                        'source_text': page.get_text(clip=pymupdf.Rect(source['source_region'])),
                        'basis': 'INDEPENDENT_F1_SOURCE_GLYPH_VERIFICATION',
                        'fidelity_candidate_id': edit['candidate_id']})
                status = 'ASSOCIATED_NATIVE_LAYOUT' if len(sources) == 1 else 'AMBIGUOUS'
            row.update(id='obj-' + digest(f"{original_mapping['input_sha256']}:{row['page']}:{row['kind']}:{row['start']}:{row['end']}")[:20],
                line_start=text.count('\n', 0, row['start'])+1,
                line_end=text.count('\n', 0, max(row['start'], row['end']-1))+1,
                status=status, sources=sources, semantic_correctness='NOT_JUDGED')
    mapping = {k: copy.deepcopy(v) for k, v in original_mapping.items() if k not in ('objects', 'states', 'markdown_sha256')}
    if set(references(text)) != set(mapping['assets']):
        raise ValueError('Fidelity edit changed image references')
    mapping.update(objects=records, states=dict(Counter(r['status'] for r in records)),
                   markdown_sha256=digest(text))
    save(output / 'source_map.json', mapping)
    validate(output)
    return mapping
