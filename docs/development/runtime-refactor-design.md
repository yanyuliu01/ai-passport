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

**Three generations and positions that must not be mixed** (review correction, see
[runtime-refactor-review](runtime-refactor-review.md) section 3):

- `log_epoch` (the existing protocol field `epoch`): the generation of one origin's log history,
  used only to decide whether a cursor can continue.
- `owner_generation`: the responder grant generation of one conversation, managed by the
  coordination authority and used only to reject stale executors (see 3.1).
- `accepted_seq` / `applied_seq`: the durable acceptance position and the projection-applied
  position within one origin and one log generation. Local durable acceptance is not Raft's
  majority commit.

Progress across origins is tracked per `(origin, log_epoch, seq)`; the sequence numbers of two
computers are never compared to decide which is newer. An ordinary restart does not reset durable
identities or generations. The names separate concepts; existing compatible fields need not be
renamed now.

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

- **A gap is filled from retained deltas first; only an invalid cursor or missing deltas get a snapshot, and an empty cursor gets a snapshot directly, never a replay of history.** The snapshot holds the
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

**Snapshot boundaries and bounds:** bind every cursor and callback to Runtime identity, epoch, authorized scope and connection generation. Freeze a snapshot at sequence S; paginate it with a stable snapshot token, retain deltas after S, and switch to live delivery only after the scoped snapshot is applied. Deltas after S are kept only within a bounded retention window; a slow client past the window takes a new snapshot instead of the log being kept forever for every client. Expired tokens or missing retained deltas explicitly restart reconciliation. Ignore callbacks from obsolete connections; tombstones prevent deleted items from returning.

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
- **Conversation responder (first version):** each conversation is bound to one durable, explicit
  responder; the first binding goes through a designated authority rather than each node guessing.
  A network timeout only shows unreachability and does not prove the responder stopped, so the
  responder is **never changed automatically on timeout**. While the responder is unreachable the
  request keeps its original id and shows as queued / responder unreachable, distinguishing local
  pending from authoritatively accepted, and never claims to have started; old tasks are not moved,
  they are queried by operation id and marked unknown if they cannot be reconciled. A manual
  "take over" must meet the same isolation conditions. Automatic takeover waits until the
  coordination authority, granting, write isolation, invalidation of model results, isolation of
  external tools, coordinator failure and clock rules are written down and pass fault tests (review
  section 2); R4 implements granting and write isolation and R6 connects the model and execution
  paths, so switching never comes before rejection.
- **Deterministic core:** state reduction is "previous state + saved events -> new state / pending
  intents". Clocks, random identities, model replies, network results and tool results are explicit
  inputs, saved when recovery needs them. Replay **never calls a model, runs a tool, sends a
  notification or regenerates a decision**; an outer layer drives model requests and tool runs, and
  their results return to the state machine as events. The projection and `applied_seq` are saved
  together (review section 4).
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
- Conversation owners (R4.2, decided by the owner on 10-23): the main conversation belongs to the Mac; a side conversation belongs to the computer that created it; when the owning computer is unreachable, messages queue and nobody answers in its place; only manual handoff while both computers are online, no forced takeover.

**How R4.2 does it:**

- **Binding records.** An owner is a binding record (conversation, owner, generation) that travels through the mesh as a record of the computer that wrote it (kind `owner`). The generation is separate from the log generation (`log_epoch`). Generation 1 of the main conversation is written by the computer configured with `mesh.home: true` (the owner sets it on the Mac). Generation 0 of a side conversation is its creator, kept in the catalog's `owner` field, which renames and sync never change; old side conversations without it belong to the owner of the main conversation. Until any binding exists, the computer that receives a message answers it as before R4.2, so nothing blocks the main conversation before the Mac is configured.
- **Who may write the next generation.** Only the current owner may write the next binding, so no separate coordination server is needed. Every computer derives the current owner from the records by the same rule: generation g counts only if written by the owner of generation g−1. If two computers both wrote generation 1 of the main conversation (a configuration error), the smaller Runtime id wins and the status reports the conflict.
- **Fencing at the write path.** When a worker claims a message it checks, under the same lock, whether this computer is still the owner; if not, it forwards the message to the owner instead of answering. A handoff writes the new binding under that same lock and only when the conversation has nothing queued, nothing being processed and no task in progress, so there is no window between the check and the write. Helper work dispatched to another computer (`hop` above 0) and a remark inside a task (`pin`) are unaffected: a task in progress stays on the computer that runs it.
- **Fencing on received records.** After a computer writes a binding that hands a conversation away, other computers reject the replies and new tasks it writes in that conversation afterwards (judged by that computer's own sequence numbers, not clocks); once the conversation is handed back to it they are accepted again.
- **Queueing when unreachable.** If the owner is offline when a message is forwarded, the message keeps its id, stays queued with operation state `pending`, and shows "queued: the owning computer "Mac" is unreachable"; it is forwarded in order once the owner is back. The forwarded message carries the binding record, which the receiver checks and adopts first, so the two computers never bounce it back and forth; a forwarded message is never forwarded a second time.
- **Manual handoff.** `POST /v1/conversations/<name>/owner` with `{"to": "<computer name or id>"}`. Called on the owner it hands off directly; called elsewhere it becomes a command to the owner. Both computers must be online; when the idle condition does not hold it refuses and says why.

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

**R3 progress:**

- R3.1 is committed (`abab81f`).
- R3.2: journal records carry an increasing number `n`, and the `cards.json` snapshot records the last one it includes (`"journal"`). Startup reads the snapshot and replays only the later records. The snapshot is rewritten in full only every 64 records; before writing it the in-memory cards are reconciled into the journal (`sync`), and the journal is compacted once the snapshot is durable (file and directory fsync). Files written by R3.1 (a snapshot without `"journal"`) take `cards.json` as the authority and are upgraded on the first start. Out-of-process readers (shared history, desktop handoff, iterate signals) read "snapshot + journal" through `world.current()`. The crash test records both files around every append (including half-written lines) and every full replacement, and checks that replay yields only the state before or after that step.
- Compaction keeps no records before the snapshot; the bounded retention window for slow clients comes with sync in R4.
- R3.3: Service messages also use "snapshot + journal" (`messages.json` as the periodic snapshot, `messages.jsonl` as the journal; old format 1 files are upgraded on the first start). `Journal` is extracted from the task journal and shared by both. Records written together (for example a message and its dedupe entry) go on one line, so a power cut never leaves half of them. A failed snapshot does not undo an acceptance already in the journal. Each message is an operation (`operations.py`): the `operation_id` is derived from the message id; it is `pending` when accepted, written as `running` before processing starts, and ends as `succeeded`, `failed` or `cancelled`. A finished operation never changes again, and a late change is dropped whole. Only a `pending` operation can be claimed, so a duplicate delivery runs once. On startup, per section 1.5: a `pending` text message runs once; a `running` one becomes `unknown` (the message still shows as unfinished); one whose reply is already on the card is recorded as `succeeded` from the stored result, without rerunning.
- Not done yet: operation identity for tasks (helper runs) and checking by execution identity come with the agent loop in R5; `cancelled` exists only in the transition table, as messages have no cancel entry yet.

**R4 progress:**

- R4.1: a `/v1/sync` snapshot holds only the present and comes in one page (see the interface table in unified-message-model); increments are paged by count (200) and size (48 KB). When more than 1000 items changed after the cursor the client gets a snapshot; while the increments are complete it gets increments. Fixes N10: syncing from 0 against a copy of the 10-23 device Runtime records used to take 408 pages and now takes one page of about 128 KB in about 160 ms on the server, with the just-finished task result in it. Persistent client cursors belong to the App and were not changed; real latency over Funnel was not measured.
- R4.2: implements section 6: bindings, fencing at the write path and on received records, queueing when the owner is unreachable, and manual handoff (`ownership.py`, `mesh.py`, `service.py`). The Mac's config needs `"mesh": {"home": true}` and a restart before it takes effect; until then nothing changes. Fault tests: a message waits while the owner is unreachable and is forwarded when it returns; after a handoff, replies the old owner still writes are rejected (that test fails with fencing turned off); no handoff while busy or while the target is offline; a forwarded message is not bounced back; a side conversation belongs to its creator. Not tested: devices, two computers really disconnected, forwarding latency over Funnel.

## 8. Lessons from Raft and DS-Bot-App

The owner asked to learn from the design logic of two open-source projects without copying them.
This section has been corrected after [Codex's review](runtime-refactor-review.md): the conditions the
borrowing had left out are added, and statements such as "the epoch plays the role of the term" and
"a timeout allows a safe handover" are removed.

### 8.1 Raft (following the design of etcd-io/raft)

Not using Raft's leader election and quorum is an optional architectural decision: the first version
relies on "single writer per origin + an explicit responder" for safety and does not promise
automatic availability when a responder is lost. This choice is **not** justified by "a home has only
two or three computers, so a quorum means nothing"; single-writer safety rests on the generations of
section 1.2 and the responder rules of 3.1.

| What Raft does | How we use it | Step |
| --- | --- | --- |
| The core is a deterministic state machine; the application owns network and disk. Inputs are messages or clock ticks; outputs are "what to persist + what to send + new state" (the Ready loop) | State reduction is "previous state + saved events -> new state / pending intents". Clocks, random identities, model replies, network and tool results are explicit, saved inputs. Replay never calls a model, runs a tool or sends a notification (see 3.1) | R3, R6 |
| Persist before sending; committed and applied are separate | `accepted_seq` (local durable acceptance) and `applied_seq` (projection applied) are tracked separately; the projection and `applied_seq` are saved together so a restart applies from the right place. Local durable acceptance is not a majority commit | R3 |
| Replication carries the previous position as a consistency check; a mismatch backs off or switches to a snapshot | Every sync batch carries the previous `(origin, log_epoch, seq)`; a gap is **first filled from retained deltas**, and only an invalid cursor or missing deltas get a snapshot. `log_epoch` only marks the log history generation; it is **not** the term and is never used to reject stale executors | R4, mesh |
| Snapshots + log compaction | A snapshot is pinned at S; deltas after S are kept within a **bounded retention window**, and a slow client past it takes a new snapshot instead of the log being kept forever | R3.2, R4 |
| Flow control: size and in-flight limits; probe a lagging follower, pipeline once caught up | Pages limit both count and size; a lagging client is served in snapshot mode and switches to deltas once caught up; one in-flight long poll per connection | R4 |
| A proposal is not guaranteed to commit; the client retries with the same request | The decided dedupe by request identity; retries carry the original id | R2 (exists) |
| Read-only requests confirm authority before answering (ReadIndex / lease) | Answers about current state read the origin's latest state; a replica is labelled with its freshness and stale data is confirmed with the origin (M1) | R3, R6 |

**Not taken:** leader election, quorum, joint-consensus membership changes, linearizable reads and
writes.

### 8.2 DS-Bot-App (multi-end communication)

| What DS-Bot-App does | How we use it (with review corrections) | Step |
| --- | --- | --- |
| QR pairing: the code carries the host's public-key fingerprint; self-signed certificate with fingerprint pinning, TLS verification never disabled, no plaintext fallback; the host approves once and later reconnects are automatic | Every route verifies **the same host identity**, with the verification method suited to each transport; the host identity and route certificates are distinct | R4 |
| A changed host identity requires re-pairing; renewal keeps the private key; a corrupted key makes the host refuse to start | A changed host identity means re-pairing; **certificate renewal or a changed tunnel entry is not a new host identity** and does not bother the user; a corrupted identity file is an error, never an automatic new identity | R4 |
| "Waiting for an answer", "host running" and "network connection" are shown separately | Connection state and what Xiaoyou is doing are shown separately | R5, UI |
| Notifications: the host keeps the latest 500 and the phone sees 100; read state per device on the host; old notifications are not replayed after reconnecting | Notifications have their own cursor; read state lives in the Runtime (S2); old notifications are not shown again, but **approvals, failures and to-dos still pending stay in the status view and notification centre — read is not handled**; opening one first checks which Runtime it came from | R4, R5 |
| Android background delivery polls from a foreground service with bounded backoff | Bounded backoff is fine, but the system does not guarantee background execution; **background notification delay never counts as foreground sync or first-response latency** | R4 |
| A lost receipt means refreshing state; after a disconnect the client queries the original task and never redoes a non-repeatable operation | As in section 1.5 | R2, R3 |
| After a session switches segments, scheduled tasks stay bound to it and reminders go to the current segment | See 8.3 item 5 | R6 |
| Safe by default; install sources pinned to a commit with SHA-256 | See 8.3 item 6 | Preinstall plan |

**Not taken:** asking the user to re-pair when an address changes; depending on third-party cloud push;
having no relay, so that nothing works when the host is off.

### 8.3 Additions the original design did not cover (with review corrections)

| # | Content | Step |
| --- | --- | --- |
| 1 | **Conversation responder.** The first version uses a **fixed responder with no automatic takeover**; the rules are in 3.1. The earlier wording "whoever accepts the first message responds; on heartbeat timeout another computer takes over and increments the token" is **withdrawn**: when two computers lose contact, each raising the token from 7 to 8 does not disqualify either. Preconditions for automatic takeover (review section 2): a defined token issuer (a durable coordination authority or a protocol with safety guarantees, never each node incrementing on its own); atomic, idempotent grants; stale tokens rejected **on the authoritative write path and at the real execution boundary**, checked before committing a decision, dispatching an action and publishing a reply without a race window; model results that span a handover kept only for audit; external tools without token support stopped from further dispatch by the old executor, with started actions reconciled; defined behaviour when the coordinator is lost or restarts; and, if leases are used, monotonic clocks, drift, sleep and expiry rules. The coordination scheme is not chosen yet; this design names no new server and does not introduce Raft | R4 implements binding, granting and write isolation; R6 adds checks on the model and execution paths; automatic takeover is enabled only after these conditions pass fault tests |
| 2 | **Deterministic simulation tests.** Replay with controlled clocks, network delivery and fault positions (drops, duplicates, reordering, partitions, crashes); fixed seeds are only an aid, and failing seeds are kept with the needed event traces. Real threads, disk, HTTP and cross-end integration tests remain; simulation never replaces concurrency or device validation | From R3; sync and mesh in R4 must be covered |
| 3 | **A new node catches up before working.** It catches up on **one conversation's required state at an explicit cut point** (messages, to-dos, commitments, permissions, required execution bindings) and then continues with the deltas after the cut; it does not wait for every node's full history, and progress is tracked per origin. Catching up is necessary but not sufficient: it still needs a responder grant and confirmed execution capability | R4 |
| 4 | **Membership changes.** A designated identity / permission authority applies them serially; they take effect when that authority has durably applied them and the needed execution boundaries enforce them, without waiting for every offline device to confirm. A node returning from offline updates its permission version before handling commands; revoking a device means every boundary that accepts its commands rejects the stale grant; without the needed permission freshness, sensitive operations are refused. The permission authority is not chosen yet and is an open design condition of R4; "one change at a time" does not replace a concrete consistency scheme | R4 (open condition) |
| 5 | **Segments for long conversations.** Conversation, task and commitment identities stay stable; context takes the current segment + summaries of earlier segments + relevant retrieval, with summaries stating their coverage and traceable to the original text; results of tasks from an earlier segment reference the original task and are delivered to the current segment without copying it. This replaces the "last 6 turns, last 5 cards" cut-off | R6 (`ContextBuilder`) |
| 6 | **Smaller rules:** (1) addresses are labelled by reach (LAN, private network, public entry) and checked for identity and connection health; a private address may serve clients on the same LAN but is never treated as reachable from anywhere, nor dropped altogether; link-local addresses must match the interface and network scope; (2) automatic discovery never overwrites manually configured routes; when a manual route is unreachable, an authorized policy may switch to another verified route while keeping the original configuration and its failure state; (3) after an action with side effects, invalidate the old snapshot of **the affected resources** and re-read before anything depending on them, without invalidating unrelated caches; a failed re-read leaves the state unknown and never repeats the action automatically; (4) preinstalling dsh and pi pins versions, verifies sources and supports offline packages and rollback, following the preinstall plan rather than being mixed into the Runtime projection work | (1)(2) R4; (3) R6 (`Executor`); (4) preinstall plan |

**Required fault cases** (review section 7): two computers cut off from each other but both able to
call models; a stale model result returning during a responder change; an old computer waking from
sleep; a coordinator crash; takeover racing a dispatch; one request retried through two entries; a
revoked offline device reconnecting; an unrelated offline node not blocking the current
conversation's catch-up; interleaved snapshot and deltas; an approval still pending while its
notification is read; switching between LAN and remote routes. Each reports real evidence; anything
not implemented or lacking a device or environment is marked NOT RUN.
