"""Dependency-free entry routing; package installation is only performed by setup."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

from .common import PRODUCT


def option(args, name):
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument(name)
    parsed, _ = parser.parse_known_args(args)
    return getattr(parsed, name[2:].replace('-', '_'))


def configuration_path(args):
    explicit = option(args, '--config')
    if explicit or os.environ.get('PDF2MD_CONFIG'):
        return Path(explicit or os.environ['PDF2MD_CONFIG']).expanduser().absolute()
    candidates = [Path.cwd() / 'runtime.local.json', PRODUCT / 'runtime.local.json',
                  Path(os.environ.get('XDG_CONFIG_HOME', str(Path.home() / '.config'))) / 'pdf2md/runtime.json']
    return next((p for p in candidates if p.is_file()), None)


def selected_python(args):
    path = configuration_path(args)
    if path is None:
        return None
    data = json.loads(path.read_text(encoding='utf-8-sig'))
    if not isinstance(data, dict):
        raise ValueError('Runtime config must be a JSON object: ' + str(path))
    python = data.get('coordinator_python')
    if not python:
        return None
    target = Path(python).expanduser()
    return os.path.abspath(target if target.is_absolute() else path.parent / target)


def main():
    args = sys.argv[1:]
    from .organization_cli import is_organization_command, runtime_guidance
    organized = is_organization_command(args)
    if args and args[0] == 'setup':
        from .setup import main as setup
        return setup(args[1:])
    try:
        python = selected_python(args) if (args and args[0] != 'init-config' and '--version' not in args
            and '--help' not in args and os.environ.get('P2_ISOLATED') != '1') else None
        # Do not resolve venv symlinks: the same binary can belong to different environments.
        if python and os.path.abspath(sys.executable) != python:
            if not Path(python).is_file():
                suffix = '.' if organized else '. Run setup --check --json.'
                raise ValueError('Configured Python is missing: ' + python + suffix)
            return subprocess.run([python, '-B', str(PRODUCT / 'scripts/pdf2md.py'), *args]).returncode
        from .cli import main as cli
        return cli()
    except (OSError, ValueError, ImportError, subprocess.SubprocessError) as error:
        print(str(error), file=sys.stderr)
        print(runtime_guidance() if organized else 'Setup: python3 scripts/pdf2md.py setup --check --json',
              file=sys.stderr)
        return 1
