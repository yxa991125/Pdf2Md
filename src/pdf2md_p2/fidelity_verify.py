"""Re-read PDF glyphs to independently accept/reject a bounded linear repair.

This module does not import the assessment, source extractor, math parser or
candidate generator. It verifies an exact glyph/script ledger and edit bounds.
"""

import re

import pymupdf

from .common import digest, sha


def _glyphs(page):
    result = []
    for block in page.get_text('rawdict', flags=3)['blocks']:
        for line in block.get('lines', []):
            for span in line['spans']:
                for char in span['chars']:
                    result.append({'text': char['c'], 'bbox': list(char['bbox']), 'origin': list(char['origin']),
                                   'font': span['font'], 'size': span['size'], 'direction': list(line['dir'])})
    return result


def _script_tokens(glyphs, baseline, size):
    tokens, active = [], None
    for glyph in sorted(glyphs, key=lambda g: (g['bbox'][0], g['origin'][1])):
        text = glyph['text']
        if text.isspace():
            continue
        if not re.fullmatch(r'[A-Za-z0-9+\-\u2212\u00d7*/=().]', text):
            raise ValueError('Independent verifier does not support this source glyph')
        delta = glyph['origin'][1] - baseline
        if abs(delta) <= 1.0:
            kind = 'base'
        elif 1.5 <= delta <= size*.5 and glyph['size'] < size*.96 and text.isalnum():
            kind = 'sub'
        elif -size*.7 <= delta <= -size*.25 and glyph['size'] < size*.96 and text.isalnum():
            kind = 'sup'
        else:
            raise ValueError('Independent script or fraction interpretation ambiguous')
        if kind != active:
            if active in ('sub', 'sup'):
                tokens.append('}')
            if kind in ('sub', 'sup'):
                tokens += ['_' if kind == 'sub' else '^', '{']
            active = kind
        tokens.append({'\u2212': '-', '\u00d7': '*'}.get(text, text))
    if active in ('sub', 'sup'):
        tokens.append('}')
    return tokens


def _candidate_tokens(text):
    text = re.sub(r'\s+', '', text)
    text = text.replace(r'\times', '*')
    if '\\' in text or not re.fullmatch(r'[A-Za-z0-9+*/=()._{}^\-]+', text):
        raise ValueError('Candidate outside independently verifiable canonical syntax')
    if text.count('=') != 1:
        raise ValueError('Candidate assignment changed')
    stack = []
    for i, char in enumerate(text):
        if char == '{' and (i == 0 or text[i-1] not in '_^'):
            raise ValueError('Unexpected candidate grouping')
        if char in '({':
            stack.append(char)
        elif char in ')}':
            if not stack or stack.pop() != ( '(' if char == ')' else '{'):
                raise ValueError('Unbalanced candidate')
    if stack:
        raise ValueError('Incomplete candidate')
    return list(text)


def _lhs(tokens):
    return ''.join(tokens[:tokens.index('=')])


def _same_lhs_count(glyphs, target):
    count = 0
    for anchor in (g for g in glyphs if g['text'] == '='):
        size, baseline = anchor['size'], anchor['origin'][1]
        left = sorted([g for g in glyphs if -size*.7 <= g['origin'][1]-baseline <= size*.5
                       and anchor['bbox'][0]-size*18 < g['bbox'][0] < anchor['bbox'][0]], key=lambda g: g['bbox'][0])
        tail = []
        for glyph in reversed(left):
            if tail and min(g['bbox'][0] for g in tail)-glyph['bbox'][2] > size*1.7:
                break
            tail.append(glyph)
        try:
            tokens = _script_tokens(tail+[anchor], baseline, size)
            if _lhs(tokens) == target:
                count += 1
        except ValueError:
            continue
    return count


def verify_candidate(pdf, original, candidate):
    evidence = {'verifier': 'independent-native-glyph-ledger-1', 'decision': 'REJECTED',
                'candidate_id': candidate.get('candidate_id')}
    try:
        if (candidate['source_version'] != 'fidelity-1.0.0-dev1'
            or candidate['candidate_generator'] != 'native-linear-formula-1'
            or candidate['target_output_version'] != digest(original)):
            raise ValueError('Unsupported candidate or target version')
        if sha(pdf) != candidate['source_document_hash'] or digest(original) != candidate['target_markdown_sha256']:
            raise ValueError('Source or target version changed')
        a, b = candidate['allowed_change_span']
        if not (0 <= a < b <= len(original)) or original[a:b] != candidate['before_text']:
            raise ValueError('Candidate target locus differs')
        if digest(original[a:b]) != candidate['expected_old_hash']:
            raise ValueError('Original object hash differs')
        source = candidate['source_evidence']
        with pymupdf.open(pdf) as document:
            page = document[source['physical_page']-1]
            if page.rotation or source.get('fraction_bar'):
                raise ValueError('This independent repair verifier supports unrotated linear formulas')
            glyphs = _glyphs(page)
            region = pymupdf.Rect(source['source_region'])
            selected = [g for g in glyphs if region.contains(pymupdf.Rect(g['bbox']))]
            key = lambda g: (g['text'], tuple(g['bbox']), tuple(g['origin']), g['font'], g['size'])
            if sorted(map(key, selected)) != sorted(map(key, source['character_spans'])):
                raise ValueError('Declared source region omits or substitutes PDF characters')
            anchors = [g for g in selected if g['text'] == '=']
            if len(anchors) != 1:
                raise ValueError('Source assignment ambiguous')
            anchor, = anchors
            expected = _script_tokens(selected, anchor['origin'][1], anchor['size'])
            target = _lhs(expected)
            if _same_lhs_count(glyphs, target) != 1:
                raise ValueError('Multiple or unconfirmed source assignments')
            before_lhs = original[a:b].split('=', 1)[0]
            before_lhs = re.sub(r'\\(?:mathsf|mathrm|mathbf|text|textrm|operatorname)\b', '', before_lhs)
            before_lhs = re.sub(r'[\s{}]', '', before_lhs)
            if before_lhs != target.replace('{', '').replace('}', ''):
                raise ValueError('Candidate targets a different formula')
            observed = _candidate_tokens(candidate['after_text'])
            if expected != observed:
                raise ValueError('Repaired formula differs from independent PDF glyph/script ledger')
            if '-' not in expected or re.search(r'[-\u2212\u2013]', candidate['before_text']) or r'\_' not in candidate['before_text']:
                raise ValueError('Original subtraction-loss defect not independently demonstrated')
            evidence.update(decision='ACCEPTED', source_tokens=expected,
                candidate_tokens=observed, source_page=source['physical_page'], source_region=list(region),
                source_document_hash=sha(pdf), original_object_sha256=digest(original[a:b]),
                repaired_object_sha256=digest(candidate['after_text']),
                checked='Complete linear formula glyph order, literal scripts, operators, target and source versions')
    except (ValueError, KeyError, IndexError, TypeError) as error:
        evidence['reason'] = str(error)
    return evidence
