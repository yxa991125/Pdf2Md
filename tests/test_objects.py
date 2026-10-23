import unittest

from pdf2md_p2.objects import source_role, formula_representation, figure_frame, symbol_risks, Tables


class ObjectTests(unittest.TestCase):
    def test_source_unsupported_operator_isolated_without_guessing_variable(self):
        self.assertEqual(symbol_risks(r'\nabla_{CC}', 'the common mode voltage VCC'), ['FORMULA_SOURCE_SYMBOL_CONFLICT_SUSPECT'])
        self.assertEqual(symbol_risks(r'\nabla f', '\u2207f is a gradient'), [])
        self.assertEqual(symbol_risks(r'V_{CC}', 'VCC'), [])

    def test_grid_does_not_prove_technical_table(self):
        self.assertEqual(source_role('Functional Block Diagrams'), 'CIRCUIT_OR_BLOCK_DIAGRAM_CANDIDATE')
        self.assertEqual(source_role('Noise Histogram Plot, Occurrence / Codes'), 'CHART_CANDIDATE')
        self.assertEqual(source_role('rectangle grid'), 'SOURCE_ROLE_UNRESOLVED')

    def test_footer_requires_geometry_content_and_repetition(self):
        self.assertEqual(source_role('6 Submit Documentation Feedback', True, 40), 'PUBLICATION_FOOTER')
        for text, bottom, count in [('6 Submit Documentation Feedback', False, 40),
                                    ('6 Submit Documentation Feedback', True, 1),
                                    ('(1) Submit Documentation Feedback', True, 40),
                                    ('(1) Values measured at 25 C.', True, 40)]:
            self.assertNotEqual(source_role(text, bottom, count), 'PUBLICATION_FOOTER')

    def test_partial_equation_not_accepted_as_complete(self):
        self.assertEqual(formula_representation(r'\frac{V_IN}'), 'FORMULA_PARTIAL')
        self.assertEqual(formula_representation(r'\frac{V_IN}{V_REF'), 'FORMULA_PARTIAL')
        self.assertEqual(formula_representation(r'x =', closed=False), 'FORMULA_PARTIAL')
        self.assertEqual(formula_representation(r'Data ='), 'FORMULA_PARTIAL')
        self.assertEqual(formula_representation(''), 'FORMULA_MISSING')
        self.assertEqual(formula_representation(r'\frac{V_{IN}}{V_{REF}}'), 'EDITABLE_MATH')

    def test_source_authentic_small_frame_is_not_whole_figure(self):
        self.assertEqual(figure_frame([0, 0, 100, 100], [0, 0, 70, 100]), 'FIGURE_FRAME_PARTIAL')
        self.assertEqual(figure_frame([0, 0, 100, 100], [0, 0, 100, 100], [[100, 20, 130, 50]]), 'FIGURE_FRAME_PARTIAL')
        self.assertEqual(figure_frame([0, 0, 100, 100], [0, 0, 100, 100],
                                     source_transform=[1, 0, 0, -1, 0, 100]), 'FIGURE_TRANSFORM_UNRESOLVED')
        self.assertEqual(figure_frame([0, 0, 100, 100], [0, 0, 100, 100]), 'FRAME_GEOMETRY_ONLY_NOT_SEMANTIC_CERTIFICATION')

    def test_table_spans_preserved_not_flattened_to_leaf_values(self):
        reader = Tables()
        reader.feed('<table><tr><th colspan="2">PIN</th><th rowspan="2">TYPE</th></tr><tr><td>NAME</td><td>NO.</td></tr></table>')
        table, = reader.records
        self.assertEqual(table['rows'][0][0]['colspan'], 2)
        self.assertEqual(table['rows'][0][1]['rowspan'], 2)


if __name__ == '__main__':
    unittest.main()
