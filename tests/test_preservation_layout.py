"""Spatial identity must not depend on expected source numbers or first matches."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import pymupdf

from pdf2md_p2.common import read, save, sha
from pdf2md_p2.preservation import MD, preserve, validate
from pdf2md_p2.preservation_layout import drawing_region, spatial_tables, table_key, table_locations
from pdf2md_p2.source_map import build, objects

TABLE = '<table><tr><td>Parameter</td><td>Value</td></tr><tr><td>Rail voltage</td><td>5</td></tr></table>'


class TableLayoutTests(unittest.TestCase):
    def outputs(self, tables):
        return objects('<!-- PAGE 1 -->\n\n'+'\n\n'.join(tables))

    def native(self, tables):
        return [{'kind': 'table', 'page': 1, 'text': value, 'bbox': [35, 60+i*160, 325, 110+i*160],
                 'native_index': 3+i*4, 'basis': 'GPU_NATIVE_LAYOUT_OBJECT'} for i, value in enumerate(tables)]

    def test_unique_native_identity_uses_actual_output_not_expected_answer(self):
        bad = TABLE.replace('>5<', '>500<')
        outputs = self.outputs([bad])
        located = table_locations(outputs, self.native([bad]), MD)
        self.assertIn(outputs[0]['start'], located)
        self.assertEqual(table_locations(outputs, self.native([TABLE]), MD), {})

    def test_case_is_part_of_serialized_identity(self):
        self.assertNotEqual(table_key(TABLE, MD), table_key(TABLE.replace('Rail', 'rail'), MD))

    def test_repeated_tables_need_complete_ordered_serialization(self):
        outputs = self.outputs([TABLE, TABLE])
        located = table_locations(outputs, self.native([TABLE, TABLE]), MD)
        self.assertEqual(len(located), 2)
        self.assertNotEqual(located[outputs[0]['start']]['bbox'], located[outputs[1]['start']]['bbox'])

    def test_missing_or_extra_duplicate_cannot_be_assigned_by_rank(self):
        self.assertEqual(table_locations(self.outputs([TABLE]*3), self.native([TABLE]*2), MD), {})
        self.assertEqual(table_locations(self.outputs([TABLE]), self.native([TABLE]*2), MD), {})

    def test_missing_native_indices_do_not_certify_duplicate_order(self):
        native = self.native([TABLE]*2)
        for row in native:
            row.pop('native_index')
        self.assertEqual(table_locations(self.outputs([TABLE]*2), native, MD), {})

    def test_nonmonotonic_native_order_is_rejected(self):
        native = self.native([TABLE]*2)
        native.reverse()
        self.assertEqual(table_locations(self.outputs([TABLE]*2), native, MD), {})

    def test_changed_sequence_blocks_duplicate_rank_matching(self):
        a = TABLE.replace('Rail', 'Bus')
        outputs = self.outputs([TABLE, a, TABLE])
        native = self.native([TABLE, TABLE, a])
        located = table_locations(outputs, native, MD)
        self.assertNotIn(outputs[0]['start'], located)
        self.assertNotIn(outputs[2]['start'], located)

    def test_spatial_selection_rejects_competing_boxes(self):
        outputs = self.outputs([TABLE]*2)
        locations = {row['start']: {'bbox': [35, 60, 325, 110]} for row in outputs}
        self.assertEqual(spatial_tables([{'bbox': [35, 60, 325, 110]}], outputs, locations), [])

    def test_duplicate_tables_trace_to_distinct_source_regions(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            pdf, output = root / 'source.pdf', root / 'result'
            (output / 'assets').mkdir(parents=True)
            with pymupdf.open() as doc:
                page = doc.new_page()
                for y in (60, 220):
                    for x in (35, 185, 325):
                        page.draw_line((x, y), (x, y+50))
                    for value in (y, y+25, y+50):
                        page.draw_line((35, value), (325, value))
                    for x, yy, value in ((40,y+17,'Parameter'), (190,y+17,'Value'),
                                         (40,y+42,'Rail voltage'), (190,y+42,'5')):
                        page.insert_text((x,yy), value, fontsize=9)
                doc.save(pdf)
            before = '<!-- PAGE 1 -->\n\n'+TABLE+'\n\n'+TABLE+'\n'
            native = self.native([TABLE]*2)
            with patch('pdf2md_p2.source_map.native_objects', return_value=native):
                text, report = preserve(pdf, before, output)
                (output / 'manual.md').write_text(text)
                mapping = build(pdf, output, [])
            self.assertEqual(report['states'], {'SOURCE_GRID_MATCH': 2})
            tables = [r for r in mapping['objects'] if r['kind'] == 'table']
            self.assertEqual([r['status'] for r in tables], ['ASSOCIATED_NATIVE_LAYOUT']*2)
            self.assertNotEqual(tables[0]['sources'][0]['bbox'], tables[1]['sources'][0]['bbox'])
            self.assertTrue(validate(output)['content_gate_passed'])
            ledger = read(output / 'source_preservation.json')
            ledger['records'][0]['bindings'][0]['source_bbox'] = [1, 2, 3, 4]
            save(output / 'source_preservation.json', ledger)
            with self.assertRaisesRegex(ValueError, 'binding span or geometry'):
                validate(output)


class DrawingRegionTests(unittest.TestCase):
    def setUp(self):
        self.doc = pymupdf.open()
        self.addCleanup(self.doc.close)
        self.page = self.doc.new_page()
        self.page.draw_polyline([(50,80), (90,80), (90,120), (150,120)])
        self.page.insert_text((45,150), 'Figure 1: Timing', fontname='hebo', fontsize=10)
        line = [line for block in self.page.get_text('dict')['blocks'] for line in block.get('lines', [])
                if ''.join(s['text'] for s in line['spans']).startswith('Figure 1')][0]
        self.caption = {'label': '1', 'caption': 'Figure 1: Timing', 'bbox': list(line['bbox'])}
        self.native = [{'kind': 'image', 'text': 'Figure 1: Timing', 'bbox': [45, 70, 135, 125]}]

    def test_source_paths_expand_a_clipped_native_rectangle(self):
        region = drawing_region(self.page, self.caption, [self.caption], self.native)
        self.assertIsNotNone(region)
        self.assertGreater(region['bbox'][2], 150)
        self.assertGreater(region['bbox'][3], self.caption['bbox'][3])

    def test_unique_source_drawing_works_without_native_image(self):
        region = drawing_region(self.page, self.caption, [self.caption], [])
        self.assertIsNotNone(region)
        self.assertEqual(region['basis'], 'UNIQUE_SOURCE_DRAWING_AND_CAPTION')

    def test_detached_unassigned_drawing_prevents_complete_claim(self):
        self.page.draw_polyline([(260,80), (290,80), (290,120), (320,120)])
        self.assertIsNone(drawing_region(self.page, self.caption, [self.caption], self.native))

    def test_caption_only_does_not_supply_missing_pixels(self):
        page = self.doc.new_page()
        self.assertIsNone(drawing_region(page, self.caption, [self.caption], self.native))

    def test_duplicate_native_loci_remain_ambiguous(self):
        self.assertIsNone(drawing_region(self.page, self.caption, [self.caption], self.native*2))

    def test_neighbor_caption_cannot_be_swallowed(self):
        other = {'label': '2', 'caption': 'Figure 2: Other', 'bbox': [80,100,130,115]}
        self.assertIsNone(drawing_region(self.page, self.caption, [self.caption, other], self.native))

    def test_other_column_prose_is_not_added_to_source_crop(self):
        self.page.insert_text((1,100), 'PROSE', fontsize=9)
        region = drawing_region(self.page, self.caption, [self.caption], self.native)
        self.assertGreater(region['bbox'][0], 30)

    def test_text_cut_at_candidate_boundary_prevents_complete_claim(self):
        self.page.insert_text((145,100), 'EXTERNAL LABEL', fontsize=10)
        self.assertIsNone(drawing_region(self.page, self.caption, [self.caption], self.native))

    def test_graphic_crop_is_read_only_for_rotated_input(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            pdf, output = root / 'source.pdf', root / 'result'
            (output / 'assets').mkdir(parents=True)
            self.page.set_rotation(270)
            self.doc.save(pdf)
            original = sha(pdf)
            _, report = preserve(pdf, '<!-- PAGE 1 -->\n\nFigure 1: Timing\n', output)
            self.assertEqual(report['states'], {'SOURCE_FIGURE_REGION_PRESERVED': 1})
            self.assertEqual(report['records'][0]['page_rotation'], 270)
            self.assertEqual(sha(pdf), original)


if __name__ == '__main__':
    unittest.main()
