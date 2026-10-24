"""Read-only environment checks. No installs, model downloads or CUDA inference."""

import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import stat
import subprocess

from .common import PRODUCT, read, sha
from .platform_runtime import bwrap_path, driver_environment, is_wsl

COORDINATOR = {'PyMuPDF': '1.26.0', 'Pillow': '12.3.0', 'markdown-it-py': '4.2.0', 'mdurl': '0.1.2'}
PARSER = {'mineru': '4.0.12', 'torch': '2.14.1', 'torchvision': '0.29.1', 'pypdfium2': '5.14.0'}
PROBE = '''import importlib.metadata as m,json,platform,sys
print(json.dumps({'python':platform.python_version(),'executable':sys.executable,'prefix':sys.prefix,
'base_prefix':sys.base_prefix,'packages':sorted([(d.metadata['Name'],d.version) for d in m.distributions()])}))'''


def run(argv, timeout=60):
    result = subprocess.run(argv, capture_output=True, text=True, timeout=timeout,
        env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1', 'HF_HUB_OFFLINE': '1', 'TRANSFORMERS_OFFLINE': '1'})
    if result.returncode:
        raise ValueError('Command failed: ' + repr(argv) + '\n' + (result.stderr or result.stdout)[-3000:])
    return result.stdout


def inspect(config, namespaces=True):
    checks, facts = [], {}

    def check(name, callback):
        try:
            detail = callback()
            checks.append({'name': name, 'passed': True, 'detail': detail})
            return detail
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
            checks.append({'name': name, 'passed': False, 'error': str(error)})
            return None

    def linux():
        if platform.system() != 'Linux':
            raise ValueError('Conversion requires Linux, NVIDIA CUDA and bubblewrap; no unsandboxed fallback')
        return platform.platform()

    facts['platform'] = check('supported_platform', linux)
    facts['runtime'] = {'wsl2': is_wsl(), 'kernel': platform.release()}
    def isolation_tool():
        path = bwrap_path(config)
        return {'path': path, 'sha256': sha(path), 'version': run([path, '--version']).strip(),
                'driver_environment': driver_environment(),
                'wsl_libcuda_sha256': sha('/usr/lib/wsl/lib/libcuda.so.1') if is_wsl() else None}
    facts['isolation_tool'] = check('isolation_tool', isolation_tool)
    for kind, key, required in (('coordinator', 'coordinator_python', COORDINATOR), ('parser', 'parser_python', PARSER)):
        def packages(key=key, required=required):
            data = json.loads(run([config[key], '-I', '-B', '-c', PROBE]))
            if data['python'].split('.')[:2] != ['3', '12']:
                raise ValueError('This backend profile is validated with Python 3.12 only')
            if Path(data['prefix']).resolve() != Path(config[key]).parent.parent.resolve():
                raise ValueError('Use the environment actual bin/python path, not a user-wide launcher or wrapper')
            installed = {name.lower().replace('_', '-'): version for name, version in data['packages']}
            mismatches = {name: {'expected': version, 'actual': installed.get(name.lower().replace('_', '-'))}
                for name, version in required.items() if installed.get(name.lower().replace('_', '-')) != version}
            if mismatches:
                raise ValueError('Pinned backend dependencies differ: ' + json.dumps(mismatches))
            return data
        facts[kind] = check(kind + '_environment', packages)

    def models():
        bindings = read(PRODUCT / 'config/baseline_bindings.json')
        if sha(PRODUCT / 'vendor/gpu_parser_worker.py') != bindings['worker_sha256']:
            raise ValueError('Bundled native worker changed')
        root = Path(config['model_path'])
        differences = []
        for name, expected in bindings['models'].items():
            path = root / name
            if not path.is_file() or sha(path) != expected:
                differences.append(name)
        if differences:
            raise ValueError('Offline model bundle missing or different: ' + ', '.join(differences))
        return {'worker_sha256': bindings['worker_sha256'], 'verified_model_files': len(bindings['models'])}
    facts['bindings'] = check('offline_models_and_worker', models)

    def devices():
        for name in config['device_files']:
            if not stat.S_ISCHR(Path(name).stat().st_mode):
                raise ValueError('Not a character device: ' + name)
            mode = os.R_OK if '/nvidia-caps/' in name else os.R_OK | os.W_OK
            if not os.access(name, mode):
                raise ValueError('Device access denied: ' + name)
        if config['authorized_gpu'] not in run(['nvidia-smi', '-L']):
            raise ValueError('Configured GPU UUID is not present; do not substitute another device')
        inventory = run(['nvidia-smi', '--query-gpu=index,uuid,name,driver_version', '--format=csv,noheader,nounits'])
        return {'authorized_gpu': config['authorized_gpu'], 'device_files': config['device_files'],
                'driver_inventory': inventory.strip(), 'CUDA_compute_probed': False}
    facts['device'] = check('authorized_device_visibility', devices)

    def namespace():
        from .sandbox import base
        script = "import json,socket; n=[v for _,v in socket.if_nameindex() if v!='lo']; print(json.dumps({'unexpected_interfaces':n})); raise SystemExit(bool(n))"
        return json.loads(run([*base(config, coordinator=True), '--chdir', '/', config['coordinator_python'], '-I', '-B', '-c', script]))
    if namespaces:
        check('network_namespace', namespace)
    ready = namespaces and all(row['passed'] for row in checks)
    fingerprint = hashlib.sha256(json.dumps(facts, sort_keys=True).encode()).hexdigest()
    return {'ready_for_conversion': ready, 'checks': checks, 'facts': facts,
        'environment_fingerprint': fingerprint, 'config': config,
        'scope': 'Same pinned MinerU/model profile; no claim of other model, OS or hardware compatibility',
        'model_downloads': False, 'GPU_inference_performed': False,
        'semantic_fidelity_certified': False}
