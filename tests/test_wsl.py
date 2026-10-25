import importlib.util
import os
import posixpath
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from pdf2md_p2 import configuration, platform_runtime, sandbox
from pdf2md_p2.common import PRODUCT, save

spec = importlib.util.spec_from_file_location('wsl_entry', PRODUCT / 'scripts/wsl_entry.py')
bridge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bridge)
UUID = 'GPU-11111111-1111-1111-1111-111111111111'


class WSLDeviceTests(unittest.TestCase):
    def resolve(self, overrides=None, mask=None, inventory=None, listing=None):
        config = configuration.validate(configuration.initial(sys.executable, 'models') | (overrides or {}))
        def query(argv, **kwargs):
            self.assertNotIn('minor_number', ' '.join(argv))
            return SimpleNamespace(returncode=0, stderr='', stdout=(listing or UUID) if '-L' in argv else
                (inventory if inventory is not None else f'0, {UUID}, NVIDIA Test, 596.36\n'))
        with patch.object(configuration, 'is_wsl', return_value=True), \
                patch.dict(os.environ, {} if mask is None else {'CUDA_VISIBLE_DEVICES': mask}, clear=True), \
                patch.object(configuration.subprocess, 'run', side_effect=query):
            return configuration.resolve_device(config)

    def test_default_dxg_and_supported_query(self):
        config = self.resolve()
        self.assertEqual(config['device_files'], ['/dev/dxg'])
        self.assertEqual(config['authorized_gpu'], UUID)

    def test_explicit_uuid_and_visibility(self):
        for mask in ('0', UUID):
            self.assertEqual(self.resolve({'authorized_gpu': UUID}, mask)['authorized_gpu'], UUID)

    def test_empty_duplicate_hidden_mig_masks_refused(self):
        for mask in ('', '-1', '1', '0,0', 'MIG-' + UUID[4:]):
            with self.subTest(mask=mask), self.assertRaises(ValueError):
                self.resolve(mask=mask)

    def test_multiple_or_malformed_inventory_refused(self):
        for inventory in ('', 'bad', f'0, {UUID}, Test, 596\n1, {UUID}, Test, 596\n'):
            with self.subTest(inventory=inventory), self.assertRaises(ValueError):
                self.resolve(inventory=inventory)

    def test_native_device_nodes_cannot_override_wsl(self):
        with self.assertRaisesRegex(ValueError, 'only /dev/dxg'):
            self.resolve({'device_files': ['/dev/nvidia0', '/dev/nvidiactl', '/dev/nvidia-uvm']})

    def test_mig_listing_rejected(self):
        with self.assertRaisesRegex(ValueError, 'MIG'):
            self.resolve(listing='MIG 1g device')

    def test_dxg_rejected_on_linux(self):
        with patch.object(configuration, 'is_wsl', return_value=False), self.assertRaisesRegex(ValueError, 'WSL2'):
            configuration.resolve_device(configuration.validate(configuration.initial(sys.executable, 'models') |
                {'device_files': ['/dev/dxg']}))

    def test_detection_uses_kernel_not_environment_marker(self):
        with patch.object(platform_runtime.platform, 'system', return_value='Linux'), \
                patch.object(platform_runtime.platform, 'release', return_value='6.6-microsoft-standard-WSL2'):
            self.assertTrue(platform_runtime.is_wsl())

    def test_missing_driver_is_not_ignored(self):
        with patch.object(platform_runtime, 'is_wsl', return_value=True), \
                patch.object(Path, 'is_file', return_value=False), self.assertRaisesRegex(ValueError, 'driver library'):
            platform_runtime.driver_environment()

    def test_invalid_bwrap_is_not_substituted(self):
        with self.assertRaisesRegex(ValueError, 'bubblewrap'):
            platform_runtime.bwrap_path({'bubblewrap_path': '/missing/bwrap'})

    def test_bwrap_relative_config_and_environment_precedence(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            save(root / 'runtime.json', configuration.initial(sys.executable, 'models') | {'bubblewrap_path': 'tools/bwrap'})
            with patch.object(configuration, 'resolve_device', side_effect=lambda value: value), \
                    patch.dict(os.environ, {}, clear=True):
                self.assertEqual(configuration.load(root / 'runtime.json')['bubblewrap_path'], str(root / 'tools/bwrap'))
            with patch.object(configuration, 'resolve_device', side_effect=lambda value: value), \
                    patch.dict(os.environ, {'PDF2MD_BWRAP': str(root / 'env-bwrap')}, clear=True):
                self.assertEqual(configuration.load(root / 'runtime.json', {'bubblewrap_path': str(root / 'cli-bwrap')})
                    ['bubblewrap_path'], str(root / 'cli-bwrap'))


@unittest.skipUnless(sys.platform == 'linux', 'Linux sandbox paths')
class SandboxTests(unittest.TestCase):
    def test_native_linux_command_keeps_nvidia_devices_and_no_wsl_driver_override(self):
        with patch.object(sandbox, 'bwrap_path', return_value='/usr/bin/bwrap'), \
                patch.object(sandbox, 'driver_environment', return_value={}), \
                patch.object(Path, 'exists', return_value=True):
            config = {'coordinator_python': sys.executable, 'parser_python': sys.executable,
                      'device_files': ['/dev/nvidia7', '/dev/nvidiactl', '/dev/nvidia-uvm'], 'authorized_gpu': UUID}
            command = sandbox.base(config)
        self.assertIn('--unshare-all', command)
        self.assertIn('/dev/nvidia7', command)
        self.assertNotIn('/dev/dxg', command)
        self.assertNotIn('LD_LIBRARY_PATH', command)
        self.assertIn(['--proc', '/proc'], [command[i:i+2] for i in range(len(command)-1)])

    def test_nested_tool_is_file_only_and_worker_retains_fresh_proc(self):
        with patch.object(sandbox, 'bwrap_path', return_value='/home/user/tools/bwrap'), \
                patch.object(sandbox, 'driver_environment', return_value={'LD_LIBRARY_PATH': '/usr/lib/wsl/lib'}), \
                patch.object(Path, 'exists', return_value=True):
            config = {'coordinator_python': sys.executable, 'parser_python': sys.executable,
                      'device_files': ['/dev/dxg'], 'authorized_gpu': UUID}
            worker = sandbox.base(config)
            coordinator = sandbox.base(config, coordinator=True)
        self.assertIn('--unshare-all', worker)
        self.assertIn(['--proc', '/proc'], [worker[i:i+2] for i in range(len(worker)-1)])
        for command in (worker, coordinator):
            self.assertIn(['--ro-bind', '/home/user/tools/bwrap', '/home/user/tools/bwrap'],
                          [command[i:i+3] for i in range(len(command)-2)])
            self.assertNotIn('/home/user/tools', command)
            self.assertIn('/usr/lib/wsl/lib', command)
            self.assertIn('/dev/dxg', command)


class WindowsBridgeTests(unittest.TestCase):
    def test_missing_selected_python_explains_runtime_setup(self):
        with patch.object(sys, 'argv', ['wsl_entry.py', 'D:\\workspace', '-', 'e30=', '--help']), \
                patch.object(bridge, 'linux_path', return_value='/mnt/d/workspace'), \
                patch.object(bridge.os, 'chdir'), \
                patch.object(bridge, 'select_python', return_value='/missing/python'), \
                patch.object(bridge.subprocess, 'run', side_effect=FileNotFoundError('missing')):
            with self.assertRaisesRegex(ValueError, 'WSL Python not found.*docs/CONFIGURATION.md'):
                bridge.main()

    def test_linux_paths_passthrough_and_unc_refused(self):
        self.assertEqual(bridge.linux_path('/home/user/a b.pdf', '/tmp'), '/home/user/a b.pdf')
        for path in ('\\\\server\\share\\a.pdf', '//server/share', 'D:relative.pdf'):
            with self.subTest(path=path), self.assertRaises(ValueError):
                bridge.linux_path(path, '/tmp')

    def test_drive_path_uses_argv_not_shell(self):
        with patch.object(bridge.subprocess, 'run', return_value=SimpleNamespace(stdout='/mnt/d/中文/a b.pdf\n')) as run:
            self.assertEqual(bridge.linux_path('D:\\中文\\a b.pdf', '/tmp'), '/mnt/d/中文/a b.pdf')
            self.assertEqual(run.call_args.args[0], ['wslpath', '-a', '-u', 'D:\\中文\\a b.pdf'])
            self.assertNotIn('shell', run.call_args.kwargs)

    def test_relative_and_equals_path_options_keep_unicode_and_spaces(self):
        with patch.object(bridge, 'os', SimpleNamespace(path=posixpath)):
            args = bridge.translate(['convert', '--input', '中文\\a b.pdf', '--output-dir=not yet', '--gpu', '0'], '/mnt/d')
        self.assertEqual(args, ['convert', '--input', '/mnt/d/中文/a b.pdf', '--output-dir=/mnt/d/not yet', '--gpu', '0'])

    def test_every_path_option_is_translated(self):
        with patch.object(bridge, 'os', SimpleNamespace(path=posixpath)):
            for key in bridge.PATH_OPTIONS:
                self.assertEqual(bridge.translate([key, 'a b'], '/tmp'), [key, '/tmp/a b'])

    def test_missing_path_value_fails(self):
        with self.assertRaises(ValueError):
            bridge.translate(['--input'], '/tmp')

    def test_python_precedence_explicit_env_config_venv_fallback(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {}, clear=True), \
                patch('pdf2md_p2.configuration.find_config', return_value=None):
            root = Path(directory)
            args = ['convert']
            self.assertEqual(bridge.select_python('/chosen/python', args, root), '/chosen/python')
            with patch.dict(os.environ, {'PDF2MD_WSL_PYTHON': '/env/python'}):
                self.assertEqual(bridge.select_python(None, args, root), '/env/python')
            save(root / 'runtime.json', {'coordinator_python': 'env/bin/python'})
            self.assertEqual(bridge.select_python(None, ['convert', '--config', str(root / 'runtime.json')], root),
                             str(root / 'env/bin/python'))
            self.assertEqual(bridge.select_python(None, args, root), 'python3')
            (root / '.venv/bin').mkdir(parents=True)
            (root / '.venv/bin/python').touch()
            self.assertEqual(bridge.select_python(None, args, root), str(root / '.venv/bin/python'))


if __name__ == '__main__':
    unittest.main()
