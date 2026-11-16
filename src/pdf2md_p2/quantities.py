"""Source-line quantities paired within output objects, never by expected values."""

from collections import Counter
from decimal import Decimal, InvalidOperation
from html.parser import HTMLParser
import re

from markdown_it import MarkdownIt
import pymupdf

from .common import PAGE, digest, page_parts, sha
from .identifiers import words

VERSION = 'source-quantity-check-0.2.1'
NUMBER = r'(?:[+\-\u2212\u2013\u00b1][ \t\r\n]*)?(?:\d+(?:\.\d+)?|\.\d+)(?:[eE][+\-\u2212]?\d+)?'
UNIT = (r'(?:GHz|MHz|kHz|Hz|mV|uV|\u00b5V|\u03bcV|V|mA|uA|\u00b5A|\u03bcA|A|'
        r'mg|ug|\u00b5g|\u03bcg|g|ns|us|\u00b5s|\u03bcs|ms|s|pF|nF|uF|\u00b5F|\u03bcF|F|'
        r'\u00b0C|\u00b0F|k\u03a9|\u03a9|%)(?:/(?:LSB|\u00b0C|W|Hz|s))?')
# Consume malformed numeric runs as a whole: never fall back to their unsigned tail.
_RAW = r'[+\-\u2212\u2013\u2014\u00b1]?[ \t\r\n]*(?:\d|\.\d)[\d.eE+\-\u2212\u2013\u2014\u00b1 \t]*'
QUANTITY = re.compile(r'(?P<number>' + _RAW + r'(?:to[ \t]*' + _RAW + r')?)[ \t]*(?P<unit>' + UNIT + r')(?!\w)')
_EXPRESSION = re.compile(r'(?P<low>' + NUMBER + r')(?:[ \t]*(?:to|[\-\u2013\u2014])[ \t]*(?P<high>' + NUMBER + r'))?')
_NORMALIZE = str.maketrans({'\u2212': '-', '\u2013': '-', '\u00b5': 'u', '\u03bc': 'u'})


def _values(raw):
    match = _EXPRESSION.fullmatch(raw.strip())
    if not match:
        return None
    values, signs = [], []
    for name in ('low', 'high'):
        if match[name] is None:
            continue
        text = re.sub(r'[ \t\r\n]', '', match[name].translate(_NORMALIZE))
        signs.append('PLUS_MINUS' if text.startswith('\u00b1') else '-' if text.startswith('-') else '+' if text.startswith('+') else '')
        try:
            value = Decimal(text.removeprefix('\u00b1'))
        except InvalidOperation:
            return None
        if not value.is_finite():
            return None
        values.append(str(value))
    return values, signs


def extract(text, *, page=None, region=None, parent_object=None):
    """Retain original codepoint spans and explicitly represent unsupported syntax."""
    result = []
    for match in QUANTITY.finditer(text):
        start, end = match.span()
        while start < end and text[start].isspace():
            start += 1
        prefix = text[:start]
        if prefix and (prefix[-1].isalnum() or prefix[-1] in '_+-\u2212\u00b1'):
            continue
        if prefix.endswith('.'):
            leader = re.search(r'\.+$', prefix)[0]
            before = prefix[:-len(leader)]
            if len(leader) < 2 or before and before[-1].isdigit():
                continue
        parsed = _values(match['number'])
        status = 'SUPPORTED' if parsed else 'UNSUPPORTED'
        # A named voltage minus an offset is an expression, not a negative limit.
        if parsed and match['number'].lstrip().startswith(('-', '\u2212', '\u2013')):
            if re.fullmatch(r'\s*[A-Z][A-Z0-9_]*\s*', prefix):
                status = 'CONTEXT_REQUIRED'
        if parsed and re.match(r'[-\u2013][ \t]+\d', text[start:end]) and not prefix.split('\n')[-1].strip():
            # A line-leading spaced dash can be a list marker. Do not certify it
            # as a unary sign without a richer layout association.
            status = 'CONTEXT_REQUIRED'
        result.append({'raw_text': text[start:end], 'raw_span': [start, end],
            'value': parsed[0] if parsed else None, 'sign': parsed[1] if parsed else None,
            'unit': match['unit'], 'page': page, 'region': region,
            'parent_object': parent_object, 'local_context': text,
            'parse_status': status, 'quantity_kind': 'RANGE' if parsed and len(parsed[0]) == 2 else 'SCALAR'})
    return result


def equivalent(source, actual):
    a, b = extract(source.strip()), extract(actual.strip())
    if len(a) != 1 or len(b) != 1:
        return None
    a, b = a[0], b[0]
    if any(r['parse_status'] != 'SUPPORTED' or r['raw_span'] != [0, len(t.strip())]
           for r, t in ((a, source), (b, actual))):
        return None
    return (list(map(Decimal, a['value'])) == list(map(Decimal, b['value']))
        and [s == 'PLUS_MINUS' for s in a['sign']] == [s == 'PLUS_MINUS' for s in b['sign']]
        and a['unit'].translate(_NORMALIZE) == b['unit'].translate(_NORMALIZE))


def _signature(text, values):
    for value in reversed(values):
        a, b = value['raw_span']
        text = text[:a] + ' ' + text[b:]
    # Do not anchor on tested numbers or units; preserve named conditions and identifiers.
    return tuple(w.lower() for w in words(text) if w.lower() not in ('to',))


class _Cells(HTMLParser):
    def __init__(self, text):
        super().__init__(convert_charrefs=False)
        self.text, self.cells, self.start = text, [], None
        self.lines = [0]
        for line in text.splitlines(keepends=True):
            self.lines.append(self.lines[-1] + len(line))

    def position(self):
        line, column = self.getpos()
        return self.lines[line-1] + column

    def handle_starttag(self, tag, attrs):
        if tag in ('td', 'th'):
            self.start = self.position() + len(self.get_starttag_text())

    def handle_endtag(self, tag):
        if tag in ('td', 'th') and self.start is not None:
            self.cells.append((self.start, self.position()))
            self.start = None


def output_segments(text):
    """Get actual table cells and prose ranges using Markdown/HTML parsers."""
    lines = [0]
    for line in text.splitlines(keepends=True):
        lines.append(lines[-1] + len(line))
    parser = MarkdownIt('commonmark', {'html': True}).enable('table')
    ranges, row, cursor = [], None, 0
    for token in parser.parse(text):
        if token.type == 'tr_open' and token.map:
            row = (lines[token.map[0]], lines[token.map[1]])
            cursor = row[0]
        elif token.type == 'tr_close':
            row = None
        elif token.type == 'inline' and row is not None and token.content:
            a = text.find(token.content, cursor, row[1])
            if a >= 0:
                ranges.append((a, a + len(token.content), 'PIPE_CELL'))
                cursor = a + len(token.content)
        elif token.type == 'inline' and token.map:
            ranges.append((lines[token.map[0]], lines[token.map[1]], 'PROSE'))
        elif token.type == 'html_block':
            cells = _Cells(token.content)
            cells.feed(token.content)
            start = lines[token.map[0]]
            ranges.extend((start + a, start + b, 'HTML_CELL') for a, b in cells.cells)
    return [{'start': a, 'end': b, 'text': text[a:b], 'kind': kind}
            for a, b, kind in ranges if b > a]


def source_segments(page):
    result = []
    for index, block in enumerate(page.get_text('dict', flags=3)['blocks']):
        lines = block.get('lines', [])
        block_text = '\n'.join(''.join(s['text'] for s in line['spans']) for line in lines)
        scale = max((s['size'] for line in lines for s in line['spans']), default=0)
        aligned = bool(lines) and all(list(line['dir']) == [1.0, 0.0] for line in lines) and (
            max(line['bbox'][0] for line in lines)-min(line['bbox'][0] for line in lines) < scale*1.5)
        spans = [{'text': s['text'], 'bbox': list(s['bbox']), 'origin': list(s['origin']),
                  'font': s['font'], 'size': s['size']} for line in lines for s in line['spans']]
        if aligned and re.search(r'\u00b1[ \t]*\n[ \t]*(?:\d|\.\d)', block_text) and not re.search(r'\.{3,}', block_text):
            # The explicit unary sign belongs to the following line. Promote this
            # aligned paragraph, retaining its exact newline/codepoint evidence.
            result.append({'text': block_text, 'bbox': list(block['bbox']), 'direction': [1.0, 0.0],
                           'block_index': index, 'block_text': block_text, 'block_offset': 0,
                           'character_spans': spans, 'source_boundary': 'ALIGNED_BLOCK_WITH_WRAPPED_UNARY_SIGN'})
            continue
        offset = 0
        for line in block.get('lines', []):
            text = ''.join(span['text'] for span in line['spans'])
            result.append({'text': text, 'bbox': list(line['bbox']), 'direction': list(line['dir']),
                           'block_index': index, 'block_text': block_text, 'block_offset': offset,
                           'character_spans': [{'text': s['text'], 'bbox': list(s['bbox']),
                                                'origin': list(s['origin']), 'font': s['font'], 'size': s['size']}
                                               for s in line['spans']]})
            offset += len(text)+1
    return result


def check(pdf, markdown):
    parts, records = page_parts(markdown), []
    pdf_hash = sha(pdf)
    offsets = {int(m[1]): m.end() for m in PAGE.finditer(markdown)}
    with pymupdf.open(pdf) as document:
        for number, text in parts.items():
            output = []
            for segment in output_segments(text):
                values = extract(segment['text'])
                output.append({**segment, 'values': values, 'signature': _signature(segment['text'], values)})
            source = []
            for segment in source_segments(document[number-1]):
                parent = 'quantity-source-' + digest(f"{pdf_hash}:{number}:{segment['bbox']}:{segment['text']}")[:20]
                values = extract(segment['text'], page=number, region=segment['bbox'], parent_object=parent)
                preceding = segment['block_text'][:segment['block_offset']]
                if re.search(r'[+\-\u2212\u00b1][ \t]*\n$', preceding):
                    for item in values:
                        if not segment['text'][:item['raw_span'][0]].strip():
                            item['parse_status'] = 'CONTEXT_REQUIRED'
                            item['boundary_note'] = 'SIGN_IN_PREVIOUS_SOURCE_LINE_NOT_IN_THIS_FRAGMENT'
                if values:
                    source.append({**segment, 'values': values, 'signature': _signature(segment['text'], values)})
            source_owners = Counter(s['signature'] for s in source)
            block_sources = {}
            for segment in source:
                if segment['block_index'] not in block_sources and not re.search(r'\.{3,}', segment['block_text']):
                    values = extract(segment['block_text'])
                    block_sources[segment['block_index']] = {'values': values,
                        'signature': _signature(segment['block_text'], values)}
            block_owners = Counter(s['signature'] for s in block_sources.values())
            block_owner_ids = {}
            for index, block in block_sources.items():
                block_owner_ids.setdefault(block['signature'], set()).add(index)
            claimed = set()
            for segment in source:
                signature = segment['signature']
                matches = [s for s in output if len(signature) >= 2 and s['signature'] == signature]
                # A matching line inside a longer paragraph does not establish
                # quantity slots: a missed formatted unit can shift every ordinal.
                # Wrapped prose is handled below by a whole-source-block match.
                competing_blocks = block_owner_ids.get(signature, set()) - {segment['block_index']}
                unique = len(matches) == 1 and source_owners[signature] == 1 and not competing_blocks
                candidate = matches[0] if unique else None
                count_equal = bool(candidate) and len(candidate['values']) == len(segment['values'])
                block_pair = None
                if not (unique and count_equal) and segment['block_index'] in block_sources:
                    block = block_sources[segment['block_index']]
                    block_matches = [s for s in output if s['kind'] == 'PROSE' and len(block['signature']) >= 4
                                     and s['signature'] == block['signature']]
                    if len(block_matches) == 1 and block_owners[block['signature']] == 1 and len(
                            block_matches[0]['values']) == len(block['values']):
                        block_pair = block, block_matches[0]
                for ordinal, item in enumerate(segment['values']):
                    state, assessment = 'QUANTITY_CONTEXT_UNRESOLVED', 'ALIGNMENT_AMBIGUOUS' if matches else 'NOT_CHECKED'
                    actual, location = [], None
                    if item['parse_status'] != 'SUPPORTED':
                        assessment = 'SOURCE_INSUFFICIENT'
                    else:
                        paired, observed, basis = None, None, None
                        if unique and count_equal:
                            paired, observed = candidate, candidate['values'][ordinal]
                            basis = 'UNIQUE_TEXTUAL_PARENT_NO_EXPECTED_VALUE'
                        elif block_pair:
                            block, paired = block_pair
                            span = [v+segment['block_offset'] for v in item['raw_span']]
                            slots = [i for i, value in enumerate(block['values']) if value['raw_span'] == span]
                            if len(slots) == 1:
                                observed = paired['values'][slots[0]]
                                basis = 'UNIQUE_SOURCE_BLOCK_AND_OUTPUT_PARAGRAPH_NO_EXPECTED_VALUE'
                        if observed is not None:
                            start = offsets[number] + paired['start'] + observed['raw_span'][0]
                            end = offsets[number] + paired['start'] + observed['raw_span'][1]
                            key = (start, end)
                            if key not in claimed and observed['parse_status'] == 'SUPPORTED':
                                claimed.add(key)
                                actual = [observed['raw_text']]
                                matched = equivalent(item['raw_text'], observed['raw_text'])
                                state = 'QUANTITY_SOURCE_CONTEXT_MATCH' if matched else 'QUANTITY_CONTEXT_DISTORTION'
                                assessment = 'SOURCE_MATCH' if matched else 'SOURCE_MISMATCH'
                                location = {'start': start, 'end': end, 'offset_unit': 'unicode_codepoint',
                                            'object_span': [offsets[number] + paired['start'], offsets[number] + paired['end']],
                                            'kind': paired['kind']}
                    records.append({'page': number, 'source_bbox': segment['bbox'],
                        'source_quantity': item['raw_text'], 'source_context': segment['text'],
                        'actual_candidates': actual, 'state': state, 'automatic_rewrite': False,
                        'assessment_status': assessment, 'quantity': item,
                        'source_document_hash': pdf_hash, 'source_character_spans': segment['character_spans'],
                        'source_direction': segment['direction'], 'output_location': location,
                        'alignment_basis': basis if location else None,
                        'candidate_parent_count': len(matches)})
    states = dict(Counter(r['state'] for r in records))
    assessments = dict(Counter(r['assessment_status'] for r in records))
    return {'version': VERSION, 'records': records, 'states': states, 'assessment_states': assessments,
        'denominator': len(records), 'reliably_paired': sum(r['output_location'] is not None for r in records),
        'ambiguous': assessments.get('ALIGNMENT_AMBIGUOUS', 0),
        'unsupported': sum(r['quantity']['parse_status'] != 'SUPPORTED' for r in records),
        'quantity_correctness_not_global_semantic_certification': True}
