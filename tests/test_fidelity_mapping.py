import copy
from pathlib import Path
import tempfile
import unittest

import pymupdf

from pdf2md_p2.common import digest
from pdf2md_p2.fidelity import assess
from pdf2md_p2.fidelity_mapping import rebind
from pdf2md_p2.fidelity_repair import apply, candidates
from pdf2md_p2.source_map import build, validate


class RebindingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.pdf = self.root / 'source.pdf'
        self.output = self.root / 'output'
        self.output.mkdir()
        with pymupdf.open() as document:
            page = document.new_page()
            page.insert_text((72, 90), 'X = C * (A - B)')
            page.insert_text((72, 120), 'Later retained prose.')
            document.save(self.pdf)
        self.original = '<!-- PAGE 1 -->\n$$\nX=C*(A^{\\_}B)\n$$\n\nLater retained prose.\n'
        (self.output / 'manual.md').write_text(self.original, encoding='utf-8')
        self.mapping = build(self.pdf, self.output, [])
        self.planned = candidates(self.original, assess(self.pdf, self.original))
        self.updated, self.log = apply(self.pdf, self.original, self.planned)
        (self.output / 'manual.md').write_text(self.updated, encoding='utf-8')

    def tearDown(self):
        self.temp.cleanup()

    def test_rebinds_actual_offsets_and_verified_source_region(self):
        result = rebind(self.pdf, self.output, self.mapping, self.log, self.planned)
        self.assertEqual(validate(self.output), result)
        self.assertEqual(result['markdown_sha256'], digest(self.updated))
        formula, = [r for r in result['objects'] if r['kind'] == 'formula']
        self.assertEqual(formula['sources'][0]['basis'], 'INDEPENDENT_F1_SOURCE_GLYPH_VERIFICATION')
        for row in result['objects']:
            self.assertEqual(self.updated[row['start']:row['end']], row['text'])

    def test_unmodified_following_object_keeps_its_sources(self):
        result = rebind(self.pdf, self.output, self.mapping, self.log, self.planned)
        old = next(r for r in self.mapping['objects'] if 'Later retained' in r['text'])
        new = next(r for r in result['objects'] if 'Later retained' in r['text'])
        self.assertEqual(old['sources'], new['sources'])
        self.assertEqual(old['status'], new['status'])
        self.assertNotEqual(old['start'], new['start'])

    def test_different_mapping_version_rejected(self):
        mapping = copy.deepcopy(self.mapping)
        mapping['markdown_sha256'] = '0'*64
        with self.assertRaises(ValueError):
            rebind(self.pdf, self.output, mapping, self.log, self.planned)

    def test_changed_result_refused(self):
        (self.output / 'manual.md').write_text(self.updated+'Altered.', encoding='utf-8')
        with self.assertRaises(ValueError):
            rebind(self.pdf, self.output, self.mapping, self.log, self.planned)


if __name__ == '__main__':
    unittest.main()
