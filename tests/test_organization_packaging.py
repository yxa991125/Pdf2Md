from copy import deepcopy
import os
from pathlib import Path
import subprocess
import shutil
import sys
import unittest
from unittest.mock import patch

import pymupdf

from pdf2md_p2 import organization, organization_packaging as packing, organization_query as query, organization_links as links
from pdf2md_p2.common import inventory, read, save, sha
import test_organization_workspace as baseline


class ReadingPackagingTests(unittest.TestCase):
    setUp = baseline.OrganizationWorkspaceTests.setUp
    fixture = baseline.OrganizationWorkspaceTests.fixture
    initial = baseline.OrganizationWorkspaceTests.initial

    def source_fixture(self, duplicate=False, different_font=False, high=False, intervening=False):
        middle = 'Not a title.\n\n' if intervening else ''
        self.fixture('<!-- PAGE 1 -->\n\n## 3.2\n\n' + middle +
                     '## Functional range\n\nDATA_STATUS is 1.\n\n## Other\n\nDifferent body.\n')
        self.pdf.unlink()
        with pymupdf.open() as doc:
            page = doc.new_page()
            y = 30 if high else 110
            page.insert_text((30, y), '3.2', fontname='hebo', fontsize=14)
            page.insert_text((130, y), 'Functional range',
                             fontname='helv' if different_font else 'hebo', fontsize=14)
            if duplicate:
                page.insert_text((130, 180), 'Functional range', fontname='hebo', fontsize=14)
            doc.save(self.pdf)
        files = inventory(self.result)
        files.pop('run_manifest.json')
        save(self.result / 'run_manifest.json', {'configuration': {'input_sha256': sha(self.pdf)}, 'artifacts': files})
        return self.initial(pdf=self.pdf)

    def exported(self, snap=None, **kwargs):
        snap = snap or self.initial()
        output = self.root / 'reading'
        result = packing.export_reading(snap, output, **kwargs)
        self.assertEqual(result['status'], 'READING_EXPORT_VERIFIED')
        return packing.read_reading(output)

    def test_source_aligned_fragments_preserve_identity_raw_text_and_old_scope_sets(self):
        snap = self.source_fixture()
        before = inventory(self.workspace)
        view = self.exported(snap, pdf=self.pdf)
        self.assertEqual(view['reconstructed'], snap['reconstructed'])
        self.assertEqual(inventory(self.workspace), before)
        self.assertEqual([u['unit_id'] for u in view['units']], [u['unit_id'] for u in snap['units']])
        pairs = view['plan']['structure']['combinations']
        self.assertEqual(len(pairs), 1)
        self.assertEqual(pairs[0]['title'], '3.2 Functional range')
        self.assertEqual(pairs[0]['scope_change'], 'NEW_COMPOSITE_PARENT_ORIGINAL_IDS_RETAINED')
        files = [p.read_text() for p in (view['root'] / 'content').glob('*.md')]
        text = '\n'.join(files)
        self.assertEqual(text.count('## 3.2 Functional range'), 1)
        self.assertNotIn('## Functional range', text)
        self.assertTrue(all(u['assessment']['location'] == 'UNMAPPED' for u in view['units']))
        self.assertEqual([(u['content_hash'], u['revision'], u['source_refs']) for u in view['units']],
                         [(u['content_hash'], u['revision'], u['source_refs']) for u in snap['units']])
        self.assertIn('3.2 Functional range', (view['root'] / 'README.md').read_text())

    def test_adjacent_strings_alone_do_not_confirm_structure(self):
        snap = self.source_fixture()
        view = self.exported(snap)
        self.assertFalse(view['plan']['structure']['combinations'])
        self.assertTrue(any(r['state'] == 'STRUCTURE_FRAGMENT' for r in view['plan']['structure']['headings']))

    def test_duplicate_source_line_is_unresolved_not_force_matched(self):
        snap = self.source_fixture(duplicate=True)
        evidence = packing.source_structure(snap, self.pdf)
        self.assertFalse(evidence['combinations'])
        self.assertTrue(any(r['state'] == 'STRUCTURE_UNRESOLVED' for r in evidence['headings']))

    def test_different_font_does_not_combine_number_and_text(self):
        snap = self.source_fixture(different_font=True)
        self.assertFalse(packing.source_structure(snap, self.pdf)['combinations'])

    def test_top_band_does_not_combine_running_fragments(self):
        snap = self.source_fixture(high=True)
        self.assertFalse(packing.source_structure(snap, self.pdf)['combinations'])

    def test_intervening_body_prevents_title_composition(self):
        snap = self.source_fixture(intervening=True)
        self.assertFalse(packing.source_structure(snap, self.pdf)['combinations'])

    def test_wrong_pdf_is_rejected_before_output_is_created(self):
        snap = self.source_fixture()
        self.pdf.write_bytes(b'not the original input')
        with self.assertRaisesRegex(ValueError, 'hash differs'):
            packing.export_reading(snap, self.root / 'reading', self.pdf)
        self.assertFalse((self.root / 'reading').exists())

    def test_three_scopes_in_one_file_filter_middle_not_neighbors(self):
        self.fixture('## Alpha\n\nLEFT_ID is 1.\n\n## Beta\n\nMIDDLE_ID is 2.\n\n## Gamma\n\nRIGHT_ID is 3.\n')
        view = self.exported()
        self.assertEqual(len(view['plan']['packaging']['files']), 1)
        middle = next(s for s in view['scopes'] if s['title'] == 'Beta')
        result = query.search(view, scope=middle['scope_id'])
        self.assertTrue(result['results'])
        self.assertTrue(all(m['unit_id'] in middle['unit_ids'] for m in result['results']))
        self.assertFalse(query.search(view, 'LEFT_ID', scope=middle['scope_id'])['results'])
        self.assertFalse(query.search(view, 'RIGHT_ID', scope=middle['scope_id'])['results'])

    def test_no_heading_virtual_scope_can_span_files_without_losing_objects(self):
        self.fixture('\n\n'.join(('Paragraph ' + str(i) + ' ' + 'x' * 180 + '.') for i in range(20)))
        view = self.exported(target_chars=256)
        self.assertGreater(len(view['plan']['packaging']['files']), 1)
        virtual = next(s for s in view['scopes'] if s['type'] == 'VIRTUAL_SCOPE')
        self.assertGreater(len(virtual['content_files']), 1)
        self.assertEqual(view['reconstructed'], self.text)
        self.assertTrue(all(b['reason'] == 'PARAGRAPH_CONTINUATION_AT_COMPLETE_OBJECT'
                            for b in view['plan']['packaging']['boundaries']))

    def test_oversized_table_formula_unicode_assets_and_cross_links_are_preserved(self):
        text = ('# Alpha\n\nSee [second](#beta).\n\n<table><tr><td>' + 'x' * 5000 +
                '</td></tr></table>\n\n$$\nx = 2\n$$\n\n# Beta\n\n' +
                '\u4e2d\u6587 \U0001f9ea &amp; \u03bcV.\n\n![plot](assets/plot.png)\n')
        self.fixture(text, assets={'assets/plot.png': 'red'})
        view = self.exported(target_chars=256)
        self.assertEqual(view['reconstructed'].encode(), text.encode())
        table = next(u for u in view['units'] if u['kind'] == 'table')
        self.assertGreater(len(table['text']), 5000)
        self.assertIn(table['unit_id'], [uid for f in view['plan']['packaging']['files']
                                       for uid in f['oversized_complete_objects']])
        self.assertEqual(sha(self.result / 'assets/plot.png'), sha(view['root'] / 'content/assets/plot.png'))

    def test_resealed_wrong_line_binding_is_rejected(self):
        self.fixture()
        view = self.exported()
        file = view['root'] / 'index/units.json'
        index = read(file)
        index['units'][0]['content_ref']['line_start'] += 1
        save(file, index)
        manifest = read(view['root'] / 'export_manifest.json')
        manifest['artifacts'] = inventory(view['root'])
        manifest['artifacts'].pop('export_manifest.json')
        save(view['root'] / 'export_manifest.json', manifest)
        with self.assertRaises(ValueError):
            packing.verify_reading(view['root'])

    def test_original_scope_membership_tampering_is_rejected(self):
        self.fixture()
        view = self.exported()
        file = view['root'] / 'index/scopes.json'
        index = read(file)
        index['scopes'][-1]['unit_ids'] = []
        save(file, index)
        manifest = read(view['root'] / 'export_manifest.json')
        manifest['artifacts'] = inventory(view['root'])
        manifest['artifacts'].pop('export_manifest.json')
        save(view['root'] / 'export_manifest.json', manifest)
        with self.assertRaises(ValueError):
            packing.verify_reading(view['root'])

    def test_existing_export_and_source_overwrite_are_rejected(self):
        self.fixture()
        snap = self.initial()
        view = self.exported(snap)
        for path in (view['root'], self.workspace / 'output', self.result / 'reading'):
            with self.subTest(path=path), self.assertRaises(ValueError):
                packing.export_reading(snap, path)

    def test_real_cli_reading_export_and_verify(self):
        self.fixture()
        self.initial()
        script = Path(organization.__file__).resolve().parents[2] / 'scripts/pdf2md.py'
        output = self.root / 'cli reading'
        for arguments in (
            ['export', '--workspace', str(self.workspace), '--format', 'reading', '--output-dir', str(output)],
            ['verify-reading', '--output-dir', str(output)],
        ):
            command = subprocess.run([sys.executable, '-B', str(script), *arguments], text=True, capture_output=True)
            self.assertEqual(command.returncode, 0, command.stdout + command.stderr)
            self.assertIn('READING_EXPORT_VERIFIED', command.stdout)

    def publish_reading(self, snap):
        view = self.exported(snap, pdf=self.pdf)
        packing.repackage(self.workspace, view['root'], snap['revision'], 'Controlled reading integration')
        return organization.snapshot(self.workspace)

    def test_repackage_changes_only_structure_retains_history_and_source(self):
        snap = self.source_fixture()
        before = inventory(snap['root'])
        new = self.publish_reading(snap)
        self.assertEqual(new['revision'], 'rev-000002')
        self.assertEqual(new['reconstructed'], snap['reconstructed'])
        self.assertEqual(inventory(snap['root']), before)
        self.assertEqual(organization.snapshot(self.workspace, 'rev-000001')['reconstructed'], snap['reconstructed'])
        for old, unit in zip(snap['units'], new['units']):
            for key in ('unit_id', 'revision', 'content_hash', 'source_refs', 'legacy_refs', 'assessment'):
                self.assertEqual(old[key], unit[key])
            self.assertEqual(unit['structure_revision'], 2)
        for old in snap['scopes']:
            current = next(s for s in new['scopes'] if s['scope_id'] == old['scope_id'])
            self.assertEqual(current['unit_ids'], old['unit_ids'])
        self.assertEqual(len({u['content_ref']['file'] for u in new['units']}), 1)

    def test_packed_middle_scope_local_edit_and_rollback(self):
        self.fixture('## Alpha\n\nLEFT_ID is 1.\n\n## Beta\n\nMIDDLE_ID is 2.\n\n## Gamma\n\nRIGHT_ID is 3.\n')
        snap = self.initial(pdf=self.pdf)
        packed = self.publish_reading(snap)
        target = next(u for u in packed['units'] if u['kind'] == 'paragraph' and 'MIDDLE_ID' in u['text'])
        proposal = organization.propose(self.workspace, [{'unit_id': target['unit_id'], 'operation': 'replace',
            'after': 'MIDDLE_ID is 4.\n'}], 'test', 'Synthetic edit')
        organization.apply_proposal(self.workspace, proposal['proposal'], packed['revision'])
        edited = organization.snapshot(self.workspace)
        self.assertEqual([u['unit_id'] for old, u in zip(packed['units'], edited['units'])
                          if old['content_hash'] != u['content_hash']], [target['unit_id']])
        self.assertEqual(len({u['content_ref']['file'] for u in edited['units']}), 1)
        self.assertFalse(query.search(edited, 'LEFT_ID', scope=target['primary_scope_id'])['results'])
        self.assertEqual(query.search(edited, 'MIDDLE_ID', scope=target['primary_scope_id'])['result_count'], 1)
        self.assertFalse(query.search(edited, scope=target['primary_scope_id'], pages=(9, 9))['results'])
        with self.assertRaisesRegex(ValueError, 'Unknown Scope'):
            query.search(edited, scope='scope-does-not-exist')
        organization.rollback(self.workspace, packed['revision'], edited['revision'], 'Undo synthetic edit')
        restored = organization.snapshot(self.workspace)
        self.assertEqual(restored['reconstructed'], packed['reconstructed'])
        self.assertEqual([u['content_hash'] for u in restored['units']], [u['content_hash'] for u in packed['units']])
        self.assertEqual(restored['metadata']['reading'], packed['metadata']['reading'])
        self.assertEqual(organization.snapshot(self.workspace, edited['revision'])['reconstructed'], edited['reconstructed'])

    def test_packed_reexport_with_or_without_pdf_retains_composition_and_identity(self):
        packed = self.publish_reading(self.source_fixture())
        before = inventory(self.workspace)
        for name, pdf in (('reexport-source', self.pdf), ('reexport-recipe', None)):
            with self.subTest(name=name):
                output = self.root / name
                packing.export_reading(packed, output, pdf=pdf)
                view = packing.read_reading(output)
                self.assertEqual(len(view['scopes']), len(packed['scopes']))
                self.assertEqual(view['reconstructed'], packed['reconstructed'])
                self.assertEqual([(u['unit_id'], u['content_hash'], u['source_refs']) for u in view['units']],
                                 [(u['unit_id'], u['content_hash'], u['source_refs']) for u in packed['units']])
                self.assertEqual({s['scope_id']: s['unit_ids'] for s in view['scopes']},
                                 {s['scope_id']: s['unit_ids'] for s in packed['scopes']})
                text = ''.join(p.read_text() for p in (output / 'content').glob('*.md'))
                self.assertEqual(text.count('## 3.2 Functional range'), 1)
                self.assertNotIn('## Functional range', text)
        self.assertEqual(inventory(self.workspace), before)

    def test_reexport_rejects_incompatible_composite_identity(self):
        packed = self.publish_reading(self.source_fixture())
        evidence = packing.source_structure(packed, self.pdf)
        scopes = deepcopy(packed['scopes'])
        composite = next(s for s in scopes if s.get('composed_from_scope_ids'))
        composite['composed_from_scope_ids'] = list(reversed(composite['composed_from_scope_ids']))
        with self.assertRaisesRegex(ValueError, 'incompatible relationships'):
            packing.refined_scopes(deepcopy(packed['units']), scopes, evidence)

    def test_title_edit_invalidates_composition_without_stale_display(self):
        snap = self.source_fixture()
        packed = self.publish_reading(snap)
        target = next(u for u in packed['units'] if u.get('heading', {}).get('title') == 'Functional range')
        proposal = organization.propose(self.workspace, [{'unit_id': target['unit_id'], 'operation': 'replace',
            'after': '## Changed range\n'}], 'test', 'Synthetic title edit')
        organization.apply_proposal(self.workspace, proposal['proposal'], packed['revision'])
        edited = organization.snapshot(self.workspace)
        self.assertFalse(edited['metadata']['reading']['display'])
        text = ''.join(p.read_text() for p in (edited['root'] / 'content').glob('*.md'))
        self.assertIn('## Changed range', text)
        self.assertNotIn('## 3.2 Functional range', text)
        self.assertTrue(any(r['reason'] == 'STALE_SOURCE_STRUCTURE_AFTER_LOCAL_REVISION'
                            for r in edited['metadata']['reading']['structure']['headings']))
        output = self.root / 'edited-reading'
        packing.export_reading(edited, output)
        view = packing.read_reading(output)
        self.assertFalse(view['plan']['display'])
        self.assertTrue(all(g['scope_change'] == 'STALE_COMPOSITION_AFTER_LOCAL_REVISION'
                            for g in view['plan']['structure']['combinations']))
        self.assertEqual(view['reconstructed'], edited['reconstructed'])

    def test_structural_rollback_restores_legacy_layout_and_monotonic_structure_revisions(self):
        self.fixture()
        initial = self.initial(pdf=self.pdf)
        packed = self.publish_reading(initial)
        organization.rollback(self.workspace, initial['revision'], packed['revision'], 'Restore split layout')
        restored = organization.snapshot(self.workspace)
        self.assertNotIn('reading', restored['metadata'])
        self.assertEqual([u['content_ref']['file'] for u in restored['units']],
                         [u['content_ref']['file'] for u in initial['units']])
        self.assertTrue(all(u['structure_revision'] == 3 for u in restored['units']))
        self.assertTrue(all(u['revision'] == 1 for u in restored['units']))

    def test_stale_export_or_expected_revision_refused(self):
        self.fixture()
        snap = self.initial(pdf=self.pdf)
        packed = self.publish_reading(snap)
        head = (self.workspace / 'HEAD.json').read_bytes()
        for expected in (snap['revision'], packed['revision']):
            with self.subTest(expected=expected), self.assertRaises(ValueError):
                packing.repackage(self.workspace, self.root / 'reading', expected, 'Stale reuse')
        self.assertEqual((self.workspace / 'HEAD.json').read_bytes(), head)

    def test_repackage_failure_phases_preserve_head_and_allow_retry(self):
        self.fixture()
        snap = self.initial(pdf=self.pdf)
        view = self.exported(snap)
        for phase in ('content_written', 'indexes_written', 'manifest_written',
                      'validated_before_publish', 'revision_published_before_head'):
            with self.subTest(phase=phase):
                workspace = self.root / phase
                shutil.copytree(self.workspace, workspace)
                head = (workspace / 'HEAD.json').read_bytes()
                def fail(at):
                    if at == phase:
                        raise RuntimeError('Injected repackage failure')
                with patch.object(organization, '_checkpoint', side_effect=fail), self.assertRaises(RuntimeError):
                    packing.repackage(workspace, view['root'], snap['revision'], 'Fault injection')
                self.assertEqual((workspace / 'HEAD.json').read_bytes(), head)
                self.assertEqual(organization.snapshot(workspace)['reconstructed'], snap['reconstructed'])
                self.assertTrue(list((workspace / 'history').glob('failed-*.json')))
                packed = packing.repackage(workspace, view['root'], snap['revision'], 'Retry')
                self.assertEqual(organization.snapshot(workspace)['revision'], packed['revision'])

    def test_packed_edit_failure_keeps_readable_current_snapshot(self):
        snap = self.source_fixture()
        packed = self.publish_reading(snap)
        target = next(u for u in packed['units'] if u['kind'] == 'paragraph')
        proposal = organization.propose(self.workspace, [{'unit_id': target['unit_id'], 'operation': 'replace',
            'after': 'DATA_STATUS is 3.\n'}], 'test', 'Synthetic edit')
        head = (self.workspace / 'HEAD.json').read_bytes()
        def fail(phase):
            if phase == 'revision_published_before_head':
                raise RuntimeError('Injected packed edit failure')
        with patch.object(organization, '_checkpoint', side_effect=fail), self.assertRaises(RuntimeError):
            organization.apply_proposal(self.workspace, proposal['proposal'], packed['revision'])
        self.assertEqual((self.workspace / 'HEAD.json').read_bytes(), head)
        self.assertEqual(organization.snapshot(self.workspace)['reconstructed'], packed['reconstructed'])

    def test_repackage_writer_exclusion(self):
        self.fixture()
        snap = self.initial()
        view = self.exported(snap)
        with organization.writer(self.workspace):
            with self.assertRaisesRegex(ValueError, 'Another workspace writer'):
                packing.repackage(self.workspace, view['root'], snap['revision'], 'Concurrent request')

    def test_real_cli_repackage(self):
        self.fixture()
        snap = self.initial()
        view = self.exported(snap)
        script = Path(organization.__file__).resolve().parents[2] / 'scripts/pdf2md.py'
        run = subprocess.run([sys.executable, '-B', str(script), 'revise', 'repackage',
            '--workspace', str(self.workspace), '--reading-export', str(view['root']),
            '--expected-revision', snap['revision'], '--reason', 'CLI controlled packaging'],
            text=True, capture_output=True)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertIn('REPACKAGED', run.stdout)

    def test_plain_text_link_fast_path_does_not_keep_environment_state(self):
        text = 'Use [x][destination].'
        self.assertEqual(links._scan(text, {}), ([], []))
        refs, _ = links._scan(text, {'references': {'DESTINATION': {'href': '#target', 'title': ''}}})
        self.assertEqual([r['href'] for r in refs], ['#target'])
        self.assertEqual(links._scan(text, {}), ([], []))
        with patch.object(links, 'MarkdownIt', side_effect=AssertionError('No second parser needed')):
            self.assertEqual(links._scan('Plain text with `[x](#target)` example.', {}), ([], []))

    def test_packed_asset_tampering_is_detected_even_with_restored_mtime(self):
        self.fixture(self.text + '\n![plot](assets/plot.png)\n', assets={'assets/plot.png': 'red'})
        snap = self.initial()
        packed = self.publish_reading(snap)
        asset = packed['root'] / 'content/assets/plot.png'
        stat = asset.stat()
        payload = bytearray(asset.read_bytes())
        payload[-1] ^= 1
        asset.write_bytes(payload)
        os.utime(asset, ns=(stat.st_atime_ns, stat.st_mtime_ns))
        with self.assertRaisesRegex(ValueError, 'Sealed product bytes changed'):
            organization.snapshot(self.workspace)

    def test_packed_scope_reconstruction_rejects_consistently_resealed_wrong_membership(self):
        self.fixture()
        packed = self.publish_reading(self.initial())
        index_file = packed['root'] / 'index/scopes.json'
        data = read(index_file)
        data['scopes'][-1]['unit_ids'] = []
        save(index_file, data)
        manifest = read(packed['root'] / 'revision_manifest.json')
        manifest['artifacts'] = inventory(packed['root'])
        manifest['artifacts'].pop('revision_manifest.json')
        save(packed['root'] / 'revision_manifest.json', manifest)
        head = read(self.workspace / 'HEAD.json')
        head['manifest_sha256'] = sha(packed['root'] / 'revision_manifest.json')
        save(self.workspace / 'HEAD.json', head)
        with self.assertRaisesRegex(ValueError, 'Scope membership'):
            organization.snapshot(self.workspace)


if __name__ == '__main__':
    unittest.main()
