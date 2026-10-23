<p align="right"><a href="runtime-refactor-design.zh_CN.md">简体中文</a> · <strong>English</strong></p>

# Runtime three-layer refactor design

Status: design approved; implementation contracts clarified on 2026-10-23. R1 (`2bf81bf`) and R2
(`1896e6d`-`0688c71`) are committed, see section 7; R3-R8 are not implemented. This is source state,
not deployment.

This document maps the [Xiaoyou complete plan](xiaoyou-agent-model.md) onto the Runtime code in
three layers: the **intake layer** (how things come in and how they are stored), the **reply layer**
(how replies are produced and delivered to every end) and the **agent loop** (how Xiaoyou decides
and acts). Issue ids refer to the [interaction simplification issue list](app-runtime-simplification.md).
Every phase must meet the [cross-stack reliability rules](cross-stack-reliability.md).

## 0. Overview

```
             +------------- 1. Intake layer ---------------+
adapters ---> Intake.accept(event) ---> event log ---> current state (projection)
 (HTTP/helpers/desktop sessions/              |                |
  other computers/timers/approvals)           |                v
                                              |      +--- 3. Agent loop ---+
                                              |      | wake -> decide ->   |
                                              |      | record decision ->  |
                                              |      | apply/queue -> speak |
                                              |      +---------+-----------+
                                              |                | results are events
                                              v                v
                                   +------ 2. Reply layer ------+
                                   | snapshot + delta /v1/sync | ---> phone / desktop / device
                                   | delivery rules            |
                                   +---------------------------+
```

The design borrows parts of Raft and DS-Bot-App; see section 8.

Three rules hold throughout:

1. **Only Intake writes the event log.** Other modules read the current state and change it only
   by submitting events.
2. **Store before acting.** Every input, decision and result is persisted as an event before
   anything follows from it. Replay rebuilds state without executing effects; recovery resumes pending work and reconciles uncertain outcomes under section 1.5.
3. **Clients see one projection.** Messages, tasks, conversations, quota, approvals and what
   Xiaoyou is doing all arrive through `/v1/sync`.

## 1. Intake layer

### 1.1 Target structure

```
Adapters (read the outside world, emit events only)
  HttpAdapter      owner messages, voice, files, write actions (/v1/messages, /v1/files, /v1/actions)
  HelperAdapter    helper progress, results, endings (replaces Tasks writing cards directly)
  DesktopAdapter   observed Codex / Claude sessions on the computer (read-only, idempotent)
  MeshAdapter      records and commands from other computers
  TimerAdapter     due reminders, watch conditions
  SystemAdapter    quota, deployment, version, online state
        |
        v
Intake.accept(event) -- dedupe, validate, write the log, advance the current state, notify subscribers
        |
        +-- EventLog (the evolution of cards.log.jsonl, the only authoritative record)
        +-- World (current-state projection: requests, tasks, conversations, computers,
                   helpers, approvals, quota, focus, commitments)
```

### 1.2 Interfaces

**Event shape** (identical for every source):

| Field | Meaning |
| --- | --- |
| `id` | Stable event identity: client requests retain `client_id`, internal events use the origin Runtime and its sequence; identity is scoped as described below; an identical retry returns the original acceptance (dedupe by identity only) |
| `seq` | Local log sequence, monotonic; the sync cursor uses it |
| `at` | Original time it happened (N9) |
| `source` | `owner` / `helper` / `desktop` / `mesh` / `timer` / `system` |
| `kind` | `said`, `action`, `task_progress`, `task_ended`, `session_observed`, `approval_requested`, `approval_answered`, `assistant_message`, `usage`, `deployed`... |
| `conversation`, `subject` | Which conversation; which task / session / computer it is about |
| `actor`, `cause` | Who did it (`app`, `desktop`, `xiaoyou`, `mesh:<node>`, `cli`, `system`) and which event led to it (A4) |
| `trust` | `owner` / `system` / `helper` / `external`; `external` is material only and never becomes an owner turn |
| `payload` | Kind-specific content |

**Python interfaces** (inside the Runtime):

```python
Intake.accept(event) -> Accepted(id, seq, duplicate: bool)
World.get(kind, id) / World.list(kind, **filter)        # read-only
World.subscribe(after_seq) -> event stream               # used by the agent loop and the reply layer
```

**Identity and authorization:** client request identity is `(origin_runtime_id, authenticated_client_id, client_id)`; the receiving authority fixes the origin on first acceptance, and retries retain it. Internal events use `(origin_runtime_id, origin_seq)`. A retry with the same identity must match its original kind, conversation, target and normalized payload; otherwise return an explicit conflict (HTTP 409), without executing it or returning another conversation's content. Payload comparison validates reuse; it is not content-based deduplication. Authenticate and check scope before returning an existing result.

Mesh preserves original identity and revision; each receiver assigns a separate local `seq` for replay. Forwarding does not mint a new identity. Peer commands have their own stable identity and target authority; replicas do not execute them. Sender-provided `actor` and `trust` are not authorization: adapters derive effective privileges from the authenticated source. Persist dedupe records across restarts and log compaction for the supported retry window; expired identities require explicit reconciliation, never silent execution as a new request.

### 1.3 Rules per source

- **Owner messages:** `accept` first, then queue into the conversation; the receipt only says
  "received, id X".
- **Helpers:** progress only updates the task's progress fields and never writes the main chat;
  every ending emits `task_ended` (done / failed / cancelled) for the agent loop to finish (A1).
- **Desktop sessions:** written only into their own observed session, never into the main chat,
  never waking Xiaoyou; re-observing the same session revision emits nothing new; recap material never quotes
  recap material (N8, N11).
- **Other computers:** each record is changed only by its origin; commands from a peer (cancel,
  confirm, hand over) enter the same `accept` with `actor=mesh:<node>`.
- **Cancel, confirm and other writes:** all `kind=action`, with `actor` and a reason (A4).

### 1.4 Mapping to existing code

| Now | Later |
| --- | --- |
| `Service.submit` / `submit_voice` / `_enqueue`, `Ledger` (`messages.json`) | `HttpAdapter` + a "requests" projection in `World`; `messages.json` becomes a rebuildable snapshot |
| `record.exchange` / `reply` / `request_changed` / `task`, direct writes in `cards.py` | All become `Intake.accept`; `cards.json` becomes a projection snapshot |
| `tasks.Control.activity`, card writes in `Xiaoyou._finished` | `HelperAdapter` emits `task_progress` / `task_ended` |
| `desktop_sync._publish`, `directory_discovery`, `project_brief.recap_text` | `DesktopAdapter` (read-only, idempotent, scoped to observed sessions) |
| `mesh._apply_current` dispatching to messages / tasks / approvals / commands | `MeshAdapter`; commands go through `accept(kind=action, actor=mesh:...)` |
| `transcript.json` (Xiaoyou's own conversation record) | No longer written; the agent loop assembles context from the log on demand |
| `Service.cancel`, `Xiaoyou.cancel`, the `__main__` interrupt, mesh cancel commands | One `action:cancel` event with `actor` = `app` / `cli` / `mesh:...` / `xiaoyou` |

### 1.5 Durable acceptance and recovery

`Accepted` means the input and pending intent are durably committed, not merely queued in memory. Intake serializes validation, identity allocation and durable append; projections and notifications advance only after that commit. If the append succeeds but the response is lost, a retry returns the same acceptance. Replay is deterministic and has no external side effects. Subscribers resume from durable cursors and tolerate redelivery. Before R3, specify the log's commit boundary, partial-tail recovery, schema versioning and retention/checkpoint rules; a JSONL append alone is not a durability guarantee.

Each executable decision has a stable `operation_id`, linked to its trigger and persisted before execution. Record `pending`, `running`, `succeeded`, `failed`, `cancelled`, or `unknown` and the external execution identity where available. Only one executor may claim an operation; a restart or expired claim does not prove the external action stopped.

| Failure boundary | Required recovery |
| --- | --- |
| Before durable acceptance | No accepted receipt; retry with the same identity |
| Accepted, before execution starts | Resume the pending intent once |
| External action may have started, result not committed | Query by execution identity or retry with the same provider-supported idempotency key; if neither is possible, mark `unknown` and reconcile before repeating |
| Result committed, delivery interrupted | Replay the stored result; do not re-execute |

A cancellation request is distinct from confirmed cancellation. Completion racing cancellation follows an explicit terminal-state transition table; late progress cannot resurrect terminal work. A confirmed terminal event wakes the originating conversation once logically, using a durable trigger/operation identity so replay cannot duplicate the announcement. Interrupting speech does not undo an accepted action: persist the interrupted reply state and explicitly cancel or retain the operation.

R3 must test every boundary with injected crashes, duplicate delivery and concurrent claims. Rebuild all promised state from the retained log/checkpoint, including pending intents and dedupe records. Validate old-data import and rollback compatibility before stopping legacy writes; deleting snapshots in tests must never delete the only copy of history.

## 2. Reply layer

### 2.1 Target structure

```
assistant_message events (every segment Xiaoyou says: blocks, formatting, status streaming/final, revision)
        |
        v
Projection (the single display projection: evolution of presentation_v1, with formatting runs)
        |
        +-- /v1/sync: snapshot + delta, the only downstream channel for every end
        +-- delivery rules: nearby -> device; foreground -> originating chat;
        |                   otherwise push only confirmations and due reminders
        +-- other computers: the same events replicated through the mesh
```

### 2.2 Interfaces

**Sync:**

```
GET /v1/sync?cursor=<cursor>&wait=<seconds>
  -> { epoch, cursor, more, snapshot?, events[] }
```

- **An empty or invalid cursor gets a snapshot, not a replay of history.** The snapshot holds the
  current state only: the latest N messages per conversation, all unfinished tasks, pending
  approvals, quota and Xiaoyou's state; deltas follow from the snapshot boundary. The target is to show the active conversation within
  1 second of authenticated transport readiness, subject to measurement, without catching up
  415 pages (N10). Older
  history is paged on demand (`GET /v1/conversations/{id}/history`).
- **Pages are sized by count**, no longer 48 KB; oversized items (long replies, attachments) carry a
  summary and a reference.
- **Clients persist applied state and its cursor atomically** and resume after a restart; storing the cursor alone can lose unapplied events.
- **Replies in progress** are `assistant_message` events with `status=streaming`, each replacing the
  whole text; once `final`, no older fragment may overwrite it. Clients no longer run a separate
  wait loop or stream per message (C1, C9).
- **Long polling:** `wait` returns as soon as anything changes; switching to SSE or WebSocket later
  only changes the transport, not the event format.

**Snapshot boundaries and bounds:** bind every cursor and callback to Runtime identity, epoch, authorized scope and connection generation. Freeze a snapshot at sequence S; paginate it with a stable snapshot token, retain deltas after S, and switch to live delivery only after the scoped snapshot is applied. Expired tokens or missing retained deltas explicitly restart reconciliation. Ignore callbacks from obsolete connections; tombstones prevent deleted items from returning.

The 50-message limit is per conversation, not an unbounded all-conversation response. Load the active conversation, relevant tasks and pending approvals first; page the remaining authorized state with visible loading status. Use item counts plus explicit byte/work limits and continuation tokens. Oversized content references remain fetchable; excerpts cannot silently replace authoritative text. On scope switches, reconcile that scope before treating it as current. Test large histories, snapshot/delta races, source switches, failed local commits and expired cursors. The 1-second target concerns current visible state; broader recovery and propagation must also meet the shared 3-second contract.

**Sending:**

```
POST /v1/messages { id, conversation, text | audio_ref | image_refs } -> { id, accepted: true }
POST /v1/actions  { id, type, target, reason } -> { id, accepted: true }
```

They only acknowledge receipt. Processing state, replies and results all arrive through `/v1/sync`.

**Display:** the projection supplies block boundaries, formatting runs (bold, lists, code) and
authors; every end renders the projection and never parses Markdown itself (N13).

### 2.3 Mapping to existing code

| Now | Later |
| --- | --- |
| `Service._report("partial")`, `_update`, `_snapshot`, `get(wait, rev)` | Emit `assistant_message` events; `get` kept only for old clients |
| `/v1/messages/{id}?wait`, `/v1/messages/{id}/stream`, the App's `MessageStream` | Removed (after clients migrate) |
| `Service.sync`, `cards.sync_since`, `FEED_BUDGET_BYTES = 48 KB` | Snapshot + delta; paging by count |
| `Service.feed`, `/v1/feed` | Removed (after clients migrate) |
| `presentation.py` | Kept as the only projection; formatting runs completed for places such as the home card |
| "Whether and where to speak" in `proactive.py` | Delivery rules (this layer); "whether to speak at all" stays in the agent loop |

## 3. Agent loop

### 3.1 Target structure

```
Wake rules (subscribe to World events; deterministic, cheap)
  owner said ---------------------+
  task_ended (dispatched by me) --+
  watch condition met ------------+--> step(conversation, trigger) for that conversation
  promise falls due --------------+
                             1. Build context: focus + open commitments + recent turns +
                                relevant recall + own state
                             2. Decide: one call, exactly one action
                                reply / dispatch / amend / cancel / watch / promise / remember / stay_silent
                             3. Record the decision: accept(action event) (store before acting)
                             4. Apply a bounded local action, or durably queue long work;
                                persist its actual outcome or acceptance state
                             5. Speak: one streamed call, read-only tools, grounded in that state;
                                long-work results return later as events
```

- **Replies and proactivity are the same `step`** with different triggers. A new proactive ability
  is one more wake rule.
- **A task ending always wakes her:** done, failed and cancelled each make her say one line in the
  originating chat (A1, M3, N12).
- **One turn speaks once:** no hidden nudge (already removed) and no "JSON failed, ask again" path;
  an invalid action is reported honestly.
- **Concurrency:** decisions within one conversation are serial; a new owner message while she
  speaks interrupts the current utterance (existing behaviour) and becomes the next step. Different
  conversations never block each other.
- **Which computer does the work:** chosen at `dispatch` from where the helper is, folder
  reachability, quota and who is online; a computer the owner names wins.
- **Self-inspection:** version, deployment, online state and running tasks are read from `World`;
  no helper is dispatched to find them (M1, M2).

### 3.2 Interfaces

```python
Loop.step(conversation, trigger: Event) -> None
ContextBuilder.build(conversation, trigger) -> Context
Decider.decide(context) -> Action            # decide first; on timeout use the fallback in decide-then-speak
Speaker.speak(context, action_result) -> streamed assistant_message events
Executor.apply(action, operation_id) -> ActionResult  # persisted local outcome or queue acceptance
HelperPort.run(job, control) -> progress and result events  # Claude Code, Codex, DeepSeek, remote nodes
```

`ActionResult` distinguishes completed local work, queued work, confirmed remote start, failure and unknown outcome. Queue acceptance permits "queued", not "started" or "finished". Slow external calls run in workers and never block the first reply. A later terminal event produces a separate completion turn; "one turn speaks once" does not suppress that result. Reply-only actions need no external execution.

Before enabling the two-model-call path, allocate one end-to-end 3,000 ms budget across queueing, context, decision, bounded application, generation, transport and client rendering. Measure the first meaningful complete bubble, not the HTTP receipt, first token or canned acknowledgement. Test real models and installed clients using the shared sample/fault matrix. Slow decisions or read tools share the same deadline; delayed/unavailable feedback is a recovery result, not a latency pass. Keep the supported path until the candidate passes; deleting the fallback is gated on evidence, not a planned date.

### 3.3 Mapping to existing code

| Now | Later |
| --- | --- |
| `Xiaoyou.hear`, `routing.decide` (named agent, router, default) | `Loop.step`; a named agent becomes a constraint in the context for `Decider` |
| `_converse` / `_converse_with`: tool talk, the JSON fallback, re-asking | Two stages, `Decider` + `Speaker`; the JSON fallback and re-asking are removed |
| `_converse_decided`, `decision.py` (`decide_first`, off by default) | Becomes the only path (once end-to-end time to the first meaningful complete bubble is measured and passes) |
| `_plan`, `_apply_conversation`, `_assign`, `_resume`, `_launch`, `_continue_desktop` | `Executor.apply` |
| `_finished`, `_relay`, `_close`, `cancel` | `task_ended` event + wake rule + `Speaker` |
| `lane_prompt`, `BACKGROUND_TURNS = 6`, `RECENT_FINISHED_CARDS = 5` | `ContextBuilder` (focus + commitments + relevance) |
| `proactive.Pulse` (every 20 seconds, three hard-coded reasons) | Wake rules |
| `memory.py`, `promises.py`, `recall.py`, `voice_tools.py`, `dsh_stream.py` | Kept, under `ContextBuilder`, `Executor` and `Speaker` respectively |
| Import cycles between `agents.py` and five adapter modules | The `HelperPort` interface sits below; adapters depend only on it |

## 4. Migration order

Principle: **strangler replacement**. The new layer first wraps the old code without changing
behaviour, then call sites move over one by one; each step is its own commit, verified on its own
and reversible. The steps map to the four steps in section 6 of the complete plan.

| Step | Content | Acceptance | Plan step |
| --- | --- | --- | --- |
| R1 | Add `actor` and `cause` to events (backward compatible); cancellation emits `task_ended` and Xiaoyou says one line in the originating chat | Cancels from every source record their initiator; she always says one line | Words match actions (the earlier a and b) |
| R2 | Introduce `Intake`, first as a wrapper around `Record`; move write sites to `accept` one by one (HTTP, then helpers, then desktop sessions, then other computers) | Unchanged paths pass differential tests; desktop isolation has separate corrected expectations; all write sites pass the Intake boundary check | One state |
| R3 | Introduce the `World` projection; `messages.json` and `cards.json` become rebuildable snapshots; stop writing `transcript.json` | Snapshot rebuild and crash-boundary tests pass; pending work resumes, uncertain effects are reconciled, completed work is not repeated | One state |
| R4 | Sync becomes snapshot + delta with paging by count; clients persist their cursor | From authenticated transport readiness, the active conversation shows current state within 1 second; task results no longer wait ten minutes (N10) | Automatic connection |
| R5 | Replies become `assistant_message` events; the projection completes formatting runs | A new client shows streamed replies and formatting from `/v1/sync` alone (N13) | One state |
| R6 | Agent loop: measure and default to decide, then speak; remove the JSON fallback and re-asking; wake rules replace Pulse; `ContextBuilder` | First meaningful complete bubble within 3 seconds; no duplicate reply per trigger; "this" resolves | Words match actions |
| R7 | Move files into the layers: `events/`, `world/`, `delivery/`, `loop/`, `helpers/`, `api/`; remove import cycles | Move only, all tests pass | Modular cleanup (decided to follow the contracts) |
| R8 | After clients switch, remove `/v1/feed`, per-message wait and stream, and old compatibility | No callers of old endpoints; minimum versions enforced | Converge the interface |

R2 separates wrapper migration from intentional behavior corrections. Keep differential baselines for unchanged paths; test desktop isolation against explicit new expectations. Use controlled scheduling for exact-log comparisons and separate stress tests for concurrency. Do not discard `seq`/`rev` merely to pass: validate monotonicity, causal ordering, identity and terminal-state invariants even where independent interleavings are allowed. The current `Writer` wrapper is transitional, not the final `accept` event contract; finish source coverage, durable identity and event validation before R3 depends on it.

R1's cancellation subset is committed; common event metadata remains part of R2. R2-R5 are the core; R6 needs real-model measurements; R7 follows once behaviour
is stable; R8 waits for the App and desktop client to migrate.

## 5. Compatibility and risks

- **The log format only grows:** old fields stay and new fields are optional; old Apps keep working
  until R8.
- **Mixed versions across computers:** another computer may still run 0.20.0. New fields are
  ignorable for it; new capabilities are gated by `/v1/info`, and anything below the minimum is told
  to upgrade.
- **Snapshot correctness:** the snapshot and the deltas must come from the same cursor point with
  no events lost in between; validate with cursor + epoch.
- **Decide, then speak latency:** one more model call may exceed 3 seconds; measure first and use
  the documented fallback if it misses.
- **The Runtime running during the change:** source-only steps leave the running process untouched. After a persisted-format migration,
  rollback needs a compatible binary/data path; checking out older code is insufficient. Deploying
  (restarting) follows the owner's authorization.

## 6. Decided (2026-10-23)

- The snapshot carries the latest 50 messages per conversation.
- R4 keeps long polling for now; a push stream waits until the event format is stable.
- R7 uses the six directory names above: `events/`, `world/`, `delivery/`, `loop/`, `helpers/`, `api/`.

## 7. Implementation handoff (2026-10-23)

R2 is done (2026-10-23; written by Codex, split, checked and committed by Claude Code). Not pushed;
the running Runtime was not restarted.

| Commit | Content |
| --- | --- |
| `1896e6d` | Intake shell and provenance; the differential log test `test_intake` with its golden `intake_golden.json` (produced by the pre-change code) |
| `1b45f23` | HTTP / Service writes go through Intake |
| `ef4d532` | Helper, Xiaoyou and timer writes go through Intake |
| `cb8d1e4` | Desktop observation may only write observed sessions; recap context is tagged and filtered on import (N8, N11) |
| `0688c71` | Writes from other computers go through Intake; a boundary test forbids writing Record directly |

- Each commit ran the full Runtime suite on its own; the last ran 696 tests, and the full gate
  (including the firmware build) passed. The differential test passed 12 times under four-way
  concurrency.
- The differential test controls only the ordering of helper wrap-up, summary and metadata inside
  the test, and keeps every sequence and revision assertion.
- The transitional Writer is not yet the durable event API of R3; `Accepted`, `operation_id` and
  `applied_seq` come with R3.
- Next: R3 (the `World` projection, rebuildable snapshots, crash-boundary tests), making `Intake` /
  `Loop.step` deterministic and replayable as in section 8.
- Build / device / real-model latency, reconnection and multi-end acceptance: NOT RUN. Not deployed.

**R3 batches (decided 2026-10-23):** the unified log turned out to hold only messages and activity;
task state itself (who works on it, state, execution records and so on) lives only in `cards.json`,
which is rewritten whole on every change (about 750 KB). R3 therefore fills the log first and changes
startup second:

| Batch | Content |
| --- | --- |
| R3.1 | Every task change is appended as a delta to an append-only task journal `cards.tasks.jsonl` (card removals too); `world.py` rebuilds state with pure functions; seeded random operations plus truncation to simulate crashes prove "rebuilt from the journal = current state". `cards.json` stays authoritative at startup |
| R3.2 | Startup becomes "snapshot + replay of the journal after it"; `cards.json` becomes a periodic snapshot instead of a full rewrite on every change; the journal is compacted after a snapshot is durable; crash-boundary tests (journal written but snapshot not, half-written snapshot and so on) |
| R3.3 | The same for Service requests (`messages.json`); `operation_id` with `pending` / `running` / `succeeded` / `failed` / `cancelled` / `unknown`, recovered at startup as in section 1.5 |
| R3.4 | Stopping writes to `transcript.json` moves to R6, where `ContextBuilder` assembles context from the log |

## 8. Lessons from Raft and DS-Bot-App

The owner asked to learn from the design logic of two open-source projects without copying them.
The tables list only what lands in this design, and what we deliberately do not take.

### 8.1 Raft (following the design of etcd-io/raft)

Raft solves "several machines agree on one log". Xiaoyou needs no leader election or quorum: each
record already has a single writer, its origin. What is worth learning is how cleanly it handles
**the parts that are hard to communicate**.

| What Raft does | How we use it | Step |
| --- | --- | --- |
| The core algorithm is a deterministic state machine; the application owns network and disk. Inputs are messages or clock ticks; outputs are "entries to persist + messages to send + new state" (the Ready loop) | `Loop.step` and `Intake` become pure logic as well: given the current state and one event, they return events to persist, actions to run and replies to deliver; an outer layer does the I/O. Time is injected as an input. Differential tests can then replay in a fixed order instead of depending on thread timing (the root of the unstable concurrent R2 runs) | R2 wrap-up, R3, R6 |
| Persist before sending; committed and applied are tracked separately | Already "store before acting". Add two explicit cursors: `accepted_seq` (durably accepted) and `applied_seq` (advanced into state); recovery replays from after `applied_seq` | R3 |
| Replication carries the previous entry's index/term as a consistency check; a mismatch backs off or switches to a snapshot (InstallSnapshot) | Sync between computers, and between clients and the Runtime, carries "previous origin sequence + epoch" on every batch; a mismatch is a gap and gets a snapshot instead of a replay from zero. The epoch plays the role of the term | R4, mesh |
| Snapshots + log compaction; a node far behind receives a snapshot | Same as section 2, snapshot + delta; the snapshot is pinned at sequence S and deltas after S are kept until the client catches up | R4 |
| Flow control: a per-message size limit and a cap on in-flight messages; a lagging follower is probed first and pipelined once it catches up | Sync pages have both a count and a size limit; a lagging client is served in snapshot mode and switches to delta mode once caught up; one in-flight long poll per connection | R4 |
| A proposal is not guaranteed to commit; the client retries with the same request | The decided dedupe by request id; client retries must carry the original id | R2 (exists) |
| Read-only requests first confirm that the node is still authoritative (ReadIndex / lease) | When Xiaoyou answers about current state she reads the record's origin; a replica is labelled with its freshness and stale data is confirmed with the origin (M1) | R3, R6 |

**Not taken:** leader election, quorum, membership changes, linearizable reads and writes. A home has
two or three computers, so a quorum means nothing, and chat does not need linearizability; a single
writer per origin is enough.

### 8.2 DS-Bot-App (multi-end communication)

It is "one host at the centre, with phone and desktop clients connecting to it", which matches our
"each record is owned by its origin, the phone holds no authoritative state". What is worth learning
is how effortless yet conservative it makes **pairing, identity and disconnection**.

| What DS-Bot-App does | How we use it | Step |
| --- | --- | --- |
| QR pairing: the code carries the host's public-key fingerprint as the trust anchor; self-signed certificate with fingerprint pinning, TLS verification never disabled, no plaintext fallback; the host approves once and later reconnects need no approval | Matches the existing encrypted relay; extend it to every route: every route checks the same identity fingerprint, and plaintext direct connections go away | R4 / automatic connection |
| A changed host identity requires re-pairing and a new key is never accepted silently; certificate renewal keeps the private key, so renewal needs no re-pairing; a corrupted private key makes the host refuse to start rather than quietly generate a new identity | Adopt as rules: a changed Runtime identity means "pair again" (one of the three prompts); renewal never bothers the user; a corrupted identity file is an error, never an automatic new identity | R4 |
| The composer shows "waiting for an answer", "host running" and "network connection" as separate states | Matches our vocabulary: connection state and what Xiaoyou is doing are shown separately, never one "online" flag | R5, UI |
| Notifications: the host keeps the latest 500 notification records and the phone sees the latest 100; read state is per device and stored on the host; the background link starts from the current notification cursor, never replays old notifications, and a backlog shows only the latest 3; tapping a notification first checks the host identity | Notifications get their own cursor, separate from the sync cursor; read state lives in the Runtime (fixes S2); old notifications are not replayed after reconnecting; opening one first checks which Runtime it came from | R4, R5 (delivery rules) |
| Android background delivery uses a foreground service polling about every 10 seconds and backing off to 60 seconds without network; cloud push is optional | Background notifications do not depend on third-party push; backoff is bounded | R4 |
| A lost receipt means refreshing state, never assuming it did not run; requests carry device-scoped ids; after a disconnect the client queries the original task and never automatically redoes a non-repeatable operation | Matches `operation_id` / `unknown` in section 1.5; the App's offline queue resends with the original id and queries first when a result is unknown | R2, R3 |
| After a session switches segments, scheduled tasks stay bound to the original session and reminders are forwarded to the current segment, without copying or rebuilding the task | Commitments and watches stay bound to the conversation that created them; when due they are delivered where the owner currently is, without copying the commitment | R6 |
| Safe by default: the phone entry is off by default, moving to a new host requires re-authorization, and plugin sources are pinned to a commit with SHA-256 | When preinstalling dsh and pi (decided), pin versions and verify them; a new computer joining the mesh is approved on an existing device | Preinstall plan |

**Not taken:** asking the user to switch off and re-pair when an address changes (our mesh distributes
routes automatically); depending on third-party cloud push; having no relay, so that nothing works
when the host computer is off (we have the encrypted relay and several computers).

### 8.3 Additions: six gaps the original design did not cover

The two tables above mostly confirm the original design. The six items below are real gaps, in
order of importance.

| # | Content | Learned from | Step |
| --- | --- | --- | --- |
| 1 | **Conversation responder lease + fencing token.** Within a lease period each conversation is answered by exactly one computer (whoever accepts the conversation's first message becomes its first responder). The responder renews by heartbeat; once it has been offline past the lease, new messages go to another computer, while work in progress is not moved and is queried or marked unknown as in section 1.5. Every change of responder increments a token; writes carry the token and stale tokens are rejected, so an old responder waking from sleep cannot answer with outdated context. This is what makes "the phone connects only to Xiaoyou" hold; otherwise one message may be answered by both computers or by neither | Raft's leader lease and term | R4 (automatic connection) defines the rule and lease; R6 (agent loop) checks the token at the entry of `Loop.step` |
| 2 | **Deterministic simulation tests.** Sync, the mesh and request recovery are tested with a controllable fake clock and fake network (drops, reordering, duplicates, disconnects, a computer crashing and restarting), replayed from a fixed seed instead of real threads and waits. A failure reproduces from the same seed | etcd-raft's deterministic testing approach | Required for all new logic from R3; sync and mesh in R4 must be covered |
| 3 | **A new computer catches up before working.** A computer joining the mesh first only syncs and takes no conversations (learner state); only after reaching the other nodes' committed position and loading the context it needs may it become a conversation's responder or accept handed-over tasks | Raft's learner nodes | R4 |
| 4 | **One membership change at a time.** Adding a computer, revoking a phone or rotating an identity key happens one at a time, and the next starts only after the previous takes effect; taking effect means applied, not merely committed. A device returning from offline syncs the membership list before accepting remote commands | Raft's membership change rules | R4, implemented with the membership registry of the desktop plan |
| 5 | **Segments for long conversations.** The main chat is split into segments: all history is kept, while the model's context takes the current segment + summaries of earlier segments + relevant retrieval. Commitments, watches and dispatched tasks stay bound to their segment and, when due, are delivered to the segment the owner is in now, without copying. This replaces the current "last 6 turns, last 5 cards" cut-off | DS-Bot-App's segments | R6 (`ContextBuilder`) |
| 6 | **Smaller rules:** (1) routes the owner configured by hand (such as Funnel or a public address) take precedence and automatic route management never overwrites them; (2) local-only and link-local addresses are never offered to the phone as external routes; (3) preinstalling dsh and pi supports offline packages and rollback on failure, not only pinned versions and checksums; (4) after a helper performs an action with side effects, earlier reads are invalid and must be refreshed before the next step | DS-Bot-App's configuration precedence, address filtering, transactional install and re-read after action | (1)(2) R4; (3) the preinstall plan; (4) R6 (`Executor`) |

All six borrow ideas only and bring in neither Raft's elections nor quorum: the responder is fixed by a
rule (whoever accepts the first message), and the lease exists only for a safe handover when the
responder is offline.
