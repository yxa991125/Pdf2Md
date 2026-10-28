import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import ModuleType
import unittest
from unittest.mock import Mock, patch

from pdf2md_p2 import bootstrap, cli, organization, organization_cli, organization_query
from pdf2md_p2.common import PRODUCT


class OrganizationCLITests(unittest.TestCase):
    def run_cli(self, arguments):
        output, errors = io.StringIO(), io.StringIO()
        with patch.object(sys, 'argv', ['pdf2md.py', *arguments]), \
                patch.object(cli, 'runtime', side_effect=AssertionError('Organization must not select a parser backend')), \
                contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            try:
                code = cli.main()
            except SystemExit as error:
                code = error.code
        return code, output.getvalue(), errors.getvalue()

    def test_organize_forwards_only_explicit_source_and_options(self):
        with patch.object(organization, 'organize', return_value={'status': 'ORGANIZED'}) as action:
            code, output, errors = self.run_cli(['organize', '--from-result', 'frozen', '--workspace', 'new',
                                               '--input', 'source.pdf', '--soft-chars', '1234'])
        self.assertEqual((code, errors), (0, ''))
        self.assertEqual(json.loads(output)['status'], 'ORGANIZED')
        action.assert_called_once_with(Path('frozen'), Path('new'), Path('source.pdf'), 1234)

    def test_invalid_soft_target_is_argument_error(self):
        code, _, error = self.run_cli(['organize', '--from-result', 'frozen', '--workspace', 'new', '--soft-chars', '0'])
        self.assertEqual(code, 2)
        self.assertIn('positive', error)

    def test_search_forwards_intersection_and_selected_snapshot(self):
        immutable = {'revision': 'rev-000003'}
        with patch.object(organization, 'snapshot', return_value=immutable) as load, \
                patch.object(organization_query, 'search', return_value={'results': []}) as action:
            code, output, _ = self.run_cli(['search', '--workspace', 'new', '--revision', 'rev-000003',
                '--query', 'DATA_STATUS', '--scope', 'scope-one', '--pages', '20:30', '--kind', 'table',
                '--unit-id', 'u-one', '--match', 'exact', '--page-mode', 'intersect', '--expand-context'])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output), {'results': []})
        load.assert_called_once_with(Path('new'), 'rev-000003')
        action.assert_called_once_with(immutable, 'DATA_STATUS', 'exact', 'scope-one', (20, 30),
                                       'table', 'u-one', 'intersect', True)

    def test_page_syntax_errors_do_not_reach_search(self):
        with patch.object(organization_query, 'search') as action:
            for value in ('0', '20:10', '-2', '1:2:3', '1,2'):
                code, _, _ = self.run_cli(['search', '--workspace', 'new', '--pages=' + value])
                self.assertEqual(code, 2, value)
        action.assert_not_called()

    def test_scopes_reports_revision_and_raw_scope_provenance(self):
        data = {'workspace_id': 'w', 'document_id': 'd', 'revision': 'rev-000002',
                'scopes': [{'scope_id': 's', 'kind': 'VIRTUAL_SCOPE'}]}
        with patch.object(organization, 'snapshot', return_value=data):
            code, output, _ = self.run_cli(['scopes', '--workspace', 'new'])
        result = json.loads(output)
        self.assertEqual(code, 0)
        self.assertEqual(result['scope_count'], 1)
        self.assertEqual(result['revision'], 'rev-000002')
        self.assertEqual(result['scopes'], data['scopes'])

    def test_workspace_trace_and_verify_use_organization_not_legacy_runtime(self):
        with patch.object(organization, 'snapshot', return_value={'revision': 'rev-000001'}) as load, \
                patch.object(organization_query, 'trace', return_value={'source_refs': []}) as action:
            code, _, _ = self.run_cli(['trace', '--workspace', 'new', '--unit-id', 'u-one',
                                      '--input', 'source.pdf', '--render-dir', 'evidence'])
        self.assertEqual(code, 0)
        load.assert_called_once_with(Path('new'), None)
        action.assert_called_once_with({'revision': 'rev-000001'}, 'u-one', Path('source.pdf'), Path('evidence'))
        with patch.object(organization, 'verify_workspace', return_value={'status': 'VERIFIED'}) as verify:
            code, _, _ = self.run_cli(['verify', '--workspace', 'new', '--revision', 'rev-000002'])
        self.assertEqual(code, 0)
        verify.assert_called_once_with(Path('new'), 'rev-000002')

    def test_trace_and_verify_reject_mixed_or_missing_selectors(self):
        for command in (
                ['trace', '--workspace', 'new', '--object-id', 'old'],
                ['trace', '--workspace', 'new', '--line', '1'],
                ['trace', '--output-dir', 'old', '--unit-id', 'u-one'],
                ['trace', '--output-dir', 'old', '--object-id', 'old', '--revision', 'rev-000001'],
                ['verify', '--output-dir', 'old', '--revision', 'rev-000001'],
                ['trace', '--output-dir', 'old', '--workspace', 'new', '--unit-id', 'u-one'],
                ['trace', '--workspace', 'new'],
                ['verify', '--output-dir', 'old', '--workspace', 'new']):
            with self.subTest(command=command):
                self.assertEqual(self.run_cli(command)[0], 2)

    def test_legacy_trace_object_and_line_calls_remain_compatible(self):
        fake = ModuleType('pdf2md_p2.source_map')
        fake.trace = Mock(return_value={'objects': []})
        with patch.dict(sys.modules, {'pdf2md_p2.source_map': fake}):
            code, _, _ = self.run_cli(['trace', '--output-dir', 'old', '--object-id', 'obj-old'])
            self.assertEqual(code, 0)
            fake.trace.assert_called_with(Path('old').resolve(), 'obj-old', None, None, None)
            code, _, _ = self.run_cli(['trace', '--output-dir', 'old', '--line', '10'])
            self.assertEqual(code, 0)
            fake.trace.assert_called_with(Path('old').resolve(), None, 10, None, None)

    def test_propose_preserves_exact_utf8_text_and_source_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            text = Path(directory) / 'correction.txt'
            text.write_bytes('中文 😀 DATA_STATUS\r\n'.encode('utf-8'))
            evidence = Path(directory) / 'source.json'
            evidence.write_text('[{"page":1,"bbox":[1,2,3,4]}]', encoding='utf-8')
            with patch.object(organization, 'propose', return_value={'status': 'PROPOSED', 'published': False}) as action:
                code, output, _ = self.run_cli(['revise', 'propose', '--workspace', 'new', '--unit-id', 'u-one',
                    '--operation', 'replace', '--text-file', str(text), '--reason', 'source spelling',
                    '--proposer', 'tool', '--judgment-method', 'SOURCE_COMPARISON', '--source-evidence', str(evidence)])
            self.assertEqual(code, 0)
            self.assertFalse(json.loads(output)['published'])
            operation, = action.call_args.args[1]
            self.assertEqual(operation['after'], '中文 😀 DATA_STATUS\r\n')
            self.assertEqual(operation['source_evidence'], [{'page': 1, 'bbox': [1, 2, 3, 4]}])
            self.assertEqual(action.call_args.args[2:], ('tool', 'source spelling', 'SOURCE_COMPARISON'))

    def test_move_proposal_does_not_require_or_read_text_file(self):
        with patch.object(organization, 'propose', return_value={'status': 'PROPOSED'}) as action:
            code, _, _ = self.run_cli(['revise', 'propose', '--workspace', 'new', '--unit-id', 'u-one',
                '--operation', 'move', '--target-scope', 'scope-new', '--reason', 'membership', '--proposer', 'tool'])
        self.assertEqual(code, 0)
        self.assertEqual(action.call_args.args[1], [{'unit_id': 'u-one', 'operation': 'move', 'target_scope_id': 'scope-new'}])

    def test_proposal_operation_arguments_are_not_silently_ignored(self):
        base = ['revise', 'propose', '--workspace', 'new', '--unit-id', 'u-one', '--reason', 'test', '--proposer', 'tool']
        for arguments in (['--operation', 'replace'], ['--operation', 'annotate'],
                          ['--operation', 'move'], ['--operation', 'move', '--target-scope', 's', '--text-file', 'f'],
                          ['--operation', 'replace', '--target-scope', 's', '--text-file', 'f']):
            with self.subTest(arguments=arguments):
                self.assertEqual(self.run_cli(base + arguments)[0], 2)

    def test_apply_requires_explicit_expected_revision_and_forwards_it(self):
        base = ['revise', 'apply', '--workspace', 'new', '--proposal', 'proposal.json']
        self.assertEqual(self.run_cli(base)[0], 2)
        with patch.object(organization, 'apply_proposal', return_value={'status': 'APPLIED'}) as action:
            self.assertEqual(self.run_cli(base + ['--expected-revision', 'rev-000001'])[0], 0)
        action.assert_called_once_with(Path('new'), Path('proposal.json'), 'rev-000001')

    def test_check_history_rollback_and_export_forward_correctly(self):
        examples = [
            ('check_proposal', ['revise', 'check', '--workspace', 'new', '--proposal', 'p.json'], (Path('new'), Path('p.json'))),
            ('history', ['revise', 'history', '--workspace', 'new'], (Path('new'),)),
            ('rollback', ['revise', 'rollback', '--workspace', 'new', '--to-revision', 'rev-000001',
                          '--expected-revision', 'rev-000003', '--reason', 'restore'],
             (Path('new'), 'rev-000001', 'rev-000003', 'restore')),
            ('export', ['export', '--workspace', 'new', '--format', 'single', '--output-dir', 'exported',
                        '--revision', 'rev-000002'], (Path('new'), Path('exported'), 'single', 'rev-000002'))]
        for name, command, expected in examples:
            with self.subTest(name=name), patch.object(organization, name, return_value={'status': 'OK'}) as action:
                self.assertEqual(self.run_cli(command)[0], 0)
                action.assert_called_once_with(*expected)

    def test_source_visibility_is_bound_to_selected_revision(self):
        mapping = {'input_sha256': 'bound'}
        snap = {'workspace_id': 'w', 'document_id': 'd', 'revision': 'rev-000004', 'source_map': mapping}
        with patch.object(organization, 'snapshot', return_value=snap), \
                patch.object(organization_query, 'source_visibility', return_value={'semantic_coverage_measured': False}) as action:
            code, output, _ = self.run_cli(['source-visibility', '--workspace', 'new', '--input', 'original.pdf'])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output)['revision'], 'rev-000004')
        action.assert_called_once_with(Path('original.pdf'), mapping)

    def test_operation_errors_have_nonzero_exit_and_no_success_json(self):
        with patch.object(organization, 'snapshot', side_effect=ValueError('Body/index revision mismatch')):
            code, output, error = self.run_cli(['search', '--workspace', 'new', '--query', 'DATA_STATUS'])
        self.assertEqual(code, 1)
        self.assertEqual(output, '')
        self.assertIn('Body/index revision mismatch', error)

    def test_all_new_help_commands_are_available_without_backend(self):
        for command in ('organize', 'scopes', 'search', 'source-visibility', 'revise', 'export'):
            code, output, _ = self.run_cli([command, '--help'])
            self.assertEqual(code, 0)
            self.assertIn('usage:', output)


class OrganizationDependencyGuidanceTests(unittest.TestCase):
    def test_known_missing_dependency_names_runtime_and_existing_environment(self):
        commands = [
            ['organize', '--from-result', 'frozen', '--workspace', 'new'],
            ['search', '--workspace', 'new', '--query', 'DATA_STATUS'],
            ['trace', '--workspace', 'new', '--unit-id', 'u-one'],
            ['verify', '--workspace', 'new'],
        ]
        for module, package in organization_cli.DEPENDENCIES.items():
            for command in commands:
                with self.subTest(module=module, command=command), patch.object(
                        organization_cli, '_dispatch', side_effect=ModuleNotFoundError(name=module)):
                    code, output, errors = OrganizationCLITests().run_cli(command)
                self.assertEqual(code, 1)
                self.assertEqual(output, '')
                self.assertIn(package, errors)
                self.assertIn(sys.executable, errors)
                self.assertIn('--wsl-python', errors)
                self.assertIn('not a GPU', errors)
                self.assertNotIn('setup --check', errors)
                self.assertNotIn('Traceback', errors)

    def test_unknown_import_failure_is_not_mislabeled_as_missing_dependency(self):
        with patch.object(organization_cli, '_dispatch', side_effect=ModuleNotFoundError(
                "No module named 'internal_mistake'", name='internal_mistake')):
            code, _, errors = OrganizationCLITests().run_cli(['scopes', '--workspace', 'new'])
        self.assertEqual(code, 1)
        self.assertIn('internal_mistake', errors)
        self.assertNotIn('runtime dependency unavailable', errors)

    def test_bootstrap_config_failure_does_not_suggest_gpu_setup_for_d1(self):
        for command in (['organize', '--from-result', 'frozen', '--workspace', 'new'],
                        ['verify', '--workspace=new'], ['trace', '--workspace', 'new', '--unit-id', 'u-one']):
            errors = io.StringIO()
            with self.subTest(command=command), patch.object(sys, 'argv', ['pdf2md.py', *command]), \
                    patch.object(bootstrap, 'selected_python', side_effect=ValueError('Configured Python is missing')), \
                    contextlib.redirect_stderr(errors):
                code = bootstrap.main()
            self.assertEqual(code, 1)
            self.assertIn('coordinator', errors.getvalue())
            self.assertNotIn('setup --check', errors.getvalue())

    def test_conversion_bootstrap_retains_existing_setup_guidance(self):
        errors = io.StringIO()
        with patch.object(sys, 'argv', ['pdf2md.py', 'convert', '--input', 'input.pdf']), \
                patch.object(bootstrap, 'selected_python', side_effect=ValueError('Configured Python is missing')), \
                contextlib.redirect_stderr(errors):
            code = bootstrap.main()
        self.assertEqual(code, 1)
        self.assertIn('setup --check --json', errors.getvalue())

    def test_standard_library_only_help_needs_no_pillow_or_parser_environment(self):
        with tempfile.TemporaryDirectory() as directory:
            env = dict(os.environ, XDG_CONFIG_HOME=directory)
            env.pop('PDF2MD_CONFIG', None)
            env.pop('PYTHONPATH', None)
            for command in ('organize', 'search', 'revise', 'export'):
                result = subprocess.run([sys.executable, '-B', '-S', str(PRODUCT / 'scripts/pdf2md.py'),
                                         command, '--help'], cwd=directory, env=env,
                                        text=True, capture_output=True, timeout=30)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn('usage:', result.stdout)
                self.assertEqual(result.stderr, '')


class OrganizationWindowsBridgeTests(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location('d1_wsl_bridge', PRODUCT / 'scripts/wsl_entry.py')
        self.bridge = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.bridge)

    def test_all_new_file_arguments_translate_without_rewriting_identifiers(self):
        paths = ('--from-result', '--workspace', '--proposal', '--text-file', '--source-evidence')
        for option in paths:
            with self.subTest(option=option), patch.object(self.bridge, 'linux_path', return_value='/mnt/d/中文 文件') as action:
                result = self.bridge.translate([option, 'D:\\中文 文件', '--unit-id', 'u-one', '--scope', 'scope-one'], '/cwd')
            self.assertEqual(result, [option, '/mnt/d/中文 文件', '--unit-id', 'u-one', '--scope', 'scope-one'])
            action.assert_called_once_with('D:\\中文 文件', '/cwd')

    def test_equals_path_arguments_translate_but_source_text_is_never_shell_code(self):
        value = 'D:\\sample space\\text.txt'
        with patch.object(self.bridge, 'linux_path', return_value='/mnt/d/sample space/text.txt'):
            result = self.bridge.translate(['--text-file=' + value, '--reason', '$(not executed); Unicode 中文'], '/cwd')
        self.assertEqual(result, ['--text-file=/mnt/d/sample space/text.txt', '--reason', '$(not executed); Unicode 中文'])


if __name__ == '__main__':
    unittest.main()
