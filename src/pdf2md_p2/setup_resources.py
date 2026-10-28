"""Pinned resources for user-level setup. No document is accepted by this module."""
from email.parser import Parser
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request

from .common import PRODUCT, read, sha


def models_manifest():
    source = read(PRODUCT / 'config/model_download.json')
    bindings = read(PRODUCT / 'config/baseline_bindings.json')['models']
    prefix = 'MinerU-4_models_torch/'
    expected = {key[len(prefix):]: value for key, value in bindings.items() if key != prefix + '.mineru_complete'}
    if set(expected) != set(source['sizes']) or not re.fullmatch(r'[0-9a-f]{40}', source['revision']):
        raise ValueError('Download manifest does not match the frozen model binding')
    for key in expected:
        if Path(key).is_absolute() or '..' in Path(key).parts or '\\' in key or source['sizes'][key] <= 0:
            raise ValueError('Unsafe model resource path')
    return source, expected


def download(url, target, expected, size, progress=lambda message: None):
    """Resume a pinned file; only a verified payload is moved into its final location."""
    if urllib.parse.urlparse(url).scheme != 'https':
        raise ValueError('Resource downloads require HTTPS')
    target = Path(target)
    if target.is_file() and sha(target) == expected:
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    part = target.with_name(target.name + '.part')
    for attempt in range(3):
        offset = part.stat().st_size if part.is_file() else 0
        if offset == size and sha(part) == expected:
            part.replace(target)
            return
        if offset >= size:
            part.replace(part.with_name(part.name + '.rejected-' + str(time.time_ns())))
            offset = 0
        request = urllib.request.Request(url, headers={'Range': 'bytes=' + str(offset) + '-'} if offset else {})
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                if urllib.parse.urlparse(response.geturl()).scheme != 'https':
                    raise ValueError('Resource redirect downgraded HTTPS')
                partial = response.status == 206
                if partial and not response.headers.get('Content-Range', '').startswith('bytes ' + str(offset) + '-'):
                    raise ValueError('Unexpected download range')
                count = offset if partial else 0
                last = count
                with part.open('ab' if partial else 'wb') as stream:
                    while block := response.read(1024 * 1024):
                        count += len(block)
                        if count > size:
                            raise ValueError('Resource exceeded its pinned size')
                        stream.write(block)
                        if count - last >= 16 * 1024 * 1024:
                            progress(f'{target.name}: {count}/{size} bytes')
                            last = count
            if part.stat().st_size != size:
                raise OSError('Incomplete resource; retaining partial download')
            if sha(part) != expected:
                part.replace(part.with_name(part.name + '.rejected-' + str(time.time_ns())))
                raise ValueError('Resource SHA256 differs: ' + target.name)
            part.replace(target)
            return
        except (OSError, urllib.error.URLError) as error:
            if attempt == 2:
                raise
            progress('Download interrupted; retrying pinned resource: ' + target.name)
            time.sleep(attempt + 1)


def prepare_models(destination, source=None, offline=False, progress=lambda message: None):
    manifest, bindings = models_manifest()
    root = Path(destination) / 'MinerU-4_models_torch'
    if source is not None:
        source = Path(source).expanduser().absolute()
        if (source / 'MinerU-4_models_torch').is_dir():
            source /= 'MinerU-4_models_torch'
        source = source.resolve()
    for relative, expected in bindings.items():
        target = root / relative
        if target.is_file() and sha(target) == expected:
            continue
        if source is not None:
            original = (source / relative).resolve()
            if not original.is_relative_to(source) or not original.is_file() or sha(original) != expected:
                raise ValueError('Offline model binding failed: ' + relative)
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_name(target.name + '.part')
            shutil.copyfile(original, temporary)
            if sha(temporary) != expected:
                raise ValueError('Model changed during import: ' + relative)
            temporary.replace(target)
        elif offline:
            raise ValueError('Offline model is missing; provide --models: ' + relative)
        else:
            url = ('https://huggingface.co/' + manifest['repository'] + '/resolve/' + manifest['revision'] + '/'
                   + urllib.parse.quote(relative, safe='/'))
            progress('Fetching pinned model: ' + relative)
            download(url, target, expected, manifest['sizes'][relative], progress)
    marker = root / '.mineru_complete'
    expected = read(PRODUCT / 'config/baseline_bindings.json')['models']['MinerU-4_models_torch/.mineru_complete']
    if hashlib.sha256(b'\n').hexdigest() != expected:
        raise ValueError('Unexpected completion-marker binding')
    marker.write_bytes(b'\n')
    return str(root.parent)


def bwrap_package():
    """Use APT's configured, authenticated official distribution package metadata."""
    if not shutil.which('apt-cache') or not shutil.which('dpkg-deb'):
        return None
    policy = subprocess.run(['apt-cache', 'policy', 'bubblewrap'], capture_output=True, text=True)
    candidate = re.search(r'^\s*Candidate:\s*(\S+)', policy.stdout, re.M)
    hosts = [urllib.parse.urlparse(url).hostname or '' for url in re.findall(r'https?://\S+', policy.stdout)]
    if not candidate or candidate[1] == '(none)' or not hosts or any(
            not (host == 'debian.org' or host.endswith('.debian.org') or host == 'ubuntu.com' or host.endswith('.ubuntu.com'))
            for host in hosts):
        return None
    result = subprocess.run(['apt-cache', 'show', 'bubblewrap=' + candidate[1]], capture_output=True, text=True)
    for paragraph in re.split(r'\n\s*\n', result.stdout):
        fields = Parser().parsestr(paragraph)
        if fields.get('Version') == candidate[1] and re.fullmatch(r'[0-9a-f]{64}', fields.get('SHA256', '')):
            return {'version': candidate[1], 'sha256': fields['SHA256'], 'size': int(fields['Size']),
                    'filename': Path(fields['Filename']).name, 'architecture': fields['Architecture']}
    return None


def prepare_bwrap(data, spec, wheelhouses, offline, run):
    destination = Path(data) / 'tools' / ('bwrap-' + spec['version'] + '-' + spec['sha256'][:12])
    executable = destination / 'usr/bin/bwrap'
    archive_dir = destination / 'download'
    archive_dir.mkdir(parents=True, exist_ok=True)
    archive = archive_dir / spec['filename']
    if not archive.is_file() or sha(archive) != spec['sha256']:
        supplied = next((Path(path) / spec['filename'] for path in wheelhouses
                         if (Path(path) / spec['filename']).is_file()), None)
        if supplied:
            if sha(supplied) != spec['sha256']:
                raise ValueError('Offline bubblewrap package SHA256 differs')
            shutil.copyfile(supplied, archive)
        elif offline:
            raise ValueError('Offline bubblewrap package is missing; provide --bwrap or its APT package')
        else:
            run(['apt-get', '-o', 'APT::Get::AllowUnauthenticated=false', '-o',
                 'Acquire::AllowInsecureRepositories=false', 'download', 'bubblewrap=' + spec['version']], cwd=archive_dir)
        if not archive.is_file() or sha(archive) != spec['sha256']:
            raise ValueError('Downloaded bubblewrap package SHA256 differs')
    # Re-extract the authenticated package so a changed local executable is not reused.
    run(['dpkg-deb', '--extract', str(archive), str(destination)])
    if not executable.is_file() or not os.access(executable, os.X_OK):
        raise ValueError('Distribution package did not provide executable bubblewrap')
    return str(executable)
