#!/usr/bin/env python3
"""Exercise the Mac guard with a disposable app, never the installed client."""
import os
import plistlib
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path


def wait_until(predicate, label, timeout=20):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.1)
    raise AssertionError('Timed out: ' + label)


def main():
    if sys.platform != 'darwin':
        raise SystemExit('This integration test requires macOS')
    binary = Path(sys.argv[1]).resolve()
    with tempfile.TemporaryDirectory(prefix='xiaoyu-guard-test-') as tmp:
        folder = Path(tmp).resolve()
        app = folder / 'GuardFixture.app'
        contents = app / 'Contents'
        executable = contents / 'MacOS/GuardFixture'
        executable.parent.mkdir(parents=True)
        (contents / 'Info.plist').write_bytes(plistlib.dumps({
            'CFBundleIdentifier': 'com.folotoy.xiaoyu.crash-guard-test',
            'CFBundleName': 'Xiaoyu Guard Fixture',
            'CFBundleExecutable': 'GuardFixture', 'CFBundlePackageType': 'APPL',
            'CFBundleVersion': '1', 'LSUIElement': True,
        }))
        source = folder / 'fixture.m'
        source.write_text('''#import <AppKit/AppKit.h>
#include <signal.h>
#include <unistd.h>
static void stop(int value) { _exit(value == SIGUSR1 ? 0 : 7); }
int main(void) { signal(SIGUSR1, stop); signal(SIGUSR2, stop);
  [NSApplication sharedApplication]; [NSApp setActivationPolicy:NSApplicationActivationPolicyAccessory];
  [NSApp run]; return 0; }
''')
        subprocess.run(['clang', '-framework', 'AppKit', str(source), '-o', str(executable)], check=True)
        log = folder / 'guard.log'
        with log.open('w') as output:
            guard = subprocess.Popen([str(binary), '--test-app',
                'com.folotoy.xiaoyu.crash-guard-test', str(app)], stdout=output, stderr=output)

        def log_text():
            return log.read_text()

        def pids():
            text = subprocess.check_output(['ps', '-axo', 'pid=,command='], text=True)
            return [int(line.strip().split(None, 1)[0]) for line in text.splitlines()
                    if len(line.strip().split(None, 1)) == 2
                    and line.strip().split(None, 1)[1] == str(executable)]

        def recovery_assertion():
            text = subprocess.check_output(['pmset', '-g', 'assertions'], text=True)
            return any(('pid %d(' % guard.pid) in line and 'xiaoyu crash recovery' in line
                       for line in text.splitlines())

        def launch():
            subprocess.run(['open', '-na', str(app)], check=True)
            pid = wait_until(lambda: next(iter(pids()), None), 'test app launch')
            wait_until(lambda: 'watching %d' % pid in log_text(), 'guard attached')
            return pid

        try:
            wait_until(lambda: 'guard ready' in log_text(), 'guard startup')
            assert not pids(), 'guard should not start an app on login'
            pid = launch()
            os.kill(pid, signal.SIGUSR1)
            wait_until(lambda: 'normal exit %d' % pid in log_text(), 'clean exit')
            time.sleep(3)
            assert not pids(), 'a clean quit must not restart the application'
            pid = launch()
            os.kill(pid, signal.SIGKILL)
            wait_until(lambda: 'abnormal exit %d;' % pid in log_text(), 'race crash detected')
            wait_until(lambda: not pids(), 'crashed process gone')
            manual_pid = launch()
            os.kill(manual_pid, signal.SIGUSR1)
            wait_until(lambda: 'normal exit %d' % manual_pid in log_text(), 'manual re-open then exit')
            time.sleep(3)
            assert not pids(), 'a pending retry must not undo a subsequent clean quit'
            guard.terminate()
            guard.wait(timeout=5)
            offset = len(log_text())
            with log.open('a') as output:
                guard = subprocess.Popen([str(binary), '--test-app',
                    'com.folotoy.xiaoyu.crash-guard-test', str(app)], stdout=output, stderr=output)
            wait_until(lambda: 'guard ready' in log_text()[offset:], 'fresh guard startup')
            pid = launch()
            os.kill(pid, signal.SIGKILL)
            wait_until(lambda: 'abnormal exit %d;' % pid in log_text(), 'retry crash detected')
            wait_until(lambda: not pids(), 'retry crashed process gone')
            held = folder / 'temporarily-unavailable.app'
            app.rename(held)
            # An invalid bundle at the expected path also exercises the
            # no-dialog NSWorkspace error path, not just the missing-path check.
            app.mkdir()
            try:
                wait_until(lambda: 'restart failed:' in log_text(), 'failed launch observed')
                wait_until(lambda: 'restart in 5 seconds' in log_text(), 'launch failure retried')
            finally:
                app.rmdir()
                held.rename(app)
            pid = wait_until(lambda: next(iter(pids()), None), 'recovery after failed launch', timeout=15)
            wait_until(lambda: 'watching %d' % pid in log_text(), 'recovered process watched')
            os.kill(pid, signal.SIGUSR1)
            wait_until(lambda: 'normal exit %d' % pid in log_text(), 'clean exit after launch retry')
            guard.terminate()
            guard.wait(timeout=5)
            offset = len(log_text())
            with log.open('a') as output:
                guard = subprocess.Popen([str(binary), '--test-app',
                    'com.folotoy.xiaoyu.crash-guard-test', str(app)], stdout=output, stderr=output)
            wait_until(lambda: 'guard ready' in log_text()[offset:], 'guard after launch retry')
            pid = launch()
            guard.terminate()
            guard.wait(timeout=5)
            offset = len(log_text())
            with log.open('a') as output:
                guard = subprocess.Popen([str(binary), '--test-app',
                    'com.folotoy.xiaoyu.crash-guard-test', str(app)], stdout=output, stderr=output)
            wait_until(lambda: 'watching %d' % pid in log_text()[offset:], 'attach to existing app')
            for attempt in range(3):
                os.kill(pid, signal.SIGUSR2 if attempt == 0 else signal.SIGKILL)
                old = pid
                wait_until(lambda: 'abnormal exit %d;' % old in log_text(), 'crash detected')
                wait_until(recovery_assertion, 'temporary wake protection')
                pid = wait_until(lambda: next((p for p in pids() if p != old), None),
                                 'restart %d' % (attempt + 1), timeout=25)
                wait_until(lambda: 'watching %d' % pid in log_text(), 'new process watched')
                if attempt == 0:
                    wait_until(lambda: not recovery_assertion(), 'protection released after stable restart', timeout=10)
            os.kill(pid, signal.SIGKILL)
            wait_until(lambda: 'restart limit reached' in log_text(), 'crash loop limit')
            wait_until(lambda: not recovery_assertion(), 'recovery protection released')
            time.sleep(3)
            assert not pids(), 'restart limit must stop a crash loop'
            print('PASS: clean quit, existing process, crash recovery, temporary wake protection, restart limit')
        except BaseException:
            print(log_text(), file=sys.stderr)
            raise
        finally:
            guard.terminate()
            try:
                guard.wait(timeout=5)
            except subprocess.TimeoutExpired:
                guard.kill()
                guard.wait()
            for pid in pids():
                try:
                    os.kill(pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass


if __name__ == '__main__':
    main()
