<p align="right">
  <a href="cross-stack-reliability.zh_CN.md">简体中文</a> · <strong>English</strong>
</p>

# Mandatory App, Runtime, and Firmware Development Rules

## Scope and authority

Every agent changing Pocket Hub (`companion/`), Xiaoyou Runtime (`runtime/`), or
firmware (`main/`, `components/`, and firmware configuration/tools) must read
this document before editing. It also applies to changes in prompts, models,
dependencies, persistence, routing, permissions, or deployment that affect these
projects. [Root AGENTS.md](../../AGENTS.md) remains the instruction entry point;
directory `AGENTS.md` files only route here and to the relevant project guide.

Three requirements apply to every change, including changes described as UI,
cleanup, optimization, or a new feature:

1. Preserve existing working user journeys.
2. Render the first meaningful assistant response within **3,000 ms** of the
   user's send action, across every supported input and connected client path.
3. Keep the same shared conversation and task consistent across participating
   clients, including after interruption and recovery.

These are acceptance requirements, **not a claim that the current implementation
already meets them**. An existing defect is a baseline failure to record and
investigate; it is not permission to weaken a gate. Only the owner's explicit
instruction can change product scope or these requirements. A missing device or
test dependency means NOT RUN, never PASS.

## Preserve the working baseline

Before changing behavior:

- Read retained shared history as directed by root `AGENTS.md`, then verify it
  against current code, running versions, and reproducible results. Record dirty
  files; preserve other agents' work and coordinate edits to shared files.
- Identify affected user journeys and their adjacent dependencies. Record the
  App version, Runtime revision, firmware identity, transport, and configuration
  variant without secrets. Distinguish source changes from installed versions.
- Run the smallest relevant existing checks before editing. For a bug fix,
  reproduce the failure and add a regression check at the responsible state,
  protocol, or lifecycle boundary. A device-only failure needs repeatable device
  acceptance steps as well as any host-testable coverage.
- Write down the behavior to preserve, the intended change, compatibility with
  installed peers, acceptance checks, and a safe rollback/recovery path.

After editing, repeat affected and adjacent journeys using the same baseline.
Do not delete features, weaken assertions, extend deadlines, hide errors, clear
history/settings, or remove memory, approval, safety, or task orchestration to
make the new path appear faster or green. Change the smallest responsible layer;
avoid unrelated rewrites. Record pre-existing failures separately from new ones.

Wire formats, HTTP/SSE events, BLE messages, IDs, status values, and stored data
are compatibility contracts. Prefer additive changes with capability/version
negotiation, safe handling of unknown fields, and explicit migration. Test new
App with installed Runtime/firmware and old supported App with new Runtime or
firmware. Do not make simultaneous upgrades of all three an implicit requirement.
Preserve pairing, permissions, message history, pending requests, approval
routing, and update/recovery behavior through upgrades and rollback.

## The 3-second first-response contract

### What to measure

`TTFT = first meaningful assistant text rendered - user send action`.
Use monotonic elapsed time on the originating client. For text, start when the
user presses Send; for phone or device voice, start at recording release or the
accepted end-of-speech event. Voice upload, BLE transfer, transcription,
connection establishment, retries, queues, locks, context preparation, model
startup, parsing, forwarding, and UI scheduling all count. Also verify first
render on other active participating displays; API time alone is insufficient.
Do not move the start to server acceptance or transcription completion, or reset
it on reconnect, retry, or fallback.

Only nonempty assistant body text that responds meaningfully to this turn
counts. Typing animations, transport heartbeats, reasoning-only events, HTTP
acknowledgements, canned acknowledgements, task cards, and error messages do not
count. Long tool/helper work must allow a genuine initial model response and
then continue asynchronously; neither initial text nor task creation proves
the requested work has finished. A task whose meaningful response depends on a
slow tool remains subject to this deadline; do not fabricate results.

The requirement is **every successful sample <= 3,000 ms**, not an average or a
percentile target. Include cold starts, long histories, parallel tasks,
foreground return, source switches, retries, direct/remote paths, and supported
fallbacks. Never silently exclude slow samples or timeouts from the report.

### Implementation constraints

- Propagate one request/turn ID across client, Runtime, provider, and firmware
  diagnostics. Record elapsed stages (capture/ASR, connect/upload, queue/context,
  provider first body, stream forwarding, first render) and the version tuple.
  Do not subtract unrelated machines' wall clocks or log private message/audio
  content or credentials to obtain timing evidence.
- Allocate the 3,000 ms budget across all stages before adding work to the
  critical path. Stream meaningful deltas immediately; do not buffer to a full
  sentence or completed response. Bound growing history/context and expensive
  discovery without silently dropping required memory or permissions.
- Keep conversation, feed/sync, cancellation, approval, and health handling
  responsive while tools, transcription, downloads, updates, or models run.
  A single busy worker or shared lock must not serialize unrelated journeys.
  Measure queue time and overload, including same-conversation ordering.
- Enforce first-body, idle, and total deadlines with a timer/cancellation path
  that works even with no incoming events or endless heartbeat comments. An
  HTTP connection or healthy heartbeat does not prove a responding model.
- Retries and approved fallbacks share the original deadline. Do not reset the
  timer per attempt, duplicate a turn, silently switch data destinations/models,
  or strip required behavior to meet it. Cancel superseded work and bound resource
  use; define whether an accepted slow turn continues or stops, and reconcile its
  final state on every client.

### Unavailable dependencies

Network loss, a powered-off Runtime, or a stalled provider cannot guarantee real
model text in three seconds. Within **3,000 ms**, show a truthful unavailable,
queued, or delayed state and a recovery/cancel action; preserve the request and
do not report it as answered. If acceptance is unknown, say so and reconcile
before resending. Such a sample **misses the first-response requirement**;
bounded failure feedback is a separate recovery check, not a TTFT pass.
Do not advertise an unconditional guarantee until the required paths are measured
and the dependencies/fallbacks can sustain it. Record the remaining gap and
continue in-scope remediation rather than concealing it.

## Connection and recovery contracts

- Model disconnected, connecting, authenticated/ready, reconnecting, stale, and
  failed states explicitly. Show the actual selected Runtime/device and cache
  age where relevant. Health, authorization, feed readiness, and model readiness
  are separate facts; one success must not erase another failure.
- Use bounded timeouts and backoff. Recover after network switching, sleep/wake,
  foreground return, App/Runtime restart, BLE reconnect, and stream interruption.
  Re-authenticate and resubscribe, then reconcile missed state; do not require
  the user to clear data or re-pair for routine transport failures.
- Keep the same logical turn ID and payload across transport retries. Retry
  writes only with an explicit idempotency contract that survives the relevant
  failure/restart window. After a lost acknowledgement or interrupted stream,
  query/resume the accepted turn rather than submitting another one.
- Bind callbacks, pending requests, approvals, cursors, and credentials to the
  originating Runtime, session, device, and connection generation. Late events
  from an old connection must not update the newly selected source or approve
  another task. Permission denials, invalid input, and authentication failures
  must not become endless network retries.
- Cancel/close obsolete requests, watchers, timers, audio sessions, and device
  workers. Keep Android UI and firmware button/LVGL callbacks non-blocking;
  retain the root guide's LVGL locking, memory, and teardown rules.

## Multi-end message consistency contracts

For the **same authorized shared conversation**, App, Runtime, and device views
must agree on message identity, order, content within each view's declared
window, task target, and status. Desktop sessions participating in that shared
conversation follow the same rule; unrelated/private desktop conversations are
not merged by matching text. Runtime owns the shared conversation's durable
record; imported or observed desktop sessions retain explicit source identity
and supported read/write boundaries.
The data structure, ids and sync interface are defined in the
[unified message model](unified-message-model.md).

- Carry stable source/conversation, turn, and message identities, plus a
  monotonic event sequence/revision. Equal text in different turns remains two
  messages. Deduplicate delivery by identity, never by content or timestamp alone.
- Persist the user message, pending work intent, and retry identity before
  acknowledging durable acceptance. Persist a final reply/terminal state before
  acknowledging or broadcasting completion. Distinguish local pending from
  Runtime-accepted, streaming, completed, failed, and cancelled states.
  A crash/restart must not silently discard accepted work or execute it twice.
- Merge local optimistic messages, stream deltas, feed updates, and history
  snapshots into the same logical message. Ignore duplicate/older revisions;
  a stale partial or snapshot cannot overwrite a newer final/cancelled state.
  Define terminal transitions and deterministic order with concurrent senders.
  Preserve the wire format's delta-versus-snapshot semantics: current HTTP
  `partial` events replace the complete partial text, including a newer empty
  snapshot that retracts it. Do not append snapshots or discard valid retractions.
- Use replay cursors and a snapshot/reconciliation path for gaps, expired
  retention, sequence reset, and Runtime restart. Establish snapshot plus live
  subscription without a loss window. Persist the applied cursor with applied
  state; advance it only after successful application. A gap must never be
  silently treated as up to date.
- Online active clients must reflect a committed message/status delta within
  **3,000 ms**; this does not replace the send-to-first-render deadline. On
  authenticated reconnection, reconcile the current conversation/latest state
  within **3,000 ms** of transport readiness, and make any older-history backfill
  explicit. Offline clients retain pending state and converge after recovery;
  immediate remote delivery while disconnected cannot be claimed.
- Slow consumers, pagination, history pruning, and task deletion must not erase
  retained chat history or block other clients. Preserve conversation/task/pin
  routing, background completion messages, approvals, and terminal states.
  Make intentional deletion a scoped, explicit operation with a defined contract.
- Firmware may display a bounded recent window/excerpt instead of a full archive;
  label truncation/cached/offline states truthfully. All messages inside that
  window must agree with the authoritative record. Memory limits never justify
  inventing success, losing an accepted request, or restoring stale final text.

## Required regression and fault matrix

For behavior changes, run affected rows and adjacent journeys before and after
the change. For chat, transport, synchronization, lifecycle, model/prompt, or
protocol changes, run **all applicable rows**. Mark a row inapplicable only with
a concrete scope reason; an unavailable device is NOT RUN. Use deterministic
host/integration tests for ordering, deduplication, timers, and fault injection,
plus real App/Runtime/device checks for rendered timing and transport recovery.

| Scenario | Required evidence |
| --- | --- |
| Text, phone voice, device voice | Correct target and one reply; timing includes ASR/forwarding; repeated record/release/cancel works |
| Warm and cold start; long history | First visible body <= 3,000 ms; no lost memory, permissions, or history |
| Slow tools, busy workers, simultaneous sends | Chat remains responsive; deterministic per-conversation order; no duplicate side effects; approval/cancel/feed remain usable |
| Normal stream, empty stream, heartbeat-only, stalled provider | Correct first-body/idle/total deadlines; no fake TTFT pass; all views reach a truthful state |
| Disconnect before send, lost acceptance, mid-stream drop | Pending/accepted state distinguished; same request resumes without duplicate messages/work |
| Direct/remote access, network switch, BLE reconnect | Explicit source/link status; bounded retry; missed messages recovered |
| Background/foreground, source switch, App/Runtime/device restart | Durable history/pending recovery; late callbacks do not contaminate another source |
| Two live clients and a returning offline client | Delta/final convergence, stable order, one bubble per message; recorded propagation/recovery times |
| Duplicate/reordered events; snapshot/stream race; empty partial retraction; cursor reset | Newest revision wins; correct replace/delta semantics; no missing turn, repeated text collapse, or final-to-partial regression |
| Paging/pruning, device window limits, large reply | No loss of retained chat; truthful excerpts; current window and final state agree |
| Approvals, helper follow-up, background completion, cancellation | Correct originating task/Runtime and preserved orchestration; status agrees on all views |
| Mixed supported versions, upgrade/rollback, update interruption | Contract compatibility, stored data/pairing preserved, update/recovery still usable |

For each affected successful input/transport path, collect at least 20 warm
sends and three repetitions of each applicable state transition/fault scenario.
Record attempt count, successes, misses/timeouts, p50, p95, and maximum TTFT,
plus propagation/recovery times. Every successful TTFT sample must meet 3,000 ms;
percentiles alone cannot pass the gate. Repeat the acceptance smoke test against
the installed candidate versions, not only a mocked provider or source checkout.

## Validation and delivery

Use existing project commands rather than inventing a replacement pipeline:

```bash
# Repository root; activate ESP-IDF 5.5.3 for the complete gate.
./tools/validate.sh --static
./tools/validate.sh

# Runtime changes: run from runtime/.
python3 -m unittest discover -s tests

# Android changes: run from companion/android/ with JDK 17 and Android SDK.
gradle testDebugUnitTest assembleDebug
```

Repository checks, host tests, builds, and mock streaming tests do not establish
real end-to-end latency or device convergence. Add missing executable coverage
at the responsible layer when implementing a reliability change; do not describe
this document or the existing static gate as an implemented live SLA monitor.
For a documentation-only change, report document/static checks and say runtime
acceptance was not exercised; it does not require an App release or flashing.

Keep the existing [App shipping flow](../../companion/README.md#shipping-an-app-change-developers-and-ai-agents),
[Runtime restart rules](../../runtime/README.md), and
[firmware test handoff](ai-guide.md#offer-on-device-testing).
Build/publish, installed-version verification, and device acceptance are separate
events. Restarting an active Runtime or flashing still needs the authorization
specified there. Preserve a known compatible recovery artifact and migration
path; rollback must account for any persisted format changes.

Every behavior-change delivery must include:

```text
Versions/configuration: App / Runtime / firmware / transport (no secrets)
Regression: PASS / FAIL / NOT RUN; baseline, affected and adjacent journeys
First response <= 3s: PASS / FAIL / NOT RUN; samples, p50/p95/max, misses
Connection recovery: PASS / FAIL / NOT RUN; tested failure paths and times
Multi-end sync: PASS / FAIL / NOT RUN; client set, scope, convergence evidence
Build: PASS / FAIL / NOT RUN
Host tests: PASS / FAIL / NOT RUN
Device tests: PASS / FAIL / NOT RUN
Unverified: missing scenarios, installed-version checks, dependency gaps
Recovery: compatible rollback artifact/data path and any required authorization
```

A failed required check blocks a claim of full acceptance; missing required
evidence stays NOT RUN and unverified. Preserve and deliver reviewable work with
its gaps, but do not silently publish or deploy a known regression as ready.
Tell the owner the concrete impact and recovery/remediation step. Do not ask
the owner to repeat routine checks the agent can run itself.
