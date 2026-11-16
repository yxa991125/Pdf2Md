import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

import pymupdf

from pdf2md_p2.common import inventory, read, save, sha, verify
from pdf2md_p2.fidelity_result import publish, restore, validate_result


def fixture(root):
    pdf, result = root / 'source.pdf', root / 'R0'
    result.mkdir()
    with pymupdf.open() as document:
        page = document.new_page()
        page.insert_text((72, 90), 'X = C * (A - B)')
        page.insert_text((72, 120), 'Y = C * (A + B)')
        document.save(pdf)
    original = '<!-- PAGE 1 -->\n$$\nX=C*(A^{\\_}B)\n$$\n\n$$Y=C*(A+B)$$\n'
    (result / 'manual.md').write_text(original, encoding='utf-8')
    binding = {'version': '0.1.0a6', 'input_sha256': sha(pdf)}
    save(result / 'configuration.json', binding)
    save(result / 'quality_report.json', {'version': '0.1.0a6', 'status': 'SUCCESS_WITH_WARNINGS',
         'expected_pages': 1, 'completed_pages': [1], 'semantic_fidelity_certified': False})
    save(result / 'provenance.json', {'input_sha256': sha(pdf), 'chunks': [],
         'native_inference_performed_this_run': True})
    from pdf2md_p2.source_map import build
    build(pdf, result, [])
    save(result / 'run_manifest.json', {'configuration': binding, 'status': 'SUCCESS_WITH_WARNINGS',
                                      'artifacts': inventory(result)})
    return pdf, result


@unittest.skipUnless(sys.platform == 'linux', 'Linux/WSL2 publication')
class PublicationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.pdf, self.r0 = fixture(self.root)
        self.r1 = self.root / 'R1'
        self.before = inventory(self.r0)

    def tearDown(self):
        self.temp.cleanup()

    def test_complete_repair_publication_and_exact_result_rollback(self):
        result = publish(self.pdf, self.r0, self.r1, 'repair')
        self.assertEqual(result['status'], 'PUBLISHED')
        self.assertEqual(result['fidelity']['repaired'], 1)
        self.assertIn('X=C*(A-B)', (self.r1 / 'manual.md').read_text())
        verify(self.r1)
        validate_result(self.r1)
        self.assertEqual(inventory(self.r0), self.before)
        restored = self.root / 'restored'
        restore(self.r1, restored)
        self.assertEqual(inventory(restored), self.before)

    def test_check_mode_does_not_change_markdown(self):
        result = publish(self.pdf, self.r0, self.r1, 'check')
        self.assertEqual(result['fidelity']['repaired'], 0)
        self.assertEqual(sha(self.r0 / 'manual.md'), sha(self.r1 / 'manual.md'))
        self.assertGreater(result['fidelity']['mismatched'], 0)

    def test_resume_reuses_identical_result_and_does_not_apply_again(self):
        publish(self.pdf, self.r0, self.r1, 'repair')
        before = inventory(self.r1)
        result = publish(self.pdf, self.r0, self.r1, 'repair', True)
        self.assertEqual(result['status'], 'REUSED_VERIFIED')
        self.assertEqual(inventory(self.r1), before)
        self.assertEqual(result['fidelity']['repaired'], 1)

    def test_resume_rejects_different_mode(self):
        publish(self.pdf, self.r0, self.r1, 'repair')
        before = inventory(self.r1)
        with self.assertRaises(ValueError):
            publish(self.pdf, self.r0, self.r1, 'check', True)
        self.assertEqual(inventory(self.r1), before)

    def test_wrong_pdf_rejected(self):
        other = self.root / 'other.pdf'
        with pymupdf.open() as document:
            document.new_page().insert_text((72, 90), 'Different source.')
            document.save(other)
        with self.assertRaises(ValueError):
            publish(other, self.r0, self.r1, 'repair')
        self.assertFalse(self.r1.exists())

    def test_unsealed_parent_rejected(self):
        (self.r0 / 'manual.md').write_text('Tampered.')
        with self.assertRaises(ValueError):
            publish(self.pdf, self.r0, self.r1, 'repair')
        self.assertFalse(self.r1.exists())

    def test_concurrent_writer_rejected(self):
        import fcntl
        with (self.root / '.R1.fidelity.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaisesRegex(ValueError, 'Another fidelity'):
                publish(self.pdf, self.r0, self.r1, 'repair')

    def test_failed_publication_preserves_evidence_and_can_resume(self):
        with mock.patch('pdf2md_p2.fidelity_result._commit', side_effect=RuntimeError('injected failure')):
            with self.assertRaises(RuntimeError):
                publish(self.pdf, self.r0, self.r1, 'repair')
        self.assertFalse(self.r1.exists())
        self.assertTrue((self.root / '.R1.fidelity-stage').is_dir())
        self.assertEqual(inventory(self.r0), self.before)
        result = publish(self.pdf, self.r0, self.r1, 'repair', True)
        self.assertEqual(result['status'], 'PUBLISHED')
        self.assertEqual(result['fidelity']['repaired'], 1)
        self.assertEqual(len(list(self.root.glob('.R1.failed-stage-*.json'))), 1)

    def test_technical_summary_tampering_detected_even_if_resealed(self):
        publish(self.pdf, self.r0, self.r1, 'repair')
        report = read(self.r1 / 'fidelity_assessment.json')
        report['summary']['repaired'] = 99
        save(self.r1 / 'fidelity_assessment.json', report)
        with self.assertRaises(ValueError):
            validate_result(self.r1)

    def test_offset_tampering_detected(self):
        publish(self.pdf, self.r0, self.r1, 'repair')
        report = read(self.r1 / 'fidelity_assessment.json')
        row = next(r for r in report['records'] if r['output_location'])
        row['output_location']['start'] += 1
        save(self.r1 / 'fidelity_assessment.json', report)
        with self.assertRaises(ValueError):
            validate_result(self.r1)


if __name__ == '__main__':
    unittest.main()
