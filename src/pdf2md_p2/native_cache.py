"""Explicit reuse of sealed own-worker chunks for CPU-only quality reprocessing."""

from pathlib import Path
import shutil

from .assets import materialize
from .common import read, save, sha, verify, page_parts, within
from .source import graphic_page

PARSE_BINDINGS = ('scripts/native_bootstrap.py', 'scripts/native_policy.py', 'vendor/gpu_parser_worker.py',
                  'config/baseline_bindings.json', 'config/s1-mineru-requirements.lock.txt',
                  'config/coordinator-requirements.lock.txt', 'src/pdf2md_p2/source.py')
PARSE_BINDINGS += ('src/pdf2md_p2/configuration.py', 'src/pdf2md_p2/environment.py',
                   'src/pdf2md_p2/sandbox.py', 'src/pdf2md_p2/native.py', 'src/pdf2md_p2/platform_runtime.py')
PARSE_BINDINGS += ('src/pdf2md_p2/backends.py', 'scripts/cpu_worker.py', 'config/cpu_bindings.json')


def load(cache, pdf, output, config, binding, inspection):
    previous = read(cache / 'configuration.json')
    if not binding.get('environment_fingerprint') or previous.get('environment_fingerprint') != binding['environment_fingerprint']:
        raise ValueError('Own native cache environment fingerprint differs')
    if previous['input_sha256'] != binding['input_sha256'] or previous['configuration'] != config:
        raise ValueError('Own native cache input or runtime configuration differs')
    if any(previous['implementation'].get(key) != binding['implementation'].get(key) for key in PARSE_BINDINGS):
        raise ValueError('Own native cache parsing/model bindings changed')
    parts, chunks = {}, []
    for root in sorted((cache / '_work').glob('*')):
        candidates = [p for p in sorted(root.glob('attempt-*')) if (p / 'chunk_manifest.json').is_file()]
        if not candidates:
            raise ValueError('Native cache contains an incomplete chunk')
        attempt = candidates[-1]
        verify(attempt, 'chunk_manifest.json')
        manifest = read(attempt / 'chunk_manifest.json')
        wanted = manifest['configuration']['pages']
        native = (attempt / 'markdown/document.md').read_text(encoding='utf-8')
        if list(page_parts(native)) != wanted or any(p in parts for p in wanted):
            raise ValueError('Native cache pages not uniquely bound')
        native = materialize(native, attempt, output)
        parts.update(page_parts(native))
        copied = output / 'native_cache_records' / root.name
        (copied / 'native_output').mkdir(parents=True, exist_ok=True)
        for name in ('worker_manifest.json', 'text_recognition_policy.json', 'p2_runtime_config.json'):
            source, target = attempt / 'native_output' / name, copied / 'native_output' / name
            if target.exists() and sha(target) != sha(source):
                raise ValueError('Partial cache materialization changed')
            if not target.exists():
                shutil.copyfile(source, target)
        native_data = read(attempt / 'native_output/native.json')
        locations = ['native_output/native.json'] + [row['middle_json'] for row in native_data['pages'] if 'middle_json' in row]
        for relative in locations:
            source, target = within(attempt, relative), within(copied, relative)
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists() and sha(target) != sha(source):
                raise ValueError('Cached source mapping data changed')
            if not target.exists():
                shutil.copyfile(source, target)
        for relative, destination in (('markdown/document.md', 'markdown/document.md'),
                                      ('chunk_manifest.json', 'original_chunk_manifest.json')):
            source, target = attempt / relative, copied / destination
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists() and sha(target) != sha(source):
                raise ValueError('Independent native snapshot changed')
            if not target.exists():
                shutil.copyfile(source, target)
        save(copied / 'upstream_chunk.json', {'attempt': str(attempt),
             'chunk_manifest_sha256': sha(attempt / 'chunk_manifest.json'),
             'full_original_native_files_retained_in_upstream_cache': True})
        chunks.append({'pages': wanted, 'attempt': copied.relative_to(output).as_posix(),
                       'worker': read(attempt / 'native_output/worker_manifest.json'), 'reused_native_inference': True})
    graphical = []
    for row in inspection['page_details']:
        if row['native_characters'] == 0 and row['images'] == 0 and row['vector_paths'] > 0:
            if row['page'] in parts:
                raise ValueError('Own cache unexpectedly inferred a source-only graphic page')
            parts[row['page']], record = graphic_page(pdf, row['page'], output)
            graphical.append(record)
    if sorted(parts) != list(range(1, inspection['pages'] + 1)):
        raise ValueError('Own native cache is not a complete whole-document base')
    save(output / 'source_graphics.json', {'pages': graphical})
    save(output / 'native_cache_reference.json', {'cache': str(cache), 'input_sha256': binding['input_sha256'],
         'original_version': previous['version'], 'parsing_bindings_verified': list(PARSE_BINDINGS),
         'native_inference_performed_this_run': False, 'all_worker_chunk_hashes_verified': True})
    return '\n\n'.join(f'<!-- PAGE {p} -->\n\n{parts[p]}' for p in sorted(parts)), chunks, []
