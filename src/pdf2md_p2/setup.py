"""Explicit, user-authorized deployment. Imports only the Python standard library."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import shlex
import subprocess
import sys
import time

from . import __version__
from .bootstrap import configuration_path
from .common import PRODUCT, implementation, read, save, sha
from .configuration import initial, load, resolve_device, validate
from .setup_resources import bwrap_package, models_manifest, prepare_bwrap, prepare_models

CODES = {'READY': 0, 'NEEDS_APPROVAL': 3, 'NEEDS_ACTION': 4, 'FAILED': 1}


def locations():
    return {
        'data': Path(os.environ.get('XDG_DATA_HOME', str(Path.home() / '.local/share'))) / 'pdf2md',
        'state': Path(os.environ.get('XDG_STATE_HOME', str(Path.home() / '.local/state'))) / 'pdf2md',
        'config': Path(os.environ.get('XDG_CONFIG_HOME', str(Path.home() / '.config'))) / 'pdf2md/runtime.json',
    }


def source_fingerprint():
    files = implementation()
    files.update({name: sha(PRODUCT / name) for name in ('requirements-cpu.txt', 'requirements-coordinator.txt')})
    return hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest()


def python312(extra=()):
    candidates = [sys.executable] if sys.version_info[:2] == (3, 12) else []
    candidates.extend(extra)
    found = shutil.which('python3.12')
    if found:
        candidates.append(found)
    for python in dict.fromkeys(candidates):
        try:
            result = subprocess.run([python, '-I', '-B', '-c',
                'import sys,venv,ensurepip; raise SystemExit(sys.version_info[:2] != (3,12))'], capture_output=True)
        except OSError:
            continue
        if result.returncode == 0:
            return os.path.abspath(python)
    return None


def inspect_config(config):
    from .backends import select
    selected, reports = select(config)
    return selected, reports


def build_plan(args):
    paths = locations()
    command = ['python3', 'scripts/pdf2md.py', 'setup', '--yes', '--compute', args.compute]
    if args.offline:
        command.append('--offline')
    for path in args.wheelhouse:
        command.extend(['--wheelhouse', path])
    for flag, value in (('--models', args.models), ('--bwrap', args.bwrap), ('--config', args.config), ('--gpu', args.gpu)):
        if value is not None:
            command.extend([flag, str(value)])
    for device in args.device or []:
        command.extend(['--device', device])
    result = {'status': 'NEEDS_APPROVAL', 'version': __version__, 'requested_compute': args.compute,
              'actions': [], 'missing': [], 'installation_root': str(paths['data']),
              'config_path': str(paths['config']), 'python_dependencies_download_bytes': None,
              'model_download_bytes': 0, 'documents_sent_to_network': False,
              'next_command': shlex.join(command)}
    if platform.system() != 'Linux':
        result.update(status='NEEDS_ACTION', missing=['Linux or WSL2 is required; Windows uses scripts/pdf2md.cmd.'])
        return result
    for path in args.wheelhouse:
        if not Path(path).expanduser().is_dir():
            raise ValueError('Wheelhouse directory does not exist: ' + path)
    selected_path = args.config.expanduser().absolute() if args.config else configuration_path([])
    owner = read(paths['state'] / 'active.json') if (paths['state'] / 'active.json').is_file() else {}
    if not isinstance(owner, dict):
        raise ValueError('Installation receipt must be a JSON object')
    saved_config = None
    if args.config:
        result['config_path'] = str(selected_path)
    if selected_path is not None and (selected_path.exists() or not args.config):
        config = load(selected_path, resolve=False)
        owned = (owner.get('config_path') == str(selected_path) and
                 selected_path.is_file() and owner.get('config_sha256') == sha(selected_path))
        compatible = args.compute == 'auto' or config.get('compute', 'auto') in ('auto', args.compute)
        if args.bwrap and config.get('bubblewrap_path') != str(Path(args.bwrap).expanduser().absolute()):
            compatible = False
        try:
            resolved, reports = inspect_config(config)
            if args.gpu or args.device:
                requested_devices = args.device if args.device is not None else (
                    config.get('device_files') if args.gpu == config.get('authorized_gpu') else None)
                requested = resolve_device(validate(initial(resolved['coordinator_python'],
                    config['model_path'], args.gpu or config['authorized_gpu'], requested_devices,
                    resolved['coordinator_python'])))
                compatible = compatible and resolved.get('resolved_compute') == 'cuda' and all(
                    resolved.get(key) == requested[key] for key in ('authorized_gpu', 'device_files'))
            if compatible and (not owned or owner.get('source_fingerprint') == source_fingerprint()):
                result.update(status='READY', config_path=str(selected_path), actual_compute=resolved['resolved_compute'],
                              coordinator_python=resolved['coordinator_python'],
                              checks={mode: {'ready': report['ready_for_conversion'],
                                  'checks': [{'name': row['name'], 'passed': row['passed'],
                                              **({'error': row['error']} if 'error' in row else {})}
                                             for row in report.get('checks', [])]} for mode, report in reports.items()},
                              selection_reason='EXISTING_VALIDATED_CONFIGURATION', next_command='convert --input /path/manual.pdf')
                return result
        except (OSError, ValueError, subprocess.SubprocessError) as error:
            result['existing_environment_error'] = str(error)
        if not owned:
            result.update(status='NEEDS_ACTION', config_path=str(selected_path), missing=[
                'Existing user configuration is not ready for this request. It will not be overwritten: ' + str(selected_path)])
            return result
        saved_config = config
        result['config_path'] = str(selected_path)
        result['expected_config_sha256'] = sha(selected_path)
    python = python312([saved_config['coordinator_python']] if saved_config else [])
    if python is None:
        result.update(status='NEEDS_ACTION', missing=[
            'Install Python 3.12 with venv/ensurepip support, then rerun setup. System Python is not modified. '
            'Ubuntu 24.04: sudo apt-get install python3.12-venv. For other systems see docs/CONFIGURATION.md.'])
        return result
    result['base_python'] = python
    mode = args.compute
    if mode == 'auto':
        # A device can be present before the backend is installed. Do not mistake missing MinerU for missing GPU.
        if saved_config and saved_config.get('compute') in ('cpu', 'cuda'):
            mode = saved_config['compute']
        else:
            inventory = subprocess.run(['nvidia-smi', '-L'], capture_output=True, text=True) if shutil.which('nvidia-smi') else None
            mask = os.environ.get('CUDA_VISIBLE_DEVICES')
            mode = 'cuda' if inventory is not None and inventory.returncode == 0 and 'GPU' in inventory.stdout and mask != '' else 'cpu'
    result['actual_compute'] = mode
    result['selection_reason'] = 'GPU_PRIORITY' if args.compute == 'auto' and mode == 'cuda' else 'CPU_NO_USABLE_GPU' if args.compute == 'auto' else 'EXPLICIT_' + mode.upper()
    if args.compute == 'auto' and saved_config and saved_config.get('compute') in ('cpu', 'cuda'):
        result['selection_reason'] = 'REVALIDATE_SAVED_' + mode.upper()
    prior_gpu = saved_config if saved_config and saved_config.get('compute') == 'cuda' else {}
    gpu = args.gpu or prior_gpu.get('authorized_gpu', '0')
    devices = args.device if args.device is not None else (
        None if args.gpu and args.gpu != prior_gpu.get('authorized_gpu') else prior_gpu.get('device_files'))
    raw = initial(python, str(paths['data'] / 'models'), gpu, devices, python)
    if mode == 'cuda':
        try:
            raw = resolve_device(validate(raw))
        except (OSError, ValueError, subprocess.SubprocessError) as error:
            result.update(status='NEEDS_ACTION', missing=[str(error)])
            return result
        result['authorized_gpu'] = raw['authorized_gpu']
        result['device_files'] = raw['device_files']
        manifest, _ = models_manifest()
        if args.models and not Path(args.models).expanduser().is_dir():
            result.update(status='NEEDS_ACTION', missing=['Model import directory does not exist: ' + args.models])
            return result
        result['model_download_bytes'] = 0 if args.models or args.offline else sum(manifest['sizes'].values())
        result['model_source'] = str(Path(args.models).expanduser().absolute()) if args.models else (
            'offline managed resources' if args.offline else 'https://huggingface.co/' + manifest['repository'] + '/tree/' + manifest['revision'])
    result['missing'] = ['Selected user runtime has not completed dependency, isolation and real-conversion validation.']
    tool = args.bwrap or (saved_config or {}).get('bubblewrap_path') or os.environ.get('PDF2MD_BWRAP') or shutil.which('bwrap')
    if tool:
        tool = str(Path(tool).expanduser().absolute())
        if not Path(tool).is_file() or not os.access(tool, os.X_OK):
            raise ValueError('Specified bubblewrap is not executable: ' + tool)
        result['bwrap_path'] = tool
    else:
        spec = bwrap_package()
        if not spec or (args.offline and not any((Path(p) / spec['filename']).is_file() for p in args.wheelhouse)):
            result.update(status='NEEDS_ACTION', missing=[
                'Provide --bwrap /path/to/bwrap or install bubblewrap from your distribution. '
                'Debian/Ubuntu: sudo apt-get install bubblewrap. No security policy is changed.'])
            return result
        result['bwrap_package'] = spec
        result['actions'].append('Download/import authenticated distribution bubblewrap and extract it in the user directory (no sudo).')
    if args.offline and not args.wheelhouse:
        result.update(status='NEEDS_ACTION', missing=['Offline installation requires --wheelhouse PATH.'])
        return result
    if mode == 'cuda' and args.offline and not args.models:
        # An already managed bundle can be reused, but a new user must supply explicit media.
        bundle = paths['data'] / 'models' / sha(PRODUCT / 'config/baseline_bindings.json')[:16] / 'MinerU-4_models_torch'
        if not bundle.is_dir():
            result.update(status='NEEDS_ACTION', missing=['Offline GPU setup requires --models PATH.'])
            return result
    requirements = [PRODUCT / 'requirements-cpu.txt'] if mode == 'cpu' else [
        PRODUCT / 'requirements-coordinator.txt', PRODUCT / 'config/s1-mineru-requirements.lock.txt']
    binding = hashlib.sha256((''.join(sha(p) for p in requirements) + __version__ + mode).encode()).hexdigest()[:16]
    result['profile'] = str(paths['data'] / 'profiles' / (mode + '-' + binding))
    result['dependency_source'] = list(args.wheelhouse) if args.offline else 'https://pypi.org/simple'
    result['actions'].extend([
        'Create/reuse only this tool-managed profile; install locked packages in separate virtual environments.',
        'Validate dependencies, isolation, and a real two-page synthetic conversion; keep failure logs.',
        'Publish the user configuration only after conversion and output verification pass.'])
    result['download_note'] = ('Python package size is reported by pip; GPU dependencies may require several GiB. '
                               'The listed model size is exact. No PDF or extracted content is uploaded.')
    return result


class Installer:
    def __init__(self, plan, args):
        self.plan, self.args = plan, args
        self.paths = locations()
        self.source_binding = source_fingerprint()
        self.attempt = self.paths['state'] / 'setup' / str(time.time_ns())
        self.attempt.mkdir(parents=True)
        self.log = self.attempt / 'install.log'
        self.steps = []
        self.env = {k: v for k, v in os.environ.items() if k not in ('PYTHONPATH', 'PYTHONHOME', 'VIRTUAL_ENV') and not k.startswith('PIP_')}
        self.env.update(PIP_CONFIG_FILE='/dev/null', PIP_DISABLE_PIP_VERSION_CHECK='1', PYTHONDONTWRITEBYTECODE='1',
                        HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1')

    def progress(self, message):
        print(message, file=sys.stderr, flush=True)
        with self.log.open('a', encoding='utf-8') as stream:
            stream.write(message + '\n')

    def run(self, command, cwd=None):
        self.progress('Running: ' + repr(list(map(str, command))))
        start = time.monotonic()
        child = subprocess.Popen(list(map(str, command)), cwd=cwd or PRODUCT, env=self.env,
                                 stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors='replace')
        try:
            for line in child.stdout:
                self.progress(line.rstrip())
            status = child.wait()
        except BaseException:
            child.terminate()
            try:
                child.wait(timeout=10)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()
            raise
        self.steps.append({'command': list(map(str, command)), 'exit_code': status, 'seconds': time.monotonic() - start})
        save(self.attempt / 'steps.json', self.steps)
        if status:
            raise ValueError('Setup command failed; see ' + str(self.log))

    def environment(self, name, requirements):
        folder = Path(self.plan['profile']) / name
        python = folder / 'bin/python'
        if not python.is_file() or not (folder / 'pyvenv.cfg').is_file():
            self.run([self.plan['base_python'], '-m', 'venv', str(folder)])
        pyvenv = folder / 'pyvenv.cfg'
        if 'include-system-site-packages = false' not in pyvenv.read_text(encoding='utf-8'):
            raise ValueError('Managed environment must not inherit system packages')
        args = [str(python), '-m', 'pip', 'install', '--no-compile', '--timeout', '30', '--retries', '2']
        if self.args.offline:
            args.append('--no-index')
            for path in self.args.wheelhouse:
                args.extend(['--find-links', str(Path(path).expanduser().absolute())])
        else:
            args.extend(['--index-url', 'https://pypi.org/simple'])
        if requirements.name == 's1-mineru-requirements.lock.txt':
            # The historical freeze includes its installer, which is not a runtime dependency.
            runtime_lock = self.attempt / 'parser-runtime-requirements.txt'
            runtime_lock.write_text('\n'.join(line for line in requirements.read_text(encoding='utf-8').splitlines()
                                               if not line.lower().startswith('pip==')) + '\n', encoding='utf-8')
            requirements = runtime_lock
        self.run([*args, '-r', str(requirements)])
        self.run([str(python), '-m', 'pip', 'check'])
        return str(python)

    def install(self):
        plan, args = self.plan, self.args
        tool = plan.get('bwrap_path') or prepare_bwrap(self.paths['data'], plan['bwrap_package'],
                                                     args.wheelhouse, args.offline, self.run)
        cpu = plan['actual_compute'] == 'cpu'
        coordinator = self.environment('coordinator', PRODUCT / ('requirements-cpu.txt' if cpu else 'requirements-coordinator.txt'))
        parser = coordinator if cpu else self.environment('parser', PRODUCT / 'config/s1-mineru-requirements.lock.txt')
        models = str(self.paths['data'] / 'models' / sha(PRODUCT / 'config/baseline_bindings.json')[:16])
        if not cpu:
            models = prepare_models(models, args.models, args.offline, self.progress)
        config = initial(parser, models, plan.get('authorized_gpu', '0'), plan.get('device_files'), coordinator)
        config.update(compute='cpu' if cpu else 'cuda', cpu_python=coordinator, bubblewrap_path=tool)
        config = validate(config)
        candidate = self.attempt / 'runtime.json'
        save(candidate, config)
        self.run([coordinator, '-B', str(PRODUCT / 'scripts/pdf2md.py'), 'doctor', '--config', str(candidate),
                  '--report', str(self.attempt / 'doctor.json')])
        pdf = self.attempt / 'smoke.pdf'
        code = ("import pymupdf,sys; d=pymupdf.open(); "
                "[(lambda p,i: (p.insert_text((60,70),'Pdf2Md setup validation'), "
                "p.insert_text((60,120),'Native source verification page '+str(i)), "
                "p.insert_text((60,160),'This synthetic document checks offline conversion and source mapping.')))"
                "(d.new_page(),i) for i in (1,2)]; "
                "d.save(sys.argv[1]); d.close()")
        self.run([coordinator, '-I', '-B', '-c', code, str(pdf)])
        output = self.attempt / 'smoke-result'
        entry = [coordinator, '-B', str(PRODUCT / 'scripts/pdf2md.py')]
        self.run([*entry, 'convert', '--config', str(candidate), '--input', str(pdf), '--output-dir', str(output)])
        self.run([*entry, 'verify', '--output-dir', str(output)])
        quality = read(output / 'quality_report.json')
        if quality['completed_pages'] != [1, 2] or quality['compute'] != plan['actual_compute']:
            raise ValueError('Setup smoke test did not finish on the requested backend')
        if not cpu:
            chunks = read(output / 'progress.json')['chunks']
            if not chunks or not all(c['worker'].get('gpu_compute_verified') for c in chunks):
                raise ValueError('Setup GPU smoke test has no actual CUDA execution evidence')
        mapping = read(output / 'source_map.json')
        obj = next((r for r in mapping['objects'] if r['sources'] and r['status'] not in ('AMBIGUOUS', 'UNMAPPED')), None)
        if obj is None:
            raise ValueError('Setup smoke test has no reliable source association')
        self.run([*entry, 'trace', '--output-dir', str(output), '--object-id', obj['id']])
        target = Path(plan['config_path'])
        expected = plan.get('expected_config_sha256')
        if source_fingerprint() != self.source_binding:
            raise ValueError('Implementation changed during setup; rerun setup before publishing a configuration')
        if target.exists() and (not expected or sha(target) != expected):
            raise ValueError('Configuration appeared or changed during setup; refusing overwrite')
        if not args.config:
            discovered = configuration_path([])
            if discovered is not None and discovered != target:
                raise ValueError('A higher-priority configuration appeared during setup; refusing publication')
        save(target, config)
        receipt = {'schema_version': 1, 'status': 'READY', 'version': __version__,
                   'source_fingerprint': self.source_binding, 'config_path': str(target),
                   'config_sha256': sha(target), 'profile': plan['profile'], 'actual_compute': plan['actual_compute'],
                   'requested_compute': args.compute, 'selection_reason': plan['selection_reason'],
                   'smoke_result': str(output), 'log': str(self.log), 'at': datetime.now(timezone.utc).isoformat()}
        save(self.paths['state'] / 'active.json', receipt)
        save(Path(plan['profile']) / 'receipt.json', receipt)
        return {'status': 'READY', 'actual_compute': plan['actual_compute'], 'config_path': str(target),
                'coordinator_python': coordinator, 'log': str(self.log), 'receipt': str(self.paths['state'] / 'active.json'),
                'smoke_result': str(output), 'next_command': 'convert --input /path/manual.pdf'}


def parser():
    result = argparse.ArgumentParser(prog='pdf2md setup', description='Prepare an isolated user runtime; never upload a PDF.')
    result.add_argument('--check', action='store_true', help='Read-only readiness and installation plan')
    result.add_argument('--json', action='store_true', help='One result JSON on stdout; progress on stderr')
    result.add_argument('--yes', action='store_true', help='Execute an installation already approved by the user')
    result.add_argument('--compute', choices=['auto', 'cpu', 'cuda'], default='auto')
    result.add_argument('--offline', action='store_true', help='Use only explicit local dependency/model media')
    result.add_argument('--wheelhouse', action='append', default=[], help='Local wheel directory; repeat for multiple media')
    result.add_argument('--models', help='Read-only GPU model import directory')
    result.add_argument('--bwrap', help='Explicit bubblewrap executable')
    result.add_argument('--config', type=Path, help='Existing config or new config destination; no manual file is overwritten')
    result.add_argument('--gpu', help='Authorized visible GPU index or full UUID')
    result.add_argument('--device', action='append', help='Authorized device node; repeat; MIG needs explicit nodes')
    return result


def main(argv=None):
    args = parser().parse_args(argv)
    installer = None
    try:
        plan = build_plan(args)
        if plan['status'] == 'NEEDS_APPROVAL' and not args.check:
            if not args.yes and sys.stdin.isatty():
                print(json.dumps(plan, ensure_ascii=False, indent=2), file=sys.stderr)
                args.yes = input('Prepare this user runtime? [y/N] ').strip().lower() in ('y', 'yes')
            if args.yes:
                import fcntl
                paths = locations()
                paths['state'].mkdir(parents=True, exist_ok=True)
                with (paths['state'] / 'setup.lock').open('a') as lock:
                    try:
                        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    except BlockingIOError as error:
                        raise ValueError('Another setup is running; concurrent deployment is refused') from error
                    # Recheck after acquiring the lock; another invocation may have just completed.
                    plan = build_plan(args)
                    if plan['status'] == 'NEEDS_APPROVAL':
                        installer = Installer(plan, args)
                        plan = installer.install()
        print(json.dumps(plan, ensure_ascii=False, indent=2))
        return CODES[plan['status']]
    except (OSError, ValueError, KeyError, TypeError, ImportError, RuntimeError, subprocess.SubprocessError, KeyboardInterrupt) as error:
        result = {'status': 'FAILED', 'error': str(error) or 'Setup interrupted; retry the same approved setup command to continue.',
                  'next_command': 'setup --check --json'}
        if installer:
            result['log'] = str(installer.log)
            save(installer.attempt / 'failure.json', result)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 1
