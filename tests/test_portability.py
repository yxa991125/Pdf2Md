import copy
import json
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from pdf2md_p2 import configuration, environment
from pdf2md_p2.common import PRODUCT, save, sha

UUID = 'GPU-11111111-1111-1111-1111-111111111111'
MIG = 'MIG-22222222-2222-2222-2222-222222222222'


def config():
    return {'coordinator_python': 'coordinator/bin/python', 'parser_python': 'parser/bin/python',
        'model_path': 'models', 'authorized_gpu': UUID,
        'device_files': ['/dev/nvidia0', '/dev/nvidiactl', '/dev/nvidia-uvm']}


class ConfigurationTests(unittest.TestCase):
    @unittest.skipUnless(sys.platform == 'linux', 'Linux virtualenv executable layout')
    def test_virtualenv_interpreter_symlink_is_not_resolved_to_base_python(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            python = root / 'coordinator/bin/python'
            python.parent.mkdir(parents=True)
            python.symlink_to(sys.executable)
            loaded = configuration.validate(config(), root)
            self.assertEqual(loaded['coordinator_python'], str(python))
            self.assertNotEqual(loaded['coordinator_python'], str(python.resolve()))
    def test_paths_are_resolved_relative_to_config_not_current_directory(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            save(root / 'deployment/runtime.json', config())
            loaded = configuration.load(root / 'deployment/runtime.json')
            self.assertEqual(loaded['model_path'], str((root / 'deployment/models').resolve()))

    def test_another_explicit_full_gpu_uuid_is_supported(self):
        self.assertEqual(configuration.validate(config())['authorized_gpu'], UUID)

    def test_mig_needs_capability_authorization(self):
        value = {**config(), 'authorized_gpu': MIG}
        with self.assertRaisesRegex(ValueError, 'capability'):
            configuration.validate(value)
        value['device_files'] += ['/dev/nvidia-caps/nvidia-cap44', '/dev/nvidia-caps/nvidia-cap45']
        self.assertEqual(configuration.validate(value)['authorized_gpu'], MIG)

    def test_visible_gpu_index_is_supported(self):
        self.assertEqual(configuration.validate({**config(), 'authorized_gpu': '0'})['authorized_gpu'], '0')

    def test_more_than_one_physical_device_refused(self):
        value = config()
        value['device_files'].append('/dev/nvidia1')
        with self.assertRaisesRegex(ValueError, 'exactly one'):
            configuration.validate(value)

    def test_arbitrary_mount_or_device_path_refused(self):
        value = config()
        value['device_files'].append('/dev/sda')
        with self.assertRaises(ValueError):
            configuration.validate(value)

    def test_unknown_configuration_key_refused(self):
        with self.assertRaisesRegex(ValueError, 'Unknown'):
            configuration.validate({**config(), 'disable_sandbox': True})

    def test_ocr_and_fusion_cannot_be_enabled(self):
        for key, value in [('parse_mode', 'auto'), ('source_enhancement', True), ('multi_parser_fusion', True)]:
            with self.subTest(key=key), self.assertRaises(ValueError):
                configuration.validate({**config(), key: value})

    def test_numeric_configuration_rejects_booleans(self):
        with self.assertRaises(ValueError):
            configuration.validate({**config(), 'chunk_size': True})

    def test_negative_retry_or_zero_chunk_refused(self):
        for key, value in [('chunk_size', 0), ('max_retries', -1), ('worker_timeout_seconds', 0)]:
            with self.subTest(key=key), self.assertRaises(ValueError):
                configuration.validate({**config(), key: value})

    def test_missing_path_and_duplicate_devices_refused(self):
        value = config()
        del value['model_path']
        with self.assertRaises(ValueError):
            configuration.validate(value)
        value = config()
        value['device_files'].append('/dev/nvidia0')
        with self.assertRaises(ValueError):
            configuration.validate(value)


class CommandTests(unittest.TestCase):
    def run_cli(self, *args):
        return subprocess.run([sys.executable, '-I', '-B', '-S', str(PRODUCT / 'scripts/pdf2md.py'), *args],
            capture_output=True, text=True)

    def test_help_and_version_require_no_site_packages(self):
        result = self.run_cli('--help')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('doctor', result.stdout)
        self.assertIn('batch', result.stdout)
        self.assertEqual(self.run_cli('--version').stdout.strip(), '0.1.0a1')

    def test_init_config_never_overwrites_existing_file(self):
        with tempfile.TemporaryDirectory() as name:
            path = Path(name) / 'runtime.json'
            args = ['init-config', '--config', str(path), '--parser-python', sys.executable,
                '--models', str(Path(name) / 'models'), '--gpu', UUID]
            for node in config()['device_files']:
                args += ['--device', node]
            first = self.run_cli(*args)
            self.assertEqual(first.returncode, 0, first.stderr)
            original = path.read_bytes()
            self.assertNotEqual(self.run_cli(*args).returncode, 0)
            self.assertEqual(path.read_bytes(), original)

    def test_init_config_defaults_need_no_uuid_or_device_arguments(self):
        with tempfile.TemporaryDirectory() as name:
            path = Path(name) / 'runtime.json'
            result = self.run_cli('init-config', '--config', str(path), '--parser-python', sys.executable,
                '--models', str(Path(name) / 'models'))
            self.assertEqual(result.returncode, 0, result.stderr)
            data = json.loads(path.read_text(encoding='utf-8'))
            self.assertEqual(data['authorized_gpu'], '0')
            self.assertIsNone(data['device_files'])

    def test_missing_input_is_reported_without_loading_dependencies(self):
        with tempfile.TemporaryDirectory() as name:
            result = self.run_cli('convert', '--input', str(Path(name) / 'missing.pdf'),
                '--output-dir', str(Path(name) / 'output'), '--offline')
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('Input PDF missing', result.stderr)
            self.assertNotIn('ModuleNotFoundError', result.stderr)


class DoctorTests(unittest.TestCase):
    def inspect(self, wrong_version=False, platform_name='Linux', wrong_prefix=False):
        value = configuration.validate(config())
        def command(argv, **kwargs):
            if argv[0] == 'nvidia-smi':
                return 'Device (' + UUID + ')'
            if argv[0] == 'namespace':
                return json.dumps({'unexpected_interfaces': []})
            deps = environment.COORDINATOR if argv[0] == value['coordinator_python'] else environment.PARSER
            packages = list(deps.items())
            if wrong_version:
                packages[0] = (packages[0][0], 'unvalidated-version')
            return json.dumps({'python': '3.12.15', 'packages': packages, 'executable': argv[0], 'base_prefix': 'base',
                'prefix': '/wrong-wide-prefix' if wrong_prefix else str(Path(argv[0]).parent.parent)})
        binding = {'worker_sha256': sha(PRODUCT / 'vendor/gpu_parser_worker.py'), 'models': {}}
        with patch.object(environment, 'run', side_effect=command), \
                patch.object(environment, 'read', return_value=binding), \
                patch.object(environment.platform, 'system', return_value=platform_name), \
                patch.object(environment.os, 'access', return_value=True), \
                patch.object(Path, 'stat', return_value=SimpleNamespace(st_mode=stat.S_IFCHR)), \
                patch('pdf2md_p2.sandbox.base', return_value=['namespace']):
            return environment.inspect(value)

    def test_complete_mock_environment_is_ready_without_inference(self):
        result = self.inspect()
        self.assertTrue(result['ready_for_conversion'])
        self.assertFalse(result['GPU_inference_performed'])
        self.assertFalse(result['semantic_fidelity_certified'])

    def test_wrong_dependency_version_not_silently_accepted(self):
        self.assertFalse(self.inspect(wrong_version=True)['ready_for_conversion'])

    def test_python_wrapper_does_not_cause_user_home_to_be_mounted(self):
        self.assertFalse(self.inspect(wrong_prefix=True)['ready_for_conversion'])

    def test_windows_is_not_reported_conversion_ready(self):
        self.assertFalse(self.inspect(platform_name='Windows')['ready_for_conversion'])

    def test_fingerprint_is_stable_without_timestamps(self):
        self.assertEqual(self.inspect()['environment_fingerprint'], self.inspect()['environment_fingerprint'])

    def test_native_cache_environment_change_is_not_accepted(self):
        from pdf2md_p2.native_cache import load
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            save(root / 'configuration.json', {'environment_fingerprint': 'old'})
            with self.assertRaisesRegex(ValueError, 'environment fingerprint'):
                load(root, root / 'unused.pdf', root / 'out', {}, {'environment_fingerprint': 'new'}, {})


@unittest.skipUnless(sys.platform == 'linux', 'Real batch lock is Linux-only')
class BatchTests(unittest.TestCase):
    def test_one_failed_pdf_does_not_stop_other_jobs_and_resume_preserves_logs(self):
        from pdf2md_p2 import batch
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            inputs = root / 'inputs'
            inputs.mkdir()
            for name in ('a.pdf', 'b.pdf', 'c.pdf'):
                (inputs / name).write_bytes(name.encode())
            runtime = root / 'runtime.json'
            save(runtime, config())
            args = SimpleNamespace(input_dir=inputs, output_dir=root / 'outputs', config=runtime,
                recursive=False, resume=False)
            calls = []
            def run(argv, **kwargs):
                source = Path(argv[argv.index('--input') + 1])
                output = Path(argv[argv.index('--output-dir') + 1])
                calls.append(source.name)
                failed = source.name == 'b.pdf'
                save(output / 'run_status.json', {'status': 'FAILED' if failed else 'SUCCESS_WITH_WARNINGS'})
                if not failed:
                    save(output / 'run_manifest.json', {'configuration': {'input_sha256': sha(source)}})
                return subprocess.CompletedProcess(argv, 2 if failed else 0)
            with patch.object(batch.subprocess, 'run', side_effect=run):
                self.assertEqual(batch.convert_directory(args), 2)
                args.resume = True
                self.assertEqual(batch.convert_directory(args), 2)
                (inputs / 'd.pdf').write_bytes(b'new')
                with self.assertRaisesRegex(ValueError, 'changed'):
                    batch.convert_directory(args)
            self.assertEqual(calls, ['a.pdf', 'b.pdf', 'c.pdf'] * 2)
            self.assertEqual(len(list((root / 'outputs/logs').glob('*.stdout.log'))), 6)
            self.assertEqual(len(list((root / 'outputs/batch_attempts').glob('*.json'))), 2)

    def test_changed_input_cannot_keep_original_batch_hash_with_success_status(self):
        from pdf2md_p2 import batch
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            inputs = root / 'inputs'
            inputs.mkdir()
            source = inputs / 'a.pdf'
            source.write_bytes(b'original input')
            runtime = root / 'runtime.json'
            save(runtime, config())
            args = SimpleNamespace(input_dir=inputs, output_dir=root / 'outputs', config=runtime,
                recursive=False, resume=False)
            def run(argv, **kwargs):
                output = Path(argv[argv.index('--output-dir') + 1])
                source.write_bytes(b'changed actual input')
                save(output / 'run_status.json', {'status': 'SUCCESS_WITH_WARNINGS'})
                save(output / 'run_manifest.json', {'configuration': {'input_sha256': sha(source)}})
                return subprocess.CompletedProcess(argv, 0)
            with patch.object(batch.subprocess, 'run', side_effect=run):
                self.assertEqual(batch.convert_directory(args), 2)
            row, = json.loads((root / 'outputs/batch_summary.json').read_text())['documents']
            self.assertEqual(row['status'], 'FAILED_INPUT_BINDING')
            self.assertFalse(row['integrity_sealed'])


if __name__ == '__main__':
    unittest.main()
