import copy
from pathlib import Path
import tempfile
import unittest

import pymupdf

from pdf2md_p2.common import digest
from pdf2md_p2.fidelity import assess
from pdf2md_p2.fidelity_repair import apply, candidates, rollback
from pdf2md_p2.fidelity_verify import verify_candidate


class RepairTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.pdf = Path(self.temp.name) / 'source.pdf'
        with pymupdf.open() as document:
            page = document.new_page()
            page.insert_text((72, 90), 'X = C * (A - B)')
            page.insert_text((72, 120), 'Y = C * (A + B)')
            document.save(self.pdf)
        self.md = '<!-- PAGE 1 -->\n$$\nX=C*(A^{\\_}B)\n$$\n\n$$Y=C*(A+B)$$\n'
        self.assessment = assess(self.pdf, self.md)
        self.candidate, = candidates(self.md, self.assessment)

    def tearDown(self):
        self.temp.cleanup()

    def test_independent_verifier_accepts_exact_source_supported_repair(self):
        self.assertEqual(verify_candidate(self.pdf, self.md, self.candidate)['decision'], 'ACCEPTED')
        updated, log = apply(self.pdf, self.md, [self.candidate])
        self.assertIn('X=C*(A-B)', updated)
        self.assertIn('$$Y=C*(A+B)$$', updated)
        self.assertEqual(rollback(updated, log), self.md)
        self.assertEqual(len(log['operations']), 1)

    def test_outside_allowed_span_is_unchanged(self):
        updated, log = apply(self.pdf, self.md, [self.candidate])
        a, b = self.candidate['allowed_change_span']
        self.assertEqual(updated[:a], self.md[:a])
        self.assertEqual(updated[a+len(self.candidate['after_text']):], self.md[b:])

    def test_target_version_changed_is_rejected_without_edit(self):
        updated, log = apply(self.pdf, self.md+'\nChanged target.', [self.candidate])
        self.assertEqual(updated, self.md+'\nChanged target.')
        self.assertEqual(log['decisions'][0]['decision'], 'REJECTED')

    def test_candidate_that_changes_non_target_operand_is_rejected(self):
        candidate = copy.deepcopy(self.candidate)
        candidate['after_text'] = candidate['after_text'].replace('C*', 'D*')
        self.assertEqual(verify_candidate(self.pdf, self.md, candidate)['decision'], 'REJECTED')

    def test_candidate_still_missing_minus_is_rejected(self):
        candidate = copy.deepcopy(self.candidate)
        candidate['after_text'] = candidate['after_text'].replace('-', '+')
        self.assertEqual(verify_candidate(self.pdf, self.md, candidate)['decision'], 'REJECTED')

    def test_source_record_cannot_drop_glyphs(self):
        candidate = copy.deepcopy(self.candidate)
        candidate['source_evidence']['character_spans'] = [g for g in candidate['source_evidence']['character_spans'] if g['text'] != '-']
        self.assertEqual(verify_candidate(self.pdf, self.md, candidate)['decision'], 'REJECTED')

    def test_wrong_formula_target_rejected(self):
        candidate = copy.deepcopy(self.candidate)
        a, b = next((r['start'], r['end']) for r in self.assessment['output_formula_objects'] if r['name'] == 'Y')
        candidate.update(allowed_change_span=[a, b], before_text=self.md[a:b], expected_old_hash=digest(self.md[a:b]))
        self.assertEqual(verify_candidate(self.pdf, self.md, candidate)['decision'], 'REJECTED')

    def test_candidate_version_rejected(self):
        candidate = copy.deepcopy(self.candidate)
        candidate['source_version'] = 'other-version'
        self.assertEqual(verify_candidate(self.pdf, self.md, candidate)['decision'], 'REJECTED')

    def test_duplicate_source_formula_does_not_generate_candidate(self):
        other = Path(self.temp.name) / 'duplicate.pdf'
        with pymupdf.open() as document:
            page = document.new_page()
            page.insert_text((72, 90), 'X = C * (A - B)')
            page.insert_text((72, 120), 'X = C * (A + B)')
            document.save(other)
        self.assertEqual(candidates(self.md, assess(other, self.md)), [])

    def test_incomplete_source_does_not_generate_candidate(self):
        other = Path(self.temp.name) / 'incomplete.pdf'
        with pymupdf.open() as document:
            page = document.new_page()
            page.insert_text((72, 90), 'X = C * (A -')
            document.save(other)
        report = assess(other, self.md)
        self.assertEqual(candidates(self.md, report), [])
        self.assertTrue(any(r['assessment_status'] == 'SOURCE_INSUFFICIENT' for r in report['records']))

    def test_non_target_numeric_constant_must_not_change(self):
        other = Path(self.temp.name) / 'constant.pdf'
        with pymupdf.open() as document:
            page = document.new_page()
            page.insert_text((72, 90), 'X = 2 * (A - B)')
            document.save(other)
        original = self.md.replace('X=C*', 'X=2*')
        candidate, = candidates(original, assess(other, original))
        candidate['after_text'] = candidate['after_text'].replace('X=2*', 'X=3*')
        self.assertEqual(verify_candidate(other, original, candidate)['decision'], 'REJECTED')

    def test_independent_check_does_not_import_generator_or_source_parser(self):
        import pdf2md_p2.fidelity_verify as independent
        self.assertFalse(hasattr(independent, 'parse'))
        self.assertFalse(hasattr(independent, 'formulas'))
        self.assertFalse(hasattr(independent, 'candidates'))


if __name__ == '__main__':
    unittest.main()
