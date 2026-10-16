#!/usr/bin/env python3
"""Fetch pinned public ASR dependencies; keep generated binaries out of the source tree."""
import hashlib
import json
import shutil
import sys
import tarfile
import urllib.request
from pathlib import Path


def digest(path):
    value = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1 << 20), b''):
            value.update(block)
    return value.hexdigest()


def prepare(output):
    manifest = json.loads(Path(__file__).with_name('phone-speech.json').read_text())
    cache = output / 'downloads'
    cache.mkdir(parents=True, exist_ok=True)
    for entry in manifest['downloads']:
        archive = cache / entry['name']
        if not archive.is_file() or digest(archive) != entry['sha256']:
            temp = archive.with_suffix('.part')
            try:
                with urllib.request.urlopen(entry['url'], timeout=60) as source, temp.open('wb') as target:
                    shutil.copyfileobj(source, target, 1 << 20)
                if digest(temp) != entry['sha256']:
                    raise ValueError('SHA-256 mismatch: ' + entry['name'])
                temp.replace(archive)
            finally:
                temp.unlink(missing_ok=True)
        for member, relative in entry['outputs'].items():
            target = output / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            if member:
                with tarfile.open(archive) as packed:
                    source = packed.extractfile(member)
                    if source is None:
                        raise ValueError('Missing model member: ' + member)
                    with source, target.open('wb') as sink:
                        shutil.copyfileobj(source, sink)
            else:
                shutil.copyfile(archive, target)
            if digest(target) != manifest['files'][relative]:
                raise ValueError('Extracted file SHA-256 mismatch: ' + relative)
    print('Pinned phone speech library and Chinese model: verified')


if __name__ == '__main__':
    prepare(Path(sys.argv[1]))
