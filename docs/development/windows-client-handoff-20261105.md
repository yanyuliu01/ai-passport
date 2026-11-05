<p align="right"><a href="windows-client-handoff-20261105.zh_CN.md">简体中文</a> · <strong>English</strong></p>

# Windows client integration handoff (2026-11-05)

This branch is a source checkpoint for review and integration, not a release or evidence of a Windows install. It captures the Mac working tree based on `9808e23`, together with the public persona update `12d1e4d`. The Mac source identifies desktop 0.1.44, Android 0.41.1 (versionCode 101), and Runtime 0.28.29. These version strings identify source, not the versions currently installed on Windows or a phone. Windows work may have advanced independently; inspect it before choosing what to integrate.

## First preserve and compare

1. On Windows read `AGENTS.md`, inspect `git status --short --branch`, existing worktrees, current branch and installed desktop/Runtime versions. If configured, read shared history as instructed there. Do not discard local changes, replace configuration, credentials, chat history or interrupt active tasks.
2. Fetch `github` and inspect this branch in a separate worktree. Replace `<branch>` with `codex/windows-handoff-20261105` and choose an unused path:

   ```powershell
   git fetch github
   git worktree add --detach ..\xiaoyu-mac-review github/codex/windows-handoff-20261105
   ```

   Compare its commit with the Windows branch and uncommitted files. Integrate selectively, resolve conflicts by behavior and run tests; do not reset the Windows checkout or assume the larger version number supersedes independent fixes. Preserve existing installers for rollback. Do not copy live state or credentials into Git.
3. The previous integration context is in [project status](project-status.md) and [Windows Runtime recovery handoff](windows-runtime-recovery-handoff.md); those records describe historical checkpoints, not current Windows state.

## Changes to review

- Desktop: account and relay recovery, device proofs, image transfer, WeChat connection and attachments, helper and Runtime startup, plus packaging. Compare Windows-specific lifecycle and reconnect behavior before integration. Inspect `companion/desktop/src/core/`, `src/relay/`, `src/ui/` and their tests.
- Android and Runtime: history synchronization and recovery, media, channel ownership and WeChat delivery, conversation context and history retrieval, interruption and delivery receipts. These are a coordinated cross-stack snapshot; avoid assuming an updated desktop alone gives equivalent behavior. Review `runtime/README.md`, `runtime/xiaoyu_runtime/channels/` and related tests.
- Runtime 0.28.29 was previously deployed on the Mac local service, with one real WeChat history-follow-up checked. The desktop-bundled Runtime was reported older. This is historical evidence: verify the running and packaged versions before any change. A model follow-up exceeded the three-second target; real phone timing and full cross-device recovery were not established.
- This branch includes source changes, not personal state, credentials, installed binaries or proof of Windows operation. It is a checkpoint assembled from a broad working tree; review it as such. Other Mac worktrees and the older dirty root checkout are not included. The public persona from `12d1e4d` differs from the uncommitted Mac source persona, so this is not a byte-for-byte reproduction of the deployed bundle.

## Build and validate on Windows

Use a native Windows isolated worktree with Node.js 24+ and Python 3.11+. Review `companion/desktop/tools/build-windows.ps1` first, then from the repository root:

```powershell
& .\companion\desktop\tools\build-windows.ps1
```

The script installs locked dependencies into local build locations, bundles the native Runtime and agents, runs desktop tests, builds an NSIS installer and prints its SHA-256. Keep `package.json`, lock file, bundled Runtime manifest and the installer tied to the same reviewed source; a Mac binary or a cross-built preview does not prove native Windows behavior. Choose a new desktop version if the integrated source differs from an existing release. Do not install or restart an active service without the authorization required by the project rules.

Record build and test results against exact commits and versions. Exercise clean startup, existing data and account preservation, helper discovery, relay and Runtime disconnection/recovery, task continuity, history and media across Windows/Mac/phone, and WeChat if used. Measure first meaningful response at the actual client; report p50/p95/max and misses under the cross-stack contract. Clearly label tests not run, and do not call a build a device or end-to-end pass. Follow [cross-stack reliability](cross-stack-reliability.md), [desktop README](../../companion/desktop/README.md) and [build and test](engineering/build-and-test.md).
