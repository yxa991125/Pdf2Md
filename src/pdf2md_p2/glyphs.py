"""Render undecodable native glyphs at their existing inline positions, without OCR."""

from collections import Counter
import math

import pymupdf

from .common import sha, save, digest
from .common import PAGE
import re


def materialize(pdf, text, events, output):
    original = text
    records = []
    with pymupdf.open(pdf) as document:
        for event in events:
            if event['kind'] != 'SOURCE_GLYPH_VISUAL_REQUIRED_NO_OCR':
                continue
            marker = event['marker']
            count = text.count(marker)
            if count > 1:
                raise ValueError('Source glyph marker is multiply bound to native Markdown')
            page = document[event['page'] - 1]
            coordinates = event['source_bbox_points']
            if (len(coordinates) != 4 or any(not isinstance(v, (int, float)) or not math.isfinite(v)
                                            for v in coordinates)):
                raise ValueError('Invalid glyph source frame')
            frame = pymupdf.Rect(coordinates)
            if frame.is_empty or frame.is_infinite or not frame.intersects(page.rect):
                raise ValueError('Invalid glyph source frame')
            # MinerU spans use the displayed page; source-map boxes use the
            # unrotated page. Rendering and text extraction need different boxes.
            bbox = (frame + (-1, -1, 1, 1)) & page.rect
            if bbox.is_empty or bbox.is_infinite:
                raise ValueError('Invalid glyph source frame')
            source_bbox = bbox * page.derotation_matrix if page.rotation else bbox
            geometry = {'source_bbox': list(source_bbox),
                        'source_coordinate_system': 'unrotated_page_top_left_pdf_points',
                        'render_bbox': list(bbox),
                        'render_coordinate_system': 'rotated_page_top_left_pdf_points',
                        'page_rotation': page.rotation}
            relative = 'assets/source-glyph-' + marker[-24:].lower() + '.png'
            path = output / relative
            html = f'<img src="{relative}" width="{round(bbox.width, 2)}" height="{round(bbox.height, 2)}" alt="Source glyph region; not editable text">'
            placement = 'INLINE_NATIVE_SPAN'
            if count == 1:
                start, before, after = text.index(marker), marker, html
                text = text.replace(marker, html, 1)
            else:
                source_text = page.get_text(clip=source_bbox).strip()
                key = lambda value: ' '.join(re.sub(r'<[^>]+>', ' ', value).replace('\\_', '_').split())
                markers = list(PAGE.finditer(text))
                index = next(i for i, m in enumerate(markers) if int(m[1]) == event['page'])
                end = markers[index + 1].start() if index + 1 < len(markers) else len(text)
                body = text[markers[index].end():end]
                if source_text and key(source_text) in key(body):
                    records.append({'page': event['page'], 'kind': 'SOURCE_NATIVE_TEXT_ALREADY_PRESENT',
                                    **geometry, 'automatic_addition': False})
                    continue
                # A native discarded/merged span has no trustworthy inline locus. Keep
                # its source fragment page-scoped, without inventing a semantic anchor.
                text = text[:end] + '\n\n' + html + '\n\n' + text[end:]
                start, before, after = end, '', '\n\n' + html + '\n\n'
                placement = 'PAGE_SCOPED_SOURCE_FRAGMENT_ASSOCIATION_UNRESOLVED'
            page.get_pixmap(clip=bbox, matrix=pymupdf.Matrix(4, 4), alpha=False).save(path)
            records.append({'page': event['page'], **geometry, 'raster_scale': 4,
                'asset': relative, 'sha256': sha(path), 'native_marker': marker, 'replacement': html,
                'kind': 'SOURCE_GLYPH_IMAGE_AT_NATIVE_SPAN', 'complete_formula_or_figure_claim': False,
                'placement': placement,
                'start': start, 'before': before, 'after': after,
                'automatic_addition': True,
                'text_recognition_performed': False, 'gain_judgment': 'NOT_EVALUATED',
                'machine_readability': 'IMAGE_NOT_EDITABLE_CHARACTERS'})
    save(output / 'source_glyphs.json', {'records': records, 'states': dict(Counter(r['kind'] for r in records)),
         'before_sha256': digest(original), 'after_sha256': digest(text),
         'no_correct_native_character_string_replaced': True})
    return text, records
