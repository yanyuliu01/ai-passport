<p align="right"><a href="code-cleanup-plan.zh_CN.md">简体中文</a> · <strong>English</strong></p>

# Code cleanup execution plan (phase one)

Status: execution plan, 2026-10-23, starts after the owner approves. Based on the
[Xiaoyou complete plan](xiaoyou-agent-model.md) and the
[interaction simplification issue list](app-runtime-simplification.md).

## Scope

This phase **only deletes; it adds nothing and moves no files**. As decided, the modular cleanup
follows the execution and recovery contracts; here we only remove code nobody uses and
compatibility that is no longer needed. The only behaviour change is removing the hidden nudge
(already decided).

Rules:

- Align and record a baseline before touching code. Each batch removes one kind of thing, in its
  own commit, and can be reverted on its own.
- After each batch run the same checks and compare with the baseline: no new failures.
- Do not restart the running Runtime; App publication and pushing wait for the owner.

## Batch 0: align local with the remote

Current state (checked 2026-10-23):

- Local and remote `feature/usage-history` diverge at `0010d2b`. The remote has 3 more commits
  (`2a60890`, `0c4ee0e`, `66e6ef7`, pushed by Codex from Windows); local has 3 more documentation
  commits.
- The 93 uncommitted local files match those 3 remote commits file by file, with three
  exceptions: the new local documents (local wins), 3 test files (the remote has the Windows
  portability fixes, remote wins), and two `.DS_Store` files (never committed).

Steps:

1. Back up: pack every uncommitted change into `build/` for rollback.
2. Re-check: compare the working tree with the remote file by file again; only the three
   exceptions above may differ. Stop and ask the owner if anything else does.
3. Discard the local uncommitted changes (the remote has the same content) and add `.DS_Store` to
   `.gitignore`.
4. Rebase the 3 local documentation commits onto the remote. Remote `2a60890` carries older
   versions of the issue list, the complete plan and the index; take the local version for every
   conflict, and the remote version of the Windows handoff document.
5. Run the baseline checks (see "Checks") and record the results as the reference for every later
   batch. `./tools/validate.sh` has failed before in sandboxes that forbid loopback ports; the
   recorded result is what counts.
6. Pushing waits for the owner.

Done when: `git status` is clean; compared with the remote only documentation commits are ahead;
the baseline is recorded.

## Batch 1: remove the hidden nudge (Runtime)

| Remove | Where |
| --- | --- |
| The nudge prompt `PROMISE_NUDGE` | `runtime/xiaoyou_runtime/voice_tools.py` |
| The `nudged` branch of the tool loop and its special handling | `runtime/xiaoyou_runtime/dsh_stream.py`, around lines 280-325 |
| Test assertions about the nudge | `runtime/tests/test_decision.py` |

Keep: the `promised()` regex as a counter only (as `xiaoyou.py` already uses it);
`promises.mark_nudged` is the due-reminder mechanism, unrelated to the nudge, and stays.

Checks: all Runtime tests, plus one new test: a reply the regex would take as a promise, with no
tool call, makes exactly one model call and produces exactly one reply.

## Batch 2: unused App code

| Remove | Evidence |
| --- | --- |
| `DsView.java` (82 lines) | No references anywhere in the repository |
| `LocalSpeechSession.java` and its test | Used only by its own test |
| `MainActivity` methods `fillTaskHead`, `refreshTaskThread`, `refreshTaskPending`, `iconButton` (about 130 lines) | Never called |
| `HubStore.pendingMessages` | Never called |
| Keyless `HubStore` overloads: `chatAsked(text)`, `chatStreaming(text)`, `chatHeard(placeholder, heard)`, the 3- and 4-argument `chatAnswered` | Only the keyed versions are used |
| `RuntimeClient.sendImage` and the 2-, 3- and 4-argument `send` overloads | Only the 5-argument version is used |

Checks: Android unit tests, debug build and Lint; the removed names have no references left.
Behaviour is unchanged.

## Batch 3: App compatibility with old Runtimes

Every Runtime in use is 0.20.0 or later (Mac 0.23.1, Windows 0.20.0), and all support `/v1/sync`
with usage.

| Remove | Where |
| --- | --- |
| The old `fetchFeed` and the capability branch in `watchLoop` that falls back to feed | `RuntimeClient.java` |
| Treating a 404 as "old Runtime" | `RuntimeClient.watchLoop` |
| The extra `fetchFeed` refresh after a task finishes (issue C2; sync already pushes changes) | `RuntimeClient.submitScoped` |
| Checks for the `sync` and `sync_usage` capabilities (assumed present) | `RuntimeClient.watchLoop` |
| Treating a 404 from the packs endpoint as "none" (0.20.0 already has it) | `RuntimeClient.appPacks` |
| The write-only `CAPABILITIES` map and `HubStore.feedSeq()`, used only by feed | `RuntimeClient`, `HubStore` |

Adjusted during execution (2026-10-23): the `presentation_v1` check **stays**. Windows runs 0.20.0,
which lacks it; assuming it would make the phone resync from scratch on every reconnect.

Known regression (2026-10-23 device test): without the fetch right after a task finishes, the phone
does not see task results until sync catches up (about 10 minutes on the Mac; see issue N10). The
owner decided not to restore it in the App and to fix it on the Runtime side. Windows chat, usage and
task results, and Mac chat and usage, all passed.

Checks: Android unit tests, debug build and Lint; then one real-phone session each against the Mac
and the Windows Runtime, confirming chat, usage and task results all show correctly. Installing on
the phone goes through the existing publication flow and needs the owner's approval.

## Not removed yet

| Item | Reason |
| --- | --- |
| Runtime `/v1/feed`, per-message wait and stream endpoints; `/v1/feed` in the desktop relay allowlist | Installed older Apps still use them; removed after clients migrate, per section 6 of the complete plan |
| `RuntimeClient.turns()` migration of old chat history | First confirm old phone data has been migrated |
| The phone uploading chat history (`shareHistory`) | Handled in the "one state" step |
| Local backup folders such as `runtime/backup-*` | Already ignored by `.gitignore` and not in the repository; cleaning them is the owner's call |

## Checks

| Scope | Command |
| --- | --- |
| Documents and repository rules | `python3 tools/check_repo.py` |
| Runtime | From `runtime/`: `python3 -m unittest discover -s tests` |
| Android | From `companion/android/`: `./gradlew testDebugUnitTest assembleDebug lint` (set `JAVA_HOME` to JDK 17 and `ANDROID_HOME` first) |
| Desktop client | From `companion/desktop/`: `npm test` |
| Full gate | `./tools/validate.sh` |

Each batch reports build, host tests, device tests and unverified items under the
[cross-stack reliability rules](cross-stack-reliability.md).

## Rollback

One commit per batch, reverted with `git revert`. Batch 0 also keeps the backup in `build/`. The
Runtime is not restarted and the App is not published without approval, so rollback does not
affect services in use.

## Expected size

Batch 1 about 50 lines, batch 2 about 300, batch 3 about 120: about 470 lines removed in total, no
new feature code (one new test only).
