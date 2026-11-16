"""Stage, validate and publish a complete derived conversion without changing R0."""

from contextlib import contextmanager
import copy
import ctypes
from datetime import datetime, timezone
import difflib
import errno
import json
import os
from pathlib import Path
import shutil
import sys
import time
import uuid

from . import __version__
from .assets import check as check_assets, references
from .common import PRODUCT, digest, implementation, inventory, page_parts, read, save, sha, verify, within
from .fidelity import VERSION, assess, move_quantity_locations
from .fidelity_mapping import rebind
from .fidelity_repair import apply, candidates, rollback as rollback_text
from .identifiers import check as check_identifiers
from .objects import check as check_objects
from .quantities import check as check_quantities
from .source_map import build as build_mapping, validate as validate_mapping

SCHEMA = 'fidelity-result-1'
CHANGED = ('manual.md', 'configuration.json', 'quality_report.json', 'provenance.json',
           'run_status.json', 'source_map.json', 'identifier_checks_after.json',
           'object_checks.json', 'quantity_checks.json', 'fidelity_assessment.json',
           'fidelity_before_assessment.json', 'fidelity_candidates.json',
           'fidelity_repair_log.json', 'fidelity_binding.json', 'fidelity.diff')


def _paths(result, output, pdf=None):
    result, output = result.resolve(), output.absolute()
    if output.is_symlink() or output.resolve().is_relative_to(PRODUCT):
        raise ValueError('Fidelity output must be outside the product and not a symlink')
    output = output.resolve()
    if output == result or output.is_relative_to(result) or result.is_relative_to(output):
        raise ValueError('Fidelity output must be separate from its original result')
    if pdf and pdf.resolve().is_relative_to(output):
        raise ValueError('Fidelity output must not contain its source PDF')
    return result, output


def _commit(stage, output):
    # Prefer no-replace publication. DrvFs does not implement renameat2 flags;
    # its directory rename is still atomic and cooperating writers hold our lock.
    libc = ctypes.CDLL(None, use_errno=True)
    rename = getattr(libc, 'renameat2', None)
    if rename is not None:
        rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
        rename.restype = ctypes.c_int
        if not rename(-100, os.fsencode(stage), -100, os.fsencode(output), 1):
            return
        error = ctypes.get_errno()
        if error not in (errno.EINVAL, errno.ENOSYS, errno.EOPNOTSUPP):
            raise OSError(error, os.strerror(error), str(output))
    if output.exists():
        raise FileExistsError(str(output))
    os.rename(stage, output)


@contextmanager
def _staging(output, binding, resume):
    if sys.platform != 'linux':
        raise ValueError('Fidelity publication runs on Linux/WSL2')
    import fcntl
    output.parent.mkdir(parents=True, exist_ok=True)
    lock_path = output.parent / ('.'+output.name+'.fidelity.lock')
    state = output.parent / ('.'+output.name+'.fidelity-state.json')
    stage = output.parent / ('.'+output.name+'.fidelity-stage')
    if lock_path.is_symlink() or state.is_symlink() or stage.is_symlink():
        raise ValueError('Fidelity transaction paths must not be symlinks')
    with lock_path.open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise ValueError('Another fidelity transaction is writing this destination') from error
        if output.exists():
            if not resume:
                raise ValueError('Destination exists; use a new directory or explicit --resume')
            yield None, state
            return
        if stage.exists():
            if not resume or not state.is_file() or read(state)['binding'] != binding:
                raise ValueError('Unfinished staging exists; resume only with identical inputs and implementation')
            abandoned = output.parent / ('.'+output.name+'.failed-stage-'+uuid.uuid4().hex)
            stage.rename(abandoned)
            save(abandoned.with_name(abandoned.name+'.json'), read(state))
        stage.mkdir()
        save(state, {'schema': SCHEMA, 'binding': binding, 'status': 'RUNNING'})
        try:
            yield stage, state
        except BaseException as error:
            save(state, {'schema': SCHEMA, 'binding': binding, 'status': 'FAILED',
                         'error_type': type(error).__name__, 'error': str(error)})
            raise


def _copy_artifacts(result, stage, manifest):
    for relative, expected in manifest['artifacts'].items():
        source, target = within(result, relative), within(stage, relative)
        if not source.is_file() or source.is_symlink() or sha(source) != expected:
            raise ValueError('Original sealed file differs: '+relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        if sha(target) != expected:
            raise ValueError('Staged original file differs: '+relative)


def _summary(report, log):
    report['summary']['repaired'] = len(log['operations'])
    report['summary']['rejected'] = sum(d['decision'] == 'REJECTED' for d in log['decisions'])
    return report


def prepare(pdf, stage, parent_manifest, parent_seal_bytes, mode, binding, *, quantities=None, inline=False):
    """Build a complete R1 in an unpublished, writable directory."""
    started = time.monotonic()
    source_hash = sha(pdf)
    isolation = None
    if inline:
        from .fidelity_sandbox import probe
        isolation = probe()
    if mode not in ('check', 'repair') or parent_manifest['configuration']['input_sha256'] != source_hash:
        raise ValueError('Fidelity mode or original PDF binding differs')
    original = (stage / 'manual.md').read_text(encoding='utf-8')
    quality = read(stage / 'quality_report.json')
    expected_pages = list(range(1, quality['expected_pages']+1))
    if quality['completed_pages'] != expected_pages or list(page_parts(original)) != expected_pages:
        raise ValueError('Fidelity requires a complete physical page sequence')
    if quality['status'] not in ('SUCCESS', 'SUCCESS_WITH_WARNINGS', 'CONTENT_REVIEW_REQUIRED'):
        raise ValueError('Fidelity requires a successfully sealed original conversion')
    parent_hash = digest(parent_seal_bytes.decode('utf-8'))
    archive_relative = 'fidelity/parents/'+parent_hash
    archive = within(stage, archive_relative)
    archive.mkdir(parents=True, exist_ok=False)
    archived = []
    for filename in CHANGED:
        if filename in parent_manifest['artifacts']:
            source = stage / filename
            shutil.copy2(source, archive / filename)
            archived.append(filename)
    (archive / 'run_manifest.json').write_bytes(parent_seal_bytes)
    mapping_started = time.monotonic()
    mapping = validate_mapping(stage) if (stage / 'source_map.json').is_file() else build_mapping(
        pdf, stage, read(stage / 'provenance.json').get('chunks', []))
    if quantities is None and inline and (stage / 'quantity_checks.json').is_file():
        # This sidecar was produced and sealed by this exact implementation moments
        # earlier; do not rescan all quantities just to enter F1.
        from .quantities import VERSION as quantity_version
        existing = read(stage / 'quantity_checks.json')
        if existing.get('version') == quantity_version:
            quantities = existing
    quantity = quantities if quantities is not None else check_quantities(pdf, original)
    before = assess(pdf, original, mapping, quantities=quantity)
    assessed = time.monotonic()
    planned = candidates(original, before) if mode == 'repair' else []
    generated = time.monotonic()
    updated, log = apply(pdf, original, planned)
    independent = time.monotonic()
    if references(updated) != references(original):
        raise ValueError('Fidelity repair changed asset references or their order')
    (stage / 'manual.md').write_text(updated, encoding='utf-8')
    final_mapping = rebind(pdf, stage, mapping, log, planned) if log['operations'] else mapping
    if log['operations']:
        quantity = move_quantity_locations(quantity, log)
        after = assess(pdf, updated, final_mapping, quantities=quantity, source_catalog=before)
    else:
        after = copy.deepcopy(before)
    _summary(after, log)
    # Check complete objects after the edit, not only a replacement substring.
    final_by_source = {r['source_object_id']: r for r in after['records'] if r['output_location']}
    previous = {r['issue_id']: r for r in before['records']}
    for operation in log['operations']:
        original_issue = previous[operation['issue_id']]
        matched = final_by_source.get(original_issue['source_object_id'])
        if not matched or matched['assessment_status'] != 'SOURCE_MATCH':
            raise ValueError('Repaired complete object did not pass post-assessment')
        operation['final_output_object_id'] = matched['output_object_id']
        operation['final_output_location'] = matched['output_location']
    checked = time.monotonic()
    images = check_assets(updated, stage)
    objects = check_objects(pdf, updated)
    identifiers = check_identifiers(pdf, updated)
    save(stage / 'object_checks.json', objects)
    save(stage / 'quantity_checks.json', quantity)
    save(stage / 'identifier_checks_after.json', identifiers)
    quality.update(version=__version__, assets=images, source_mapping_states=final_mapping['states'],
        object_check_states=objects['states'], quantity_check_states=quantity['states'],
        technical_identifiers_after=identifiers['counts'], fidelity=after['summary'],
        fidelity_assessment='fidelity_assessment.json', fidelity_pending=False, semantic_fidelity_certified=False)
    provenance = read(stage / 'provenance.json')
    provenance['fidelity'] = {'mode': mode, 'parent_seal_sha256': parent_hash,
        'before_markdown_sha256': digest(original), 'after_markdown_sha256': digest(updated),
        'assessment_version': VERSION, 'repair_log': 'fidelity_repair_log.json',
        'native_inference_repeated_for_fidelity': False}
    if inline:
        provenance['fidelity']['isolation'] = isolation
    if not inline:
        provenance['native_inference_performed_this_run'] = False
    save(stage / 'configuration.json', binding)
    save(stage / 'quality_report.json', quality)
    save(stage / 'provenance.json', provenance)
    save(stage / 'run_status.json', {'status': quality['status'], 'at': datetime.now(timezone.utc).isoformat()})
    save(stage / 'fidelity_before_assessment.json', before)
    save(stage / 'fidelity_assessment.json', after)
    save(stage / 'fidelity_candidates.json', planned)
    save(stage / 'fidelity_repair_log.json', log)
    (stage / 'fidelity.diff').write_text(''.join(difflib.unified_diff(
        original.splitlines(keepends=True), updated.splitlines(keepends=True),
        fromfile='R0/manual.md', tofile='R1/manual.md')), encoding='utf-8')
    if sha(pdf) != source_hash:
        raise ValueError('Source PDF changed during fidelity processing')
    timing = {'source_map_and_fast_check_seconds': assessed-mapping_started,
        'candidate_seconds': generated-assessed, 'independent_verification_seconds': independent-generated,
        'post_check_seconds': checked-independent, 'final_check_and_metadata_seconds': time.monotonic()-checked,
        'preparation_seconds': time.monotonic()-started}
    save(stage / 'fidelity_binding.json', {'schema': SCHEMA, 'mode': mode,
        'source_document_hash': source_hash, 'parent_seal_sha256': parent_hash,
        'parent_snapshot': archive_relative, 'archived_files': archived,
        'producer_implementation': implementation(), 'configuration': binding,
        'timing': timing})
    save(stage / 'run_manifest.json', {'configuration': binding, 'status': quality['status'], 'artifacts': inventory(stage)})
    verify(stage)
    validate_result(stage)
    return {'fidelity': after['summary'], 'timing': timing}


def validate_result(output):
    if not (output / 'fidelity_binding.json').is_file():
        if (output / 'fidelity_assessment.json').exists():
            raise ValueError('Fidelity assessment missing its result binding')
        return None
    binding = read(output / 'fidelity_binding.json')
    if binding['schema'] != SCHEMA or binding['mode'] not in ('check', 'repair'):
        raise ValueError('Unsupported fidelity result')
    report, log = read(output / 'fidelity_assessment.json'), read(output / 'fidelity_repair_log.json')
    text = (output / 'manual.md').read_text(encoding='utf-8')
    mapping = validate_mapping(output)
    if (report['markdown_sha256'] != digest(text) or report['source_document_hash'] != binding['source_document_hash']
        or report['source_document_hash'] != mapping['input_sha256']):
        raise ValueError('Fidelity final Markdown/PDF bindings differ')
    original = rollback_text(text, log)
    archive = within(output, binding['parent_snapshot'])
    if sha(archive / 'run_manifest.json') != binding['parent_seal_sha256']:
        raise ValueError('Fidelity parent seal differs')
    parent = read(archive / 'run_manifest.json')
    if digest(original) != parent['artifacts']['manual.md'] or sha(archive / 'manual.md') != digest(original):
        raise ValueError('Fidelity original Markdown cannot be reproduced')
    for filename in binding['archived_files']:
        if filename not in CHANGED or sha(within(archive, filename)) != parent['artifacts'].get(filename):
            raise ValueError('Fidelity original metadata snapshot differs')
    by_id = {r['id']: r for r in mapping['objects']}
    for row in report['records']:
        location = row['output_location']
        if location is not None:
            a, b = location['start'], location['end']
            if not (0 <= a < b <= len(text)) or text[a:b] != row['actual_output_expression']:
                raise ValueError('Fidelity output excerpt or offset differs')
            owner = by_id.get(row['output_object_id'])
            if owner is None and row['output_object_id'] is None:
                owners = [obj['id'] for obj in by_id.values() if obj['page'] == row['source_page']
                          and obj['start'] <= a and b <= obj['end']]
                if (len(owners) != 1 and owners == row['evidence_refs'].get('source_map_candidates')
                    and row['alignment_status'] == 'OUTPUT_SOURCE_MAP_UNRESOLVED'
                    and row['assessment_status'] in ('ALIGNMENT_AMBIGUOUS', 'NOT_CHECKED')):
                    continue
            if owner is None or not (owner['page'] == row['source_page'] and owner['start'] <= a and b <= owner['end']):
                raise ValueError('Fidelity record does not point to its final source-map object')
    from collections import Counter
    counts = Counter(r['assessment_status'] for r in report['records'])
    expected = {'checked': counts['SOURCE_MATCH']+counts['SOURCE_MISMATCH'], 'matched': counts['SOURCE_MATCH'],
        'mismatched': counts['SOURCE_MISMATCH'], 'repaired': len(log['operations']),
        'rejected': sum(d['decision'] == 'REJECTED' for d in log['decisions']),
        'unresolved': counts['ALIGNMENT_AMBIGUOUS']+counts['SOURCE_INSUFFICIENT'],
        'unchecked': counts['NOT_CHECKED'], 'denominator': len(report['records'])}
    if (expected != report['summary'] or read(output / 'quality_report.json')['fidelity'] != expected
        or dict(counts) != report['assessment_states']):
        raise ValueError('Fidelity summary differs from object-level evidence')
    if read(output / 'configuration.json') != binding['configuration']:
        raise ValueError('Fidelity configuration differs')
    return expected


def publish(pdf, result, output, mode='check', resume=False, *, inline=False):
    result, output = _paths(result, output, pdf)
    verify(result)
    parent_seal = (result / 'run_manifest.json').read_bytes()
    parent = read(result / 'run_manifest.json')
    parent_hash = digest(parent_seal.decode('utf-8'))
    source_hash = sha(pdf)
    if parent['configuration']['input_sha256'] != source_hash:
        raise ValueError('Original result belongs to a different PDF')
    binding = {'version': __version__, 'input_sha256': source_hash, 'implementation': implementation(),
               'fidelity_mode': mode, 'parent_seal_sha256': parent_hash}
    if inline:
        binding = copy.deepcopy(parent['configuration'])
        if binding.get('fidelity_mode') != mode or binding.get('implementation') != implementation():
            raise ValueError('Inline fidelity differs from its raw conversion binding')
    with _staging(output, binding, resume) as (stage, state):
        if stage is None:
            verify(output)
            if read(output / 'configuration.json') != binding:
                raise ValueError('Resume source, mode or implementation differs')
            summary = validate_result(output)
            return {'status': 'REUSED_VERIFIED', 'output': str(output), 'fidelity': summary}
        started = time.monotonic()
        _copy_artifacts(result, stage, parent)
        copied = time.monotonic()
        prepared = prepare(pdf, stage, parent, parent_seal, mode, binding, inline=inline)
        if sha(result / 'run_manifest.json') != parent_hash:
            raise ValueError('Original result seal changed during derivation')
        _commit(stage, output)
        save(state, {'schema': SCHEMA, 'binding': binding, 'status': 'PUBLISHED',
            'output_seal_sha256': sha(output / 'run_manifest.json'),
            'copy_seconds': copied-started, 'prepare_publish_seconds': time.monotonic()-copied,
            'total_seconds': time.monotonic()-started, 'preparation': prepared['timing']})
        return {'status': 'PUBLISHED', 'output': str(output), 'fidelity': prepared['fidelity'], 'receipt': str(state)}


def restore(result, output, resume=False):
    result, output = _paths(result, output)
    verify(result)
    validate_result(result)
    metadata = read(result / 'fidelity_binding.json')
    archive = within(result, metadata['parent_snapshot'])
    parent = read(archive / 'run_manifest.json')
    binding = {'operation': 'fidelity-rollback', 'source_seal_sha256': sha(result / 'run_manifest.json'),
               'parent_seal_sha256': metadata['parent_seal_sha256']}
    with _staging(output, binding, resume) as (stage, state):
        if stage is None:
            verify(output)
            if sha(output / 'run_manifest.json') != binding['parent_seal_sha256']:
                raise ValueError('Existing rollback result differs')
            return {'status': 'REUSED_VERIFIED', 'output': str(output)}
        for relative, expected in parent['artifacts'].items():
            source = within(archive if relative in metadata['archived_files'] else result, relative)
            if sha(source) != expected:
                raise ValueError('Cannot restore original sealed bytes: '+relative)
            target = within(stage, relative)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
        shutil.copy2(archive / 'run_manifest.json', stage / 'run_manifest.json')
        verify(stage)
        _commit(stage, output)
        save(state, {'schema': SCHEMA, 'binding': binding, 'status': 'RESTORED'})
        return {'status': 'RESTORED', 'output': str(output), 'seal_sha256': sha(output / 'run_manifest.json')}
