"""Offline CPU-only layout conversion; independent of the frozen GPU worker."""
import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import resource
import socket
import time


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--pages', required=True)
    args = parser.parse_args()
    output = args.output
    for name in ('assets', 'markdown', 'native_output'):
        (output / name).mkdir(exist_ok=True)
    import pymupdf
    import pymupdf4llm
    from markdown_it import MarkdownIt
    if not pymupdf4llm._use_layout:
        raise ValueError('Pinned CPU Layout backend required; legacy fallback refused')
    if os.environ.get('CUDA_VISIBLE_DEVICES') != '' or any(Path(p).exists() for p in ('/dev/dxg', '/dev/nvidia0')):
        raise ValueError('CPU worker must not have a visible GPU device')
    if any(name != 'lo' for _, name in socket.if_nameindex()):
        raise ValueError('CPU worker network is not isolated')
    events = []
    def refuse_ocr(*a, **kw):
        events.append({'kind': 'UNEXPECTED_OCR_REQUEST_REJECTED'})
        raise ValueError('CPU native-text profile does not permit OCR')
    pymupdf.Page.get_textpage_ocr = refuse_ocr
    pages = [int(p) for p in args.pages.split(',')]
    started = time.monotonic()
    try:
        chunks = pymupdf4llm.to_markdown(str(args.input), pages=[p - 1 for p in pages],
            page_chunks=True, write_images=True, image_path='assets', image_format='png',
            use_ocr=False, force_ocr=False, table_output='html', show_progress=False)
    finally:
        (output / 'native_output/text_recognition_policy.json').write_text(json.dumps({
            'policy': 'cpu-native-no-ocr-0.1', 'events': events,
            'neural_text_recognition_calls_executed': 0, 'ocr_enabled': False}), encoding='utf-8')
    native, texts = [], []
    for number, chunk in zip(pages, chunks, strict=True):
        text = chunk['text']
        def urls(tokens):
            for token in tokens:
                if token.type == 'image':
                    yield token.attrGet('src')
                if token.children:
                    yield from urls(token.children)
        for url in dict.fromkeys(urls(MarkdownIt().parse(text))):
            path = (output / url).resolve()
            if not path.is_relative_to((output / 'assets').resolve()) or not path.is_file():
                raise ValueError('CPU image outside its declared asset directory')
            text = text.replace(url, '../' + path.relative_to(output).as_posix())
        native.append({'page': number, **chunk, 'emitted_text': text})
        texts.append(f'<!-- PAGE {number} -->\n\n{text}')
    (output / 'markdown/document.md').write_text('\n\n'.join(texts), encoding='utf-8')
    (output / 'native_output/native.json').write_text(json.dumps({'pages': native}, ensure_ascii=False, default=str), encoding='utf-8')
    (output / 'native_output/p2_runtime_config.json').write_text(json.dumps({
        'effective_configuration': {'parse_mode': 'txt', 'use_ocr': False, 'device': 'cpu'}}), encoding='utf-8')
    sha = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
    manifest = {'status': 'success', 'pages': pages, 'compute': 'cpu', 'engine': 'PyMuPDF4LLM',
        'parser_version': importlib.metadata.version('pymupdf4llm'), 'GPU_inference_performed': False,
        'cpu_execution_verified': True, 'network_interfaces': socket.if_nameindex(),
        'pid_namespace': os.readlink('/proc/self/ns/pid'),
        'wall_seconds': time.monotonic() - started, 'peak_rss_kib': resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        'markdown_sha256': sha(output / 'markdown/document.md'), 'native_sha256': sha(output / 'native_output/native.json')}
    (output / 'native_output/worker_manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    print(json.dumps(manifest), flush=True)


if __name__ == '__main__':
    main()
