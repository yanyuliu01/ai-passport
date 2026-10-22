#!/usr/bin/env python3
"""Assemble official Windows binaries without running a target-OS interpreter."""
import hashlib
import json
import re
import shutil
import urllib.request
import zipfile
from pathlib import Path

DESKTOP = Path(__file__).resolve().parents[1]
PACKAGE = DESKTOP.parents[1] / 'runtime/xiaoyou_runtime'

def main():
    lock = json.loads((DESKTOP / 'tools/windows-runtime-lock.json').read_text())
    cache = DESKTOP / '.build-cache/windows-runtime'
    cache.mkdir(parents=True, exist_ok=True)
    downloads = {}
    for name, item in lock.items():
        target = cache / item['url'].rsplit('/', 1)[-1]
        if not target.exists():
            with urllib.request.urlopen(item['url'], timeout=60) as source, target.open('wb') as out:
                shutil.copyfileobj(source, out)
        if hashlib.sha256(target.read_bytes()).hexdigest() != item['sha256']:
            raise RuntimeError('Official dependency checksum mismatch: ' + name)
        downloads[name] = target
    output = DESKTOP / 'runtime-dist-win'
    if output.exists(): shutil.rmtree(output)
    app = output / 'app'; app.mkdir(parents=True)
    for name, directory in [('python', output / 'python'), ('pillow', app)]:
        directory.mkdir(exist_ok=True)
        with zipfile.ZipFile(downloads[name]) as archive:
            for entry in archive.infolist():
                path = Path(entry.filename)
                if path.is_absolute() or '..' in path.parts or '\\' in entry.filename:
                    raise RuntimeError('Unsafe dependency archive path')
            archive.extractall(directory)
    shutil.copytree(PACKAGE, app / 'xiaoyou_runtime', ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    shutil.copyfile(DESKTOP / 'tools/runtime_entry.py', app / 'runtime_entry.py')
    (output / 'python/python313._pth').write_text('python313.zip\n.\n../app\n', encoding='utf-8')
    digest = hashlib.sha256()
    for path in sorted(PACKAGE.rglob('*.py')):
        digest.update(path.relative_to(PACKAGE).as_posix().encode()); digest.update(b'\0')
        digest.update(path.read_bytes()); digest.update(b'\0')
    version = re.search(r'__version__\s*=\s*["\']([^"\']+)', (PACKAGE / '__init__.py').read_text()).group(1)
    manifest = {'layout': 'python-embedded', 'platform': 'win32', 'architecture': 'AMD64',
        'runtime_version': version, 'python': lock['python']['version'], 'pillow': lock['pillow']['version'],
        'source_sha256': digest.hexdigest(), 'dependencies': lock, 'native_execution_verified': False}
    (output / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print(json.dumps({'bundle': 'runtime-dist-win', 'python': manifest['python'], 'native_execution_verified': False}))

if __name__ == '__main__': main()
