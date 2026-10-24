"""Standard-library Windows argv bridge. Runs inside the selected WSL distro."""

import base64
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

PATH_OPTIONS = {'--input', '--input-dir', '--output-dir', '--config', '--models', '--report',
                '--native-cache', '--parser-python', '--coordinator-python', '--cpu-python', '--render-dir', '--bwrap'}


def linux_path(value, cwd):
    if value.startswith(('\\\\', '//')):
        raise ValueError('UNC paths are not supported; use a drive path or WSL absolute path')
    if value.startswith('/'):
        return value
    if re.match(r'^[A-Za-z]:', value):
        if not re.match(r'^[A-Za-z]:[\\/]', value):
            raise ValueError('Drive-relative paths are ambiguous; use an absolute drive path')
        result = subprocess.run(['wslpath', '-a', '-u', value], capture_output=True, text=True, check=True)
        return result.stdout.strip()
    return os.path.abspath(os.path.join(cwd, value.replace('\\', '/')))


def translate(args, cwd):
    result, expect = [], False
    for value in args:
        if expect:
            result.append(linux_path(value, cwd))
            expect = False
            continue
        key, sep, val = value.partition('=')
        if key in PATH_OPTIONS:
            if sep:
                result.append(key + '=' + linux_path(val, cwd))
            else:
                result.append(key)
                expect = True
        else:
            result.append(value)
    if expect:
        raise ValueError('Missing path argument')
    return result


def option(args, name):
    for index, value in enumerate(args):
        if value == name and index + 1 < len(args):
            return args[index + 1]
        if value.startswith(name + '='):
            return value.split('=', 1)[1]
    return None


def interpreter_path(value, cwd):
    return value if '/' not in value and '\\' not in value and ':' not in value else linux_path(value, cwd)


def select_python(explicit, args, product):
    value = explicit or os.environ.get('PDF2MD_WSL_PYTHON')
    if value:
        return value
    sys.path.insert(0, str(product / 'src'))
    from pdf2md_p2.configuration import find_config
    config = option(args, '--config') if args and args[0] != 'init-config' else None
    config = Path(config) if config else (find_config() if not args or args[0] != 'init-config' else None)
    if config is not None:
        data = json.loads(config.read_text(encoding='utf-8-sig'))
        if data.get('coordinator_python'):
            return linux_path(data['coordinator_python'], str(config.parent))
    local = product / '.venv/bin/python'
    return str(local) if local.is_file() else 'python3'


def main():
    # Fixed bridge parameters precede unmodified CLI arguments; no shell parser involved.
    cwd, explicit, encoded, *args = sys.argv[1:]
    cwd = linux_path(cwd, os.getcwd())
    os.chdir(cwd)
    forwarded = json.loads(base64.b64decode(encoded).decode('utf-8'))
    for key, value in forwarded.items():
        os.environ[key] = interpreter_path(value, cwd) if key == 'PDF2MD_WSL_PYTHON' else linux_path(value, cwd) if key in {
            'PDF2MD_CONFIG', 'PDF2MD_MODELS', 'PDF2MD_BWRAP', 'PDF2MD_PARSER_PYTHON',
            'PDF2MD_WSL_PYTHON', 'PDF2MD_CPU_PYTHON'} else value
    product = Path(__file__).resolve().parents[1]
    args = translate(args, cwd)
    python = select_python(interpreter_path(explicit, cwd) if explicit != '-' else None, args, product)
    status = subprocess.run([python, '-B', str(product / 'scripts/pdf2md.py'), *args]).returncode
    if status == 0 and args and args[0] in ('convert', 'batch'):
        sys.path.insert(0, str(product / 'src'))
        from pdf2md_p2.cli import default_output
        output = option(args, '--output-dir')
        if not output:
            source = option(args, '--input' if args[0] == 'convert' else '--input-dir')
            if source:
                output = str(default_output(Path(source), batch=args[0] == 'batch'))
        if output:
            opened = subprocess.run(['wslpath', '-w', output], capture_output=True, text=True)
            if opened.returncode == 0:
                print('Windows output: ' + opened.stdout.strip(), flush=True)
    return status


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1)
