"""Versioned organization of existing conversions, without invoking a parser worker."""
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shutil
import uuid

from .common import PRODUCT, digest, inventory, read, save, sha, verify, within

MODULE_VERSION = 'd1.2'
SCHEMA = 1


def _id(prefix):
    return prefix + '-' + uuid.uuid4().hex


def _now():
    return datetime.now(timezone.utc).isoformat()


def _text(path):
    return Path(path).read_bytes().decode('utf-8')


def _write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(value.encode('utf-8'))


def _disjoint(a, b):
    a, b = Path(a).resolve(), Path(b).resolve()
    return not (a == b or a.is_relative_to(b) or b.is_relative_to(a))


def _no_links(root):
    root = Path(root)
    if root.is_symlink() or any(p.is_symlink() for p in root.rglob('*')):
        raise ValueError('Symbolic links are not allowed in an organized workspace or import')


@contextmanager
def writer(workspace):
    """OS locks survive neither crashes nor process exit; stale PID files are not locks."""
    workspace = Path(workspace)
    if workspace.exists():
        _no_links(workspace)
    workspace.mkdir(parents=True, exist_ok=True)
    lock_path = workspace / '.workspace.lock'
    flags = os.O_CREAT | os.O_RDWR | getattr(os, 'O_NOFOLLOW', 0)
    if lock_path.is_symlink():
        raise ValueError('Workspace lock cannot be a symbolic link')
    descriptor = os.open(lock_path, flags, 0o600)
    with os.fdopen(descriptor, 'r+b') as stream:
        stream.seek(0, 2)
        if stream.tell() == 0:
            stream.write(b'0')
            stream.flush()
        stream.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise ValueError('Another workspace writer is active') from exc
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == 'nt':
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream, fcntl.LOCK_UN)


def _checkpoint(phase):
    """Tests inject failure here; there is deliberately no public bypass/fault flag."""


def _next_revision(workspace):
    numbers = [int(p.name[4:]) for p in (workspace / 'revisions').glob('rev-*')
               if re.fullmatch(r'rev-\d{6}', p.name)]
    return f'rev-{max(numbers, default=0) + 1:06d}'


def _source_meta(mapping, input_hash):
    return {'schema_version': 'd1-1', 'input_sha256': input_hash,
            'pages': deepcopy((mapping or {}).get('pages', {})),
            'offset_unit': 'unicode_codepoint', 'page_numbering': 'physical_1_based',
            'semantic_correctness_certified': False}


def _module_files():
    return {p.name: sha(p) for p in Path(__file__).parent.glob('organization*.py')}


def _copy_assets(source, destination, paths):
    for relative in paths:
        unresolved = Path(source) / relative
        if any(p.is_symlink() for p in (unresolved, *unresolved.parents) if p.is_relative_to(Path(source))):
            raise ValueError('Shared/symbolic asset links are not allowed')
        old, new = within(source, relative), within(destination, relative)
        if not old.is_file():
            raise ValueError('Missing asset: ' + relative)
        new.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(old, new)
        if sha(old) != sha(new):
            raise ValueError('Asset copy changed bytes')


def _relationships(units, scopes, previous=()):
    links = []
    for scope in scopes:
        for unit in units:
            if unit['primary_scope_id'] == scope['scope_id']:
                links.append({'type': 'contains', 'source': scope['scope_id'], 'target': unit['unit_id'],
                              'status': 'STRUCTURAL_MEMBERSHIP'})
    for left, right in zip(units, units[1:]):
        links.append({'type': 'next_in_output', 'source': left['unit_id'], 'target': right['unit_id'],
                      'status': 'OUTPUT_ORDER_ONLY'})
    current = {u['unit_id']: u for u in units}
    for old in previous:
        if old['type'] not in ('context_dependency', 'derived_from_revision'):
            continue
        link = deepcopy(old)
        expected = link.get('depends_on_revision', {})
        changed = [key for key, version in expected.items()
                   if key not in current or current[key]['revision'] != version]
        if changed:
            link['status'] = 'STALE_NEEDS_REVIEW'
            link['changed_dependencies'] = changed
        links.append(link)
    return links


def _render(stage, revision, units, scopes, source_meta, asset_root, asset_paths,
            references=(), annotations=None, operation=None, reading=None, previous=None):
    from .organization_links import render_files, validate_links
    stage.mkdir(parents=True, exist_ok=False)
    # Keep original asset URLs valid by retaining their location relative to every content file.
    _copy_assets(asset_root, stage / 'content', asset_paths)
    if reading is not None:
        from .organization_packaging import prepare_revision
        reading = prepare_revision(units, scopes, reading)
    files, link_records, generated = render_files(units, scopes, references,
        layout=reading['packaging']['layout'] if reading is not None else None,
        display=reading['display'] if reading is not None else None)
    if previous is not None:
        prior = {u['unit_id']: u for u in previous['units']}
        def ancestry(items):
            by_id = {s['scope_id']: s for s in items}
            chains = {}
            for sid in by_id:
                chain, cursor = [], sid
                while cursor is not None:
                    chain.append(cursor)
                    cursor = by_id[cursor]['parent_id']
                chains[sid] = chain
            return chains
        old_chains, chains = ancestry(previous['scopes']), ancestry(scopes)
        for unit in units:
            old = prior[unit['unit_id']]
            changed = (old['content_ref']['file'] != unit['content_ref']['file'] or
                       old_chains[old['primary_scope_id']] != chains[unit['primary_scope_id']])
            if changed or 'structure_revision' in old or 'structure_revision' in unit:
                unit['structure_revision'] = old.get('structure_revision', 1) + changed
    for name, value in files.items():
        _write(within(stage, name), value)
    _checkpoint('content_written')
    for name, value in (annotations or {}).items():
        _write(within(stage, name), value)
    refs = _relationships(units, scopes, references) + link_records
    nav = ['# Organized Document', '', 'Generated navigation; technical content is under content/.', '']
    for scope in scopes:
        members = [u for u in units if u['primary_scope_id'] == scope['scope_id']]
        if members:
            first = members[0]
            title = scope['title'].replace('\\', '\\\\').replace('[', '\\[').replace(']', '\\]')
            nav.append(f"- [{title}]({first['content_ref']['file']}#{first['unit_id']}) "
                       f"({scope['type']}; {len(members)} units)")
    for ref in link_records:
        if ref.get('status') != 'RESOLVED' and ref.get('navigation_anchor'):
            nav += ['', f'<a id="{ref["navigation_anchor"]}"></a>',
                    f"Unresolved original reference: `{ref.get('original_target', '')}`. "
                    'No unique destination was selected.']
    if reading is not None:
        from .organization_packaging import _navigation
        nav = _navigation(units, scopes, reading['packaging'], link_records).rstrip('\n').split('\n')
    if annotations:
        nav += ['', '## Derived annotations', '', 'These notes are not source-document text.', '']
        for name in annotations:
            states = {r['status'] for r in refs if r.get('file') == name}
            nav.append(f'- [{Path(name).stem}]({name}) ({", ".join(sorted(states))})')
    _write(stage / 'README.md', '\n'.join(nav) + '\n')
    validate_links(stage, files, refs)
    source_meta = deepcopy(source_meta)
    source_meta.update(revision=revision, objects=[{
        'unit_id': u['unit_id'], 'revision': u['revision'], 'content_ref': u['content_ref'],
        'content_hash': u['content_hash'], 'source_refs': u['source_refs'], 'assessment': u['assessment'],
        'legacy_refs': u['legacy_refs']} for u in units])
    indexes = [{k: deepcopy(v) for k, v in u.items() if k != 'text'} for u in units]
    save(stage / 'index/units.json', {'schema_version': SCHEMA, 'revision': revision, 'units': indexes})
    save(stage / 'index/scopes.json', {'schema_version': SCHEMA, 'revision': revision, 'scopes': scopes})
    save(stage / 'index/references.json', {'schema_version': SCHEMA, 'revision': revision, 'references': refs})
    save(stage / 'index/search.json', {'revision': revision, 'entries': [
        {'unit_id': u['unit_id'], 'revision': u['revision'], 'content_hash': u['content_hash'],
         'text': u['text']} for u in units if u.get('searchable', True)]})
    save(stage / 'source_map.json', source_meta)
    save(stage / 'revision.json', {'schema_version': SCHEMA, 'module_version': MODULE_VERSION,
        'implementation': _module_files(),
        'revision': revision, 'operation': operation, 'created_at': _now(), 'generated': generated,
        **({'reading': reading} if reading is not None else {}),
        'asset_hashes': {p: sha(within(stage / 'content', p)) for p in asset_paths},
        'reconstructed_sha256': digest(''.join(u['text'] for u in units)),
        'semantic_correctness_certified': False})
    _checkpoint('indexes_written')
    save(stage / 'revision_manifest.json', {'schema_version': SCHEMA, 'revision': revision,
        'artifacts': inventory(stage)})
    _checkpoint('manifest_written')
    _read_revision(stage, revision)
    return refs


def _read_revision(root, expected):
    from .organization_links import restore, validate_links, render_files
    _no_links(root)
    verify(root, 'revision_manifest.json')
    manifest = read(root / 'revision_manifest.json')
    info = read(root / 'revision.json')
    if manifest['revision'] != expected or info['revision'] != expected:
        raise ValueError('Revision seal mismatch')
    parts = {name: read(root / f'index/{name}.json') for name in ('units', 'scopes', 'references', 'search')}
    if any(value['revision'] != expected for value in parts.values()):
        raise ValueError('Body/index revision mismatch')
    units, scopes = parts['units']['units'], parts['scopes']['scopes']
    texts, ids, occupied = {}, set(), {}
    scope_ids = {s['scope_id'] for s in scopes}
    for unit in units:
        uid = unit['unit_id']
        if uid in ids or not re.fullmatch(r'u-[a-f0-9]{32}', uid):
            raise ValueError('Invalid or duplicated stable Unit identity')
        ids.add(uid)
        if unit['primary_scope_id'] not in scope_ids or unit['revision'] < 1:
            raise ValueError('Unit lacks primary scope or content revision')
        ref = unit['content_ref']
        filename = ref['file']
        if filename not in texts:
            texts[filename] = _text(within(root, filename))
        text = texts[filename]
        a, b = ref['start'], ref['end']
        if not (0 <= a <= b <= len(text)) or ref['anchor'] != uid:
            raise ValueError('Invalid canonical content range')
        if any(a < d and c < b for c, d in occupied.setdefault(filename, [])):
            raise ValueError('Overlapping normative Unit ranges')
        occupied[filename].append((a, b))
        raw = text[a:b]
        if digest(raw) != unit['rendered_hash']:
            raise ValueError('Canonical content hash differs')
        unit['text'] = restore(raw, unit.get('transforms', []))
        if digest(unit['text']) != unit['content_hash']:
            raise ValueError('Reconstructed Unit content differs')
        if f'<a id="{uid}"></a>\n' not in text[:a]:
            raise ValueError('Stable anchor missing')
        if ref['line_start'] != text.count('\n', 0, a) + 1:
            raise ValueError('Canonical line binding differs')
    expected_entries = [{'unit_id': u['unit_id'], 'revision': u['revision'],
                         'content_hash': u['content_hash'], 'text': u['text']}
                        for u in units if u.get('searchable', True)]
    if parts['search']['entries'] != expected_entries:
        raise ValueError('Search index is not bound to canonical content')
    reconstructed = ''.join(u['text'] for u in units)
    if digest(reconstructed) != info['reconstructed_sha256']:
        raise ValueError('Reconstructed document differs')
    reading = info.get('reading')
    expected_units, expected_scopes = deepcopy(units), deepcopy(scopes)
    if reading is not None:
        from .organization_packaging import prepare_revision
        regenerated = prepare_revision(expected_units, expected_scopes, reading)
        if regenerated != reading:
            raise ValueError('Canonical reading plan is stale or inconsistent')
    layout = reading['packaging']['layout'] if reading is not None else None
    display = reading['display'] if reading is not None else None
    # Rendering reconstructs Scope membership too. Compare both derived results
    # from one isolated copy instead of cloning and refreshing the same tree twice.
    expected_files, _, _ = render_files(expected_units, expected_scopes, parts['references']['references'],
                                      layout=layout, display=display)
    if expected_scopes != scopes:
        raise ValueError('Scope membership is not bound to the canonical Units')
    if expected_files != texts:
        raise ValueError('Canonical files contain unassigned or inconsistent generated/body fragments')
    if expected_units != units:
        raise ValueError('Canonical Unit offsets, transforms or generated bindings differ')
    mapping = read(root / 'source_map.json')
    if mapping['revision'] != expected or mapping['semantic_correctness_certified'] is not False:
        raise ValueError('Organization source map version or semantics differ')
    expected_objects = [{key: u[key] for key in ('unit_id', 'revision', 'content_ref', 'content_hash',
                                                'source_refs', 'assessment', 'legacy_refs')} for u in units]
    if mapping['objects'] != expected_objects:
        raise ValueError('Source map and Unit index differ')
    for name, checksum in info['asset_hashes'].items():
        if sha(within(root / 'content', name)) != checksum:
            raise ValueError('Asset binding differs')
    validate_links(root, texts, parts['references']['references'])
    return {'revision': expected, 'root': root, 'units': units, 'scopes': scopes,
            'references': parts['references']['references'], 'source_map': mapping,
            'metadata': info, 'reconstructed': reconstructed}


def snapshot(workspace, revision=None):
    workspace = Path(workspace).resolve()
    meta = read(workspace / 'WORKSPACE.json')
    # Read HEAD exactly once. Every subsequent read addresses the same immutable directory.
    head = read(workspace / 'HEAD.json')
    chosen = revision or head['revision']
    if not re.fullmatch(r'rev-\d{6}', chosen):
        raise ValueError('Invalid revision name')
    if meta['schema_version'] != SCHEMA or head['workspace_id'] != meta['workspace_id']:
        raise ValueError('Workspace/HEAD binding differs')
    if revision and revision not in _lineage(workspace, head['revision']):
        raise ValueError('Requested revision was never published on the current history')
    result = _read_revision(workspace / 'revisions' / chosen, chosen)
    if chosen == head['revision'] and sha(result['root'] / 'revision_manifest.json') != head['manifest_sha256']:
        raise ValueError('HEAD seal differs')
    imported = read(workspace / 'imports/import-manifest.json')
    if sha(workspace / 'imports/import-manifest.json') != meta['import_manifest_sha256']:
        raise ValueError('Import identity changed')
    if sha(workspace / 'imports/identities.json') != meta.get('identities_sha256'):
        raise ValueError('Initial Unit identity register changed')
    result.update(workspace=workspace, workspace_id=meta['workspace_id'], document_id=meta['document_id'],
                  input_root=Path(imported['from_result']), input_pdf=imported.get('input_pdf'), import_manifest=imported)
    return result


def _lineage(workspace, revision):
    seen = set()
    while revision:
        if revision in seen or not re.fullmatch(r'rev-\d{6}', revision):
            raise ValueError('Invalid revision history')
        seen.add(revision)
        info = read(workspace / 'revisions' / revision / 'revision.json')
        revision = (info.get('operation') or {}).get('parent_revision')
    return seen


def _publish(workspace, stage, revision, event):
    _checkpoint('validated_before_publish')
    destination = workspace / 'revisions' / revision
    if destination.exists():
        raise ValueError('Revision already exists')
    stage.replace(destination)
    _checkpoint('revision_published_before_head')
    meta = read(workspace / 'WORKSPACE.json')
    save(workspace / 'HEAD.json', {'workspace_id': meta['workspace_id'], 'revision': revision,
         'manifest_sha256': sha(destination / 'revision_manifest.json'), 'event': event, 'published_at': _now()})


def _failure(workspace, event, error, stage=None):
    save(workspace / 'history' / (_id('failed') + '.json'), {
        'status': 'FAILED', 'at': _now(), 'event': event, 'error': str(error),
        'retained_stage': str(stage) if stage else None,
        'head': read(workspace / 'HEAD.json') if (workspace / 'HEAD.json').exists() else None})


def organize(result, workspace, pdf=None, soft_chars=12000):
    from .assets import check, local_path
    from .organization_links import linked_assets
    from .organization_structure import partition, organize_scopes
    result, workspace = Path(result).resolve(), Path(workspace).resolve()
    pdf = Path(pdf).resolve() if pdf else None
    if not _disjoint(result, workspace) or not _disjoint(PRODUCT, workspace):
        raise ValueError('Organization workspace must be separate from the source result and product')
    if pdf and (pdf == workspace or pdf.is_relative_to(workspace)):
        raise ValueError('Workspace must not contain original PDF')
    if soft_chars < 1:
        raise ValueError('Soft size target must be positive')
    verify(result)
    sealed = read(result / 'run_manifest.json')
    quality = read(result / 'quality_report.json')
    if (not str(quality.get('status', '')).startswith('SUCCESS') or quality.get('expected_pages', 0) < 1
            or quality.get('completed_pages') != list(range(1, quality.get('expected_pages', 0) + 1))):
        raise ValueError('Import requires an existing complete conversion result')
    text = _text(result / 'manual.md')
    asset_records = check(text, result)
    asset_paths = list(dict.fromkeys(local_path(result, url).relative_to(result).as_posix()
        for url in [r['path'] for r in asset_records] + linked_assets(text)))
    for relative in asset_paths:
        if not within(result, relative).is_file():
            raise ValueError('Missing linked asset: ' + relative)
    mapping = None
    if (result / 'source_map.json').is_file():
        from .source_map import validate
        mapping = deepcopy(validate(result))
    input_hash = (mapping or {}).get('input_sha256') or sealed.get('configuration', {}).get('input_sha256')
    if pdf and sha(pdf) != input_hash:
        raise ValueError('Original PDF SHA256 does not match the converted result')
    binding = {'result_manifest_sha256': sha(result / 'run_manifest.json'), 'manual_sha256': sha(result / 'manual.md'),
               'input_sha256': input_hash, 'soft_chars': soft_chars, 'module_version': MODULE_VERSION}
    if digest(text) != binding['manual_sha256']:
        raise ValueError('Input Markdown changed during read')
    with writer(workspace):
        if (workspace / 'WORKSPACE.json').exists():
            imported = read(workspace / 'imports/import-manifest.json')
            if imported['binding'] != binding:
                raise ValueError('Workspace is already bound to a different import or organization configuration')
            if (workspace / 'HEAD.json').exists():
                snap = snapshot(workspace)
                return {'status': 'REUSED', 'workspace': str(workspace), 'revision': snap['revision'],
                        'units': len(snap['units']), 'identities_reused': True}
            # Reuse a completely validated initial stage after an interrupted import.
            for path in sorted((workspace / 'revisions').glob('rev-*')):
                try:
                    candidate = _read_revision(path, path.name)
                except (OSError, ValueError, KeyError):
                    continue
                if candidate['metadata']['operation']['type'] == 'import':
                    save(workspace / 'HEAD.json', {'workspace_id': read(workspace / 'WORKSPACE.json')['workspace_id'],
                        'revision': path.name, 'manifest_sha256': sha(path / 'revision_manifest.json'),
                        'event': {'type': 'import_recovery'}, 'published_at': _now()})
                    return {'status': 'RECOVERED', 'revision': path.name, 'workspace': str(workspace)}
        else:
            existing = [p for p in workspace.iterdir() if p.name != '.workspace.lock']
            if existing:
                raise ValueError('New organization workspace must be empty')
            imported = {'schema_version': SCHEMA, 'binding': binding, 'from_result': str(result),
                'input_pdf': str(pdf) if pdf else None, 'artifacts': sealed['artifacts'], 'created_at': _now(),
                'source_map_present': mapping is not None, 'manual_encoding': 'utf-8',
                'asset_placement': 'content/assets; original relative asset URLs remain unchanged'}
            save(workspace / 'imports/import-manifest.json', imported)
            save(workspace / 'WORKSPACE.json', {'schema_version': SCHEMA, 'module_version': MODULE_VERSION,
                'workspace_id': _id('workspace'), 'document_id': _id('document'),
                'import_manifest_sha256': sha(workspace / 'imports/import-manifest.json')})
        if mapping and '\r\n' in text:
            # Legacy mapping offsets count normalized newlines; D1 preserves the actual bytes.
            offsets, pos = [], 0
            while pos < len(text):
                offsets.append(pos)
                pos += 2 if text[pos:pos+2] == '\r\n' else 1
            offsets.append(len(text))
            for row in mapping['objects']:
                row['normalized_legacy_range'] = [row['start'], row['end']]
                row['start'], row['end'] = offsets[row['start']], offsets[row['end']]
                row['text'] = text[row['start']:row['end']]
        identity_file = workspace / 'imports/identities.json'
        if identity_file.exists():
            if sha(identity_file) != read(workspace / 'WORKSPACE.json').get('identities_sha256'):
                raise ValueError('Initial Unit identity register changed')
            identities = read(identity_file)
            if identities['binding'] != binding:
                raise ValueError('Initial Unit identity binding differs')
            units, scopes = identities['units'], identities['scopes']
            for unit in units:
                unit['text'] = text[unit['start']:unit['end']]
                if digest(unit['text']) != unit['content_hash']:
                    raise ValueError('Initial Unit content binding differs')
        else:
            units = partition(text, mapping)
            scopes = organize_scopes(units, soft_chars=soft_chars)
            for unit in units:
                unit['content_hash'] = digest(unit['text'])
            save(identity_file, {'binding': binding, 'scopes': scopes,
                'units': [{k: v for k, v in unit.items() if k != 'text'} for unit in units]})
            workspace_meta = read(workspace / 'WORKSPACE.json')
            workspace_meta['identities_sha256'] = sha(identity_file)
            save(workspace / 'WORKSPACE.json', workspace_meta)
        if ''.join(u['text'] for u in units) != text:
            raise ValueError('Structural import did not preserve the complete original text')
        revision = _next_revision(workspace)
        stage = workspace / 'revisions' / ('.staging-' + uuid.uuid4().hex)
        event = {'type': 'import', 'input_binding': binding, 'content_preservation': 'EXACT_UTF8_RECONSTRUCTION'}
        try:
            _render(stage, revision, units, scopes, _source_meta(mapping, input_hash), result,
                    asset_paths, operation=event)
            verify(result)
            if sha(result / 'run_manifest.json') != binding['result_manifest_sha256'] or (pdf and sha(pdf) != input_hash):
                raise ValueError('Historical input changed during organization')
            _publish(workspace, stage, revision, event)
        except Exception as exc:
            _failure(workspace, event, exc, stage)
            raise
        return {'status': 'ORGANIZED', 'workspace': str(workspace), 'revision': revision,
                'units': len(units), 'scopes': len(scopes),
                'files': len({u['content_ref']['file'] for u in units}),
                'content_preservation': True, 'input_sha256': input_hash}


def verify_workspace(workspace, revision=None):
    snap = snapshot(workspace, revision)
    return {'status': 'VERIFIED', 'revision': snap['revision'], 'units': len(snap['units']),
            'scopes': len(snap['scopes']), 'canonical_files': len({u['content_ref']['file'] for u in snap['units']}),
            'reconstructed_sha256': snap['metadata']['reconstructed_sha256'],
            'original_content_preserved': snap['metadata']['reconstructed_sha256'] == snap['import_manifest']['binding']['manual_sha256'],
            'assets': len(snap['metadata']['asset_hashes']), 'semantic_correctness_certified': False}


def propose(workspace, operations, proposer, reason, judgment_method='SOURCE_REVIEW_NOT_HUMAN_VERIFICATION'):
    workspace = Path(workspace).resolve()
    with writer(workspace):
        snap = snapshot(workspace)
        by_id = {u['unit_id']: u for u in snap['units']}
        prepared = []
        for operation in operations:
            op = deepcopy(operation)
            if op.get('unit_id') not in by_id:
                raise ValueError('Unknown Unit')
            unit = by_id[op['unit_id']]
            op.update(expected_content_hash=unit['content_hash'], before=unit['text'])
            prepared.append(op)
        proposal = {'schema_version': SCHEMA, 'proposal_id': _id('proposal'),
            'workspace_id': snap['workspace_id'], 'document_id': snap['document_id'],
            'expected_revision': snap['revision'], 'operations': prepared, 'proposer': proposer,
            'reason': reason, 'judgment_method': judgment_method, 'created_at': _now(),
            'human_verified': False}
        _check(snap, proposal)
        path = workspace / 'proposals' / (proposal['proposal_id'] + '.json')
        save(path, proposal)
        return {'status': 'PROPOSED', 'proposal': str(path), 'proposal_id': proposal['proposal_id'],
                'expected_revision': snap['revision'], 'published': False}


def _check(snap, proposal):
    from .organization_structure import partition, revised_heading
    if (proposal.get('schema_version') != SCHEMA or proposal.get('workspace_id') != snap['workspace_id']
            or proposal.get('document_id') != snap['document_id']):
        raise ValueError('Proposal targets a different workspace/document')
    if proposal.get('expected_revision') != snap['revision']:
        raise ValueError('Stale proposal: expected_revision differs')
    if not proposal.get('proposer') or not proposal.get('reason') or not proposal.get('judgment_method'):
        raise ValueError('Proposal requires proposer, reason and judgment method')
    if proposal.get('human_verified') is not False:
        raise ValueError('Explicit submission is not human verification')
    units = {u['unit_id']: u for u in snap['units']}
    seen, checks = set(), []
    if not proposal.get('operations'):
        raise ValueError('Empty proposal')
    for op in proposal['operations']:
        uid = op.get('unit_id')
        if uid not in units or uid in seen:
            raise ValueError('Unknown or duplicate proposal Unit')
        seen.add(uid)
        unit = units[uid]
        if op.get('expected_content_hash') != unit['content_hash'] or op.get('before') != unit['text']:
            raise ValueError('Stale expected_content_hash or before content')
        kind = op.get('operation')
        if kind not in ('replace', 'move', 'annotate'):
            raise ValueError('Unsupported operation; split/merge need new identities and replacement relations')
        evidence = op.get('source_evidence', [])
        for ref in evidence:
            if ref.get('input_sha256') != snap['source_map']['input_sha256']:
                raise ValueError('Source evidence PDF hash differs')
            if not any(ref.get('page') == candidate.get('page') and ref.get('bbox') == candidate.get('bbox')
                       for candidate in unit['source_refs']):
                raise ValueError('Source evidence is not one of the retained source regions')
        if kind in ('replace', 'annotate'):
            if not isinstance(op.get('after'), str) or not op['after'].strip() or '\x00' in op['after']:
                raise ValueError('Replacement/annotation must contain text')
        if kind == 'replace':
            meaningful = [u for u in partition(op['after']) if u.get('searchable', True)]
            if unit.get('structure_basis') == 'CONFIRMED_ATOMIC_GROUP':
                before_parts = [u for u in partition(unit['text']) if u.get('searchable', True)]
                shape = lambda parts: [(u['kind'], u.get('heading', {}).get('level')) for u in parts]
                preserved = shape(meaningful) == shape(before_parts)
            else:
                preserved = len(meaningful) == 1 and meaningful[0]['kind'] == unit['kind']
            if not preserved:
                raise ValueError('Structural split/merge/kind change is not a content revision')
            if op['after'] == unit['text']:
                raise ValueError('No content change')
            revised_heading(unit, op['after'])
        if kind == 'annotate':
            from .assets import references
            if references(op['after']) or re.search(r'\]\(|<\s*(?:a|img)\b', op['after'], re.I):
                raise ValueError('Annotations currently support explanatory text, not new resource links')
        if kind == 'move' and op.get('target_scope_id') not in {s['scope_id'] for s in snap['scopes']}:
            raise ValueError('Unknown target Scope')
        if kind == 'move' and op['target_scope_id'] == unit['primary_scope_id']:
            raise ValueError('Unit already belongs to the target Scope')
        checks.append({'unit_id': uid, 'operation': kind, 'mechanical': 'PASSED',
            'source_evidence': 'BOUND_REGION_NOT_SEMANTIC_CERTIFICATION' if evidence else 'UNRESOLVED_NO_EVIDENCE',
            'human_verified': False})
    replacements = {op['unit_id']: op['after'] for op in proposal['operations'] if op['operation'] == 'replace'}
    if replacements:
        from .assets import check as check_assets, local_path
        from .organization_links import linked_assets
        # Resolve reference-style resources in their full document context, before publication.
        text = ''.join(replacements.get(u['unit_id'], u['text']) for u in snap['units'])
        asset_root = snap['root'] / 'content'
        urls = [item['path'] for item in check_assets(text, asset_root)] + linked_assets(text)
        for url in urls:
            path = local_path(asset_root, url)
            relative = path.relative_to(asset_root).as_posix()
            if relative not in snap['metadata']['asset_hashes'] or not path.is_file():
                raise ValueError('Replacement references an asset absent from the current snapshot: ' + url)
    return {'status': 'CHECKED', 'revision': snap['revision'], 'checks': checks, 'published': False}


def check_proposal(workspace, proposal):
    return _check(snapshot(workspace), read(proposal) if not isinstance(proposal, dict) else proposal)


def _annotations(snap):
    folder = snap['root'] / 'annotations'
    return {p.relative_to(snap['root']).as_posix(): _text(p) for p in folder.rglob('*.md')} if folder.exists() else {}


def apply_proposal(workspace, proposal, expected_revision):
    workspace = Path(workspace).resolve()
    with writer(workspace):
        snap = snapshot(workspace)
        data = read(proposal) if not isinstance(proposal, dict) else deepcopy(proposal)
        if expected_revision != snap['revision']:
            raise ValueError('Explicit expected_revision differs from current HEAD')
        checked = _check(snap, data)
        units, refs, annotations = deepcopy(snap['units']), deepcopy(snap['references']), _annotations(snap)
        by_id = {u['unit_id']: u for u in units}
        revision = _next_revision(workspace)
        for op in data['operations']:
            unit = by_id[op['unit_id']]
            if op['operation'] == 'replace':
                unit['text'] = op['after']
                unit['content_hash'] = digest(unit['text'])
                unit['assessment']['content'] = 'UNVERIFIED_LOCAL_REVISION'
                unit['assessment']['source_text_match_current'] = 'NOT_RECHECKED'
                unit['revision'] += 1
                unit['revision_evidence'] = op.get('source_evidence', [])
                if unit.get('heading'):
                    from .organization_structure import revised_heading
                    unit['heading'] = revised_heading(unit, unit['text'])
            elif op['operation'] == 'move':
                unit['primary_scope_id'] = op['target_scope_id']
                unit['content_file'] = 'content/' + op['target_scope_id'] + '.md'
                unit['structure_revision'] = unit.get('structure_revision', 1) + 1
            else:
                note_id = _id('annotation')
                name = 'annotations/' + note_id + '.md'
                annotations[name] = '# Derived Annotation\n\n' + op['after']
                refs.append({'type': 'derived_from_revision', 'source': note_id, 'target': unit['unit_id'],
                    'file': name, 'depends_on_revision': {unit['unit_id']: unit['revision']},
                    'status': 'DERIVED_NOT_SOURCE_TEXT', 'proposer': data['proposer']})
                refs.append({'type': 'context_dependency', 'source': note_id, 'target': unit['unit_id'],
                    'file': name, 'depends_on_revision': {unit['unit_id']: unit['revision']},
                    'status': 'DERIVED_NOT_SOURCE_TEXT', 'proposer': data['proposer']})
        event = {'type': 'local_revision', 'parent_revision': snap['revision'], 'proposal': data,
                 'checks': checked, 'human_verified': False}
        stage = workspace / 'revisions' / ('.staging-' + uuid.uuid4().hex)
        try:
            _render(stage, revision, units, deepcopy(snap['scopes']), snap['source_map'], snap['root'] / 'content',
                    list(snap['metadata']['asset_hashes']), refs, annotations, event,
                    reading=snap['metadata'].get('reading'), previous=snap)
            _publish(workspace, stage, revision, event)
        except Exception as exc:
            _failure(workspace, event, exc, stage)
            raise
        return {'status': 'APPLIED', 'revision': revision, 'parent_revision': snap['revision'],
                'changed_units': [op['unit_id'] for op in data['operations']], 'human_verified': False}


def rollback(workspace, target_revision, expected_revision, reason):
    workspace = Path(workspace).resolve()
    with writer(workspace):
        current = snapshot(workspace)
        if current['revision'] != expected_revision:
            raise ValueError('Rollback expected_revision differs')
        target = snapshot(workspace, target_revision)
        units = deepcopy(target['units'])
        prior = {u['unit_id']: u for u in current['units']}
        for unit in units:
            old = prior[unit['unit_id']]
            unit['revision'] = old['revision'] + (unit['content_hash'] != old['content_hash'])
            unit['structure_revision'] = old.get('structure_revision', 1) + (
                unit['primary_scope_id'] != old['primary_scope_id'])
        revision = _next_revision(workspace)
        event = {'type': 'rollback', 'parent_revision': current['revision'], 'target_revision': target_revision,
                 'reason': reason, 'history_preserved': True}
        stage = workspace / 'revisions' / ('.staging-' + uuid.uuid4().hex)
        try:
            _render(stage, revision, units, deepcopy(target['scopes']), target['source_map'], target['root'] / 'content',
                    list(target['metadata']['asset_hashes']), target['references'], _annotations(target), event,
                    reading=target['metadata'].get('reading'), previous=current)
            _publish(workspace, stage, revision, event)
        except Exception as exc:
            _failure(workspace, event, exc, stage)
            raise
        return {'status': 'ROLLED_BACK', 'revision': revision, 'restored_content_from': target_revision,
                'reconstructed_sha256': target['metadata']['reconstructed_sha256']}


def history(workspace):
    workspace = Path(workspace).resolve()
    current = snapshot(workspace)
    published = _lineage(workspace, current['revision'])
    events = []
    for path in sorted((workspace / 'revisions').glob('rev-*')):
        data = _read_revision(path, path.name)['metadata']
        events.append({'revision': path.name, 'current': path.name == current['revision'],
                       'publication_status': 'PUBLISHED' if path.name in published else 'UNPUBLISHED_RETAINED',
                       'created_at': data['created_at'], 'operation': data['operation']})
    failures = [read(p) for p in sorted((workspace / 'history').glob('failed-*.json'))]
    return {'current_revision': current['revision'], 'events': events, 'failures': failures}


def export(workspace, output, format='split', revision=None):
    snap, output = snapshot(workspace, revision), Path(output).resolve()
    if (not _disjoint(output, snap['workspace']) or not _disjoint(output, snap['input_root'])
            or not _disjoint(output, PRODUCT) or (snap.get('input_pdf') and Path(snap['input_pdf']).is_relative_to(output))):
        raise ValueError('Export must be outside input, workspace and product')
    if output.exists():
        raise ValueError('Export destination exists')
    if format not in ('split', 'single'):
        raise ValueError('Unsupported export format')
    output.mkdir(parents=True)
    if format == 'split':
        shutil.copytree(snap['root'] / 'content', output / 'content')
        shutil.copyfile(snap['root'] / 'README.md', output / 'README.md')
    else:
        if any(r['type'] == 'explicit_reference' for r in snap['references']):
            from .organization_links import render_single
            text, references, generated = render_single(snap['units'], snap['scopes'], snap['references'])
            _write(output / 'manual.md', text)
            nav = ['# Single-document export', '', '[Document](manual.md)', '']
            for ref in references:
                if ref.get('navigation_anchor'):
                    nav += [f'<a id="{ref["navigation_anchor"]}"></a>',
                            f"Unresolved original reference: `{ref['original_target']}`.", '']
            _write(output / 'README.md', '\n'.join(nav))
            save(output / 'export_transforms.json', generated)
        else:
            _write(output / 'manual.md', snap['reconstructed'])
        _copy_assets(snap['root'] / 'content', output, list(snap['metadata']['asset_hashes']))
    if (snap['root'] / 'annotations').is_dir():
        shutil.copytree(snap['root'] / 'annotations', output / 'annotations')
    save(output / 'export_manifest.json', {'revision': snap['revision'], 'format': format,
        'workspace_id': snap['workspace_id'], 'generated_from_canonical_content': True, 'artifacts': inventory(output)})
    return {'status': 'EXPORTED', 'revision': snap['revision'], 'format': format, 'output': str(output)}
