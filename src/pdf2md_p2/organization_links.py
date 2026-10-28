"""Reversible rendering of source text, stable anchors and local cross references."""
from collections import defaultdict
from copy import deepcopy
from html import escape
from html.parser import HTMLParser
from pathlib import Path
import re
from urllib.parse import unquote, urlsplit

from markdown_it import MarkdownIt
from markdown_it.rules_inline import html_inline as markdown_html
from markdown_it.rules_inline import link as markdown_link

from .assets import references, local_path
from .common import digest, within

MD = MarkdownIt('default', {'html': True, 'linkify': False})
_ATTR = re.compile(r'''\s+([^\s=/>]+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+)))?''')


def _visible_inline(value):
    pieces = []
    for token in MD.parseInline(value)[0].children:
        if token.type in ('text', 'code_inline'):
            pieces.append(token.content)
        elif token.type in ('softbreak', 'hardbreak'):
            pieces.append(' ')
        elif token.type == 'image':
            pieces.append(token.content)
    return ''.join(pieces)


def _slug(value):
    value = _visible_inline(value).strip().lower()
    value = re.sub(r'[^\w\-\s]', '', value)
    return re.sub(r'\s', '-', value)


class _HTMLReferences(HTMLParser):
    """Capture attributes, excluding examples in raw HTML code containers."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.records, self.anchors, self.raw_containers = [], [], []
        self.raw = ''

    def piece(self, raw):
        self.reset()
        self.raw, self.records, self.anchors = raw, [], []
        self.feed(raw)
        self.close()
        return self.records, self.anchors

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag in ('pre', 'code', 'script', 'style', 'textarea'):
            self.raw_containers.append(tag)
            return
        if self.raw_containers:
            return
        values = dict(attrs)
        if values.get('id'):
            self.anchors.append(values['id'])
        if tag == 'a' and values.get('name'):
            self.anchors.append(values['name'])
        if tag != 'a' or 'href' not in values:
            return
        if sum(name.lower() == 'href' for name, _ in attrs) != 1:
            raise ValueError('Duplicate HTML href attributes cannot be safely relocated')
        if values['href'] is None:
            raise ValueError('HTML reference is missing its href value')
        raw = self.get_starttag_text()
        line, column = self.getpos()
        base = sum(len(part) + 1 for part in self.raw.split('\n')[:line - 1]) + column
        name_end = re.match(r'<\s*[^\s/>]+', raw).end()
        for attribute in _ATTR.finditer(raw, name_end):
            if attribute[1].lower() != 'href':
                continue
            group = next((index for index in (2, 3, 4) if attribute[index] is not None), None)
            if group is None:
                raise ValueError('HTML href value cannot be safely relocated')
            start, end = attribute.span(group)
            self.records.append({'start': base + start, 'end': base + end,
                                 'href': values['href'], 'syntax': 'html_href'})
            return
        raise ValueError('HTML href source range could not be identified')

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_endtag(self, tag):
        if tag in self.raw_containers:
            index = len(self.raw_containers) - 1 - self.raw_containers[::-1].index(tag)
            del self.raw_containers[index:]


def _origins(text, content, start, end):
    direct = text.find(content, start, end)
    if direct >= 0:
        return list(range(direct, direct + len(content)))
    normalized, offsets, position = [], [], start
    while position < end:
        if text[position:position + 2] in ('\\|', '\r\n'):
            normalized.append(text[position + 1])
            offsets.append(position + 1)
            position += 2
        elif text[position] == '\r':
            normalized.append('\n')
            offsets.append(position)
            position += 1
        else:
            normalized.append(text[position])
            offsets.append(position)
            position += 1
    direct = ''.join(normalized).find(content)
    if direct >= 0:
        return offsets[direct:direct + len(content)]
    # Containers remove prefixes before inline parsing. Locate complete lines,
    # not the link string alone: an earlier code example may contain that string.
    origins, cursor = [], start
    for line in re.findall(r'[^\r\n]*(?:\r\n|\r|\n|$)', content):
        if not line:
            continue
        body = line.rstrip('\r\n')
        found = text.find(body, cursor, end)
        if found < 0:
            raise ValueError('Cannot safely relocate an inline token across container indentation')
        origins.extend(range(found, found + len(body)))
        cursor = found + len(body)
        for char in line[len(body):]:
            found = text.find(char, cursor, end)
            if found < 0:
                raise ValueError('Cannot safely relocate an inline newline')
            origins.append(found)
            cursor = found + 1
    return origins


def _line_offsets(text):
    """Markdown line maps count CR/LF, not Unicode paragraph separators."""
    offsets = [0, *(match.end() for match in re.finditer(r'\r\n|\r|\n', text))]
    if offsets[-1] != len(text):
        offsets.append(len(text))
    return offsets


def _scan(text, global_env):
    parsed = MD.parse(text, deepcopy(global_env))
    lines = _line_offsets(text)
    inline_md = None
    found, anchors, captures, cursors = [], [], [], {}
    active = [None]

    def capture_link(state, silent):
        start, count = state.pos, len(state.tokens)
        end_label = state.md.helpers.parseLinkLabel(state, state.pos, True) if state.src[state.pos:state.pos + 1] == '[' else -1
        ok = markdown_link(state, silent)
        if ok and not silent and state.src is active[0]:
            opening = next(t for t in state.tokens[count:] if t.type == 'link_open')
            captures.append({'start': start, 'end': state.pos, 'href': opening.attrGet('href'),
                             'label_start': start + 1, 'label_end': end_label,
                             'title': opening.attrGet('title'), 'syntax': 'markdown'})
        return ok

    def capture_html(state, silent):
        start = state.pos
        ok = markdown_html(state, silent)
        if ok and not silent and state.src is active[0]:
            captures.append({'start': start, 'end': state.pos, 'syntax': 'html_piece'})
        return ok

    for token in parsed:
        if token.map is None or token.type not in ('inline', 'html_block'):
            continue
        start, end = (lines[index] for index in token.map)
        if token.type == 'html_block':
            html = _HTMLReferences()
            records, names = html.piece(token.content)
            anchors.extend(names)
            if records:
                origins = _origins(text, token.content, start, end)
                for record in records:
                    a, b = origins[record['start']], origins[record['end'] - 1] + 1
                    found.append({**record, 'start': a, 'end': b, 'raw': text[a:b]})
            continue
        if not token.content:
            continue
        key = tuple(token.map)
        origins = _origins(text, token.content, cursors.get(key, start), end)
        cursors[key] = origins[-1] + 1
        # Keep cursor advancement even for literal table cells: a later active
        # link may have the same spelling. Only the second parser is unnecessary.
        if not any(child.type in ('link_open', 'html_inline') for child in (token.children or [])):
            continue
        if inline_md is None:
            inline_md = MarkdownIt('default', {'html': True, 'linkify': False})
            inline_md.inline.ruler.at('link', capture_link)
            inline_md.inline.ruler.at('html_inline', capture_html)
        captures.clear()
        active[0] = token.content
        inline_md.inline.parse(token.content, inline_md, deepcopy(global_env), [])
        html = _HTMLReferences()
        for record in sorted(captures, key=lambda item: item['start']):
            a, b = origins[record['start']], origins[record['end'] - 1] + 1
            if record['syntax'] == 'html_piece':
                raw = text[a:b]
                records, names = html.piece(raw)
                anchors.extend(names)
                for href in records:
                    found.append({**href, 'start': a + href['start'], 'end': a + href['end'],
                                  'raw': raw[href['start']:href['end']]})
            else:
                label_start = origins[record['label_start']] if record['label_start'] < record['label_end'] else a + 1
                label_end = origins[record['label_end'] - 1] + 1 if record['label_start'] < record['label_end'] else label_start
                found.append({**record, 'start': a, 'end': b, 'raw': text[a:b],
                              'label': text[label_start:label_end]})
    found.sort(key=lambda item: item['start'])
    for left, right in zip(found, found[1:]):
        if right['start'] < left['end']:
            raise ValueError('Overlapping nested link syntax cannot be safely relocated')
    return found, anchors


def _links(text, global_env):
    return _scan(text, global_env)[0]


def restore(text, transforms):
    for item in reversed(transforms):
        a, b = item['output_start'], item['output_end']
        if text[a:b] != item['after']:
            raise ValueError('Reversible link transformation differs')
        text = text[:a] + item['before'] + text[b:]
    return text


def _refresh_heading(unit):
    heading = unit.get('heading')
    if not heading:
        return
    tokens = MD.parse(unit['text'])
    for index, token in enumerate(tokens):
        if token.type == 'heading_open':
            heading.update(title=tokens[index + 1].content, level=int(token.tag[1:]))
            return
    if heading.get('type') == 'INFERRED_STRUCTURE':
        stripped = unit['text'].strip()
        if '\n' not in stripped:
            heading['title'] = _visible_inline(stripped)


def refresh_scopes(units, scopes, layout=None):
    by_id = {scope['scope_id']: scope for scope in scopes}
    if len(by_id) != len(scopes):
        raise ValueError('Duplicate Scope identity')
    for scope in scopes:
        parent = scope.get('parent_id')
        if parent is not None and parent not in by_id:
            raise ValueError('Unknown Scope parent')
        current, visited = scope, set()
        while current:
            if current['scope_id'] in visited:
                raise ValueError('Cyclic Scope ancestry')
            visited.add(current['scope_id'])
            current = by_id.get(current.get('parent_id'))
        scope.update(direct_unit_ids=[], unit_ids=[], pages=[], content_files=[])
    units_by_id = {unit['unit_id']: unit for unit in units}
    for unit in units:
        _refresh_heading(unit)
        if unit['primary_scope_id'] not in by_id:
            raise ValueError('Unknown primary Scope')
        scope = by_id[unit['primary_scope_id']]
        unit['content_file'] = layout[unit['unit_id']] if layout is not None else 'content/' + scope['scope_id'] + '.md'
        scope['direct_unit_ids'].append(unit['unit_id'])
        while scope:
            scope['unit_ids'].append(unit['unit_id'])
            scope['pages'] = sorted(set(scope['pages']) | set(unit.get('pages', [])))
            scope['content_files'] = sorted(set(scope['content_files']) | {unit['content_file']})
            scope = by_id.get(scope.get('parent_id'))
    for scope in scopes:
        heading_id = scope.get('heading_unit_id')
        if heading_id is not None:
            if heading_id not in units_by_id:
                raise ValueError('Scope heading Unit is missing')
            heading = units_by_id[heading_id].get('heading')
            if heading:
                scope['title'] = heading['title']
                if scope.get('level') is not None and scope['level'] != heading['level']:
                    warning = {'type': 'HEADING_LEVEL_CHANGED_SCOPE_NOT_REINFERRED', 'heading_level': heading['level']}
                    if warning not in scope.setdefault('conflicts', []):
                        scope['conflicts'].append(warning)


def _local_kind(href):
    url = urlsplit(href)
    if url.scheme or url.netloc:
        if url.scheme.lower() in ('http', 'https', 'mailto', 'ftp', 'ftps') or (url.netloc and href.startswith('//')):
            return 'external', url
        raise ValueError('Unsupported or unsafe reference scheme: ' + href)
    path = unquote(url.path)
    if '\\' in path or ':' in path or path.startswith('/') or '..' in path.split('/'):
        raise ValueError('Unsupported or unsafe local document link: ' + href)
    if path.startswith('assets/') and not url.query and not url.fragment:
        return 'asset', url
    if path not in ('', 'manual.md', './manual.md') or not url.fragment or url.query:
        raise ValueError('Internal reference must identify a supported document anchor: ' + href)
    return 'anchor', url


def _render(units, scopes, prior_references, single, layout=None, display=None):
    refresh_scopes(units, scopes, layout)
    if single:
        for unit in units:
            unit['content_file'] = 'manual.md'
    env = {}
    MD.parse(''.join(unit['text'] for unit in units), env)
    anchors, scans = defaultdict(list), {}
    for unit in units:
        anchors[unit['unit_id']].append(unit)
        if unit.get('heading'):
            anchors[_slug(unit['heading']['title'])].append(unit)
        scans[unit['unit_id']], names = _scan(unit['text'], env)
        for name in names:
            anchors[name].append(unit)
    by_id = {unit['unit_id']: unit for unit in units}
    previous = {ref['reference_id']: ref for ref in prior_references
                if ref.get('type') == 'explicit_reference' and ref.get('reference_id')}
    definitions = []
    for label, record in env.get('references', {}).items():
        title = (' "' + record['title'].replace('"', '&quot;') + '"') if record.get('title') else ''
        definitions.append(f'[{label}]: <{record["href"]}>{title}')
    prefix = ('<!-- Generated reference definitions -->\n' + '\n'.join(definitions) + '\n\n') if definitions else ''
    files, relations = {}, []
    for unit in units:
        filename, raw = unit['content_file'], unit['text']
        files.setdefault(filename, prefix)
        changes, delta = [], 0
        for index, record in enumerate(scans[unit['unit_id']]):
            kind, url = _local_kind(record['href'])
            if kind == 'external':
                continue
            ref_id = unit['unit_id'] + '-reference-' + str(index + 1)
            relation = {'type': 'explicit_reference', 'source': unit['unit_id'],
                        'original_target': record['href'], 'reference_id': ref_id,
                        'source_content_hash': digest(raw), 'syntax': record['syntax']}
            if kind == 'asset':
                relation.update(target=None, candidates=[], status='LOCAL_ASSET', asset_url=record['href'], file=filename)
                relations.append(relation)
                continue
            candidates = list({target['unit_id']: target for target in anchors.get(unquote(url.fragment), [])}.values())
            prior = previous.get(ref_id, {})
            retained = (prior.get('status') == 'RESOLVED' and prior.get('source_content_hash') == digest(raw)
                        and prior.get('original_target') == record['href'] and prior.get('target') in by_id)
            if retained:
                candidates = [by_id[prior['target']]]
            relation['candidates'] = [target['unit_id'] for target in candidates]
            if len(candidates) == 1:
                target = candidates[0]
                href = Path(target['content_file']).name + '#' + target['unit_id']
                relation.update(target=target['unit_id'], status='RESOLVED', target_revision=target['revision'],
                                target_file=target['content_file'],
                                basis='RETAINED_STABLE_REFERENCE_IDENTITY' if retained else 'UNIQUE_DOCUMENT_ANCHOR')
            else:
                href = ('README.md#' if single else '../README.md#') + ref_id
                relation.update(target=None, status='AMBIGUOUS' if candidates else 'UNRESOLVED', navigation_anchor=ref_id)
            if record['syntax'] == 'html_href':
                replacement = escape(href, quote=True)
            else:
                title = (' "' + record['title'].replace('"', '&quot;') + '"') if record.get('title') else ''
                replacement = '[' + record['label'] + '](' + href + title + ')'
            a = record['start'] + delta
            changes.append({'output_start': a, 'output_end': a + len(replacement),
                            'before': record['raw'], 'after': replacement, 'basis': 'STABLE_ANCHOR_RELOCATION'})
            delta += len(replacement) - len(record['raw'])
            relations.append(relation)
        rendered = raw
        for item in changes:
            a = item['output_start']
            rendered = rendered[:a] + item['after'] + rendered[a + len(item['before']):]
        if display and unit['unit_id'] in display:
            view = display[unit['unit_id']]
            if view['content_hash'] != digest(raw):
                raise ValueError('Reading transformation targets stale content')
            changes.append({'output_start': 0, 'output_end': len(view['after']),
                            'before': rendered, 'after': view['after'], 'basis': view['basis']})
            rendered = view['after']
        # A blank line closes the HTML anchor block before ordinary Markdown.
        files[filename] += f'<a id="{unit["unit_id"]}"></a>\n\n'
        start = len(files[filename])
        files[filename] += rendered + '\n\n'
        unit['transforms'] = changes
        unit['rendered_hash'] = digest(rendered)
        unit['content_ref'] = {'file': filename, 'anchor': unit['unit_id'], 'start': start,
            'end': start + len(rendered), 'line_start': files[filename].count('\n', 0, start) + 1,
            'line_end': files[filename].count('\n', 0, max(start, start + len(rendered) - 1)) + 1}
        if restore(rendered, changes) != raw:
            raise ValueError('Internal reference transformation is not reversible')
    return files, relations, {'unit_anchors': True, 'separators': '\n\n',
        'reference_definitions': prefix, 'asset_paths': 'UNCHANGED_RELATIVE_TO_CONTENT_DIRECTORY' if not single else 'UNCHANGED_RELATIVE_TO_EXPORT_DIRECTORY'}


def render_files(units, scopes, prior_references=(), *, layout=None, display=None):
    return _render(units, scopes, prior_references, single=False, layout=layout, display=display)


def render_single(units, scopes, prior_references=()):
    files, relations, generated = _render(deepcopy(units), deepcopy(scopes), prior_references, single=True)
    return files.get('manual.md', ''), relations, generated


def linked_assets(text):
    """Local download-link URLs; image references are handled by assets.check."""
    env = {}
    MD.parse(text, env)
    return list(dict.fromkeys(record['href'] for record in _links(text, env)
                              if _local_kind(record['href'])[0] == 'asset'))


def validate_links(root, files, relations):
    root = Path(root)
    for name, text in files.items():
        boundary = root / 'content' if name.startswith('content/') else root
        for url in references(text):
            path = local_path(within(root, name).parent, url, boundary)
            if not path.is_file():
                raise ValueError('Missing organized image: ' + url)
    for relation in relations:
        if relation['type'] != 'explicit_reference':
            continue
        if relation.get('status') == 'LOCAL_ASSET':
            filename = relation['file']
            boundary = root / 'content' if filename.startswith('content/') else root
            path = local_path(within(root, filename).parent, relation['asset_url'], boundary)
            if not path.is_file():
                raise ValueError('Missing organized linked asset: ' + relation['asset_url'])
        elif relation.get('status') == 'RESOLVED':
            target = relation['target']
            target_files = [files.get(relation['target_file'], '')] if relation.get('target_file') else files.values()
            if not any(f'<a id="{target}"></a>' in text for text in target_files):
                raise ValueError('Broken stable cross-file reference')
        elif relation.get('navigation_anchor'):
            navigation = root / 'README.md'
            if (not navigation.is_file() or
                    f'<a id="{relation["navigation_anchor"]}"></a>' not in navigation.read_text(encoding='utf-8')):
                raise ValueError('Unresolved reference is missing its explicit navigation destination')
