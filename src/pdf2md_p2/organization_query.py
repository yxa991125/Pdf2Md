"""Read-only queries over one verified organization snapshot.

The caller loads and verifies the immutable revision before calling this module.
Source associations locate content; they never certify technical correctness.
"""

from collections import Counter
from copy import deepcopy
from pathlib import Path
import re

from .common import PRODUCT, save, sha


def _scope_id(scope):
    return scope.get('scope_id', scope.get('id'))


def _parent_id(scope):
    return scope.get('parent_id', scope.get('parent_scope_id'))


def _scope_path(unit, scope_by_id):
    current, seen, path = unit.get('primary_scope_id'), set(), []
    while current is not None:
        if current in seen or current not in scope_by_id:
            raise ValueError('Invalid or cyclic Scope ancestry in snapshot')
        seen.add(current)
        item = scope_by_id[current]
        path.append({key: deepcopy(item[key]) for key in ('scope_id', 'id', 'title', 'kind', 'type') if key in item})
        current = _parent_id(item)
    return list(reversed(path))


def _scope_members(scope, scopes, units):
    by_id = {_scope_id(item): item for item in scopes}
    if scope is None:
        return {unit['unit_id'] for unit in units}
    if scope not in by_id:
        raise ValueError(f'Unknown Scope: {scope}')
    allowed = {scope}
    while True:
        expanded = allowed | {key for key, item in by_id.items() if _parent_id(item) in allowed}
        if expanded == allowed:
            break
        allowed = expanded
    members = {unit['unit_id'] for unit in units if unit.get('primary_scope_id') in allowed}
    for key in allowed:
        for member in by_id[key].get('unit_ids', by_id[key].get('members', [])):
            members.add(member if isinstance(member, str) else member['unit_id'])
    return members


def _pages(unit):
    values = unit.get('pages', [])
    if not isinstance(values, list) or any(type(value) is not int or value < 1 for value in values):
        raise ValueError('Invalid physical page inventory in Unit')
    return sorted(set(values))


def _page_check(unit, pages, mode):
    actual = _pages(unit)
    if pages is None:
        return True
    if not actual:
        return False
    inside = [pages[0] <= number <= pages[1] for number in actual]
    return all(inside) if mode == 'contained' else any(inside)


def _identifier_char(char):
    return char.isalnum() or char in '_-'


def _exact_boundary(text, start, end, query):
    if start and _identifier_char(query[0]) and _identifier_char(text[start - 1]):
        return False
    if end < len(text) and _identifier_char(query[-1]) and _identifier_char(text[end]):
        return False
    # Numeric queries do not quietly match a component of a decimal literal.
    if query[0].isdigit() and start and text[start - 1] in '.+':
        return False
    if query[-1].isdigit() and end < len(text) and text[end] == '.' and end + 1 < len(text) and text[end + 1].isdigit():
        return False
    return True


def _loose_text(text):
    normalized, origins = [], []
    for index, char in enumerate(text):
        value = ' ' if char.isspace() or char in '_-' else char.casefold()
        for part in value:
            if part == ' ' and normalized and normalized[-1] == ' ':
                origins[-1][1] = index + 1
            else:
                normalized.append(part)
                origins.append([index, index + 1])
    return ''.join(normalized), origins


def _markdown_escape_positions(text):
    from markdown_it import MarkdownIt
    from markdown_it.rules_inline import escape as markdown_escape
    from .organization_links import _line_offsets, _origins

    markdown = MarkdownIt('default', {'html': True, 'linkify': False})
    lines, positions, cursors = _line_offsets(text), set(), {}
    captures, active = [], [None]

    def capture_escape(state, silent):
        start, count = state.pos, len(state.tokens)
        accepted = markdown_escape(state, silent)
        if accepted and not silent and state.src is active[0]:
            emitted = state.tokens[count:]
            if (state.pos == start + 2 and emitted and emitted[-1].type == 'text_special'
                    and emitted[-1].content == state.src[start + 1]):
                captures.append(start)
        return accepted

    inline = MarkdownIt('default', {'html': True, 'linkify': False})
    inline.inline.ruler.at('escape', capture_escape)
    for token in markdown.parse(text):
        if token.type != 'inline' or not token.content:
            continue
        start, end = (lines[index] for index in token.map)
        key = tuple(token.map)
        origins = _origins(text, token.content, cursors.get(key, start), end)
        cursors[key] = origins[-1] + 1
        captures.clear()
        active[0] = token.content
        inline.inline.parse(token.content, inline, {}, [])
        for position in captures:
            raw = origins[position]
            if origins[position + 1] == raw + 1:
                positions.add(raw)
    return positions


def _literal_text(text):
    """Decode parsed Markdown escapes, not literal fenced or inline code."""
    escapes = _markdown_escape_positions(text) if '\\' in text else set()
    normalized, origins, index = [], [], 0
    while index < len(text):
        end = index + 1
        if index in escapes:
            end += 1
        normalized.append(text[end - 1])
        origins.append([index, end])
        index = end
    return ''.join(normalized), origins


def _hits(text, query, mode):
    if query is None:
        return []
    haystack, needle = text, query
    origins = None
    if mode == 'exact':
        haystack, origins = _literal_text(text)
    elif mode == 'loose':
        haystack, origins = _loose_text(text)
        needle = _loose_text(query)[0].strip()
    matches = []
    for found in re.finditer(re.escape(needle), haystack):
        start, end = found.span()
        if mode == 'exact' and not _exact_boundary(haystack, start, end, needle):
            continue
        if origins is not None:
            start, end = origins[start][0], origins[end - 1][1]
        matches.append({'start': start, 'end': end, 'text': text[start:end],
                        'match_basis': 'markdown_escape_decoded_literal' if mode == 'exact' and text[start:end] != needle
                        else 'loose_candidate' if mode == 'loose' else 'literal'})
    return matches


def _relation_ids(reference):
    ids = [reference.get('source'), reference.get('target')]
    for candidate in reference.get('candidates', []):
        ids.append(candidate if isinstance(candidate, str) else candidate.get('unit_id', candidate.get('target')))
    return {value for value in ids if isinstance(value, str)}


def _view(unit, scopes):
    return {**deepcopy(unit), 'scope_path': _scope_path(unit, scopes),
            'semantic_correctness_certified': False}


def search(snapshot, query=None, match='exact', scope=None, pages=None, kind=None,
           unit_id=None, page_mode='contained', expand_context=False):
    """Filter first, then search canonical text without altering identifiers.

    ``exact`` is case-sensitive matching with identifier boundaries, decoding
    only reversible Markdown punctuation escapes;
    ``substring`` is case-sensitive literal substring matching. ``loose`` folds
    case and space/underscore/hyphen separators and is only a candidate search.
    Offsets address Unicode code points in the returned complete Unit text.
    """
    if match not in ('exact', 'substring', 'loose'):
        raise ValueError('Match must be exact, substring or loose')
    if page_mode not in ('contained', 'intersect'):
        raise ValueError('Page mode must be contained or intersect')
    if query is not None and (not isinstance(query, str) or not query.strip()):
        raise ValueError('Query must be nonempty text when provided')
    if match == 'loose' and query is not None and not _loose_text(query)[0].strip():
        raise ValueError('Loose query must contain a non-separator character')
    if pages is not None and (not isinstance(pages, (tuple, list)) or len(pages) != 2
                             or any(type(value) is not int for value in pages)
                             or not 1 <= pages[0] <= pages[1]):
        raise ValueError('Page range must contain two inclusive positive physical page numbers')
    units = snapshot['units']
    by_id = {unit['unit_id']: unit for unit in units}
    scopes = {_scope_id(item): item for item in snapshot['scopes']}
    if unit_id is not None and unit_id not in by_id:
        raise ValueError(f'Unknown Unit: {unit_id}')
    members = _scope_members(scope, snapshot['scopes'], units)
    allowed = {unit['unit_id'] for unit in units if unit['unit_id'] in members
               and (kind is None or unit['kind'] == kind)
               and _page_check(unit, pages, page_mode)
               and (unit_id is None or unit['unit_id'] == unit_id)}
    relations_by_id = {}
    for reference in snapshot.get('references', []):
        for target in _relation_ids(reference):
            relations_by_id.setdefault(target, []).append(reference)
    results = []
    for unit in units:
        if unit['unit_id'] not in allowed:
            continue
        hits = _hits(unit['text'], query, match)
        if query is not None and not hits:
            continue
        row = _view(unit, scopes)
        row.update(match_mode=match if query is not None else 'unit_selection', hits=hits,
                   match_is_candidate=(match == 'loose' and query is not None),
                   outside_pages=[number for number in _pages(unit)
                                  if pages is not None and not pages[0] <= number <= pages[1]])
        row['crosses_page_boundary'] = bool(row['outside_pages'])
        related = relations_by_id.get(unit['unit_id'], [])
        row['references'] = deepcopy(related)
        neighbors = sorted(set().union(*(_relation_ids(ref) for ref in related)) - {unit['unit_id']}) if related else []
        row['related_context'] = []
        for target_id in neighbors:
            if target_id not in by_id:
                continue
            target = by_id[target_id]
            context = {'unit_id': target_id, 'revision': target['revision'],
                       'content_ref': deepcopy(target['content_ref']),
                       'outside_requested_range': target_id not in allowed,
                       'expanded': bool(expand_context)}
            if expand_context:
                context['unit'] = _view(target, scopes)
                context['outside_pages'] = [number for number in _pages(target)
                                            if pages is not None and not pages[0] <= number <= pages[1]]
            row['related_context'].append(context)
        results.append(row)
    return {'workspace_id': snapshot['workspace_id'], 'document_id': snapshot['document_id'],
            'revision': snapshot['revision'], 'query': query, 'match_mode': match,
            'offset_unit': 'unicode_codepoint', 'hit_offsets_relative_to': 'unit_text',
            'filters': {'scope': scope, 'pages': list(pages) if pages is not None else None,
                        'kind': kind, 'unit_id': unit_id, 'page_mode': page_mode,
                        'combination': 'intersection', 'expand_context': bool(expand_context)},
            'allowed_unit_count': len(allowed), 'result_count': len(results), 'results': results,
            'semantic_correctness_certified': False}


def _binding(mapping, pdf):
    expected = mapping.get('input_sha256')
    if not expected or sha(pdf) != expected:
        raise ValueError('Source PDF SHA256 must match the imported source binding')


def _evidence_destination(snapshot, pdf, directory):
    target = Path(directory).resolve()
    protected = [PRODUCT.resolve(), Path(snapshot['workspace']).resolve(), Path(snapshot['root']).resolve()]
    for key in ('input_root', 'from_result'):
        if snapshot.get(key):
            protected.append(Path(snapshot[key]).resolve())
    for root in protected:
        if target.is_relative_to(root) or root.is_relative_to(target):
            raise ValueError('Evidence directory must be separate from workspace, input and product')
    if Path(pdf).resolve().is_relative_to(target):
        raise ValueError('Evidence directory cannot contain the source PDF')
    if target.exists():
        raise ValueError('Evidence directory exists; use a new directory')
    return target


def _geometry(page):
    rotation = page.rotation
    page.set_rotation(0)
    return {'width': page.rect.width, 'height': page.rect.height, 'rotation': rotation,
            'coordinate_system': 'unrotated_page_top_left_pdf_points'}


def trace(snapshot, unit_id, pdf=None, render_dir=None):
    """Return all source candidates, optionally read and crop a hash-bound PDF."""
    rows = search(snapshot, unit_id=unit_id)['results']
    unit, = rows
    mapping = snapshot['source_map']
    result = {'workspace_id': snapshot['workspace_id'], 'document_id': snapshot['document_id'],
              'revision': snapshot['revision'], 'unit': unit,
              'input_sha256': mapping.get('input_sha256'), 'offset_unit': 'unicode_codepoint',
              'page_numbering': 'physical_1_based', 'pages': deepcopy(mapping.get('pages', {})),
              'source_refs': deepcopy(unit.get('source_refs', [])),
              'source_pdf_checked': False, 'semantic_correctness_certified': False}
    if render_dir is not None and pdf is None:
        raise ValueError('Provide the exact source PDF to render evidence')
    if pdf is None:
        return result
    pdf = Path(pdf).resolve()
    _binding(mapping, pdf)
    destination = _evidence_destination(snapshot, pdf, render_dir) if render_dir is not None else None
    import pymupdf
    from .source_map import valid_bbox

    checked, renders, geometries = [], [], {}
    with pymupdf.open(pdf) as document:
        for index, source in enumerate(result['source_refs']):
            entry = {'candidate': index + 1, 'source': deepcopy(source), 'checked': False}
            number = source.get('page')
            if type(number) is not int or not 1 <= number <= len(document):
                entry['reason'] = 'SOURCE_PAGE_UNAVAILABLE'
            else:
                page = document[number - 1]
                if number not in geometries:
                    geometries[number] = _geometry(page)
                geometry = geometries[number]
                recorded = mapping.get('pages', {}).get(str(number))
                if recorded is None:
                    entry['reason'] = 'SOURCE_COORDINATE_SYSTEM_UNRECORDED'
                elif (recorded.get('coordinate_system') != geometry['coordinate_system']
                      or any(recorded.get(key) != geometry[key] for key in ('width', 'height', 'rotation'))):
                    entry['reason'] = 'SOURCE_GEOMETRY_CONFLICT'
                elif not valid_bbox(source.get('bbox'), geometry):
                    entry['reason'] = 'SOURCE_REGION_UNAVAILABLE'
                else:
                    entry.update(checked=True, source_text=page.get_text(clip=pymupdf.Rect(source['bbox'])),
                                 geometry=geometry)
                    renders.append((index + 1, number, source['bbox']))
            checked.append(entry)
        result.update(source_pdf_checked=True, source_checks=checked)
        if destination is not None:
            destination.mkdir(parents=True, exist_ok=False)
            images = []
            for index, number, bbox in renders:
                page = document[number - 1]
                page.set_rotation(0)
                name = f'candidate-{index:04d}.png'
                page.get_pixmap(clip=pymupdf.Rect(bbox), matrix=pymupdf.Matrix(2, 2), alpha=False).save(destination / name)
                images.append({'candidate': index, 'unit_id': unit_id, 'path': name,
                               'sha256': sha(destination / name), 'orientation': 'unrotated_source'})
            result['evidence'] = images
            result['render_directory'] = str(destination)
    _binding(mapping, pdf)
    if destination is not None:
        save(destination / 'evidence.json', result)
    return result


def source_visibility(pdf, mapping):
    """Geometric text-block visibility only, not omitted-content or accuracy scoring."""
    pdf = Path(pdf).resolve()
    _binding(mapping, pdf)
    import pymupdf
    from .source_map import valid_bbox
    from .identifiers import source_blocks

    candidates = {}
    for row in mapping.get('units', mapping.get('objects', [])):
        for source in row.get('source_refs', row.get('sources', [])):
            candidates.setdefault(source.get('page'), []).append({
                'unit_id': row.get('unit_id', row.get('id')), 'source': source,
                'status': source.get('status', row.get('status', row.get('assessment', {}).get('location')))})
    rows = []
    with pymupdf.open(pdf) as document:
        for number, page in enumerate(document, 1):
            geometry = _geometry(page)
            recorded = mapping.get('pages', {}).get(str(number))
            geometry_valid = bool(recorded and recorded.get('coordinate_system') == geometry['coordinate_system']
                                  and all(recorded.get(key) == geometry[key] for key in ('width', 'height', 'rotation')))
            blocks = list(source_blocks(page))
            if not blocks:
                rows.append({'page': number, 'status': 'unchecked', 'reason': 'NO_SUPPORTED_TEXT_BLOCKS',
                             'bbox': None, 'text': '', 'associations': []})
            for index, block in enumerate(blocks):
                item = {'page': number, 'block': index, **block, 'status': 'unchecked', 'associations': []}
                if not geometry_valid or not valid_bbox(block['bbox'], geometry):
                    item['reason'] = 'SOURCE_GEOMETRY_UNCHECKED'
                else:
                    region = pymupdf.Rect(block['bbox'])
                    for candidate in candidates.get(number, []):
                        source = candidate['source']
                        if source.get('page') != number or not valid_bbox(source.get('bbox'), geometry):
                            continue
                        overlap = (region & pymupdf.Rect(source['bbox'])).get_area() / region.get_area()
                        if overlap > 0:
                            item['associations'].append({'unit_id': candidate['unit_id'], 'status': candidate['status'],
                                                         'block_area_overlap': overlap, 'basis': source.get('basis')})
                    matches = item['associations']
                    if not matches:
                        item['status'] = 'candidate_unmapped'
                    elif (len(matches) == 1 and matches[0]['block_area_overlap'] >= 0.95
                          and matches[0]['status'] in ('ASSOCIATED_NATIVE_LAYOUT', 'MATCHED_SOURCE_TEXT')):
                        item['status'] = 'linked'
                    else:
                        item['status'] = 'partial_or_conflict'
                rows.append(item)
    _binding(mapping, pdf)
    counts = Counter(row['status'] for row in rows)
    return {'input_sha256': mapping['input_sha256'], 'page_numbering': 'physical_1_based',
            'coordinate_system': 'unrotated_page_top_left_pdf_points',
            'method': 'source_text_block_geometric_overlap', 'minimum_linked_block_area_overlap': 0.95,
            'population': 'Extractable PDF text blocks plus one unchecked placeholder for each text-free page',
            'categories': {key: counts[key] for key in ('linked', 'partial_or_conflict', 'candidate_unmapped', 'unchecked')},
            'records': rows, 'semantic_correctness_certified': False, 'semantic_coverage_measured': False,
            'unmapped_means': 'No supported geometric association; not proof of loss or coverage',
            'unsupported': ['Image-only source content', 'Vector drawings', 'Semantic completeness']}
