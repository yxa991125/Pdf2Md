"""Narrow source-bound identifier repairs in uniquely mapped plain prose only."""

from bisect import bisect_right
import re

from markdown_it import MarkdownIt
import pymupdf

from .common import PAGE, digest, save, sha

RULE = 'source-unique-prose-identifier-0.1.0'


def prose_ranges(markdown):
    offsets = [0]
    for line in markdown.splitlines(keepends=True):
        offsets.append(offsets[-1] + len(line))
    output = []
    for token in MarkdownIt('commonmark', {'html': True}).parse(markdown):
        if token.type != 'paragraph_open' or token.level != 0 or token.map is None:
            continue
        a, b = offsets[token.map[0]], offsets[token.map[1]]
        text = markdown[a:b]
        if any(c in text for c in ('$', '`', '<', '>', '|')):
            continue
        output.append((a, b))
    return output


def reverse(text, log):
    if digest(text) != log['after_sha256']:
        raise ValueError('Modified output cannot be rolled back with this log')
    for row in sorted(log['operations'], key=lambda r: r['output_start'], reverse=True):
        start = row['output_start']
        if text[start:start + len(row['after'])] != row['after']:
            raise ValueError('Rollback locus mismatch')
        text = text[:start] + row['before'] + text[start + len(row['after']):]
    if digest(text) != log['before_sha256']:
        raise ValueError('Rollback did not reproduce native bytes')
    return text


def apply(pdf, native, findings, output):
    markers = list(PAGE.finditer(native))
    page_offsets = {int(m[1]): m.end() for m in markers}
    eligible = prose_ranges(native)
    planned, rejected = [], []
    for row in findings['records']:
        if row['state'] != 'TECHNICAL_IDENTIFIER_DISTORTION':
            continue
        token = row['source_identifier']
        candidate, = row['output_candidates']
        start = page_offsets[row['page']] + candidate['start']
        end = page_offsets[row['page']] + candidate['end']
        raw = candidate['text']
        safe = (not row['source_encoding_ambiguity'] and '_' in token and token.isupper()
                and not re.search(r'[\u3400-\u9fff]', row['source_context'])
                and re.sub(r'[ _]', '', raw) == token.replace('_', '')
                and any(a <= start and end <= b for a, b in eligible)
                and native[start:end] == raw)
        if not safe:
            rejected.append({'page': row['page'], 'source_identifier': token,
                             'state': 'UNSAFE_OR_UNSUPPORTED_REPAIR_REJECTED'})
            continue
        planned.append({'page': row['page'], 'native_start': start, 'native_end': end,
                        'before': raw, 'after': token.replace('_', '\\_'),
                        'source_identifier': token, 'source_bbox': row['source_bbox'],
                        'source_context': row['source_context'], 'source_lines': row['source_lines'],
                        'rule': RULE, 'decision': 'APPLIED_SOURCE_BOUND_CHANGE',
                        'independent_quality_state': 'NOT_EVALUATED'})
    planned.sort(key=lambda r: r['native_start'])
    if any(a['native_end'] > b['native_start'] for a, b in zip(planned, planned[1:])):
        raise ValueError('Overlapping repairs rejected')
    updated = native
    for row in reversed(planned):
        updated = updated[:row['native_start']] + row['after'] + updated[row['native_end']:]
    delta = 0
    with pymupdf.open(pdf) as document:
        for number, row in enumerate(planned, 1):
            row['id'] = f'identifier-{number:04d}'
            row['output_start'] = row['native_start'] + delta
            delta += len(row['after']) - len(row['before'])
            path = output / 'quality_evidence' / (row['id'] + '.png')
            path.parent.mkdir(exist_ok=True)
            page = document[row['page'] - 1]
            bbox = (pymupdf.Rect(row['source_bbox']) + (-2, -2, 2, 2)) & page.rect
            page.get_pixmap(clip=bbox, matrix=pymupdf.Matrix(2, 2), alpha=False).save(path)
            row['source_render'] = {'path': path.relative_to(output).as_posix(), 'sha256': sha(path),
                                    'bbox': list(bbox), 'scope': 'Identifier source context, not a complete formula/figure claim'}
    log = {'version': RULE, 'before_sha256': digest(native), 'after_sha256': digest(updated),
           'source_pdf_sha256': sha(pdf), 'operations': planned, 'rejected': rejected,
           'generator_does_not_certify_gain': True}
    if reverse(updated, log) != native:
        raise ValueError('Repair log not reversible')
    return updated, log
