"""Explicit P2 native-only configuration of the unchanged frozen B3 worker."""

import json
import os
from pathlib import Path
import runpy
from native_policy import install


def main():
    worker = runpy.run_path('/worker.py', run_name='p2_frozen_worker')
    globals_ = worker['main'].__globals__
    args = globals_['_arguments']().parse_args()
    if args.parser != 'b3' or os.environ.get('P2_NATIVE_MODE') != 'txt':
        raise ValueError('This P2 profile supports B3 native txt only')
    original = globals_['_run_b3']

    def native_run(pdf, output, pages, models):
        # Match the original worker's pre-import model environment, with no weight mutation.
        os.environ.update({'MINERU_MODEL_BASE_DIR': str(output / 'native_output/mineru_runtime_models'),
                           'MINERU_MODEL_SOURCE': 'local', 'MINERU_MODEL_SMALL_BACKEND': 'torch',
                           'HF_HUB_OFFLINE': '1', 'TRANSFORMERS_OFFLINE': '1'})
        import mineru.parser
        factory = mineru.parser.MinerUParser
        set_page, flush = install(pdf, output)

        def configured_parser(*positional, **keywords):
            if positional or keywords != {'tier': 'basic', 'parse_mode': 'auto', 'image_analysis': True}:
                raise ValueError('Frozen worker factory configuration differs')
            effective = {**keywords, 'parse_mode': 'txt'}
            evidence = {'version': 'p2-native-no-text-recognition-profile-0.2.0', 'requested_by_frozen_worker': keywords,
                        'effective_configuration': effective, 'configuration_change_is_explicit': True,
                        'legacy_worker_file_unchanged': True, 'scanned_text_OCR_enabled': False}
            path = output / 'native_output/p2_runtime_config.json'
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(evidence, indent=2) + '\n', encoding='utf-8')
            instance = factory(**effective)
            original_parse = instance.parse

            def parse(path, *, page_range='', **options):
                if not str(page_range).isdecimal():
                    raise ValueError('Frozen worker must request one physical page at a time')
                set_page(page_range)
                try:
                    return original_parse(path, page_range=page_range, **options)
                finally:
                    flush()
            instance.parse = parse
            return instance

        # In-process public constructor adaptation only; installed package files stay read-only.
        mineru.parser.MinerUParser = configured_parser
        try:
            return original(pdf, output, pages, models)
        finally:
            mineru.parser.MinerUParser = factory

    globals_['_run_b3'] = native_run
    return worker['main']()


if __name__ == '__main__':
    raise SystemExit(main())
