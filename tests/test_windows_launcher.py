"""Exercise the real CMD launcher under Restricted without requiring WSL or models."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

PRODUCT = Path(__file__).resolve().parents[1]


class LauncherContractTests(unittest.TestCase):
    def test_launcher_uses_only_child_process_policy_override(self):
        text = (PRODUCT / 'scripts/pdf2md.cmd').read_text(encoding='ascii')
        self.assertIn('setlocal DisableDelayedExpansion', text)
        self.assertIn('-NoProfile -ExecutionPolicy Bypass -File', text)
        self.assertIn('"%~dp0pdf2md.ps1" %*', text)
        self.assertIn('exit /b %errorlevel%', text)
        self.assertNotIn('Set-ExecutionPolicy', text)

    def test_user_and_skill_windows_examples_use_cmd(self):
        for relative in ('README.md', 'README.zh-CN.md', 'SKILL.md'):
            text = (PRODUCT / relative).read_text(encoding='utf-8')
            self.assertIn('scripts\\pdf2md.cmd', text)
            self.assertNotIn('.\\scripts\\pdf2md.ps1', text)


@unittest.skipUnless(os.name == 'nt', 'Windows PowerShell and CMD integration')
class RestrictedLauncherTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.install = self.root / '中文 & app !'
        self.install.mkdir()
        self.cwd = self.root / 'user working directory'
        self.cwd.mkdir()
        self.record = self.root / 'arguments.json'
        shutil.copyfile(PRODUCT / 'scripts/pdf2md.cmd', self.install / 'pdf2md.cmd')
        # A fixture replaces only the child script to inspect argv, cwd and process policy.
        (self.install / 'pdf2md.ps1').write_text(
            "$data = @{ argv = @($args); cwd = $PWD.Path; policy = (Get-ExecutionPolicy).ToString() }\n"
            "$utf8 = New-Object System.Text.UTF8Encoding($false)\n"
            "[IO.File]::WriteAllText($env:PDF2MD_TEST_RECORD, ($data | ConvertTo-Json -Depth 4), $utf8)\n"
            "exit [int]$env:PDF2MD_TEST_EXIT\n", encoding='ascii')

    def launch(self, arguments, code=0):
        env = dict(os.environ, PDF2MD_TEST_LAUNCHER=str(self.install / 'pdf2md.cmd'),
                   PDF2MD_TEST_RECORD=str(self.record), PDF2MD_TEST_ARGS=json.dumps(arguments),
                   PDF2MD_TEST_EXIT=str(code))
        command = ("$before = Get-ExecutionPolicy; $forward = @($env:PDF2MD_TEST_ARGS | ConvertFrom-Json); "
                   "& $env:PDF2MD_TEST_LAUNCHER @forward; $result = $LASTEXITCODE; "
                   "if ($before -ne 'Restricted' -or (Get-ExecutionPolicy) -ne $before) { exit 99 }; exit $result")
        completed = subprocess.run(['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Restricted',
                                    '-Command', command], cwd=self.cwd, env=env, capture_output=True, timeout=30)
        self.assertEqual(completed.returncode, code, completed.stderr.decode(errors='replace'))
        record = json.loads(self.record.read_text(encoding='utf-8'))
        self.assertEqual(record['policy'], 'Bypass')
        self.assertEqual(record['cwd'], str(self.cwd))
        self.assertEqual(record['argv'], arguments)

    def test_version_argument_under_restricted(self):
        self.launch(['--version'])

    def test_empty_argument_list_and_original_cwd(self):
        self.launch([])

    def test_unicode_spaces_and_shell_metacharacters_are_literal_arguments(self):
        self.launch(['convert', '--input', "D:\\中文 & samples\\(a'b)!.pdf", '--output-dir', 'D:\\new output'])

    def test_named_options_and_equals_values_are_preserved(self):
        self.launch(['doctor', '--wsl-distro', 'Ubuntu-24.04', '--wsl-python=/home/user/env/bin/python'])

    def test_failure_and_batch_exit_codes_are_preserved(self):
        for code in (1, 2, 37):
            with self.subTest(code=code):
                self.launch(['batch', '--input-dir', 'D:\\pdfs'], code)


@unittest.skipUnless(os.name == 'nt', 'Windows setup prerequisites')
class SetupPrerequisiteTests(unittest.TestCase):
    def test_missing_wsl_is_json_action_under_restricted(self):
        env = dict(os.environ, PATH='', PDF2MD_TEST_LAUNCHER=str(PRODUCT / 'scripts/pdf2md.cmd'))
        command = '& $env:PDF2MD_TEST_LAUNCHER setup --check --json; exit $LASTEXITCODE'
        powershell = str(Path(os.environ['SystemRoot']) / 'System32/WindowsPowerShell/v1.0/powershell.exe')
        result = subprocess.run([powershell, '-NoProfile', '-ExecutionPolicy', 'Restricted', '-Command', command],
            env=env, capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 4, result.stderr)
        self.assertEqual(json.loads(result.stdout.decode('utf-8-sig'))['status'], 'NEEDS_ACTION')

    def test_missing_windows_option_value_is_argument_error(self):
        env = dict(os.environ, PDF2MD_TEST_LAUNCHER=str(PRODUCT / 'scripts/pdf2md.cmd'))
        result = subprocess.run(['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Restricted', '-Command',
            '& $env:PDF2MD_TEST_LAUNCHER setup --wsl-distro; exit $LASTEXITCODE'], env=env, capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 2, result.stderr)


if __name__ == '__main__':
    unittest.main()
