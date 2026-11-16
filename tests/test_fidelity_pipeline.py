import errno
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from pdf2md_p2 import __version__
from pdf2md_p2.common import implementation, inventory, read, save, sha, verify
from pdf2md_p2.fidelity_pipeline import command, exchange, finish, pipeline_lock, recover, transaction_path
from pdf2md_p2.fidelity_result import publish, validate_result
from test_fidelity_result import fixture


@unittest.skipUnless(sys.platform == 'linux', 'Linux/WSL2 fidelity pipeline')
class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.pdf, self.result = fixture(self.root)
        configuration = read(self.result / 'configuration.json')
        configuration.update(version=__version__, fidelity_mode='repair', implementation=implementation())
        save(self.result / 'configuration.json', configuration)
        save(self.result / 'run_manifest.json', {'configuration': configuration, 'status': 'SUCCESS_WITH_WARNINGS',
                                                'artifacts': {k: v for k, v in inventory(self.result).items() if k != 'run_manifest.json'}})
        self.original = inventory(self.result)
        self.config = {'coordinator_python': sys.executable, 'parser_python': sys.executable,
                       'cpu_python': sys.executable, 'device_files': ['/dev/forbidden-gpu'],
                       'resolved_compute': 'cuda', 'authorized_gpu': '0', 'bubblewrap_path': '/bin/true'}

    def tearDown(self):
        self.temp.cleanup()

    def executor(self, argv, **kwargs):
        transaction = transaction_path(self.result)
        with mock.patch('pdf2md_p2.fidelity_sandbox.probe', return_value={'unit_test_executor': True}):
            publish(self.pdf, self.result, transaction / 'R1', 'repair', True, inline=True)
        return subprocess.CompletedProcess(argv, 0)

    def test_complete_directory_publication_preserves_raw_version(self):
        with mock.patch('pdf2md_p2.fidelity_pipeline.subprocess.run', side_effect=self.executor):
            result = finish(self.config, self.pdf, self.result, 'repair')
        self.assertEqual(result['fidelity']['repaired'], 1)
        state = read(transaction_path(self.result) / 'pipeline.json')
        raw = Path(state['raw_result'])
        self.assertEqual(inventory(raw), self.original)
        verify(self.result)
        validate_result(self.result)

    def test_reuse_does_not_reinvoke_fidelity_executor(self):
        with mock.patch('pdf2md_p2.fidelity_pipeline.subprocess.run', side_effect=self.executor):
            finish(self.config, self.pdf, self.result, 'repair')
        before = inventory(self.result)
        with mock.patch('pdf2md_p2.fidelity_pipeline.subprocess.run', side_effect=AssertionError('executed twice')):
            reused = finish(self.config, self.pdf, self.result, 'repair')
        self.assertEqual(reused['status'], 'REUSED_VERIFIED')
        self.assertEqual(inventory(self.result), before)

    def test_failed_fidelity_keeps_coherent_raw_directory(self):
        with mock.patch('pdf2md_p2.fidelity_pipeline.subprocess.run', return_value=subprocess.CompletedProcess([], 1)):
            with self.assertRaisesRegex(ValueError, 'raw result'):
                finish(self.config, self.pdf, self.result, 'repair')
        self.assertEqual(inventory(self.result), self.original)
        self.assertEqual(read(transaction_path(self.result) / 'pipeline.json')['status'], 'FAILED_RAW_RESULT_RETAINED')

    def test_interrupted_two_rename_publication_restores_raw_on_resume(self):
        transaction = transaction_path(self.result)
        transaction.mkdir()
        raw_hash = sha(self.result / 'run_manifest.json')
        self.result.rename(transaction / 'R0')
        save(transaction / 'pipeline.json', {'status': 'EXCHANGING', 'binding': {'input_sha256': sha(self.pdf)},
             'raw_seal_sha256': raw_hash, 'new_seal_sha256': '0'*64})
        recover(self.result, self.pdf, True)
        self.assertEqual(inventory(self.result), self.original)
        self.assertEqual(read(transaction / 'pipeline.json')['status'], 'RAW_RESTORED')

    def test_interrupted_publication_requires_explicit_resume(self):
        transaction = transaction_path(self.result)
        transaction.mkdir()
        save(transaction / 'pipeline.json', {'status': 'EXCHANGING', 'binding': {'input_sha256': sha(self.pdf)},
             'raw_seal_sha256': sha(self.result / 'run_manifest.json'), 'new_seal_sha256': '0'*64})
        with self.assertRaises(ValueError):
            recover(self.result, self.pdf, False)

    def test_pipeline_lock_rejects_other_conversion(self):
        with pipeline_lock(self.result):
            with self.assertRaisesRegex(ValueError, 'Another conversion'):
                with pipeline_lock(self.result):
                    pass

    def test_postprocessing_command_has_no_gpu_mount_and_full_namespaces(self):
        argv = command(self.config, self.pdf, self.result, self.root / 'transaction', 'repair')
        self.assertIn('--unshare-all', argv)
        self.assertNotIn('/dev/forbidden-gpu', argv)
        self.assertNotIn('/models', argv)
        self.assertIn('PDF2MD_INLINE_FIDELITY', argv)
        self.assertIn('/original', argv)


if __name__ == '__main__':
    unittest.main()
