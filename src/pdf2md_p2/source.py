"""Native PDF preflight and source-layer occurrences, not a new document IR."""

import pymupdf

from .common import page_parts


def preflight(pdf):
    rows = []
    with pymupdf.open(pdf) as document:
        if document.needs_pass or not document.is_pdf or not len(document):
            raise ValueError('Readable unencrypted PDF required')
        for number, page in enumerate(document, 1):
            text = page.get_text()
            images = len(page.get_images())
            vectors = len(page.get_drawings())
            count = len(text.strip())
            rows.append({'page': number, 'native_characters': count, 'width': page.rect.width,
                         'height': page.rect.height, 'rotation': page.rotation,
                         'images': images, 'vector_paths': vectors,
                         'kind': 'NATIVE_TEXT' if count >= 80 else 'GRAPHIC_OR_LOW_TEXT',
                         'replacement_characters': text.count('\ufffd')})
    native = sum(r['native_characters'] >= 80 for r in rows)
    total_chars = sum(r['native_characters'] for r in rows)
    supported = total_chars >= 80 and native >= max(1, len(rows) * .2)
    return {'supported': supported, 'pages': len(rows), 'native_text_pages': native,
            'scope': 'Native electronic PDF; low-text graphics pages retained, not OCRed',
            'scan_classifier_limit': 'Heuristic text-layer screening; mixed raster/text ambiguity is reported, not inferred as OCR-safe',
            'page_details': rows}


def source_lines(page):
    for block in page.get_text('dict')['blocks']:
        for line in block.get('lines', []):
            spans = line['spans']
            yield {'text': ''.join(s['text'] for s in spans), 'bbox': list(line['bbox']),
                   'spans': [{'text': s['text'], 'bbox': list(s['bbox']), 'size': s['size']} for s in spans]}


def empty_content_pages(inspection, markdown):
    parts = page_parts(markdown)
    return [{'page': row['page'], 'state': 'SOURCE_CONTENT_PRESENT_OUTPUT_EMPTY',
             'native_characters': row['native_characters'], 'images': row['images'], 'vector_paths': row['vector_paths']}
            for row in inspection['page_details']
            if row['page'] in parts and not parts[row['page']].strip()
            and (row['native_characters'] > 0 or row['images'] > 0 or row['vector_paths'] > 0)]


def graphic_page(pdf, number, output):
    """Preserve a zero-text vector-only source page without recognizing its labels."""
    from .common import sha
    path = output / 'assets' / f'source-vector-page-{number:04d}.png'
    path.parent.mkdir(exist_ok=True)
    with pymupdf.open(pdf) as document:
        page = document[number - 1]
        if page.get_text().strip() or page.get_images() or not page.get_drawings():
            raise ValueError('Source-only graphic admission no longer matches')
        page.get_pixmap(matrix=pymupdf.Matrix(4, 4), alpha=False).save(path)
        record = {'page': number, 'source_bbox': list(page.rect), 'rotation': page.rotation,
                  'asset': path.relative_to(output).as_posix(), 'sha256': sha(path),
                  'native_text_characters': 0, 'role': 'COMPLETE_VECTOR_ONLY_SOURCE_PAGE',
                  'representation': 'SOURCE_GRAPHIC_NOT_EDITABLE_TEXT', 'neural_inference_performed': False,
                  'gain_judgment': 'NOT_EVALUATED', 'raster_scale': 4}
    return f'![Source graphic, PDF page {number}]({record["asset"]})\n', record
