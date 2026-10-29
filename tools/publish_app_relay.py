#!/usr/bin/env python3
"""Upload verified APKs to the owner's relay, then atomically publish Runtime metadata."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import tempfile
import ssl
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'runtime'))
from xiaoyu_runtime.app_release import AppRelease, _metadata_beside
from xiaoyu_runtime.config import load


def verify_download(url, sha, size, prefix, certificate):
    der = ssl.PEM_cert_to_DER_cert(certificate.decode())
    if hashlib.sha256(der).hexdigest() != '9c5e10134c225cf6d19e566532ee8464f85f1be97ccff4ccf8aa3653ba1f442b':
        raise ValueError('Relay certificate does not match the installed App pin')
    context = ssl.create_default_context(cadata=certificate.decode())
    request = urllib.request.Request(url, headers={'Range': 'bytes=0-%d' % (len(prefix)-1)})
    with urllib.request.urlopen(request, context=context, timeout=15) as reply:
        if reply.status != 206 or reply.headers.get('ETag') != '"'+sha+'"' or reply.read(65) != prefix:
            raise ValueError('Published HTTPS download/range verification failed')
        if reply.headers.get('Content-Range') != 'bytes 0-%d/%d' % (len(prefix)-1, size):
            raise ValueError('Published HTTPS size mismatch')


def publish(config_path, apk_dir, settings_path, note):
    settings = json.loads(settings_path.read_text())
    host, directory, base = (settings[k] for k in ('host', 'directory', 'base_url'))
    if not re.fullmatch(r'[a-zA-Z0-9_.-]+@[a-zA-Z0-9.-]+', host) or not re.fullmatch(r'/[a-zA-Z0-9_./-]+', directory):
        raise ValueError('Invalid release SSH target')
    if base != 'https://150.158.176.23/v1/releases/apps':
        raise ValueError('Release origin must match the App account certificate pin')
    ssh = ['ssh', '-i', str(Path(settings['key']).expanduser()), '-o', 'BatchMode=yes', '-o',
           'StrictHostKeyChecking=yes', '-o', 'UserKnownHostsFile=' + str(Path(settings['known_hosts']).expanduser()), host]
    def remote(command, data=None):
        return subprocess.run(ssh + [command], input=data, check=True, capture_output=True).stdout
    remote('mkdir -p ' + shlex.quote(directory))
    packages, urls, hashes = {}, {}, {}
    version = None
    for abi in ('universal', 'arm64-v8a', 'armeabi-v7a', 'x86', 'x86_64'):
        path = apk_dir / ('app-' + abi + '-debug.apk')
        meta = _metadata_beside(path)
        identity = (meta.get('version_code'), meta.get('version_name'))
        if not path.is_file() or meta.get('abi') != abi or not identity[0] or version and identity != version:
            raise ValueError('Missing or inconsistent Gradle APK metadata: ' + abi)
        version = identity
        sha = hashlib.sha256(path.read_bytes()).hexdigest()
        if not 0 < path.stat().st_size <= 512 * 1024 * 1024 or path.read_bytes()[:4] != b'PK\x03\x04':
            raise ValueError('Invalid APK: ' + abi)
        target = directory + '/' + sha + '.apk'
        exists = remote('if test -f ' + shlex.quote(target) + '; then sha256sum ' + shlex.quote(target) + '; fi').decode()
        if exists.split()[:1] != [sha]:
            temporary = target + '.upload-' + os.urandom(6).hex()
            with path.open('rb') as source:
                # stdin goes straight to SSH; no provider/account credentials are copied.
                done = subprocess.run(ssh + ['cat > ' + shlex.quote(temporary)], stdin=source, capture_output=True)
                if done.returncode: raise RuntimeError('APK upload failed: ' + abi)
            actual = remote('sha256sum ' + shlex.quote(temporary)).decode().split()[0]
            if actual != sha: raise ValueError('Relay APK checksum mismatch: ' + abi)
            remote('chmod 644 ' + shlex.quote(temporary) + ' && mv ' + shlex.quote(temporary) + ' ' + shlex.quote(target))
        packages[abi], urls[abi], hashes[abi] = path, base + '/' + sha + '.apk', sha
        print('Relay verified:', abi, sha)
    certificate = remote('cat ' + shlex.quote(str(Path(directory).parents[1] / 'tls/server.crt')))
    for abi, path in packages.items():
        with path.open('rb') as source: prefix = source.read(64)
        verify_download(urls[abi], hashes[abi], path.stat().st_size, prefix, certificate)
    config = load(config_path)
    manifest = {'version_code': version[0], 'version_name': version[1], 'note': note,
                'variants': {abi: {'sha256': hashes[abi], 'size': path.stat().st_size, 'download_url': urls[abi]}
                             for abi, path in packages.items()}}
    latest = directory + '/latest.json'
    temporary = latest + '.upload-' + os.urandom(6).hex()
    remote('cat > ' + shlex.quote(temporary), json.dumps(manifest).encode())
    remote('chmod 644 ' + shlex.quote(temporary) + ' && mv ' + shlex.quote(temporary) + ' ' + shlex.quote(latest))
    result = AppRelease(config.state_dir).publish(packages['universal'], note,
        variants={abi: path for abi, path in packages.items() if abi != 'universal'},
        download_url=urls['universal'], variant_download_urls={abi: url for abi, url in urls.items() if abi != 'universal'})
    print('Runtime published:', result['version_name'], result['version_code'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--apk-dir', type=Path, default=ROOT / 'companion/android/app/build/outputs/apk/debug')
    parser.add_argument('--settings', type=Path, default=Path.home()/'.config/xiaoyu/release-publisher.json')
    parser.add_argument('--note', required=True)
    args = parser.parse_args()
    publish(args.config, args.apk_dir, args.settings, args.note)


if __name__ == '__main__':
    main()
