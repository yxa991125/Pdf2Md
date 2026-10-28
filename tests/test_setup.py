import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from pdf2md_p2 import bootstrap, setup, setup_resources as resources
from pdf2md_p2.common import save, sha


class SetupTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.paths = {key: self.root / key for key in ('data', 'state', 'config')}
        self.paths['config'] /= 'runtime.json'
        for item in (patch.object(setup, 'locations', return_value=self.paths),
                     patch.object(setup.platform, 'system', return_value='Linux'),
                     patch.object(setup, 'configuration_path', return_value=None),
                     patch.object(setup, 'python312', return_value='/usr/bin/python3.12'),
                     patch.object(setup.shutil, 'which', return_value='/usr/bin/bwrap'),
                     patch.object(setup.os, 'access', return_value=True),
                     patch.object(setup.Path, 'is_file', autospec=True, side_effect=lambda p: p.name == 'bwrap' or p.exists())):
            item.start()
            self.addCleanup(item.stop)

    def args(self, *args):
        return setup.parser().parse_args(['--compute', 'cpu', *map(str, args)])

    def call(self, *args):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), patch.object(sys.stdin, 'isatty', return_value=False):
            code = setup.main(['--compute', 'cpu', *map(str, args)])
        return code, json.loads(out.getvalue())

    def test_check_is_read_only_and_requires_approval(self):
        code, value = self.call('--check', '--json')
        self.assertEqual(code, 3)
        self.assertEqual(value['actual_compute'], 'cpu')
        self.assertFalse(any(self.root.iterdir()))

    def test_noninteractive_never_installs_without_yes(self):
        with patch.object(setup, 'Installer') as installer:
            code, _ = self.call()
        self.assertEqual(code, 3)
        installer.assert_not_called()
        self.assertFalse(any(self.root.iterdir()))

    def test_check_yes_still_cannot_install(self):
        with patch.object(setup, 'Installer') as installer:
            self.assertEqual(self.call('--check', '--yes')[0], 3)
        installer.assert_not_called()

    def test_missing_python_is_action_not_install(self):
        with patch.object(setup, 'python312', return_value=None):
            code, value = self.call('--yes')
        self.assertEqual(code, 4)
        self.assertIn('Python 3.12', value['missing'][0])
        self.assertFalse(any(self.root.iterdir()))

    def test_malformed_manual_configuration_is_not_ignored(self):
        path = self.root / 'manual.json'
        path.write_text('{broken', encoding='utf-8')
        before = sha(path)
        self.assertEqual(self.call('--config', path, '--yes')[0], 1)
        self.assertEqual(sha(path), before)

    def test_unready_manual_configuration_is_not_overwritten(self):
        path = self.root / 'manual.json'
        save(path, {})
        with patch.object(setup, 'inspect_config', side_effect=ValueError('missing runtime')):
            code, value = self.call('--config', path, '--yes')
        self.assertEqual(code, 4)
        self.assertIn('not be overwritten', value['missing'][0])
        self.assertEqual(json.loads(path.read_text()), {})

    def test_new_explicit_configuration_destination(self):
        target = self.root / 'new/runtime.json'
        value = setup.build_plan(self.args('--config', target))
        self.assertEqual(value['config_path'], str(target))
        self.assertEqual(value['status'], 'NEEDS_APPROVAL')
        self.assertFalse(target.exists())

    def test_next_command_preserves_authorized_mode_and_media(self):
        media = self.root / 'wheels with spaces'
        media.mkdir()
        args = self.args('--offline', '--wheelhouse', media, '--config', self.root / 'new.json')
        import shlex
        command = shlex.split(setup.build_plan(args)['next_command'])
        self.assertEqual(command[command.index('--compute') + 1], 'cpu')
        self.assertIn('--offline', command)
        self.assertEqual(command[command.index('--wheelhouse') + 1], str(media))

    def test_ready_configuration_is_idempotent(self):
        target = self.paths['config']
        save(target, {})
        with patch.object(setup, 'inspect_config', return_value=(
                {'resolved_compute': 'cpu', 'coordinator_python': '/runtime/bin/python'}, {})):
            code, value = self.call('--config', target, '--yes')
        self.assertEqual(code, 0)
        self.assertEqual(value['coordinator_python'], '/runtime/bin/python')
        self.assertFalse(self.paths['state'].exists())

    def test_managed_environment_can_resume_after_failed_install(self):
        target = self.paths['config']
        save(target, {})
        save(self.paths['state'] / 'active.json', {'config_path': str(target),
            'config_sha256': sha(target), 'source_fingerprint': 'old'})
        with patch.object(setup, 'inspect_config', side_effect=ValueError('incomplete')):
            value = setup.build_plan(self.args('--config', target))
        self.assertEqual(value['status'], 'NEEDS_APPROVAL')
        self.assertEqual(value['expected_config_sha256'], sha(target))

    def test_offline_requires_explicit_dependency_media(self):
        self.assertEqual(self.call('--offline', '--yes')[0], 4)

    def test_invalid_media_is_not_ignored(self):
        code, value = self.call('--wheelhouse', self.root / 'missing')
        self.assertEqual(code, 1)
        self.assertIn('Wheelhouse', value['error'])

    def test_concurrent_installer_is_refused(self):
        fake = type('Lock', (), {'LOCK_EX': 1, 'LOCK_NB': 2,
            'flock': lambda *a: (_ for _ in ()).throw(BlockingIOError())})()
        with patch.dict(sys.modules, {'fcntl': fake}), patch.object(setup, 'Installer') as installer:
            code, value = self.call('--yes')
        self.assertEqual(code, 1)
        self.assertIn('concurrent', value['error'])
        installer.assert_not_called()

    def test_gpu_failure_never_falls_back_to_cpu(self):
        with patch.object(setup, 'resolve_device', side_effect=ValueError('GPU denied')):
            value = setup.build_plan(setup.parser().parse_args(['--compute', 'cuda']))
        self.assertEqual(value['actual_compute'], 'cuda')
        self.assertEqual(value['status'], 'NEEDS_ACTION')

    def test_explicit_other_device_does_not_reuse_existing_config(self):
        target = self.paths['config']
        save(target, {'compute': 'cuda'})
        original = {'resolved_compute': 'cuda', 'coordinator_python': '/runtime/bin/python',
            'authorized_gpu': 'GPU-11111111-1111-1111-1111-111111111111', 'device_files': ['/dev/nvidia0']}
        with patch.object(setup, 'inspect_config', return_value=(original, {})), \
                patch.object(setup, 'resolve_device', return_value={
                    'authorized_gpu': 'GPU-22222222-2222-2222-2222-222222222222', 'device_files': ['/dev/nvidia1']}):
            value = setup.build_plan(setup.parser().parse_args(['--config', str(target), '--gpu', '1']))
        self.assertEqual(value['status'], 'NEEDS_ACTION')
        self.assertEqual(json.loads(target.read_text())['compute'], 'cuda')

    def test_invalid_receipt_is_a_structured_failure(self):
        save(self.paths['state'] / 'active.json', [])
        code, value = self.call('--check')
        self.assertEqual(code, 1)
        self.assertIn('receipt', value['error'])

    def test_revalidation_retains_existing_cpu_choice(self):
        target = self.paths['config']
        save(target, {'compute': 'cpu'})
        save(self.paths['state'] / 'active.json', {'config_path': str(target),
            'config_sha256': sha(target), 'source_fingerprint': 'old'})
        with patch.object(setup, 'inspect_config', side_effect=ValueError('needs validation')), \
                patch.object(setup.subprocess, 'run') as run:
            value = setup.build_plan(setup.parser().parse_args(['--config', str(target)]))
        self.assertEqual(value['actual_compute'], 'cpu')
        run.assert_not_called()

    def test_revalidation_retains_existing_gpu_authorization(self):
        target = self.paths['config']
        gpu = 'MIG-11111111-1111-1111-1111-111111111111'
        devices = ['/dev/nvidia0', '/dev/nvidiactl', '/dev/nvidia-uvm',
                   '/dev/nvidia-caps/nvidia-cap12', '/dev/nvidia-caps/nvidia-cap13']
        save(target, {'compute': 'cuda', 'authorized_gpu': gpu, 'device_files': devices})
        save(self.paths['state'] / 'active.json', {'config_path': str(target),
            'config_sha256': sha(target), 'source_fingerprint': 'old'})
        with patch.object(setup, 'inspect_config', side_effect=ValueError('needs validation')), \
                patch.object(setup, 'resolve_device', side_effect=lambda value: value):
            value = setup.build_plan(setup.parser().parse_args(['--config', str(target)]))
        self.assertEqual(value['actual_compute'], 'cuda')
        self.assertEqual(value['authorized_gpu'], gpu)
        self.assertEqual(set(value['device_files']), set(devices))


class ResourceTests(unittest.TestCase):
    def test_manifest_covers_all_frozen_payloads(self):
        manifest, bindings = resources.models_manifest()
        self.assertEqual(len(bindings), 13)
        self.assertEqual(set(bindings), set(manifest['sizes']))
        self.assertEqual(len(manifest['revision']), 40)

    def test_non_https_is_rejected_without_writing(self):
        with tempfile.TemporaryDirectory() as name:
            target = Path(name) / 'resource'
            with self.assertRaisesRegex(ValueError, 'HTTPS'):
                resources.download('http://example.org/file', target, 'x', 2)
            self.assertFalse(target.exists())

    def response(self, data, status=200, content_range=''):
        stream = io.BytesIO(data)
        stream.status = status
        stream.headers = {'Content-Range': content_range}
        stream.geturl = lambda: 'https://example.org/resource'
        return stream

    def test_range_resume_and_verified_publish(self):
        data = b'complete payload'
        with tempfile.TemporaryDirectory() as name:
            target = Path(name) / 'resource'
            target.with_suffix('.part').write_bytes(data[:4])
            with patch.object(resources.urllib.request, 'urlopen', return_value=self.response(data[4:], 206, 'bytes 4-15/16')) as get:
                resources.download('https://example.org/resource', target, hashlib.sha256(data).hexdigest(), len(data))
            self.assertEqual(get.call_args.args[0].get_header('Range'), 'bytes=4-')
            self.assertEqual(target.read_bytes(), data)
            self.assertFalse(target.with_suffix('.part').exists())

    def test_ignored_range_restarts_instead_of_appending(self):
        data = b'valid'
        with tempfile.TemporaryDirectory() as name:
            target = Path(name) / 'resource'
            target.with_suffix('.part').write_bytes(b'bad')
            with patch.object(resources.urllib.request, 'urlopen', return_value=self.response(data)):
                resources.download('https://example.org/resource', target, hashlib.sha256(data).hexdigest(), len(data))
            self.assertEqual(target.read_bytes(), data)

    def test_corrupt_payload_never_published_and_can_retry(self):
        with tempfile.TemporaryDirectory() as name:
            target = Path(name) / 'resource'
            expected = hashlib.sha256(b'valid').hexdigest()
            with patch.object(resources.urllib.request, 'urlopen', return_value=self.response(b'wrong')):
                with self.assertRaisesRegex(ValueError, 'SHA256'):
                    resources.download('https://example.org/resource', target, expected, 5)
            self.assertFalse(target.exists())
            self.assertEqual(len(list(target.parent.glob('*.rejected-*'))), 1)
            with patch.object(resources.urllib.request, 'urlopen', return_value=self.response(b'valid')):
                resources.download('https://example.org/resource', target, expected, 5)
            self.assertEqual(target.read_bytes(), b'valid')

    def test_offline_bad_model_does_not_create_completion_marker(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            source = root / 'media'
            source.mkdir()
            (source / 'payload').write_bytes(b'wrong')
            with patch.object(resources, 'models_manifest', return_value=(
                    {'sizes': {'payload': 5}}, {'payload': hashlib.sha256(b'valid').hexdigest()})):
                with self.assertRaisesRegex(ValueError, 'binding'):
                    resources.prepare_models(root / 'managed', source, True)
            self.assertFalse((root / 'managed/MinerU-4_models_torch/.mineru_complete').exists())
            self.assertEqual((source / 'payload').read_bytes(), b'wrong')

    def test_untrusted_apt_source_is_rejected(self):
        result = subprocess.CompletedProcess([], 0, '  Candidate: 1\n 500 https://attacker.example/packages noble/main\n', '')
        with patch.object(resources.shutil, 'which', return_value='apt-cache'), \
                patch.object(resources.subprocess, 'run', return_value=result):
            self.assertIsNone(resources.bwrap_package())


class BootstrapTests(unittest.TestCase):
    def test_relative_python_keeps_venv_path(self):
        with tempfile.TemporaryDirectory() as name:
            config = Path(name) / 'runtime.json'
            save(config, {'coordinator_python': 'runtime/bin/python'})
            self.assertEqual(bootstrap.selected_python(['convert', '--config', str(config)]),
                             str(Path(name) / 'runtime/bin/python'))

    def test_broken_configuration_is_not_bypassed(self):
        with tempfile.TemporaryDirectory() as name:
            config = Path(name) / 'runtime.json'
            config.write_text('{broken', encoding='utf-8')
            with self.assertRaises(ValueError):
                bootstrap.selected_python(['verify', '--config', str(config)])

    def test_script_forwards_to_prepared_coordinator(self):
        with patch.object(sys, 'argv', ['pdf2md.py', 'convert', '--input', 'manual.pdf']), \
                patch.object(bootstrap, 'selected_python', return_value='/prepared/bin/python'), \
                patch.object(bootstrap.Path, 'is_file', return_value=True), \
                patch.object(bootstrap.subprocess, 'run', return_value=subprocess.CompletedProcess([], 7)) as run:
            self.assertEqual(bootstrap.main(), 7)
        self.assertEqual(run.call_args.args[0][0], '/prepared/bin/python')

    def test_isolated_execution_never_forwards(self):
        with patch.dict(os.environ, {'P2_ISOLATED': '1'}), \
                patch.object(sys, 'argv', ['pdf2md.py', 'verify']), \
                patch.object(bootstrap, 'selected_python') as select, \
                patch('pdf2md_p2.cli.main', return_value=0):
            self.assertEqual(bootstrap.main(), 0)
        select.assert_not_called()


if __name__ == '__main__':
    unittest.main()
