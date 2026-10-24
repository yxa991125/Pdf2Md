"""Offline source-only namespaces, restricted to the already authorized MIG."""

import os
import json
from pathlib import Path
import shutil
import sys

from .common import PRODUCT
from .platform_runtime import bwrap_path, driver_environment


def base(config, coordinator=False):
    bwrap = bwrap_path(config)
    command = [bwrap]
    command += ['--unshare-user', '--unshare-net', '--unshare-ipc', '--unshare-uts', '--unshare-cgroup-try'] if coordinator else ['--unshare-all']
    command += ['--die-with-parent', '--new-session']
    for name in ('/usr', '/etc', '/lib', '/lib64', '/bin', '/sbin', '/sys'):
        if Path(name).exists():
            command += ['--ro-bind', name, name]
    paths = set()
    cpu = config.get('resolved_compute') == 'cpu'
    for value in (config['coordinator_python'], config['cpu_python'] if cpu else config['parser_python']):
        python = Path(value)
        if not python.is_file():
            raise ValueError('Existing locked environment missing: ' + value)
        paths.update((python.parent.parent, python.resolve().parents[1]))
    for path in sorted(paths):
        command += ['--ro-bind', str(path), str(path)]
    # A user-local tool must also exist in the coordinator's nested worker mount.
    command += ['--ro-bind', bwrap, bwrap]
    command += ['--bind', '/proc', '/proc'] if coordinator else ['--proc', '/proc']
    if not coordinator and Path('/proc/driver/nvidia').exists():
        command += ['--ro-bind', '/proc/driver/nvidia', '/proc/driver/nvidia']
    command += ['--dev', '/dev', '--tmpfs', '/tmp']
    for path in config['device_files']:
        if not Path(path).exists():
            raise ValueError('Authorized device missing: ' + path)
        command += ['--dev-bind', path, path]
    for name, value in {'HOME': '/tmp', 'XDG_CACHE_HOME': '/tmp/cache', 'CUDA_VISIBLE_DEVICES': '' if cpu else config['authorized_gpu'],
                        'HF_HUB_OFFLINE': '1', 'TRANSFORMERS_OFFLINE': '1', 'MINERU_MODEL_SOURCE': 'local',
                        'DO_NOT_TRACK': '1', 'NO_PROXY': '*', 'PYTHONDONTWRITEBYTECODE': '1',
                        'S1_NETWORK_ISOLATED': '1', 'PYTHONPATH': '/product/src',
                        **({} if cpu else driver_environment())}.items():
        command += ['--setenv', name, value]
    return command


def coordinator(config, pdf, output, remaining, native_cache=None):
    command = base(config, coordinator=True)
    if native_cache is not None:
        command += ['--ro-bind', str(native_cache), '/native-cache']
        remaining = [*remaining, '--native-cache', '/native-cache']
    if config.get('resolved_compute') != 'cpu':
        command += ['--ro-bind', str(Path(config['model_path']) / 'MinerU-4_models_torch'),
                    str(Path(config['model_path']) / 'MinerU-4_models_torch')]
    command += ['--ro-bind', str(PRODUCT), '/product', '--ro-bind', str(pdf), '/input.pdf',
                '--bind', str(output), '/output', '--setenv', 'P2_ISOLATED', '1',
                '--setenv', 'PDF2MD_EFFECTIVE_CONFIG', json.dumps(config, sort_keys=True), '--chdir', '/product',
                config['coordinator_python'], '-m', 'pdf2md_p2', 'convert', '--input', '/input.pdf',
                '--output-dir', '/output', '--offline', '--resume', *remaining]
    return command


def worker(config, pdf, output, pages):
    if config['parse_mode'] != 'txt':
        raise ValueError('P2 native-only profile does not permit auto/ocr inference')
    command = base(config)
    if config.get('resolved_compute') == 'cpu':
        return [*command, '--ro-bind', str(PRODUCT / 'scripts/cpu_worker.py'), '/cpu_worker.py',
                '--ro-bind', str(pdf), '/input.pdf', '--bind', str(output), '/output',
                '--setenv', 'OMP_NUM_THREADS', '8', '--setenv', 'OPENBLAS_NUM_THREADS', '8',
                '--chdir', '/output', config['cpu_python'], '-B', '/cpu_worker.py',
                '--input', '/input.pdf', '--output', '/output', '--pages', ','.join(map(str, pages))]
    command += ['--ro-bind', str(PRODUCT / 'vendor/gpu_parser_worker.py'), '/worker.py',
                '--ro-bind', str(PRODUCT / 'scripts/native_bootstrap.py'), '/bootstrap.py',
                '--ro-bind', str(PRODUCT / 'scripts/native_policy.py'), '/native_policy.py',
                '--setenv', 'P2_NATIVE_MODE', 'txt',
                '--ro-bind', str(pdf), '/input.pdf', '--ro-bind', config['model_path'], '/models',
                '--bind', str(output), '/output', '--chdir', '/output',
                config['parser_python'], '/bootstrap.py', '--parser', 'b3', '--input', '/input.pdf',
                '--output', '/output', '--mode', 'core', '--pages', ','.join(map(str, pages)),
                '--model-path', '/models', '--expected-gpu-uuid', config['authorized_gpu']]
    return command


def access_probe():
    import socket
    result = {name: Path(name).exists() for name in ('/gold', '/references', '/benchmark')}
    result['unexpected_network_interfaces'] = any(name != 'lo' for _, name in socket.if_nameindex())
    for name in ('/product', '/input.pdf'):
        result[name + '_not_read_only'] = not bool(os.statvfs(name).f_flag & os.ST_RDONLY)
    return result
