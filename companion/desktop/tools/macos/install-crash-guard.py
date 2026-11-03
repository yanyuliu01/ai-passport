#!/usr/bin/env python3
"""Install the optional per-user macOS crash guard without replacing settings."""
import os
import plistlib
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


LABEL = 'com.folotoy.xiaoyu.crash-guard'


def install(home, source, app, uid, run=subprocess.run):
    support = home / 'Library/Application Support/xiaoyu-crash-guard'
    plist = home / 'Library/LaunchAgents' / (LABEL + '.plist')
    if support.exists() or support.is_symlink() or plist.exists() or plist.is_symlink():
        raise FileExistsError('Existing crash guard files were left unchanged')
    with (app / 'Contents/Info.plist').open('rb') as stream:
        if plistlib.load(stream).get('CFBundleIdentifier') != 'com.folotoy.xiaoyu':
            raise ValueError('The installed app is not Xiaoyu')
    label = f'gui/{uid}/{LABEL}'
    if run(['launchctl', 'print', label], stdout=subprocess.DEVNULL,
           stderr=subprocess.DEVNULL).returncode == 0:
        raise FileExistsError('An existing crash guard service was left unchanged')
    plist.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='xiaoyu-guard-build-') as folder:
        built = Path(folder) / 'crash-guard'
        run(['swiftc', '-O', '-swift-version', '5', str(source), '-o', str(built)], check=True)
        support.mkdir(mode=0o700, parents=True)
        created_plist = False
        try:
            binary = support / 'crash-guard'
            shutil.copyfile(built, binary)
            binary.chmod(0o700)
            copied_source = support / 'crash-guard.swift'
            shutil.copyfile(source, copied_source)
            copied_source.chmod(0o600)
            log = support / 'events.log'
            log.touch(mode=0o600, exist_ok=False)
            job = {'Label': LABEL, 'ProgramArguments': [str(binary)], 'RunAtLoad': True,
                   'KeepAlive': True, 'ThrottleInterval': 10,
                   'StandardOutPath': str(log), 'StandardErrorPath': str(log)}
            with plist.open('xb') as stream:
                created_plist = True
                plistlib.dump(job, stream)
            plist.chmod(0o600)
            run(['launchctl', 'bootstrap', f'gui/{uid}', str(plist)], check=True)
        except BaseException:
            # Remove only files created by this installation attempt.
            if created_plist:
                plist.unlink(missing_ok=True)
            shutil.rmtree(support)
            raise
    return log


def main():
    if sys.platform != 'darwin':
        raise SystemExit('The crash guard is available only on macOS')
    try:
        log = install(Path.home(), Path(__file__).with_name('crash-guard.swift'),
                      Path('/Applications/小鱼.app'), os.getuid())
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        raise SystemExit(f'Crash guard installation failed: {error}') from error
    print(f'Crash guard installed. Event log: {log}')


if __name__ == '__main__':
    main()
