from pathlib import Path
import tempfile
import unittest

import pymupdf

from pdf2md_p2.quantities import check, equivalent, extract


class QuantityLexicalTests(unittest.TestCase):
    def test_leader_keeps_minus_and_original_span(self):
        text = 'AVSS to DGND.........-1.95V'
        item, = extract(text)
        self.assertEqual(item['raw_text'], '-1.95V')
        self.assertEqual(item['value'], ['-1.95'])
        self.assertEqual(text[slice(*item['raw_span'])], item['raw_text'])

    def test_signed_units_ranges_and_exponents(self):
        for text, values in [('-0.3V', ['-0.3']), ('\u22120.3 V', ['-0.3']),
                ('+2.2V', ['2.2']), ('\u00b10.5%', ['0.5']), ('1e-3 A', ['0.001']),
                ('-40 to +125\u00b0C', ['-40', '125']), ('3.0\u20133.6V', ['3.0', '3.6'])]:
            with self.subTest(text=text):
                item, = extract(text)
                self.assertEqual(item['parse_status'], 'SUPPORTED')
                self.assertEqual(item['value'], values)
                self.assertEqual(item['raw_span'], [0, len(text)])

    def test_bad_expression_never_degrades_to_unsigned_tail(self):
        for text in ('1..95V', '1e--3A', '+-0.3V', '3 6V'):
            with self.subTest(text=text):
                self.assertFalse(any(r['parse_status'] == 'SUPPORTED' for r in extract(text)))

    def test_binary_offset_not_a_negative_limit(self):
        item, = extract('VDD - 0.3V')
        self.assertEqual(item['parse_status'], 'CONTEXT_REQUIRED')
        self.assertEqual(item['raw_text'], '- 0.3V')

    def test_spaced_leading_dash_is_not_certified_as_negative(self):
        for text in ('- 1.8 V application supply', '\u2013 32 kHz oscillator'):
            item, = extract(text)
            self.assertEqual(item['parse_status'], 'CONTEXT_REQUIRED')

    def test_explicit_unary_sign_can_wrap_with_exact_original_span(self):
        text = '\u00b1\n1 Hz'
        item, = extract(text)
        self.assertEqual(item['parse_status'], 'SUPPORTED')
        self.assertEqual(item['sign'], ['PLUS_MINUS'])
        self.assertEqual(item['raw_span'], [0, len(text)])

    def test_presentation_equivalence_not_value_or_unit_guessing(self):
        self.assertTrue(equivalent('1e-3 A', '0.001A'))
        self.assertTrue(equivalent('-40 to +125\u00b0C', '-40\u2013125\u00b0C'))
        self.assertFalse(equivalent('\u00b10.5%', '0.5%'))
        self.assertFalse(equivalent('-0.3V', '+0.3V'))
        self.assertFalse(equivalent('1mA', '1A'))
        self.assertIsNone(equivalent('3 6V', '36V'))


class QuantityAlignmentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.pdf = Path(self.temp.name) / 'source.pdf'

    def tearDown(self):
        self.temp.cleanup()

    def source(self, lines):
        with pymupdf.open() as doc:
            page = doc.new_page()
            for i, text in enumerate(lines):
                page.insert_text((72, 90 + i*24), text)
            doc.save(self.pdf)

    def test_adjacent_capreg_limits_do_not_cross_cells(self):
        self.source(['CAPREG to DGND.........-0.3V to +2.2V',
                     'CAPREG to AVSS.........-0.3V to +3.9V'])
        text = ('<!-- PAGE 1 -->\n| Left | Right |\n| --- | --- |\n'
                '| CAPREG to DGND. -0.3V to +2.2V | unrelated text |\n'
                '| miscellaneous | -0.3V to +3.9V CAPREG to AVSS |\n')
        result = check(self.pdf, text)
        self.assertEqual(result['states'], {'QUANTITY_SOURCE_CONTEXT_MATCH': 4})
        self.assertEqual(result['reliably_paired'], 4)

    def test_changed_quantity_detected_without_using_expected_value_to_align(self):
        self.source(['CAPREG to DGND.........-0.3V to +2.2V'])
        result = check(self.pdf, '<!-- PAGE 1 -->\nCAPREG to DGND -0.3V to +3.9V\n')
        self.assertEqual(result['states'].get('QUANTITY_CONTEXT_DISTORTION'), 1)
        row = next(r for r in result['records'] if r['state'] == 'QUANTITY_CONTEXT_DISTORTION')
        self.assertEqual(row['source_quantity'], '+2.2V')
        self.assertEqual(row['actual_candidates'], ['+3.9V'])

    def test_duplicate_parameter_objects_are_ambiguous(self):
        self.source(['Supply voltage is -0.3V under normal operation.'])
        result = check(self.pdf, '<!-- PAGE 1 -->\nSupply voltage is -0.3V under normal operation.\n\n'
                       'Supply voltage is +0.3V under normal operation.\n')
        self.assertEqual(result['assessment_states'], {'ALIGNMENT_AMBIGUOUS': 1})
        self.assertEqual(result['reliably_paired'], 0)

    def test_source_duplicate_objects_are_not_forced_to_same_output(self):
        self.source(['Supply voltage is -0.3V under normal operation.']*2)
        result = check(self.pdf, '<!-- PAGE 1 -->\nSupply voltage is -0.3V under normal operation.\n')
        self.assertEqual(result['reliably_paired'], 0)

    def test_quantity_locations_are_actual_unicode_codepoint_offsets(self):
        self.source(['Supply voltage is -0.3V under normal operation.'])
        md = '<!-- PAGE 1 -->\n\U0001f680 Supply voltage is +0.3V under normal operation.\n'
        row, = check(self.pdf, md)['records']
        a, b = row['output_location']['start'], row['output_location']['end']
        self.assertEqual(md[a:b], '+0.3V')

    def test_html_cells_have_separate_object_boundaries(self):
        self.source(['Supply voltage is -0.3V under normal operation.'])
        text = '<!-- PAGE 1 -->\n<table><tr><td>Supply voltage is -0.3V under normal operation.</td><td>0.9V</td></tr></table>\n'
        row, = check(self.pdf, text)['records']
        self.assertEqual(row['state'], 'QUANTITY_SOURCE_CONTEXT_MATCH')
        self.assertEqual(row['output_location']['kind'], 'HTML_CELL')

    def test_wrapped_source_prose_uses_whole_unique_block(self):
        with pymupdf.open() as doc:
            page = doc.new_page()
            page.insert_text((72, 90), 'Supply voltage is -0.3V\nunder normal operation.')
            doc.save(self.pdf)
        row, = check(self.pdf, '<!-- PAGE 1 -->\nSupply voltage is +0.3V under normal operation.\n')['records']
        self.assertEqual(row['state'], 'QUANTITY_CONTEXT_DISTORTION')
        self.assertEqual(row['alignment_basis'], 'UNIQUE_SOURCE_BLOCK_AND_OUTPUT_PARAGRAPH_NO_EXPECTED_VALUE')

    def test_formatted_missing_unit_cannot_shift_slots_within_larger_paragraph(self):
        self.source(['5 pF to 25 pF range designed for high frequency applications.'])
        md = ('<!-- PAGE 1 -->\nFor capacitors, 5 pF to 25 $p F$ range designed for high frequency applications. '
              'The board capacitance is 10 pF.\n')
        result = check(self.pdf, md)
        self.assertEqual(result['reliably_paired'], 0)
        self.assertNotIn('QUANTITY_CONTEXT_DISTORTION', result['states'])

    def test_wrapped_plus_minus_is_not_compared_as_unsigned_fragment(self):
        with pymupdf.open() as doc:
            page = doc.new_page()
            page.insert_text((72, 90), 'The rejection at 50 Hz \u00b1\n1 Hz degrades significantly.')
            doc.save(self.pdf)
        md = '<!-- PAGE 1 -->\nThe rejection at 50 Hz \u00b1 1 Hz degrades significantly.\n'
        result = check(self.pdf, md)
        self.assertEqual(result['reliably_paired'], 2)
        self.assertNotIn('QUANTITY_CONTEXT_DISTORTION', result['states'])
        self.assertTrue(any('\n' in r['source_quantity'] for r in result['records']))

    def test_multiline_source_parent_competes_with_single_line_condition(self):
        with pymupdf.open() as doc:
            page = doc.new_page()
            page.insert_text((72, 90), 'VEN = VIN = 6 V,\nIOUT = 0 mA')
            page.insert_text((72, 145), 'VEN = VIN = 6 V, IOUT = 300 mA')
            doc.save(self.pdf)
        md = ('<!-- PAGE 1 -->\n<table><tr><td>VEN = VIN = 6 V,<br/>IOUT = 0 mA</td></tr>'
              '<tr><td>VEN = VIN = 6 V, IOUT = 300</td><td>mA</td></tr></table>\n')
        result = check(self.pdf, md)
        self.assertNotIn('QUANTITY_CONTEXT_DISTORTION', result['states'])
        row, = [r for r in result['records'] if r['source_quantity'] == '300 mA']
        self.assertEqual(row['assessment_status'], 'ALIGNMENT_AMBIGUOUS')
        self.assertIsNone(row['output_location'])

    def test_source_quantity_survives_when_whole_output_parent_is_absent(self):
        self.source(['Supply voltage is -0.3V under normal operation.'])
        from pdf2md_p2.fidelity import assess
        report = assess(self.pdf, '<!-- PAGE 1 -->\nOther retained content.\n')
        row, = [r for r in report['records'] if r['issue_type'] == 'QUANTITY_SOURCE_COMPARISON']
        self.assertEqual(row['expected_source_expression'], '-0.3V')
        self.assertTrue(row['source_regions'])
        self.assertIsNone(row['output_location'])
        self.assertIsNone(row['actual_output_expression'])
        self.assertEqual(row['assessment_status'], 'NOT_CHECKED')


if __name__ == '__main__':
    unittest.main()
