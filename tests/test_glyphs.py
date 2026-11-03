from pathlib import Path
import tempfile
import unittest

import pymupdf

from pdf2md_p2.glyphs import materialize
from pdf2md_p2.assets import check
from pdf2md_p2.common import inventory, save, sha, verify
from pdf2md_p2 import source_map


def displayed(box, width, height, rotation):
    x0, y0, x1, y1 = box
    return {0: box, 90: (height-y1, x0, height-y0, x1),
            180: (width-x1, height-y1, width-x0, height-y0),
            270: (y0, width-x1, y1, width-x0)}[rotation]


def crop_pixels(pixmap, box, scale=4):
    # Independent oracle: crop the already rendered whole page, with no PDF matrix.
    x0, y0, x1, y1 = (int(v * scale) for v in box)
    return b''.join(pixmap.samples[y * pixmap.stride + x0 * pixmap.n:
                                 y * pixmap.stride + x1 * pixmap.n] for y in range(y0, y1))


class GlyphTests(unittest.TestCase):
    def fixture(self, root, rotation, cropped=False):
        (root / 'assets').mkdir(exist_ok=True)
        pdf = root / 'source.pdf'
        with pymupdf.open() as document:
            page = document.new_page(width=200, height=300)
            if cropped:
                page.set_cropbox(pymupdf.Rect(10, 20, 190, 280))
            page.draw_rect((30, 40, 100, 65), color=(1, 0, 0), fill=(1, .9, .9))
            page.draw_line((32, 42), (42, 62), color=(0, 0, 1), width=2)
            page.insert_text((45, 55), 'Ab12', fontsize=10)
            page.set_rotation(rotation)
            document.save(pdf)
        width, height = (180, 260) if cropped else (200, 300)
        box = displayed((30, 40, 100, 65), width, height, rotation)
        return pdf, {'kind': 'SOURCE_GLYPH_VISUAL_REQUIRED_NO_OCR', 'page': 1,
                     'source_bbox_points': list(box), 'marker': 'P2SOURCEGLYPHROTATION123'}

    def test_rotation_pixels_geometry_and_input_unchanged(self):
        for cropped in (False, True):
            for rotation in (0, 90, 180, 270):
                with self.subTest(cropped=cropped, rotation=rotation), tempfile.TemporaryDirectory() as tmp:
                    root = Path(tmp)
                    pdf, event = self.fixture(root, rotation, cropped)
                    original_hash = sha(pdf)
                    text, (record,) = materialize(pdf, '<!-- PAGE 1 -->\nContext ' + event['marker'], [event], root)
                    with pymupdf.open(pdf) as document:
                        full = document[0].get_pixmap(matrix=pymupdf.Matrix(4, 4), alpha=False)
                        clip = record['render_bbox']
                        expected = crop_pixels(full, clip)
                    actual = pymupdf.Pixmap(root / record['asset'])
                    self.assertEqual(actual.samples, expected)
                    self.assertGreater(len(set(actual.samples)), 2)
                    self.assertEqual((actual.width, actual.height),
                                     (int((clip[2]-clip[0])*4), int((clip[3]-clip[1])*4)))
                    for value, wanted in zip(record['source_bbox'], (29, 39, 101, 66)):
                        self.assertAlmostEqual(value, wanted, places=4)
                    self.assertEqual(record['page_rotation'], rotation)
                    self.assertIn(f'width="{round(clip[2]-clip[0], 2)}"', text)
                    self.assertEqual(sha(pdf), original_hash)

    def test_rotated_source_text_is_not_duplicated(self):
        for rotation in (90, 180, 270):
            with self.subTest(rotation=rotation), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                pdf, event = self.fixture(root, rotation)
                before = '<!-- PAGE 1 -->\nAb12'
                text, (record,) = materialize(pdf, before, [event], root)
                self.assertEqual(text, before)
                self.assertFalse(record['automatic_addition'])
                self.assertEqual(list((root / 'assets').iterdir()), [])

    def test_rotated_missing_marker_retains_page_scoped_fragment(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pdf, event = self.fixture(root, 90)
            text, (record,) = materialize(pdf, '<!-- PAGE 1 -->\nUnchanged.', [event], root)
            self.assertIn('Unchanged.', text)
            self.assertEqual(record['placement'], 'PAGE_SCOPED_SOURCE_FRAGMENT_ASSOCIATION_UNRESOLVED')
            self.assertEqual(len(check(text, root)), 1)

    def test_duplicate_marker_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pdf, event = self.fixture(root, 90)
            with self.assertRaisesRegex(ValueError, 'multiply bound'):
                materialize(pdf, '<!-- PAGE 1 -->\n' + event['marker'] * 2, [event], root)

    def test_invalid_regions_are_rejected(self):
        boxes = ([float('nan'), 0, 10, 10], [0, 0, float('inf'), 10], [9, 2, 1, 10],
                 [0, 0, 0, 10], [400, 400, 410, 410], [1, 2, 3], ['x', 0, 1, 2])
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pdf, event = self.fixture(root, 90)
            for box in boxes:
                with self.subTest(box=box), self.assertRaisesRegex(ValueError, 'Invalid glyph source frame'):
                    materialize(pdf, '<!-- PAGE 1 -->\n' + event['marker'],
                                [{**event, 'source_bbox_points': box}], root)

    def test_edge_region_is_clipped_to_displayed_page(self):
        for rotation in (0, 90, 180, 270):
            with self.subTest(rotation=rotation), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                pdf, event = self.fixture(root, rotation, cropped=True)
                event['source_bbox_points'] = [-2, -3, 10, 12]
                _, (record,) = materialize(pdf, '<!-- PAGE 1 -->\n' + event['marker'], [event], root)
                self.assertEqual(record['render_bbox'], [0, 0, 11, 13])
                with pymupdf.open(pdf) as document:
                    page = document[0]
                    page.set_rotation(0)
                    self.assertTrue(page.rect.contains(pymupdf.Rect(record['source_bbox'])))

    def test_rotated_fragment_maps_and_traces_to_the_same_region(self):
        for rotation in (90, 180, 270):
            with self.subTest(rotation=rotation), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                result = root / 'result'
                result.mkdir()
                pdf, event = self.fixture(result, rotation, cropped=True)
                # Keep the source outside the sealed output, as required by trace.
                pdf = pdf.rename(root / 'source.pdf')
                text, (record,) = materialize(pdf, '<!-- PAGE 1 -->\n' + event['marker'], [event], result)
                (result / 'manual.md').write_text(text, encoding='utf-8')
                save(result / 'configuration.json', {'input_sha256': sha(pdf)})
                mapping = source_map.build(pdf, result, [])
                row, = mapping['objects']
                self.assertEqual(row['status'], 'ASSOCIATED_NATIVE_LAYOUT')
                self.assertEqual(row['sources'][0]['bbox'], record['source_bbox'])
                save(result / 'run_manifest.json', {'artifacts': inventory(result)})
                verify(result)
                source_map.validate(result)
                before = inventory(result)
                traced = source_map.trace(result, object_id=row['id'], pdf=pdf, render_dir=root / 'evidence')
                self.assertEqual(traced['objects'][0]['sources'][0]['bbox'], record['source_bbox'])
                self.assertEqual(inventory(result), before)
                with pymupdf.open(pdf) as document:
                    document[0].set_rotation(0)
                    full = document[0].get_pixmap(matrix=pymupdf.Matrix(2, 2), alpha=False)
                    expected = crop_pixels(full, record['source_bbox'], scale=2)
                actual = pymupdf.Pixmap(root / 'evidence' / traced['evidence'][0]['path'])
                self.assertEqual(actual.samples, expected)

    def test_glyph_inline_and_discarded_span_preservation(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'assets').mkdir()
            pdf = root / 'source.pdf'
            document = pymupdf.open()
            page = document.new_page()
            page.draw_rect((20, 20, 80, 40))
            page.draw_line((30, 25), (70, 35))
            document.save(pdf)
            document.close()
            event = {'kind': 'SOURCE_GLYPH_VISUAL_REQUIRED_NO_OCR', 'page': 1,
                     'source_bbox_points': [20, 20, 80, 40], 'marker': 'P2SOURCEGLYPHABC123'}
            text, records = materialize(pdf, '<!-- PAGE 1 -->\nText P2SOURCEGLYPHABC123 tail.', [event], root)
            self.assertNotIn(event['marker'], text)
            self.assertEqual(records[0]['placement'], 'INLINE_NATIVE_SPAN')
            self.assertEqual(len(check(text, root)), 1)
            text, records = materialize(pdf, '<!-- PAGE 1 -->\nOriginal unchanged text.', [event], root)
            self.assertIn('Original unchanged text.', text)
            self.assertEqual(records[0]['placement'], 'PAGE_SCOPED_SOURCE_FRAGMENT_ASSOCIATION_UNRESOLVED')
            self.assertFalse(records[0]['complete_formula_or_figure_claim'])

    def test_no_addition_when_source_text_already_exported(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'assets').mkdir()
            pdf = root / 'source.pdf'
            document = pymupdf.open()
            document.new_page().insert_text((20, 40), 'Original value 123')
            document.save(pdf)
            document.close()
            event = {'kind': 'SOURCE_GLYPH_VISUAL_REQUIRED_NO_OCR', 'page': 1,
                     'source_bbox_points': [19, 25, 200, 45], 'marker': 'P2SOURCEGLYPHABC123'}
            before = '<!-- PAGE 1 -->\nOriginal value 123'
            text, records = materialize(pdf, before, [event], root)
            self.assertEqual(text, before)
            self.assertFalse(records[0]['automatic_addition'])
            self.assertEqual(list((root / 'assets').iterdir()), [])


if __name__ == '__main__':
    unittest.main()
