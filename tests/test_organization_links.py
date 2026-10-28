from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from urllib.parse import unquote, urlsplit

from markdown_it import MarkdownIt
from PIL import Image

from pdf2md_p2 import organization
from pdf2md_p2.assets import references
from pdf2md_p2.common import digest, inventory, save, sha
from pdf2md_p2.organization_links import refresh_scopes, render_files, render_single, restore, validate_links


def scope(name, heading=None, title=None):
    item = {'scope_id': name, 'parent_id': None, 'title': title or name, 'type': 'EXPLICIT_STRUCTURE', 'level': 1}
    if heading:
        item['heading_unit_id'] = heading
    return item


def unit(name, text, scope_id='one', heading=None):
    item = {'unit_id': name, 'revision': 1, 'text': text, 'kind': 'paragraph', 'pages': [1],
            'primary_scope_id': scope_id, 'content_hash': digest(text)}
    if heading:
        item['kind'] = 'heading'
        item['heading'] = {'title': heading, 'level': 1, 'type': 'EXPLICIT_STRUCTURE', 'basis': 'MARKDOWN_HEADING'}
    return item


class OrganizationLinksTests(unittest.TestCase):
    def rendered(self, files, item):
        ref = item['content_ref']
        return files[ref['file']][ref['start']:ref['end']]

    def assert_restored(self, files, units):
        for item in units:
            self.assertEqual(restore(self.rendered(files, item), item['transforms']), item['text'])

    def test_cross_file_markdown_and_html_links_use_stable_anchors(self):
        units = [unit('u-link', '[Jump](#target) and <a class="x" href="#target" title="keep">HTML</a>.\n'),
                 unit('u-target', '# Target\n', 'two', 'Target')]
        files, refs, _ = render_files(units, [scope('one'), scope('two', 'u-target')])
        text = self.rendered(files, units[0])
        self.assertIn('[Jump](two.md#u-target)', text)
        self.assertIn('<a class="x" href="two.md#u-target" title="keep">HTML</a>', text)
        self.assertEqual([ref['status'] for ref in refs], ['RESOLVED', 'RESOLVED'])
        self.assert_restored(files, units)

    def test_html_block_href_unquoted_single_quoted_and_entity_anchor(self):
        units = [unit('u-link', '<div>\n<a href=\'#named&amp;anchor\'>First</a>\n<a href=#named%26anchor>Second</a>\n</div>\n'),
                 unit('u-target', '<p id="named&amp;anchor">Original body</p>\n', 'two')]
        files, refs, _ = render_files(units, [scope('one'), scope('two')])
        self.assertEqual([ref['target'] for ref in refs], ['u-target', 'u-target'])
        self.assertIn("href='two.md#u-target'", self.rendered(files, units[0]))
        self.assertIn('href=two.md#u-target', self.rendered(files, units[0]))
        self.assert_restored(files, units)

    def test_reference_images_keep_document_wide_definitions_after_split(self):
        units = [unit('u-img', '![Diagram][picture]\n\n'),
                 unit('u-def', '[picture]: <assets/figure(a).png> "Figure label"\n', 'two')]
        files, refs, generated = render_files(units, [scope('one'), scope('two')])
        self.assertEqual(references(files['content/one.md']), ['assets/figure(a).png'])
        self.assertIn('Generated reference definitions', generated['reference_definitions'])
        self.assert_restored(files, units)
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            (root / 'content/assets').mkdir(parents=True)
            (root / 'content/assets/figure(a).png').write_bytes(b'asset bytes retained')
            validate_links(root, files, refs)

    def test_reference_links_with_definitions_in_other_file_relocate(self):
        units = [unit('u-link', '[Read details][destination]\n'), unit('u-target', '# Target\n', 'two', 'Target'),
                 unit('u-definition', '[destination]: #target\n', 'two')]
        files, refs, _ = render_files(units, [scope('one'), scope('two')])
        self.assertEqual(len(refs), 1)
        self.assertEqual(refs[0]['target'], 'u-target')
        self.assertIn('[Read details](two.md#u-target)', self.rendered(files, units[0]))
        self.assert_restored(files, units)

    def test_reference_image_and_reference_link_can_share_label(self):
        units = [unit('u-both', '![Diagram][asset]\n\n[Download][asset]\n\n'),
                 unit('u-definition', '[asset]: assets/file.png\n', 'two')]
        files, refs, _ = render_files(units, [scope('one'), scope('two')])
        self.assertEqual(references(files['content/one.md']), ['assets/file.png'])
        self.assertEqual([ref['status'] for ref in refs], ['LOCAL_ASSET'])
        self.assert_restored(files, units)

    def test_duplicate_heading_is_ambiguous_not_first_candidate(self):
        units = [unit('u-link', '[Jump](#target)\n'), unit('u-target', '# Target\n', 'two', 'Target'),
                 unit('u-other', '# Target\n', 'three', 'Target')]
        files, refs, _ = render_files(units, [scope('one'), scope('two'), scope('three')])
        self.assertEqual(refs[0]['status'], 'AMBIGUOUS')
        self.assertIsNone(refs[0]['target'])
        self.assertEqual(refs[0]['candidates'], ['u-target', 'u-other'])
        self.assertIn('../README.md#u-link-reference-1', self.rendered(files, units[0]))

    def test_heading_markup_slug_uses_visible_text(self):
        units = [unit('u-link', '[Jump](#target-name)\n'),
                 unit('u-target', '# Target **Name**\n', 'two', 'Target **Name**')]
        _, refs, _ = render_files(units, [scope('one'), scope('two')])
        self.assertEqual(refs[0]['target'], 'u-target')

    def test_missing_anchor_retains_unresolved_relation(self):
        units = [unit('u-link', '[Jump](#unknown)\n')]
        files, refs, _ = render_files(units, [scope('one')])
        self.assertEqual(refs[0]['status'], 'UNRESOLVED')
        self.assertEqual(refs[0]['candidates'], [])
        self.assert_restored(files, units)

    def test_fenced_and_inline_code_are_not_links_or_anchor_targets(self):
        text = '```html\n<a id="fake" href="#target">example</a>\n[Jump](#target)\n```\n\n'
        text += '`[Jump](#target)` [Jump](#target) `<a id="fake2">` [Missing](#fake2)\n'
        units = [unit('u-link', text), unit('u-target', '# Target\n', 'two', 'Target')]
        files, refs, _ = render_files(units, [scope('one'), scope('two')])
        rendered = self.rendered(files, units[0])
        self.assertIn('`[Jump](#target)` [Jump](two.md#u-target)', rendered)
        self.assertIn('```html\n<a id="fake" href="#target">example</a>', rendered)
        self.assertEqual(len(refs), 2)
        self.assertEqual(refs[1]['status'], 'UNRESOLVED')
        self.assert_restored(files, units)

    def test_html_code_examples_do_not_create_links_or_anchors(self):
        units = [unit('u-code', '<pre><code><a id="fake" href="#target">example</a></code></pre>\n'),
                 unit('u-inline', 'Inline <code><a id="fake2" href="#target">x</a></code> [Jump](#fake2)\n')]
        files, refs, _ = render_files(units, [scope('one')])
        self.assertEqual(len(refs), 1)
        self.assertEqual(refs[0]['status'], 'UNRESOLVED')
        self.assert_restored(files, units)

    def test_identical_links_in_distinct_table_cells_keep_correct_spans(self):
        units = [unit('u-table', '| A | B |\n|---|---|\n| `[x](#target)` | [x](#target) |\n| [x](#target) | [x](#target) |\n'),
                 unit('u-target', '# Target\n', 'two', 'Target')]
        files, refs, _ = render_files(units, [scope('one'), scope('two')])
        self.assertEqual(len(refs), 3)
        rendered = self.rendered(files, units[0])
        self.assertIn('`[x](#target)`', rendered)
        self.assertEqual(rendered.count('[x](two.md#u-target)'), 3)
        self.assert_restored(files, units)

    def test_nested_destination_escaped_label_and_container_prefixes(self):
        units = [unit('u-link', '> Intro `[x](#target)`\n> [label \\[x\\]](#target "title") and [file](assets/file\\(x\\).png)\n'),
                 unit('u-target', '# Target\n', 'two', 'Target')]
        files, refs, _ = render_files(units, [scope('one'), scope('two')])
        self.assertEqual(len(refs), 2)
        self.assertEqual(refs[0]['status'], 'RESOLVED')
        self.assertEqual(refs[1]['asset_url'], 'assets/file(x).png')
        self.assert_restored(files, units)

    def test_escaped_table_pipe_does_not_break_inline_source_ranges(self):
        units = [unit('u-table', '| A | B |\n|---|---|\n| A\\|B | [x](#target) |\n'),
                 unit('u-target', '# Target\n', 'two', 'Target')]
        files, refs, _ = render_files(units, [scope('one'), scope('two')])
        self.assertEqual(len(refs), 1)
        self.assertIn('A\\|B', self.rendered(files, units[0]))
        self.assertIn('[x](two.md#u-target)', self.rendered(files, units[0]))
        self.assert_restored(files, units)

    def test_target_rename_preserves_prior_resolved_identity_and_refreshes_scope_title(self):
        units = [unit('u-link', '[Jump](#target)\n'), unit('u-target', '# Target\n', 'two', 'Target')]
        scopes = [scope('one'), scope('two', 'u-target', 'Target')]
        _, refs, _ = render_files(units, scopes)
        units[1]['text'] = '# Renamed [Title]\n'
        units[1]['revision'] = 2
        units[1]['content_hash'] = digest(units[1]['text'])
        files, changed, _ = render_files(units, scopes, refs)
        self.assertEqual(changed[0]['target'], 'u-target')
        self.assertEqual(changed[0]['basis'], 'RETAINED_STABLE_REFERENCE_IDENTITY')
        self.assertEqual(changed[0]['target_revision'], 2)
        self.assertEqual(scopes[1]['title'], 'Renamed [Title]')
        self.assert_restored(files, units)

    def test_changed_reference_source_does_not_inherit_previous_resolution(self):
        units = [unit('u-link', '[Jump](#target)\n'), unit('u-target', '# Target\n', 'two', 'Target')]
        scopes = [scope('one'), scope('two', 'u-target')]
        _, refs, _ = render_files(units, scopes)
        units[0]['text'] = 'Changed source [Jump](#target)\n'
        units[1]['text'] = '# Renamed\n'
        _, changed, _ = render_files(units, scopes, refs)
        self.assertEqual(changed[0]['status'], 'UNRESOLVED')
        self.assertEqual(changed[0]['source_content_hash'], digest(units[0]['text']))

    def test_move_updates_file_link_without_changing_target_identity(self):
        units = [unit('u-link', '[Jump](#target)\n'), unit('u-target', '# Target\n', 'two', 'Target')]
        scopes = [scope('one'), scope('two'), scope('three')]
        _, refs, _ = render_files(units, scopes)
        units[1]['primary_scope_id'] = 'three'
        files, changed, _ = render_files(units, scopes, refs)
        self.assertIn('three.md#u-target', self.rendered(files, units[0]))
        self.assertEqual(changed[0]['target'], 'u-target')
        self.assertEqual(scopes[2]['direct_unit_ids'], ['u-target'])

    def test_single_export_has_stable_anchors_and_preserves_canonical_metadata(self):
        units = [unit('u-link', '[Jump](#target) [Missing](#unknown)\n'), unit('u-target', '# Target\n', 'two', 'Target')]
        scopes = [scope('one'), scope('two')]
        _, refs, _ = render_files(units, scopes)
        before = deepcopy((units, scopes))
        text, exported, _ = render_single(units, scopes, refs)
        self.assertIn('<a id="u-target"></a>', text)
        self.assertIn('[Jump](manual.md#u-target)', text)
        self.assertIn('(README.md#u-link-reference-2)', text)
        self.assertEqual((units, scopes), before)
        self.assertEqual(exported[0]['target'], 'u-target')

    def test_unsafe_or_unsupported_local_links_are_rejected(self):
        for href in ('../other.md#target', 'assets/../../outside.png', 'assets/%2e%2e/outside.png',
                     '/absolute.md#target', 'other.md#target', 'C:/secret.txt', 'file:///etc/passwd'):
            with self.subTest(href=href), self.assertRaises(ValueError):
                render_files([unit('u-link', '<a href="' + href + '">x</a>\n')], [scope('one')])

    def test_linked_asset_and_image_missing_paths_fail_validation(self):
        for text in ('[Download](assets/missing.png)\n', '![Image](assets/missing.png)\n'):
            units = [unit('u-link', text)]
            files, refs, _ = render_files(units, [scope('one')])
            with tempfile.TemporaryDirectory() as name, self.assertRaisesRegex(ValueError, 'Missing organized'):
                validate_links(Path(name), files, refs)

    def test_external_links_remain_byte_identical_and_are_not_fetched(self):
        text = '[Site](https://example.invalid/manual?q=1#tag) <a href="https://example.invalid">site</a>\n'
        units = [unit('u-link', text)]
        files, refs, _ = render_files(units, [scope('one')])
        self.assertEqual(self.rendered(files, units[0]), text)
        self.assertEqual(refs, [])

    def test_tampered_transforms_are_rejected(self):
        units = [unit('u-link', '[Jump](#target)\n'), unit('u-target', '# Target\n', 'two', 'Target')]
        files, _, _ = render_files(units, [scope('one'), scope('two')])
        with self.assertRaisesRegex(ValueError, 'differs'):
            restore(self.rendered(files, units[0]).replace('two.md', 'bad.md'), units[0]['transforms'])

    def test_duplicate_href_and_unknown_or_cyclic_scope_are_rejected(self):
        with self.assertRaisesRegex(ValueError, 'Duplicate HTML href'):
            render_files([unit('u-link', '<a href="#x" href="#y">bad</a>\n')], [scope('one')])
        for scopes in ([scope('one') | {'parent_id': 'missing'}],
                       [scope('one') | {'parent_id': 'two'}, scope('two') | {'parent_id': 'one'}]):
            with self.assertRaises(ValueError):
                refresh_scopes([], scopes)

    def test_rendered_markdown_link_targets_parse_as_expected(self):
        units = [unit('u-link', '[**Important** [nested label]](#target)\n'),
                 unit('u-target', '# Target\n', 'two', 'Target')]
        files, _, _ = render_files(units, [scope('one'), scope('two')])
        hrefs = [child.attrGet('href') for token in MarkdownIt('default').parse(files['content/one.md'])
                 for child in token.children or [] if child.type == 'link_open']
        self.assertEqual(hrefs, ['two.md#u-target'])
        self.assert_restored(files, units)

    def test_validate_checks_target_file_not_only_anchor_anywhere(self):
        units = [unit('u-link', '[Jump](#target)\n'), unit('u-target', '# Target\n', 'two', 'Target')]
        files, refs, _ = render_files(units, [scope('one'), scope('two')])
        refs[0]['target_file'] = 'content/missing.md'
        with tempfile.TemporaryDirectory() as name, self.assertRaisesRegex(ValueError, 'Broken stable'):
            validate_links(Path(name), files, refs)

    def test_unresolved_reference_needs_actual_navigation_destination(self):
        files, refs, _ = render_files([unit('u-link', '[Jump](#missing)\n')], [scope('one')])
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            with self.assertRaisesRegex(ValueError, 'navigation destination'):
                validate_links(root, files, refs)
            (root / 'README.md').write_text('<a id="u-link-reference-1"></a>\n\nUnresolved.\n', encoding='utf-8')
            validate_links(root, files, refs)

    def test_unicode_separators_are_not_markdown_or_html_newlines(self):
        for newline in ('\n', '\r\n', '\r'):
            text = newline.join(('Prefix\u2028text\u0085more\u2029words [Jump](#target)',
                                 '', '<div>\u2028text', '<a href="#target">HTML</a>', '</div>', ''))
            units = [unit('u-link', text), unit('u-target', '# Target' + newline, 'two', 'Target')]
            with self.subTest(newline=repr(newline)):
                files, refs, _ = render_files(units, [scope('one'), scope('two')])
                self.assertEqual([ref['target'] for ref in refs], ['u-target', 'u-target'])
                self.assertEqual(self.rendered(files, units[0]).count('two.md#u-target'), 2)
                self.assert_restored(files, units)


class OrganizationLinkIntegrationTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.input = self.root / 'source-result'
        self.input.mkdir()
        self.workspace = self.root / 'workspace'

    def seal(self, text):
        (self.input / 'manual.md').write_text(text, encoding='utf-8')
        save(self.input / 'quality_report.json', {'status': 'SUCCESS', 'expected_pages': 1, 'completed_pages': [1]})
        save(self.input / 'run_manifest.json', {'configuration': {'version': '0.1.0a2', 'input_sha256': '0' * 64},
                                              'artifacts': inventory(self.input)})

    def test_target_rename_then_single_export_has_real_resolvable_stable_hrefs(self):
        self.seal('<!-- PAGE 1 -->\n\n# Start\n\n[Go to target](#target)\n\n# Target\n\nOriginal text.\n')
        original_bytes = inventory(self.input)
        organization.organize(self.input, self.workspace, soft_chars=100)
        before = organization.snapshot(self.workspace)
        target = next(item for item in before['units'] if item.get('heading', {}).get('title') == 'Target')
        proposal = organization.propose(self.workspace,
            [{'unit_id': target['unit_id'], 'operation': 'replace', 'after': '# Renamed Target\n'}],
            proposer='integration-test', reason='Exercise versioned heading metadata')
        applied = organization.apply_proposal(self.workspace, Path(proposal['proposal']), before['revision'])
        after = organization.snapshot(self.workspace)
        relation = next(ref for ref in after['references'] if ref['type'] == 'explicit_reference')
        self.assertEqual(relation['target'], target['unit_id'])
        self.assertEqual(relation['basis'], 'RETAINED_STABLE_REFERENCE_IDENTITY')
        self.assertEqual(next(s for s in after['scopes'] if s.get('heading_unit_id') == target['unit_id'])['title'],
                         'Renamed Target')
        output = self.root / 'single-export'
        result = organization.export(self.workspace, output, format='single')
        self.assertEqual(result['revision'], applied['revision'])
        markdown = (output / 'manual.md').read_text(encoding='utf-8')
        hrefs = [child.attrGet('href') for token in MarkdownIt('default').parse(markdown)
                 for child in token.children or [] if child.type == 'link_open']
        self.assertEqual(hrefs, ['manual.md#' + target['unit_id']])
        for href in hrefs:
            location = urlsplit(href)
            destination = output / unquote(location.path)
            self.assertTrue(destination.is_file())
            self.assertIn('<a id="' + unquote(location.fragment) + '"></a>', destination.read_text(encoding='utf-8'))
        self.assertEqual(inventory(self.input), original_bytes)

    def test_percent_encoded_image_and_download_only_asset_are_copied_unchanged(self):
        (self.input / 'assets').mkdir()
        picture = self.input / 'assets/picture space.png'
        Image.new('RGB', (4, 6), 'green').save(picture)
        document = self.input / 'assets/download only.txt'
        document.write_bytes(b'original asset data')
        text = '<!-- PAGE 1 -->\n\n# Assets\n\n![Image](assets/picture%20space.png)\n\n[Download](assets/download%20only.txt)\n'
        self.seal(text)
        original_bytes = inventory(self.input)
        organization.organize(self.input, self.workspace)
        snapshot = organization.snapshot(self.workspace)
        for original in (picture, document):
            copied = snapshot['root'] / 'content/assets' / original.name
            self.assertTrue(copied.is_file())
            self.assertEqual(sha(copied), sha(original))
        self.assertEqual(snapshot['reconstructed'], text)
        organization.verify_workspace(self.workspace)
        output = self.root / 'split-export'
        organization.export(self.workspace, output, format='split')
        self.assertEqual((output / 'content/assets/download only.txt').read_bytes(), b'original asset data')
        self.assertEqual(inventory(self.input), original_bytes)

    def test_real_workspace_preserves_unicode_line_separators_and_links(self):
        text = ('<!-- PAGE 1 -->\r\n\r\n# Start\r\n\r\n'
                'Prefix\u2028line\u0085another\u2029text [Target](#target).\r\n\r\n'
                '# Target\r\n\r\nFinal\u000btext\u000cstill one paragraph.\r\n')
        self.seal(text)
        original_bytes = inventory(self.input)
        organization.organize(self.input, self.workspace)
        snapshot = organization.snapshot(self.workspace)
        self.assertEqual(snapshot['reconstructed'], text)
        reference, = [ref for ref in snapshot['references'] if ref['type'] == 'explicit_reference']
        self.assertEqual(reference['status'], 'RESOLVED')
        organization.verify_workspace(self.workspace)
        output = self.root / 'unicode-export'
        organization.export(self.workspace, output, format='single')
        rendered = (output / 'manual.md').read_bytes().decode('utf-8')
        self.assertIn('Prefix\u2028line\u0085another\u2029text', rendered)
        self.assertIn('manual.md#' + reference['target'], rendered)
        self.assertEqual(inventory(self.input), original_bytes)


if __name__ == '__main__':
    unittest.main()
