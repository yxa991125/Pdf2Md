"""Bounded source-object risk checks, not a semantic document IR or repair engine."""

from collections import Counter
from html.parser import HTMLParser
import re

import pymupdf

from .assets import references
from .common import page_parts
from .source import source_lines


class Tables(HTMLParser):
    def __init__(self):
        super().__init__()
        self.records = []
        self.current = None
        self.cell = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'table':
            self.current = {'rows': [], 'issues': []}
        elif self.current is not None and tag == 'tr':
            self.current['rows'].append([])
        elif self.current is not None and tag in ('td', 'th'):
            try:
                span = [int(attrs.get('rowspan', 1)), int(attrs.get('colspan', 1))]
                if min(span) < 1:
                    raise ValueError()
            except ValueError:
                self.current['issues'].append('INVALID_SPAN')
                span = [1, 1]
            self.cell = {'text': '', 'rowspan': span[0], 'colspan': span[1]}
            if not self.current['rows']:
                self.current['issues'].append('CELL_WITHOUT_ROW')
                self.current['rows'].append([])
            self.current['rows'][-1].append(self.cell)

    def handle_endtag(self, tag):
        if tag in ('td', 'th'):
            self.cell = None
        elif tag == 'table' and self.current is not None:
            self.records.append(self.current)
            self.current = None

    def handle_data(self, data):
        if self.cell is not None:
            self.cell['text'] += data


def source_role(text, bottom=False, repetitions=0):
    """Report evidence-qualified role hints; a grid alone never proves a data table."""
    if bottom and repetitions >= 3 and not re.match(r'\s*(?:\(\d+\)|\[\d+\])', text):
        if re.search(r'copyright|submit documentation feedback|product (?:folder )?links|rev\.?.*\bof\b', text, re.I):
            return 'PUBLICATION_FOOTER'
    if re.search(r'functional block diagrams?|circuit diagram|schematic', text, re.I):
        return 'CIRCUIT_OR_BLOCK_DIAGRAM_CANDIDATE'
    if re.search(r'histogram|noise.*plot|occurrence.*codes', text, re.I | re.S):
        return 'CHART_CANDIDATE'
    if re.search(r'\bbit\b.*\b(?:name|field)\b', text, re.I | re.S):
        return 'REGISTER_LAYOUT_OR_TABLE_UNRESOLVED'
    if len(set(re.findall(r'\b(?:min|max|typ|unit|parameter|address|reset|description)\b', text.lower()))) >= 2:
        return 'TECHNICAL_TABLE_CANDIDATE_NOT_CERTIFIED'
    if re.match(r'\s*(?:\(\d+\)|\[\d+\])\s+\S', text):
        return 'SOURCE_NOTE_CANDIDATE_ANCHOR_UNRESOLVED'
    return 'SOURCE_ROLE_UNRESOLVED'


def formula_representation(expression, closed=True):
    """Lexical damage detection only. Balanced syntax is not mathematical proof."""
    if not expression.strip():
        return 'FORMULA_MISSING'
    balance = 0
    for match in re.finditer(r'(?<!\\)[{}]', expression):
        balance += 1 if match[0] == '{' else -1
        if balance < 0:
            return 'FORMULA_PARTIAL'
    if not closed or balance:
        return 'FORMULA_PARTIAL'
    if re.search(r'(?:=|[+*/-]|\\(?:leq?|geq?|times|cdot))\s*$', expression):
        return 'FORMULA_PARTIAL'
    # A numerator with no second argument is a demonstrably partial fraction.
    for match in re.finditer(r'\\(?:d?frac|tfrac)\s*\{', expression):
        depth, position = 1, match.end()
        while position < len(expression) and depth:
            if expression[position] == '{' and expression[position - 1] != '\\':
                depth += 1
            elif expression[position] == '}' and expression[position - 1] != '\\':
                depth -= 1
            position += 1
        if not expression[position:].lstrip().startswith('{'):
            return 'FORMULA_PARTIAL'
    return 'EDITABLE_MATH'


def figure_frame(target, actual, overlays=(), source_transform=None, display_transform_applied=None):
    expected, observed = pymupdf.Rect(target), pymupdf.Rect(actual)
    if not observed.contains(expected) or any(not observed.contains(pymupdf.Rect(box)) for box in overlays):
        return 'FIGURE_FRAME_PARTIAL'
    if source_transform is not None:
        a, b, c, d, _, _ = source_transform
        if a * d - b * c < 0 and display_transform_applied is not True:
            return 'FIGURE_TRANSFORM_UNRESOLVED'
    return 'FRAME_GEOMETRY_ONLY_NOT_SEMANTIC_CERTIFICATION'


def symbol_risks(expression, source):
    # This only isolates a source-unsupported operator, never guesses a replacement.
    if re.search(r'\\nabla(?![A-Za-z])', expression) and '\u2207' not in source and not re.search(r'\bnabla\b', source, re.I):
        return ['FORMULA_SOURCE_SYMBOL_CONFLICT_SUSPECT']
    return []


def check(pdf, markdown):
    parts = page_parts(markdown)
    page_data = []
    repetitions = Counter()
    with pymupdf.open(pdf) as document:
        for number, page in enumerate(document, 1):
            lines = list(source_lines(page))
            signatures = set()
            for line in lines:
                if line['bbox'][1] >= page.rect.height * .9:
                    signature = re.sub(r'\d+', '#', line['text'].strip().lower())
                    signatures.add(signature)
            repetitions.update(signatures)
            page_data.append((number, page.rect.height, lines))
    records = []
    for number, height, lines in page_data:
        if number not in parts:
            continue
        text = parts[number]
        source = '\n'.join(line['text'] for line in lines)
        role = source_role(source)
        tables = Tables()
        tables.feed(text)
        pipe_tables = bool(re.search(r'^\s*\|?\s*:?-{3,}:?\s*\|', text, re.M))
        if tables.records or pipe_tables:
            records.append({'page': number, 'kind': 'TABLE_ROLE_AND_STRUCTURE', 'source_role_hint': role,
                'state': 'TABLE_ROLE_CONFLICT_SUSPECT' if role in ('CIRCUIT_OR_BLOCK_DIAGRAM_CANDIDATE', 'CHART_CANDIDATE')
                    else 'TABLE_OWNERSHIP_AND_HEADER_RELATIONS_UNRESOLVED',
                'native_HTML_tables': tables.records, 'native_pipe_table_present': pipe_tables,
                'grid_or_native_role_is_not_semantic_truth': True})
        # Explicit source figure captions are an independent hint, never a complete-frame oracle.
        captions = [line for line in lines if re.match(r'\s*(?:Figure|Fig\.)\s*\d+[.:]', line['text'])]
        if captions or references(text):
            records.append({'page': number, 'kind': 'FIGURE', 'source_role_hint': role,
                'state': 'FIGURE_COMPLETENESS_UNRESOLVED' if references(text) else 'FIGURE_REPRESENTATION_UNRESOLVED',
                'source_captions': captions, 'native_images': references(text),
                'caption_attachment': 'UNRESOLVED', 'pixel_authenticity_does_not_prove_complete_object': True})
        fences = list(re.finditer(r'\$\$', text))
        expressions = []
        for index in range(0, len(fences), 2):
            start = fences[index].end()
            closed = index + 1 < len(fences)
            end = fences[index + 1].start() if closed else len(text)
            expression = text[start:end]
            expressions.append(expression)
            records.append({'page': number, 'kind': 'FORMULA', 'state': formula_representation(expression, closed),
                'expression': expression, 'source_completeness': 'UNRESOLVED',
                'operands_units_scripts_and_relations': 'NOT_SYMBOLICALLY_CERTIFIED',
                'editable_representation_is_not_complete_source_equivalence': True})
        for match in re.finditer(r'(?<![\\$])\$(?!\$)([^\n$]+?)(?<!\\)\$(?!\$)', text):
            expressions.append(match[1])
        for expression in expressions:
            for risk in symbol_risks(expression, source):
                records.append({'page': number, 'kind': 'FORMULA_SOURCE_SYMBOL', 'state': risk,
                    'expression': expression, 'source_lines': lines,
                    'basis': 'Output nabla operator absent from independently read source text layer',
                    'source_encoding_or_object_mapping': 'UNRESOLVED', 'automatic_modifications': 0})
        for line in lines:
            bottom = line['bbox'][1] >= height * .9
            signature = re.sub(r'\d+', '#', line['text'].strip().lower())
            line_role = source_role(line['text'], bottom, repetitions[signature])
            if line_role == 'PUBLICATION_FOOTER':
                claim = re.search(r'(?:\[\^\w+\]:|footnote[^\n]*)\s*' + re.escape(line['text'].strip()), text, re.I)
                records.append({'page': number, 'kind': 'FOOTER', 'source_bbox': line['bbox'],
                    'source_text': line['text'], 'repeated_on_pages': repetitions[signature],
                    'state': 'FOOTER_MISLABELED_AS_NOTE' if claim else 'PUBLICATION_FOOTER_NOT_A_NOTE',
                    'source_role_basis': 'Publication literal + bottom geometry + cross-page repetition'})
            elif line_role == 'SOURCE_NOTE_CANDIDATE_ANCHOR_UNRESOLVED':
                records.append({'page': number, 'kind': 'NOTE', 'source_bbox': line['bbox'],
                    'source_text': line['text'], 'state': 'NOTE_ANCHOR_UNRESOLVED',
                    'automatic_link_created': False})
    return {'version': 'source-object-checks-0.2.0', 'records': records,
            'states': dict(Counter(row['state'] for row in records)), 'automatic_modifications': 0,
            'whole_document_fidelity_certified': False}
