import copy
from pathlib import Path
import tempfile
import unittest

from pdf2md_p2.common import inventory, sha
from pdf2md_p2.organization_query import search, source_visibility, trace

try:
    import pymupdf
except ImportError:
    pymupdf = None


def unit(name, text, scope='root', pages=None, kind='paragraph'):
    return {'unit_id': name, 'revision': 1, 'kind': kind, 'text': text,
            'pages': [1] if pages is None else pages, 'source_refs': [],
            'assessment': {'location': 'UNMAPPED', 'structure': 'UNREVIEWED', 'content': 'NOT_JUDGED'},
            'primary_scope_id': scope, 'content_ref': {'file': 'content/part.md', 'anchor': name,
                                                    'start': 0, 'end': len(text), 'line_start': 1, 'line_end': 1},
            'legacy_refs': []}


def snapshot(units, **changes):
    result = {'workspace_id': 'workspace-test', 'document_id': 'document-test', 'revision': 'rev-000001',
              'root': Path('/temporary/workspace/revisions/rev-000001'), 'workspace': Path('/temporary/workspace'),
              'units': units, 'scopes': [{'scope_id': 'root', 'parent_id': None, 'title': 'Root', 'kind': 'VIRTUAL_SCOPE'},
                                        {'scope_id': 'a', 'parent_id': 'root', 'title': 'A', 'kind': 'EXPLICIT_STRUCTURE'},
                                        {'scope_id': 'b', 'parent_id': 'a', 'title': 'B', 'kind': 'INFERRED_STRUCTURE'},
                                        {'scope_id': 'other', 'parent_id': 'root', 'title': 'Other', 'kind': 'VIRTUAL_SCOPE'}],
              'references': [], 'source_map': {'input_sha256': None, 'pages': {}}}
    return result | changes


class ScopedSearchTests(unittest.TestCase):
    def test_exact_identifiers_keep_case_and_separators(self):
        text = 'DATA_STATUS DATA STATUS DATA-STATUS data_status XDATA_STATUS DATA_STATUS_EXTRA DATA_STATUS'
        data = snapshot([unit('u-one', text)])
        result = search(data, 'DATA_STATUS')
        row, = result['results']
        self.assertEqual([hit['text'] for hit in row['hits']], ['DATA_STATUS', 'DATA_STATUS'])
        self.assertEqual(row['text'], text)
        self.assertEqual(search(data, 'DATA STATUS')['results'][0]['hits'][0]['text'], 'DATA STATUS')
        self.assertEqual(len(search(data, 'DATA-STATUS')['results'][0]['hits']), 1)
        self.assertEqual(len(search(data, 'data_status')['results'][0]['hits']), 1)
        self.assertFalse(result['semantic_correctness_certified'])

    def test_substring_mode_is_explicit(self):
        result = search(snapshot([unit('u-one', 'XDATA_STATUS DATA_STATUS_EXTRA')]), 'DATA_STATUS', match='substring')
        self.assertEqual(len(result['results'][0]['hits']), 2)
        self.assertEqual(result['results'][0]['match_mode'], 'substring')

    def test_markdown_escape_is_serialization_not_identifier_variation(self):
        text = r'中文 😀 DATA\_STATUS; DATA STATUS; DATA-STATUS; DATA\_STATUS\_EXTRA'
        result = search(snapshot([unit('u-one', text)]), 'DATA_STATUS')
        hit, = result['results'][0]['hits']
        self.assertEqual(hit['text'], r'DATA\_STATUS')
        self.assertEqual(text[hit['start']:hit['end']], r'DATA\_STATUS')
        self.assertEqual(hit['match_basis'], 'markdown_escape_decoded_literal')

    def test_code_escapes_remain_literal_while_prose_escapes_decode(self):
        examples = [r'`DATA\_STATUS`', r'``DATA\_STATUS``',
                    '```text\nDATA\\_STATUS\n```\n', '~~~\nDATA\\_STATUS\n~~~\n',
                    '    DATA\\_STATUS\n', '> ```\n> DATA\\_STATUS\n> ```\n']
        for text in examples:
            with self.subTest(text=text):
                data = snapshot([unit('u-one', text)])
                self.assertEqual(search(data, 'DATA_STATUS')['results'], [])
                literal, = search(data, r'DATA\_STATUS')['results'][0]['hits']
                self.assertEqual(literal['text'], r'DATA\_STATUS')
                self.assertEqual(literal['match_basis'], 'literal')
        text = r'`DATA\_STATUS` followed by DATA\_STATUS.'
        hit, = search(snapshot([unit('u-one', text)]), 'DATA_STATUS')['results'][0]['hits']
        self.assertEqual(hit['start'], text.rindex(r'DATA\_STATUS'))
        self.assertEqual(text[hit['start']:hit['end']], r'DATA\_STATUS')

    def test_prose_escape_source_spans_survive_containers_and_unicode_separators(self):
        for text in ('> prefix\u2028DATA\\_STATUS\n> next\u0085line\n',
                     '| Name | Value |\r\n| --- | --- |\r\n| `DATA\\_STATUS` | DATA\\_STATUS |\r\n',
                     'prefix\u2029DATA\\_STATUS\rnext\u000bline\r'):
            with self.subTest(text=text):
                hit, = search(snapshot([unit('u-one', text)]), 'DATA_STATUS')['results'][0]['hits']
                self.assertEqual(hit['start'], text.rindex(r'DATA\_STATUS'))
                self.assertEqual(text[hit['start']:hit['end']], r'DATA\_STATUS')

    def test_loose_preserves_original_spans_and_is_only_candidate(self):
        text = '中文 😀 DATA_status; DATA  STATUS; data-status; STRASSE Straße.'
        result = search(snapshot([unit('u-one', text)]), 'DATA_STATUS', match='loose')
        row, = result['results']
        self.assertTrue(row['match_is_candidate'])
        self.assertEqual([hit['text'] for hit in row['hits']], ['DATA_status', 'DATA  STATUS', 'data-status'])
        for hit in row['hits']:
            self.assertEqual(text[hit['start']:hit['end']], hit['text'])
        folded = search(snapshot([unit('u-one', text)]), 'strasse', match='loose')
        self.assertEqual([hit['text'] for hit in folded['results'][0]['hits']], ['STRASSE', 'Straße'])

    def test_scope_descendants_and_page_intersection_before_matching(self):
        units = [unit('u-one', 'VALUE', 'a', [1]), unit('u-two', 'VALUE', 'b', [2]),
                 unit('u-three', 'VALUE', 'other', [2]), unit('u-four', 'VALUE', 'b', [3])]
        result = search(snapshot(units), 'VALUE', scope='a', pages=(2, 2))
        self.assertEqual([row['unit_id'] for row in result['results']], ['u-two'])
        self.assertEqual(result['allowed_unit_count'], 1)
        self.assertEqual([row['scope_id'] for row in result['results'][0]['scope_path']], ['root', 'a', 'b'])

    def test_scope_declared_members_are_searchable_without_moving_primary_scope(self):
        data = snapshot([unit('u-one', 'VALUE', 'other')])
        data['scopes'][1]['unit_ids'] = ['u-one']
        self.assertEqual(search(data, 'VALUE', scope='a')['result_count'], 1)

    def test_cross_page_default_containment_and_explicit_intersection(self):
        text = '<table><tr><td>VALUE</td></tr><tr><td>20</td></tr></table>'
        data = snapshot([unit('u-one', text, pages=[2, 3], kind='table')])
        self.assertEqual(search(data, 'VALUE', pages=(2, 2))['results'], [])
        result = search(data, 'VALUE', pages=(2, 2), page_mode='intersect')['results'][0]
        self.assertEqual(result['outside_pages'], [3])
        self.assertTrue(result['crosses_page_boundary'])
        self.assertEqual(result['text'], text)
        self.assertEqual(search(data, 'VALUE', pages=(2, 3))['result_count'], 1)

    def test_unknown_pages_are_not_admitted_to_page_filters(self):
        data = snapshot([unit('u-one', 'VALUE', pages=[])])
        self.assertEqual(search(data, 'VALUE')['result_count'], 1)
        for mode in ('contained', 'intersect'):
            self.assertEqual(search(data, 'VALUE', pages=(1, 100), page_mode=mode)['result_count'], 0)

    def test_unknown_scope_or_unit_never_broadens_query(self):
        data = snapshot([unit('u-one', 'VALUE')])
        for arguments in ({'scope': 'missing'}, {'unit_id': 'missing'}):
            with self.subTest(arguments=arguments), self.assertRaisesRegex(ValueError, 'Unknown'):
                search(data, 'VALUE', **arguments)

    def test_unit_and_kind_filters_also_intersect_scope(self):
        data = snapshot([unit('u-one', 'VALUE', 'a', kind='table')])
        self.assertEqual(search(data, unit_id='u-one')['result_count'], 1)
        self.assertEqual(search(data, unit_id='u-one', kind='formula')['result_count'], 0)
        self.assertEqual(search(data, unit_id='u-one', scope='other')['result_count'], 0)

    def test_outside_context_requires_expansion_and_is_flagged(self):
        data = snapshot([unit('u-one', 'VALUE', 'a', [1]), unit('u-two', 'Outside confidential condition', 'other', [3])],
                        references=[{'type': 'context_dependency', 'source': 'u-one', 'target': 'u-two',
                                     'status': 'SOURCE_LINKED', 'depends_on_revision': {'u-two': 1}}])
        first = search(data, 'VALUE', scope='a', pages=(1, 1))['results'][0]
        context, = first['related_context']
        self.assertTrue(context['outside_requested_range'])
        self.assertFalse(context['expanded'])
        self.assertNotIn('unit', context)
        self.assertNotIn('Outside confidential condition', str(first))
        expanded = search(data, 'VALUE', scope='a', pages=(1, 1), expand_context=True)['results'][0]['related_context'][0]
        self.assertTrue(expanded['expanded'])
        self.assertTrue(expanded['outside_requested_range'])
        self.assertEqual(expanded['unit']['text'], 'Outside confidential condition')
        self.assertEqual(expanded['outside_pages'], [3])

    def test_multiple_reference_targets_remain_candidates(self):
        data = snapshot([unit('u-one', 'See Table 1'), unit('u-two', 'Table 1'), unit('u-three', 'Table 1')],
                        references=[{'type': 'explicit_reference', 'source': 'u-one', 'target': None,
                                     'candidates': ['u-two', 'u-three'], 'status': 'AMBIGUOUS'}])
        row = search(data, unit_id='u-one')['results'][0]
        self.assertEqual(row['references'][0]['candidates'], ['u-two', 'u-three'])
        self.assertEqual(len(row['related_context']), 2)

    def test_invalid_queries_and_ranges_are_rejected(self):
        data = snapshot([unit('u-one', 'VALUE')])
        for arguments in ({'query': ''}, {'query': '   '}, {'query': '_ -', 'match': 'loose'},
                          {'match': 'unknown'}, {'page_mode': 'unknown'}, {'pages': (0, 3)},
                          {'pages': (3, 2)}, {'pages': (1, True)}, {'pages': '1:3'}):
            with self.subTest(arguments=arguments), self.assertRaises(ValueError):
                search(data, **arguments)

    def test_numeric_exact_does_not_claim_decimal_components(self):
        data = snapshot([unit('u-one', '5 5.1 0.5 15 -5 +5')])
        self.assertEqual([hit['text'] for hit in search(data, '5')['results'][0]['hits']], ['5'])
        self.assertEqual(search(data, '-5')['results'][0]['hits'][0]['text'], '-5')

    def test_canonical_snapshot_and_previous_results_are_not_mutated(self):
        data = snapshot([unit('u-one', 'VALUE')])
        before = copy.deepcopy(data)
        result = search(data, 'VALUE')
        result['results'][0]['assessment']['content'] = 'CHANGED'
        result['results'][0]['source_refs'].append({'page': 99})
        self.assertEqual(data, before)
        self.assertEqual(search(data, 'VALUE')['revision'], 'rev-000001')

    def test_no_pdf_trace_preserves_all_candidates_and_location_status(self):
        item = unit('u-one', 'Wrong output value 99')
        item['assessment']['location'] = 'AMBIGUOUS'
        item['source_refs'] = [{'page': 1, 'bbox': [10, 10, 20, 20], 'source_text': 'Value 1'},
                               {'page': 1, 'bbox': [30, 30, 40, 40], 'source_text': 'Value 1'}]
        result = trace(snapshot([item]), 'u-one')
        self.assertEqual(result['source_refs'], item['source_refs'])
        self.assertEqual(result['unit']['assessment']['location'], 'AMBIGUOUS')
        self.assertFalse(result['source_pdf_checked'])
        self.assertFalse(result['semantic_correctness_certified'])

    def test_unmapped_trace_is_explicit_without_inventing_source(self):
        result = trace(snapshot([unit('u-one', 'No source')]), 'u-one')
        self.assertEqual(result['source_refs'], [])
        self.assertEqual(result['unit']['assessment']['location'], 'UNMAPPED')


@unittest.skipIf(pymupdf is None, 'Source rendering requires the prepared PyMuPDF environment')
class SourceEvidenceTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.pdf = self.root / 'original.pdf'
        document = pymupdf.open()
        page = document.new_page()
        page.insert_text((50, 80), 'VALUE 1')
        page.insert_text((50, 180), 'UNLINKED 2')
        page.set_rotation(90)
        document.new_page()
        document.save(self.pdf)
        document.close()
        item = unit('u-one', 'VALUE 99')
        item['source_refs'] = [{'page': 1, 'bbox': [45, 60, 150, 90], 'basis': 'GPU_NATIVE_LAYOUT_OBJECT',
                                'source_text': 'VALUE 1'}]
        item['assessment']['location'] = 'ASSOCIATED_NATIVE_LAYOUT'
        self.mapping = {'input_sha256': sha(self.pdf), 'pages': {
            '1': {'width': 595.0, 'height': 842.0, 'rotation': 90,
                  'coordinate_system': 'unrotated_page_top_left_pdf_points'},
            '2': {'width': 595.0, 'height': 842.0, 'rotation': 0,
                  'coordinate_system': 'unrotated_page_top_left_pdf_points'}}, 'units': [item]}
        self.workspace = self.root / 'workspace'
        self.workspace.mkdir()
        (self.workspace / 'sentinel').write_text('unchanged', encoding='utf-8')
        self.input_root = self.root / 'frozen-input'
        self.input_root.mkdir()
        self.data = snapshot([item], source_map=self.mapping, workspace=self.workspace,
                             root=self.workspace / 'revisions/rev-000001', input_root=self.input_root)

    def test_real_rotated_crop_keeps_wrong_output_and_source_distinct(self):
        before = inventory(self.workspace)
        result = trace(self.data, 'u-one', pdf=self.pdf, render_dir=self.root / 'evidence')
        self.assertEqual(result['unit']['text'], 'VALUE 99')
        self.assertIn('VALUE 1', result['source_checks'][0]['source_text'])
        self.assertFalse(result['semantic_correctness_certified'])
        self.assertEqual(inventory(self.workspace), before)
        picture = self.root / 'evidence' / result['evidence'][0]['path']
        bitmap = pymupdf.Pixmap(picture)
        self.assertGreater(bitmap.width, bitmap.height)
        self.assertGreater(len(set(bitmap.samples)), 1)

    def test_duplicate_candidates_on_rotated_page_are_all_rendered(self):
        self.data['units'][0]['source_refs'].append(copy.deepcopy(self.data['units'][0]['source_refs'][0]))
        self.data['units'][0]['assessment']['location'] = 'AMBIGUOUS'
        result = trace(self.data, 'u-one', pdf=self.pdf, render_dir=self.root / 'duplicate-evidence')
        self.assertEqual(len(result['source_refs']), 2)
        self.assertEqual(len(result['evidence']), 2)
        self.assertTrue(all(row['checked'] for row in result['source_checks']))
        self.assertEqual(result['unit']['assessment']['location'], 'AMBIGUOUS')

    def test_hash_mismatch_is_rejected_even_without_rendering(self):
        wrong = self.root / 'wrong.pdf'
        wrong.write_bytes(b'not the imported PDF')
        for operation in (lambda: trace(self.data, 'u-one', pdf=wrong),
                          lambda: source_visibility(wrong, self.mapping)):
            with self.assertRaisesRegex(ValueError, 'SHA256'):
                operation()

    def test_render_requires_pdf_and_new_external_directory(self):
        with self.assertRaisesRegex(ValueError, 'exact source PDF'):
            trace(self.data, 'u-one', render_dir=self.root / 'no-pdf')
        for destination in (self.workspace / 'evidence', self.input_root / 'evidence', self.root, self.workspace):
            with self.subTest(destination=destination), self.assertRaises(ValueError):
                trace(self.data, 'u-one', pdf=self.pdf, render_dir=destination)
        existing = self.root / 'existing'
        existing.mkdir()
        with self.assertRaisesRegex(ValueError, 'exists'):
            trace(self.data, 'u-one', pdf=self.pdf, render_dir=existing)

    def test_invalid_region_is_reported_not_clamped_or_labeled_checked(self):
        self.data['units'][0]['source_refs'][0]['bbox'] = [-5, 10, 50, 90]
        result = trace(self.data, 'u-one', pdf=self.pdf, render_dir=self.root / 'invalid-evidence')
        self.assertEqual(result['evidence'], [])
        self.assertFalse(result['source_checks'][0]['checked'])
        self.assertEqual(result['source_checks'][0]['reason'], 'SOURCE_REGION_UNAVAILABLE')
        self.assertEqual(result['source_refs'][0]['bbox'][0], -5)

    def test_conflicting_coordinate_system_does_not_render(self):
        self.mapping['pages']['1']['coordinate_system'] = 'rotated_pixels'
        result = trace(self.data, 'u-one', pdf=self.pdf)
        self.assertEqual(result['source_checks'][0]['reason'], 'SOURCE_GEOMETRY_CONFLICT')

    def test_visibility_distinguishes_linked_unmapped_and_unchecked(self):
        report = source_visibility(self.pdf, self.mapping)
        self.assertEqual(report['categories']['linked'], 1)
        self.assertEqual(report['categories']['candidate_unmapped'], 1)
        self.assertEqual(report['categories']['unchecked'], 1)
        self.assertFalse(report['semantic_coverage_measured'])
        self.assertFalse(report['semantic_correctness_certified'])

    def test_visibility_ambiguous_association_is_not_linked(self):
        self.mapping['units'][0]['assessment']['location'] = 'AMBIGUOUS'
        report = source_visibility(self.pdf, self.mapping)
        self.assertEqual(report['categories']['linked'], 0)
        self.assertEqual(report['categories']['partial_or_conflict'], 1)

    def test_visibility_legacy_mapping_schema_is_supported(self):
        mapping = copy.deepcopy(self.mapping)
        item = mapping.pop('units')[0]
        mapping['objects'] = [{'id': item['unit_id'], 'sources': item['source_refs'], 'status': 'ASSOCIATED_NATIVE_LAYOUT'}]
        self.assertEqual(source_visibility(self.pdf, mapping)['categories']['linked'], 1)


if __name__ == '__main__':
    unittest.main()
