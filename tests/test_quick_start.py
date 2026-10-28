import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from pdf2md_p2 import cli, configuration
from pdf2md_p2.common import save, sha


UUID = 'GPU-11111111-1111-1111-1111-111111111111'


class DefaultConfigurationTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.cwd = self.root / 'working'
        self.cwd.mkdir()
        self.product = self.root / 'Pdf2Md'
        self.product.mkdir()
        for target in (
                patch.dict(os.environ, {}, clear=True),
                patch.object(configuration, 'PRODUCT', self.product),
                patch.object(Path, 'cwd', return_value=self.cwd),
                patch.object(Path, 'home', return_value=self.root / 'home'),
                patch.object(configuration, 'resolve_device', side_effect=lambda value: value)):
            target.start()
            self.addCleanup(target.stop)

    def test_without_config_uses_current_python_and_sibling_models(self):
        result = configuration.load()
        self.assertEqual(result['parser_python'], sys.executable)
        self.assertEqual(result['coordinator_python'], sys.executable)
        self.assertEqual(result['model_path'], str(self.root / 'models'))
        self.assertEqual(result['authorized_gpu'], '0')
        self.assertIsNone(result['device_files'])

    def test_config_discovery_order(self):
        user = self.root / 'home/.config/pdf2md/runtime.json'
        source = self.product / 'runtime.local.json'
        local = self.cwd / 'runtime.local.json'
        for path in (user, source, local):
            save(path, {})
            self.assertEqual(configuration.find_config(), path)
        selected = self.root / 'explicit.json'
        with patch.dict(os.environ, {'PDF2MD_CONFIG': str(selected)}):
            self.assertEqual(configuration.find_config(), selected)
            with self.assertRaises(FileNotFoundError):
                configuration.load()

    def test_xdg_config_location(self):
        directory = self.root / 'xdg'
        path = directory / 'pdf2md/runtime.json'
        save(path, {})
        with patch.dict(os.environ, {'XDG_CONFIG_HOME': str(directory)}):
            self.assertEqual(configuration.find_config(), path)

    def test_partial_config_relative_paths_and_override_precedence(self):
        path = self.root / 'settings/runtime.json'
        save(path, {'model_path': 'models', 'authorized_gpu': '1'})
        with patch.dict(os.environ, {'PDF2MD_MODELS': str(self.root / 'env-models')}):
            result = configuration.load(path)
            self.assertEqual(result['model_path'], str(self.root / 'settings/models'))
            result = configuration.load(path, {'model_path': str(self.root / 'cli-models')})
            self.assertEqual(result['model_path'], str(self.root / 'cli-models'))
        self.assertEqual(result['authorized_gpu'], '1')

    def test_environment_paths_override_builtin_defaults(self):
        parser = str(self.root / 'backend/bin/python')
        models = str(self.root / 'custom-models')
        with patch.dict(os.environ, {'PDF2MD_MODELS': models, 'PDF2MD_PARSER_PYTHON': parser}):
            result = configuration.load()
        self.assertEqual(result['parser_python'], parser)
        self.assertEqual(result['model_path'], models)

    def test_explicit_config_wins_over_discovery_and_invalid_config_is_not_ignored(self):
        save(self.cwd / 'runtime.local.json', {'unsupported': True})
        explicit = self.root / 'chosen.json'
        save(explicit, {'authorized_gpu': '2'})
        self.assertEqual(configuration.load(explicit)['authorized_gpu'], '2')
        with self.assertRaisesRegex(ValueError, 'Unknown'):
            configuration.load()

    def test_gpu_override_resets_previous_device_nodes_for_detection(self):
        path = self.root / 'runtime.json'
        save(path, {'authorized_gpu': UUID,
            'device_files': ['/dev/nvidia0', '/dev/nvidiactl', '/dev/nvidia-uvm']})
        result = configuration.load(path, {'authorized_gpu': '1'})
        self.assertEqual(result['authorized_gpu'], '1')
        self.assertIsNone(result['device_files'])


class QuickStartCommandTests(unittest.TestCase):
    def test_current_and_a3_results_require_mapping_but_older_results_remain_verifiable(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            (output / 'manual.md').write_text('body\n', encoding='utf-8')
            save(output / 'quality_report.json', {'completed_pages': [1], 'expected_pages': 1})
            for version, expected in (('0.1.0a2', 0), ('0.1.0a3', 1), ('0.1.0a4', 1), ('0.1.0a5', 1)):
                with self.subTest(version=version), patch.object(cli, 'verify'):
                    save(output / 'run_manifest.json', {'configuration': {'version': version}})
                    self.assertEqual(self.call('verify', '--output-dir', output), expected)

    def call(self, *args):
        with patch.object(sys, 'argv', ['pdf2md.py', *map(str, args)]), \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return cli.main()

    def test_minimal_convert_routes_to_checks_and_sandbox_with_named_output(self):
        with tempfile.TemporaryDirectory() as name:
            pdf = Path(name) / 'manual.pdf'
            pdf.write_bytes(b'input fixture')
            config = {'deployment': 'resolved'}
            with patch.object(cli, 'runtime', return_value=config), \
                    patch('pdf2md_p2.environment.inspect', return_value={
                        'ready_for_conversion': True, 'environment_fingerprint': 'test'}) as inspect, \
                    patch.object(cli, 'coordinator', return_value=['sandbox', 'worker']) as worker, \
                    patch.object(cli.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0)):
                self.assertEqual(self.call('convert', '--input', pdf), 0)
            inspect.assert_called_once_with(config)
            self.assertEqual(worker.call_args.args[2], Path(name) / 'manual_pdf2md')
            self.assertTrue((Path(name) / 'manual_pdf2md/environment_report.json').is_file())

    def test_existing_default_output_not_overwritten_or_inferred_again(self):
        with tempfile.TemporaryDirectory() as name:
            pdf = Path(name) / 'manual.pdf'
            pdf.write_bytes(b'input fixture')
            output = Path(name) / 'manual_pdf2md'
            output.mkdir()
            with patch.object(cli, 'runtime', return_value={}), \
                    patch('pdf2md_p2.environment.inspect') as inspect:
                self.assertEqual(self.call('convert', '--input', pdf), 1)
            inspect.assert_not_called()

    def test_explicit_output_and_legacy_flags_still_work(self):
        with tempfile.TemporaryDirectory() as name:
            pdf = Path(name) / 'manual.pdf'
            pdf.write_bytes(b'input fixture')
            output = Path(name) / 'chosen'
            with patch.object(cli, 'convert', return_value=0) as convert:
                self.assertEqual(self.call('convert', '--input', pdf, '--output-dir', output,
                    '--offline', '--config', Path(name) / 'runtime.json', '--resume'), 0)
            args = convert.call_args.args[0]
            self.assertEqual(args.output_dir, output)
            self.assertTrue(args.offline)
            self.assertTrue(args.resume)

    def test_minimal_batch_gets_output_name_and_offline_default(self):
        with tempfile.TemporaryDirectory() as name:
            inputs = Path(name) / 'pdfs'
            with patch('pdf2md_p2.batch.convert_directory', return_value=0) as convert:
                self.assertEqual(self.call('batch', '--input-dir', inputs), 0)
            args = convert.call_args.args[0]
            self.assertEqual(args.output_dir, Path(name) / 'pdfs_pdf2md')
            self.assertTrue(args.offline)
            self.assertIsNone(args.config)

    def test_source_tree_inputs_write_outside_source(self):
        with tempfile.TemporaryDirectory() as name, patch.object(cli, 'PRODUCT', Path(name) / 'Pdf2Md'):
            self.assertEqual(cli.default_output(Path(name) / 'Pdf2Md/manual.pdf'),
                Path(name) / 'pdf2md-output/manual_pdf2md')

    def test_init_config_needs_no_path_arguments(self):
        with tempfile.TemporaryDirectory() as name:
            path = Path(name) / 'runtime.json'
            self.assertEqual(self.call('init-config', '--config', path), 0)
            data = json.loads(path.read_text(encoding='utf-8'))
            self.assertEqual(data['parser_python'], sys.executable)
            self.assertEqual(data['authorized_gpu'], '0')

    def test_doctor_needs_no_config_argument(self):
        with patch.object(cli, 'runtime', return_value={}) as runtime, \
                patch('pdf2md_p2.environment.inspect', return_value={'ready_for_conversion': True}):
            self.assertEqual(self.call('doctor'), 0)
        self.assertIsNone(runtime.call_args.args[0].config)

    def test_batch_children_use_resolved_snapshot_not_rediscovery(self):
        from pdf2md_p2 import batch
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            inputs = root / 'pdfs'
            inputs.mkdir()
            (inputs / 'a.pdf').write_bytes(b'fixture')
            resolved = {'authorized_gpu': UUID, 'coordinator_python': sys.executable}
            args = SimpleNamespace(input_dir=inputs, output_dir=root / 'result',
                config=None, recursive=False, resume=False)
            captured = []

            def run(argv, **kwargs):
                selected = Path(argv[argv.index('--config') + 1])
                captured.append(json.loads(selected.read_text(encoding='utf-8')))
                output = Path(argv[argv.index('--output-dir') + 1])
                save(output / 'run_manifest.json', {'configuration': {'input_sha256': sha(inputs / 'a.pdf')}})
                return subprocess.CompletedProcess(argv, 0)

            lock = SimpleNamespace(LOCK_EX=1, LOCK_NB=2, flock=lambda *args: None)
            with patch.object(batch.platform, 'system', return_value='Linux'), \
                    patch.dict(sys.modules, {'fcntl': lock}), patch.object(cli, 'runtime', return_value=resolved), \
                    patch.object(batch.subprocess, 'run', side_effect=run), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(batch.convert_directory(args), 0)
            self.assertEqual(captured, [resolved])


if __name__ == '__main__':
    unittest.main()
