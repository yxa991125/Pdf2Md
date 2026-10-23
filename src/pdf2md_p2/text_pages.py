"""Recover an actually empty native output page only from unambiguous source text."""

import html
import pymupdf

from .common import PAGE, digest, page_parts, save, sha


def recover(pdf, text, output):
    original = text
    records = []
    parts = page_parts(text)
    with pymupdf.open(pdf) as document:
        for number in sorted(parts):
            if parts[number].strip():
                continue
            page = document[number - 1]
            source = page.get_text(sort=True)
            if not source.strip() or '\ufffd' in source or page.get_images() or page.get_drawings():
                continue
            paragraphs = [p for p in page.get_text('blocks') if p[6] == 0 and len(p[4].strip()) >= 60]
            if not paragraphs or max(p[0] for p in paragraphs) - min(p[0] for p in paragraphs) > page.rect.width * .1:
                continue
            if not any(p[2] - p[0] >= page.rect.width * .6 for p in paragraphs):
                continue
            serialized = '<pre>' + html.escape(source) + '</pre>'
            markers = list(PAGE.finditer(text))
            index = next(i for i, m in enumerate(markers) if int(m[1]) == number)
            a, b = markers[index].end(), markers[index + 1].start() if index + 1 < len(markers) else len(text)
            before = text[a:b]
            text = text[:a] + '\n\n' + serialized + '\n\n' + text[b:]
            records.append({'page': number, 'kind': 'SOURCE_ONLY_TEXT_PAGE_RECOVERY',
                            'start': a, 'before': before, 'after': '\n\n' + serialized + '\n\n',
                            'native_before': before, 'source_bbox': list(page.rect), 'source_text': source,
                            'source_text_sha256': digest(source), 'serialized': serialized,
                            'source_pdf_sha256': sha(pdf), 'state': 'APPLIED_SOURCE_LITERAL',
                            'gain_judgment': 'NOT_EVALUATED', 'footnote_role_or_anchor_claim': False,
                            'basis': 'Native output empty; source is nonempty single-column text without graphics'})
    save(output / 'source_text_pages.json', {'records': records, 'before_sha256': digest(original),
                                           'after_sha256': digest(text)})
    return text, records
