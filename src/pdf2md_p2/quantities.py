"""Conservative source-context quantity checks; never rewrite a technical value."""

from collections import Counter
from decimal import Decimal, InvalidOperation
import re

import pymupdf

from .common import page_parts
from .identifiers import source_blocks, words

NUMBER = r'[+\-\u2212\u2013]?\d+(?:\.\d+)?'
UNIT = r'(?:GHz|MHz|kHz|Hz|mV|uV|\u00b5V|\u03bcV|V|mA|uA|\u00b5A|\u03bcA|A|mg|ug|\u00b5g|\u03bcg|g|ns|us|\u00b5s|\u03bcs|ms|s|pF|nF|uF|\u00b5F|\u03bcF|F|\u00b0C|\u00b0F|k\u03a9|\u03a9)'
QUANTITY = re.compile(r'(?<![\w.])(?P<number>' + NUMBER + r')[ \t]*(?P<unit>' + UNIT + r')(?!\w)')


def equivalent(source, actual):
    a, b = QUANTITY.fullmatch(source.strip()), QUANTITY.fullmatch(actual.strip())
    if not a or not b:
        return None
    normalize = lambda s: s.translate(str.maketrans({'\u2212': '-', '\u2013': '-', '\u00b5': 'u', '\u03bc': 'u'}))
    try:
        return Decimal(normalize(a['number'])) == Decimal(normalize(b['number'])) and normalize(a['unit']) == normalize(b['unit'])
    except InvalidOperation:
        return None


def check(pdf, markdown):
    parts, records = page_parts(markdown), []
    with pymupdf.open(pdf) as document:
        for page, text in parts.items():
            candidates = list(QUANTITY.finditer(text))
            for block in source_blocks(document[page - 1]):
                for item in QUANTITY.finditer(block['text']):
                    left, right = words(block['text'][:item.start()])[-3:], words(block['text'][item.end():])[:3]
                    matches = []
                    for match in candidates:
                        l, r = words(text[:match.start()]), words(text[match.end():])
                        if len(left) >= 2 and len(right) >= 2 and l[-len(left):] == left and r[:len(right)] == right:
                            matches.append(match[0])
                    status = 'QUANTITY_CONTEXT_UNRESOLVED'
                    if len(matches) == 1:
                        status = 'QUANTITY_SOURCE_CONTEXT_MATCH' if equivalent(item[0], matches[0]) else 'QUANTITY_CONTEXT_DISTORTION'
                    records.append({'page': page, 'source_bbox': block['bbox'], 'source_quantity': item[0],
                                    'source_context': block['text'], 'actual_candidates': matches, 'state': status,
                                    'automatic_rewrite': False})
    return {'version': 'source-quantity-check-0.1.0', 'records': records,
            'states': dict(Counter(r['state'] for r in records)), 'quantity_correctness_not_global_semantic_certification': True}
