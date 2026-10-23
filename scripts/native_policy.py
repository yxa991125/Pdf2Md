"""Source-only replacements for legacy neural text-recognition fallbacks.

Math recognition and layout detection remain the selected B3 engine. No installed
package or frozen worker is edited. The profile is explicitly versioned and paired.
"""

import json
import hashlib
from pathlib import Path
import re


def install(input_pdf, output):
    import pypdfium2 as pdfium
    import mineru.backend.analysis.pdf.text.content as content
    import mineru.backend.analysis.pdf.ocr as ocr
    import mineru.backend.analysis.pdf.formulas as formulas
    import mineru.model.runtime.hybrid as hybrid

    ledger = {'policy': 'native-no-text-recognition-0.2.0', 'events': [],
              'neural_text_recognition_calls_executed': 0,
              'existing_layout_detection_and_math_recognition_retained': True}
    current = {'page': None}

    def record(kind, **fields):
        ledger['events'].append({'page': current['page'], 'kind': kind, **fields})

    def text_in_bbox(bbox):
        if current['page'] is None or not all(0 <= v <= 1 for v in bbox):
            return ''
        doc = pdfium.PdfDocument(str(input_pdf))
        page = doc[current['page'] - 1]
        text_page = page.get_textpage()
        try:
            width, height = page.get_size()
            x0, y0, x1, y1 = bbox
            return text_page.get_text_bounded(x0 * width, (1 - y1) * height,
                                              x1 * width, (1 - y0) * height).strip()
        finally:
            text_page.close()
            page.close()
            doc.close()

    def native_formula_numbers(local_context, model_list, np_images):
        for blocks in model_list:
            for item in blocks:
                if item.get('type') != formulas.RAW_FORMULA_NUMBER:
                    continue
                bbox = list(item['bbox'])
                value = text_in_bbox(bbox)
                if re.fullmatch(r'\(?\d+[a-z]?\)?', value):
                    item['content'] = value
                    record('FORMULA_NUMBER_FROM_NATIVE_TEXT', source_bbox_normalized=bbox, text=value)
                else:
                    record('FORMULA_NUMBER_UNRESOLVED_NO_OCR', source_bbox_normalized=bbox,
                           native_text=value)

    def native_text_fallback(local_context, page_block_lines_list):
        for blocks in page_block_lines_list:
            for index, lines in blocks.items():
                for line in lines:
                    for span in line.spans:
                        if span.image is None:
                            continue
                        before = str(span.content or '')
                        restored = content._restore_post_ocr_fallback(span)
                        span.image = None
                        fields = {'block_index': index, 'source_bbox_points': list(span.bbox),
                                  'before': before, 'native_fallback': str(span.content or '')}
                        if restored:
                            record('NATIVE_GLYPH_FALLBACK_RETAINED', **fields)
                        elif span.type == 'text':
                            key = str(current['page']) + ':' + str(span.bbox) + ':' + str(len(ledger['events']))
                            marker = 'P2SOURCEGLYPH' + hashlib.sha256(key.encode()).hexdigest()[:24].upper()
                            span.content = marker
                            record('SOURCE_GLYPH_VISUAL_REQUIRED_NO_OCR', marker=marker, **fields)
                        else:
                            record('NATIVE_GLYPH_UNRESOLVED_NO_OCR', **fields)

    def no_scan_recognition(local_context, results):
        if any('np_img' in row for page in results for row in page):
            record('TEXT_RECOGNITION_REQUEST_REJECTED')
            raise RuntimeError('Native-only profile refused a text OCR request')

    def no_seal_recognition(local_context, model_list, np_images):
        for blocks in model_list:
            for item in blocks:
                if item.get('sub_type') == 'seal':
                    record('SEAL_TEXT_UNSUPPORTED_VISUAL_ONLY', source_bbox_normalized=list(item['bbox']))

    def refuse(*args, **kwargs):
        record('UNEXPECTED_TEXT_RECOGNITION_REQUEST_REJECTED')
        raise RuntimeError('Neural text OCR is outside this product profile')

    original_get = hybrid.HybridLocalModelContextSingleton.get_model

    def guarded_get(*args, **kwargs):
        model = original_get(*args, **kwargs)
        model.ocr_model.ocr = refuse
        return model

    content._apply_window_post_ocr = native_text_fallback
    ocr._apply_ocr_rec_results = no_scan_recognition
    ocr._apply_seal_ocr = no_seal_recognition
    formulas._apply_medium_formula_number_ocr = native_formula_numbers
    hybrid.HybridLocalModelContextSingleton.get_model = guarded_get

    def set_page(page):
        current['page'] = int(page)

    def flush():
        path = Path(output) / 'native_output/text_recognition_policy.json'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(ledger, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')

    return set_page, flush
