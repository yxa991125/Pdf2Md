import copy
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import pymupdf
from PIL import Image

from pdf2md_p2 import backends, configuration, environment, sandbox, source_map
from pdf2md_p2.common import digest, inventory, read, save, sha


class BackendSelectionTests(unittest.TestCase):
    def test_no_backend_explains_setup_without_installing_anything(self):
        error = backends.NoBackendAvailable({'cpu': {'ready_for_conversion': False}})
        self.assertIn('No prepared backend', str(error))
        self.assertIn('docs/CONFIGURATION.md', str(error))
        self.assertIn('--config', str(error))
        self.assertIn('--wsl-python', str(error))

    def config(self, **values):
        return configuration.validate(configuration.initial(sys.executable, 'models') | values)

    def choose(self, config, ready=('cpu', 'cuda'), pinned=None, integrity=None, probe_all=False):
        calls = []
        def inspect(value):
            mode = value['resolved_compute']
            calls.append(mode)
            return {'ready_for_conversion': mode in ready,
                    'checks': [{'integrity_violation': mode == integrity}] if mode == integrity else []}
        with patch.object(backends, 'resolve_device', side_effect=lambda value: value), \
                patch.object(environment, 'inspect', side_effect=inspect):
            result = backends.select(config, pinned, probe_all)
        return *result, calls

    def test_auto_prefers_ready_gpu(self):
        config, reports, calls = self.choose(self.config())
        self.assertEqual(config['resolved_compute'], 'cuda')
        self.assertEqual(set(reports), {'cpu', 'cuda'})

    def test_no_gpu_uses_ready_cpu_and_no_device_nodes(self):
        config, _, _ = self.choose(self.config(), ready=('cpu',))
        self.assertEqual(config['resolved_compute'], 'cpu')
        self.assertEqual(config['device_files'], [])
        self.assertEqual(config['selection_reason'], 'AUTO_CPU_GPU_UNAVAILABLE')

    def test_explicit_cpu_never_resolves_gpu(self):
        with patch.object(backends, 'resolve_device', side_effect=AssertionError('GPU query')), \
                patch.object(environment, 'inspect', return_value={'ready_for_conversion': True, 'checks': []}):
            config, _ = backends.select(self.config(compute='cpu'))
        self.assertEqual(config['engine'], 'PyMuPDF4LLM')

    def test_explicit_cuda_does_not_fall_back(self):
        with self.assertRaisesRegex(ValueError, 'No prepared backend'):
            self.choose(self.config(compute='cuda'), ready=('cpu',))

    def test_missing_nvidia_smi_allows_auto_cpu(self):
        with patch.object(backends, 'resolve_device', side_effect=FileNotFoundError('nvidia-smi')), \
                patch.object(environment, 'inspect', return_value={'ready_for_conversion': True, 'checks': []}):
            result, _ = backends.select(self.config())
        self.assertEqual(result['resolved_compute'], 'cpu')

    def test_invalid_inventory_is_not_disguised_as_cpu_fallback(self):
        with patch.object(backends, 'resolve_device', side_effect=ValueError('Invalid GPU inventory')), \
                self.assertRaisesRegex(ValueError, 'Invalid GPU inventory'):
            backends.select(self.config())

    def test_gpu_binding_corruption_does_not_fall_back(self):
        with self.assertRaisesRegex(ValueError, 'integrity'):
            self.choose(self.config(), ready=('cpu',), integrity='cuda')

    def test_pinned_cpu_does_not_switch_when_gpu_becomes_available(self):
        pinned = {'resolved_compute': 'cpu', 'selection_reason': 'AUTO_CPU_GPU_UNAVAILABLE'}
        config, _, calls = self.choose(self.config(), pinned=pinned)
        self.assertEqual(config['resolved_compute'], 'cpu')
        self.assertEqual(calls, ['cpu'])
        self.assertEqual(config['selection_reason'], pinned['selection_reason'])

    def test_pinned_unavailable_gpu_refuses_resume(self):
        with self.assertRaises(ValueError):
            self.choose(self.config(), ready=('cpu',), pinned={'resolved_compute': 'cuda'})

    def test_conflicting_compute_override_refuses_resume(self):
        with self.assertRaisesRegex(ValueError, 'conflicts'):
            self.choose(self.config(compute='cpu'), pinned={'resolved_compute': 'cuda'})

    def test_doctor_can_probe_both_but_respects_explicit_selection(self):
        config, reports, _ = self.choose(self.config(compute='cpu'), probe_all=True)
        self.assertEqual(set(reports), {'cpu', 'cuda'})
        self.assertEqual(config['resolved_compute'], 'cpu')

    def test_cpu_python_precedence_and_relative_config(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            save(root / 'runtime.json', {'cpu_python': 'cpu/bin/python'})
            with patch.dict(os.environ, {'PDF2MD_CPU_PYTHON': str(root / 'env/bin/python')}):
                self.assertEqual(configuration.load(root / 'runtime.json', resolve=False)['cpu_python'], str(root / 'cpu/bin/python'))
                self.assertEqual(configuration.load(root / 'runtime.json', {'cpu_python': str(root / 'cli/bin/python')}, resolve=False)
                    ['cpu_python'], str(root / 'cli/bin/python'))
                self.assertEqual(configuration.load(overrides={}, resolve=False)['cpu_python'], str(root / 'env/bin/python'))

    def test_unknown_compute_profile_and_cpu_gpu_nodes_refused(self):
        for settings in ({'compute': 'guess'}, {'resolved_compute': 'other'},
                         {**configuration.CPU_POLICY, 'resolved_compute': 'cpu', 'device_files': ['/dev/dxg']}):
            with self.subTest(settings=settings), self.assertRaises(ValueError):
                self.config(**settings)

    def test_partially_prepared_gpu_model_bundle_is_integrity_failure(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            (root / 'MinerU-4_models_torch').mkdir()
            bindings = {'worker_sha256': sha(environment.PRODUCT / 'vendor/gpu_parser_worker.py'),
                        'models': {'MinerU-4_models_torch/missing-model': '0' * 64}}
            config = self.config(model_path=str(root), device_files=['/dev/nvidia0', '/dev/nvidiactl', '/dev/nvidia-uvm'])
            with patch.object(environment, 'read', return_value=bindings):
                result = environment.inspect(config, namespaces=False)
            row, = [r for r in result['checks'] if r['name'] == 'offline_models_and_worker']
            self.assertFalse(row['passed'])
            self.assertTrue(row['integrity_violation'])

    def test_doctor_failure_still_exports_machine_readable_availability(self):
        from pdf2md_p2 import cli
        with tempfile.TemporaryDirectory() as name:
            report = Path(name) / 'doctor.json'
            reports = {'cpu': {'ready_for_conversion': False}, 'cuda': {'ready_for_conversion': False}}
            with patch.object(cli, 'runtime', side_effect=backends.NoBackendAvailable(reports)), \
                    patch.object(sys, 'argv', ['pdf2md.py', 'doctor', '--report', str(report)]), \
                    contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(cli.main(), 1)
            self.assertEqual(read(report)['backend_availability'], reports)

    @unittest.skipUnless(sys.platform == 'linux', 'Linux sandbox paths')
    def test_cpu_worker_sandbox_has_fresh_proc_and_no_gpu_bind_or_driver_override(self):
        config = backends.candidate(self.config(), 'cpu')
        with patch.object(sandbox, 'bwrap_path', return_value='/usr/bin/bwrap'), \
                patch.object(sandbox, 'driver_environment', side_effect=AssertionError('CPU driver dependency')):
            command = sandbox.worker(config, Path('/tmp/in.pdf'), Path('/tmp/out'), [1])
        self.assertIn('--unshare-all', command)
        self.assertNotIn('--dev-bind', command)
        self.assertNotIn('/models', command)
        self.assertNotIn('LD_LIBRARY_PATH', command)
        self.assertIn(['--proc', '/proc'], [command[i:i+2] for i in range(len(command)-1)])


class MappingTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.pdf = self.root / 'source.pdf'
        self.output = self.root / 'result'
        self.output.mkdir()
        self.document = pymupdf.open()
        self.page = self.document.new_page()
        self.page.insert_text((60, 80), 'Actual voltage is -0.3 V.')
        self.page.insert_text((60, 160), 'Repeated technical text.')
        self.page.insert_text((60, 240), 'Repeated technical text.')

    def build(self, body, native=None, rotation=0):
        self.page.set_rotation(rotation)
        self.document.save(self.pdf)
        self.document.close()
        (self.output / 'manual.md').write_text('<!-- PAGE 1 -->\n\n' + body, encoding='utf-8')
        save(self.output / 'configuration.json', {'input_sha256': sha(self.pdf)})
        chunks = []
        if native is not None:
            save(self.output / 'chunk/native_output/native.json', {'pages': [
                {'page': 1, 'text': body, 'page_boxes': native}]})
            chunks = [{'attempt': 'chunk'}]
        return source_map.build(self.pdf, self.output, chunks)

    def seal(self):
        save(self.output / 'run_manifest.json', {'artifacts': inventory(self.output)})

    def test_unique_source_text_has_exact_page_and_region(self):
        mapping = self.build('Actual voltage is -0.3 V.\n')
        row, = mapping['objects']
        self.assertEqual(row['status'], 'MATCHED_SOURCE_TEXT')
        self.assertEqual(row['sources'][0]['page'], 1)
        self.assertLess(row['sources'][0]['bbox'][1], 80)
        self.assertIn('Actual voltage', row['sources'][0]['source_text'])

    def test_wrong_output_is_located_without_claiming_correctness(self):
        body = 'Actual voltage is 30.9 V.\n'
        mapping = self.build(body, [{'index': 0, 'class': 'text', 'bbox': [55, 65, 300, 85], 'pos': [0, len(body)]}])
        row, = mapping['objects']
        self.assertEqual(row['status'], 'ASSOCIATED_NATIVE_LAYOUT')
        self.assertIn('-0.3', row['sources'][0]['source_text'])
        self.assertEqual(row['semantic_correctness'], 'NOT_JUDGED')

    def test_repeated_source_text_does_not_choose_first(self):
        row, = self.build('Repeated technical text.\n')['objects']
        self.assertEqual(row['status'], 'AMBIGUOUS')
        self.assertEqual(len(row['sources']), 2)

    def test_repeated_output_cannot_reuse_unique_source_as_certainty(self):
        rows = self.build('Actual voltage is -0.3 V.\n\nActual voltage is -0.3 V.\n')['objects']
        self.assertEqual([r['status'] for r in rows], ['AMBIGUOUS', 'AMBIGUOUS'])

    def test_unmapped_is_explicit_and_does_not_hide_missing_output(self):
        rows = self.build('Invented text.\n')['objects']
        self.assertEqual(rows[0]['status'], 'UNMAPPED')
        self.assertEqual(rows[0]['sources'], [])

    def test_invalid_geometry_is_not_accepted(self):
        body = 'Wrong output.\n'
        row, = self.build(body, [{'index': 0, 'class': 'text', 'bbox': [-1, 0, 50, 20], 'pos': [0, len(body)]}])['objects']
        self.assertEqual(row['status'], 'UNMAPPED')

    def test_native_coordinate_conflicting_with_pdf_is_ambiguous(self):
        body = 'Actual voltage is -0.3 V.\n'
        row, = self.build(body, [{'index': 0, 'class': 'text', 'bbox': [40, 400, 300, 450], 'pos': [0, len(body)]}])['objects']
        self.assertEqual(row['status'], 'AMBIGUOUS')

    def test_final_unicode_offsets_are_not_utf8_or_utf16_offsets(self):
        body = '中文 😀 prefix\n\nActual voltage is -0.3 V.\n'
        mapping = self.build(body)
        row = mapping['objects'][1]
        text = (self.output / 'manual.md').read_text(encoding='utf-8')
        self.assertEqual(text[row['start']:row['end']], row['text'])
        self.assertNotEqual(row['start'], len(text[:row['start']].encode('utf-8')))
        self.assertEqual(row['status'], 'MATCHED_SOURCE_TEXT')

    def test_escaped_text_and_inserted_prefix_keep_final_locus(self):
        self.page.insert_text((60, 320), 'Read DATA_STATUS now.')
        body = 'Added leading paragraph.\n\nRead DATA\\_STATUS now.\n'
        row = self.build(body)['objects'][1]
        self.assertEqual(row['status'], 'MATCHED_SOURCE_TEXT')
        self.assertEqual(row['line_start'], 5)

    def test_types_cover_heading_list_table_formula_image(self):
        text = '<!-- PAGE 1 -->\n\n# Header\n\n- Item\n\n| A | B |\n|---|---|\n|1|2|\n\n$$x=1$$\n\n![x](assets/x.png)\n'
        self.assertEqual({r['kind'] for r in source_map.objects(text)}, {'heading', 'list_item', 'table', 'formula', 'image'})

    def test_formula_layout_association_does_not_require_correct_formula(self):
        body = '$$x = 42$$\n'
        mapping = self.build(body, [{'index': 0, 'class': 'formula', 'bbox': [50, 60, 300, 90], 'pos': [0, len(body)]}])
        self.assertEqual(mapping['objects'][0]['kind'], 'formula')
        self.assertEqual(mapping['objects'][0]['status'], 'ASSOCIATED_NATIVE_LAYOUT')
        self.assertFalse(mapping['semantic_correctness_certified'])

    def test_gpu_normalized_layout_coordinates_use_physical_page(self):
        self.page.set_rotation(0)
        self.document.save(self.pdf)
        self.document.close()
        width, height = 595.0, 842.0
        body = '<!-- PAGE 1 -->\n\n# GPU heading\n'
        (self.output / 'manual.md').write_text(body, encoding='utf-8')
        save(self.output / 'chunk/native_output/native.json', {'pages': [
            {'page': 1, 'middle_json': 'native_output/page.json'}]})
        save(self.output / 'chunk/native_output/page.json', {'pages': [{'page_idx': 0, 'blocks': [
            {'type': 'paragraph_title', 'index': 0, 'bbox': [50/width, 60/height, 300/width, 90/height],
             'content': [{'type': 'text', 'content': 'GPU heading'}]}]}]})
        mapping = source_map.build(self.pdf, self.output, [{'attempt': 'chunk'}])
        row, = mapping['objects']
        self.assertEqual(row['status'], 'ASSOCIATED_NATIVE_LAYOUT')
        self.assertEqual(row['sources'][0]['page'], 1)
        self.assertAlmostEqual(row['sources'][0]['bbox'][0], 50)

    def test_source_addition_image_has_source_coordinates_and_hash(self):
        (self.output / 'assets').mkdir()
        Image.new('RGB', (20, 30), 'red').save(self.output / 'assets/fragment.png')
        save(self.output / 'source_glyphs.json', {'records': [{'page': 1, 'source_bbox': [50, 60, 250, 85], 'asset': 'assets/fragment.png'}]})
        row, = self.build('![x](assets/fragment.png)\n')['objects']
        self.assertEqual(row['status'], 'ASSOCIATED_NATIVE_LAYOUT')
        self.assertEqual(row['sources'][0]['basis'], 'RECORDED_SOURCE_ADDITION')

    def test_map_validator_rejects_forged_offset_id_counts_assets_and_geometry(self):
        mapping = self.build('Actual voltage is -0.3 V.\n')
        for mutation in ('offset', 'id', 'counts', 'geometry', 'claim'):
            changed = copy.deepcopy(mapping)
            if mutation == 'offset':
                changed['objects'][0]['start'] += 1
            elif mutation == 'id':
                changed['objects'][0]['id'] = 'obj-' + '0' * 20
            elif mutation == 'counts':
                changed['states'] = {}
            elif mutation == 'geometry':
                changed['objects'][0]['sources'][0]['bbox'] = [0, 0, 9999, 9999]
            else:
                changed['semantic_correctness_certified'] = True
            save(self.output / 'source_map.json', changed)
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                source_map.validate(self.output)

    def test_trace_by_id_and_line_match_without_source_pdf(self):
        row, = self.build('Actual voltage is -0.3 V.\n')['objects']
        self.seal()
        by_id = source_map.trace(self.output, object_id=row['id'])
        by_line = source_map.trace(self.output, line=row['line_start'])
        self.assertEqual(by_id, by_line)

    def test_trace_crops_rotated_page_and_does_not_mutate_sealed_output(self):
        row, = self.build('Actual voltage is -0.3 V.\n', rotation=90)['objects']
        self.seal()
        before = inventory(self.output)
        result = source_map.trace(self.output, object_id=row['id'], pdf=self.pdf, render_dir=self.root / 'evidence')
        self.assertEqual(inventory(self.output), before)
        image = self.root / 'evidence' / result['evidence'][0]['path']
        with Image.open(image) as bitmap:
            self.assertGreater(bitmap.width, bitmap.height)
            self.assertGreater(len(set(bitmap.convert('L').tobytes())), 1)

    def test_render_refuses_wrong_pdf_and_inside_output_and_existing_export(self):
        row, = self.build('Actual voltage is -0.3 V.\n')['objects']
        self.seal()
        wrong = self.root / 'wrong.pdf'
        wrong.write_bytes(b'wrong')
        with self.assertRaisesRegex(ValueError, 'SHA256'):
            source_map.trace(self.output, object_id=row['id'], pdf=wrong, render_dir=self.root / 'wrong-export')
        with self.assertRaises(ValueError):
            source_map.trace(self.output, object_id=row['id'], pdf=self.pdf, render_dir=self.output / 'evidence')
        existing = self.root / 'existing'
        existing.mkdir()
        with self.assertRaises(ValueError):
            source_map.trace(self.output, object_id=row['id'], pdf=self.pdf, render_dir=existing)

    def test_mapping_stable_ids_when_identical_inputs_repeat(self):
        mapping = self.build('Actual voltage is -0.3 V.\n')
        repeated = source_map.build(self.pdf, self.output, [])
        self.assertEqual(mapping, repeated)


if __name__ == '__main__':
    unittest.main()
