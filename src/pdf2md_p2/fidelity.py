"""Source-grounded technical assessment, independent of document organization."""

from collections import Counter
import copy
import re
import time

import pymupdf
from markdown_it import MarkdownIt

from .assets import references
from .common import PAGE, digest, page_parts, sha
from .fidelity_math import UnsupportedMath, differences, formula_name, parse
from .fidelity_context import annotations, compare as compare_annotations
from .fidelity_source import formulas
from .quantities import check as check_quantities

VERSION = 'fidelity-1.0.0-dev1'


def output_formulas(markdown):
    offsets = {int(m[1]): m.end() for m in PAGE.finditer(markdown)}
    result = []
    for page, text in page_parts(markdown).items():
        line_offsets = [0]
        for line in text.splitlines(keepends=True):
            line_offsets.append(line_offsets[-1]+len(line))
        blocked = [(line_offsets[token.map[0]], line_offsets[token.map[1]])
                   for token in MarkdownIt('commonmark', {'html': True}).parse(text)
                   if token.type in ('fence', 'code_block') and token.map]
        display = [m for m in re.finditer(r'\$\$([\s\S]*?)\$\$', text)
                   if not any(a <= m.start() < b for a, b in blocked)]
        inline = [m for m in re.finditer(r'(?<![\\$])\$(?!\$)([^\n$]+)(?<!\\)\$(?!\$)', text)
                  if not any(d.start() <= m.start() < d.end() for d in display)
                  and not any(a <= m.start() < b for a, b in blocked)]
        for match in sorted(display+inline, key=lambda m: m.start()):
            expression = match[1]
            body, _, context = annotations(expression)
            arithmetic = True
            try:
                parsed = parse(body)
                arithmetic = parsed.tree[0] not in ('name', 'number')
            except UnsupportedMath:
                pass
            a, b = offsets[page] + match.start(1), offsets[page] + match.end(1)
            result.append({'id': 'fidelity-formula-' + digest(f'{page}:{a}:{b}:{expression}')[:20],
                'page': page, 'start': a, 'end': b, 'expression': expression,
                'name': formula_name(body), 'arithmetic': arithmetic, 'annotations': context,
                'arithmetic_expression': body,
                'representation': 'DISPLAY' if match in display else 'INLINE'})
        for index, line in enumerate(text.splitlines(keepends=True)):
            local = line_offsets[index]
            raw = line.strip()
            body, _, context = annotations(raw)
            if ('=' not in raw or '$' in raw or '<' in raw or '|' in raw or chr(96) in raw
                or any(a <= local < b for a, b in blocked)
                or any(m.start() <= local < m.end() for m in display+inline)
                or formula_name(body) is None):
                continue
            try:
                arithmetic = parse(body).tree[0] not in ('name', 'number')
            except UnsupportedMath:
                arithmetic = True
            a = offsets[page]+local+len(line)-len(line.lstrip())
            b = a+len(raw)
            result.append({'id': 'fidelity-formula-'+digest(f'{page}:{a}:{b}:{raw}')[:20],
                'page': page, 'start': a, 'end': b, 'expression': raw,
                'name': formula_name(body), 'arithmetic': arithmetic, 'representation': 'PLAIN',
                'annotations': context, 'arithmetic_expression': body})
    return sorted(result, key=lambda r: r['start'])


def _issue(pdf_hash, source, output, assessment, issue_type, detail):
    page = source['physical_page'] if source else output['page']
    source_id = source['id'] if source else 'none'
    output_id = output['id'] if output else 'none'
    return {'issue_id': 'f1-' + digest(f'{pdf_hash}:{source_id}:{output_id}:{issue_type}')[:20],
        'issue_type': issue_type, 'source_document_hash': pdf_hash, 'source_page': page,
        'source_regions': [source['source_region']] if source else [],
        'source_object_id': source_id, 'output_object_id': output_id,
        'output_location': {'start': output['start'], 'end': output['end'], 'offset_unit': 'unicode_codepoint'} if output else None,
        'expected_source_expression': source['source_expression'] if source else None,
        'output_representation': output['representation'] if output else None,
        'actual_output_expression': output['expression'] if output else None,
        'specific_difference': detail, 'assessment_status': assessment,
        'alignment_status': 'UNIQUE_FORMULA_NAME_AND_PAGE' if source and output and assessment not in (
            'ALIGNMENT_AMBIGUOUS', 'SOURCE_INSUFFICIENT') else assessment,
        'evidence_refs': {'source_object': source_id, 'source_extraction': source['source_extraction_status'] if source else None},
        'checked_attributes': ['literal_operands', 'arithmetic_structure', 'literal_scripts'] if assessment in ('SOURCE_MATCH', 'SOURCE_MISMATCH') else [],
        'unchecked_attributes': ['surrounding_condition_binding', 'equation_number', 'algebraic_equivalence_outside_supported_subset'],
        'whole_object_certified': False}


def bind_output_ids(report, mapping):
    if mapping['input_sha256'] != report['source_document_hash'] or mapping['markdown_sha256'] != report['markdown_sha256']:
        raise ValueError('Assessment and final source map bindings differ')
    for record in report['records']:
        location = record['output_location']
        if location is None:
            continue
        owners = [obj for obj in mapping['objects'] if obj['page'] == record['source_page']
                  and obj['start'] <= location['start'] and location['end'] <= obj['end']]
        record['fidelity_object_id'] = record['output_object_id']
        record['output_object_id'] = owners[0]['id'] if len(owners) == 1 else None
        record['evidence_refs']['source_map_candidates'] = [obj['id'] for obj in owners]
        if len(owners) != 1:
            record['evidence_refs']['assessment_before_map_binding'] = record['assessment_status']
            record['assessment_status'] = 'ALIGNMENT_AMBIGUOUS' if owners else 'NOT_CHECKED'
            record['alignment_status'] = 'OUTPUT_SOURCE_MAP_UNRESOLVED'
            record['checked_attributes'] = []
    counts = Counter(r['assessment_status'] for r in report['records'])
    report['assessment_states'] = dict(counts)
    report['summary'].update(checked=counts['SOURCE_MATCH']+counts['SOURCE_MISMATCH'],
        matched=counts['SOURCE_MATCH'], mismatched=counts['SOURCE_MISMATCH'],
        unresolved=counts['ALIGNMENT_AMBIGUOUS']+counts['SOURCE_INSUFFICIENT'],
        unchecked=counts['NOT_CHECKED'])
    return report


def assess(pdf, markdown, mapping=None, *, quantities=None, source_catalog=None):
    started = time.monotonic()
    pdf_hash = sha(pdf)
    output = output_formulas(markdown)
    parts = page_parts(markdown)
    sources, records = [], []
    source_started = time.monotonic()
    if source_catalog is None:
        with pymupdf.open(pdf) as document:
            for number, page in enumerate(document, 1):
                sources.extend(formulas(page, number, pdf_hash))
    else:
        if source_catalog['source_document_hash'] != pdf_hash:
            raise ValueError('Cached source catalog PDF differs')
        sources = source_catalog['source_objects']
    source_seconds = time.monotonic() - source_started
    used = set()
    for source in sources:
        if source['source_extraction_status'] != 'SUPPORTED_NATIVE_GEOMETRY':
            records.append(_issue(pdf_hash, source, None, 'SOURCE_INSUFFICIENT', 'SOURCE_FORMULA_NOT_INTERPRETED', source['reason']))
            continue
        same_sources = [s for s in sources if s['physical_page'] == source['physical_page']
                        and s['formula_name'] == source['formula_name'] and s['source_extraction_status'] == 'SUPPORTED_NATIVE_GEOMETRY']
        candidates = [o for o in output if o['page'] == source['physical_page'] and o['name'] == source['formula_name'] and o['arithmetic']]
        if len(same_sources) != 1 or len(candidates) > 1:
            records.append(_issue(pdf_hash, source, None, 'ALIGNMENT_AMBIGUOUS', 'FORMULA_ALIGNMENT_AMBIGUOUS',
                                  {'source_candidates': len(same_sources), 'output_candidates': len(candidates)}))
            used.update(o['id'] for o in candidates)
        elif len(candidates) == 1:
            observed, = candidates
            used.add(observed['id'])
            comparison = differences(source['source_expression'], observed['arithmetic_expression'])
            context = compare_annotations(source['annotations'], observed['annotations'])
            comparison['annotations'] = context
            if context['differences'] and comparison['status'] in ('SOURCE_MATCH', 'SOURCE_MISMATCH'):
                comparison['status'] = 'SOURCE_MISMATCH'
                comparison['differences'].extend(context['differences'])
            record = _issue(pdf_hash, source, observed, comparison['status'], 'FORMULA_SOURCE_COMPARISON', comparison)
            record['checked_attributes'].extend(context['checked'])
            record['unchecked_attributes'] = context['unchecked']+['algebraic_equivalence_outside_supported_subset']
            records.append(record)
        else:
            # An image may preserve the source formula. Do not call it content loss
            # without evidence that no source-associated image represents that region.
            images = references(parts.get(source['physical_page'], ''))
            plain_name = source['formula_name'].replace('__', '')
            name_elsewhere = bool(re.search(r'(?<!\w)'+re.escape(plain_name)+r'(?!\w)',
                                           parts.get(source['physical_page'], '')))
            assessment = 'NOT_CHECKED' if images or name_elsewhere else 'SOURCE_MISMATCH'
            records.append(_issue(pdf_hash, source, None, assessment,
                'EDITABLE_FORMULA_NOT_FOUND' if assessment == 'NOT_CHECKED' else 'FORMULA_OMITTED',
                'Image coverage or unsupported text representation unresolved' if assessment == 'NOT_CHECKED'
                else 'Supported source formula has no output formula'))
    for observed in output:
        if observed['id'] not in used:
            records.append(_issue(pdf_hash, None, observed, 'NOT_CHECKED', 'OUTPUT_FORMULA_NOT_ASSOCIATED',
                                  'No uniquely interpreted arithmetic source object'))
    quantity_started = time.monotonic()
    quantities = quantities if quantities is not None else check_quantities(pdf, markdown)
    quantity_seconds = time.monotonic() - quantity_started
    for row in quantities['records']:
        identity = f"{pdf_hash}:{row['quantity']['parent_object']}:{row['quantity']['raw_span']}"
        records.append({'issue_id': 'f1-quantity-' + digest(identity)[:20], 'issue_type': 'QUANTITY_SOURCE_COMPARISON',
            'source_document_hash': pdf_hash, 'source_page': row['page'], 'source_regions': [row['source_bbox']],
            'source_object_id': row['quantity']['parent_object'],
            'output_object_id': 'fidelity-quantity-' + digest(str(row['output_location']))[:20] if row['output_location'] else None,
            'output_location': row['output_location'], 'expected_source_expression': row['source_quantity'],
            'actual_output_expression': row['actual_candidates'][0] if row['actual_candidates'] else None,
            'specific_difference': row['state'], 'assessment_status': row['assessment_status'],
            'alignment_status': row['alignment_basis'] or row['assessment_status'],
            'evidence_refs': {'quantity_parent': row['quantity']['parent_object'], 'source_context': row['source_context']},
            'checked_attributes': ['quantity_value', 'quantity_sign', 'literal_unit'] if row['output_location'] else [],
            'unchecked_attributes': ['complex_header_ownership'], 'whole_object_certified': False})
    counts = Counter(r['assessment_status'] for r in records)
    report = {'version': VERSION, 'source_document_hash': pdf_hash, 'markdown_sha256': digest(markdown),
        'offset_unit': 'unicode_codepoint', 'records': records, 'source_objects': sources,
        'output_formula_objects': output, 'assessment_states': dict(counts),
        'summary': {'checked': counts['SOURCE_MATCH']+counts['SOURCE_MISMATCH'],
                    'matched': counts['SOURCE_MATCH'], 'mismatched': counts['SOURCE_MISMATCH'],
                    'repaired': 0, 'rejected': 0,
                    'unresolved': counts['ALIGNMENT_AMBIGUOUS']+counts['SOURCE_INSUFFICIENT'],
                    'unchecked': counts['NOT_CHECKED'], 'denominator': len(records)},
        'timing': {'source_alignment_seconds': source_seconds, 'quantity_seconds': quantity_seconds,
                   'total_seconds': time.monotonic()-started}, 'semantic_fidelity_certified': False}
    return bind_output_ids(report, mapping) if mapping else report


def move_quantity_locations(checks, log):
    """Reuse source quantities; no numerical object may silently survive an edited span."""
    result = copy.deepcopy(checks)

    def move(position):
        delta = 0
        for edit in log['operations']:
            a, b = edit['original_span']
            if a < position < b:
                return None
            if position >= b:
                delta += len(edit['after'])-len(edit['before'])
        return position+delta

    for row in result['records']:
        location = row['output_location']
        if location is None:
            continue
        intersects = any(location['start'] < edit['original_span'][1] and
                         edit['original_span'][0] < location['end'] for edit in log['operations'])
        if intersects:
            row.update(output_location=None, state='QUANTITY_CONTEXT_UNRESOLVED',
                       assessment_status='NOT_CHECKED', actual_candidates=[], alignment_basis=None,
                       fidelity_note='Quantity lies inside a repaired formula; consult its independent full-object verification')
            continue
        location['start'], location['end'] = move(location['start']), move(location['end'])
        location['object_span'] = [move(p) for p in location['object_span']]
        if None in location['object_span']:
            row['output_location'] = None
            row.update(state='QUANTITY_CONTEXT_UNRESOLVED', assessment_status='NOT_CHECKED',
                       actual_candidates=[], alignment_basis=None)
    result['states'] = dict(Counter(r['state'] for r in result['records']))
    result['assessment_states'] = dict(Counter(r['assessment_status'] for r in result['records']))
    result['reliably_paired'] = sum(r['output_location'] is not None for r in result['records'])
    return result
