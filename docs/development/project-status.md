<p align="right">
  <a href="project-status.zh_CN.md">简体中文</a> · <strong>English</strong>
</p>

# Project status and source synchronization

Snapshot: **2026-10-28, Asia/Shanghai**. This is a progress index, not a release
or a claim of complete acceptance. The subsystem documents linked below remain
authoritative for implementation and test evidence.

## Current baseline

| Part | Committed source included in this synchronization | Live or pending evidence |
| --- | --- | --- |
| Runtime | **0.24.0**; three-layer migration, durable execution/recovery, conversation ownership, negotiated sync, bounded context and wake lifecycles through `aba494e`; helper model preferences in `080d0f4` | Both Windows and Mac `/healthz` reported **0.24.0** during this inspection. A version string does not establish identical binaries or acceptance |
| Desktop | **0.1.16**, `080d0f4`: accounts, public relay, helper model selection; earlier five-entry UI, owner reviews and late Runtime startup recovery; `3f1b905` fixes restarting a peer sync stopped before its account link is ready | Mac package metadata independently reports **0.1.16**. Retained Windows inspection reports 0.1.16 connected to 0.24.0, account retention and model selection working; the new reconnect fix has source-level regression coverage, but installed cross-computer acceptance remains open |
| Android | **0.33.2 (71)**, `81f658a`: avoid replaying and re-saving unchanged history; earlier sync-cache and UI-thread work | **0.33.4 (73)** named APKs are published, but the corresponding source changes remain uncommitted in the Mac account/relay worktree. They are not part of this source baseline; phone installation was not checked in this inspection |
| Firmware | Existing committed firmware, unchanged by this synchronization | No new build identity, flash or physical-device acceptance is asserted here |

Implementation and shipping details: [Runtime](../../runtime/README.md),
[desktop](../../companion/desktop/README.md), [Android](../../companion/README.md),
[firmware](../claude-pocket.md).

## What was reconciled

The GitHub default branch is already **`feature/usage-history`**. Before this
synchronization it pointed to `66e6ef7`, while Windows' primary checkout was at
`12bbd86` with **59 unpublished commits**. These include the latest Runtime
refactor, client migration, desktop 0.1.16 and the cross-computer history issue
record. The remote `claude/client-five-entries` branch stopped at `aba494e`;
that history is already included in `12bbd86`.

During inspection Windows advanced to `b4002be`: `3f1b905` adds the peer-sync
reconnect fix, followed by the same history issue record. This newer committed
line is also merged into the synchronization. Its desktop core regression
suite is compiled and run on the integrated source (24/24 PASS).

Direct SSH inspection of the Mac found:

| Checkout / line | Committed head at inspection | Work outside the commit |
| --- | --- | --- |
| Primary, `feature/usage-history` | `a0e4dc4` | Conversation experiment revisions, harness changes, cases and tests |
| R7, `claude/client-five-entries` | `aba494e` | R6 speech-deadline / decision / proactive-wake candidate and regression evidence |
| Account/relay worktree, detached | `aba494e` | Desktop 0.1.16 work overlapping Windows `080d0f4`, plus newer Android 0.33.4 and update/connection work; this is a mixed worktree, not a clean release |

The Mac primary line contributes two committed changes absent from Windows:
`6349ba3` (dialogue-naturalness design, serial/speculative experiment plan and
offline harness) and `a0e4dc4` (separate model-call cost from the experiment
decision rule). They are merged with the Windows baseline, retaining both
histories. References in the imported design are updated for the R7 directory
layout. Experiment execution remains separate from the production Runtime.

Dirty Mac work is retained in place and excluded from this synchronization.
Its contents must be compared with the integrated baseline before a later
commit: committing the account worktree wholesale would repeat already
integrated changes and mix them with newer unfinished Android work. Private
experiment results, conversations, credentials, caches and installers are not
uploaded. Windows' two pre-existing untracked Python cache files are preserved
and excluded.

## Progress and remaining acceptance

| Track | Implemented / recorded | Still required |
| --- | --- | --- |
| Runtime R1-R5 | Intake/provenance, journal and recovery, ownership, bounded snapshot/delta sync, host/device credentials and reply presentation | Real multi-computer faults, recovery, authority and client convergence across the supported paths |
| R6 | Execution/speech/progress separation, bounded context, deadlines, callback fencing and lifecycle work | **First meaningful complete bubble <=3s remains FAIL in retained real-provider evidence**. Default-flow cutover stays gated; Mac candidate changes are not deployed |
| R7 | Six layer packages and import-cycle cleanup committed | Continue enforcing dependency tests as behavior changes |
| R8 | Negotiated presentation/request sync and client migration are partially implemented | Finish client acceptance before removing old endpoints/adapters or enforcing new minimum versions |
| Desktop accounts and relay | 0.1.16 source committed and packages exercised locally; peer-sync restart fix `3f1b905` included and core tests pass | Installed acceptance of the reconnect fix; public routing with Tailscale off; real network interruption/recovery and history convergence |
| Shared history H1-H4 | Problems recorded in `12bbd86` | Correct answering-computer identity; make replicated desktop history available to Xiaoyu's recall; present one coherent history; exclude automation noise from conversational recall |
| Conversation style | Serial/speculative plan and harness committed | Mac has later uncommitted experiment results/design changes. A private 30-case companion blind-review handoff records v3 16 wins, R7 9, 5 ties (p about 0.23, not significant); no production prompt change follows from that result alone |

Read the [refactor acceptance record](runtime-refactor-design.md),
[history and interaction issues](app-runtime-simplification.md),
[dialogue design](dialogue-naturalness-design.md), and
[experiment plan](decide-speak-experiment.md) before resuming those tracks.
The imported [experiment handoff](decide-speak-handoff.md) records the original
handoff; later private results and dirty Mac revisions are not represented by
its older completion claims.

## Branch and GitHub navigation

### Published artifacts and source do not yet match

[App 0.33.4 (73)](https://github.com/yanyuliu01/ai-passport/releases/tag/app-0.33.4-73)
has separately uploaded, versioned APK assets. However, both its tag and
[`companion-build-80`](https://github.com/yanyuliu01/ai-passport/releases/tag/companion-build-80)
resolve to **`66e6ef7`**, whose Android version configuration is **0.31.2 (59)**. The successful
tag-triggered [Companion app run](https://github.com/yanyuliu01/ai-passport/actions/runs/40479262721)
therefore verifies that older committed source, not the Mac's dirty 0.33.4
source. The auto-generated `pocket-hub-debug.apk`, firmware asset and GitHub
Source code archives must not be treated as matching the separately uploaded
0.33.4 APKs merely because they share a release/tag trigger.

This inspection verifies release metadata, Git refs and source version fields;
it does not establish APK contents, reproducibility or phone installation.
Retain existing tags/assets and update their explanatory notes. Reconcile and
commit the exact Mac source, then make a future uniquely versioned, tested
publication from that commit. Moving an existing tag or replacing its binaries
would conceal the mismatch and may affect installed update clients.

| Branch / PR | Role |
| --- | --- |
| `feature/usage-history` | Default integration line for the latest committed product source and this status index |
| `claude/client-five-entries` | Refactor/client development line; its remote head `aba494e` is included in the integration line |
| `runtime/r4-recovery-review` | Recovery review line; `9d6cc37` is included through merge `28f3244` |
| `feature/multi-end-sync` | Earlier mesh/client milestone `5631087`, included in the integration line |
| `feature/usage-effort`, `feature/xiaoyu-agent-router`, `feature/ble-firmware-update`, `feature/pocket-hub-app`, `feature/claude-pocket` | Historical development milestones; names and version descriptions are not current release indicators |
| `main` | Upstream hardware-test baseline at `33d3d1d`, not the current product/default branch |
| [PR #1](https://github.com/yanyuliu01/ai-passport/pull/1) | Historical open draft targeting `feature/pocket-hub-app`; its Runtime 0.4.1 / App 0.5.0 description is not the latest project status |

No branch/tag deletion, history rewriting, default-branch rename or release is
part of this synchronization. When resuming on a dirty checkout, inspect and
reconcile its work first; do not reset, clean or pull over it. Prefer an isolated
worktree for source review. Source synchronization does not restart a Runtime,
replace an installed client, publish an APK or flash a device.

## Validation of this synchronization

| Check | Result | Evidence / limits |
| --- | --- | --- |
| Repository checks | PASS | `python tools/check_repo.py`; bilingual files, links, secret patterns and repository rules checked |
| Offline experiment harness | PASS | `run --dry-run --repeats 1 --pause 0`: 46 cases, both arms, 92 distinct case/arm/repeat records; no live model or real action |
| Desktop compilation / core regression | PASS | `npm run build:main`; `node --test dist-main/test/core.test.js`: 24/24, including the same-endpoint peer-sync restart case |
| Complete gate / aggregate host tests | FAIL | `bash tools/validate.sh` ran repository, workflow, C host and many Runtime checks, then stopped in `test_helper_model_preferences` discovery: the inherited `test_helpers.HttpTests.test_add_list_and_remove_over_http_and_across_a_restart` timed out and cleanup hit Windows file locking. Its five-test suite passed separately on both Windows baseline and integrated source. The initial full-gate failure is retained; later suites were not reached |
| Build | NOT RUN | Firmware stage was not reached; no APK, installer or new firmware package was built. Desktop TypeScript compilation is reported separately |
| Device tests | NOT RUN | No phone/device acceptance or flash in this synchronization |
| Regression / first response / recovery / multi-end | Partial / FAIL in retained evidence / NOT RUN / NOT RUN | Source regression above does not establish installed behavior. Live health was inspected on Runtime 0.24.0; no new client-render timing or real network acceptance was performed |

Firmware-update interop was skipped in the complete-gate run because the shell
did not have a JDK. The full-gate Runtime code is unchanged by the source merge;
the focused recheck passing does not erase the timeout or establish its cause.
Unverified: remaining full-gate suites, firmware build, packaged desktop/Android
tests, installed peer reconnect, phone/BLE, first-response compliance, public
network recovery, history convergence and exact 0.33.4 source/artifact identity.

## Client validation follow-up (2026-10-28)

Reviewed CC's October 27 work against integrated commit `b8b0886` in a separate
checkout. Desktop 0.1.16, R7 and the negotiated R8 client migration are already
in that baseline; the earlier instruction to merge them is no longer pending.
The active account/connection redesign and R6 candidates in other working
directories are outside this increment. No Runtime implementation, prompt,
configuration, client version or installed program was changed.

Two desktop test issues were addressed: owned-service fixtures now use a
temporary script and wait for the PID record before signalling the child;
interop defaults to current Python source and requires an explicit bundle or
executable for packaged validation. A stale ignored bundle can no longer change
what the default source test exercises. The desktop guide documents both modes.

Validation on macOS: desktop **111/111** before and after the test changes,
plus **5/5** lifecycle cases with a temporary path containing spaces and Chinese
characters; main/UI builds, Android **220/220**
and all five ABI/universal debug APK outputs passed. A deliberately invalid
ignored bundle did not affect the default interop test; selecting that bundle
explicitly failed with the expected platform error. Interop uses isolated local
Runtimes and an echo provider, not real-model rendered latency. Windows-native
fixture validation and packaged/installed-client acceptance remain NOT RUN.
No APK was published: Android source was unchanged, and these 0.33.2 artifacts
are validation outputs, not an update over the active account redesign.

R8 removal is still pending installed-client migration, compatibility/version
gates and acceptance. R6's retained first-response failure remains open. Real
phone/public-network recovery, three-second first response and multi-end timing
are not established by these builds. Test cleanup does not close those gates.

The final complete `./tools/validate.sh` gate passed, including repository,
workflow, C/Runtime host checks, firmware-update interoperability, ESP-IDF
5.5.3 build, merged-image and partition verification. An initial run stopped
because IDF's Python lacked Pillow; the final run used existing host Python
3.9.6/Pillow 11.3.0 for host checks and retained IDF's own build interpreter.
JDK 17.0.20.1 was selected. No dependency pins were changed.
The separate archive verification also passed. Validation-only firmware bundle:
`build/firmware/3c075ba621b909c8d6bab2ee9f4ccf163f84df53856409b231a1ce90f3adac53/`;
full-image SHA-256 `3c075ba621b909c8d6bab2ee9f4ccf163f84df53856409b231a1ce90f3adac53`,
matching ELF SHA-256 `44c71e818833d33057fe571df68f2a166e1f8bf9868cb7da9019e5d7ebc054fe`.
Firmware was unchanged and not flashed. Build: PASS; Host tests: PASS; Device
tests: NOT RUN. Windows-native fixture execution and installed-client,
real-model first-response/recovery/synchronization acceptance remain unverified.
The initial six-file follow-up is uncommitted in the attached `client-refactor-finish`
worktree; no commit, push or release was performed.

## Packaging follow-up (2026-10-28)

Continued in the same isolated worktree, leaving the active account, connection,
helper-management and quota changes elsewhere untouched. The next reproducible
issue was in desktop packaging: `XIAOYOU_RUNTIME_BUNDLE` could approve a fresh
bundle while `extraResources` still copied another, stale directory. Three of
the first six packaging regression cases failed on the old hook, including
that exact false approval.

The hook now validates the directory actually mapped to `runtime` in the global
and platform-specific packaging configuration. An optional shell override must
resolve to that same directory. Missing, duplicate, nested or filtered Runtime
mappings fail before replacing build provenance. Native and preview paths keep
their existing configured directories; the preview command no longer requires
a second, matching shell variable. No Runtime implementation, protocol, client
UI or version was changed. Recovery is a build-tool revert/rebuild; installed
state and pairing formats are unchanged.

Desktop source tests pass **119/119**, including eight packaging checks. The
Mac arm64 directory package was built from desktop **0.1.16**, Electron
**44.9.3** and freshly frozen Runtime **0.24.0** (Python 3.9.6, PyInstaller
6.22.3, Pillow 11.3.0). Its embedded Runtime manifest matches the generated
`build-info.json`; all **104** regular Runtime files match the build directory.
The Runtime source SHA-256 is
`a3464ab9af0a918e590c28617a2aee3de7cb1997a1b80709b574bef51c0f522e`.
Two isolated processes launched from the packaged Runtime passed encrypted
mesh, remote continuation and relay recovery using echo. This does not measure
real-model TTFT or installed-client recovery.

The private unsigned candidate is under
`companion/desktop/release/mac-arm64/`; it was not installed or published and
should not replace the active account redesign. Windows-native packaging and
installation remain NOT RUN. Android was not changed in this increment.

The complete gate was rerun after this packaging fix and passed, including the
firmware build/layout checks; separate archive verification passed. Final
validation-only image SHA-256:
`ffa80e5c2cc5b49e72ec7a9b3de35d6bcae02e89ced6d83233b1ca3236827ce3`;
matching ELF SHA-256:
`e0ac1da47f09a7bda901bcb6737fcb4547f12a260a87283bd7556e858b66ea0b`.
The bundle is under `build/firmware/<image-sha256>/`. No firmware was changed
or flashed. Build: PASS; Host tests: PASS; Device tests: NOT RUN.
Local packaged provenance evidence is in
`build/client-refactor-validation/packaging.json`.

At the final overlap check, the account worktree already contained the same
`runtime-host.test.ts` and `runtime-interop.test.ts` fixes from the first
increment. Keep those identical changes once when integrating. The packaging
hook/test follow-up remains isolated here, uncommitted and unpushed.
