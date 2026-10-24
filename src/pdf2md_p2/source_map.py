"""Object-level verification index. Association is not semantic correctness."""
from collections import Counter
from html.parser import HTMLParser
import math
from pathlib import Path
import re

from markdown_it import MarkdownIt
import pymupdf

from .assets import references
from .common import PAGE, PRODUCT, digest, read, save, sha, verify, within
from .identifiers import source_blocks

MD = MarkdownIt('default', {'html': True, 'linkify': False})
KINDS = {'paragraph_title': 'heading', 'doc_title': 'heading', 'title': 'heading',
         'section-header': 'heading', 'table': 'table', 'image': 'image', 'picture': 'image',
         'formula': 'formula', 'interline_equation': 'formula', 'equation': 'formula',
         'list': 'list_item', 'list-item': 'list_item'}


class PlainHTML(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []

    def handle_data(self, data):
        self.parts.append(data)


def visible(text):
    stripped = text.strip()
    if stripped.startswith('$$') and stripped.endswith('$$'):
        text = stripped[2:-2]
    pieces = []
    def visit(tokens):
        for token in tokens:
            if token.children:
                visit(token.children)
            elif token.type in ('text', 'code_inline', 'code_block', 'fence'):
                pieces.append(token.content)
            elif token.type in ('html_inline', 'html_block'):
                parser = PlainHTML()
                parser.feed(token.content)
                pieces.extend(parser.parts)
    visit(MD.parse(text))
    return ' '.join(' '.join(pieces).split())


def objects(text):
    """Offsets always address the final Unicode string, not bytes or earlier stages."""
    records = []
    markers = list(PAGE.finditer(text))
    for page_index, marker in enumerate(markers):
        body_start = marker.end()
        end = markers[page_index + 1].start() if page_index + 1 < len(markers) else len(text)
        body = text[body_start:end]
        offsets = [0]
        for line in body.splitlines(keepends=True):
            offsets.append(offsets[-1] + len(line))
        covered = set()
        list_depth = 0
        for token in MD.parse(body):
            if token.type in ('bullet_list_open', 'ordered_list_open'):
                list_depth += 1
            elif token.type in ('bullet_list_close', 'ordered_list_close'):
                list_depth -= 1
            if token.map is None or token.type not in ('heading_open', 'paragraph_open', 'table_open', 'html_block', 'fence', 'code_block', 'hr'):
                continue
            a, b = (body_start + offsets[i] for i in token.map)
            if (a, b) in covered:
                continue
            covered.add((a, b))
            raw = text[a:b]
            if not raw.strip():
                continue
            kind = ('heading' if token.type == 'heading_open' else 'table' if token.type == 'table_open'
                    or re.match(r'\s*<table\b', raw, re.I) else 'list_item' if list_depth else
                    'formula' if raw.strip().startswith('$$') else 'image' if references(raw) else 'paragraph')
            records.append({'page': int(marker[1]), 'kind': kind, 'start': a, 'end': b, 'text': raw})
        # Retain nonempty syntax fragments not claimed by a standard block token.
        for index, line in enumerate(body.splitlines(keepends=True)):
            a, b = body_start + offsets[index], body_start + offsets[index + 1]
            if line.strip() and not any(r['page'] == int(marker[1]) and r['start'] <= a < r['end'] for r in records):
                records.append({'page': int(marker[1]), 'kind': 'paragraph', 'start': a, 'end': b, 'text': line})
    return sorted(records, key=lambda row: (row['start'], row['end']))


def _content(node):
    if isinstance(node, str):
        return node
    if isinstance(node, list):
        return ' '.join(_content(part) for part in node)
    if isinstance(node, dict):
        return _content(node.get('content', ''))
    return ''


def _images(node):
    if isinstance(node, dict):
        if node.get('image_path'):
            yield Path(node['image_path']).name
        yield from _images(node.get('content'))
    elif isinstance(node, list):
        for part in node:
            yield from _images(part)


def native_objects(output, chunks, pages):
    result = []
    for chunk in chunks:
        path = within(output, chunk['attempt']) / 'native_output/native.json'
        if not path.is_file():
            continue
        data = read(path)
        for entry in data['pages']:
            number = entry['page']
            geometry = pages[str(number)]
            if 'page_boxes' in entry:
                for box in entry['page_boxes']:
                    a, b = box['pos']
                    text = entry['text'][a:b]
                    result.append({'page': number, 'bbox': list(box['bbox']),
                        'kind': KINDS.get(box['class'], 'paragraph'), 'text': text,
                        'images': [Path(p).name for p in references(text)],
                        'basis': 'CPU_PAGE_BOX_AND_NATIVE_TEXT_RANGE', 'native_index': box['index']})
            elif 'middle_json' in entry:
                middle = read(within(path.parent.parent, entry['middle_json']))
                def append(block):
                    bbox = block.get('bbox')
                    if bbox is not None:
                        result.append({'page': number, 'bbox': [bbox[0] * geometry['width'], bbox[1] * geometry['height'],
                            bbox[2] * geometry['width'], bbox[3] * geometry['height']],
                            'kind': KINDS.get(block['type'], 'paragraph'), 'text': _content(block),
                            'images': list(_images(block)), 'basis': 'GPU_NATIVE_LAYOUT_OBJECT',
                            'native_index': block.get('index')})
                    if block['type'] == 'image':
                        for child in block.get('content', []):
                            if child.get('type') in ('image_caption', 'image_footnote'):
                                append(child)
                for page in middle['pages']:
                    for block in page['blocks']:
                        append(block)
    return result


def valid_bbox(box, geometry):
    return (isinstance(box, list) and len(box) == 4 and all(type(v) in (int, float) and math.isfinite(v) for v in box)
            and 0 <= box[0] < box[2] <= geometry['width'] and 0 <= box[1] < box[3] <= geometry['height'])


def build(pdf, output, chunks):
    text = (output / 'manual.md').read_text(encoding='utf-8')
    input_sha = sha(pdf)
    records, pages, independent = objects(text), {}, []
    with pymupdf.open(pdf) as document:
        for number, page in enumerate(document, 1):
            original_rotation = page.rotation
            page.set_rotation(0)
            pages[str(number)] = {'width': page.rect.width, 'height': page.rect.height, 'rotation': original_rotation,
                                  'coordinate_system': 'unrotated_page_top_left_pdf_points'}
            independent.extend({'page': number, 'bbox': row['bbox'], 'text': row['text'],
                                'kind': 'paragraph', 'basis': 'INDEPENDENT_PDF_TEXT_BLOCK'} for row in source_blocks(page))
        native = native_objects(output, chunks, pages)
        for ledger, group, bbox_key in (('source_glyphs.json', 'records', 'source_bbox'),
                                        ('source_graphics.json', 'pages', 'source_bbox'),
                                        ('source_text_pages.json', 'records', 'source_bbox')):
            if not (output / ledger).is_file():
                continue
            for row in read(output / ledger)[group]:
                if row.get('automatic_addition', True) is False:
                    continue
                native.append({'page': row['page'], 'bbox': row[bbox_key], 'kind': 'image' if row.get('asset') else 'paragraph',
                               'text': row.get('serialized', ''), 'images': [Path(row['asset']).name] if row.get('asset') else [],
                               'basis': 'RECORDED_SOURCE_ADDITION'})
        counts = Counter((row['page'], row['kind'], visible(row['text'])) for row in records)
        for row in records:
            key, urls = visible(row['text']), {Path(p).name for p in references(row['text'])}
            candidates = []
            for source in native:
                if source['page'] != row['page'] or source['kind'] != row['kind']:
                    continue
                if (urls and urls.intersection(source.get('images', []))) or (key and key == visible(source['text'])):
                    candidates.append(source)
            pdf_matches = [source for source in independent if source['page'] == row['page'] and key
                           and key == ' '.join(source['text'].split())]
            if not candidates:
                candidates = pdf_matches
            # Reversible identifier changes retain their independently recorded source locus.
            if not candidates and (output / 'change_log.json').is_file():
                for edit in read(output / 'change_log.json')['operations']:
                    if edit['page'] == row['page'] and row['start'] <= edit['output_start'] < row['end']:
                        candidates.append({'page': row['page'], 'bbox': edit['source_bbox'],
                            'text': edit['source_context'], 'basis': 'RECORDED_SOURCE_BOUND_REPAIR'})
            candidates = [source for source in candidates if valid_bbox(source['bbox'], pages[str(row['page'])])]
            # Duplicated output occurrences cannot claim a single original by text alone.
            unique = len(candidates) == 1 and counts[(row['page'], row['kind'], key)] == 1
            if unique and pdf_matches and not any(pymupdf.Rect(candidates[0]['bbox']).intersects(pymupdf.Rect(p['bbox'])) for p in pdf_matches):
                unique = False
            status = 'UNMAPPED' if not candidates else 'AMBIGUOUS'
            sources = []
            for source in candidates:
                page = document[source['page'] - 1]
                page.set_rotation(0)
                excerpt = page.get_text(clip=pymupdf.Rect(source['bbox']))
                sources.append({'page': source['page'], 'bbox': source['bbox'], 'source_text': excerpt,
                                'basis': source['basis'], 'native_index': source.get('native_index')})
            if unique:
                status = 'MATCHED_SOURCE_TEXT' if candidates[0]['basis'] == 'INDEPENDENT_PDF_TEXT_BLOCK' else 'ASSOCIATED_NATIVE_LAYOUT'
            row.update(id='obj-' + digest(f"{input_sha}:{row['page']}:{row['kind']}:{row['start']}:{row['end']}")[:20],
                       line_start=text.count('\n', 0, row['start']) + 1,
                       line_end=text.count('\n', 0, max(row['start'], row['end'] - 1)) + 1,
                       status=status, sources=sources, semantic_correctness='NOT_JUDGED')
    assets = {p: sha(within(output, p)) for p in dict.fromkeys(references(text))}
    mapping = {'schema_version': 1, 'input_sha256': input_sha, 'markdown_sha256': sha(output / 'manual.md'),
               'offset_unit': 'unicode_codepoint', 'page_numbering': 'physical_1_based',
               'pages': pages, 'assets': assets, 'objects': records,
               'states': dict(Counter(row['status'] for row in records)),
               'semantic_correctness_certified': False}
    save(output / 'source_map.json', mapping)
    validate(output)
    return mapping


def validate(output):
    data = read(output / 'source_map.json')
    text = (output / 'manual.md').read_text(encoding='utf-8')
    if data['schema_version'] != 1 or data['offset_unit'] != 'unicode_codepoint' or sha(output / 'manual.md') != data['markdown_sha256']:
        raise ValueError('Source map version, offsets or Markdown binding differs')
    if data['semantic_correctness_certified'] is not False or data['page_numbering'] != 'physical_1_based':
        raise ValueError('Unsupported source map semantics or page numbering')
    manifest = output / 'configuration.json'
    if manifest.is_file() and read(manifest)['input_sha256'] != data['input_sha256']:
        raise ValueError('Source map PDF binding differs')
    if (output / 'input_preflight.json').is_file():
        inspection = read(output / 'input_preflight.json')
        if set(data['pages']) != {str(i) for i in range(1, inspection['pages'] + 1)}:
            raise ValueError('Source map physical page inventory differs')
        for page in inspection['page_details']:
            geometry = data['pages'][str(page['page'])]
            width, height = page['width'], page['height']
            if page['rotation'] % 180:
                width, height = height, width
            if (geometry['width'], geometry['height'], geometry['rotation']) != (width, height, page['rotation']):
                raise ValueError('Source map geometry differs from input inspection')
    ids = set()
    expected = objects(text)
    if [(r['page'], r['kind'], r['start'], r['end'], r['text']) for r in data['objects']] != [
            (r['page'], r['kind'], r['start'], r['end'], r['text']) for r in expected]:
        raise ValueError('Source map does not index the final output objects')
    for row in data['objects']:
        if not re.fullmatch(r'obj-[0-9a-f]{20}', row['id']) or row['id'] in ids:
            raise ValueError('Invalid or duplicate mapping ID')
        ids.add(row['id'])
        expected_id = 'obj-' + digest(f"{data['input_sha256']}:{row['page']}:{row['kind']}:{row['start']}:{row['end']}")[:20]
        if row['id'] != expected_id or row['semantic_correctness'] != 'NOT_JUDGED':
            raise ValueError('Source map ID binding or semantics differs')
        if (row['line_start'] != text.count('\n', 0, row['start']) + 1 or
                row['line_end'] != text.count('\n', 0, max(row['start'], row['end'] - 1)) + 1):
            raise ValueError('Source map line range differs')
        if row['status'] not in ('UNMAPPED', 'AMBIGUOUS', 'MATCHED_SOURCE_TEXT', 'ASSOCIATED_NATIVE_LAYOUT'):
            raise ValueError('Invalid mapping status')
        if (row['status'] == 'UNMAPPED') != (not row['sources']) or (
                row['status'] in ('MATCHED_SOURCE_TEXT', 'ASSOCIATED_NATIVE_LAYOUT') and len(row['sources']) != 1):
            raise ValueError('Mapping certainty conflicts with source candidates')
        for source in row['sources']:
            if source['page'] != row['page'] or not valid_bbox(source['bbox'], data['pages'][str(source['page'])]):
                raise ValueError('Source map page or coordinate is invalid')
    actual = {p: sha(within(output, p)) for p in dict.fromkeys(references(text))}
    if actual != data['assets'] or dict(Counter(r['status'] for r in data['objects'])) != data['states']:
        raise ValueError('Source map asset binding or counts differ')
    return data


def trace(output, object_id=None, line=None, pdf=None, render_dir=None):
    verify(output)
    data = validate(output)
    rows = [r for r in data['objects'] if r['id'] == object_id] if object_id else [
        r for r in data['objects'] if r['line_start'] <= line <= r['line_end']]
    if not rows:
        raise ValueError('No mapped output object matches this query')
    result = {'input_sha256': data['input_sha256'], 'markdown_sha256': data['markdown_sha256'],
              'offset_unit': data['offset_unit'], 'pages': data['pages'], 'objects': rows,
              'semantic_correctness_certified': False}
    if render_dir is not None:
        if pdf is None or sha(pdf) != data['input_sha256']:
            raise ValueError('Provide the exact source PDF for rendering; SHA256 must match')
        if (render_dir.is_relative_to(PRODUCT) or render_dir == output or render_dir.is_relative_to(output) or output.is_relative_to(render_dir)
                or pdf.is_relative_to(render_dir)):
            raise ValueError('Evidence export must be separate from sealed output and source PDF')
        if render_dir.exists():
            raise ValueError('Evidence export directory exists; use a new directory')
        render_dir.mkdir(parents=True)
        images = []
        with pymupdf.open(pdf) as document:
            for row in rows:
                for index, source in enumerate(row['sources']):
                    page = document[source['page'] - 1]
                    page.set_rotation(0)
                    name = f"{row['id']}-candidate-{index+1}.png"
                    page.get_pixmap(clip=pymupdf.Rect(source['bbox']), matrix=pymupdf.Matrix(2, 2), alpha=False).save(render_dir / name)
                    images.append({'object_id': row['id'], 'source': source, 'path': name,
                                   'sha256': sha(render_dir / name), 'orientation': 'unrotated_source'})
        result['evidence'] = images
        save(render_dir / 'evidence.json', result)
    return result
