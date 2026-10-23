"""Small file and product-contract helpers; no benchmark imports."""

import hashlib
import json
from pathlib import Path
import re

PAGE = re.compile(r'<!-- PAGE (\d+) -->')
PRODUCT = Path(__file__).resolve().parents[2]


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def digest(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def save(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.writing')
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    temporary.replace(path)


def inventory(root):
    return {p.relative_to(root).as_posix(): sha(p) for p in sorted(Path(root).rglob('*'))
            if p.is_file() and not p.is_symlink() and '__pycache__' not in p.parts
            and p.name != '.conversion.lock'}


def page_parts(text):
    matches = list(PAGE.finditer(text))
    numbers = [int(m[1]) for m in matches]
    if numbers != sorted(set(numbers)):
        raise ValueError('Duplicate or unordered physical page declarations')
    return {int(m[1]): text[m.end():matches[i + 1].start() if i + 1 < len(matches) else len(text)]
            for i, m in enumerate(matches)}


def within(root, relative):
    if not relative or '\\' in relative or ':' in relative:
        raise ValueError('Unsafe local resource path')
    path = (Path(root) / relative).resolve()
    if not path.is_relative_to(Path(root).resolve()):
        raise ValueError('Resource escapes product')
    return path


def verify(root, name='run_manifest.json'):
    actual = inventory(root)
    actual.pop(name, None)
    if actual != read(Path(root) / name)['artifacts']:
        raise ValueError('Sealed product bytes changed; refusing reuse')


def implementation():
    return {p.relative_to(PRODUCT).as_posix(): sha(p)
            for folder in ('src', 'scripts', 'config', 'vendor')
            for p in sorted((PRODUCT / folder).rglob('*'))
            if p.is_file() and '__pycache__' not in p.parts}
