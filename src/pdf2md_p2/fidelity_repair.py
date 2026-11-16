"""Generate bounded candidates, validate independently, and preserve exact undo."""

from .common import digest
from .fidelity_verify import verify_candidate


def candidates(markdown, assessment):
    if digest(markdown) != assessment['markdown_sha256']:
        raise ValueError('Assessment is bound to different Markdown')
    source = {r['id']: r for r in assessment['source_objects']}
    result = []
    for row in assessment['records']:
        difference = row['specific_difference']
        if (row['assessment_status'] != 'SOURCE_MISMATCH' or row['issue_type'] != 'FORMULA_SOURCE_COMPARISON'
            or not isinstance(difference, dict) or 'SUBTRACTION_OPERATOR_ABSENT' not in difference.get('differences', [])):
            continue
        evidence = source[row['source_object_id']]
        if evidence['source_extraction_status'] != 'SUPPORTED_NATIVE_GEOMETRY' or evidence.get('fraction_bar'):
            continue
        if any(evidence.get('annotations', {}).values()) or any(
                difference.get('annotations', {}).get('actual', {}).values()):
            # The independent repair verifier does not yet certify annotations.
            continue
        if row.get('output_representation') == 'PLAIN' and any(
                marker in evidence['source_expression'] for marker in ('_{', '^{', '\\frac')):
            continue
        a, b = row['output_location']['start'], row['output_location']['end']
        before = markdown[a:b]
        after = evidence['source_expression']
        if before.startswith('\n'):
            after = '\n' + after
        if before.endswith('\n'):
            after += '\n'
        result.append({'candidate_id': 'candidate-' + digest(row['issue_id']+before+after)[:20],
            'issue_id': row['issue_id'], 'source_version': assessment['version'],
            'target_output_version': assessment['markdown_sha256'], 'target_object': row['output_object_id'],
            'target_markdown_sha256': digest(markdown), 'source_document_hash': assessment['source_document_hash'],
            'expected_old_hash': digest(before), 'before_text': before, 'after_text': after,
            'source_evidence': evidence, 'difference_type': 'SUBTRACTION_OPERATOR_ABSENT',
            'candidate_generator': 'native-linear-formula-1', 'allowed_change_span': [a, b]})
    return result


def apply(pdf, markdown, planned):
    ordered = sorted(planned, key=lambda c: c['allowed_change_span'])
    decisions = [verify_candidate(pdf, markdown, c) for c in ordered]
    if any(a['allowed_change_span'][1] > b['allowed_change_span'][0] for a, b in zip(ordered, ordered[1:])):
        raise ValueError('Overlapping fidelity candidates')
    accepted = [(c, d) for c, d in zip(ordered, decisions) if d['decision'] == 'ACCEPTED']
    result = markdown
    for candidate, _ in reversed(accepted):
        a, b = candidate['allowed_change_span']
        result = result[:a] + candidate['after_text'] + result[b:]
    delta, operations = 0, []
    for candidate, decision in accepted:
        a, b = candidate['allowed_change_span']
        operations.append({'candidate_id': candidate['candidate_id'], 'issue_id': candidate['issue_id'],
            'original_span': [a, b], 'output_start': a+delta,
            'before': candidate['before_text'], 'after': candidate['after_text'],
            'independent_verification': decision})
        delta += len(candidate['after_text'])-len(candidate['before_text'])
    log = {'version': 'fidelity-repair-1', 'before_sha256': digest(markdown), 'after_sha256': digest(result),
           'operations': operations, 'decisions': decisions}
    if rollback(result, log) != markdown:
        raise ValueError('Fidelity repair is not exactly reversible')
    return result, log


def rollback(markdown, log):
    if digest(markdown) != log['after_sha256']:
        raise ValueError('Changed result cannot be rolled back using this record')
    for row in reversed(log['operations']):
        start = row['output_start']
        if markdown[start:start+len(row['after'])] != row['after']:
            raise ValueError('Repair rollback locus differs')
        markdown = markdown[:start]+row['before']+markdown[start+len(row['after']):]
    if digest(markdown) != log['before_sha256']:
        raise ValueError('Original Markdown not reproduced')
    return markdown
