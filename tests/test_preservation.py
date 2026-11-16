import json
import io
from contextlib import redirect_stdout
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import pymupdf

from pdf2md_p2.common import digest, inventory, read, save, sha
from pdf2md_p2.preservation import CAPTION, VERSION, _cell_text, _matrix, norm, preserve, validate
from pdf2md_p2.source_map import build


GOOD = ('<!-- PAGE 1 -->\n\n| Parameter | Min. | Max. |\n| --- | --- | --- |\n'
        '| Rail voltage | -0.3 | 5.5 |\n| Gate current | 1 | 2 |\n')


class PreservationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.pdf = self.root / 'source.pdf'
        self.output = self.root / 'result'
        (self.output / 'assets').mkdir(parents=True)

    def table(self, page, x=35, y=60, rows=None):
        rows = rows or [['Parameter', 'Min.', 'Max.'], ['Rail voltage', '-0.3', '5.5'], ['Gate current', '1', '2']]
        xs = [x, x+150, x+220, x+290]
        for i in range(len(rows)+1):
            page.draw_line((xs[0], y+i*25), (xs[-1], y+i*25))
        for value in xs:
            page.draw_line((value, y), (value, y+len(rows)*25))
        for r, cells in enumerate(rows):
            for c, cell in enumerate(cells):
                page.insert_text((xs[c]+4, y+r*25+17), cell, fontsize=9)

    def source(self, *, two=False, caption=None, rotation=0, rows=None):
        with pymupdf.open() as document:
            page = document.new_page()
            if caption is None:
                self.table(page, rows=rows)
                if two:
                    self.table(page, y=230, rows=rows)
            else:
                page.draw_rect(pymupdf.Rect(35, 60, 400, 190))
                page.draw_line((65, 95), (250, 95))
                page.insert_text((65, 90), 'INPUT', fontsize=10)
                page.insert_text((50, 178), caption, fontsize=10, fontname='hebo')
            page.set_rotation(rotation)
            document.save(self.pdf)

    def recover(self, text):
        original_sha = sha(self.pdf)
        result, report = preserve(self.pdf, text, self.output)
        self.assertEqual(sha(self.pdf), original_sha)
        (self.output / 'manual.md').write_text(result, encoding='utf-8')
        return result, report

    def test_lettered_caption_and_punctuation(self):
        for caption in ('Figure 11i: Inputs', 'Fig. 2b. Timing', 'Figure 1: Circuit'):
            self.assertIsNotNone(CAPTION.match(caption))

    def test_correct_table_is_unchanged(self):
        self.source()
        text, report = self.recover(GOOD)
        self.assertEqual(text, GOOD)
        self.assertEqual(report['states'], {'SOURCE_GRID_MATCH': 1})

    def test_merged_rows_rebuilt_from_cell_geometry(self):
        self.source()
        bad = GOOD[:GOOD.index('| Rail')]+'| Rail voltage Gate current | -0.31 | 5.52 |\n'
        text, report = self.recover(bad)
        self.assertEqual(report['states'], {'REBUILT_SOURCE_GRID': 1})
        self.assertEqual(_matrix(text.split('\n\n', 1)[1]), _matrix(GOOD.split('\n\n', 1)[1]))
        self.assertNotIn('5.52', text)

    def test_wrong_numeric_value_is_not_used_to_choose_a_table(self):
        self.source()
        text, report = self.recover(GOOD.replace('-0.3', '0.30').replace('5.5', '55'))
        self.assertIn('-0.3', text)
        self.assertEqual(report['states']['REBUILT_SOURCE_GRID'], 1)

    def test_missing_source_table_is_explicit_visual_and_review_required(self):
        self.source()
        text, report = self.recover('<!-- PAGE 1 -->\n\nNo table here.\n')
        self.assertIn('visual reference, structure unresolved', text)
        self.assertFalse(report['content_gate_passed'])
        self.assertIsNone(report['whole_document_content_percent'])

    def test_ambiguous_duplicate_output_is_not_overwritten(self):
        self.source()
        text, report = self.recover(GOOD+'\n\n'+GOOD.split('\n\n', 1)[1])
        self.assertEqual(text.count('| Rail voltage |'), 2)
        self.assertEqual(report['states']['SOURCE_TABLE_IMAGE_REVIEW_REQUIRED'], 1)

    def test_framed_figure_recovers_even_if_caption_survives_without_pixels(self):
        self.source(caption='Figure 11i: Inputs')
        text, report = self.recover('<!-- PAGE 1 -->\n\nFigure 11i: Inputs\n')
        self.assertIn('complete source panel', text)
        self.assertEqual(report['states']['SOURCE_FIGURE_REGION_PRESERVED'], 1)
        asset = report['records'][0]['asset']
        self.assertTrue((self.output / asset).is_file())

    def test_source_crop_uses_entire_frame_not_caption_only(self):
        self.source(caption='Figure 3a: Inputs')
        _, report = self.recover('<!-- PAGE 1 -->\n\nBody\n')
        box = report['records'][0]['source_bbox']
        self.assertAlmostEqual(box[0], 35)
        self.assertAlmostEqual(box[1], 60)
        self.assertAlmostEqual(box[3], 190)

    def test_rotated_source_input_remains_unchanged_and_coordinates_unrotated(self):
        self.source(caption='Figure 2: Inputs', rotation=90)
        _, report = self.recover('<!-- PAGE 1 -->\n\nBody\n')
        self.assertEqual(report['records'][0]['page_rotation'], 90)
        with pymupdf.open(self.pdf) as document:
            self.assertEqual(document[0].rotation, 90)

    def test_plain_body_figure_reference_is_not_a_caption(self):
        with pymupdf.open() as doc:
            page = doc.new_page()
            page.insert_text((40, 80), 'Figure 2. Data is read on this edge.', fontsize=10)
            doc.save(self.pdf)
        _, report = self.recover('<!-- PAGE 1 -->\n\nFigure 2. Data is read on this edge.\n')
        self.assertEqual(report['records'], [])

    def test_no_frame_does_not_claim_complete_image(self):
        with pymupdf.open() as doc:
            page = doc.new_page()
            page.insert_text((40, 80), 'Figure 2: Timing', fontname='hebo', fontsize=10)
            doc.save(self.pdf)
        _, report = self.recover('<!-- PAGE 1 -->\n\nFigure 2: Timing\n')
        self.assertEqual(report['states'], {'FIGURE_REGION_UNRESOLVED': 1})
        self.assertIsNone(report['whole_document_content_percent'])
        self.assertFalse(report['content_gate_passed'])

    def test_restored_object_trace_has_source_geometry(self):
        self.source()
        _, report = self.recover(GOOD.replace('-0.3', '3'))
        mapping = build(self.pdf, self.output, [])
        table, = [r for r in mapping['objects'] if r['kind'] == 'table']
        self.assertEqual(table['status'], 'ASSOCIATED_NATIVE_LAYOUT')
        self.assertEqual(table['sources'][0]['basis'], 'SOURCE_PRESERVATION_GEOMETRY')
        self.assertTrue(validate(self.output)['content_gate_passed'])

    def test_compound_table_recovery_traces_each_component(self):
        rows = [['Parameter', 'Min.', 'Max.'], ['Control bias', '4', '5'], ['Bus offset', '6', '7']]
        with pymupdf.open() as doc:
            page = doc.new_page()
            self.table(page)
            self.table(page, y=150, rows=rows)
            doc.save(self.pdf)
        text = GOOD.replace('5.5', '55')+'\n\n'+GOOD.split('\n\n', 1)[1].replace(
            'Rail voltage', 'Control bias').replace('Gate current', 'Bus offset')
        _, report = self.recover(text)
        self.assertEqual(report['states'], {'REBUILT_SOURCE_GRID': 1})
        mapping = build(self.pdf, self.output, [])
        tables = [r for r in mapping['objects'] if r['kind'] == 'table']
        self.assertEqual(len(tables), 2)
        self.assertEqual([r['status'] for r in tables], ['ASSOCIATED_NATIVE_LAYOUT']*2)
        self.assertNotEqual(tables[0]['sources'][0]['bbox'], tables[1]['sources'][0]['bbox'])

    def test_multiple_source_panels_retain_top_to_bottom_order(self):
        with pymupdf.open() as doc:
            page = doc.new_page()
            for label, y in (('2', 200), ('1', 60)):
                page.draw_rect(pymupdf.Rect(35, y, 400, y+100))
                page.insert_text((50, y+85), 'Figure '+label+': Circuit', fontname='hebo', fontsize=10)
            doc.save(self.pdf)
        text, _ = self.recover('<!-- PAGE 1 -->\n\nBody\n')
        self.assertLess(text.index('Figure 1:'), text.index('Figure 2:'))

    def test_empty_declared_page_preserves_image_inside_page_markers(self):
        self.source(caption='Figure 1: Circuit')
        text, _ = self.recover('<!-- PAGE 1 -->\n\n')
        self.assertTrue(text.startswith('<!-- PAGE 1 -->'))
        self.assertIn('complete source panel', text)
        build(self.pdf, self.output, [])
        self.assertTrue(validate(self.output)['content_gate_passed'])

    def test_failed_chunk_page_is_not_inserted_into_another_page(self):
        with pymupdf.open() as doc:
            self.table(doc.new_page())
            self.table(doc.new_page())
            doc.save(self.pdf)
        text, report = self.recover(GOOD)
        self.assertEqual(text, GOOD)
        self.assertEqual([r['page'] for r in report['records']], [1])

    def test_removing_recovered_content_is_rejected(self):
        self.source(caption='Figure 3: Circuit')
        self.recover('<!-- PAGE 1 -->\n\nBody\n')
        build(self.pdf, self.output, [])
        (self.output / 'manual.md').write_text('<!-- PAGE 1 -->\n\nBody\n', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'missing'):
            validate(self.output)

    def test_changing_preserved_asset_is_rejected(self):
        self.source(caption='Figure 3: Circuit')
        _, report = self.recover('<!-- PAGE 1 -->\n\nBody\n')
        build(self.pdf, self.output, [])
        (self.output / report['records'][0]['asset']).write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, 'image bytes'):
            validate(self.output)

    def test_summary_cannot_fabricate_full_content_percentage(self):
        self.source()
        self.recover(GOOD)
        build(self.pdf, self.output, [])
        data = read(self.output / 'source_preservation.json')
        data['whole_document_content_percent'] = 100
        save(self.output / 'source_preservation.json', data)
        with self.assertRaisesRegex(ValueError, 'inconsistent'):
            validate(self.output)

    def test_quality_summary_must_match_preservation_evidence(self):
        self.source()
        self.recover(GOOD)
        build(self.pdf, self.output, [])
        summary = validate(self.output)
        save(self.output / 'quality_report.json', {'content_preservation': summary})
        self.assertEqual(validate(self.output), summary)
        summary['content_gate_passed'] = False
        save(self.output / 'quality_report.json', {'content_preservation': summary})
        with self.assertRaisesRegex(ValueError, 'summary differs'):
            validate(self.output)

    def test_trace_validation_checks_preserved_assets(self):
        from pdf2md_p2.source_map import validate as validate_mapping
        self.source(caption='Figure 3: Circuit')
        _, report = self.recover('<!-- PAGE 1 -->\n\nBody\n')
        build(self.pdf, self.output, [])
        data = read(self.output / 'source_preservation.json')
        data['records'][0]['asset_sha256'] = '0'*64
        save(self.output / 'source_preservation.json', data)
        with self.assertRaisesRegex(ValueError, 'image bytes'):
            validate_mapping(self.output)

    def test_source_table_without_label_match_is_not_replaced(self):
        self.source()
        unrelated = GOOD.replace('Rail voltage', 'Other power').replace('Gate current', 'Bus signal').replace(
            'Parameter', 'Description')
        text, report = self.recover(unrelated)
        self.assertIn(unrelated, text)
        self.assertEqual(report['states'], {'SOURCE_TABLE_IMAGE_REVIEW_REQUIRED': 1})

    def test_superscript_and_escape_are_kept(self):
        glyphs = [{'text': '<', 'size': 10, 'origin': [10, 50], 'bbox': [10, 40, 17, 52], 'direction': [1., 0.]},
                  {'text': '2', 'size': 7, 'origin': [17, 46], 'bbox': [17, 39, 22, 48], 'direction': [1., 0.]}]
        self.assertEqual(_cell_text(glyphs), '&lt;<sup>2</sup>')

    def test_rowspan_and_colspan_change_is_detected(self):
        a = '<table><tr><td rowspan="2">A</td><td>B</td></tr><tr><td>C</td></tr></table>'
        b = '<table><tr><td>A</td><td>B</td></tr><tr><td></td><td>C</td></tr></table>'
        self.assertNotEqual(_matrix(a), _matrix(b))

    def test_legacy_result_needs_no_new_sidecar(self):
        self.assertIsNone(validate(self.output))

    def test_current_result_requires_preservation_sidecar(self):
        save(self.output / 'configuration.json', {'source_preservation_version': 'source-preservation-1'})
        with self.assertRaisesRegex(ValueError, 'missing source_preservation'):
            validate(self.output)

    def test_formal_verify_keeps_page_success_separate_from_content_review(self):
        from pdf2md_p2.cli import main
        self.source()
        self.recover('<!-- PAGE 1 -->\n\nMissing table\n')
        build(self.pdf, self.output, [])
        binding = {'version': '0.1.0a8', 'input_sha256': sha(self.pdf),
                   'source_preservation_version': VERSION}
        summary = validate(self.output)
        save(self.output / 'configuration.json', binding)
        save(self.output / 'quality_report.json', {'status': 'CONTENT_REVIEW_REQUIRED',
            'completed_pages': [1], 'expected_pages': 1, 'page_processing': {'coverage_percent': 100},
            'content_preservation': summary})
        save(self.output / 'run_manifest.json', {'configuration': binding, 'artifacts': inventory(self.output)})
        stdout = io.StringIO()
        with patch.object(sys, 'argv', ['pdf2md', 'verify', '--output-dir', str(self.output)]), redirect_stdout(stdout):
            self.assertEqual(main(), 2)
        result = json.loads(stdout.getvalue())
        self.assertTrue(result['integrity_passed'])
        self.assertEqual(result['page_processing']['coverage_percent'], 100)
        self.assertFalse(result['content_preservation']['content_gate_passed'])

    def test_script_geometry_is_serialized_not_split_into_plain_lines(self):
        glyphs = [{'text': 'T', 'size': 10, 'origin': [10, 50], 'bbox': [10, 40, 17, 52], 'direction': [1., 0.]},
                  {'text': 'A', 'size': 7, 'origin': [17, 53], 'bbox': [17, 46, 22, 55], 'direction': [1., 0.]}]
        self.assertEqual(_cell_text(glyphs), 'T<sub>A</sub>')

    def test_math_sign_normalization_does_not_erase_sign(self):
        self.assertEqual(norm('\u22120.3'), '-0.3')
        self.assertNotEqual(norm('\u22120.3'), norm('0.3'))

    def test_table_binding_moves_with_preceding_prose_edit(self):
        self.source()
        before = GOOD.replace('\n\n', '\n\nREG _ NAME\n\n', 1)
        text, _ = self.recover(before)
        (self.output / 'native_manual.md').write_text(text, encoding='utf-8')
        a = text.index('REG _ NAME')
        updated = text[:a]+'REG_NAME'+text[a+10:]
        save(self.output / 'change_log.json', {'before_sha256': digest(text), 'after_sha256': digest(updated),
            'operations': [{'native_start': a, 'native_end': a+10, 'before': 'REG _ NAME', 'after': 'REG_NAME',
                            'page': 1, 'output_start': a, 'source_bbox': [35, 35, 135, 45],
                            'source_context': 'REG_NAME'}]})
        (self.output / 'manual.md').write_text(updated, encoding='utf-8')
        mapping = build(self.pdf, self.output, [])
        table, = [r for r in mapping['objects'] if r['kind'] == 'table']
        self.assertEqual(table['status'], 'ASSOCIATED_NATIVE_LAYOUT')
        self.assertEqual(table['sources'][0]['basis'], 'SOURCE_PRESERVATION_GEOMETRY')
        self.assertTrue(validate(self.output)['content_gate_passed'])

    def test_prose_log_cannot_rewrite_a_preserved_table(self):
        self.source()
        text, _ = self.recover(GOOD)
        (self.output / 'native_manual.md').write_text(text, encoding='utf-8')
        a = text.index('5.5')
        updated = text[:a]+'555'+text[a+3:]
        save(self.output / 'change_log.json', {'before_sha256': digest(text), 'after_sha256': digest(updated),
            'operations': [{'native_start': a, 'native_end': a+3, 'before': '5.5', 'after': '555'}]})
        (self.output / 'manual.md').write_text(updated, encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'binding span'):
            build(self.pdf, self.output, [])

    def test_deleted_binding_is_not_hidden_by_a_successful_summary(self):
        self.source()
        self.recover(GOOD)
        build(self.pdf, self.output, [])
        ledger = read(self.output / 'source_preservation.json')
        ledger['records'][0]['bindings'] = []
        save(self.output / 'source_preservation.json', ledger)
        with self.assertRaisesRegex(ValueError, 'Missing preserved table bindings'):
            validate(self.output)

    def test_legacy_source_preservation_v1_remains_readable(self):
        self.source()
        self.recover(GOOD)
        build(self.pdf, self.output, [])
        ledger = read(self.output / 'source_preservation.json')
        ledger['version'] = 'source-preservation-1'
        for row in ledger['records']:
            row.pop('bindings', None)
        ledger.pop('preserved_markdown_sha256')
        save(self.output / 'source_preservation.json', ledger)
        self.assertTrue(validate(self.output)['content_gate_passed'])


if __name__ == '__main__':
    unittest.main()
