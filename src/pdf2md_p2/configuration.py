"""Deployment paths and GPU selection with a first-visible-device default."""

import csv
import io
from pathlib import Path
import os
import re
import subprocess
import sys

from .common import PRODUCT, read

POLICY = {
    'schema_version': 1, 'engine': 'MinerU', 'version': '4.0.12',
    'tier': 'basic', 'parse_mode': 'txt', 'profile': 'pdf2md-native-no-text-ocr-0.1',
    'neural_text_recognition': 'DENIED', 'image_analysis': True,
    'legacy_builtin_detection_and_formula_recognition': 'RETAINED_UNCHANGED',
    'source_enhancement': False, 'multi_parser_fusion': False,
}
DEFAULTS = {'chunk_size': 50, 'max_retries': 1, 'worker_timeout_seconds': 14400}
PATHS = ('coordinator_python', 'parser_python', 'model_path')
DEVICE = re.compile(r'/dev/nvidia(?:[0-9]+|ctl|-uvm|-uvm-tools)|/dev/nvidia-caps/nvidia-cap[0-9]+')
GPU = re.compile(r'(?:GPU|MIG)-[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}')


def validate(data, base=None):
    allowed = set(POLICY) | set(DEFAULTS) | set(PATHS) | {'authorized_gpu', 'device_files'}
    unknown = set(data) - allowed
    if unknown:
        raise ValueError('Unknown runtime settings: ' + ', '.join(sorted(unknown)))
    for key, value in POLICY.items():
        if key in data and (type(data[key]) is not type(value) or data[key] != value):
            raise ValueError('Unsupported engine/policy setting: ' + key)
    result = {**POLICY, **DEFAULTS, 'authorized_gpu': '0', 'device_files': None, **data}
    for key in PATHS:
        value = result.get(key)
        if not isinstance(value, str) or not value.strip():
            raise ValueError('Configure ' + key)
        path = Path(value).expanduser()
        if not path.is_absolute():
            path = Path(base or Path.cwd()) / path
        # Resolving a venv's bin/python symlink would silently select the base interpreter.
        result[key] = os.path.abspath(path)
    selector = result['authorized_gpu']
    if not isinstance(selector, str) or not (GPU.fullmatch(selector) or re.fullmatch(r'0|[1-9][0-9]*', selector)):
        raise ValueError('Use a nonnegative visible GPU index or a full GPU-UUID or MIG-UUID')
    devices = result.get('device_files')
    if devices is None:
        if selector.startswith('MIG-'):
            raise ValueError('MIG requires explicit device_files including capability nodes')
        return _validate_numbers(result)
    if not isinstance(devices, list) or not devices or any(not isinstance(p, str) or not DEVICE.fullmatch(p) for p in devices):
        raise ValueError('device_files must explicitly list authorized NVIDIA device nodes')
    if len(devices) != len(set(devices)):
        raise ValueError('Duplicate device node')
    if sum(bool(re.fullmatch(r'/dev/nvidia[0-9]+', p)) for p in devices) != 1:
        raise ValueError('Authorize exactly one physical NVIDIA device node')
    for required in ('/dev/nvidiactl', '/dev/nvidia-uvm'):
        if required not in devices:
            raise ValueError('Required authorized device node missing: ' + required)
    if result['authorized_gpu'].startswith('MIG-') and sum('/nvidia-caps/' in p for p in devices) < 2:
        raise ValueError('MIG requires explicitly authorized GPU-instance and compute-instance capability nodes')
    result['device_files'] = sorted(devices)
    return _validate_numbers(result)


def _validate_numbers(result):
    for key, minimum in (('chunk_size', 1), ('max_retries', 0), ('worker_timeout_seconds', 1)):
        if type(result[key]) is not int or result[key] < minimum:
            raise ValueError('Invalid integer setting: ' + key)
    return result


def resolve_device(config):
    selector = config['authorized_gpu']
    if GPU.fullmatch(selector) and config['device_files'] is not None:
        return config
    try:
        result = subprocess.run(['nvidia-smi', '--query-gpu=index,uuid,minor_number,mig.mode.current',
            '--format=csv,noheader,nounits'], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError) as error:
        raise ValueError('Cannot detect GPU with nvidia-smi: ' + str(error)) from error
    if result.returncode:
        raise ValueError('GPU detection failed: ' + result.stderr.strip())
    rows = []
    for row in csv.reader(io.StringIO(result.stdout), skipinitialspace=True):
        if not row:
            continue
        row = [value.strip() for value in row]
        if (len(row) != 4 or not row[0].isdigit() or not row[2].isdigit()
                or not GPU.fullmatch(row[1]) or not row[1].startswith('GPU-')):
            raise ValueError('Invalid nvidia-smi GPU inventory')
        rows.append({'index': row[0], 'uuid': row[1], 'minor': row[2], 'mig': row[3]})
    if not rows:
        raise ValueError('No NVIDIA GPU found')
    rows.sort(key=lambda row: int(row['index']))
    if selector.isdigit():
        visible = rows
        if 'CUDA_VISIBLE_DEVICES' in os.environ:
            visible = []
            for token in os.environ['CUDA_VISIBLE_DEVICES'].split(','):
                token = token.strip()
                if token.startswith('MIG-'):
                    raise ValueError('MIG selection requires --gpu MIG-UUID and explicit --device nodes')
                matches = [row for row in rows if row['index'] == token or row['uuid'] == token]
                if len(matches) != 1 or matches[0] in visible:
                    raise ValueError('CUDA_VISIBLE_DEVICES has an unavailable or duplicate GPU: ' + token)
                visible.extend(matches)
        index = int(selector)
        if index >= len(visible):
            raise ValueError('Visible GPU index is unavailable: ' + selector)
        selected = visible[index]
    else:
        matches = [row for row in rows if row['uuid'] == selector]
        if len(matches) != 1:
            raise ValueError('Configured GPU UUID is not present: ' + selector)
        selected = matches[0]
    if selected['mig'].lower() not in ('disabled', 'n/a', '[n/a]', 'not supported', '[not supported]'):
        raise ValueError('MIG-enabled GPU requires --gpu MIG-UUID and explicit --device nodes')
    devices = config['device_files']
    physical = '/dev/nvidia' + selected['minor']
    if devices is None:
        devices = [physical, '/dev/nvidiactl', '/dev/nvidia-uvm']
    elif physical not in devices:
        raise ValueError('device_files do not match the selected GPU: ' + physical)
    return validate({**config, 'authorized_gpu': selected['uuid'], 'device_files': devices})


def default_paths():
    paths = {'coordinator_python': sys.executable,
        'parser_python': os.environ.get('PDF2MD_PARSER_PYTHON', sys.executable),
        'model_path': os.environ.get('PDF2MD_MODELS', str(PRODUCT.parent / 'models'))}
    return {key: os.path.abspath(Path(value).expanduser()) for key, value in paths.items()}


def find_config():
    if os.environ.get('PDF2MD_CONFIG'):
        return Path(os.environ['PDF2MD_CONFIG']).expanduser()
    candidates = [Path.cwd() / 'runtime.local.json', PRODUCT / 'runtime.local.json',
        Path(os.environ.get('XDG_CONFIG_HOME', str(Path.home() / '.config'))) / 'pdf2md/runtime.json']
    return next((path for path in candidates if path.is_file()), None)


def load(path=None, overrides=None):
    path = Path(path).expanduser().resolve() if path is not None else find_config()
    defaults = default_paths()
    data = read(path) if path is not None else {}
    if not isinstance(data, dict):
        raise ValueError('Runtime config must be a JSON object')
    overrides = {key: value for key, value in (overrides or {}).items() if value is not None}
    for key in PATHS:
        if key in overrides:
            overrides[key] = os.path.abspath(Path(overrides[key]).expanduser())
    if 'authorized_gpu' in overrides and 'device_files' not in overrides:
        overrides['device_files'] = None
    return resolve_device(validate({**defaults, **data, **overrides}, path.parent if path else Path.cwd()))


def initial(parser_python=None, models=None, gpu='0', devices=None, coordinator_python=None):
    paths = default_paths()
    return {'schema_version': 1, 'coordinator_python': coordinator_python or sys.executable,
        'parser_python': parser_python or paths['parser_python'], 'model_path': models or paths['model_path'],
        'authorized_gpu': gpu, 'device_files': devices, **DEFAULTS}
