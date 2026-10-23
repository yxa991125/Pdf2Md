"""Occurrence- and context-bound source checks. No guessed or global substitutions."""

from collections import Counter
import re

import pymupdf

from .common import page_parts

TOKEN = re.compile(r'(?<![A-Za-z0-9_])(?:[A-Za-z][A-Za-z0-9]*(?:[_-][A-Za-z0-9]+)+|[A-Z]{2,}\d+[A-Z0-9]*|0[xX][0-9A-Fa-f]+|\[\d+:\d+\])(?![A-Za-z0-9_])')


def words(text):
    text = re.sub(r'<[^>]*>', ' ', text)
    text = text.replace('\\_', '_')
    return re.findall(r'[A-Za-z0-9_]+', text)


def source_blocks(page):
    for block in page.get_text('dict')['blocks']:
        if not block.get('lines'):
            continue
        lines = [''.join(s['text'] for s in line['spans']) for line in block['lines']]
        text = '\n'.join(lines)
        # A visible terminal underscore is an explicit joining character, not an inferred one.
        text = re.sub(r'(?<=[A-Za-z0-9])_\s*\n\s*(?=[A-Z0-9])', '_', text)
        yield {'text': re.sub(r'\s+', ' ', text), 'bbox': list(block['bbox']), 'source_lines': lines}


def pattern(token):
    if '_' not in token and '-' not in token:
        if re.fullmatch(r'[A-Z]{2,}\d+[A-Z0-9]*', token):
            return r'[ \t]*'.join(map(re.escape, re.findall(r'[A-Z]+|\d+', token)))
        return re.escape(token)
    return r'(?:\\?_|[ \t]+|-|)'.join(map(re.escape, re.split(r'[_-]', token)))


def inspect_block(source, markdown, page):
    rows = []
    for occurrence in TOKEN.finditer(source['text']):
        token = occurrence[0]
        left = words(source['text'][:occurrence.start()])[-3:]
        right = words(source['text'][occurrence.end():])[:3]
        matches = []
        for match in re.finditer(r'(?<![\w])' + pattern(token) + r'(?![\w])', markdown, re.I):
            before, after = words(markdown[:match.start()]), words(markdown[match.end():])
            left_ok = len(left) >= 2 and before[-len(left):] == left
            right_ok = len(right) >= 2 and after[:len(right)] == right
            # Both flanks when available, never heading presence as paragraph correctness.
            bound = ((left_ok and (right_ok or len(right) < 2)) or
                     (right_ok and len(left) < 2))
            if bound:
                matches.append({'start': match.start(), 'end': match.end(), 'text': match[0]})
        state = 'TECHNICAL_IDENTIFIER_UNRESOLVED'
        if len(matches) == 1:
            state = 'SOURCE_OCCURRENCE_PRESERVED' if matches[0]['text'].replace('\\_', '_') == token else 'TECHNICAL_IDENTIFIER_DISTORTION'
        rows.append({'page': page, 'source_identifier': token, 'source_bbox': source['bbox'],
                     'source_context': source['text'], 'source_lines': source['source_lines'],
                     'source_offset': [occurrence.start(), occurrence.end()], 'left_context': left,
                     'right_context': right, 'output_candidates': matches, 'state': state,
                     'automatic_action': 'NONE', 'evidence_level': 'SOURCE_TEXT_AND_LOCAL_CONTEXT',
                     'source_encoding_ambiguity': '\ufffd' in source['text']})
    return rows


def check(pdf, markdown):
    parts, records = page_parts(markdown), []
    with pymupdf.open(pdf) as document:
        for number, text in parts.items():
            for block in source_blocks(document[number - 1]):
                records.extend(inspect_block(block, text, number))
    owners = Counter((r['page'], r['output_candidates'][0]['start'], r['output_candidates'][0]['end'])
                     for r in records if len(r['output_candidates']) == 1)
    for row in records:
        if len(row['output_candidates']) == 1:
            match = row['output_candidates'][0]
            if owners[row['page'], match['start'], match['end']] > 1:
                row['state'] = 'TECHNICAL_IDENTIFIER_UNRESOLVED'
                row['mapping_warning'] = 'Multiple source occurrences claim the same output location'
    return {'version': 'identifier-check-0.1.0', 'counts': dict(Counter(r['state'] for r in records)),
            'records': records, 'applied_repairs': [],
            'scope': 'Source-layer identifiers and local occurrences; unresolved mappings are not accepted or silently repaired'}
