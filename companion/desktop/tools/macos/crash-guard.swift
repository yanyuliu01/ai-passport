import AppKit
import Darwin
import Foundation
import IOKit.pwr_mgt

// launchd keeps this small observer alive; it does not launch the app on login.
// NSWorkspace observes application launches, and kqueue waits for exits without polling.
final class CrashGuard {
    private let bundleID: String
    private let appURL: URL
    private var watched = Set<pid_t>()
    private var restartTimes: [TimeInterval] = []
    private var shuttingDown = false
    private var observers: [NSObjectProtocol] = []
    private var applicationsObservation: NSKeyValueObservation?
    private var recoveryAssertion: IOPMAssertionID?
    private var recoveryGeneration = 0

    init(bundleID: String, appPath: String) {
        self.bundleID = bundleID
        self.appURL = URL(fileURLWithPath: appPath).standardizedFileURL.resolvingSymlinksInPath()
    }

    private func matches(_ app: NSRunningApplication) -> Bool {
        app.bundleIdentifier == bundleID &&
        app.bundleURL?.standardizedFileURL.resolvingSymlinksInPath() == appURL
    }

    func start() {
        let center = NSWorkspace.shared.notificationCenter
        // Unlike didLaunchApplicationNotification, this also observes background-only apps.
        applicationsObservation = NSWorkspace.shared.observe(\.runningApplications, options: [.initial, .new]) { [weak self] _, change in
            guard let self, let applications = change.newValue else { return }
            for app in applications where self.matches(app) { self.watch(app.processIdentifier) }
        }
        observers.append(center.addObserver(forName: NSWorkspace.willPowerOffNotification,
                                            object: nil, queue: .main) { [weak self] _ in
            self?.shuttingDown = true
        })
        print("guard ready")
    }

    private func watch(_ pid: pid_t) {
        guard pid > 0, watched.insert(pid).inserted else { return }
        DispatchQueue.global(qos: .utility).async { [weak self] in
            let queue = kqueue()
            guard queue >= 0 else {
                DispatchQueue.main.async { self?.watchFailed(pid, "kqueue") }
                return
            }
            defer { close(queue) }
            var change = kevent(ident: UInt(pid), filter: Int16(EVFILT_PROC),
                                flags: UInt16(EV_ADD | EV_ENABLE | EV_ONESHOT),
                                fflags: UInt32(NOTE_EXIT) | UInt32(NOTE_EXITSTATUS), data: 0, udata: nil)
            guard kevent(queue, &change, 1, nil, 0, nil) == 0 else {
                DispatchQueue.main.async { self?.watchFailed(pid, "register") }
                return
            }
            print("watching \(pid)")
            DispatchQueue.main.async { self?.watchReady(pid) }
            var event = kevent()
            let count = kevent(queue, nil, 0, &event, 1, nil)
            guard count == 1 else {
                DispatchQueue.main.async { self?.watchFailed(pid, "wait") }
                return
            }
            DispatchQueue.main.async { self?.exited(pid, status: event.data, flags: event.fflags) }
        }
    }

    private func holdAwakeDuringRecovery() {
        recoveryGeneration += 1
        let generation = recoveryGeneration
        if recoveryAssertion == nil {
            var id: IOPMAssertionID = 0
            let result = IOPMAssertionCreateWithName(kIOPMAssertionTypePreventUserIdleSystemSleep as CFString,
                IOPMAssertionLevel(kIOPMAssertionLevelOn), "xiaoyu crash recovery" as CFString, &id)
            if result == kIOReturnSuccess { recoveryAssertion = id }
            else { print("recovery sleep protection unavailable: \(result)") }
        }
        // Never keep the computer awake indefinitely if launch fails.
        DispatchQueue.main.asyncAfter(deadline: .now() + 30) { [weak self] in
            guard let self, self.recoveryGeneration == generation else { return }
            self.releaseRecoveryAssertion()
        }
    }

    private func releaseRecoveryAssertion() {
        if let id = recoveryAssertion { IOPMAssertionRelease(id); recoveryAssertion = nil }
    }

    private func watchReady(_ pid: pid_t) {
        guard recoveryAssertion != nil else { return }
        let generation = recoveryGeneration
        // Give the relaunched client time to establish its own sleep protection.
        DispatchQueue.main.asyncAfter(deadline: .now() + 5) { [weak self] in
            guard let self, self.recoveryGeneration == generation, self.watched.contains(pid) else { return }
            self.releaseRecoveryAssertion()
        }
    }

    private func watchFailed(_ pid: pid_t, _ stage: String) {
        watched.remove(pid)
        // If the exit status is unavailable, do not guess that it was a crash.
        print("watch unavailable \(pid) \(stage)")
    }

    private func exited(_ pid: pid_t, status: Int, flags: UInt32) {
        watched.remove(pid)
        guard flags & UInt32(NOTE_EXITSTATUS) != 0 else {
            print("exit status unavailable \(pid)")
            return
        }
        guard status != 0 else {
            recoveryGeneration += 1 // Cancel any retry after a deliberate exit.
            releaseRecoveryAssertion()
            print("normal exit \(pid)")
            return
        }
        guard !shuttingDown else { releaseRecoveryAssertion(); print("shutdown; no restart"); return }
        print("abnormal exit \(pid); scheduling recovery")
        scheduleRecovery()
    }

    private func scheduleRecovery() {
        guard !shuttingDown else { releaseRecoveryAssertion(); return }
        let now = ProcessInfo.processInfo.systemUptime
        restartTimes.removeAll { now - $0 > 600 }
        guard restartTimes.count < 3 else {
            recoveryGeneration += 1
            releaseRecoveryAssertion()
            print("restart limit reached")
            return
        }
        let delays: [TimeInterval] = [2, 5, 15]
        let delay = delays[restartTimes.count]
        restartTimes.append(now)
        holdAwakeDuringRecovery()
        let generation = recoveryGeneration
        print("restart in \(Int(delay)) seconds")
        DispatchQueue.main.asyncAfter(deadline: .now() + delay) { [weak self] in
            guard let self, !self.shuttingDown, self.recoveryGeneration == generation else { return }
            // A manual launch or a different recovery may have already won the race.
            guard !NSRunningApplication.runningApplications(withBundleIdentifier: self.bundleID).contains(where: {
                self.matches($0) && !$0.isTerminated && kill($0.processIdentifier, 0) == 0
            }) else {
                print("already running; no restart")
                return
            }
            guard FileManager.default.fileExists(atPath: self.appURL.path) else {
                print("restart failed: application not found")
                self.scheduleRecovery()
                return
            }
            let config = NSWorkspace.OpenConfiguration()
            config.activates = false
            config.promptsUserIfNeeded = false
            config.allowsRunningApplicationSubstitution = false
            NSWorkspace.shared.openApplication(at: self.appURL, configuration: config) { [weak self] app, error in
                if let error {
                    print("restart failed: \(error.localizedDescription)")
                    DispatchQueue.main.async {
                        guard let self, self.recoveryGeneration == generation else { return }
                        self.scheduleRecovery()
                    }
                }
                else if let app { print("restarted \(app.processIdentifier)") }
            }
        }
    }
}

setvbuf(stdout, nil, _IOLBF, 0)
let arguments = CommandLine.arguments
let testMode = arguments.count == 4 && arguments[1] == "--test-app"
let guarder = CrashGuard(bundleID: testMode ? arguments[2] : "com.folotoy.xiaoyu",
                         appPath: testMode ? arguments[3] : "/Applications/小鱼.app")
guarder.start()
RunLoop.main.run()
