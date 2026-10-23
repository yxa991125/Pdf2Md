import ast
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import pymupdf
from PIL import Image

from pdf2md_p2 import assets, identifiers, native
from pdf2md_p2.common import PRODUCT, digest, inventory, page_parts, read, save, sha, verify
from pdf2md_p2.source import preflight, empty_content_pages
from pdf2md_p2.repair import apply as repair, reverse


def source(text):
    return {'text': text, 'bbox': [0, 0, 200, 40], 'source_lines': [text]}


class IdentifierTests(unittest.TestCase):
    def test_correct_escaped_identifier_kept(self):
        row, = identifiers.inspect_block(source('Set the DATA_STATUS bit to one.'), 'Set the DATA\\_STATUS bit to one.', 1)
        self.assertEqual(row['state'], 'SOURCE_OCCURRENCE_PRESERVED')
        self.assertEqual(row['automatic_action'], 'NONE')

    def test_underscore_loss_bound_to_body_not_correct_heading(self):
        text = '## DATA\\_STATUS\n\nSet the DATA STATUS bit to one.'
        row, = identifiers.inspect_block(source('Set the DATA_STATUS bit to one.'), text, 1)
        self.assertEqual(row['state'], 'TECHNICAL_IDENTIFIER_DISTORTION')
        self.assertEqual(row['output_candidates'][0]['text'], 'DATA STATUS')

    def test_space_hyphen_values_not_globally_normalized(self):
        text = 'Use DATA STATUS and READ-MODE for output.'
        rows = identifiers.inspect_block(source(text), text, 1)
        self.assertTrue(all(r['state'] == 'SOURCE_OCCURRENCE_PRESERVED' and r['automatic_action'] == 'NONE' for r in rows))

    def test_digit_identifier_boundary_is_not_accepted_as_space_equivalence(self):
        row, = identifiers.inspect_block(source('Read the IN1N field right now.'), 'Read the IN1 N field right now.', 1)
        self.assertEqual(row['state'], 'TECHNICAL_IDENTIFIER_DISTORTION')

    def test_similar_register_not_accepted_by_heading(self):
        row, = identifiers.inspect_block(source('Set the CTRL_REG bit to one.'),
                                        'CTRL_REG\nSet the OTHER_REG bit to one.', 1)
        self.assertEqual(row['state'], 'TECHNICAL_IDENTIFIER_UNRESOLVED')

    def test_case_change_not_accepted(self):
        row, = identifiers.inspect_block(source('Set the CTRL_REG bit to one.'), 'Set the ctrl_reg bit to one.', 1)
        self.assertNotEqual(row['state'], 'SOURCE_OCCURRENCE_PRESERVED')

    def test_repeated_local_context_ambiguity_preserved(self):
        text = 'Set the DATA STATUS bit to one. Set the DATA STATUS bit to one.'
        row, = identifiers.inspect_block(source('Set the DATA_STATUS bit to one.'), text, 1)
        self.assertEqual(row['state'], 'TECHNICAL_IDENTIFIER_UNRESOLVED')

    def test_hexadecimal_changed_value_not_accepted(self):
        row, = identifiers.inspect_block(source('Read the 0x42 register value now.'), 'Read the 0x43 register value now.', 1)
        self.assertNotEqual(row['state'], 'SOURCE_OCCURRENCE_PRESERVED')

    def test_bit_range_changed_not_accepted(self):
        row, = identifiers.inspect_block(source('Read bits [15:8] from this register.'), 'Read bits [7:0] from this register.', 1)
        self.assertNotEqual(row['state'], 'SOURCE_OCCURRENCE_PRESERVED')

    def test_math_script_is_not_plain_identifier_rewrite(self):
        text = r'The value $T_{A}$ is 25 C.'
        self.assertEqual(identifiers.inspect_block(source('The value T_A is 25 C.'), text, 1)[0]['automatic_action'], 'NONE')


class ProductTests(unittest.TestCase):
    def test_empty_output_cannot_hide_graphics_only_source_page(self):
        inspection = {'page_details': [{'page': 1, 'native_characters': 0, 'images': 0, 'vector_paths': 2086}]}
        gaps = empty_content_pages(inspection, '<!-- PAGE 1 -->\n\n')
        self.assertEqual(gaps[0]['state'], 'SOURCE_CONTENT_PRESENT_OUTPUT_EMPTY')
        self.assertFalse(empty_content_pages(inspection, '<!-- PAGE 1 -->\n![drawing](assets/drawing.png)'))

    def test_source_bound_prose_repair_and_exact_reverse(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            pdf = root / 'source.pdf'
            document = pymupdf.open()
            page = document.new_page()
            page.insert_text((40, 50), 'Set the DATA_STATUS bit to one.')
            page.insert_text((40, 100), 'Clear the CTRL_REG bit to zero.')
            document.save(pdf)
            document.close()
            native_text = '<!-- PAGE 1 -->\n\nSet the DATA STATUS bit to one.\n\nClear the CTRL REG bit to zero.\n'
            findings = identifiers.check(pdf, native_text)
            after, log = repair(pdf, native_text, findings, root)
            self.assertEqual(len(log['operations']), 2)
            self.assertIn('DATA\\_STATUS', after)
            self.assertIn('CTRL\\_REG', after)
            self.assertEqual(reverse(after, log), native_text)
            self.assertTrue(all(r['independent_quality_state'] == 'NOT_EVALUATED' for r in log['operations']))
            with self.assertRaises(ValueError):
                reverse(after + 'tamper', log)

    def test_formula_or_table_like_text_not_rewritten(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            pdf = root / 'source.pdf'
            document = pymupdf.open()
            document.new_page().insert_text((40, 50), 'Set the DATA_STATUS bit to one.')
            document.save(pdf)
            document.close()
            for content in ('Set the DATA STATUS bit to one. $x=1$',
                            '| Set the DATA STATUS bit to one. |',
                            '<p>Set the DATA STATUS bit to one.</p>',
                            'Set the `DATA STATUS` bit to one.'):
                before = '<!-- PAGE 1 -->\n\n' + content
                after, log = repair(pdf, before, identifiers.check(pdf, before), root)
                self.assertEqual(after, before)
                self.assertEqual(log['operations'], [])

    def test_graphics_only_page_in_native_document_is_retained(self):
        with tempfile.TemporaryDirectory() as temporary:
            pdf = Path(temporary) / 'native.pdf'
            document = pymupdf.open()
            document.new_page().insert_text((40, 50), 'Native electronic source technical text. ' * 4)
            graphic = document.new_page()
            graphic.draw_rect((20, 20, 180, 120))
            document.save(pdf)
            document.close()
            result = preflight(pdf)
            self.assertTrue(result['supported'])
            self.assertEqual(result['pages'], 2)
            self.assertEqual(result['page_details'][1]['kind'], 'GRAPHIC_OR_LOW_TEXT')

    def test_no_text_scan_does_not_trigger_ocr(self):
        with tempfile.TemporaryDirectory() as temporary:
            pdf = Path(temporary) / 'scan.pdf'
            document = pymupdf.open()
            document.new_page().draw_rect((20, 20, 180, 120))
            document.save(pdf)
            document.close()
            self.assertFalse(preflight(pdf)['supported'])

    def test_real_markdown_and_html_images_decoded(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'assets').mkdir()
            Image.new('RGB', (12, 15), 'red').save(root / 'assets/a.png')
            rows = assets.check('![x](assets/a.png) <img src="assets/a.png">', root)
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[0]['width'], 12)

    def test_escaped_path_and_remote_asset_rejected(self):
        for url in ('../outside.png', '%2e%2e/outside.png', 'https://example.test/a.png', 'file:///a.png', r'assets\a.png'):
            with self.subTest(url=url):
                with self.assertRaises(ValueError):
                    assets.local_path(Path.cwd(), url)

    def test_worker_parent_asset_relocated_to_product(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            chunk, output = root / 'chunk', root / 'product'
            (chunk / 'assets/page-001').mkdir(parents=True)
            (chunk / 'markdown').mkdir()
            output.mkdir()
            Image.new('RGB', (8, 9), 'red').save(chunk / 'assets/page-001/a.png')
            text = assets.materialize('![x](../assets/page-001/a.png)', chunk, output)
            self.assertEqual(text, '![x](assets/page-001/a.png)')
            self.assertEqual(len(assets.check(text, output)), 1)

    def test_tampered_sealed_result_not_reused(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'manual.md').write_text('original')
            save(root / 'run_manifest.json', {'artifacts': inventory(root)})
            verify(root)
            (root / 'manual.md').write_text('changed')
            with self.assertRaises(ValueError):
                verify(root)

    def test_duplicate_page_declarations_rejected(self):
        with self.assertRaises(ValueError):
            page_parts('<!-- PAGE 1 -->\na\n<!-- PAGE 1 -->\nb')

    def test_runtime_has_no_benchmark_imports(self):
        for path in (PRODUCT / 'src').rglob('*.py'):
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    self.assertFalse(any(n.name.split('.')[0] in ('m2', 'm3_p1', 's1_gt', 's1_pdf2md') for n in node.names))
                elif isinstance(node, ast.ImportFrom):
                    self.assertNotIn((node.module or '').split('.')[0], ('m2', 'm3_p1', 's1_gt', 's1_pdf2md'))

    def test_vendor_worker_is_frozen_exact_copy(self):
        expected = read(PRODUCT / 'config/baseline_bindings.json')['worker_sha256']
        self.assertEqual(sha(PRODUCT / 'vendor/gpu_parser_worker.py'), expected)

    def test_partial_chunk_then_resume_reuses_successful_chunk(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = root / 'output'
            output.mkdir()
            config = {'chunk_size': 1, 'max_retries': 0, 'worker_timeout_seconds': 20,
                      'authorized_gpu': 'authorized-test-device', 'version': 'test'}
            binding = {'input_sha256': 'synthetic', 'implementation': {'test': 'v1'}}
            completed_calls = []
            failed = {2}

            def run(argv, **kwargs):
                attempt, page = Path(argv[0]), int(argv[1])
                completed_calls.append(page)
                if page in failed:
                    return subprocess.CompletedProcess(argv, 2)
                (attempt / 'markdown').mkdir()
                text = f'<!-- PAGE {page} -->\nPage {page} original technical text.'
                (attempt / 'markdown/document.md').write_text(text)
                save(attempt / 'native_output/native.json', {'pages': [{'page': page, 'middle_json': 'native_output/middle.json'}]})
                save(attempt / 'native_output/middle.json', {'extensions': {'mineru': {'parse_mode': 'txt'}}})
                save(attempt / 'native_output/p2_runtime_config.json', {'effective_configuration': {'parse_mode': 'txt'}})
                save(attempt / 'native_output/text_recognition_policy.json', {'neural_text_recognition_calls_executed': 0, 'events': []})
                save(attempt / 'native_output/worker_manifest.json', {
                    'status': 'success', 'pages': [page], 'cuda_visible_devices': config['authorized_gpu'],
                    'gpu_compute_verified': True, 'parser_version': 'test',
                    'markdown_sha256': sha(attempt / 'markdown/document.md'),
                    'native_sha256': sha(attempt / 'native_output/native.json')})
                return subprocess.CompletedProcess(argv, 0)

            with patch.object(native, 'worker', side_effect=lambda c, p, o, pages: [str(o), str(pages[0])]), \
                    patch.object(native.subprocess, 'run', side_effect=run):
                text, chunks, failures = native.infer(root / 'not-used.pdf', output, 3, config, binding)
                self.assertEqual(list(page_parts(text)), [1, 3])
                self.assertEqual(failures[0]['pages'], [2])
                failed.clear()
                text, chunks, failures = native.infer(root / 'not-used.pdf', output, 3, config, binding)
                self.assertEqual(list(page_parts(text)), [1, 2, 3])
                self.assertFalse(failures)
                self.assertEqual(completed_calls, [1, 2, 3, 2])


if __name__ == '__main__':
    unittest.main()
