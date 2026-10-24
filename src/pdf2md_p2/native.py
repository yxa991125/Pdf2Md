"""Resumable chunk orchestration around the byte-identical frozen B3 worker."""

import os
from pathlib import Path
import subprocess
import time

from .assets import materialize
from .common import inventory, page_parts, read, save, sha, verify
from .sandbox import worker
from .source import graphic_page


def infer(pdf, output, total, config, binding, inspection=None):
    parts, chunks, failures = {}, [], []
    graphics = {r['page'] for r in (inspection or {}).get('page_details', [])
                if r['native_characters'] == 0 and r['images'] == 0 and r['vector_paths'] > 0}
    graphic_records = []
    work = output / '_work'
    work.mkdir(exist_ok=True)
    for first in range(1, total + 1, config['chunk_size']):
        wanted = list(range(first, min(first + config['chunk_size'], total + 1)))
        rendered = sorted(set(wanted) & graphics)
        for number in rendered:
            parts[number], record = graphic_page(pdf, number, output)
            graphic_records.append(record)
        wanted = [number for number in wanted if number not in graphics]
        if not wanted:
            continue
        root = work / f'{first:04d}-{wanted[-1]:04d}'
        root.mkdir(exist_ok=True)
        expected = {'input_sha256': binding['input_sha256'], 'implementation': binding['implementation'],
                    'config': config, 'pages': wanted}
        completed = None
        attempts = sorted(root.glob('attempt-*'))
        for attempt in reversed(attempts):
            if (attempt / 'chunk_manifest.json').is_file():
                verify(attempt, 'chunk_manifest.json')
                if read(attempt / 'chunk_manifest.json')['configuration'] != expected:
                    raise ValueError('Sealed chunk configuration mismatch')
                completed = attempt
                break
        if completed is None:
            for number in range(len(attempts) + 1, len(attempts) + config['max_retries'] + 2):
                attempt = root / f'attempt-{number:02d}'
                attempt.mkdir(exist_ok=False)
                argv = worker(config, pdf, attempt, wanted)
                save(attempt / 'configuration.json', {'configuration': expected, 'argv': argv})
                started = time.monotonic()
                save(attempt / 'state.json', {'status': 'RUNNING', 'coordinator_pid': os.getpid()})
                try:
                    with (attempt / 'stdout.log').open('w') as stdout, (attempt / 'stderr.log').open('w') as stderr:
                        result = subprocess.run(argv, stdout=stdout, stderr=stderr, timeout=config['worker_timeout_seconds'])
                    if result.returncode:
                        raise RuntimeError('Worker return code ' + str(result.returncode))
                    meta = read(attempt / 'native_output/worker_manifest.json')
                    cpu = config.get('resolved_compute') == 'cpu'
                    compute_valid = (meta.get('compute') == 'cpu' and meta.get('cpu_execution_verified') is True
                                     and meta.get('GPU_inference_performed') is False) if cpu else (
                        meta.get('cuda_visible_devices') == config['authorized_gpu'] and meta.get('gpu_compute_verified'))
                    if (meta['status'] != 'success' or meta['pages'] != wanted
                            or not compute_valid or meta['parser_version'] != config['version']
                            or sha(attempt / 'markdown/document.md') != meta['markdown_sha256']
                            or sha(attempt / 'native_output/native.json') != meta['native_sha256']):
                        raise ValueError('Unbound or unauthorized native output')
                    if list(page_parts((attempt / 'markdown/document.md').read_text())) != wanted:
                        raise ValueError('Native chunk incomplete')
                    if not cpu:
                        native_pages = read(attempt / 'native_output/native.json')['pages']
                        for record in native_pages:
                            middle = read(attempt / record['middle_json'])
                            if middle['extensions']['mineru']['parse_mode'] != 'txt':
                                raise ValueError('Native-only profile returned a non-txt page')
                    mode = read(attempt / 'native_output/p2_runtime_config.json')
                    if mode['effective_configuration']['parse_mode'] != 'txt':
                        raise ValueError('Native profile guard missing or inconsistent')
                    policy = read(attempt / 'native_output/text_recognition_policy.json')
                    if policy['neural_text_recognition_calls_executed'] != 0:
                        raise ValueError('Text OCR unexpectedly executed')
                    save(attempt / 'state.json', {'status': 'COMPLETED', 'wall_seconds': time.monotonic() - started})
                    save(attempt / 'chunk_manifest.json', {'configuration': expected, 'artifacts': inventory(attempt)})
                    completed = attempt
                    break
                except Exception as exc:
                    save(attempt / 'state.json', {'status': 'FAILED', 'error': repr(exc), 'wall_seconds': time.monotonic() - started})
        if completed is None:
            failures.append({'pages': wanted, 'attempts': [p.name for p in root.glob('attempt-*')], 'status': 'FAILED_RETAINED'})
        else:
            native = (completed / 'markdown/document.md').read_text(encoding='utf-8')
            native = materialize(native, completed, output)
            parts.update(page_parts(native))
            chunks.append({'pages': wanted, 'attempt': completed.relative_to(output).as_posix(),
                           'worker': read(completed / 'native_output/worker_manifest.json')})
        # A partial output remains useful and never claims the missing pages were completed.
        text = '\n\n'.join(f'<!-- PAGE {page} -->\n\n{parts[page]}' for page in sorted(parts))
        (output / 'manual.md').write_text(text, encoding='utf-8')
        save(output / 'progress.json', {'completed_pages': sorted(parts), 'failed_chunks': failures, 'chunks': chunks})
    text = '\n\n'.join(f'<!-- PAGE {page} -->\n\n{parts[page]}' for page in sorted(parts))
    (output / 'manual.md').write_text(text, encoding='utf-8')
    save(output / 'source_graphics.json', {'pages': graphic_records,
         'role': 'Native zero-text vector page preservation, not a general screenshot enhancement'})
    return text, chunks, failures
