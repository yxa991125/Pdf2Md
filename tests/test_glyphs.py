from pathlib import Path
import tempfile
import unittest

import pymupdf

from pdf2md_p2.glyphs import materialize
from pdf2md_p2.assets import check


class GlyphTests(unittest.TestCase):
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
