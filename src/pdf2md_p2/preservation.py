"""Source-first preservation of ruled tables and independently framed figures."""

from collections import Counter
from html import escape
import math
from statistics import median
import re

from markdown_it import MarkdownIt
import pymupdf

from .assets import references
from .common import PAGE, digest, page_parts, read, save, sha, within
from .fidelity_source import characters
from .objects import Tables
from .source_map import objects, visible

VERSION = 'source-preservation-2'
REVIEW_STATES = {'SOURCE_TABLE_IMAGE_REVIEW_REQUIRED', 'FIGURE_REGION_UNRESOLVED'}
CAPTION = re.compile(r'^\s*(?:Figure|Fig\.)\s*(\d+[a-z]?)\s*[:.]\s*\S', re.I)
MD = MarkdownIt('default', {'html': True})


def norm(text):
    return ''.join(text.split()).replace('\u2212', '-').replace('\u2013', '-').casefold()


def _axes(values):
    groups = []
    for value in sorted(values):
        if not groups or value - groups[-1][-1] > .8:
            groups.append([])
        groups[-1].append(value)
    return [median(group) for group in groups]


def _index(values, value):
    choices = [i for i, v in enumerate(values) if abs(value-v) <= .85]
    if len(choices) != 1:
        raise ValueError('Ambiguous source cell boundary')
    return choices[0]


def _cell_text(glyphs):
    if not glyphs:
        return ''
    if any(g['direction'] != [1.0, 0.0] or '\ufffd' in g['text'] or
           any(0xe000 <= ord(c) <= 0xf8ff for c in g['text']) for g in glyphs):
        raise ValueError('Unsupported source cell glyph or orientation')
    size = max(g['size'] for g in glyphs)
    baselines = _axes([g['origin'][1] for g in glyphs if g['size'] >= size*.95])
    rows = [[] for _ in baselines]
    for glyph in glyphs:
        index = min(range(len(baselines)), key=lambda i: abs(glyph['origin'][1]-baselines[i]))
        if abs(glyph['origin'][1]-baselines[index]) > size*.8:
            raise ValueError('Source cell script baseline unresolved')
        rows[index].append(glyph)
    result = []
    for baseline, row in zip(baselines, rows):
        fragments, current, previous = [], 'base', None
        for glyph in sorted(row, key=lambda g: (g['bbox'][0], g['origin'][1])):
            delta = glyph['origin'][1]-baseline
            kind = ('sub' if delta > 1.2 and glyph['size'] < size*.98 else
                    'sup' if delta < -size*.2 and glyph['size'] < size*.98 else 'base')
            if kind != current:
                if current != 'base':
                    fragments.append('</'+current+'>')
                if kind != 'base':
                    fragments.append('<'+kind+'>')
                current = kind
            if previous and kind == 'base' and not glyph['text'].isspace() and not previous['text'].isspace() \
                    and glyph['bbox'][0]-previous['bbox'][2] > size*.27:
                fragments.append(' ')
            fragments.append(escape(glyph['text']))
            previous = glyph
        if current != 'base':
            fragments.append('</'+current+'>')
        result.append(''.join(fragments).strip())
    return '<br>'.join(result)


def _source_table(table, glyphs):
    boxes = sorted(set(tuple(cell) for cell in table.cells if cell))
    xs = _axes([v for b in boxes for v in (b[0], b[2])])
    ys = _axes([v for b in boxes for v in (b[1], b[3])])
    if len(xs)-1 != table.col_count or len(ys)-1 != table.row_count:
        raise ValueError('Source grid axes do not match table geometry')
    occupied, cells = {}, []
    for box in boxes:
        r, c = _index(ys, box[1]), _index(xs, box[0])
        bottom, right = _index(ys, box[3]), _index(xs, box[2])
        inside = [g for g in glyphs if box[0] <= (g['bbox'][0]+g['bbox'][2])/2 < box[2]
                  and box[1] <= (g['bbox'][1]+g['bbox'][3])/2 < box[3]]
        text = _cell_text(inside)
        for y in range(r, bottom):
            for x in range(c, right):
                if (y, x) in occupied:
                    raise ValueError('Overlapping source table cells')
                occupied[y, x] = len(cells)
        cells.append({'row': r, 'column': c, 'rowspan': bottom-r, 'colspan': right-c,
                      'bbox': list(box), 'html': text})
    if len(occupied) != table.row_count*table.col_count:
        raise ValueError('Incomplete source grid coverage')
    bounds = pymupdf.Rect(table.bbox)
    table_glyphs = [g for g in glyphs if bounds.contains(pymupdf.Point(
        (g['bbox'][0]+g['bbox'][2])/2, (g['bbox'][1]+g['bbox'][3])/2))]
    serialized = []
    for row in range(table.row_count):
        serialized.append('<tr>')
        for cell in sorted((c for c in cells if c['row'] == row), key=lambda c: c['column']):
            attrs = ''.join(f' {name}="{cell[name]}"' for name in ('rowspan', 'colspan') if cell[name] > 1)
            serialized.append('<td'+attrs+'>'+cell['html']+'</td>')
        serialized.append('</tr>')
    html = '<table><tbody>'+''.join(serialized)+'</tbody></table>'
    plain = visible(html)
    if Counter(norm(plain)) != Counter(norm(''.join(g['text'] for g in table_glyphs))):
        raise ValueError('Source grid serialization lost or duplicated characters')
    return {'bbox': list(table.bbox), 'rows': table.row_count, 'columns': table.col_count,
            'cells': cells, 'serialized': html, 'text': plain}


def _matrix(raw):
    parser = Tables()
    parser.feed(MD.render(raw))
    if len(parser.records) != 1 or parser.records[0]['issues']:
        return None
    occupied = {}
    for r, row in enumerate(parser.records[0]['rows']):
        col = 0
        for cell in row:
            while (r, col) in occupied:
                col += 1
            if cell['rowspan'] > 1024 or cell['colspan'] > 1024:
                return None
            for y in range(r, r+cell['rowspan']):
                for x in range(col, col+cell['colspan']):
                    if (y, x) in occupied:
                        return None
                    occupied[y, x] = norm(cell['text'])
            col += cell['colspan']
    return occupied


def _anchors(tables):
    result = set()
    for table in tables:
        for cell in table.get('cells', []):
            text = norm(visible(cell['html']))
            if cell['column'] == 0 and len(text) >= 5 and re.search('[a-z]', text):
                result.add(text)
        # Small enumeration tables are identified by their whole explicit header.
        header = ''.join(norm(visible(c['html'])) for c in sorted(
            (c for c in table.get('cells', []) if c['row'] == 0), key=lambda c: c['column']))
        if re.search('[a-z]', header) and len(header) >= 7:
            result.add(header)
        for index in range(min(3, table['rows'])):
            labels = [norm(visible(c['html'])) for c in sorted(
                (c for c in table.get('cells', []) if c['row'] == index), key=lambda c: c['column'])]
            if len(labels) >= 2 and all(not label or re.search('[a-z]', label) for label in labels):
                joined = ''.join(labels)
                if len(joined) >= 5:
                    result.add(joined)
    return result


def _groups(tables):
    result = []
    for table in sorted(tables, key=lambda t: (t['bbox'][1], t['bbox'][0])):
        box = table['bbox']
        if result:
            last = result[-1][-1]['bbox']
            header = result[-1][-1]
            bit_header = header['rows'] == 1 and header['columns']+1 == table['columns'] and all(
                not c['html'] or visible(c['html']).isdigit() for c in header.get('cells', []))
            if (abs(box[0]-last[0]) < 3 or bit_header) and abs(box[2]-last[2]) < 3 and 0 <= box[1]-last[3] < 25:
                result[-1].append(table)
                continue
        result.append([table])
    return result


def _captions(page):
    result = []
    for block in page.get_text('dict')['blocks']:
        for line in block.get('lines', []):
            text = ''.join(s['text'] for s in line['spans']).strip()
            match = CAPTION.match(text)
            if match and any(s['flags'] & 16 or 'bold' in s['font'].lower() for s in line['spans']):
                result.append({'caption': text, 'label': match[1], 'bbox': list(line['bbox'])})
    return result


def _frame(page, caption):
    target = pymupdf.Rect(caption['bbox'])
    frames = []
    for drawing in page.get_drawings():
        for item in drawing['items']:
            if item[0] != 're':
                continue
            rect = pymupdf.Rect(item[1])
            if rect.contains(target) and rect.height > 35 and rect.width > target.width*.9 \
                    and target.y0 > rect.y0+rect.height*.5 and rect.get_area() < page.rect.get_area()*.7:
                frames.append(rect)
    return min(frames, key=lambda r: r.get_area()) if frames else None


def _image(page, bbox, output, identifier):
    rect = pymupdf.Rect(bbox) & page.rect
    if rect.is_empty or not all(math.isfinite(v) for v in rect):
        raise ValueError('Invalid source preservation region')
    relative = 'assets/source-preserved-'+identifier+'.png'
    page.get_pixmap(matrix=pymupdf.Matrix(3, 3), clip=rect, alpha=False).save(within(output, relative))
    return relative, sha(within(output, relative))


def preserve(pdf, markdown, output, chunks=()):
    source_hash = sha(pdf)
    outputs = objects(markdown)
    parts = page_parts(markdown)
    markers = list(PAGE.finditer(markdown))
    page_ends = {int(m[1]): markers[i+1].start() if i+1 < len(markers) else len(markdown)
                 for i, m in enumerate(markers)}
    records, edits = [], []
    with pymupdf.open(pdf) as document:
        from .source_map import native_objects
        geometry = {}
        for number, page in enumerate(document, 1):
            width, height = page.rect.width, page.rect.height
            if page.rotation % 180:
                width, height = height, width
            geometry[str(number)] = {'width': width, 'height': height}
        native = native_objects(output, chunks, geometry)
        for number, page in enumerate(document, 1):
            # Failed chunks stay under the existing PARTIAL-page gate.
            if number not in parts:
                continue
            rotation = page.rotation
            page.set_rotation(0)
            captions = sorted(_captions(page), key=lambda c: (c['bbox'][1], c['bbox'][0]))
            page_objects = [r for r in outputs if r['page'] == number]
            output_tables = [r for r in page_objects if r['kind'] == 'table' and not references(r['text'])]
            from .preservation_layout import drawing_region, spatial_tables, table_locations
            native_page = [r for r in native if r['page'] == number] if rotation == 0 else []
            locations = table_locations(output_tables, native_page, MD)
            tables = []
            source_text = page.get_text()
            header_labels = set(re.findall(r'\b(?:parameter|min|max|typ|units?)\b', source_text, re.I))
            if output_tables or len({s.lower() for s in header_labels}) >= 3 or re.search(
                    r'\b(?:Table\s+\d|Characteristic|Rating)\b', source_text, re.I):
                glyphs = None
                for index, table in enumerate(page.find_tables(strategy='lines_strict').tables):
                    if any(pymupdf.Rect(table.bbox).contains(pymupdf.Rect(c['bbox'])) for c in captions):
                        continue
                    if table.row_count < 2:
                        values = [str(v).strip() for v in table.extract()[0] if v is not None]
                        if len(values) < 4 or not all(v.isdigit() for v in values) or values != [str(v) for v in range(len(values)-1, -1, -1)]:
                            continue
                    if glyphs is None:
                        glyphs = characters(page)
                    try:
                        tables.append(_source_table(table, glyphs))
                    except ValueError as error:
                        tables.append({'bbox': list(table.bbox), 'error': str(error), 'rows': table.row_count,
                                       'columns': table.col_count, 'cells': [], 'text': ''})
            used = set()
            for group in _groups(tables):
                bbox = pymupdf.Rect(group[0]['bbox'])
                for table in group[1:]:
                    bbox |= pymupdf.Rect(table['bbox'])
                identifier = digest(f'{source_hash}:{number}:table:{list(bbox)}')[:20]
                anchors = _anchors(group)
                scored = [(sum(anchor in norm(visible(r['text'])) for anchor in anchors), r) for r in output_tables]
                matches = [(score, row) for score, row in scored if score > 0 and row['start'] not in used]
                # A group may be represented as several adjacent output tables.
                selected = [row for score, row in matches if score == max((s for s, _ in matches), default=0)]
                if len(group) > 1:
                    selected = [row for score, row in matches if score > 0]
                located = spatial_tables(group, [r for r in output_tables if r['start'] not in used], locations)
                if located:
                    selected = located
                ambiguous = len(group) == 1 and len(selected) > 1
                row = {'id': identifier, 'kind': 'table', 'page': number, 'source_bbox': list(bbox),
                       'page_rotation': rotation, 'source_coordinate_system': 'unrotated_page_top_left_pdf_points',
                       'source_tables': group, 'state': 'SOURCE_TABLE_UNRESOLVED'}
                row['association_basis'] = ([locations[r['start']] for r in located] if located else 'SOURCE_LABELS')
                if selected and not ambiguous and all('error' not in t for t in group):
                    a, b = min(r['start'] for r in selected), max(r['end'] for r in selected)
                    intervening = [r for r in page_objects if a <= r['start'] < b]
                    if all(r['kind'] == 'table' and not references(r['text']) for r in intervening):
                        serialized = '\n\n'.join(t['serialized'] for t in group)+'\n'
                        same = len(group) == len(selected) and all(_matrix(t['serialized']) == _matrix(r['text'])
                            for t, r in zip(group, sorted(selected, key=lambda r: r['start'])))
                        row.update(state='SOURCE_GRID_MATCH' if same else 'REBUILT_SOURCE_GRID',
                                   serialized=markdown[a:b] if same else serialized,
                                   previous_output=markdown[a:b], original_span=[a, b])
                        if not same:
                            edits.append((a, b, serialized))
                        used.update(r['start'] for r in selected)
                if row['state'] == 'SOURCE_TABLE_UNRESOLVED':
                    asset, asset_sha = _image(page, bbox, output, identifier)
                    row.update(asset=asset, asset_sha256=asset_sha,
                        serialized=f'\n\n![Source table on PDF page {number}; visual reference, structure unresolved]({asset})\n')
                    row['state'] = 'SOURCE_TABLE_IMAGE_REVIEW_REQUIRED'
                    end = max((r['end'] for r in page_objects), default=page_ends[number])
                    edits.append((end, end, row['serialized']))
                records.append(row)
            for caption in captions:
                frame = _frame(page, caption)
                drawing = drawing_region(page, caption, captions, native_page) if frame is None else None
                if drawing:
                    frame = pymupdf.Rect(drawing['bbox'])
                identifier = digest(f'{source_hash}:{number}:figure:{caption["bbox"]}:{caption["label"]}')[:20]
                row = {'id': identifier, 'kind': 'figure', 'page': number, 'caption': caption['caption'],
                       'caption_bbox': caption['bbox'],
                       'source_bbox': list(frame) if frame else caption['bbox'], 'page_rotation': rotation,
                       'source_coordinate_system': 'unrotated_page_top_left_pdf_points'}
                row['region_evidence'] = drawing or {'basis': 'SOURCE_ENCLOSING_RECTANGLE' if frame else 'UNRESOLVED'}
                if frame is not None:
                    asset, asset_sha = _image(page, frame, output, identifier)
                    serialized = f'\n\n![{caption["caption"]} - complete source panel]({asset})\n'
                    end = max((r['end'] for r in page_objects), default=page_ends[number])
                    edits.append((end, end, serialized))
                    row.update(state='SOURCE_FIGURE_REGION_PRESERVED', asset=asset, asset_sha256=asset_sha,
                               serialized=serialized)
                else:
                    row['state'] = 'FIGURE_REGION_UNRESOLVED'
                records.append(row)
    # Do not allow two independently chosen replacements to consume the same object.
    spans = sorted((a, b) for a, b, _ in edits if a != b)
    if any(a < previous[1] for previous, (a, b) in zip(spans, spans[1:])):
        raise ValueError('Overlapping source-preservation replacements')
    text = markdown
    # Insertions at the same page boundary retain source order.
    ordered = sorted(enumerate(edits), key=lambda r: (r[1][0], r[1][1], r[0]), reverse=True)
    for _, (a, b, replacement) in ordered:
        text = text[:a]+replacement+text[b:]
    _record_bindings(text, records, edits)
    summary = dict(Counter(r['state'] for r in records))
    report = {'version': VERSION, 'input_sha256': source_hash, 'records': records,
              'states': summary, 'source_scope': 'Native ruled grids and explicit Figure/Fig captions; not every source object',
              'whole_document_content_percent': None,
              'content_gate_passed': not any(summary.get(state, 0) for state in REVIEW_STATES),
              'semantic_accuracy': None, 'original_markdown_sha256': digest(markdown),
              'preserved_markdown_sha256': digest(text),
              'changes': [{'original_span': [a, b], 'before': markdown[a:b], 'after': value} for a, b, value in edits]}
    save(output / 'source_preservation.json', report)
    return text, report


def _record_bindings(text, records, edits):
    parsed = objects(text)
    def translate(position, end=False):
        return position+sum(len(value)-(b-a) for a, b, value in edits
                            if b < position or b == position and (a != b or not end))
    for row in records:
        if row['kind'] != 'table' or 'original_span' not in row:
            continue
        a, b = row['original_span']
        start = translate(a)
        end = start+len(row['serialized']) if row['state'] == 'REBUILT_SOURCE_GRID' else translate(b, True)
        members = [r for r in parsed if r['kind'] == 'table' and r['page'] == row['page']
                   and start <= r['start'] < r['end'] <= end]
        if len(members) != len(row['source_tables']):
            raise ValueError('Recovered table boundaries do not match source components')
        row['bindings'] = [{'start': member['start'], 'end': member['end'], 'kind': 'table',
            'sha256': digest(member['text']), 'source_bbox': table['bbox']}
            for member, table in zip(members, row['source_tables'])]


def bound_objects(output, text):
    path = output / 'source_preservation.json'
    if not path.is_file():
        return {}
    report = read(path)
    if report['version'] != VERSION:
        return {}
    native = output / 'native_manual.md'
    original = native.read_text(encoding='utf-8') if native.is_file() else text
    if digest(original) != report['preserved_markdown_sha256']:
        raise ValueError('Preserved table binding text differs')
    operations = []
    if original != text:
        log = read(output / 'change_log.json')
        if log['before_sha256'] != digest(original) or log['after_sha256'] != digest(text):
            raise ValueError('Preserved table identifier-edit binding differs')
        operations = log['operations']
    result = {}
    for row in report['records']:
        for item in row.get('bindings', []):
            a, b = item['start'], item['end']
            if digest(original[a:b]) != item['sha256'] or any(
                    edit['native_start'] < b and edit['native_end'] > a for edit in operations):
                raise ValueError('Preserved table binding span differs')
            start = a+sum(len(e['after'])-len(e['before']) for e in operations if e['native_end'] <= a)
            end = b+sum(len(e['after'])-len(e['before']) for e in operations if e['native_end'] <= b)
            if digest(text[start:end]) != item['sha256']:
                raise ValueError('Final preserved table content differs')
            result[start, end] = {'page': row['page'], 'bbox': item['source_bbox'], 'kind': 'table',
                'text': text[start:end], 'basis': 'SOURCE_PRESERVATION_GEOMETRY'}
    return result


def validate(output):
    path = output / 'source_preservation.json'
    if not path.is_file():
        configuration = output / 'configuration.json'
        quality = output / 'quality_report.json'
        if (configuration.is_file() and read(configuration).get('source_preservation_version')) or (
                quality.is_file() and 'content_preservation' in read(quality)):
            raise ValueError('Current result is missing source_preservation.json')
        return None
    report = read(path)
    mapping = read(output / 'source_map.json')
    if report['version'] not in ('source-preservation-1', VERSION) or report['input_sha256'] != mapping['input_sha256']:
        raise ValueError('Source preservation version or PDF binding differs')
    configuration = output / 'configuration.json'
    if configuration.is_file() and read(configuration).get('source_preservation_version') not in (None, report['version']):
        raise ValueError('Source preservation configuration version differs')
    parts = page_parts((output / 'manual.md').read_text(encoding='utf-8'))
    for row in report['records']:
        geometry = mapping['pages'].get(str(row['page']))
        box = row['source_bbox']
        if (not geometry or len(box) != 4 or not all(math.isfinite(v) for v in box)
                or not 0 <= box[0] < box[2] <= geometry['width']
                or not 0 <= box[1] < box[3] <= geometry['height']):
            raise ValueError('Invalid preserved source geometry')
        if row.get('serialized') and row['serialized'].strip() not in parts.get(row['page'], ''):
            raise ValueError('Preserved source object missing from final Markdown')
        if row.get('asset') and sha(within(output, row['asset'])) != row['asset_sha256']:
            raise ValueError('Preserved source image bytes changed')
        if report['version'] == VERSION and row['state'] == 'SOURCE_FIGURE_REGION_PRESERVED':
            evidence = row['region_evidence']
            region = pymupdf.Rect(box)
            if not region.contains(pymupdf.Rect(row['caption_bbox'])):
                raise ValueError('Preserved figure crop omits its source caption')
            if evidence['basis'] != 'SOURCE_ENCLOSING_RECTANGLE':
                if evidence.get('bbox') != box or not evidence.get('source_drawing_regions') or any(
                        not region.contains(pymupdf.Rect(b)) for b in evidence['source_drawing_regions']):
                    raise ValueError('Preserved figure drawing geometry differs')
    if report['version'] == VERSION:
        _validate_bindings(output, report, mapping)
    if dict(Counter(r['state'] for r in report['records'])) != report['states']:
        raise ValueError('Source preservation counts differ')
    if report['whole_document_content_percent'] is not None or report['content_gate_passed'] != (
            not any(report['states'].get(state, 0) for state in REVIEW_STATES)):
        raise ValueError('Source preservation gate is inconsistent with evidence')
    if len({r['id'] for r in report['records']}) != len(report['records']):
        raise ValueError('Duplicate source preservation object')
    summary = {key: report[key] for key in ('states', 'whole_document_content_percent', 'content_gate_passed', 'source_scope')}
    quality_path = output / 'quality_report.json'
    if quality_path.is_file() and read(quality_path).get('content_preservation') != summary:
        raise ValueError('Quality report source-preservation summary differs')
    if quality_path.is_file() and not summary['content_gate_passed'] and read(quality_path).get('status') in (
            'SUCCESS', 'SUCCESS_WITH_WARNINGS'):
        raise ValueError('Unresolved source objects cannot report successful content checks')
    return summary


def _validate_bindings(output, report, mapping):
    path = output / 'native_manual.md'
    original = (path if path.is_file() else output / 'manual.md').read_text(encoding='utf-8')
    if digest(original) != report['preserved_markdown_sha256']:
        raise ValueError('Preserved table binding text differs')
    seen = set()
    for row in report['records']:
        if row['state'] not in ('SOURCE_GRID_MATCH', 'REBUILT_SOURCE_GRID'):
            continue
        bindings = row.get('bindings', [])
        if len(bindings) != len(row['source_tables']):
            raise ValueError('Missing preserved table bindings')
        for item, table in zip(bindings, row['source_tables']):
            a, b = item['start'], item['end']
            if (type(a) is not int or type(b) is not int or not 0 <= a < b <= len(original)
                    or (a, b) in seen or digest(original[a:b]) != item['sha256']
                    or item['source_bbox'] != table['bbox']):
                raise ValueError('Preserved table binding span or geometry differs')
            seen.add((a, b))
            matches = [r for r in mapping['objects'] if r['page'] == row['page'] and r['kind'] == 'table'
                       and digest(r['text']) == item['sha256'] and len(r['sources']) == 1
                       and r['sources'][0]['bbox'] == item['source_bbox']
                       and r['sources'][0]['basis'] == 'SOURCE_PRESERVATION_GEOMETRY']
            if len(matches) != 1:
                raise ValueError('Preserved table source mapping differs')
