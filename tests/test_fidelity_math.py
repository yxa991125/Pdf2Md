from pathlib import Path
import tempfile
import unittest

import pymupdf

from pdf2md_p2.fidelity import assess, output_formulas
from pdf2md_p2.fidelity_math import UnsupportedMath, differences, formula_name, parse


class ArithmeticTests(unittest.TestCase):
    def test_style_and_spacing_preserve_literal_script(self):
        a = parse(r'V_{CAPP}=V_{CM}+GAIN*(V_{AINP}-V_{CM})')
        b = parse(r'\mathsf{V_{C A P P}}=\mathrm{V}_{CM}+\mathsf{G A I N}\times(V_{AINP}-V_{CM})')
        self.assertEqual(a.tree, b.tree)

    def test_fraction_structure_and_parentheses(self):
        self.assertEqual(parse(r'V_{CM}=\frac{(V_{AINP}+V_{AINN})}{2}').tree,
                         parse(r'V_{CM}=(V_{AINP}+V_{AINN})/2').tree)
        self.assertNotEqual(parse('X=(A+B)/2').tree, parse('X=A+B/2').tree)

    def test_literal_operator_and_operand_difference(self):
        self.assertEqual(differences('X=A-B', 'X=A+B')['status'], 'SOURCE_MISMATCH')
        self.assertEqual(differences('X=A-B', 'X=A-C')['status'], 'SOURCE_MISMATCH')

    def test_numeric_presentation_equivalence_without_binary_float_rounding(self):
        self.assertEqual(differences('X=A/2.', 'X=A/2')['status'], 'SOURCE_MATCH')
        self.assertEqual(differences('X=A*1e3', 'X=A*1000.0')['status'], 'SOURCE_MATCH')
        self.assertEqual(differences('X=A*1.00000000000000001', 'X=A*1.00000000000000002')['status'], 'SOURCE_MISMATCH')
        self.assertEqual(differences('X=A*1.'+'0'*40+'1', 'X=A*1.'+'0'*40+'2')['status'], 'SOURCE_MISMATCH')

    def test_corrupted_underscore_has_specific_missing_subtraction(self):
        result = differences(r'X=A*(V_{IN}-V_{CM})', r'X=A*(V_{IN^{\_}}V_{CM})')
        self.assertEqual(result['status'], 'SOURCE_MISMATCH')
        self.assertIn('SUBTRACTION_OPERATOR_ABSENT', result['differences'])

    def test_unrecognized_notation_is_not_declared_wrong(self):
        self.assertEqual(differences('X=A-B', r'X=\unknown{A,B}')['status'], 'NOT_CHECKED')
        self.assertEqual(differences('X=2^{15}', r'X=2^{^{15}}')['status'], 'NOT_CHECKED')

    def test_never_executes_functions_or_arbitrary_python(self):
        for text in ('X=__import__(os)', 'X=A[0]', 'X=A.attr', 'X=(lambda:1)()', 'X=[A,B]'):
            with self.subTest(text=text), self.assertRaises(UnsupportedMath):
                parse(text)


class SourceAssessmentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.pdf = Path(self.temp.name) / 'source.pdf'

    def tearDown(self):
        self.temp.cleanup()

    def source(self, rows):
        with pymupdf.open() as doc:
            p = doc.new_page()
            for i, row in enumerate(rows):
                p.insert_text((72, 90+i*30), row)
            doc.save(self.pdf)

    def test_actual_source_region_and_output_span(self):
        self.source(['X = A - B'])
        md = '<!-- PAGE 1 -->\n\n$$X=A+B$$\n'
        report = assess(self.pdf, md)
        row, = [r for r in report['records'] if r['issue_type'] == 'FORMULA_SOURCE_COMPARISON']
        self.assertEqual(row['assessment_status'], 'SOURCE_MISMATCH')
        self.assertEqual(md[row['output_location']['start']:row['output_location']['end']], 'X=A+B')
        self.assertTrue(row['source_regions'])

    def test_source_side_can_find_completely_missing_formula(self):
        self.source(['X = A - B'])
        report = assess(self.pdf, '<!-- PAGE 1 -->\nOther retained content.\n')
        rows = [r for r in report['records'] if r['issue_type'] == 'FORMULA_OMITTED']
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['assessment_status'], 'SOURCE_MISMATCH')

    def test_same_named_formulas_are_ambiguous_not_value_selected(self):
        self.source(['X = A - B', 'X = A + B'])
        report = assess(self.pdf, '<!-- PAGE 1 -->\n$$X=A-B$$\n')
        self.assertFalse(any(r['assessment_status'] == 'SOURCE_MATCH' for r in report['records']))
        self.assertTrue(any(r['assessment_status'] == 'ALIGNMENT_AMBIGUOUS' for r in report['records']))

    def test_inline_and_display_formulas_have_distinct_offsets(self):
        rows = output_formulas('<!-- PAGE 1 -->\n$x=a+b$\n\n$$y=a-b$$\n')
        self.assertEqual([r['representation'] for r in rows], ['INLINE', 'DISPLAY'])

    def test_image_representation_not_assumed_to_be_formula_loss(self):
        self.source(['X = A - B'])
        report = assess(self.pdf, '<!-- PAGE 1 -->\n![formula](assets/formula.png)\n')
        row, = [r for r in report['records'] if r['issue_type'] == 'EDITABLE_FORMULA_NOT_FOUND']
        self.assertEqual(row['assessment_status'], 'NOT_CHECKED')

    def test_plain_native_cpu_formula_is_compared_not_reported_missing(self):
        self.source(['X = A - B'])
        report = assess(self.pdf, '<!-- PAGE 1 -->\nX = A - B\n')
        row, = [r for r in report['records'] if r['issue_type'] == 'FORMULA_SOURCE_COMPARISON']
        self.assertEqual(row['assessment_status'], 'SOURCE_MATCH')
        self.assertEqual(row['output_representation'], 'PLAIN')

    def test_formulas_inside_code_blocks_are_not_output_math(self):
        fence = chr(96)*3
        text = '<!-- PAGE 1 -->\n'+fence+'text\n$$X=A-B$$\nX=A-B\n'+fence+'\n'
        self.assertEqual(output_formulas(text), [])

    def test_unsupported_marked_up_formula_is_not_falsely_declared_absent(self):
        self.source(['X = A - B'])
        report = assess(self.pdf, '<!-- PAGE 1 -->\n**X = A - B**\n')
        self.assertFalse(any(r['issue_type'] == 'FORMULA_OMITTED' for r in report['records']))

    def test_math_fence_body_is_not_counted_again_as_plain_formula(self):
        self.source(['X = A - B'])
        md = '<!-- PAGE 1 -->\n$$\nX=A-B\n$$\n'
        self.assertEqual(len(output_formulas(md)), 1)
        self.assertEqual(assess(self.pdf, md)['summary']['matched'], 1)

    def test_explicit_equation_number_is_checked(self):
        self.source(['X = A - B    (7)'])
        for tag, expected in (('7', 'SOURCE_MATCH'), ('8', 'SOURCE_MISMATCH')):
            report = assess(self.pdf, '<!-- PAGE 1 -->\n$$X=A-B'+r'\tag{'+tag+'}$$\n')
            row, = [r for r in report['records'] if r['issue_type'] == 'FORMULA_SOURCE_COMPARISON']
            self.assertEqual(row['assessment_status'], expected)
            self.assertIn('equation_number', row['checked_attributes'])

    def test_explicit_same_line_local_condition_is_checked_not_used_to_pick_source(self):
        self.source(['When MODE = 0: X = A - B'])
        for mode, expected in (('0', 'SOURCE_MATCH'), ('1', 'SOURCE_MISMATCH')):
            md = '<!-- PAGE 1 -->\nWhen MODE = '+mode+': X = A - B\n'
            row, = [r for r in assess(self.pdf, md)['records'] if r['issue_type'] == 'FORMULA_SOURCE_COMPARISON']
            self.assertEqual(row['assessment_status'], expected)
            self.assertIn('condition', row['checked_attributes'])

    def test_missing_explicit_annotation_is_unchecked_not_asserted_equal(self):
        self.source(['When MODE = 0: X = A - B    (7)'])
        row, = [r for r in assess(self.pdf, '<!-- PAGE 1 -->\n$$X=A-B$$\n')['records']
                 if r['issue_type'] == 'FORMULA_SOURCE_COMPARISON']
        self.assertNotIn('condition', row['checked_attributes'])
        self.assertIn('condition', row['unchecked_attributes'])
        self.assertIn('equation_number', row['unchecked_attributes'])

    def test_final_map_ambiguity_prevents_false_confirmation(self):
        self.source(['X = A - B'])
        md = '<!-- PAGE 1 -->\n$$X=A-B$$\n'
        from pdf2md_p2.common import digest, sha
        mapping = {'input_sha256': sha(self.pdf), 'markdown_sha256': digest(md),
                   'objects': [{'id': name, 'page': 1, 'start': 15, 'end': len(md)} for name in ('first', 'second')]}
        report = assess(self.pdf, md, mapping)
        row, = [r for r in report['records'] if r['output_location']]
        self.assertIsNone(row['output_object_id'])
        self.assertEqual(row['assessment_status'], 'ALIGNMENT_AMBIGUOUS')
        self.assertEqual(report['summary']['checked'], 0)


if __name__ == '__main__':
    unittest.main()
