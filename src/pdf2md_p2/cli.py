"""Offline product CLI. Historical parsers and benchmark answers are not dependencies."""

import argparse
import json
from datetime import datetime, timezone
import os
from pathlib import Path
import subprocess
import sys
import time

from . import __version__
from .common import PRODUCT, implementation, inventory, page_parts, read, save, sha, verify
from .sandbox import access_probe, coordinator
from .configuration import load as load_config, validate as validate_config, initial


def runtime(args):
    config = load_config(args.config, {key: getattr(args, option, None) for key, option in (
        ('parser_python', 'parser_python'), ('model_path', 'models'),
        ('authorized_gpu', 'gpu'), ('device_files', 'device'), ('bubblewrap_path', 'bwrap'),
        ('compute', 'compute'), ('cpu_python', 'cpu_python'))}, resolve=False)
    if config.get('compute') == 'cpu' and (getattr(args, 'gpu', None) or getattr(args, 'device', None)):
        raise ValueError('CPU mode conflicts with explicit GPU authorization arguments')
    pinned = None
    if getattr(args, 'resume', False):
        batch = hasattr(args, 'input_dir')
        source = args.input_dir if batch else args.input
        output = (args.output_dir or default_output(source, batch=batch)).resolve()
        path = output / ('batch_configuration.json' if batch else 'configuration.json')
        if path.is_file():
            previous = read(path)
            pinned = previous['runtime' if batch else 'configuration']
    from .backends import select
    config, args.backend_availability = select(config, pinned, probe_all=getattr(args, 'command', None) == 'doctor')
    return config


def default_output(source, batch=False):
    source = source.resolve()
    name = (source.name if batch else source.stem) + '_pdf2md'
    parent = source.parent
    if parent.is_relative_to(PRODUCT):
        parent = PRODUCT.parent / 'pdf2md-output'
    return parent / name


def convert(args):
    pdf = args.input.resolve()
    if not pdf.is_file():
        raise ValueError('Input PDF missing: ' + str(pdf))
    isolated = os.environ.get('P2_ISOLATED') == '1'
    if isolated:
        config = validate_config(json.loads(os.environ['PDF2MD_EFFECTIVE_CONFIG']))
    else:
        config = runtime(args)
    for name in ('chunk_size', 'max_retries'):
        value = getattr(args, name)
        if value is not None:
            config[name] = value
    output = (args.output_dir or default_output(pdf)).resolve()
    if pdf.is_relative_to(output) or output == PRODUCT or output.is_relative_to(PRODUCT):
        raise ValueError('Output must not contain source PDF or be inside the product distribution')
    if not isolated:
        if output.exists() and not args.resume:
            raise ValueError('Output exists; use a new directory or explicit --resume')
        from .environment import inspect
        environment = inspect(config)
        environment['backend_availability'] = getattr(args, 'backend_availability', {})
        if not environment['ready_for_conversion']:
            print(json.dumps(environment, ensure_ascii=False, indent=2), file=sys.stderr)
            return 1
        output.mkdir(parents=True, exist_ok=True)
        report = output / 'environment_report.json'
        if not report.exists():
            save(report, environment)
        remaining = []
        for name in ('chunk_size', 'max_retries'):
            if getattr(args, name) is not None:
                remaining += ['--' + name.replace('_', '-'), str(getattr(args, name))]
        command = coordinator(config, pdf, output, remaining, args.native_cache.resolve() if args.native_cache else None)
        command[1:1] = ['--setenv', 'PDF2MD_ENVIRONMENT_FINGERPRINT', environment['environment_fingerprint']]
        # The outer process does no PDF processing and never imports benchmark modules.
        result = subprocess.run(command)
        if result.returncode == 0:
            print(json.dumps({'output': str(output), 'manual': str(output / 'manual.md')}), flush=True)
        return result.returncode
    from .assets import check as check_assets
    from .identifiers import check as check_identifiers
    from .native import infer
    from .source import preflight, empty_content_pages
    from .repair import apply as repair_identifiers
    from .glyphs import materialize as source_glyphs
    from .objects import check as check_objects
    from .quantities import check as check_quantities
    from .text_pages import recover as recover_text_pages
    probes = access_probe()
    if any(probes.values()):
        raise ValueError('Source-only isolation failed')
    binding = {'version': __version__, 'input_sha256': sha(pdf), 'configuration': config,
               'implementation': implementation(), 'native_cache_requested': bool(args.native_cache),
               'environment_fingerprint': os.environ['PDF2MD_ENVIRONMENT_FINGERPRINT']}
    if (output / 'run_manifest.json').exists():
        if not args.resume or read(output / 'run_manifest.json')['configuration'] != binding:
            raise ValueError('Sealed result/configuration cannot be overwritten')
        verify(output)
        if (output / 'source_map.json').is_file():
            from .source_map import validate
            validate(output)
        print(__import__('json').dumps({'status': 'REUSED_VERIFIED', 'output': str(output)}), flush=True)
        return 0
    import fcntl
    with (output / '.conversion.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        prior = output / 'configuration.json'
        if prior.exists() and read(prior) != binding:
            raise ValueError('Resume configuration or implementation changed')
        save(prior, binding)
        save(output / 'run_status.json', {'status': 'RUNNING', 'pid': os.getpid(), 'at': datetime.now(timezone.utc).isoformat()})
        started = time.monotonic()
        try:
            inspection = preflight(pdf)
            save(output / 'input_preflight.json', inspection)
            if not inspection['supported']:
                raise ValueError('OUT_OF_SCOPE_NATIVE_TEXT_LAYER_REQUIRED; no OCR fallback')
            cpu = config.get('resolved_compute') == 'cpu'
            lock_data = read(PRODUCT / ('config/cpu_bindings.json' if cpu else 'config/baseline_bindings.json'))
            if not cpu:
                if sha(PRODUCT / 'vendor/gpu_parser_worker.py') != lock_data['worker_sha256']:
                    raise ValueError('Frozen B3 worker changed')
                changed_models = [r for r, h in lock_data['models'].items() if sha(Path(config['model_path']) / r) != h]
                if changed_models:
                    raise ValueError('Frozen offline model bytes changed')
            (output / 'assets').mkdir(exist_ok=True)
            if args.native_cache:
                from .native_cache import load
                native_text, chunks, failures = load(args.native_cache, pdf, output, config, binding, inspection)
            else:
                native_text, chunks, failures = infer(pdf, output, inspection['pages'], config, binding, inspection)
            (output / 'worker_manual.md').write_text(native_text, encoding='utf-8')
            policy_events = [event for chunk in chunks
                            for event in read(output / chunk['attempt'] / 'native_output/text_recognition_policy.json')['events']]
            native_text, glyph_records = source_glyphs(pdf, native_text, policy_events, output)
            (output / 'native_before_source_text.md').write_text(native_text, encoding='utf-8')
            native_text, text_page_records = recover_text_pages(pdf, native_text, output)
            (output / 'native_manual.md').write_text(native_text, encoding='utf-8')
            identifiers = check_identifiers(pdf, native_text)
            text, changes = repair_identifiers(pdf, native_text, identifiers, output)
            (output / 'manual.md').write_text(text, encoding='utf-8')
            save(output / 'change_log.json', changes)
            post_checks = check_identifiers(pdf, text)
            images = check_assets(text, output)
            save(output / 'identifier_checks.json', identifiers)
            save(output / 'identifier_checks_after.json', post_checks)
            object_checks = check_objects(pdf, text)
            save(output / 'object_checks.json', object_checks)
            quantity_checks = check_quantities(pdf, text)
            save(output / 'quantity_checks.json', quantity_checks)
            from .source_map import build as build_source_map
            mapping = build_source_map(pdf, output, chunks)
            content_gaps = empty_content_pages(inspection, text)
            glyph_gaps = [event for event in policy_events if event['kind'] == 'NATIVE_GLYPH_UNRESOLVED_NO_OCR']
            complete = list(page_parts(text)) == list(range(1, inspection['pages'] + 1)) and not failures and not content_gaps and not glyph_gaps
            status = 'SUCCESS_WITH_WARNINGS' if complete else ('PARTIAL' if chunks else 'FAILED')
            quality = {'status': status, 'version': __version__, 'completed_pages': list(page_parts(text)),
                       'compute': config.get('resolved_compute', 'cuda'), 'source_mapping_states': mapping['states'],
                       'release_level': 'ENGINEERING_TEST_PROTOTYPE', 'semantic_fidelity_certified': False,
                       'expected_pages': inspection['pages'], 'failed_chunks': failures, 'assets': images,
                       'empty_source_content_pages': content_gaps,
                       'native_text_recognition_policy_events': policy_events,
                       'unresolved_native_glyph_spans': glyph_gaps,
                       'source_graphic_pages': read(output / 'source_graphics.json')['pages'],
                       'source_glyph_candidates': len(glyph_records),
                       'source_glyph_images': sum(r.get('automatic_addition', True) for r in glyph_records),
                       'object_check_states': object_checks['states'],
                       'source_only_text_pages': len(text_page_records),
                       'quantity_check_states': quantity_checks['states'],
                       'technical_identifiers': identifiers['counts'], 'applied_repairs': len(changes['operations']),
                       'technical_identifiers_before': identifiers['counts'],
                       'technical_identifiers_after': post_checks['counts'],
                       'G1': 'SOURCE_CONTEXT_CHECKS_EXECUTED_NOT_GLOBAL_CERTIFICATION',
                       'G2': 'BOUNDED_OBJECT_CHECKS_EXECUTED_UNRESOLVED_RELATIONS_RETAINED',
                       'G3': 'SEPARATE_INDEPENDENT_EVALUATION_REQUIRED',
                       'whole_document_accuracy': None, 'visual_completeness': 'UNRESOLVED',
                       'formula_semantics': 'UNRESOLVED', 'footnote_attachment': 'UNRESOLVED',
                       'warnings': ['Prototype: no whole-manual semantic certification',
                                    'Identifier ambiguities are preserved in identifier_checks.json',
                                    'Neural text recognition denied; existing layout/math paths retained',
                                    'Source glyph images preserve pixels, not editable text or certified gain'],
                       'wall_seconds': time.monotonic() - started}
            save(output / 'quality_report.json', quality)
            save(output / 'provenance.json', {'input_sha256': binding['input_sha256'],
                 'native_inference_performed_this_run': not bool(args.native_cache),
                 'page_mapping': {str(p): p for p in page_parts(text)}, 'chunks': chunks,
                 'runtime_access_probe': probes, 'network': 'bubblewrap_unshared',
                 'native_worker_sha256': sha(PRODUCT / 'scripts/cpu_worker.py') if cpu else lock_data['worker_sha256'],
                 'model_bindings': lock_data['resources'] if cpu else lock_data['models'],
                 'compute': config.get('resolved_compute', 'cuda'), 'engine': config['engine'],
                 'requested_compute': config.get('compute', 'auto'), 'selection_reason': config.get('selection_reason'),
                 'neural_text_OCR': 'DENIED_BY_RUNTIME_POLICY',
                 'source_graphic_pages': read(output / 'source_graphics.json')['pages'],
                 'source_glyph_images': glyph_records,
                 'source_only_text_pages': text_page_records,
                 'Gold_or_scorer_dependency': False, 'S1_S2_enabled': False,
                 'applied_repairs': changes['operations'], 'baseline_configuration': config['profile'],
                 'native_builtin_recognition': config['legacy_builtin_detection_and_formula_recognition']})
            save(output / 'run_status.json', {'status': status, 'at': datetime.now(timezone.utc).isoformat()})
            if complete:
                if sha(pdf) != binding['input_sha256']:
                    raise ValueError('Source PDF changed during conversion; refusing a completed seal')
                save(output / 'run_manifest.json', {'configuration': binding, 'status': status, 'artifacts': inventory(output)})
                verify(output)
            print(__import__('json').dumps({'status': status, 'pages': len(page_parts(text)),
                  'expected_pages': inspection['pages'], 'markdown_sha256': sha(output / 'manual.md')}), flush=True)
            return 0 if complete else 2
        except Exception as exc:
            save(output / 'run_status.json', {'status': 'FAILED', 'error': repr(exc)})
            save(output / 'quality_report.json', {'status': 'FAILED', 'error': repr(exc),
                 'whole_document_accuracy': None, 'partial_results_preserved': True})
            raise


def main():
    parser = argparse.ArgumentParser(description='Offline native electronic PDF -> manual.md + assets/',
        epilog='Quick start: python scripts/pdf2md.py convert --input manual.pdf\n'
               'Setup and configuration: docs/CONFIGURATION.md', formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--version', action='version', version=__version__)
    sub = parser.add_subparsers(dest='command', required=True)
    p = sub.add_parser('init-config', help='Write a new deployment config; defaults to the first visible GPU')
    p.add_argument('--config', type=Path, default=Path('runtime.local.json'), help='Output config (default: runtime.local.json)')
    p.add_argument('--parser-python', help='MinerU Python (default: current interpreter or PDF2MD_PARSER_PYTHON)')
    p.add_argument('--models', help='Model root (default: ../models beside this distribution or PDF2MD_MODELS)')
    p.add_argument('--gpu', default='0', help='Visible GPU index (default: 0), full GPU-UUID or MIG-UUID')
    p.add_argument('--device', action='append', help='Override detected device nodes; repeat for each node; required for MIG')
    p.add_argument('--coordinator-python', default=sys.executable)
    p.add_argument('--bwrap', help='Path to bubblewrap executable')
    p.add_argument('--compute', choices=['auto', 'cpu', 'cuda'], default='auto')
    p.add_argument('--cpu-python', help='CPU backend Python (default: coordinator Python)')
    p = sub.add_parser('doctor', help='Check pinned environments, model hashes, device permissions and network isolation; no inference')
    runtime_options(p)
    p.add_argument('--report', type=Path)
    p = sub.add_parser('preflight', help='Inspect a PDF without loading models or using GPU')
    p.add_argument('--input', type=Path, required=True)
    p = sub.add_parser('verify', help='Verify a completed output seal and all local image references, not semantic fidelity')
    p.add_argument('--output-dir', type=Path, required=True)
    p = sub.add_parser('trace', help='Query source locations for a final Markdown object or line; JSON output')
    p.add_argument('--output-dir', type=Path, required=True)
    query = p.add_mutually_exclusive_group(required=True)
    query.add_argument('--object-id')
    query.add_argument('--line', type=int)
    p.add_argument('--input', type=Path, help='Exact original PDF, required only for source screenshots')
    p.add_argument('--render-dir', type=Path, help='New external directory for requested source screenshots')
    p = sub.add_parser('batch', help='Convert a local PDF directory sequentially, preserving failures and per-run logs')
    runtime_options(p)
    p.add_argument('--input-dir', type=Path, required=True)
    p.add_argument('--output-dir', type=Path, help='Output directory (default: <input-directory>_pdf2md beside input)')
    p.add_argument('--offline', action='store_true', default=True, help='Offline execution (always enabled; flag optional)')
    p.add_argument('--recursive', action='store_true')
    p.add_argument('--resume', action='store_true')
    p = sub.add_parser('convert', help='Convert a PDF; preflight and integrity checks run automatically')
    runtime_options(p)
    p.add_argument('--input', type=Path, required=True)
    p.add_argument('--output-dir', type=Path, help='Output directory (default: <input-stem>_pdf2md beside PDF)')
    p.add_argument('--offline', action='store_true', default=True, help='Offline execution (always enabled; flag optional)')
    p.add_argument('--resume', action='store_true')
    p.add_argument('--native-cache', type=Path, help='Explicit read-only own-worker cache; no new inference is claimed')
    p.add_argument('--chunk-size', type=int)
    p.add_argument('--max-retries', type=int)
    args = parser.parse_args()
    if (getattr(args, 'chunk_size', None) is not None and args.chunk_size <= 0) or (getattr(args, 'max_retries', None) is not None and args.max_retries < 0):
        parser.error('Chunk size must be positive; retries must be nonnegative')
    try:
        if args.command == 'init-config':
            if args.config.exists():
                raise ValueError('Config exists; refusing overwrite')
            data = initial(args.parser_python, args.models, args.gpu, args.device, args.coordinator_python)
            data['compute'] = args.compute
            if args.cpu_python:
                data['cpu_python'] = args.cpu_python
            if args.bwrap:
                data['bubblewrap_path'] = args.bwrap
            data = validate_config(data, args.config.resolve().parent)
            save(args.config, data)
            print(json.dumps({'config': str(args.config.resolve()), 'next': 'doctor --config ' + str(args.config)}))
            return 0
        if args.command == 'doctor':
            from .environment import inspect
            from .backends import NoBackendAvailable
            if args.report and args.report.exists():
                raise ValueError('Report exists; use a new report path')
            try:
                result = inspect(runtime(args))
                result['backend_availability'] = getattr(args, 'backend_availability', {})
            except NoBackendAvailable as exc:
                result = {'ready_for_conversion': False, 'backend_availability': exc.reports,
                          'selection_reason': 'NO_PREPARED_BACKEND', 'GPU_inference_performed': False}
            if args.report:
                save(args.report, result)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0 if result['ready_for_conversion'] else 1
        if args.command == 'preflight':
            from .source import preflight
            result = preflight(args.input.resolve())
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0 if result['supported'] else 2
        if args.command == 'verify':
            from .assets import check
            output = args.output_dir.resolve()
            verify(output)
            quality = read(output / 'quality_report.json')
            if quality['completed_pages'] != list(range(1, quality['expected_pages'] + 1)):
                raise ValueError('Incomplete physical page sequence')
            assets = check((output / 'manual.md').read_text(encoding='utf-8'), output)
            mapping = None
            if (output / 'source_map.json').is_file():
                from .source_map import validate
                mapping = validate(output)['states']
            elif read(output / 'run_manifest.json')['configuration'].get('version') in ('0.1.0a3', '0.1.0a4'):
                raise ValueError('New-version result is missing source_map.json')
            print(json.dumps({'integrity_passed': True, 'pages': quality['expected_pages'],
                'image_references': len(assets), 'source_mapping_states': mapping, 'semantic_fidelity_certified': False}))
            return 0
        if args.command == 'trace':
            from .source_map import trace
            result = trace(args.output_dir.resolve(), args.object_id, args.line,
                args.input.resolve() if args.input else None, args.render_dir.resolve() if args.render_dir else None)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
        if args.command == 'batch':
            from .batch import convert_directory
            if args.output_dir is None:
                args.output_dir = default_output(args.input_dir, batch=True)
            return convert_directory(args)
        return convert(args)
    except (OSError, ValueError, RuntimeError, KeyError, ImportError, subprocess.SubprocessError) as exc:
        print(str(exc), file=sys.stderr)
        return 1


def runtime_options(parser):
    parser.add_argument('--compute', choices=['auto', 'cpu', 'cuda'], help='Compute mode (default: auto)')
    parser.add_argument('--cpu-python', help='CPU backend Python (default: PDF2MD_CPU_PYTHON or coordinator Python)')
    parser.add_argument('--bwrap', help='Bubblewrap executable (default: PDF2MD_BWRAP or PATH)')
    parser.add_argument('--config', type=Path, help='Config JSON (default: discover runtime.local.json or user config)')
    parser.add_argument('--parser-python', help='Override MinerU Python (default: current interpreter)')
    parser.add_argument('--models', help='Override model root (default: ../models beside this distribution)')
    parser.add_argument('--gpu', help='Override visible GPU index or UUID (default: 0)')
    parser.add_argument('--device', action='append', help='Override device nodes; repeat per node (default: detect; explicit for MIG)')
