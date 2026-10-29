#!/usr/bin/env python3
"""Read provenance from each packed app; do not label old packages with current source."""
import hashlib
import json
import subprocess
import re
from datetime import datetime, timezone
from pathlib import Path

root = Path(__file__).resolve().parents[1]
package = json.loads((root / 'package.json').read_text())
release = root / 'release'
builds = {}
for platform, relative in [('darwin', 'mac-arm64/小鱼.app/Contents/Resources/app.asar'), ('win32', 'win-unpacked/resources/app.asar')]:
    archive = release / relative
    if not archive.exists(): continue
    output = subprocess.check_output(['node', '--input-type=module', '-e',
        "import {extractFile} from '@electron/asar'; process.stdout.write(extractFile(process.argv[1], 'dist-main/build-info.json'));", str(archive)], cwd=root)
    builds[platform] = json.loads(output)
artifacts = []
for path in sorted(release.iterdir()):
    if path.suffix not in ('.dmg', '.exe', '.zip'): continue
    if not re.search(r'(?<![0-9.])' + re.escape(package['version']) + r'(?![0-9.])', path.stem): continue
    platform = 'darwin' if path.suffix == '.dmg' else 'win32'
    if platform not in builds: raise RuntimeError('Missing packed provenance for ' + path.name)
    if builds[platform].get('version') != package['version']: raise RuntimeError('Packed version mismatch for ' + path.name)
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''): digest.update(chunk)
    artifacts.append({'name': path.name, 'platform': platform, 'size': path.stat().st_size, 'sha256': digest.hexdigest()})
manifest = {'version': package['version'], 'created_utc': datetime.now(timezone.utc).isoformat(),
            'electron': package['devDependencies']['electron'], 'builds': builds,
            'distribution': 'private-unsigned-preview', 'artifacts': artifacts}
(release / 'manifest.json').write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + '\n')
print(json.dumps(manifest, indent=2, ensure_ascii=False))
