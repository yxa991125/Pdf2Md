"""Associate actual table serialization and source drawing geometry, not answers."""

from collections import Counter
import json
import re

import pymupdf

from .objects import Tables


def table_key(text, renderer):
    parser = Tables()
    parser.feed(renderer.render(text))
    if len(parser.records) != 1 or parser.records[0]['issues']:
        return None
    return json.dumps([[(c['rowspan'], c['colspan'], ''.join(c['text'].split())) for c in row]
                       for row in parser.records[0]['rows']], ensure_ascii=True)


def table_locations(outputs, native, renderer):
    """The entire serialized sequence must agree before repeated tables get a rank."""
    actual = [(row, table_key(row['text'], renderer)) for row in outputs]
    layouts = [(row, table_key(row['text'], renderer)) for row in native if row['kind'] == 'table']
    layouts = [(row, key) for row, key in layouts if key is not None]
    a_counts, n_counts = Counter(key for _, key in actual), Counter(key for _, key in layouts)
    indices = [row.get('native_index') for row, _ in layouts]
    ordered = (len(actual) == len(layouts) and [key for _, key in actual] == [key for _, key in layouts]
               and all(isinstance(i, int) for i in indices) and indices == sorted(set(indices)))
    result = {}
    for index, (row, key) in enumerate(actual):
        if key is None:
            continue
        choices = [n for n, k in layouts if k == key]
        basis = 'UNIQUE_NATIVE_TABLE_SERIALIZATION'
        if a_counts[key] == n_counts[key] == 1:
            chosen, = choices
        elif ordered:
            chosen = layouts[index][0]
            basis = 'COMPLETE_NATIVE_TABLE_SERIALIZATION_ORDER'
        else:
            continue
        result[row['start']] = {'bbox': chosen['bbox'], 'basis': basis,
                                'native_index': chosen.get('native_index')}
    return result


def spatial_tables(tables, outputs, locations):
    selected = []
    for table in tables:
        target = pymupdf.Rect(table['bbox'])
        matches = []
        for row in outputs:
            if row['start'] not in locations:
                continue
            region = pymupdf.Rect(locations[row['start']]['bbox'])
            overlap = (target & region).get_area()
            if target.get_area() and region.get_area() and overlap/target.get_area() >= .8 and overlap/region.get_area() >= .7:
                matches.append(row)
        if len(matches) != 1 or matches[0] in selected:
            return []
        selected.extend(matches)
    return sorted(selected, key=lambda r: r['start'])


def drawing_region(page, caption, captions, native):
    target = pymupdf.Rect(caption['bbox'])
    label = re.compile(r'\b(?:Figure|Fig\.)\s*'+re.escape(caption['label'])+r'(?![\w])', re.I)
    hints = [pymupdf.Rect(row['bbox']) for row in native
             if row['kind'] == 'image' and label.search(row['text'])]
    clusters = [pymupdf.Rect(r) for r in page.cluster_drawings()
                if r.width > 8 and r.height > 8 and r.y1 <= target.y0+1]
    if len(hints) > 1:
        return None
    if hints:
        seed, = hints
        if not 0 <= target.y0-seed.y1 <= target.height*4:
            return None
        selected = [r for r in clusters if (r & seed).get_area() >= r.get_area()*.5]
        if not selected:
            return None
        basis = 'NATIVE_FIGURE_LOCUS_AND_SOURCE_DRAWINGS'
    else:
        near = [r for r in clusters if 0 <= target.y0-r.y1 <= target.height*4
                and max(0, min(r.x1, target.x1)-max(r.x0, target.x0)) >= min(r.width, target.width)*.5]
        if not near:
            return None
        bottom = max(r.y1 for r in near)
        selected = [r for r in near if abs(r.y1-bottom) < 4]
        # Multiple columns without an independent native locus are ambiguous.
        if len(selected) != 1:
            return None
        seed = selected[0]
        basis = 'UNIQUE_SOURCE_DRAWING_AND_CAPTION'
    region = pymupdf.Rect(seed)
    for rect in selected:
        region |= rect
    for rect in clusters:
        vertical_overlap = max(0, min(rect.y1, region.y1)-max(rect.y0, region.y0))
        if rect in selected or vertical_overlap < min(rect.height, region.height)*.5:
            continue
        owners = [other for other in captions if other is not caption
                  and 0 <= other['bbox'][1]-rect.y1 <= target.height*4
                  and max(0, min(rect.x1, other['bbox'][2])-max(rect.x0, other['bbox'][0]))
                      >= min(rect.width, other['bbox'][2]-other['bbox'][0])*.5]
        if not owners:
            return None
    region |= target
    anchor = pymupdf.Rect(region)
    # Include complete source label lines near the drawing, not just the vector paths.
    for block in page.get_text('dict')['blocks']:
        for line in block.get('lines', []):
            rect = pymupdf.Rect(line['bbox'])
            size = max(s['size'] for s in line['spans'])
            margin = size*(1.5 if hints else 3)
            horizontal_overlap = max(0, min(rect.x1, anchor.x1+margin)-max(rect.x0, anchor.x0-margin))
            if rect.y0 >= seed.y0-size and rect.y1 <= region.y1 and (
                    horizontal_overlap >= rect.width*.5):
                region |= rect
    region = (region+(-1, -1, 1, 1)) & page.rect
    if any(other is not caption and (region & pymupdf.Rect(other['bbox'])).get_area() > 0 for other in captions):
        return None
    if region.get_area() > page.rect.get_area()*.75:
        return None
    for block in page.get_text('rawdict')['blocks']:
        for line in block.get('lines', []):
            for span in line['spans']:
                for char in span['chars']:
                    rect = pymupdf.Rect(char['bbox'])
                    if not char['c'].isspace() and (rect & region).get_area() > .2 and not region.contains(rect):
                        return None
    return {'bbox': list(region), 'basis': basis, 'native_hint': list(seed) if hints else None,
            'source_drawing_regions': [list(r) for r in selected]}
