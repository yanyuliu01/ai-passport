#!/usr/bin/env python3
"""Assemble private, locked Node/DSH/Pi resources without user config or install scripts."""
import argparse
import hashlib
import json
import os
import platform
import shutil
import subprocess
import tarfile
import tempfile
import urllib.request
import zipfile
from pathlib import Path

DESKTOP = Path(__file__).resolve().parents[1]
LOCKS = DESKTOP / 'tools/agent-runtime'
# Runtime only launches the headless/ACP profiles, never DSH's separate desktop,
# browser, voice or office surfaces. Retain the full declared transitive graph
# of these roots, including optional/peer packages installed for the target.
DSH_RUNTIME_ROOTS = {
    '@deepseek-ai/cordis', '@deepseek-ai/dsh-hmr', '@deepseek-ai/cordis-plugin-include',
    '@deepseek-ai/cordis-plugin-loader', '@deepseek-ai/cordis-plugin-timer',
    '@deepseek-ai/dsh-app-boot', '@deepseek-ai/dsh-home-paths', 'commander', 'js-yaml',
    '@deepseek-ai/dsh-base', '@deepseek-ai/dsh-headless', '@deepseek-ai/dsh-acp-app',
    '@deepseek-ai/dsh-mcp-client', '@deepseek-ai/dsh-plugin-manager',
    '@deepseek-ai/dsh-tool-fs-search', '@deepseek-ai/dsh-tool-skill',
}


def prune_packages(root):
    lock = json.loads((root / 'package-lock.json').read_text())
    queue = [root / 'node_modules' / name for name in ['@deepseek-ai/dsh', '@mariozechner/pi-coding-agent']]
    seen = set()

    def resolve_dependency(name, folder):
        for parent in [folder, *folder.parents]:
            if not parent.is_relative_to(root):
                break
            candidate = parent / 'node_modules' / name
            if (candidate / 'package.json').is_file():
                return candidate

    while queue:
        folder = queue.pop()
        if folder in seen:
            continue
        seen.add(folder)
        metadata = json.loads((folder / 'package.json').read_text())
        dependencies = {**metadata.get('dependencies', {}), **metadata.get('optionalDependencies', {}),
                        **metadata.get('peerDependencies', {})}
        if metadata['name'] == '@deepseek-ai/dsh':
            dependencies = {name: version for name, version in dependencies.items() if name in DSH_RUNTIME_ROOTS}
        for name in dependencies:
            candidate = resolve_dependency(name, folder)
            if candidate:
                queue.append(candidate)
    excluded = []
    for name in sorted(lock['packages'], key=len, reverse=True):
        if not name:
            continue
        folder = root / name
        if folder not in seen and (folder / 'package.json').is_file():
            # A retained nested dependency requires the ancestor directory. Keep
            # that rare package whole rather than removing its needed child.
            if any(path.is_relative_to(folder) for path in seen):
                continue
            shutil.rmtree(folder)
            excluded.append(name)
    return sorted(excluded)


def sha(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def host_target():
    system = {'Darwin': 'darwin', 'Windows': 'win32'}.get(platform.system())
    arch = {'arm64': 'arm64', 'aarch64': 'arm64', 'AMD64': 'x64', 'x86_64': 'x64'}.get(platform.machine())
    return f'{system}-{arch}'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--target', default=host_target(), choices=['darwin-arm64', 'darwin-x64', 'win32-x64'])
    args = parser.parse_args()
    system, arch = args.target.split('-')
    node_lock = json.loads((LOCKS / 'node-lock.json').read_text())
    node = node_lock['targets'][args.target]
    cache = DESKTOP / '.build-cache/agent-runtime'
    cache.mkdir(parents=True, exist_ok=True)
    archive = cache / node['url'].rsplit('/', 1)[1]
    if not archive.exists() or sha(archive) != node['sha256']:
        with urllib.request.urlopen(node['url'], timeout=60) as response, archive.open('wb') as stream:
            shutil.copyfileobj(response, stream)
    if sha(archive) != node['sha256']:
        raise RuntimeError('Official Node archive checksum mismatch')
    output = DESKTOP / ('agents-dist-win' if system == 'win32' else 'agents-dist')
    with tempfile.TemporaryDirectory(prefix='xiaoyu-agents-', dir=cache) as temporary:
        root = Path(temporary)
        binary = 'node.exe' if system == 'win32' else 'node'
        # Extract only known members; never unpack arbitrary archive paths or links.
        prefix = archive.name.removesuffix('.tar.gz').removesuffix('.zip')
        if system == 'win32':
            with zipfile.ZipFile(archive) as source:
                for member, destination in [(prefix + '/node.exe', binary), (prefix + '/LICENSE', 'NODE-LICENSE')]:
                    (root / destination).write_bytes(source.read(member))
        else:
            with tarfile.open(archive) as source:
                for member, destination in [(prefix + '/bin/node', binary), (prefix + '/LICENSE', 'NODE-LICENSE')]:
                    info = source.getmember(member)
                    if not info.isfile():
                        raise RuntimeError('Node archive member is not a regular file')
                    with source.extractfile(info) as stream:
                        (root / destination).write_bytes(stream.read())
            (root / binary).chmod(0o755)
        for name in ['package.json', 'package-lock.json']:
            shutil.copyfile(LOCKS / name, root / name)
        for name in ['pi-entry.mjs', 'pi-session.mjs']:
            shutil.copyfile(LOCKS / name, root / name)
        # Build tools may use npm. End users never need npm, Node or a global CLI.
        npm = shutil.which('npm.cmd' if os.name == 'nt' else 'npm')
        if not npm:
            raise RuntimeError('npm is required on the build host only')
        env = {key: value for key, value in os.environ.items() if not key.lower().startswith('npm_config_')}
        for name in ['user-npmrc', 'global-npmrc']:
            (root / name).write_text('')
        env.update({'NPM_CONFIG_USERCONFIG': str(root / 'user-npmrc'),
                    'NPM_CONFIG_GLOBALCONFIG': str(root / 'global-npmrc'),
                    'NPM_CONFIG_CACHE': str(cache / 'npm')})
        command = [npm, 'ci', '--ignore-scripts', '--omit=dev', '--no-audit', '--no-fund',
                   '--registry=https://registry.npmjs.org', f'--os={system}', f'--cpu={arch}']
        if os.name == 'nt':
            # npm's fixed .cmd path/arguments go through subprocess' Windows handling;
            # no user input or provider credentials enter this build-only command.
            subprocess.run(command, cwd=root, env=env, check=True, shell=False)
        else:
            subprocess.run(command, cwd=root, env=env, check=True)
        for name in ['user-npmrc', 'global-npmrc']:
            (root / name).unlink()
        excluded = prune_packages(root)
        # No executable shims/symlinks: invoke Node + each package's declared JS entry.
        for directory in (root / 'node_modules').rglob('.bin'):
            if directory.is_dir():
                shutil.rmtree(directory)
        # Development-only maps/declarations are never executed. Keep JS, native
        # libraries, assets, manifests and upstream notices intact.
        for path in (root / 'node_modules').rglob('*'):
            if path.is_file() and (path.name.endswith('.map') or path.name.endswith('.d.ts') or path.name == '.gitkeep'):
                path.unlink()
        packages = json.loads((root / 'package.json').read_text())['dependencies']
        tools = {}
        for name, package in [('dsh', '@deepseek-ai/dsh'), ('pi', '@mariozechner/pi-coding-agent')]:
            folder = root / 'node_modules' / package
            metadata = json.loads((folder / 'package.json').read_text())
            if metadata['version'] != packages[package] or metadata.get('license') != 'MIT':
                raise RuntimeError('Unexpected bundled package version/license: ' + package)
            entry = metadata['bin'] if isinstance(metadata['bin'], str) else metadata['bin'][name]
            path = (folder / entry).resolve()
            if not path.is_relative_to(root) or not path.is_file():
                raise RuntimeError('Invalid CLI entry point')
            tools[name] = {'package': package, 'version': metadata['version'],
                           'entry': path.relative_to(root).as_posix(), 'license': metadata['license']}
        tools['pi']['upstream_entry'] = tools['pi']['entry']
        tools['pi']['entry'] = 'pi-entry.mjs'
        native = args.target == host_target()
        if native:
            for tool in tools.values():
                probe = subprocess.run([str(root / binary), str(root / tool['entry']), '--version'],
                                       cwd=root, env={'PATH': '', 'HOME': str(root / 'qa-home'),
                                       'USERPROFILE': str(root / 'qa-home'), 'CI': '1', 'NO_COLOR': '1',
                                       'DSH_HOME': str(root / 'qa-home/dsh'),
                                       'PI_CODING_AGENT_DIR': str(root / 'qa-home/pi'),
                                       **({'SystemRoot': os.environ['SystemRoot']} if 'SystemRoot' in os.environ else {})},
                                       timeout=30, capture_output=True, text=True)
                if probe.returncode or tool['version'] not in probe.stdout + probe.stderr:
                    raise RuntimeError('Bundled CLI version smoke check failed: ' + tool['package'] + '\n' + probe.stderr[-1000:])
            for profile in ['headless', 'acp']:
                probe = subprocess.run([str(root / binary), str(root / tools['dsh']['entry']), '--profile', profile, '--help'],
                                       cwd=root, env=env_for_smoke(root), timeout=30, capture_output=True, text=True)
                if probe.returncode or 'Usage:' not in probe.stdout + probe.stderr:
                    raise RuntimeError('Bundled DSH profile boot failed: ' + profile)
            shutil.rmtree(root / 'qa-home', ignore_errors=True)
        # electron-builder deliberately ignores a resource root's node_modules.
        # A nested payload keeps Node's resolution layout and copies in full.
        payload = root / 'payload'
        payload.mkdir()
        for path in list(root.iterdir()):
            if path != payload:
                path.rename(payload / path.name)
        for tool in tools.values():
            tool['entry'] = 'payload/' + tool['entry']
            if 'upstream_entry' in tool:
                tool['upstream_entry'] = 'payload/' + tool['upstream_entry']
        files = {}
        for path in sorted(root.rglob('*')):
            if path.is_symlink():
                raise RuntimeError('Unexpected symlink in agent bundle')
            if path.is_file():
                files[path.relative_to(root).as_posix()] = sha(path)
        manifest = {'v': 1, 'platform': system, 'architecture': arch, 'node': 'payload/' + binary,
                    'node_version': node_lock['version'], 'node_archive_sha256': node['sha256'],
                    'lock_sha256': sha(LOCKS / 'package-lock.json'), 'node_lock_sha256': sha(LOCKS / 'node-lock.json'),
                    'builder_sha256': sha(Path(__file__)), 'profiles': ['headless', 'acp'], 'excluded_packages': excluded,
                    'tools': tools, 'files': files, 'bytes': sum((root / name).stat().st_size for name in files),
                    'native_smoke_pass': native}
        (root / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
        if output.exists():
            shutil.rmtree(output)
        shutil.copytree(root, output)
    print(json.dumps({key: manifest[key] for key in ['platform', 'architecture', 'node_version', 'tools', 'bytes', 'native_smoke_pass']}))


def env_for_smoke(root):
    return {'PATH': '', 'HOME': str(root / 'qa-home'), 'USERPROFILE': str(root / 'qa-home'),
            'CI': '1', 'NO_COLOR': '1', 'DSH_HOME': str(root / 'qa-home/dsh'),
            'PI_CODING_AGENT_DIR': str(root / 'qa-home/pi'),
            **({'SystemRoot': os.environ['SystemRoot']} if 'SystemRoot' in os.environ else {})}


if __name__ == '__main__':
    main()
