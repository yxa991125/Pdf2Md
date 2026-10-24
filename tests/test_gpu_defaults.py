import os
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from pdf2md_p2 import configuration
from pdf2md_p2.common import save


FIRST = 'GPU-11111111-1111-1111-1111-111111111111'
SECOND = 'GPU-22222222-2222-2222-2222-222222222222'
MIG = 'MIG-33333333-3333-3333-3333-333333333333'
INVENTORY = f'0, {FIRST}, 3, Disabled\n1, {SECOND}, 7, [N/A]\n'


class GPUDefaultTests(unittest.TestCase):
    def setUp(self):
        native = patch.object(configuration, 'is_wsl', return_value=False)
        native.start()
        self.addCleanup(native.stop)

    def resolve(self, overrides=None, inventory=INVENTORY, env=None):
        value = configuration.validate({
            'coordinator_python': 'coordinator/bin/python', 'parser_python': 'parser/bin/python',
            'model_path': 'models', **(overrides or {})})
        with patch.dict(os.environ, env or {}, clear=True), \
                patch.object(configuration.subprocess, 'run',
                    return_value=SimpleNamespace(returncode=0, stdout=inventory, stderr='')) as query:
            result = configuration.resolve_device(value)
        return result, query

    def test_default_is_first_gpu_with_queried_minor_not_assumed_nvidia0(self):
        result, query = self.resolve()
        self.assertEqual(result['authorized_gpu'], FIRST)
        self.assertEqual(result['device_files'], ['/dev/nvidia-uvm', '/dev/nvidia3', '/dev/nvidiactl'])
        self.assertIn('--query-gpu=index,uuid,minor_number,mig.mode.current', query.call_args.args[0])
        self.assertEqual(query.call_args.kwargs['timeout'], 30)

    def test_missing_gpu_fields_default_on_load_and_resolve_before_runtime(self):
        with tempfile.TemporaryDirectory() as name:
            path = Path(name) / 'runtime.json'
            save(path, {'coordinator_python': 'coordinator/bin/python',
                'parser_python': 'parser/bin/python', 'model_path': 'models'})
            with patch.dict(os.environ, {}, clear=True), \
                    patch.object(configuration.subprocess, 'run',
                        return_value=SimpleNamespace(returncode=0, stdout=INVENTORY, stderr='')):
                result = configuration.load(path)
            self.assertEqual(result['authorized_gpu'], FIRST)
            self.assertIn('/dev/nvidia3', result['device_files'])

    def test_explicit_index_selects_second_gpu(self):
        result, _ = self.resolve({'authorized_gpu': '1'})
        self.assertEqual(result['authorized_gpu'], SECOND)
        self.assertIn('/dev/nvidia7', result['device_files'])

    def test_numeric_visibility_mask_is_respected_in_order(self):
        result, _ = self.resolve(env={'CUDA_VISIBLE_DEVICES': '1,0'})
        self.assertEqual(result['authorized_gpu'], SECOND)
        result, _ = self.resolve({'authorized_gpu': '1'}, env={'CUDA_VISIBLE_DEVICES': '1,0'})
        self.assertEqual(result['authorized_gpu'], FIRST)

    def test_uuid_visibility_mask_is_respected(self):
        result, _ = self.resolve(env={'CUDA_VISIBLE_DEVICES': SECOND})
        self.assertEqual(result['authorized_gpu'], SECOND)

    def test_hidden_invalid_or_duplicate_devices_do_not_fall_back(self):
        for mask in ('', '-1', '4', '0,0', FIRST + ',0', 'GPU-unknown'):
            with self.subTest(mask=mask), self.assertRaises(ValueError):
                self.resolve(env={'CUDA_VISIBLE_DEVICES': mask})

    def test_unavailable_index_does_not_fall_back(self):
        with self.assertRaisesRegex(ValueError, 'unavailable'):
            self.resolve({'authorized_gpu': '2'})

    def test_invalid_selectors_rejected(self):
        for selector in (-1, 0, '', '-1', 'auto', '01', '0,1', True):
            with self.subTest(selector=selector), self.assertRaises(ValueError):
                self.resolve({'authorized_gpu': selector})

    def test_explicit_uuid_can_detect_device_nodes(self):
        result, _ = self.resolve({'authorized_gpu': SECOND})
        self.assertEqual(result['authorized_gpu'], SECOND)
        self.assertIn('/dev/nvidia7', result['device_files'])

    def test_existing_explicit_uuid_and_nodes_need_no_detection(self):
        nodes = ['/dev/nvidia7', '/dev/nvidiactl', '/dev/nvidia-uvm']
        result, query = self.resolve({'authorized_gpu': SECOND, 'device_files': nodes})
        self.assertEqual(result['authorized_gpu'], SECOND)
        self.assertEqual(result['device_files'], sorted(nodes))
        query.assert_not_called()

    def test_explicit_nodes_must_match_resolved_index(self):
        with self.assertRaisesRegex(ValueError, 'do not match'):
            self.resolve({'device_files': ['/dev/nvidia0', '/dev/nvidiactl', '/dev/nvidia-uvm']})

    def test_mig_enabled_physical_device_is_not_selected_automatically(self):
        with self.assertRaisesRegex(ValueError, 'MIG-enabled'):
            self.resolve(inventory=f'0, {FIRST}, 3, Enabled\n')
        with self.assertRaisesRegex(ValueError, 'MIG selection'):
            self.resolve(env={'CUDA_VISIBLE_DEVICES': MIG})

    def test_explicit_mig_config_remains_supported(self):
        nodes = ['/dev/nvidia3', '/dev/nvidiactl', '/dev/nvidia-uvm',
            '/dev/nvidia-caps/nvidia-cap1', '/dev/nvidia-caps/nvidia-cap2']
        result, query = self.resolve({'authorized_gpu': MIG, 'device_files': nodes})
        self.assertEqual(result['authorized_gpu'], MIG)
        query.assert_not_called()
        with self.assertRaisesRegex(ValueError, 'explicit device_files'):
            self.resolve({'authorized_gpu': MIG})

    def test_missing_empty_malformed_or_failed_inventory(self):
        for inventory in ('', 'invalid\n', f'0, {FIRST}, wrong, Disabled\n'):
            with self.subTest(inventory=inventory), self.assertRaises(ValueError):
                self.resolve(inventory=inventory)
        for error in (FileNotFoundError('nvidia-smi'), subprocess.TimeoutExpired('nvidia-smi', 30)):
            with self.subTest(error=error), patch.object(configuration.subprocess, 'run', side_effect=error), \
                    self.assertRaisesRegex(ValueError, 'Cannot detect GPU'):
                configuration.resolve_device(configuration.validate(configuration.initial('parser', 'models')))
        with patch.object(configuration.subprocess, 'run',
                return_value=SimpleNamespace(returncode=1, stdout='', stderr='driver unavailable')), \
                self.assertRaisesRegex(ValueError, 'driver unavailable'):
            configuration.resolve_device(configuration.validate(configuration.initial('parser', 'models')))

    def test_detection_changes_effective_config_when_hardware_changes(self):
        first, _ = self.resolve()
        changed, _ = self.resolve(inventory=f'0, {SECOND}, 7, Disabled\n')
        self.assertNotEqual(first, changed)


if __name__ == '__main__':
    unittest.main()
