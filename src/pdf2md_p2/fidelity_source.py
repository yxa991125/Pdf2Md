"""Small source evidence catalog from PDF characters; not a document workspace."""

from statistics import median
import re

import pymupdf

from .common import digest
from .fidelity_math import UnsupportedMath, parse
from .fidelity_context import annotations


def characters(page):
    result = []
    for block in page.get_text('rawdict', flags=3)['blocks']:
        for line in block.get('lines', []):
            for span in line['spans']:
                for char in span['chars']:
                    result.append({'text': char['c'], 'bbox': list(char['bbox']),
                        'origin': list(char['origin']), 'size': span['size'],
                        'font': span['font'], 'direction': list(line['dir'])})
    return result


def _row(glyphs, anchor):
    size, y = anchor['size'], anchor['origin'][1]
    eligible = sorted((g for g in glyphs if -size*.72 <= g['origin'][1]-y <= size*.5),
                      key=lambda g: (g['bbox'][0], g['origin'][1]))
    groups = []
    for glyph in eligible:
        if not groups or glyph['bbox'][0] - max(g['bbox'][2] for g in groups[-1]) > size*2:
            groups.append([])
        groups[-1].append(glyph)
    matches = [group for group in groups if anchor in group]
    return matches[0] if len(matches) == 1 else []


def _literal(glyphs, baseline, size):
    result, script, previous = '', None, None
    for glyph in glyphs:
        text = glyph['text']
        if text.isspace():
            continue
        delta = glyph['origin'][1] - baseline
        kind = 'sub' if delta > 1.2 and glyph['size'] < size*.98 else 'sup' if delta < -size*.2 else 'base'
        if kind != 'base' and (not re.fullmatch(r'[A-Za-z0-9]', text) or not result):
            raise UnsupportedMath('Unclear script or fraction geometry')
        if kind != script:
            if script in ('sub', 'sup'):
                result += '}'
            if kind in ('sub', 'sup'):
                result += '_{' if kind == 'sub' else '^{'
            script = kind
        if previous is not None and kind == 'base' and script == 'base':
            gap = glyph['bbox'][0] - previous['bbox'][2]
            if re.fullmatch(r'[A-Za-z]', text) and result and result[-1].isalpha() and gap > size*.25:
                raise UnsupportedMath('Separated words or implicit multiplication')
        result += {'\u2212': '-', '\u2013': '-', '\u00d7': '*', '\u00b7': '*'}.get(text, text)
        previous = glyph
    if script in ('sub', 'sup'):
        result += '}'
    return result


def _fraction(glyphs, anchor, row, bars):
    x, y = anchor['bbox'][2], anchor['origin'][1]
    size = anchor['size']
    matching = [bar for bar in bars if x < bar[0] < x+size*3 and
                -size*.8 < bar[1]-y < size*.2 and size*2 < bar[2]-bar[0] < size*24]
    if not matching:
        return None
    if len(matching) != 1:
        raise UnsupportedMath('Multiple possible fraction bars')
    bar, = matching
    numerator = sorted([g for g in glyphs if bar[0]-1 <= g['bbox'][0] and g['bbox'][2] <= bar[2]+1
                        and bar[1]-size*1.8 < g['origin'][1] < bar[1]], key=lambda g: g['bbox'][0])
    denominator = sorted([g for g in glyphs if bar[0]-1 <= g['bbox'][0] and g['bbox'][2] <= bar[2]+1
                          and bar[1] < g['origin'][1] < bar[1]+size*1.5], key=lambda g: g['bbox'][0])
    left = [g for g in row if g['bbox'][0] <= anchor['bbox'][0]]
    tails = [g for g in row if abs(g['origin'][1]-y) < 1.2 and
             g['bbox'][0] > x and (g['bbox'][2] < bar[0] or g['bbox'][0] > bar[2]) and not g['text'].isspace()]
    if tails or not numerator or not denominator:
        raise UnsupportedMath('Fraction with prefix/suffix or missing component not supported')
    nbase = [g['origin'][1] for g in numerator if g['size'] >= size*.98]
    dbase = [g['origin'][1] for g in denominator if g['size'] >= size*.98]
    if not nbase or not dbase:
        raise UnsupportedMath('Fraction baseline insufficient')
    expression = (_literal(left, y, size) + r'\frac{' + _literal(numerator, median(nbase), size)
                  + '}{' + _literal(denominator, median(dbase), size) + '}')
    return expression, sorted(left+numerator+denominator, key=lambda g: (g['bbox'][0], g['bbox'][1])), bar


def formulas(page, page_number, pdf_hash):
    glyphs = characters(page)
    bars = []
    for drawing in page.get_drawings():
        for item in drawing['items']:
            if item[0] == 'l' and abs(item[1].y-item[2].y) < .1:
                bars.append([min(item[1].x, item[2].x), item[1].y, max(item[1].x, item[2].x), item[2].y])
    records, seen = [], set()
    for anchor in (g for g in glyphs if g['text'] == '='):
        row = _row(glyphs, anchor)
        if not row:
            continue
        annotated_glyphs = row
        raw_row = ''.join(g['text'] for g in row)
        _, (start, end), context = annotations(raw_row)
        position, body = 0, []
        for glyph in row:
            if start <= position and position+len(glyph['text']) <= end:
                body.append(glyph)
            position += len(glyph['text'])
        row = body
        if anchor not in row or sum(g['text'] == '=' for g in row) != 1:
            continue
        fraction = None
        fraction_error = None
        try:
            fraction = _fraction(glyphs, anchor, row, bars)
            if fraction:
                row = fraction[1]
        except UnsupportedMath as error:
            fraction_error = str(error)
        bbox = list(pymupdf.Rect(row[0]['bbox']))
        for glyph in row[1:]:
            bbox = list(pymupdf.Rect(bbox) | pymupdf.Rect(glyph['bbox']))
        key = tuple(round(v, 2) for v in bbox)
        if key in seen:
            continue
        seen.add(key)
        raw = ''.join(g['text'] for g in row)
        status, expression, name, reason = 'SOURCE_INSUFFICIENT', None, None, None
        try:
            if fraction_error:
                raise UnsupportedMath(fraction_error)
            if page.rotation or any(g['direction'] != [1.0, 0.0] for g in row):
                raise UnsupportedMath('Rotated formula requires a supported source interpretation')
            if any(not re.fullmatch(r'[A-Za-z0-9\s+\-\u2212\u2013\u00d7\u00b7*/=().]', g['text']) for g in row):
                raise UnsupportedMath('Unsupported source glyph')
            if any('symbol' in g['font'].lower() and re.search(r'[A-Za-z]', g['text']) for g in row):
                raise UnsupportedMath('Symbol-font letters need independent encoding evidence')
            expression = fraction[0] if fraction else _literal(row, anchor['origin'][1], anchor['size'])
            parsed = parse(expression)
            name = parsed.name
            # A name-only RHS is commonly prose definitions, not an arithmetic equation.
            if parsed.tree[0] in ('name', 'number'):
                raise UnsupportedMath('Definition or scalar assignment; not checked as arithmetic')
            status = 'SUPPORTED_NATIVE_GEOMETRY'
        except UnsupportedMath as error:
            reason = str(error)
        records.append({'id': 'source-formula-' + digest(f'{pdf_hash}:{page_number}:{key}:{raw}')[:20],
            'object_type': 'formula', 'physical_page': page_number, 'source_region': bbox,
            'native_text': raw, 'character_spans': row, 'source_extraction_status': status,
            'source_expression': expression, 'formula_name': name, 'reason': reason,
            'annotations': context, 'annotated_native_text': raw_row,
            'annotation_character_spans': [g for g in annotated_glyphs if g not in row],
            'fraction_bar': fraction[2] if fraction else None,
            'page_rotation': page.rotation, 'coordinate_system': 'unrotated_page_top_left_pdf_points'})
    return records
