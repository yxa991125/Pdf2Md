"""Lossless information units and evidence-labelled scopes, without PDF inference."""

from bisect import bisect_right
from collections import Counter
from copy import deepcopy
from html.parser import HTMLParser
import re
from uuid import uuid4

from markdown_it import MarkdownIt

from .common import PAGE


MD = MarkdownIt('default', {'html': True, 'linkify': False})
_MATH_OPEN = re.compile(r'^\s*(\$\$|\\\[|\\begin\{(equation\*?|align\*?|gather\*?|multline\*?|displaymath)\})')
_BOLD_HEADING = re.compile(r'^(?:\*\*(.+)\*\*|__(.+)__)\s*$')
_NUMBERED_HEADING = re.compile(r'^(\d+(?:\.\d+)+)\s+([^\n]{1,140})$')
_LOCATION_STATES = {'UNMAPPED', 'AMBIGUOUS', 'MATCHED_SOURCE_TEXT', 'ASSOCIATED_NATIVE_LAYOUT'}


def _identity(prefix):
    return prefix + uuid4().hex


def _lines(text):
    return re.findall(r'[^\r\n]*(?:\r\n|\r|\n)|[^\r\n]+\Z', text)


def _line_offsets(text):
    offsets = [0]
    for line in _lines(text):
        offsets.append(offsets[-1] + len(line))
    return offsets


def _markdown_spans(text, offsets):
    spans, consumed = [], -1
    tokens = MD.parse(text)
    for index, token in enumerate(tokens):
        if token.map is None:
            continue
        a, b = (offsets[number] for number in token.map)
        if a < consumed:
            continue
        allowed = token.level == 0 or (token.type == 'list_item_open' and token.level == 1)
        if not allowed or token.type not in {
                'heading_open', 'paragraph_open', 'table_open', 'html_block', 'fence',
                'code_block', 'hr', 'blockquote_open', 'list_item_open'}:
            continue
        kinds = {'heading_open': 'heading', 'paragraph_open': 'paragraph',
                 'table_open': 'table', 'list_item_open': 'list_item'}
        span = {'start': a, 'end': b, 'kind': kinds.get(token.type, 'raw_block'),
                'structure_basis': 'MARKDOWN_' + token.type.upper(), 'hints': []}
        if token.type == 'heading_open':
            span['heading'] = {'title': tokens[index + 1].content, 'level': int(token.tag[1:]),
                               'type': 'EXPLICIT_STRUCTURE', 'basis': 'FINAL_MARKDOWN_HEADING'}
        if token.type == 'paragraph_open':
            children = tokens[index + 1].children or []
            nonempty = [child for child in children if child.type not in ('softbreak', 'hardbreak')
                        and (child.type != 'text' or child.content.strip())]
            if nonempty and all(child.type == 'image' for child in nonempty):
                span['kind'] = 'image'
        spans.append(span)
        consumed = b
    return spans


class _Containers(HTMLParser):
    """HTML block tokens end at blank lines; paired containers must not."""

    protected_tags = {'table', 'figure', 'div', 'details', 'pre', 'script', 'style', 'section'}

    def __init__(self, text, offsets, excluded):
        super().__init__(convert_charrefs=False)
        self.text, self.offsets, self.excluded = text, offsets, excluded
        self.opened, self.spans = [], []

    def _position(self):
        line, column = self.getpos()
        return self.offsets[line - 1] + column

    def handle_starttag(self, tag, attrs):
        position = self._position()
        if tag not in self.protected_tags or any(a <= position < b for a, b in self.excluded):
            return
        self.opened.append((tag, position))

    def handle_endtag(self, tag):
        if not self.opened or tag not in self.protected_tags:
            return
        matches = [index for index, (name, _) in enumerate(self.opened) if name == tag]
        if not matches:
            return
        index = matches[-1]
        name, start = self.opened[index]
        del self.opened[index:]
        end = self.text.find('>', self._position())
        if end >= 0:
            self.spans.append({'start': start, 'end': end + 1,
                               'kind': 'table' if name == 'table' else 'raw_block',
                               'structure_basis': 'PAIRED_HTML_' + name.upper(), 'hints': []})

    def finish(self):
        for tag, start in self.opened:
            self.spans.append({'start': start, 'end': len(self.text), 'kind': 'raw_block',
                               'structure_basis': 'UNCLOSED_HTML_CONTAINER',
                               'hints': [{'type': 'UNRESOLVED_CONTAINER', 'tag': tag}]})
        return self.spans


def _literal_spans(text, offsets):
    return [(offsets[token.map[0]], offsets[token.map[1]]) for token in MD.parse(text)
            if token.type in ('fence', 'code_block') and token.map is not None]


def _protected_spans(text, offsets, excluded):
    html = _Containers(text, offsets, excluded)
    html.feed(text)
    protected = html.finish()
    lines, index = _lines(text), 0
    while index < len(lines):
        a = offsets[index]
        if any(start <= a < end for start, end in excluded):
            index += 1
            continue
        match = _MATH_OPEN.match(lines[index])
        if not match:
            index += 1
            continue
        closer = '$$' if match[1] == '$$' else r'\]' if match[1] == r'\[' else '\\end{' + match[2] + '}'
        finish = index
        tail = lines[index][match.end():]
        while closer not in tail and finish + 1 < len(lines):
            finish += 1
            tail = lines[finish]
        closed = closer in tail
        protected.append({'start': a, 'end': offsets[finish + 1],
                          'kind': 'formula' if closed else 'raw_block',
                          'structure_basis': 'MATH_DELIMITERS' if closed else 'UNCLOSED_MATH_CONTAINER',
                          'hints': [] if closed else [{'type': 'UNRESOLVED_CONTAINER', 'delimiter': match[1]}]})
        index = finish + 1
    return protected


def _validate_rows(mapping, text):
    if mapping is None:
        return []
    if not isinstance(mapping, dict) or not isinstance(mapping.get('objects', []), list):
        raise ValueError('Source mapping must contain an object list')
    if mapping.get('offset_unit', 'unicode_codepoint') != 'unicode_codepoint':
        raise ValueError('Source mapping offsets must use Unicode codepoints')
    rows = mapping.get('objects', [])
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError('Source mapping object must be a dictionary')
        start, end = row.get('start'), row.get('end')
        if type(start) is not int or type(end) is not int or not 0 <= start < end <= len(text):
            raise ValueError('Source mapping object has invalid output offsets')
        if 'text' in row and row['text'] != text[start:end]:
            raise ValueError('Source mapping excerpt differs from final Markdown')
        if row.get('status', 'UNMAPPED') not in _LOCATION_STATES:
            raise ValueError('Unsupported source mapping location state')
    return rows


def _atomic_spans(rows):
    groups = {}
    for row in rows:
        group = row.get('atomic_group')
        if group and row.get('atomic_group_confirmed') is True:
            groups.setdefault(str(group), []).append(row)
    spans = []
    for group, members in groups.items():
        kinds = set(row.get('kind', 'raw_block') for row in members)
        kind = 'table' if 'table' in kinds else 'image' if 'image' in kinds else 'raw_block'
        spans.append({'start': min(row['start'] for row in members),
                      'end': max(row['end'] for row in members), 'kind': kind,
                      'structure_basis': 'CONFIRMED_ATOMIC_GROUP',
                      'hints': [{'type': 'ATOMIC_GROUP', 'group': group,
                                 'members': [row.get('id') for row in members]}]})
    return spans


def _coalesce(spans):
    """Combine overlapping containers; adjacent complete objects remain separate."""
    result = []
    for span in sorted(spans, key=lambda row: (row['start'], -row['end'])):
        if not result or span['start'] >= result[-1]['end']:
            result.append(deepcopy(span))
            continue
        previous = result[-1]
        if span['end'] > previous['end']:
            previous['end'] = span['end']
            previous['hints'].append({'type': 'OVERLAPPING_CONTAINER_BOUNDARIES',
                                      'basis': span['structure_basis']})
        previous['hints'].extend(deepcopy(span.get('hints', [])))
    return result


def _locations(unit, rows, mapping, markers):
    sources, legacy, page_basis, states, covered = [], [], [], [], []
    for row in rows:
        if row['start'] >= unit['end'] or row['end'] <= unit['start']:
            continue
        same = (row['start'], row['end']) == (unit['start'], unit['end'])
        relation = 'EXACT' if same else 'CONTAINED_SOURCE_OBJECT' if (
            unit['start'] <= row['start'] and row['end'] <= unit['end']) else 'PARTIAL_SOURCE_OBJECT'
        status = row.get('status', 'UNMAPPED')
        states.append(status)
        legacy.append({'object_id': row.get('id'), 'start': row['start'], 'end': row['end'],
                       'status': status, 'overlap': relation})
        if 'normalized_legacy_range' in row:
            legacy[-1]['original_map_range'] = deepcopy(row['normalized_legacy_range'])
        if row.get('sources'):
            covered.append((max(unit['start'], row['start']), min(unit['end'], row['end'])))
        for source in row.get('sources', []):
            item = deepcopy(source)
            page = item.get('page')
            item.update(input_sha256=mapping.get('input_sha256'),
                        legacy_object_id=row.get('id'), status=status, overlap=relation,
                        geometry=deepcopy(mapping.get('pages', {}).get(str(page), {})))
            sources.append(item)
            if type(page) is int and page > 0:
                page_basis.append({'page': page, 'basis': 'SOURCE_CANDIDATE',
                                   'status': status, 'legacy_object_id': row.get('id')})
        if type(row.get('page')) is int and row['page'] > 0:
            page_basis.append({'page': row['page'], 'basis': 'LEGACY_OBJECT_PAGE',
                               'legacy_object_id': row.get('id')})
    positions = [marker.start() for marker in markers]
    initial = max(0, bisect_right(positions, unit['start']) - 1)
    for index in range(initial, len(markers)):
        marker = markers[index]
        section_end = markers[index + 1].start() if index + 1 < len(markers) else float('inf')
        if marker.start() >= unit['end']:
            break
        if section_end > unit['start']:
            page_basis.append({'page': int(marker[1]), 'basis': 'OUTPUT_PAGE_MARKER'})
    cursor, uncovered = unit['start'], []
    for start, end in sorted(covered):
        if start > cursor:
            uncovered.append(unit['text'][cursor - unit['start']:start - unit['start']])
        cursor = max(cursor, end)
    uncovered.append(unit['text'][cursor - unit['start']:])
    partial = bool(re.sub(r'<!--[\s\S]*?-->', '', ''.join(uncovered)).strip())
    if not sources:
        location = 'UNMAPPED'
    elif 'AMBIGUOUS' in states:
        location = 'AMBIGUOUS'
    elif 'UNMAPPED' in states or partial:
        location = 'PARTIALLY_ASSOCIATED'
    elif len(set(states)) == 1:
        location = states[0]
    else:
        location = 'ASSOCIATED_MIXED'
    unit.update(source_refs=sources, legacy_refs=legacy, page_basis=page_basis,
                pages=sorted({record['page'] for record in page_basis}))
    unit['assessment']['location'] = location


def _infer_heading_hints(units):
    candidates = []
    for unit in units:
        if unit['kind'] != 'paragraph':
            continue
        stripped = unit['text'].strip()
        if '\n' in stripped or len(stripped) > 160:
            continue
        bold = _BOLD_HEADING.fullmatch(stripped)
        numbered = _NUMBERED_HEADING.fullmatch(stripped)
        if bold:
            title = bold[1] or bold[2]
            if not title.endswith(('.', '?', '!', ':', ';')):
                candidates.append((unit, 'REPEATED_STANDALONE_BOLD_STYLE', title, 1))
        elif numbered:
            title = numbered[2]
            if not title.endswith(('.', '?', '!', ':', ';')):
                candidates.append((unit, 'REPEATED_NUMBERED_PARAGRAPH_STYLE', stripped,
                                   len(numbered[1].split('.'))))
    counts = Counter(candidate[1] for candidate in candidates)
    for unit, basis, title, level in candidates:
        if counts[basis] >= 2:
            unit['heading'] = {'title': title, 'level': level, 'type': 'INFERRED_STRUCTURE',
                               'basis': basis}
            unit['hints'].append({'type': 'HEADING_CANDIDATE', 'basis': basis,
                                  'status': 'INFERRED_NOT_SOURCE_VERIFIED'})


def revised_heading(unit, after):
    """A title edit retains its recorded boundary evidence, not a new hierarchy."""
    previous = unit.get('heading')
    if previous is None:
        return None
    if not isinstance(after, str):
        raise ValueError('Heading replacement must be text')
    updated = deepcopy(previous)
    if previous['type'] == 'EXPLICIT_STRUCTURE':
        spans = _markdown_spans(after, _line_offsets(after))
        headings = [span['heading'] for span in spans if span.get('heading')]
        if len(spans) != 1 or len(headings) != 1:
            raise ValueError('Removing a heading boundary requires a structural operation')
        if headings[0]['level'] != previous['level']:
            raise ValueError('Changing a heading level requires a structural operation')
        updated['title'] = headings[0]['title']
        return updated
    stripped = after.strip()
    if '\n' in stripped or '\r' in stripped or len(stripped) > 160:
        raise ValueError('Changing inferred heading style requires a structural operation')
    basis = previous['basis']
    if basis == 'REPEATED_STANDALONE_BOLD_STYLE':
        match = _BOLD_HEADING.fullmatch(stripped)
        if not match:
            raise ValueError('Removing inferred heading style requires a structural operation')
        title = match[1] or match[2]
    elif basis == 'REPEATED_NUMBERED_PARAGRAPH_STYLE':
        match = _NUMBERED_HEADING.fullmatch(stripped)
        if not match or len(match[1].split('.')) != previous['level']:
            raise ValueError('Changing inferred heading level/style requires a structural operation')
        title = stripped
    else:
        raise ValueError('Unsupported inferred heading evidence for a title revision')
    if title.endswith(('.', '?', '!', ':', ';')):
        raise ValueError('Changing inferred heading style requires a structural operation')
    updated['title'] = title
    return updated


def partition(text, mapping=None):
    """Allocate fresh identities once; callers persist them for every later revision."""
    if not isinstance(text, str):
        raise TypeError('Markdown must be a Unicode string')
    rows = _validate_rows(mapping, text)
    if not text:
        return []
    offsets = _line_offsets(text)
    standard = _markdown_spans(text, offsets)
    literal = _literal_spans(text, offsets)
    protected = _coalesce(_protected_spans(text, offsets, literal) + _atomic_spans(rows))
    # Expand protected containers to encompass any Markdown block they overlap.
    for span in protected:
        for block in standard:
            if block['start'] < span['end'] and block['end'] > span['start']:
                span['start'] = min(span['start'], block['start'])
                span['end'] = max(span['end'], block['end'])
    protected = _coalesce(protected)
    spans = protected + [span for span in standard if not any(
        span['start'] < group['end'] and span['end'] > group['start'] for group in protected)]
    spans.sort(key=lambda span: span['start'])
    complete, cursor = [], 0
    for span in spans:
        if cursor < span['start']:
            complete.append({'start': cursor, 'end': span['start'], 'kind': 'raw_block',
                             'structure_basis': 'UNCLAIMED_OR_FORMAT_FRAGMENT', 'hints': []})
        complete.append(span)
        cursor = span['end']
    if cursor < len(text):
        complete.append({'start': cursor, 'end': len(text), 'kind': 'raw_block',
                         'structure_basis': 'UNCLAIMED_OR_FORMAT_FRAGMENT', 'hints': []})
    units, markers = [], []
    for marker in PAGE.finditer(text):
        line = bisect_right(offsets, marker.start()) - 1
        if (not text[offsets[line]:marker.start()].strip()
                and not text[marker.end():offsets[line + 1]].strip()
                and not any(start <= marker.start() < end for start, end in literal)):
            markers.append(marker)
    for index, span in enumerate(complete):
        excerpt = text[span['start']:span['end']]
        meaningful = re.sub(r'<!--[\s\S]*?-->', '', excerpt).strip()
        unit = dict(span, unit_id=_identity('u-'), revision=1, text=excerpt, order=index,
                    searchable=bool(meaningful),
                    assessment={'location': 'UNMAPPED', 'structure': 'PRESERVED_' + span['structure_basis'],
                                'content': 'NOT_JUDGED'}, relationships=[])
        _locations(unit, rows, mapping or {}, markers)
        units.append(unit)
    _infer_heading_hints(units)
    for current, following in zip(units, units[1:]):
        current['relationships'].append({'type': 'next_in_output', 'target_unit_id': following['unit_id'],
                                         'basis': 'FINAL_MARKDOWN_ORDER'})
    if ''.join(unit['text'] for unit in units) != text:
        raise ValueError('Structural partition failed exact content preservation')
    return units


def organize_scopes(units, soft_chars=12000):
    """Assign contiguous whole units; headings are evidence, never guaranteed truth."""
    if type(soft_chars) is not int or soft_chars <= 0:
        raise ValueError('soft_chars must be a positive integer')
    root = {'scope_id': _identity('scope-'), 'type': 'ROOT', 'title': 'Document', 'parent_id': None,
            'level': 0, 'boundary_basis': 'COMPLETE_OUTPUT_SEQUENCE', 'conflicts': [],
            'unit_ids': [], 'direct_unit_ids': [], 'pages': [], 'content_files': [], 'oversized_unit_ids': []}
    scopes, by_id, stack = [root], {root['scope_id']: root}, [root]
    current, size, virtual_number = None, 0, 0

    def make_scope(kind, title, parent, level, basis, heading=None):
        scope = {'scope_id': _identity('scope-'), 'type': kind, 'title': title,
                 'parent_id': parent['scope_id'], 'level': level, 'boundary_basis': basis,
                 'conflicts': [], 'unit_ids': [], 'direct_unit_ids': [], 'pages': [],
                 'content_files': [], 'oversized_unit_ids': []}
        if heading:
            scope['heading_unit_id'] = heading['unit_id']
        scopes.append(scope)
        by_id[scope['scope_id']] = scope
        return scope

    titles = Counter()
    for unit in units:
        heading = unit.get('heading')
        if heading:
            level = heading['level']
            while len(stack) > 1 and stack[-1]['level'] >= level:
                stack.pop()
            parent = stack[-1]
            current = make_scope(heading['type'], heading['title'], parent, level,
                                 heading['basis'], unit)
            if level > parent['level'] + 1:
                current['conflicts'].append({'type': 'SKIPPED_HEADING_LEVEL',
                                             'parent_level': parent['level'], 'heading_level': level})
            titles[heading['title']] += 1
            if titles[heading['title']] > 1:
                current['conflicts'].append({'type': 'DUPLICATE_HEADING_TEXT',
                                             'policy': 'DISTINCT_SCOPES_NOT_MERGED'})
            stack.append(current)
            size = 0
        if current is None or (size and size + len(unit['text']) > soft_chars):
            virtual_number += 1
            current = make_scope('VIRTUAL_SCOPE', f'Output range {virtual_number}', stack[-1],
                                 stack[-1]['level'] + 1, 'CONTIGUOUS_COMPLETE_UNITS_SOFT_SIZE')
            size = 0
        unit['primary_scope_id'] = current['scope_id']
        unit['content_file'] = f"content/{current['scope_id']}.md"
        current['direct_unit_ids'].append(unit['unit_id'])
        if len(unit['text']) > soft_chars:
            unit['hints'].append({'type': 'OVERSIZED_COMPLETE_UNIT', 'soft_chars': soft_chars})
            current['oversized_unit_ids'].append(unit['unit_id'])
        scope = current
        while scope:
            scope['unit_ids'].append(unit['unit_id'])
            scope['pages'] = sorted(set(scope['pages']).union(unit.get('pages', [])))
            if unit['content_file'] not in scope['content_files']:
                scope['content_files'].append(unit['content_file'])
            scope = by_id.get(scope['parent_id'])
        size += len(unit['text'])
    for scope in scopes:
        if scope['type'] == 'VIRTUAL_SCOPE' and scope['pages']:
            first, last = scope['pages'][0], scope['pages'][-1]
            scope['title'] = f'Physical page {first} output range' if first == last else f'Physical pages {first}-{last} output range'
            scope['title_basis'] = 'RECORDED_SOURCE_OR_OUTPUT_MARKER_PAGES'
        scope['generated_navigation'] = True
    return scopes
