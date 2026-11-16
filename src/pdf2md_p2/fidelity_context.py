"""Explicit local annotations only; no nearest-paragraph condition guessing."""

import re

CONDITION = r'(?P<keyword>when|if|for)\s+(?P<parameter>[A-Za-z][A-Za-z0-9_]*)\s*=\s*(?P<value>[+\-\u2212]?\d+(?:\.\d+)?)'


def annotations(text):
    start, end = 0, len(text)
    result = {'equation_number': None, 'condition': None}
    number = re.search(r'(?:\\tag\{(?P<tag>\d+)\}|\s+\((?P<plain>\d+)\))\s*$', text)
    if number:
        result['equation_number'] = number['tag'] or number['plain']
        end = number.start()
    prefix = re.match(r'\s*'+CONDITION+r'\s*:\s*', text[:end], re.I)
    suffix = re.search(r';\s*'+CONDITION+r'\s*$', text[:end], re.I)
    condition = prefix or suffix
    if condition:
        result['condition'] = {'parameter': condition['parameter'],
                               'value': condition['value'].replace('\u2212', '-')}
        if prefix:
            start = prefix.end()
        else:
            end = suffix.start()
    while start < end and text[start].isspace():
        start += 1
    while end > start and text[end-1].isspace():
        end -= 1
    return text[start:end], (start, end), result


def compare(source, output):
    checked, unchecked, differences = [], [], []
    for attribute in ('equation_number', 'condition'):
        expected, actual = source.get(attribute), output.get(attribute)
        if expected is not None and actual is not None:
            checked.append(attribute)
            if expected != actual:
                differences.append(attribute.upper()+'_DIFFERS')
        else:
            unchecked.append(attribute)
    return {'checked': checked, 'unchecked': unchecked, 'differences': differences,
            'expected': source, 'actual': output}
