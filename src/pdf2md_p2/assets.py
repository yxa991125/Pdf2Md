"""Parse actual Markdown/HTML image references and enforce local readable assets."""

from html.parser import HTMLParser
from pathlib import Path
import shutil
from urllib.parse import unquote, urlsplit

from markdown_it import MarkdownIt
from PIL import Image

from .common import sha, within


class Images(HTMLParser):
    def __init__(self):
        super().__init__()
        self.urls = []

    def handle_starttag(self, tag, attrs):
        if tag.lower() == 'img':
            values = dict(attrs)
            if 'src' not in values:
                raise ValueError('Image without src')
            self.urls.append(values['src'])


def references(text):
    urls = []

    def visit(tokens):
        for token in tokens:
            if token.type == 'image':
                urls.append(token.attrGet('src'))
            if token.type in ('html_block', 'html_inline'):
                parser = Images()
                parser.feed(token.content)
                urls.extend(parser.urls)
            if token.children:
                visit(token.children)
    visit(MarkdownIt('commonmark', {'html': True}).parse(text))
    return urls


def local_path(root, url, boundary=None):
    parsed = urlsplit(url)
    if parsed.scheme or parsed.netloc or parsed.query or parsed.fragment or url.startswith('/'):
        raise ValueError('Only plain local image paths are supported')
    relative = unquote(parsed.path)
    if '\\' in relative or ':' in relative:
        raise ValueError('Unsafe local resource path')
    target = (root / relative).resolve()
    if not target.is_relative_to((boundary or root).resolve()):
        raise ValueError('Image path escapes its allowed root')
    return target


def materialize(text, chunk, output):
    for url in dict.fromkeys(references(text)):
        source = local_path(chunk / 'markdown', url, chunk)
        if not source.is_relative_to((chunk / 'assets').resolve()):
            raise ValueError('Worker asset outside its declared asset directory')
        relative = source.relative_to(chunk / 'assets')
        target = within(output, 'assets/' + relative.as_posix())
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists() and sha(source) != sha(target):
            raise ValueError('Conflicting worker assets')
        if not target.exists():
            shutil.copyfile(source, target)
        text = text.replace(url, target.relative_to(output).as_posix())
    return text


def check(text, output):
    result = []
    for url in references(text):
        path = local_path(output, url)
        if not path.is_relative_to((output / 'assets').resolve()):
            raise ValueError('Final image is not under assets/')
        with Image.open(path) as image:
            width, height = image.size
            image.verify()
        result.append({'path': url, 'sha256': sha(path), 'width': width, 'height': height})
    return result
