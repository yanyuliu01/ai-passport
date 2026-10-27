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
| Shared history H1-H4 | Problems recorded in `12bbd86` | Correct answering-computer identity; make replicated desktop history available to Xiaoyou's recall; present one coherent history; exclude automation noise from conversational recall |
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
| `feature/usage-effort`, `feature/xiaoyou-agent-router`, `feature/ble-firmware-update`, `feature/pocket-hub-app`, `feature/claude-pocket` | Historical development milestones; names and version descriptions are not current release indicators |
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
