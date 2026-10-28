import os
from pathlib import Path
import queue
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

from PIL import Image
import pymupdf

from pdf2md_p2 import organization, source_map
from pdf2md_p2.common import inventory, read, save, sha


class OrganizationWorkspaceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.result = self.root / 'sealed'
        self.result.mkdir()
        self.workspace = self.root / 'organized'
        self.pdf = self.root / 'original.pdf'
        self.text = '<!-- PAGE 1 -->\n\n# First\n\nDATA_STATUS is 1.\n\n# Second\n\nADCRANGE is 2.\n'

    def fixture(self, text=None, mapping=False, assets=None, pages=1):
        self.text = self.text if text is None else text
        (self.result / 'manual.md').write_bytes(self.text.encode('utf-8'))
        with pymupdf.open() as document:
            for number in range(pages):
                page = document.new_page()
                page.insert_text((30, 50), 'DATA_STATUS is 1.\nADCRANGE is 2.\nPage ' + str(number + 1))
            document.save(self.pdf)
        for name, color in (assets or {}).items():
            path = self.result / name
            path.parent.mkdir(parents=True, exist_ok=True)
            Image.new('RGB', (12, 12), color).save(path)
        save(self.result / 'quality_report.json', {'expected_pages': pages,
            'completed_pages': list(range(1, pages + 1)), 'status': 'SUCCESS'})
        save(self.result / 'provenance.json', {'fixture': 'synthetic_existing_conversion',
                                              'compute_backend': 'NO_PARSER_EXECUTED'})
        if mapping:
            source_map.build(self.pdf, self.result, [])
        save(self.result / 'run_manifest.json', {'configuration': {'input_sha256': sha(self.pdf)},
                                                'artifacts': inventory(self.result)})

    def initial(self, **kwargs):
        organization.organize(self.result, self.workspace, **kwargs)
        return organization.snapshot(self.workspace)

    def target(self, snap, text='DATA_STATUS'):
        return next(unit for unit in snap['units'] if unit['kind'] == 'paragraph' and text in unit['text'])

    def candidate(self, snap=None, operation='replace', **values):
        snap = snap or organization.snapshot(self.workspace)
        unit = self.target(snap)
        op = {'operation': operation, 'unit_id': unit['unit_id']}
        if operation == 'replace':
            op['after'] = 'DATA_STATUS is 3.\n'
        op.update(values)
        return organization.propose(self.workspace, [op], proposer='test-tool',
                                    reason='Controlled synthetic change; not a technical correction')

    def publish(self, candidate):
        return organization.apply_proposal(self.workspace, candidate['proposal'], candidate['expected_revision'])

    def assert_memberships(self, snap):
        scopes = {scope['scope_id']: scope for scope in snap['scopes']}
        direct = {sid: [] for sid in scopes}
        descendant = {sid: [] for sid in scopes}
        for unit in snap['units']:
            direct[unit['primary_scope_id']].append(unit['unit_id'])
            cursor, seen = unit['primary_scope_id'], set()
            while cursor is not None:
                self.assertNotIn(cursor, seen, 'Scope hierarchy contains a cycle')
                seen.add(cursor)
                descendant[cursor].append(unit['unit_id'])
                cursor = scopes[cursor]['parent_id']
        by_id = {unit['unit_id']: unit for unit in snap['units']}
        for sid, scope in scopes.items():
            self.assertEqual(scope['direct_unit_ids'], direct[sid])
            self.assertEqual(scope['unit_ids'], descendant[sid])
            self.assertEqual(scope['pages'], sorted({page for uid in descendant[sid] for page in by_id[uid]['pages']}))
            self.assertEqual(set(scope['content_files']), {by_id[uid]['content_ref']['file'] for uid in descendant[sid]})

    def test_import_is_idempotent_preserves_input_and_allocated_identities(self):
        self.fixture(mapping=True)
        protected = inventory(self.result)
        pdf_hash = sha(self.pdf)
        first = self.initial(pdf=self.pdf)
        second = organization.organize(self.result, self.workspace, pdf=self.pdf)
        current = organization.snapshot(self.workspace)
        self.assertEqual(second['status'], 'REUSED')
        self.assertEqual(first['revision'], current['revision'])
        self.assertEqual([unit['unit_id'] for unit in first['units']], [unit['unit_id'] for unit in current['units']])
        self.assertEqual(current['reconstructed'], self.text)
        self.assertEqual(inventory(self.result), protected)
        self.assertEqual(sha(self.pdf), pdf_hash)
        self.assert_memberships(current)

    def test_no_source_map_and_no_accessible_pdf_keeps_usable_content(self):
        self.fixture()
        self.pdf.unlink()
        snap = self.initial()
        self.assertEqual(snap['reconstructed'], self.text)
        self.assertFalse(snap['import_manifest']['source_map_present'])
        self.assertIsNone(snap['input_pdf'])
        self.assertTrue(all(unit['assessment']['location'] == 'UNMAPPED' for unit in snap['units']))
        self.assertTrue(any(unit['searchable'] for unit in snap['units']))

    def test_crlf_non_bmp_exact_bytes_survive_import_and_single_export(self):
        text = '<!-- PAGE 1 -->\r\n\r\n# \u4e2d\u6587\r\n\r\nDATA_STATUS \U0001f9ea\r\n'
        self.fixture(text)
        snap = self.initial()
        self.assertEqual(snap['reconstructed'].encode('utf-8'), (self.result / 'manual.md').read_bytes())
        exported = self.root / 'single export'
        organization.export(self.workspace, exported, format='single')
        self.assertEqual((exported / 'manual.md').read_bytes(), text.encode('utf-8'))
        self.assertTrue(organization.verify_workspace(self.workspace)['original_content_preserved'])

    def test_crlf_existing_source_map_offsets_are_translated_without_byte_changes(self):
        text = '<!-- PAGE 1 -->\r\n\r\n# \u6e90\r\n\r\nDATA_STATUS is 1.\r\n'
        self.fixture(text, mapping=True)
        snap = self.initial(pdf=self.pdf)
        self.assertEqual(snap['reconstructed'].encode('utf-8'), text.encode('utf-8'))
        unit = self.target(snap)
        self.assertTrue(unit['legacy_refs'])
        original_mapping = read(self.result / 'source_map.json')
        original = next(row for row in original_mapping['objects'] if row['id'] == unit['legacy_refs'][0]['object_id'])
        self.assertEqual(unit['legacy_refs'][0]['original_map_range'], [original['start'], original['end']])
        self.assertEqual(unit['assessment']['content'], 'NOT_JUDGED')

    def test_no_headings_produce_multiple_virtual_scopes_and_complete_objects(self):
        table = '<table><tr><td>' + 'DATA_STATUS ' * 60 + '</td></tr>\n\n<tr><td>2</td></tr></table>\n'
        formula = '$$\nx =\n\ny + z\n$$\n'
        text = '<!-- PAGE 1 -->\n\n' + table + '\n' + formula + '\n' + 'Plain paragraph. ' * 30 + '\n'
        self.fixture(text)
        snap = self.initial(soft_chars=100)
        self.assertGreater(len({unit['content_ref']['file'] for unit in snap['units']}), 1)
        self.assertTrue(all(scope['type'] in ('ROOT', 'VIRTUAL_SCOPE') for scope in snap['scopes']))
        self.assertEqual(len([unit for unit in snap['units'] if unit['kind'] == 'table']), 1)
        self.assertEqual(len([unit for unit in snap['units'] if unit['kind'] == 'formula']), 1)
        self.assertIn(table, next(unit['text'] for unit in snap['units'] if unit['kind'] == 'table'))
        self.assertEqual(snap['reconstructed'], text)
        self.assert_memberships(snap)

    def test_asset_subdirectories_same_basename_remain_distinct_and_readable(self):
        text = '# First\n\n![one](assets/a/same.png)\n\n# Second\n\n![two](assets/b/same.png)\n'
        self.fixture(text, assets={'assets/a/same.png': 'red', 'assets/b/same.png': 'blue'})
        protected = inventory(self.result)
        snap = self.initial()
        for name in ('assets/a/same.png', 'assets/b/same.png'):
            self.assertEqual(sha(self.result / name), sha(snap['root'] / 'content' / name))
        self.assertNotEqual(sha(snap['root'] / 'content/assets/a/same.png'), sha(snap['root'] / 'content/assets/b/same.png'))
        self.assertEqual(len(snap['metadata']['asset_hashes']), 2)
        self.assertEqual(inventory(self.result), protected)
        export = self.root / 'split export'
        organization.export(self.workspace, export)
        self.assertTrue((export / 'README.md').is_file())
        self.assertTrue((export / 'content/assets/a/same.png').is_file())

    def test_missing_asset_rejected_before_publication(self):
        self.fixture('![missing](assets/missing.png)\n')
        with self.assertRaises((ValueError, FileNotFoundError)):
            self.initial()
        self.assertFalse((self.workspace / 'HEAD.json').exists())

    def test_candidate_check_rejects_missing_image_and_local_download_before_apply(self):
        self.fixture('# First\n\n![old](assets/existing.png)\n\nDATA_STATUS is 1.\n',
                     assets={'assets/existing.png': 'blue'})
        snap = self.initial()
        head = (self.workspace / 'HEAD.json').read_bytes()
        image = next(u for u in snap['units'] if u['kind'] == 'image')
        with self.assertRaises((ValueError, FileNotFoundError)):
            organization.propose(self.workspace, [{'unit_id': image['unit_id'], 'operation': 'replace',
                'after': '![new](assets/missing.png)\n'}], 'test', 'Missing image must fail checking')
        good = self.candidate(snap)
        candidate = read(good['proposal'])
        for after in ('DATA_STATUS [download](assets/missing.txt).\n',
                      'DATA_STATUS [outside](../outside.txt).\n'):
            with self.subTest(after=after):
                candidate['operations'][0]['after'] = after
                with self.assertRaises((ValueError, FileNotFoundError)):
                    organization.check_proposal(self.workspace, candidate)
        self.assertEqual((self.workspace / 'HEAD.json').read_bytes(), head)
        self.assertEqual(organization.snapshot(self.workspace)['reconstructed'], self.text)

    def test_candidate_accepts_existing_resource_without_changing_asset_bytes(self):
        self.fixture('# First\n\n![old](assets/existing.png)\n\nDATA_STATUS is 1.\n',
                     assets={'assets/existing.png': 'blue'})
        snap = self.initial()
        image = next(u for u in snap['units'] if u['kind'] == 'image')
        proposed = organization.propose(self.workspace, [{'unit_id': image['unit_id'], 'operation': 'replace',
            'after': '![new label](assets/existing.png)\n'}], 'test', 'Label-only synthetic revision')
        self.assertEqual(organization.check_proposal(self.workspace, proposed['proposal'])['status'], 'CHECKED')
        self.publish(proposed)
        self.assertEqual(organization.snapshot(self.workspace)['metadata']['asset_hashes'], snap['metadata']['asset_hashes'])

    def test_confirmed_atomic_image_caption_allows_bounded_edit_but_not_member_removal(self):
        text = '<!-- PAGE 1 -->\n\n![Timing](assets/timing.png)\n\nFigure 3. Timing conditon.\n'
        self.fixture(text, mapping=True, assets={'assets/timing.png': 'blue'})
        mapping = read(self.result / 'source_map.json')
        for row in mapping['objects']:
            if 'Timing' in row['text']:
                row.update(atomic_group='figure-3-condition', atomic_group_confirmed=True)
        save(self.result / 'source_map.json', mapping)
        sealed = read(self.result / 'run_manifest.json')
        sealed['artifacts'] = {p: h for p, h in inventory(self.result).items() if p != 'run_manifest.json'}
        save(self.result / 'run_manifest.json', sealed)
        snap = self.initial()
        unit = next(u for u in snap['units'] if u['structure_basis'] == 'CONFIRMED_ATOMIC_GROUP')
        proposed = organization.propose(self.workspace, [{'unit_id': unit['unit_id'], 'operation': 'replace',
            'after': unit['text'].replace('conditon', 'condition')}], 'test', 'Controlled caption spelling edit')
        self.publish(proposed)
        current = organization.snapshot(self.workspace)
        revised = next(u for u in current['units'] if u['unit_id'] == unit['unit_id'])
        self.assertIn('Figure 3. Timing condition.', revised['text'])
        self.assertIn('![Timing]', revised['text'])
        self.assertEqual(revised['revision'], unit['revision'] + 1)
        self.assertEqual(revised['source_refs'], unit['source_refs'])
        with self.assertRaisesRegex(ValueError, 'Structural split/merge'):
            organization.propose(self.workspace, [{'unit_id': unit['unit_id'], 'operation': 'replace',
                'after': '![Timing](assets/timing.png)\n'}], 'test', 'Member removal must not masquerade as text revision')

    def test_asset_escape_and_external_resource_refused(self):
        for index, url in enumerate(('../outside.png', '/tmp/absolute.png', 'https://example.invalid/image.png')):
            with self.subTest(url=url):
                if index:
                    (self.result / 'run_manifest.json').unlink()
                    self.pdf.unlink()
                self.fixture(f'![not allowed]({url})\n')
                with self.assertRaises(ValueError):
                    self.initial()
                self.assertFalse((self.workspace / 'HEAD.json').exists())

    def test_original_pdf_hash_mismatch_rejected(self):
        self.fixture()
        wrong = self.root / 'wrong.pdf'
        wrong.write_bytes(b'not the bound input')
        with self.assertRaisesRegex(ValueError, 'SHA256'):
            self.initial(pdf=wrong)

    def test_incomplete_result_or_changed_import_cannot_be_organized(self):
        self.fixture()
        save(self.result / 'quality_report.json', {'expected_pages': 2, 'completed_pages': [1]})
        (self.result / 'run_manifest.json').unlink()
        save(self.result / 'run_manifest.json', {'artifacts': inventory(self.result)})
        with self.assertRaisesRegex(ValueError, 'complete conversion'):
            self.initial()
        (self.result / 'manual.md').write_bytes(b'changed outside seal')
        with self.assertRaisesRegex(ValueError, 'Sealed product'):
            self.initial()

    def test_replacement_keeps_unit_identity_and_unrelated_canonical_hashes(self):
        self.fixture(mapping=True)
        before = self.initial(pdf=self.pdf)
        sealed_before = inventory(before['root'])
        target = self.target(before)
        candidate = self.candidate(before)
        self.assertEqual(organization.snapshot(self.workspace)['revision'], before['revision'])
        checked = organization.check_proposal(self.workspace, candidate['proposal'])
        self.assertFalse(checked['published'])
        published = self.publish(candidate)
        after = organization.snapshot(self.workspace)
        self.assertEqual(published['revision'], after['revision'])
        self.assertEqual([unit['unit_id'] for unit in before['units']], [unit['unit_id'] for unit in after['units']])
        prior = {unit['unit_id']: unit for unit in before['units']}
        for unit in after['units']:
            old = prior[unit['unit_id']]
            self.assertEqual(unit['source_refs'], old['source_refs'])
            if unit['unit_id'] == target['unit_id']:
                self.assertEqual(unit['revision'], old['revision'] + 1)
                self.assertNotEqual(unit['content_hash'], old['content_hash'])
                self.assertEqual(unit['assessment']['content'], 'UNVERIFIED_LOCAL_REVISION')
            else:
                self.assertEqual(unit['content_hash'], old['content_hash'])
                self.assertEqual(unit['text'], old['text'])
                self.assertEqual(unit['revision'], old['revision'])
        self.assertEqual(inventory(before['root']), sealed_before)
        self.assert_memberships(after)

    def test_move_changes_membership_and_file_but_not_text_identity_or_content_revision(self):
        self.fixture(self.text.replace('# Second', '<!-- PAGE 2 -->\n\n# Second'), pages=2)
        before = self.initial()
        unit = self.target(before)
        destination = self.target(before, 'ADCRANGE')['primary_scope_id']
        candidate = self.candidate(before, operation='move', target_scope_id=destination)
        self.publish(candidate)
        after = organization.snapshot(self.workspace)
        moved = next(item for item in after['units'] if item['unit_id'] == unit['unit_id'])
        self.assertEqual(moved['primary_scope_id'], destination)
        self.assertEqual(moved['text'], unit['text'])
        self.assertEqual(moved['content_hash'], unit['content_hash'])
        self.assertEqual(moved['revision'], unit['revision'])
        self.assertNotEqual(moved['content_ref']['file'], unit['content_ref']['file'])
        self.assertEqual(after['reconstructed'], before['reconstructed'])
        self.assert_memberships(after)

    def test_heading_revision_refreshes_title_without_reallocating_scope_identity(self):
        self.fixture()
        before = self.initial()
        heading = next(unit for unit in before['units'] if unit['kind'] == 'heading' and 'First' in unit['text'])
        proposal = organization.propose(self.workspace,
            [{'operation': 'replace', 'unit_id': heading['unit_id'], 'after': '# Revised Heading\n'}],
            proposer='test-tool', reason='Synthetic heading metadata consistency check')
        self.publish(proposal)
        after = organization.snapshot(self.workspace)
        current = next(unit for unit in after['units'] if unit['unit_id'] == heading['unit_id'])
        self.assertEqual(current['primary_scope_id'], heading['primary_scope_id'])
        self.assertEqual(current['heading']['title'], 'Revised Heading')
        scope = next(scope for scope in after['scopes'] if scope['scope_id'] == current['primary_scope_id'])
        self.assertEqual(scope['title'], 'Revised Heading')
        self.assertIn('Revised Heading', (after['root'] / 'README.md').read_text(encoding='utf-8'))
        self.assert_memberships(after)

    def test_resolved_reference_remains_bound_to_stable_target_after_heading_rename(self):
        self.fixture('# First\n\nDATA_STATUS is 1.\n\n# Second\n\nSee [First](#first).\n')
        before = self.initial()
        original_ref, = [ref for ref in before['references'] if ref['type'] == 'explicit_reference']
        heading = next(unit for unit in before['units'] if unit['unit_id'] == original_ref['target'])
        proposal = organization.propose(self.workspace,
            [{'operation': 'replace', 'unit_id': heading['unit_id'], 'after': '# Revised Heading\n'}],
            proposer='test-tool', reason='Stable target identity must survive heading text changes')
        self.publish(proposal)
        after = organization.snapshot(self.workspace)
        current_ref, = [ref for ref in after['references'] if ref['type'] == 'explicit_reference']
        self.assertEqual(current_ref['status'], 'RESOLVED')
        self.assertEqual(current_ref['target'], original_ref['target'])
        self.assertIn('[First](#first)', after['reconstructed'])

    def test_heading_level_change_is_explicitly_rejected_not_silently_reparented(self):
        self.fixture()
        before = self.initial()
        heading = next(unit for unit in before['units'] if unit['kind'] == 'heading')
        with self.assertRaisesRegex(ValueError, '[Ss]tructur|[Ll]evel|[Hh]ierarchy'):
            organization.propose(self.workspace,
                [{'operation': 'replace', 'unit_id': heading['unit_id'], 'after': '### First\n'}],
                proposer='test-tool', reason='Level change requires an explicit structural operation')
        self.assertEqual(organization.snapshot(self.workspace)['revision'], before['revision'])

    def test_inferred_heading_same_style_title_revision_preserves_boundary_evidence(self):
        self.fixture('**First section**\n\nDATA_STATUS is 1.\n\n**Second section**\n\nADCRANGE is 2.\n')
        before = self.initial()
        heading = next(unit for unit in before['units'] if unit.get('heading', {}).get('title') == 'First section')
        proposal = organization.propose(self.workspace,
            [{'operation': 'replace', 'unit_id': heading['unit_id'], 'after': '**Revised section**\n'}],
            proposer='test-tool', reason='Preserve repeated-style evidence while changing only a synthetic title')
        self.publish(proposal)
        after = organization.snapshot(self.workspace)
        current = next(unit for unit in after['units'] if unit['unit_id'] == heading['unit_id'])
        self.assertEqual(current['primary_scope_id'], heading['primary_scope_id'])
        self.assertEqual(current['heading']['title'], 'Revised section')
        self.assertEqual(current['heading']['type'], 'INFERRED_STRUCTURE')
        self.assertEqual(current['heading']['basis'], heading['heading']['basis'])
        scope = next(scope for scope in after['scopes'] if scope['scope_id'] == current['primary_scope_id'])
        self.assertEqual(scope['title'], 'Revised section')
        self.assertEqual(scope['type'], 'INFERRED_STRUCTURE')

    def test_fabricated_source_evidence_cannot_become_a_bound_repair(self):
        self.fixture(mapping=True)
        before = self.initial(pdf=self.pdf)
        with self.assertRaisesRegex(ValueError, 'Source evidence'):
            self.candidate(before, source_evidence=[{'input_sha256': '0' * 64,
                                                    'page': 1, 'bbox': [1, 2, 3, 4]}])
        with self.assertRaisesRegex(ValueError, 'retained source regions'):
            self.candidate(before, source_evidence=[{'input_sha256': sha(self.pdf),
                                                    'page': 99, 'bbox': [1, 2, 3, 4]}])
        self.assertEqual(organization.snapshot(self.workspace)['revision'], before['revision'])

    def test_stale_workspace_revision_and_hash_refused_without_changing_head(self):
        self.fixture()
        initial = self.initial()
        stale = self.candidate(initial)
        other = self.candidate(initial, after='DATA_STATUS is 4.\n')
        self.publish(other)
        head = (self.workspace / 'HEAD.json').read_bytes()
        with self.assertRaisesRegex(ValueError, 'expected_revision'):
            self.publish(stale)
        with self.assertRaisesRegex(ValueError, 'Stale proposal'):
            organization.check_proposal(self.workspace, stale['proposal'])
        fresh = self.candidate(after='DATA_STATUS is 5.\n')
        data = read(fresh['proposal'])
        data['operations'][0]['expected_content_hash'] = '0' * 64
        with self.assertRaisesRegex(ValueError, 'expected_content_hash'):
            organization.apply_proposal(self.workspace, data, fresh['expected_revision'])
        self.assertEqual((self.workspace / 'HEAD.json').read_bytes(), head)

    def test_reader_pins_old_snapshot_when_head_changes_during_read(self):
        self.fixture(mapping=True)
        initial = self.initial(pdf=self.pdf)
        candidate = self.candidate(initial)
        original_read = organization.read
        first_head = True
        def publish_after_head_read(path):
            nonlocal first_head
            value = original_read(path)
            if Path(path).name == 'HEAD.json' and first_head:
                first_head = False
                self.publish(candidate)
            return value
        with patch.object(organization, 'read', side_effect=publish_after_head_read):
            pinned = organization.snapshot(self.workspace)
        self.assertFalse(first_head)
        self.assertEqual(pinned['revision'], initial['revision'])
        self.assertEqual(pinned['reconstructed'], initial['reconstructed'])
        self.assertEqual(pinned['source_map']['revision'], initial['revision'])
        current = organization.snapshot(self.workspace)
        self.assertNotEqual(current['revision'], pinned['revision'])
        self.assertIn('DATA_STATUS is 3.', current['reconstructed'])

    def test_annotation_kept_separate_and_dependency_invalidated_by_replacement(self):
        self.fixture()
        initial = self.initial()
        candidate = self.candidate(initial, operation='annotate', after='Derived explanation, not original manual text.\n')
        self.publish(candidate)
        annotated = organization.snapshot(self.workspace)
        relation, = [ref for ref in annotated['references'] if ref['type'] == 'derived_from_revision']
        self.assertEqual(annotated['reconstructed'], initial['reconstructed'])
        self.assertTrue((annotated['root'] / relation['file']).is_file())
        self.assertEqual(relation['status'], 'DERIVED_NOT_SOURCE_TEXT')
        self.publish(self.candidate())
        revised = organization.snapshot(self.workspace)
        dependency, = [ref for ref in revised['references'] if ref['type'] == 'derived_from_revision']
        self.assertEqual(dependency['status'], 'STALE_NEEDS_REVIEW')
        self.assertEqual(dependency['changed_dependencies'], [self.target(initial)['unit_id']])
        self.assertEqual(dependency['depends_on_revision'], relation['depends_on_revision'])

    def test_rollback_restores_content_sources_and_membership_without_rewriting_history(self):
        self.fixture(mapping=True)
        initial = self.initial(pdf=self.pdf)
        original_inventory = inventory(initial['root'])
        self.publish(self.candidate(initial))
        changed = organization.snapshot(self.workspace)
        changed_inventory = inventory(changed['root'])
        result = organization.rollback(self.workspace, initial['revision'], changed['revision'], 'Controlled rollback')
        restored = organization.snapshot(self.workspace)
        self.assertNotEqual(restored['revision'], initial['revision'])
        self.assertEqual(result['revision'], restored['revision'])
        self.assertEqual(restored['reconstructed'], initial['reconstructed'])
        for before, after in zip(initial['units'], restored['units']):
            self.assertEqual(before['unit_id'], after['unit_id'])
            self.assertEqual(before['content_hash'], after['content_hash'])
            self.assertEqual(before['source_refs'], after['source_refs'])
            self.assertEqual(before['primary_scope_id'], after['primary_scope_id'])
        self.assertEqual(self.target(restored)['revision'], self.target(initial)['revision'] + 2)
        self.assertEqual(inventory(initial['root']), original_inventory)
        self.assertEqual(inventory(changed['root']), changed_inventory)
        self.assertEqual(len(organization.history(self.workspace)['events']), 3)
        self.assert_memberships(restored)

    def test_structural_split_and_false_human_verification_refused(self):
        self.fixture()
        snap = self.initial()
        with self.assertRaisesRegex(ValueError, 'split/merge'):
            self.candidate(snap, after='First paragraph.\n\nSecond paragraph.\n')
        with self.assertRaisesRegex(ValueError, 'Unsupported operation'):
            self.candidate(snap, operation='merge')
        candidate = self.candidate(snap)
        data = read(candidate['proposal'])
        data['human_verified'] = True
        with self.assertRaisesRegex(ValueError, 'not human verification'):
            organization.check_proposal(self.workspace, data)

    def test_failure_at_each_transaction_phase_preserves_old_readable_head(self):
        self.fixture()
        for phase in ('content_written', 'indexes_written', 'manifest_written',
                      'validated_before_publish', 'revision_published_before_head'):
            with self.subTest(phase=phase):
                self.workspace = self.root / ('organized-' + phase)
                initial = self.initial()
                head = (self.workspace / 'HEAD.json').read_bytes()
                old_files = inventory(initial['root'])
                candidate = self.candidate(initial)
                seen = []
                def fail(current):
                    seen.append(current)
                    visible = organization.snapshot(self.workspace)
                    self.assertEqual(visible['revision'], initial['revision'])
                    self.assertEqual(visible['reconstructed'], initial['reconstructed'])
                    if current == phase:
                        raise RuntimeError('Injected at ' + phase)
                with patch.object(organization, '_checkpoint', side_effect=fail):
                    with self.assertRaisesRegex(RuntimeError, phase):
                        self.publish(candidate)
                self.assertIn(phase, seen)
                self.assertEqual((self.workspace / 'HEAD.json').read_bytes(), head)
                self.assertEqual(inventory(initial['root']), old_files)
                self.assertEqual(organization.snapshot(self.workspace)['reconstructed'], initial['reconstructed'])
                failures = list((self.workspace / 'history').glob('failed-*.json'))
                self.assertEqual(len(failures), 1)
                self.assertEqual(read(failures[0])['status'], 'FAILED')
                self.publish(candidate)
                self.assertIn('DATA_STATUS is 3.', organization.snapshot(self.workspace)['reconstructed'])

    def test_initial_import_after_publish_before_head_can_recover_same_identities(self):
        self.fixture()
        def fail(phase):
            if phase == 'revision_published_before_head':
                raise RuntimeError('Interrupted initial publication')
        with patch.object(organization, '_checkpoint', side_effect=fail):
            with self.assertRaises(RuntimeError):
                self.initial()
        self.assertFalse((self.workspace / 'HEAD.json').exists())
        published = read(self.workspace / 'revisions/rev-000001/index/units.json')['units']
        result = organization.organize(self.result, self.workspace)
        self.assertEqual(result['status'], 'RECOVERED')
        recovered = organization.snapshot(self.workspace)
        self.assertEqual([unit['unit_id'] for unit in published], [unit['unit_id'] for unit in recovered['units']])
        self.assertEqual(len(list((self.workspace / 'history').glob('failed-*.json'))), 1)

    def test_initial_import_before_index_publication_reuses_recorded_identities(self):
        self.fixture()
        def fail(phase):
            if phase == 'content_written':
                raise RuntimeError('Interrupted before indexes')
        with patch.object(organization, '_checkpoint', side_effect=fail):
            with self.assertRaises(RuntimeError):
                self.initial()
        identities = read(self.workspace / 'imports/identities.json')
        self.assertFalse((self.workspace / 'HEAD.json').exists())
        organization.organize(self.result, self.workspace)
        recovered = organization.snapshot(self.workspace)
        self.assertEqual([unit['unit_id'] for unit in identities['units']],
                         [unit['unit_id'] for unit in recovered['units']])
        self.assertEqual([scope['scope_id'] for scope in identities['scopes']],
                         [scope['scope_id'] for scope in recovered['scopes']])

    def test_source_changed_during_import_is_detected_before_head_publication(self):
        self.fixture()
        changed = False
        def alter_source(phase):
            nonlocal changed
            if phase == 'content_written' and not changed:
                changed = True
                (self.result / 'manual.md').write_bytes((self.text + 'External concurrent edit.\n').encode('utf-8'))
        with patch.object(organization, '_checkpoint', side_effect=alter_source):
            with self.assertRaises(ValueError):
                self.initial()
        self.assertTrue(changed)
        self.assertFalse((self.workspace / 'HEAD.json').exists())

    def test_interrupted_import_rejects_tampered_persisted_identity_records(self):
        self.fixture()
        def fail(phase):
            if phase == 'content_written':
                raise RuntimeError('Pause initial construction')
        with patch.object(organization, '_checkpoint', side_effect=fail):
            with self.assertRaises(RuntimeError):
                self.initial()
        path = self.workspace / 'imports/identities.json'
        identities = read(path)
        identities['units'][0]['unit_id'] = 'u-' + 'f' * 32
        save(path, identities)
        with self.assertRaisesRegex(ValueError, '[Ii]dentity|[Ii]dentities|[Ii]mport|[Ss]eal'):
            organization.organize(self.result, self.workspace)
        self.assertFalse((self.workspace / 'HEAD.json').exists())

    def test_external_body_index_source_and_asset_tamper_are_rejected(self):
        self.fixture(self.text + '\n![asset](assets/sample.png)\n', assets={'assets/sample.png': 'green'})
        snap = self.initial()
        for name in (self.target(snap)['content_ref']['file'], 'index/search.json',
                     'source_map.json', 'content/assets/sample.png'):
            with self.subTest(name=name):
                path = snap['root'] / name
                original = path.read_bytes()
                try:
                    path.write_bytes(original + b'UNDECLARED_CHANGE')
                    with self.assertRaisesRegex(ValueError, 'Sealed product'):
                        organization.snapshot(self.workspace)
                finally:
                    path.write_bytes(original)
        self.assertEqual(organization.snapshot(self.workspace)['revision'], snap['revision'])

    def test_import_identity_tamper_refused(self):
        self.fixture()
        self.initial()
        path = self.workspace / 'imports/import-manifest.json'
        altered = read(path)
        altered['binding']['manual_sha256'] = 'b' * 64
        save(path, altered)
        with self.assertRaisesRegex(ValueError, 'Import identity'):
            organization.snapshot(self.workspace)

    def test_real_subprocess_writer_lock_rejects_concurrent_writer_then_releases(self):
        self.fixture()
        self.initial()
        source = str(Path(organization.__file__).resolve().parents[1])
        env = dict(os.environ, PYTHONPATH=source + os.pathsep + os.environ.get('PYTHONPATH', ''))
        program = ('from pathlib import Path\nimport sys\nfrom pdf2md_p2.organization import writer\n'
                   'with writer(Path(sys.argv[1])):\n    print("LOCK_HELD", flush=True)\n    sys.stdin.read(1)\n')
        process = subprocess.Popen([sys.executable, '-B', '-u', '-c', program, str(self.workspace)],
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   text=True, env=env)
        try:
            ready = queue.Queue()
            threading.Thread(target=lambda: ready.put(process.stdout.readline()), daemon=True).start()
            self.assertEqual(ready.get(timeout=15).strip(), 'LOCK_HELD')
            with self.assertRaisesRegex(ValueError, 'Another workspace writer'):
                self.candidate()
            process.stdin.write('x')
            process.stdin.flush()
            self.assertEqual(process.wait(timeout=15), 0)
            with organization.writer(self.workspace):
                self.assertTrue((self.workspace / 'HEAD.json').is_file())
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=10)
            for stream in (process.stdin, process.stdout, process.stderr):
                stream.close()

    @unittest.skipUnless(os.name != 'nt', 'Symlink creation without Windows privilege requirement')
    def test_symbolic_lock_cannot_write_outside_workspace(self):
        self.fixture()
        self.workspace.mkdir()
        protected = self.root / 'must-remain-empty.bin'
        protected.write_bytes(b'')
        (self.workspace / '.workspace.lock').symlink_to(protected)
        with self.assertRaisesRegex(ValueError, '[Ss]ymbolic|[Ll]ink|[Ll]ock'):
            self.initial()
        self.assertEqual(protected.read_bytes(), b'')
        self.assertFalse((self.workspace / 'HEAD.json').exists())

    def test_different_import_configuration_and_overlapping_workspace_refused(self):
        self.fixture()
        self.initial()
        with self.assertRaisesRegex(ValueError, 'different import'):
            organization.organize(self.result, self.workspace, soft_chars=123)
        with self.assertRaisesRegex(ValueError, 'separate'):
            organization.organize(self.result, self.result / 'organized')
        with self.assertRaisesRegex(ValueError, 'outside'):
            organization.export(self.workspace, self.result / 'export')


if __name__ == '__main__':
    unittest.main()
