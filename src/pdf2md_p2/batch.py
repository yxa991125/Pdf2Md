"""Sequential corpus tests through the real CLI; no benchmark or scoring dependency."""

from datetime import datetime, timezone
from pathlib import Path
import subprocess
import platform
import time

from .common import PRODUCT, implementation, read, save, sha


def convert_directory(args):
    if platform.system() != 'Linux':
        raise ValueError('Batch conversion requires Linux; use preflight/doctor for local inspection')
    source, output = args.input_dir.resolve(), args.output_dir.resolve()
    if not source.is_dir():
        raise ValueError('Input directory missing')
    if output.is_relative_to(source) or source.is_relative_to(output) or output.is_relative_to(PRODUCT):
        raise ValueError('Batch output must be separate from input and product directories')
    from .cli import runtime
    config = runtime(args)
    paths = source.rglob('*') if args.recursive else source.iterdir()
    pdfs = sorted(p for p in paths if p.is_file() and not p.is_symlink() and p.suffix.lower() == '.pdf')
    if not pdfs:
        raise ValueError('No local PDF files found')
    inputs = [{'path': str(p), 'relative': p.relative_to(source).as_posix(), 'sha256': sha(p)} for p in pdfs]
    binding = {'inputs': inputs, 'runtime': config, 'implementation': implementation()}
    if output.exists() and not args.resume:
        raise ValueError('Batch output exists; use a new directory or explicit --resume')
    output.mkdir(parents=True, exist_ok=True)
    import fcntl
    with (output / '.batch.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        old = output / 'batch_configuration.json'
        if old.exists() and read(old) != binding:
            raise ValueError('Batch inputs/configuration/source changed; start a new test batch')
        save(old, binding)
        runtime_file = output / 'effective_runtime.json'
        save(runtime_file, config)
        logs = output / 'logs'
        logs.mkdir(exist_ok=True)
        rows = []
        for ordinal, item in enumerate(inputs, 1):
            job = f"{ordinal:04d}-{item['sha256'][:12]}"
            destination = output / 'documents' / job
            attempt = len(list(logs.glob(job + '-*.stdout.log'))) + 1
            command = [config['coordinator_python'], str(PRODUCT / 'scripts/pdf2md.py'), 'convert',
                '--config', str(runtime_file), '--input', item['path'],
                '--output-dir', str(destination), '--offline']
            if args.resume:
                command.append('--resume')
            started = time.monotonic()
            try:
                if sha(Path(item['path'])) != item['sha256']:
                    raise ValueError('Input changed after batch enumeration')
                with (logs / f'{job}-{attempt:02d}.stdout.log').open('w') as stdout, \
                        (logs / f'{job}-{attempt:02d}.stderr.log').open('w') as stderr:
                    result = subprocess.run(command, stdout=stdout, stderr=stderr)
                status_file = destination / 'run_status.json'
                status = read(status_file).get('status', 'UNKNOWN') if status_file.exists() else 'FAILED_BEFORE_CONVERSION'
                seal = destination / 'run_manifest.json'
                bound = (result.returncode == 0 and seal.is_file()
                    and read(seal)['configuration']['input_sha256'] == item['sha256']
                    and sha(Path(item['path'])) == item['sha256'])
                if result.returncode == 0 and not bound:
                    status = 'FAILED_INPUT_BINDING'
                elif result.returncode != 0 and status in ('SUCCESS', 'SUCCESS_WITH_WARNINGS'):
                    status = 'FAILED_RESUME_OR_PREFLIGHT'
                rows.append({**item, 'output': destination.relative_to(output).as_posix(), 'returncode': result.returncode,
                    'status': status, 'wall_seconds': time.monotonic() - started, 'argv': command,
                    'integrity_sealed': bound})
            except (OSError, ValueError, KeyError) as error:
                rows.append({**item, 'status': 'LAUNCH_FAILED', 'returncode': 1, 'error': str(error), 'integrity_sealed': False})
            save(output / 'batch_summary.json', {'documents': rows, 'total': len(inputs), 'finished': len(rows),
                'complete': len(rows) == len(inputs), 'semantic_fidelity_certified': False})
        summary = {'finished_at': datetime.now(timezone.utc).isoformat(), 'documents': rows, 'total': len(inputs),
            'finished': len(rows), 'complete': True, 'engineering_passed': all(r['integrity_sealed'] for r in rows),
            'semantic_fidelity_certified': False}
        attempts = output / 'batch_attempts'
        attempts.mkdir(exist_ok=True)
        save(attempts / f'attempt-{len(list(attempts.glob("attempt-*.json"))) + 1:03d}.json', summary)
        save(output / 'batch_summary.json', summary)
        print(__import__('json').dumps({'total': len(inputs), 'sealed': sum(r['integrity_sealed'] for r in rows),
            'summary': str(output / 'batch_summary.json'), 'semantic_fidelity_certified': False}), flush=True)
        return 0 if summary['engineering_passed'] else 2
