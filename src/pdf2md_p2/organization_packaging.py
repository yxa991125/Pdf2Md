"""Source-constrained structure metadata and reversible reading-file packaging."""
from collections import Counter, defaultdict
from copy import deepcopy
import json
from pathlib import Path
import re
from uuid import NAMESPACE_URL, uuid5

from .common import PRODUCT, digest, inventory, read, save, sha, verify, within
from .organization_links import _visible_inline, refresh_scopes, render_files, restore, validate_links

VERSION = 'd1.2-reading-1'
NUMBER = re.compile(r'^\d+(?:\.\d+)*\.?$')
NUMBERED = re.compile(r'^(\d+(?:\.\d+)*)\s+\S')
CAPTION = re.compile(r'^(?:Table|Figure)\s+\d+\b|^\(table continues', re.I)


def _norm(value):
    return ' '.join(value.split())


def _title(unit):
    return _norm(_visible_inline(unit.get('heading', {}).get('title', '')))


def _overlap(a, b):
    width = max(0, min(a[2], b[2]) - max(a[0], b[0]))
    height = max(0, min(a[3], b[3]) - max(a[1], b[1]))
    return width * height / max(1, min((a[2]-a[0])*(a[3]-a[1]), (b[2]-b[0])*(b[3]-b[1])))


def source_structure(snapshot, pdf=None):
    """PDF lines supply structural evidence only, never new Unit source mappings."""
    records, lines, bookmarks = [], defaultdict(list), []
    input_hash = snapshot['source_map'].get('input_sha256')
    if pdf is not None:
        import pymupdf
        pdf = Path(pdf)
        if not input_hash or sha(pdf) != input_hash:
            raise ValueError('Structure evidence PDF hash differs')
        with pymupdf.open(pdf) as document:
            bookmarks = document.get_toc()
            for number, page in enumerate(document, 1):
                rotation = page.rotation
                page.set_rotation(0)
                for block in page.get_text('dict')['blocks']:
                    for line in block.get('lines', []):
                        spans = line['spans']
                        text = _norm(''.join(s['text'] for s in spans))
                        if text:
                            lines[number].append({'page': number, 'text': text, 'bbox': list(line['bbox']),
                                'size': max(s['size'] for s in spans), 'font': spans[0]['font'],
                                'bold': all(s['flags'] & 16 for s in spans),
                                'baseline': spans[0]['origin'][1], 'height': page.rect.height,
                                'width': page.rect.width, 'rotation': rotation})
        if sha(pdf) != input_hash:
            raise ValueError('Structure PDF changed during read')
    titles = Counter(_title(u) for u in snapshot['units'] if u.get('heading'))
    for unit in snapshot['units']:
        if not unit.get('heading'):
            continue
        title = _title(unit)
        hits = [line for page in unit.get('pages', []) for line in lines[page] if line['text'] == title]
        retained_regions = [r for r in unit.get('source_refs', []) if r.get('bbox')]
        if retained_regions:
            hits = [line for line in hits if any(r.get('page') == line['page'] and
                    _overlap(r['bbox'], line['bbox']) > .5 for r in retained_regions)]
        evidence = hits if len(hits) == 1 else []
        state, reason = 'INFERRED_STRUCTURE', 'MARKDOWN_BOUNDARY_ONLY'
        level = None
        if not title:
            state, reason = 'STRUCTURE_FRAGMENT', 'EMPTY_OR_FORMAT_ONLY_TITLE'
        elif NUMBER.fullmatch(title):
            state, reason = 'STRUCTURE_FRAGMENT', 'NUMBER_WITHOUT_TITLE'
        elif len(hits) > 1:
            state, reason = 'STRUCTURE_UNRESOLVED', 'MULTIPLE_SOURCE_LINES'
        elif evidence:
            line = evidence[0]
            if titles[title] >= 3 and line['bbox'][1] < line['height'] * .12:
                state, reason = 'REPEATED_HEADER_CANDIDATE', 'REPEATED_TEXT_IN_SOURCE_TOP_BAND'
            elif CAPTION.match(title):
                state, reason = 'NON_SECTION_TITLE', 'SOURCE_TABLE_OR_FIGURE_LABEL'
            else:
                number = NUMBERED.match(title)
                outline = [b for b in bookmarks if b[2] == line['page'] and _norm(b[1]) == title]
                if line['bold'] and (number or len(outline) == 1):
                    state, reason = 'CONFIRMED_STRUCTURE', 'SOURCE_LINE_WITH_NUMBER_OR_OUTLINE'
                    level = len(number[1].split('.')) if number else outline[0][0]
        records.append({'unit_id': unit['unit_id'], 'scope_id': unit['primary_scope_id'],
            'content_hash': unit['content_hash'], 'title': title, 'state': state, 'reason': reason,
            'source_lines': evidence, 'source_candidate_count': len(hits),
            'source_level': level, 'original_level': unit['heading']['level'],
            'duplicate_count': titles[title]})
    by_id = {r['unit_id']: r for r in records}
    meaningful = [u for u in snapshot['units'] if u.get('searchable')]
    combinations, used = [], set()
    for left, right in zip(meaningful, meaningful[1:]):
        a, b = by_id.get(left['unit_id']), by_id.get(right['unit_id'])
        if not a or not b or not NUMBER.fullmatch(a['title']) or NUMBER.fullmatch(b['title']):
            continue
        if a['unit_id'] in used or b['unit_id'] in used or not a['source_lines'] or not b['source_lines']:
            continue
        first, last = a['source_lines'][0], b['source_lines'][0]
        # Adjacency is necessary but insufficient: require independently located,
        # same-baseline, same-font source fragments, outside the running header band.
        supported = (first['page'] == last['page'] and first['bold'] and last['bold']
            and first['font'] == last['font'] and abs(first['size'] - last['size']) < .25
            and abs(first['baseline'] - last['baseline']) < max(1, first['size'] * .15)
            and first['bbox'][2] < last['bbox'][0] and first['bbox'][0] < first['width'] * .2
            and first['bbox'][1] > first['height'] * .08 and not CAPTION.match(b['title'])
            and not re.search(r'[\[\]<>`]', left['heading']['title'] + right['heading']['title']))
        if not supported:
            continue
        level = len(a['title'].rstrip('.').split('.'))
        title = a['title'] + ' ' + b['title']
        combinations.append({'unit_ids': [a['unit_id'], b['unit_id']],
            'scope_ids': [a['scope_id'], b['scope_id']], 'title': title, 'level': level,
            'state': 'CONFIRMED_STRUCTURE', 'reason': 'SOURCE_ALIGNED_NUMBER_AND_TITLE',
            'source_lines': [first, last], 'input_sha256': input_hash,
            'content_hashes': {u['unit_id']: u['content_hash'] for u in (left, right)}})
        used.update((a['unit_id'], b['unit_id']))
    return {'version': VERSION, 'input_sha256': input_hash, 'pdf_checked': pdf is not None,
            'headings': records, 'combinations': combinations,
            'semantic_correctness_certified': False}


def refined_scopes(units, scopes, evidence):
    """Keep original IDs/query sets; new parents express confirmed compositions."""
    scopes = deepcopy(scopes)
    by_id = {s['scope_id']: s for s in scopes}
    root = next(s for s in scopes if s['parent_id'] is None)
    for record in evidence['headings']:
        scope = by_id[record['scope_id']]
        scope['structure_assessment'] = deepcopy(record)
        scope['prior_membership'] = list(scope['unit_ids'])
    for group in evidence['combinations']:
        if group.get('scope_change') == 'STALE_COMPOSITION_AFTER_LOCAL_REVISION':
            continue
        old = [by_id[s] for s in group['scope_ids']]
        uid = 'scope-' + uuid5(NAMESPACE_URL, '|'.join(group['scope_ids'])).hex
        if uid in by_id:
            combined = by_id[uid]
            if (combined.get('composed_from_scope_ids') != group['scope_ids'] or
                    any(s['parent_id'] != uid or s.get('composition_parent') != uid for s in old)):
                raise ValueError('Composite Scope identity has incompatible relationships')
            group['scope_change'] = 'NEW_COMPOSITE_PARENT_ORIGINAL_IDS_RETAINED'
            group['composite_scope_id'] = uid
            continue
        if (old[0]['parent_id'] != old[1]['parent_id'] or
                any(old[0]['scope_id'] == s.get('parent_id') for s in scopes)):
            # Do not reparent an existing subtree merely because a title matches.
            group['scope_change'] = 'UNRESOLVED_ANCESTRY'
            continue
        combined = {'scope_id': uid, 'type': 'EXPLICIT_STRUCTURE', 'title': group['title'],
            'display_title': group['title'], 'parent_id': old[0]['parent_id'] or root['scope_id'],
            'level': group['level'], 'boundary_basis': group['reason'], 'conflicts': [],
            'unit_ids': [], 'direct_unit_ids': [], 'pages': [], 'content_files': [], 'oversized_unit_ids': [],
            'structure_assessment': deepcopy(group), 'composed_from_scope_ids': group['scope_ids'],
            'generated_navigation': True}
        scopes.insert(scopes.index(old[0]), combined)
        by_id[uid] = combined
        for scope in old:
            scope['previous_parent_id'] = scope['parent_id']
            scope['parent_id'] = uid
            scope['composition_parent'] = uid
            scope['historical_query_policy'] = 'RETAIN_ORIGINAL_SCOPE_MEMBERSHIP'
        group['scope_change'] = 'NEW_COMPOSITE_PARENT_ORIGINAL_IDS_RETAINED'
        group['composite_scope_id'] = uid
    refresh_scopes(units, scopes)
    for scope in scopes:
        if 'prior_membership' in scope and scope['unit_ids'] != scope['prior_membership']:
            raise ValueError('Structure refinement changed an existing Scope query set')
    return scopes


def display_transforms(units, evidence):
    by_id = {u['unit_id']: u for u in units}
    views = {}
    for group in evidence['combinations']:
        if group.get('scope_change') != 'NEW_COMPOSITE_PARENT_ORIGINAL_IDS_RETAINED':
            continue
        if any(by_id[uid]['content_hash'] != checksum for uid, checksum in group['content_hashes'].items()):
            continue
        first, second = group['unit_ids']
        for uid, text in ((first, '#' * min(6, group['level']) + ' ' + group['title'] + '\n'), (second, '')):
            views[uid] = {'content_hash': by_id[uid]['content_hash'], 'after': text,
                          'basis': 'REVERSIBLE_SOURCE_CONFIRMED_TITLE_COMPOSITION'}
    return views


def package(units, scopes, evidence, target_chars=None):
    """Pack continuous whole objects, not Scope-sized files or fixed text slices."""
    if target_chars is not None and (type(target_chars) is not int or target_chars < 256):
        raise ValueError('Reading target must be an integer of at least 256 characters')
    objects = sorted(len(u['text']) for u in units if u.get('searchable') and u['kind'] != 'heading')
    p90 = objects[min(len(objects)-1, int(len(objects)*.9))] if objects else 1000
    target = target_chars or max(4000, min(14000, p90 * 4))
    records = {r['unit_id']: r for r in evidence['headings']}
    confirmed = {uid: r for uid, r in records.items() if r['state'] == 'CONFIRMED_STRUCTURE'}
    for group in evidence['combinations']:
        if group.get('composite_scope_id') and group.get('scope_change') == 'NEW_COMPOSITE_PARENT_ORIGINAL_IDS_RETAINED':
            confirmed[group['unit_ids'][0]] = {'source_level': group['level'], 'title': group['title']}
    cohesive = set()
    positions = {u['unit_id']: i for i, u in enumerate(units)}
    for group in evidence['combinations']:
        if group.get('composite_scope_id') and group.get('scope_change') == 'NEW_COMPOSITE_PARENT_ORIGINAL_IDS_RETAINED':
            start, end = (positions[uid] for uid in group['unit_ids'])
            cohesive.update(range(start+1, end+1))
    bundles, boundaries, current = [], [], []
    size, has_body, last_heading, previous = 0, False, False, None
    parents = {s['scope_id']: s.get('parent_id') for s in scopes}
    excluded = {'STRUCTURE_FRAGMENT', 'REPEATED_HEADER_CANDIDATE', 'NON_SECTION_TITLE'}
    for index, unit in enumerate(units):
        meaningful = unit.get('searchable')
        structure = confirmed.get(unit['unit_id'], {})
        strong = structure.get('source_level') == 1
        role = records.get(unit['unit_id'], {}).get('state')
        heading = bool(unit.get('heading')) and role not in excluded
        page_break = bool(previous and previous.get('pages') and unit.get('pages') and
                          max(previous['pages']) < min(unit['pages']))
        sibling = bool(previous and parents.get(previous['primary_scope_id']) ==
                       parents.get(unit['primary_scope_id']))
        # Prefer section starts. Inside an oversized section, page changes or
        # paragraph-to-paragraph transitions are neutral continuation boundaries.
        # Do not separate an introduction/caption from its following visual object.
        prose_break = bool(previous and previous['kind'] == unit['kind'] == 'paragraph' and
                           previous['text'].rstrip().endswith(('.', '!', '?', '\u3002')))
        reason = None
        if strong:
            reason = 'SOURCE_CONFIRMED_TOP_LEVEL'
        elif heading and size >= target:
            reason = 'RELATED_SCOPE_START_AFTER_SOFT_TARGET' if sibling else 'SCOPE_START_AFTER_SOFT_TARGET'
        elif size >= target * 1.5 and meaningful and not unit.get('heading'):
            if page_break and previous and previous['kind'] not in ('image', 'table', 'formula'):
                reason = 'PAGE_CONTINUATION_AT_COMPLETE_OBJECT'
            elif prose_break:
                reason = 'PARAGRAPH_CONTINUATION_AT_COMPLETE_OBJECT'
        if current and has_body and not last_heading and reason and index not in cohesive:
            bundles.append(current)
            boundaries.append({'before_unit_id': unit['unit_id'], 'reason': reason,
                               'previous_chars': size, 'same_parent_scope': sibling})
            current, size, has_body = [], 0, False
        current.append(unit)
        size += len(unit['text'])
        if meaningful:
            last_heading = bool(unit.get('heading'))
            has_body |= not last_heading
            previous = unit
    if current:
        bundles.append(current)
    layout, files = {}, []
    for index, members in enumerate(bundles, 1):
        filename = f'content/reading-{index:03d}.md'
        for unit in members:
            layout[unit['unit_id']] = filename
        files.append({'file': filename, 'unit_ids': [u['unit_id'] for u in members],
            'scope_ids': list(dict.fromkeys(u['primary_scope_id'] for u in members)),
            'chars': sum(len(u['text']) for u in members),
            'pages': sorted({p for u in members for p in u.get('pages', [])}),
            'oversized_complete_objects': [u['unit_id'] for u in members if len(u['text']) > target]})
    return {'version': VERSION, 'target_chars': target,
            'target_basis': 'EXPLICIT_SOFT_TARGET' if target_chars else '4_TIMES_P90_COMPLETE_OBJECT_CLAMP_4000_14000',
            'scope_boundary_policy': 'SOURCE_TOP_LEVEL_THEN_SCOPE_START_THEN_NATURAL_CONTINUATION',
            'boundaries': boundaries, 'layout': layout, 'files': files}


def diagnostics(units, scopes):
    grouped = defaultdict(list)
    for unit in units:
        grouped[unit['content_ref']['file']].append(unit)
    files = []
    for filename, members in grouped.items():
        kinds = Counter(u['kind'] for u in members)
        chars = sum(len(u['text']) for u in members)
        files.append({'file': filename, 'chars': chars, 'units': len(members),
            'scope_ids': list(dict.fromkeys(u['primary_scope_id'] for u in members)), 'kinds': dict(kinds),
            'only_heading_or_format': not any(u.get('searchable') and u['kind'] != 'heading' for u in members),
            'visual_short': chars < 100 and any(kinds[k] for k in ('image', 'table', 'formula'))})
    lengths = sorted(f['chars'] for f in files)
    def quantile(q):
        if not lengths:
            return 0
        x = (len(lengths)-1)*q
        lo, hi = int(x), min(int(x)+1, len(lengths)-1)
        return lengths[lo]+(lengths[hi]-lengths[lo])*(x-lo)
    return {'units': len(units), 'scopes': len(scopes), 'files': len(files),
        'scope_types': dict(Counter(s['type'] for s in scopes)),
        'under100': sum(f['chars'] < 100 for f in files),
        'heading_or_format_files': sum(f['only_heading_or_format'] for f in files),
        'visual_short_files': sum(f['visual_short'] for f in files),
        'multiple_scope_files': sum(len(f['scope_ids']) > 1 for f in files),
        'quantiles': {f'P{q}': quantile(q/100) for q in (10,50,90,99)}, 'file_mapping': files}


def _identity_digest(units):
    fields = ('unit_id', 'revision', 'content_hash', 'primary_scope_id', 'source_refs',
              'legacy_refs', 'assessment', 'pages', 'kind')
    return digest(json.dumps([{k: u[k] for k in fields} for u in units], sort_keys=True, ensure_ascii=False))


def _navigation(units, scopes, plan, references):
    rows = ['# Reading Document', '', 'Generated navigation; source text is preserved through reversible transforms.', '']
    by_id = {u['unit_id']: u for u in units}
    for file in plan['files']:
        pages = file['pages']
        title = f"Physical pages {pages[0]}-{pages[-1]}" if pages else 'Output range'
        rows.append(f"- [{title}]({file['file']}) ({len(file['scope_ids'])} direct Scopes)")
    rows += ['', '## Logical scopes', '']
    for scope in scopes:
        status = scope.get('structure_assessment', {}).get('state')
        if scope.get('composition_parent') or status in ('REPEATED_HEADER_CANDIDATE', 'NON_SECTION_TITLE'):
            continue
        if scope['parent_id'] is None or not scope['unit_ids']:
            continue
        first = by_id[scope['unit_ids'][0]]
        title = scope.get('display_title', scope['title']).replace('[', '\\[').replace(']', '\\]')
        rows.append(f"- [{title}]({first['content_ref']['file']}#{first['unit_id']}) ({scope['scope_id']})")
    for ref in references:
        if ref.get('navigation_anchor'):
            rows += ['', f'<a id="{ref["navigation_anchor"]}"></a>',
                     f"Unresolved original reference: `{ref.get('original_target', '')}`."]
    return '\n'.join(rows) + '\n'


def prepare_revision(units, scopes, recipe):
    """Recompute a revision's layout, invalidating stale structural evidence."""
    recipe = deepcopy(recipe)
    if recipe.get('version') != VERSION:
        raise ValueError('Unsupported canonical reading recipe')
    by_id = {u['unit_id']: u for u in units}
    by_scope = {s['scope_id']: s for s in scopes}
    evidence = recipe['structure']
    for record in evidence['headings']:
        unit = by_id.get(record['unit_id'])
        if unit is None or unit['content_hash'] != record['content_hash'] or unit['primary_scope_id'] != record['scope_id']:
            record.update(state='STRUCTURE_UNRESOLVED', reason='STALE_SOURCE_STRUCTURE_AFTER_LOCAL_REVISION',
                          source_level=None)
        if record['scope_id'] in by_scope:
            by_scope[record['scope_id']]['structure_assessment'] = deepcopy(record)
    for group in evidence['combinations']:
        valid = all(uid in by_id and by_id[uid]['content_hash'] == checksum
                    for uid, checksum in group['content_hashes'].items())
        valid &= all(uid in by_id and by_id[uid]['primary_scope_id'] == sid
                     for uid, sid in zip(group['unit_ids'], group['scope_ids']))
        if not valid:
            group['scope_change'] = 'STALE_COMPOSITION_AFTER_LOCAL_REVISION'
            composite = by_scope.get(group.get('composite_scope_id'))
            if composite:
                composite['structure_assessment'] = {**deepcopy(group), 'state': 'STRUCTURE_UNRESOLVED'}
                composite['display_title'] = 'Unresolved composite heading'
    recipe['packaging'] = package(units, scopes, evidence, recipe['target_chars'])
    recipe['display'] = display_transforms(units, evidence)
    recipe['level_policy'] = 'SOURCE_LEVEL_FOR_PACKAGING_HISTORICAL_SCOPE_TREE_RETAINED'
    return recipe


def repackage(workspace, reading_export, expected_revision, reason):
    """Publish a checked reading plan through the existing versioned transaction."""
    from . import organization as org
    from uuid import uuid4
    workspace, reading_export = Path(workspace).resolve(), Path(reading_export).resolve()
    if not reason or not reason.strip():
        raise ValueError('Repackaging requires a reason')
    if not org._disjoint(workspace, reading_export):
        raise ValueError('Reading export must be separate from the canonical workspace')
    with org.writer(workspace):
        snap = org.snapshot(workspace)
        if snap['revision'] != expected_revision:
            raise ValueError('Repackage expected_revision differs from current HEAD')
        view = read_reading(reading_export)
        plan = view['plan']
        if (plan['workspace_id'] != snap['workspace_id'] or plan['document_id'] != snap['document_id'] or
                plan['source_revision'] != snap['revision'] or
                plan['source_manifest_sha256'] != sha(snap['root'] / 'revision_manifest.json') or
                plan['source_identity_sha256'] != _identity_digest(snap['units']) or
                view['source_map']['input_sha256'] != snap['source_map']['input_sha256']):
            raise ValueError('Reading export is stale or targets another source revision')
        old_scopes = {s['scope_id']: s for s in snap['scopes']}
        for scope in view['scopes']:
            if scope['scope_id'] in old_scopes and scope['unit_ids'] != old_scopes[scope['scope_id']]['unit_ids']:
                raise ValueError('Reading plan changed a historical Scope query set')
        recipe = {'version': VERSION, 'structure': deepcopy(plan['structure']),
                  'target_chars': plan['packaging']['target_chars'],
                  'approved_export_sha256': sha(reading_export / 'export_manifest.json'),
                  'origin_revision': snap['revision'], 'origin_manifest_sha256': plan['source_manifest_sha256']}
        units, scopes = deepcopy(snap['units']), deepcopy(view['scopes'])
        revision = org._next_revision(workspace)
        event = {'type': 'repackage', 'parent_revision': snap['revision'], 'reason': reason,
                 'reading_export_sha256': recipe['approved_export_sha256'],
                 'historical_scope_policy': 'RETAIN_ORIGINAL_IDS_AND_MEMBERSHIPS',
                 'scope_mapping': {s['scope_id']: {'retained_scope_id': s['scope_id'],
                     'composite_parent': next((n.get('composition_parent') for n in scopes if n['scope_id'] == s['scope_id']), None)}
                     for s in snap['scopes']}, 'content_unchanged': True}
        stage = workspace / 'revisions' / ('.staging-' + uuid4().hex)
        try:
            org._render(stage, revision, units, scopes, snap['source_map'], snap['root'] / 'content',
                        list(snap['metadata']['asset_hashes']), snap['references'], org._annotations(snap), event,
                        reading=recipe, previous=snap)
            org._publish(workspace, stage, revision, event)
        except Exception as error:
            org._failure(workspace, event, error, stage)
            raise
        return {'status': 'REPACKAGED', 'revision': revision, 'parent_revision': snap['revision'],
                'units': len(units), 'scopes': len(scopes), 'files': len({u['content_ref']['file'] for u in units}),
                'content_unchanged': True, 'historical_scope_memberships_retained': True}


def export_reading(snapshot, output, pdf=None, target_chars=None):
    from .organization import _copy_assets, _disjoint, _write
    output = Path(output).resolve()
    protected = [snapshot['workspace'], snapshot['input_root'], PRODUCT]
    original_pdf = pdf or snapshot.get('input_pdf')
    if any(not _disjoint(output, root) for root in protected) or (
            original_pdf and Path(original_pdf).resolve().is_relative_to(output)):
        raise ValueError('Reading output must be separate from source, workspace and product')
    if output.exists():
        raise ValueError('Reading output already exists')
    units = deepcopy(snapshot['units'])
    reading = snapshot['metadata'].get('reading')
    evidence = (deepcopy(reading['structure']) if reading and pdf is None
                else source_structure(snapshot, pdf))
    if reading and target_chars is None:
        target_chars = reading['target_chars']
    scopes = refined_scopes(units, snapshot['scopes'], evidence)
    plan = package(units, scopes, evidence, target_chars)
    views = display_transforms(units, evidence)
    files, refs, generated = render_files(units, scopes, snapshot['references'], layout=plan['layout'], display=views)
    output.mkdir(parents=True)
    _copy_assets(snapshot['root'] / 'content', output / 'content', list(snapshot['metadata']['asset_hashes']))
    for filename, value in files.items():
        _write(within(output, filename), value)
    _write(output / 'README.md', _navigation(units, scopes, plan, refs))
    validate_links(output, files, refs)
    save(output / 'index/units.json', {'units': [{k:v for k,v in u.items() if k != 'text'} for u in units]})
    save(output / 'index/scopes.json', {'scopes': scopes})
    save(output / 'index/references.json', {'references': refs})
    mapping = deepcopy(snapshot['source_map'])
    mapping['objects'] = [{k:u[k] for k in ('unit_id', 'revision', 'content_ref', 'content_hash',
                          'source_refs', 'assessment', 'legacy_refs')} for u in units]
    mapping['derived_reading_view'] = True
    save(output / 'source_map.json', mapping)
    save(output / 'reading_plan.json', {'version': VERSION, 'workspace_id': snapshot['workspace_id'],
        'document_id': snapshot['document_id'],
        'source_revision': snapshot['revision'], 'source_manifest_sha256': sha(snapshot['root'] / 'revision_manifest.json'),
        'reconstructed_sha256': snapshot['metadata']['reconstructed_sha256'],
        'source_identity_sha256': _identity_digest(snapshot['units']),
        'original_scope_membership': {s['scope_id']: s['unit_ids'] for s in snapshot['scopes']},
        'structure': evidence, 'packaging': plan, 'display': views, 'generated': generated,
        'asset_hashes': snapshot['metadata']['asset_hashes'], 'original_unit_ids': [u['unit_id'] for u in snapshot['units']]})
    save(output / 'diagnostics.json', {'before': diagnostics(snapshot['units'], snapshot['scopes']),
                                    'after': diagnostics(units, scopes)})
    save(output / 'export_manifest.json', {'format': 'reading', 'source_revision': snapshot['revision'],
                                        'artifacts': inventory(output)})
    return verify_reading(output)


def read_reading(output):
    """Hydrate a fully verified derived view; never trust file names as Scope bounds."""
    from .organization import _no_links
    output = Path(output)
    _no_links(output)
    verify(output, 'export_manifest.json')
    plan = read(output / 'reading_plan.json')
    if plan['version'] != VERSION:
        raise ValueError('Unsupported reading plan version')
    units = read(output / 'index/units.json')['units']
    scopes = read(output / 'index/scopes.json')['scopes']
    refs = read(output / 'index/references.json')['references']
    texts, ids = {}, set()
    for unit in units:
        uid = unit['unit_id']
        if uid in ids or not re.fullmatch(r'u-[a-f0-9]{32}', uid):
            raise ValueError('Invalid or duplicated reading Unit identity')
        ids.add(uid)
        ref = unit['content_ref']
        if ref['file'] not in texts:
            texts[ref['file']] = within(output, ref['file']).read_bytes().decode('utf-8')
        if not 0 <= ref['start'] <= ref['end'] <= len(texts[ref['file']]):
            raise ValueError('Invalid reading range')
        raw = texts[ref['file']][ref['start']:ref['end']]
        if digest(raw) != unit['rendered_hash']:
            raise ValueError('Reading range hash differs')
        unit['text'] = restore(raw, unit['transforms'])
        if digest(unit['text']) != unit['content_hash']:
            raise ValueError('Reading Unit differs from canonical content')
    if ([u['unit_id'] for u in units] != plan['original_unit_ids'] or
            digest(''.join(u['text'] for u in units)) != plan['reconstructed_sha256'] or
            _identity_digest(units) != plan['source_identity_sha256']):
        raise ValueError('Reading export lost or repeated canonical content')
    current_membership = {s['scope_id']: s['unit_ids'] for s in scopes}
    if any(current_membership.get(sid) != members for sid, members in plan['original_scope_membership'].items()):
        raise ValueError('Historical Scope query membership changed')
    regenerated = package(units, scopes, plan['structure'], plan['packaging']['target_chars'])
    regenerated['target_basis'] = plan['packaging']['target_basis']
    if regenerated != plan['packaging'] or display_transforms(units, plan['structure']) != plan['display']:
        raise ValueError('Reading layout or display plan differs from its evidence')
    expected_units, expected_scopes = deepcopy(units), deepcopy(scopes)
    rendered, _, _ = render_files(expected_units, expected_scopes, refs,
                                 layout=plan['packaging']['layout'], display=plan['display'])
    if rendered != texts or expected_units != units or expected_scopes != scopes:
        raise ValueError('Reading files or indices contain unassigned changes')
    actual_markdown = {p.relative_to(output).as_posix() for p in (output / 'content').glob('*.md')}
    if actual_markdown != set(texts):
        raise ValueError('Unassigned reading Markdown files')
    for filename, checksum in plan['asset_hashes'].items():
        if sha(within(output / 'content', filename)) != checksum:
            raise ValueError('Reading asset changed')
    mapping = read(output / 'source_map.json')
    expected = [{k:u[k] for k in ('unit_id', 'revision', 'content_ref', 'content_hash',
                'source_refs', 'assessment', 'legacy_refs')} for u in units]
    if mapping['objects'] != expected or mapping.get('semantic_correctness_certified') is not False:
        raise ValueError('Reading source map differs')
    validate_links(output, texts, refs)
    return {'root': output, 'units': units, 'scopes': scopes, 'references': refs,
            'source_map': mapping, 'plan': plan, 'revision': plan['source_revision'],
            'workspace_id': plan['workspace_id'], 'document_id': plan['document_id'],
            'reconstructed': ''.join(u['text'] for u in units)}


def verify_reading(output):
    snapshot = read_reading(output)
    plan = snapshot['plan']
    return {'status': 'READING_EXPORT_VERIFIED', 'output': str(output),
        'units': len(snapshot['units']), 'files': len(plan['packaging']['files']), 'content_preserved': True,
        'source_revision': plan['source_revision'], 'semantic_correctness_certified': False}
