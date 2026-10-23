from pathlib import Path
import tempfile
import unittest
import html

import pymupdf

from pdf2md_p2.text_pages import recover


class SourcePageTests(unittest.TestCase):
    def test_short_trailing_paragraph_does_not_hide_missing_full_page(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            pdf = root / 'source.pdf'
            document = pymupdf.open()
            page = document.new_page()
            page.insert_textbox((30, 40, 580, 200), 'Original source text and literal values 3.3 V. ' * 12)
            page.insert_text((30, 240), 'A short final paragraph with sixty characters remains source text.')
            document.save(pdf)
            document.close()
            before = '<!-- PAGE 1 -->\n'
            after, rows = recover(pdf, before, root)
            self.assertEqual(len(rows), 1)
            row, = rows
            self.assertEqual(after[:row['start']] + row['before'] + after[row['start'] + len(row['after']):], before)

    def test_two_column_source_does_not_get_linear_text_fallback(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            pdf = root / 'source.pdf'
            document = pymupdf.open()
            page = document.new_page()
            page.insert_textbox((30, 40, 260, 350), 'Left source text DATA_STATUS 3.3 V. ' * 10)
            page.insert_textbox((320, 40, 560, 350), 'Right source text DATA_READY 1.8 V. ' * 10)
            document.save(pdf)
            document.close()
            before = '<!-- PAGE 1 -->\n'
            after, rows = recover(pdf, before, root)
            self.assertEqual(after, before)
            self.assertEqual(rows, [])

    def test_empty_source_text_page_recovered_without_overwriting_correct_content(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            pdf = root / 'source.pdf'
            document = pymupdf.open()
            page = document.new_page()
            page.insert_textbox((30, 40, 580, 300), 'Original source 3.3 V and DATA_STATUS must remain literal. ' * 7)
            document.save(pdf)
            document.close()
            after, rows = recover(pdf, '<!-- PAGE 1 -->\n\n', root)
            self.assertEqual(len(rows), 1)
            self.assertIn('DATA_STATUS', after)
            self.assertEqual(html.unescape(rows[0]['serialized'][5:-6]), rows[0]['source_text'])
            before = '<!-- PAGE 1 -->\nOriginal correct native output'
            after, rows = recover(pdf, before, root)
            self.assertEqual(after, before)
            self.assertEqual(rows, [])


if __name__ == '__main__':
    unittest.main()
