import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from pdf2md_p2.common import PRODUCT, inventory, read, save
from test_fidelity_result import fixture


@unittest.skipUnless(sys.platform == 'linux', 'Linux/WSL2 fidelity CLI')
class FidelityCLITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.pdf, self.r0 = fixture(self.root)
        self.r1 = self.root / 'R1'
        config = self.root / 'runtime.json'
        save(config, {'coordinator_python': sys.executable})
        self.env = {**os.environ, 'PDF2MD_CONFIG': str(config), 'PYTHONDONTWRITEBYTECODE': '1'}

    def tearDown(self):
        self.temp.cleanup()

    def run_cli(self, *args):
        return subprocess.run([sys.executable, '-B', str(PRODUCT / 'scripts/pdf2md.py'), *map(str, args)],
                              env=self.env, text=True, capture_output=True)

    def publish(self):
        result = self.run_cli('fidelity', '--input', self.pdf, '--result', self.r0,
                              '--output-dir', self.r1, '--mode', 'repair')
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_publish_verify_trace_and_whole_result_rollback(self):
        result = self.publish()
        self.assertEqual(result['fidelity']['repaired'], 1)
        verified = self.run_cli('verify', '--output-dir', self.r1)
        self.assertEqual(verified.returncode, 0, verified.stderr)
        self.assertEqual(json.loads(verified.stdout)['fidelity']['repaired'], 1)
        operation, = read(self.r1 / 'fidelity_repair_log.json')['operations']
        trace = self.run_cli('trace', '--output-dir', self.r1, '--object-id', operation['final_output_object_id'])
        self.assertEqual(trace.returncode, 0, trace.stderr)
        self.assertEqual(json.loads(trace.stdout)['objects'][0]['sources'][0]['page'], 1)
        technical = json.loads(trace.stdout)['technical_assessment']
        self.assertEqual(len(technical['repairs']), 1)
        self.assertTrue(any(r['assessment_status'] == 'SOURCE_MATCH' for r in technical['records']))
        restored = self.root / 'restored'
        rollback = self.run_cli('fidelity', '--result', self.r1, '--mode', 'rollback', '--output-dir', restored)
        self.assertEqual(rollback.returncode, 0, rollback.stderr)
        self.assertEqual(inventory(restored), inventory(self.r0))
        verified = self.run_cli('verify', '--output-dir', restored)
        self.assertEqual(verified.returncode, 0, verified.stderr)

    def test_missing_pdf_argument_has_parser_exit_two(self):
        result = self.run_cli('fidelity', '--result', self.r0, '--mode', 'repair')
        self.assertEqual(result.returncode, 2)
        self.assertFalse(self.r1.exists())

    def test_repeated_repair_is_verified_reuse(self):
        self.publish()
        before = inventory(self.r1)
        result = self.run_cli('fidelity', '--input', self.pdf, '--result', self.r0,
                              '--output-dir', self.r1, '--mode', 'repair', '--resume')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['status'], 'REUSED_VERIFIED')
        self.assertEqual(inventory(self.r1), before)

    def test_changed_mode_does_not_overwrite_existing_result(self):
        self.publish()
        before = inventory(self.r1)
        result = self.run_cli('fidelity', '--input', self.pdf, '--result', self.r0,
                              '--output-dir', self.r1, '--mode', 'check', '--resume')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(inventory(self.r1), before)


if __name__ == '__main__':
    unittest.main()
