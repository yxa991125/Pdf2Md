import copy
import re
import unittest

from pdf2md_p2.organization_structure import organize_scopes, partition, revised_heading


def mapped(text, fragments):
    objects = []
    for index, (excerpt, kind, pages, status) in enumerate(fragments):
        start = text.index(excerpt)
        objects.append({'id': f'old-{index}', 'start': start, 'end': start + len(excerpt),
                        'text': excerpt, 'kind': kind, 'page': pages[0], 'status': status,
                        'sources': [{'page': page, 'bbox': [1, 2, 3, 4],
                                     'source_text': 'Source ' + excerpt, 'basis': 'GPU_NATIVE_LAYOUT_OBJECT'}
                                    for page in pages] if status != 'UNMAPPED' else []})
    return {'input_sha256': 'a' * 64, 'offset_unit': 'unicode_codepoint', 'objects': objects,
            'pages': {str(page): {'width': 100, 'height': 200, 'rotation': 90,
                                 'coordinate_system': 'unrotated_page_top_left_pdf_points'}
                      for row in objects for page in [source['page'] for source in row['sources']]}}


class OrganizationStructureTests(unittest.TestCase):
    def preserve(self, text, mapping=None):
        units = partition(text, mapping)
        self.assertEqual(''.join(unit['text'] for unit in units), text)
        cursor = 0
        for index, unit in enumerate(units):
            self.assertEqual(unit['start'], cursor)
            self.assertEqual(unit['text'], text[unit['start']:unit['end']])
            self.assertEqual(unit['order'], index)
            self.assertEqual(unit['revision'], 1)
            self.assertRegex(unit['unit_id'], r'^u-[0-9a-f]{32}$')
            self.assertEqual(unit['assessment']['content'], 'NOT_JUDGED')
            cursor = unit['end']
        self.assertEqual(cursor, len(text))
        return units

    def test_empty_whitespace_comments_and_page_markers_preserved(self):
        self.assertEqual(partition(''), [])
        for text in ('  \n\t\n', '<!-- unknown -->\n\n<!-- PAGE 5 -->\n', '\n<!-- PAGE 1 -->\n\n# Intro\n\nText.\n'):
            with self.subTest(text=text):
                units = self.preserve(text)
                if '#' not in text:
                    self.assertTrue(all(not unit['searchable'] for unit in units))

    def test_markdown_table_nested_list_and_fence_are_atomic(self):
        table = '| Column | Value |\n| --- | --- |\n| T_A | 25 |\n'
        nested = '- Parent item\n  - Child item\n\n  Continued condition.\n'
        fence = '```html\n<table>not an HTML container\n\n$$ literal\n```\n'
        text = f'# Header\n\n{table}\n{nested}\n- Other item\n\n{fence}\nEnd.\n'
        units = self.preserve(text)
        self.assertTrue(any(unit['kind'] == 'table' and unit['text'] == table for unit in units))
        self.assertTrue(any(unit['kind'] == 'list_item' and nested.rstrip() in unit['text'] for unit in units))
        self.assertTrue(any(unit['kind'] == 'raw_block' and unit['text'] == fence for unit in units))
        self.assertEqual(sum(unit['kind'] == 'list_item' for unit in units), 2)

    def test_html_table_and_formula_cross_page_markers_and_blank_lines(self):
        table = '<table><tr><td>A</td></tr>\n\n<!-- PAGE 2 -->\n\n<tr><td>B</td></tr></table>\n'
        formula = '$$\nV =\n\n<!-- PAGE 3 -->\n\nI R\n$$\n'
        text = '<!-- PAGE 1 -->\n\n' + table + '\n' + formula + '\nEnd.\n'
        units = self.preserve(text)
        tables = [unit for unit in units if unit['kind'] == 'table']
        formulas = [unit for unit in units if unit['kind'] == 'formula']
        self.assertEqual(len(tables), 1)
        self.assertIn(table, tables[0]['text'])
        self.assertEqual(tables[0]['pages'], [1, 2])
        self.assertEqual(len(formulas), 1)
        self.assertEqual(formulas[0]['text'], formula)
        self.assertEqual(formulas[0]['pages'], [2, 3])
        scopes = organize_scopes(units, soft_chars=20)
        self.assertTrue(tables[0]['hints'])
        self.assertIn(tables[0]['unit_id'], next(scope for scope in scopes if scope['scope_id'] ==
                                               tables[0]['primary_scope_id'])['oversized_unit_ids'])

    def test_literal_page_markers_in_code_or_inline_text_do_not_expand_page_scope(self):
        text = ('<!-- PAGE 1 -->\n\n```html\n<!-- PAGE 99 -->\n```\n\n'
                '- Example\n  ```html\n  <!-- PAGE 80 -->\n  ```\n\n'
                'Text with `<!-- PAGE 70 -->` example.\n\nAfter example.\n')
        units = self.preserve(text)
        self.assertTrue(units)
        self.assertTrue(all(unit['pages'] == [1] for unit in units))

    def test_math_delimiters_and_unknown_html_are_not_split(self):
        cases = [r'\[a' + '\n\nb' + r'\]' + '\n',
                 '\\begin{align*}\nx &= 2 \\\\\n\ny &= 3\n\\end{align*}\n',
                 '<div>Unknown content\n\n<custom-tag>retain exactly</custom-tag>\n</div>\n',
                 '<figure><img src="assets/a.png">\n\n<figcaption>Confirmed caption</figcaption></figure>\n']
        for text in cases:
            with self.subTest(text=text):
                units = self.preserve(text)
                self.assertEqual(len(units), 1)
                self.assertIn(units[0]['kind'], ('formula', 'raw_block'))

    def test_unclosed_container_preserves_remaining_content_with_uncertainty(self):
        for text in ('<table>\n\nNever closed\n\n# Not a safe boundary\n', '$$\nx + y\n\n## Still inside unresolved math\n'):
            with self.subTest(text=text):
                units = self.preserve(text)
                self.assertEqual(len(units), 1)
                self.assertEqual(units[0]['kind'], 'raw_block')
                self.assertTrue(any(hint['type'] == 'UNRESOLVED_CONTAINER' for hint in units[0]['hints']))

    def test_unicode_offsets_crlf_escaping_and_unicode_line_separator(self):
        text = '# \u4e2d\u6587 \U0001f9ea\r\n\r\nDATA\\_STATUS \u2028 \U0001f600\r\n\r\n\u5c3e\u90e8\r\n'
        units = self.preserve(text)
        paragraphs = [unit for unit in units if unit['kind'] == 'paragraph']
        self.assertEqual(len(paragraphs), 2)
        self.assertEqual(paragraphs[0]['text'], 'DATA\\_STATUS \u2028 \U0001f600\r\n')

    def test_multiple_sources_preserve_basis_geometry_and_ambiguity(self):
        text = '<!-- PAGE 8 -->\n\nDuplicated text.\n'
        mapping = mapped(text, [('Duplicated text.\n', 'paragraph', [8, 9], 'AMBIGUOUS')])
        original = copy.deepcopy(mapping)
        units = self.preserve(text, mapping)
        unit, = [unit for unit in units if unit['kind'] == 'paragraph']
        self.assertEqual(unit['assessment']['location'], 'AMBIGUOUS')
        self.assertEqual(len(unit['source_refs']), 2)
        self.assertEqual(unit['pages'], [8, 9])
        self.assertEqual(unit['source_refs'][0]['input_sha256'], 'a' * 64)
        self.assertEqual(unit['source_refs'][0]['geometry']['rotation'], 90)
        self.assertEqual(unit['source_refs'][0]['basis'], 'GPU_NATIVE_LAYOUT_OBJECT')
        self.assertTrue(any(record['basis'] == 'OUTPUT_PAGE_MARKER' for record in unit['page_basis']))
        self.assertEqual(mapping, original)

    def test_native_association_never_upgrades_content_correctness(self):
        text = 'Wrong numerical value 999.\n'
        mapping = mapped(text, [(text, 'paragraph', [1], 'ASSOCIATED_NATIVE_LAYOUT')])
        unit, = self.preserve(text, mapping)
        self.assertEqual(unit['assessment']['location'], 'ASSOCIATED_NATIVE_LAYOUT')
        self.assertEqual(unit['assessment']['content'], 'NOT_JUDGED')

    def test_partial_mapping_cannot_claim_full_unit_association(self):
        text = 'Known content; unlocated condition.\n'
        mapping = mapped(text, [('Known content', 'paragraph', [1], 'ASSOCIATED_NATIVE_LAYOUT')])
        unit, = self.preserve(text, mapping)
        self.assertEqual(unit['assessment']['location'], 'PARTIALLY_ASSOCIATED')
        self.assertEqual(unit['source_refs'][0]['status'], 'ASSOCIATED_NATIVE_LAYOUT')
        self.assertEqual(unit['source_refs'][0]['overlap'], 'CONTAINED_SOURCE_OBJECT')

    def test_missing_map_keeps_unmapped_searchable_units(self):
        unit, = self.preserve('DATA_STATUS\n')
        self.assertTrue(unit['searchable'])
        self.assertEqual(unit['assessment']['location'], 'UNMAPPED')
        self.assertEqual(unit['source_refs'], [])

    def test_invalid_or_stale_mapping_offsets_refused(self):
        text = 'Content.\n'
        mapping = mapped(text, [(text, 'paragraph', [1], 'UNMAPPED')])
        for key, value in (('start', -1), ('end', len(text) + 1), ('text', 'different'), ('start', True)):
            invalid = copy.deepcopy(mapping)
            invalid['objects'][0][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                partition(text, invalid)

    def test_original_normalized_mapping_offsets_survive_crlf_translation(self):
        text = '\r\nContent.\r\n'
        mapping = mapped(text, [('Content.\r\n', 'paragraph', [1], 'UNMAPPED')])
        mapping['objects'][0]['normalized_legacy_range'] = [1, 10]
        units = self.preserve(text, mapping)
        unit, = [unit for unit in units if unit['kind'] == 'paragraph']
        self.assertEqual(unit['legacy_refs'][0]['start'], 2)
        self.assertEqual(unit['legacy_refs'][0]['original_map_range'], [1, 10])

    def test_confirmed_conditions_group_is_atomic_but_unconfirmed_group_is_not(self):
        image = '![Timing](assets/timing.png)\n'
        caption = 'Figure 3. Timing at V_DD = 3.3 V.\n'
        text = image + '\n' + caption + '\nOther.\n'
        mapping = mapped(text, [(image, 'image', [3], 'ASSOCIATED_NATIVE_LAYOUT'),
                                (caption, 'paragraph', [3], 'ASSOCIATED_NATIVE_LAYOUT')])
        for row in mapping['objects']:
            row.update(atomic_group='figure-3-with-condition', atomic_group_confirmed=True)
        units = self.preserve(text, mapping)
        image_unit, = [unit for unit in units if unit['kind'] == 'image']
        self.assertIn(caption, image_unit['text'])
        self.assertEqual(len(image_unit['source_refs']), 2)
        self.assertEqual(image_unit['structure_basis'], 'CONFIRMED_ATOMIC_GROUP')
        for row in mapping['objects']:
            row['atomic_group_confirmed'] = False
        separate = self.preserve(text, mapping)
        self.assertTrue(any(unit['text'] == caption for unit in separate))

    def test_unit_ids_are_fresh_random_identity_not_content_or_offset_digest(self):
        first = self.preserve('Repeated.\n\nRepeated.\n')
        second = self.preserve('Repeated.\n\nRepeated.\n')
        self.assertEqual(len({unit['unit_id'] for unit in first}), len(first))
        self.assertFalse({unit['unit_id'] for unit in first}.intersection(unit['unit_id'] for unit in second))

    def test_no_headings_get_multiple_neutral_scopes_without_splitting_units(self):
        text = '\n\n'.join('Paragraph ' + str(index) + ' X' * 35 for index in range(5)) + '\n'
        units = self.preserve(text)
        scopes = organize_scopes(units, soft_chars=100)
        self.assertGreater(len({unit['content_file'] for unit in units}), 1)
        self.assertEqual(scopes[0]['unit_ids'], [unit['unit_id'] for unit in units])
        self.assertEqual(len({unit['primary_scope_id'] for unit in units}), len(scopes) - 1)
        self.assertTrue(all(scope['type'] == 'VIRTUAL_SCOPE' for scope in scopes[1:]))
        self.assertTrue(all(scope['title'].startswith('Output range ') for scope in scopes[1:]))
        self.assertEqual(''.join(unit['text'] for unit in units), text)

    def test_repeated_headings_and_skipped_levels_remain_distinct(self):
        text = '# Repeated\n\nA.\n\n### Child\n\nB.\n\n# Repeated\n\nC.\n'
        units = self.preserve(text)
        scopes = organize_scopes(units)
        repeats = [scope for scope in scopes if scope['title'] == 'Repeated']
        self.assertEqual(len(repeats), 2)
        self.assertNotEqual(repeats[0]['scope_id'], repeats[1]['scope_id'])
        self.assertEqual(repeats[1]['conflicts'][0]['type'], 'DUPLICATE_HEADING_TEXT')
        child = next(scope for scope in scopes if scope['title'] == 'Child')
        self.assertEqual(child['parent_id'], repeats[0]['scope_id'])
        self.assertEqual(child['conflicts'][0]['type'], 'SKIPPED_HEADING_LEVEL')
        self.assertTrue(set(child['unit_ids']).issubset(repeats[0]['unit_ids']))

    def test_inferred_bold_heading_needs_repeated_style_and_remains_inferred(self):
        single = self.preserve('**Only candidate**\n\nText.\n')
        self.assertFalse(any('heading' in unit for unit in single))
        units = self.preserve('**Register map**\n\nText.\n\n**Timing parameters**\n\nMore.\n')
        scopes = organize_scopes(units)
        inferred = [scope for scope in scopes if scope['type'] == 'INFERRED_STRUCTURE']
        self.assertEqual(len(inferred), 2)
        self.assertTrue(all(scope['boundary_basis'] == 'REPEATED_STANDALONE_BOLD_STYLE' for scope in inferred))

    def test_title_revision_preserves_explicit_and_inferred_evidence(self):
        explicit = partition('# Old title\n')[0]
        changed = revised_heading(explicit, '# New title\n')
        self.assertEqual(changed, {**explicit['heading'], 'title': 'New title'})
        self.assertEqual(explicit['heading']['title'], 'Old title')
        units = partition('**First section**\n\nBody.\n\n**Second section**\n')
        inferred = next(unit for unit in units if unit.get('heading'))
        changed = revised_heading(inferred, '**Revised section**\n')
        self.assertEqual(changed, {**inferred['heading'], 'title': 'Revised section'})
        numbered = next(unit for unit in partition('1.1 First section\n\nText.\n\n1.2 Second section\n')
                        if unit.get('heading'))
        self.assertEqual(revised_heading(numbered, '2.4 Updated section\n')['title'], '2.4 Updated section')
        self.assertIsNone(revised_heading(partition('Plain paragraph.\n')[0], 'New paragraph.\n'))

    def test_title_revision_rejects_implicit_hierarchy_or_style_changes(self):
        explicit = partition('# Old title\n')[0]
        inferred = next(unit for unit in partition('**First**\n\n**Second**\n') if unit.get('heading'))
        numbered = next(unit for unit in partition('1.1 First\n\n1.2 Second\n') if unit.get('heading'))
        for unit, replacement in ((explicit, '## Changed level\n'), (explicit, 'No heading\n'),
                                  (inferred, 'No bold style\n'), (inferred, '**A sentence.**\n'),
                                  (numbered, '1.2.3 Changed level\n')):
            with self.subTest(replacement=replacement), self.assertRaisesRegex(ValueError, 'structural operation'):
                revised_heading(unit, replacement)

    def test_oversized_table_kept_whole_and_membership_is_total(self):
        text = '<table>\n<tr><td>' + 'DATA_STATUS ' * 200 + '</td></tr>\n</table>\n'
        units = self.preserve(text)
        scopes = organize_scopes(units, soft_chars=30)
        self.assertEqual(len(units), 1)
        self.assertEqual(scopes[-1]['oversized_unit_ids'], [units[0]['unit_id']])
        self.assertIn(units[0]['unit_id'], scopes[0]['unit_ids'])
        self.assertFalse(any('text' in scope for scope in scopes))

    def test_output_order_relationships_and_empty_root(self):
        units = self.preserve('First.\n\nSecond.\n')
        for current, following in zip(units, units[1:]):
            relation, = current['relationships']
            self.assertEqual(relation['type'], 'next_in_output')
            self.assertEqual(relation['target_unit_id'], following['unit_id'])
        self.assertEqual(organize_scopes([])[0]['unit_ids'], [])
        for value in (0, -1, 1.5, True):
            with self.assertRaises(ValueError):
                organize_scopes(units, soft_chars=value)


if __name__ == '__main__':
    unittest.main()
