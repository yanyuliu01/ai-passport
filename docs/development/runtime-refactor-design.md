<p align="right"><a href="runtime-refactor-design.zh_CN.md">简体中文</a> · <strong>English</strong></p>

# Runtime three-layer refactor design

Status: design approved; implementation contracts clarified on 2026-10-23. R1-R4 and the Runtime
part of R5 are committed, see section 7; R6 has execution recovery, message-derived context and a shared
conversation queue, but decision/effect migration is incomplete. R7 directory moves, cycle removal and direct
dependency cleanup are implemented. R8 negotiated client migration is implemented; legacy API removal
and the R6 default cutover remain gated. Section 7 distinguishes source, deployment and acceptance.

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

**Display:** the projection supplies block boundaries, formatting runs (bold, italic, code, headings, links, lists, quotes) and
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
- Permissions (R4.3, decided by the owner on 10-23): the permission authority is the Mac; each device gets its own credential, and the old shared token keeps working during the transition; answering approvals, adding or removing devices and computers, and handing off an owner are sensitive operations. R4.3 does the Runtime part first; the App and desktop client follow in a separate batch.

**How R4.3 does it (Runtime part):**

- **The permission authority** is the runtime that wrote generation 1 of the main conversation's binding (the Mac, configured with `mesh.home`); handing the main conversation to another computer later does not move the authority. A Runtime outside a mesh is its own authority.
- **Host identity** is the existing `runtime_id` (`state/runtime-id`). A damaged file used to be replaced silently; now it is generated only when missing, and a damaged one makes the Runtime refuse to start and say why, because a new identity means every device must pair again. `/v1/info` reports the identity id and fingerprint. The Runtime has no asymmetric crypto library and does not terminate TLS itself, so identity is proven with the device credential: the client sends a random nonce and the Runtime answers with an HMAC keyed by the hash of that device's credential, which can be checked on any route (LAN, private network, public entry, relay). **It proves "this is a Runtime in the owner's mesh that holds my credential", not which computer in the mesh** (credential hashes are synced to every runtime); proving a specific computer needs asymmetric keys, which belong to the client batch. Certificate renewal or a new tunnel entry does not change the host identity.
- **Device credentials.** At pairing the authority issues `dev.<id>.<secret>`, shown once; every runtime stores only the hash of the secret, and credentials travel through the mesh (only records written by the authority count). Revocation is also recorded by the authority and synced, after which every runtime rejects the credential. The old shared token keeps working until the App and desktop client have migrated.
- **Sensitive operations.** Answering approvals, adding or removing devices, removing a computer, and handing off an owner: on a runtime other than the authority they require the authority to be online now (so the list is current), and are otherwise refused with "the Mac is offline". Chat, viewing and stopping tasks are unaffected. Adding or removing devices and removing a computer run only on the authority; started elsewhere they become a command to the authority.
- **Removing a computer.** Joining still uses the mesh secret; the authority can remove a computer, after which every runtime refuses to exchange records with it.
- **Address scope and manual routes.** Each address is labelled with its scope: loopback, LAN, private network (Tailscale 100.64/10) or public entry. Addresses written by hand in `mesh.peers` are never overwritten by discovered ones and go first once the peer is identified.

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
- The next step at that point was R3 (the `World` projection, rebuildable snapshots and crash-boundary tests). For the current continuation order, see the 2026-10-24 review at the end of this section.
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
- Not done yet: operation identity for tasks (helper runs) and checking by execution identity move to R6 with the agent loop; `cancelled` exists only in the transition table, as messages have no cancel entry yet.

**R4 progress:**

- R4.1: a `/v1/sync` snapshot holds only the present and comes in one page (see the interface table in unified-message-model); increments are paged by count (200) and size (48 KB). When more than 1000 items changed after the cursor the client gets a snapshot; while the increments are complete it gets increments. Fixes N10: syncing from 0 against a copy of the 10-23 device Runtime records used to take 408 pages and now takes one page of about 128 KB in about 160 ms on the server, with the just-finished task result in it. Persistent client cursors belong to the App and were not changed; real latency over Funnel was not measured.
- R4.2: implements section 6: bindings, fencing at the write path and on received records, queueing when the owner is unreachable, and manual handoff (`ownership.py`, `mesh.py`, `service.py`). The Mac's config needs `"mesh": {"home": true}` and a restart before it takes effect; until then nothing changes. Fault tests: a message waits while the owner is unreachable and is forwarded when it returns; after a handoff, replies the old owner still writes are rejected (that test fails with fencing turned off); no handoff while busy or while the target is offline; a forwarded message is not bounced back; a side conversation belongs to its creator. Not tested: devices, two computers really disconnected, forwarding latency over Funnel.
- R4.3 (Runtime part): implements section 6: host identity (a damaged file refuses to start), `host_proof`, per-device credentials (`devices.py`: issue, sync, revoke), the permission authority and sensitive-operation checks, removing a computer, address scopes and manual routes (`identity.py`, `mesh.py`, `service.py`, `server.py`). New endpoints: `GET/POST /v1/devices`, `POST /v1/devices/<id>/revoke`, `POST /v1/mesh/members/<id>/remove`, `GET /v1/info?nonce=`. Tests cover: a damaged identity is not replaced; a proof holds only for the same credential; revocation is final; a device paired on Windows is recorded by the Mac and its secret never crosses the mesh; sensitive operations are refused while the Mac is offline; a removed computer can no longer exchange records; hand-written addresses stay first; a credential works over HTTP and gets 401 after revocation. Until the Mac sets `mesh.home`, every runtime is its own authority (as before). Not done: the App and desktop client switching to device credentials and checking `host_proof`; proving a specific computer needs the asymmetric keys of the client batch.

- R4 review (Windows, 2026-10-23): the R3/R4 recovery paths were checked against section 1.5 with fault tests, and four gaps were fixed. An append interrupted mid-line is repaired before the next one, and a failed append is truncated back. Without a snapshot, a journal that starts at 1 is replayed (messages accepted before the first snapshot are kept); a journal that is only a suffix is not replayed, and the Runtime starts empty under a new epoch instead of refusing to start. Out-of-process readers re-check the snapshot after reading the journal, so compaction never pairs an old snapshot with a trimmed journal. A message's terminal state is visible only after it is durable. Also, a message that reaches a computer before the side conversation's creator metadata waits queued instead of being answered there.

**R5 progress:**

- R5 (Runtime part): Xiaoyou's replies were already log records that stream by whole-text replacement and never return from final to streaming; R5 makes this explicit in the projection and completes the formatting. `presentation.event` names the record with the event kinds of 1.2: `said` (the owner), `assistant_message` (a reply in the chat), `task_progress` (work inside a task) and `session_observed` (an observed desktop session). Runs now carry `italic`, `strike`, `code` (`inline` / `block`, with `language`), `heading` (level, also `bold`), `href` (only http, https and mailto) and `quote`; fence lines, `#` and the other markers are not part of the text. Every run keeps `text` and `bold`, so current clients show the words without markers and lose nothing. `presentation.plain` is the unstyled text for one-line places (home card, device screen, copying), and a task's `presentation.brief` / `presentation.progress` are its words without markers. `presentation_policy` lists `formatting` and `events`. The shared fixture `tests/fixtures/presentation-v1.json` gains a case with every format; `test_presentation_policy` checks that a client following only the `/v1/sync` cursor sees a formatted reply streaming and then final. Not done: the App and desktop client drawing the new styles and using `plain` instead of their own `Markdown.plain` (a separate client batch); operation identity for helper runs moves to R6 with the agent loop.

### 2026-10-24 review and continuation order

The [complete Xiaoyou proposal](xiaoyou-agent-model.md) defines the product direction; this document governs the three layers and migration acceptance. Raft capability grants and the Magpie model service in the [helper capability design](helper-capabilities.md) belong to helper integration and do not replace R6's unified processing path.

Before starting R6, the review of baseline `9ca76c7` and the then-uncommitted changes found an initial implementation of Magpie model discovery, helper bindings, desktop selection and MCP grants; see the helper capability design for scope and validation. `xiaoyou.py` still falls back from a failed tool-based reply to JSON and asks again when a plan is invalid; `decide_first` defaults to false; `Pulse` wakes for background progress and task completion uses separate callbacks. At that point `operations.py` covered only Service messages. The two progress batches below describe subsequent helper recovery; the unified loop remains incomplete.

Proposed independently verifiable batches, before moving directories:

| Batch | Scope | Acceptance boundary |
| --- | --- | --- |
| R6.1 Execution and recovery | Persist decisions and pending helper intents through Intake; reuse task, run and execution-binding identities; claim before dispatch; reconcile uncertain results by original execution identity; save a stable trigger identity for terminal-result delivery | A failed save cannot dispatch; saved, unclaimed work recovers; an external action that may have started is never blindly repeated; lost receipts, duplicate results and restart do not repeat execution or replies. Check conversation ownership when committing decisions, dispatching and publishing replies; old results spanning a handover cannot take effect as new decisions |
| R6.2 Context and wake-ups | Build focus, promises, segmented history and relevant retrieval from records and current state with `ContextBuilder`; route terminal and due events through the same processing path; replace Pulse's in-memory counters with durable trigger progress; stop writing `transcript.json` per R3.4 | Side-conversation results return there; each trigger has one effective processing result; replay calls no models, tools or notifications; current versions, tasks and connections come from current state; migration retains history and a rollback path |
| R6.3 Measurement and cutover | Unify decision, execution facts and speaking; enable by default and remove JSON fallback and re-asking only after real-model and client measurements | Validate the first meaningful complete bubble within three seconds, interruption, cancellation, approvals, connection recovery and multi-end consistency under the cross-stack reliability rules; model first text and a queued receipt do not count as a client response |

R4's client cursors, device credentials and identity checks, and R5's client formatting still have unfinished work. Runtime commits do not establish end-to-end acceptance of these contracts. R8 removes old interfaces only after client migration and version gates; R7 file organization follows stable execution and recovery behavior.

This is a source review and continuation plan only: no commit, deployment or restart of the live Runtime. The complete gate has not passed. Focused tests and isolated model-service probes do not replace full regression, real-account, device, three-second response, real reconnection or multi-end synchronization acceptance.

**Overall review and first R6.1 batch (2026-10-24):** retain the three layers. Reuse the existing task journal, `run_id` and execution bindings without adding a task store, event bus or general scheduler; retain fixed ownership and manual idle handoff. This batch only tightens local dispatch boundaries; it adds no model calls and keeps the `decide_first` default.

- Reserve a card in the task manager before saving its run identity and starting its thread. Duplicate dispatch cannot overwrite the original identity or record another assignment. A failed save, missing card or failed thread start releases admission.
- Before each actual helper invocation, the initial execution binding must be saved and its active card and current run identity checked; stale runs cannot begin execution. Subsequent native-session, turn and end-time metadata may still report an audit gap, so a metadata failure does not strand a result already executed.
- A failed started callback releases the parallel slot and goes through failure completion, instead of retaining a nonexistent active worker. New fault tests join the static gate; the log comparison keeps its original golden file, adapting only how the test intercepts the start callback.

The first batch only tightened dispatch boundaries. Recovery of pending inputs and saved results follows in the second batch below; this does not establish completion of R6.

**Second R6.1 batch (2026-10-24): pending-input recovery.** A versioned private `dispatch` field in the existing task journal stores the original run identity, input, model parameters, borrowed session, queued supplements and image references. It stores a helper configuration fingerprint rather than its credentials. Public cards, sync and Mesh omit this input.

- Unclaimed `pending` inputs resume under their original identities after policies, identity and delivery lanes are ready; Mesh nodes wait for ownership initialization. Missing or changed helpers wait for reconciliation instead of falling back to another helper. Images must still match their original snapshots, otherwise work remains waiting.
- Claim state and the initial execution binding are saved in one task-journal change. A started execution with no confirmed result becomes `unknown` after restart and is never automatically replayed. Older tasks without recovery intents retain the previous unfinished-task behavior.
- When the original execution's final result is already in the message log, deliver that saved result to the original conversation using `<run_id>-summary:reply`, without rerunning the action or calling a model. The write checks both run identity and state and cannot overwrite a newer run. Partial output, progress or an audit end marker alone still leave the result unknown.
- Delegated work restores the original request's waiter: queued work continues and unconfirmed work remains waiting. Terminal receipts use the original run's summary identity, never a later run or casual reply.
- Conversation handoff is refused while an execution result still needs reconciliation. The handoff scenario waits for an idle conversation lane before asserting transfer; a durable reply alone does not prove lane cleanup has finished. Busy-handoff rejection remains unchanged.
- A failed snapshot does not undo a durable journal acceptance; a failed thread start leaves accepted work queued for recovery. A failed recovery write cannot launch a helper and retains the identity for retry. Log comparison still checks complete replayed state; its old public-state golden excludes the new private input, covered separately by recovery fault tests.

**R6.2 source batch (2026-10-24): context and conversation scheduling.** `context.py` projects model history from a consistent snapshot of canonical messages and task attribution. Chat, helper background, result summaries, cancellation, recall and proactive notes read it. New local turns no longer write `transcript.json`; old transcript-only history and the supported clients' share/import endpoint remain compatible. Canonical revisions take precedence over old copies; partial, failed, activity and raw helper-result records do not masquerade as a second completed answer. `context.json` stores only message-version hashes, model-delivery receipts and reset boundaries. Resetting model context preserves saved conversation messages; a malformed reset file fails visibly rather than silently restoring forgotten context.

`ConversationLoop` extracts the existing bounded per-conversation queue from Service. Owner messages, helper-result callbacks, due promises, progress notifications and memory upkeep use this same queue. Timer work rechecks the original conversation, source state and owner generation before running. It shares the normal model context and interruption handling; upkeep uses an isolated bounded window so it cannot alter a chat's native-session pointer. Closing the loop prevents queued model work from starting; saved owner requests and due promises remain the recovery sources.

A failed read-only reminder call leaves the due trigger retryable. Before a reminder can change commitments, its existing promise record durably claims that wake. A lost result after a possible tool effect stays visibly unconfirmed and is not automatically repeated. Stable due-message ids reconcile a saved reply whose final nudge checkpoint was lost. Upkeep likewise saves its window marker before memory edits, so an interrupted edit window is not blindly repeated. These boundaries reuse the existing promise and memory files; they do not add a workflow database. At that batch stage, task-progress watch counters still lived in memory; the durable-progress batch below replaces them.

Source validation on Windows, Runtime 0.23.1 plus uncommitted changes: 235 focused tests and 95 adjacent tests passed, including 30 new context/loop cases. Repository checks and the whitespace check passed. The complete gate failed in its 210-test Runtime stage with the same 12 failure ids as the preceding recovery baseline and two `WinError 10053` connection aborts. The two affected HTTP/approval tests passed in isolation on the candidate; the approval abort also reproduced on the unchanged baseline. This does not make the gate pass. The firmware build stage was not reached; device, real-model first response, reconnection and cross-end acceptance were not run.

**R6 request-effect batch (2026-10-24): saved selections, claims and results.** Main conversation memory/commitment tools and model-selected task actions now use `Effects`, with versioned private records in the existing Service request journal. The input is appended as `pending`, then claimed as `running`, before invoking its mutation. A saved result is reused; an unconfirmed effect blocks further mutations in that turn and owner handoff. Competing claims are refused. Failed claims do not invoke tools, and storage recovery or restart resumes the saved input without another model decision. Recovery checks the original helper configuration and target task revision; changed inputs require reconciliation. Task recovery announcements use saved execution facts rather than the model's pre-execution promise. Remote command admission is not represented as confirmed remote completion.

Tool results survive a lost provider response without switching to JSON or asking the model to make another mutation. Effects with saved results return to the original conversation after restart; running/unknown effects are not repeated. Already committed journal state remains accepted if a later receipt/projection write fails. Owner/generation checks fence selection, claim, model output and result publication on this request path. Private effect payloads are excluded from client and Mesh projections. Memory writes and their audit entries are flushed before confirming their effect result. An uncertain record is retained instead of being trimmed with completed requests.

CLI `--once` now submits through Service and its conversation loop, using the same request journal and effect boundary. A completed request carries additive `started` metadata, preserving the distinction between handing work out and finishing it even when a helper finishes quickly. The background timer starts after Service and Mesh initialization. Existing timer promise/upkeep claims remain as documented above; this is not a claim that all event sources use the same effect implementation.

Recovery retains the outer request's `running` claim while queueing its saved effects/results. The R3-capable baseline therefore sees a started request on rollback, rather than replaying its owner input as wholly new work. This boundary test is not full live rollback acceptance. Runtime-generated recovery replies are not marked as already delivered to a model that never received them; the next model turn can recap those saved facts.

Request-effect source validation: 276 focused tests passed, including 20 new effect/recovery cases; 95 adjacent tests passed. Repository and whitespace checks passed. The final complete gate failed in the 210-test Runtime stage: the same 12 baseline failure ids, plus a remote-error assertion after `WinError 10053` replaced the expected HTTP response. That remote test passed in isolation on both the candidate and unchanged baseline; this diagnostic does not override the failed gate. Firmware build, device acceptance, real-model three-second response, live reconnection and cross-end acceptance were not run. Source remains Runtime 0.23.1 plus uncommitted changes, not a deployed release.

**R6.1 native-result query batch (2026-10-24).** When the Runtime has no saved worker result, recovery now reads the original Codex rollout without starting a native process or calling a model. It requires the original helper fingerprint and home scope, session id, latest execution attempt and matching explicit turn-start/turn-complete evidence. A final-looking message, audit end marker, another turn, abort, malformed source or ambiguous/forked source cannot establish success. The scan is bounded at 16 MiB; unsupported sources stay unknown. Recovered worker results and summaries retain stable run ids and original conversation attribution; long text keeps a complete attachment. Saving and publishing compare both the run and the captured latest execution binding, so a concurrent new attempt cannot receive an older result. A saved worker result survives a failed summary checkpoint and can be delivered later without querying or rerunning the action.

The authenticated `POST /v1/cards/<id>/reconcile` endpoint takes only the original `run_id` and rechecks an unknown local helper run; stale identities and foreign, observed or active cards are refused. Repeated/concurrent queries publish one effective result and summary; incomplete evidence returns `unconfirmed`. File reads do not hold Service's polling condition lock. Owner and shutdown checks precede publication. `/v1/info` advertises `execution_reconciliation`. This is a backend query entry point; an App review screen and owner-confirmed resolution of uncertain request effects remain separate work. No new workflow database or scheduler was added.

Native-query source validation: 311 focused tests and 95 adjacent tests passed, including 35 new native-query cases. Repository and whitespace checks passed. The final complete gate failed in its 210-test Runtime stage with the same 12 baseline failure ids and one HTTP-test `WinError 10053` connection abort; that HTTP test passed in isolation on both candidate and unchanged baseline. An extended desktop-filter check also failed on both candidate and unchanged baseline. An earlier gate attempt stopped on a Zig compiler cache read error, which did not recur in the final run. None of these diagnostics makes the complete gate pass. Firmware build, devices, real-model/client three-second response, live upgrade/rollback, reconnection and cross-end acceptance remain NOT RUN. Source is Runtime 0.23.1 plus uncommitted changes, not deployed.

**R6.2 durable-progress batch (2026-10-24).** Progress notifications now keep one private versioned `progress_watch` record in the existing task journal. It contains the original run and conversation's owner generation, bounded progress input, stable `<run>-gate-<ordinal>` id, admission/claim state, saved reply, wake count and wall-clock cooldown. Pending input is saved before queueing; claiming it is a compare-and-save operation before a model call. A saved reply is published under its original message id without another model call, and a canonical note repairs a lost card projection or completion receipt. Silent decisions also consume their saved ordinal. Interrupted claims without a saved reply or canonical note stay unknown and are not automatically regenerated; later independent progress may produce a new bounded trigger. A known read-only provider failure retains the same pending input for retry after the cooldown. Retries share the persisted per-run wake-attempt budget and cannot exceed the existing limit after a restart.

Watch replay performs no model, tool or notification work. A replacement Pulse reads its counters and pending/saved results from the task journal. A full Runtime restart does not revive an interrupted helper merely to deliver progress: the original task must still be active, otherwise normal execution reconciliation or the terminal task result governs it. Completed tasks, changed runs, changed owner generations and closed Pulses do not publish stale updates. Progress callbacks and note writes now compare the active run, and changing a run clears its old watch. Private watch input/results do not enter client or Mesh projections. No additional workflow store or scheduler was added; remaining timer tool effects still use their previously documented claims.

Durable-progress source validation: 346 focused tests and 95 adjacent tests passed, including 22 new progress-wake cases. Repository and whitespace checks passed. A direct final run of all 210 Runtime tests failed on the same 12 baseline failure ids, with no additional errors. The complete gate separately stopped with exit 126 when Windows refused to execute the compiled BSP button host test (`Permission denied`); an earlier attempt stopped on the first display-rounding host executable, and directly retrying that executable was also refused. These are failed gates, not acceptance. No ACL or security-policy changes were made. Firmware build, devices, real-model/client three-second response, connection recovery, cross-end acceptance and live upgrade/rollback remain NOT RUN. Source remains Runtime 0.23.1 plus uncommitted changes, not deployed.

**Owner review of request effects (2026-10-24, backend source):** added authenticated `GET/POST /v1/messages/<id>/effects` on baseline `96c7c2b`, advertised by `request_effect_review`. The owner inspects original selections and confirms each disposition using the original operation identity, exact revision and stable review id; unclaimed effects can only be marked not applied. The actor is bound to the authenticated device. New reviews are refused when the permission authority is unavailable or the original owner generation changes. Reviews use the existing request journal, preserve original claims/results and never invoke a model, retry or roll back an effect. One note follows the original request main/task reply scope. Only a durable publication receipt releases that request's idle-handoff block; uncertain helper executions still need separate reconciliation. Lost receipts, duplicate confirmation and restart repair the same note. Ordinary history retention does not resurrect already-reviewed work. The `96c7c2b` baseline retains the original uncertainty and conservatively blocks it without replaying. See the [Runtime contract](../../runtime/README.md#owner-review-of-uncertain-request-effects-source). Client screens belong to the separate frontend batch; no client code changes in this increment.

This increment adds 25 owner-review tests; the final combined regression run passed 466 tests. Repository and whitespace checks passed. Under native Windows UTF-8, all 210 Runtime tests retain one Windows path assertion failure, reproduced on committed baseline `96c7c2b`. A synthetic state sample loaded by that baseline and then returned to the candidate preserved uncertainty without exposing review metadata or invoking any model/helper; this is a host compatibility probe, not live upgrade/rollback acceptance. The complete gate remains failed: earlier C executable launches were denied by Smart App Control; the last attempt passed several C tests and then hit a Zig CRT cache-file read error. ACLs, security policy and tool caches were not changed. Firmware build, devices, real-model rendered latency, reconnect and cross-end acceptance remain NOT RUN.

**R6.2 retained-history batch (2026-10-24, backend source).** `ContextBuilder` derives 32-turn segments from all readable retained canonical/legacy history, without adding a body store or model-generated summaries. Segment identities bind the conversation and content revisions; changed, reset or foreign segments are refused under the original id. Existing recent-history callers keep their 200-turn view, while `recall` can search older retained turns and attributes hits to their segment and turn. The speaking window retains its 8,000-character budget and states retained/quoted/excerpt coverage. Previously, truncating the prompt still marked all selected history as seen. Now only fully quoted turns get receipts after a successful speaking call; excerpts, omitted history, failed calls and decision-only calls do not. Current task/commitment facts still come from current state. Reset boundaries and legacy import remain intact. This is retained-source coverage, not a permanent archive, automatic segment summarization or a new client paging API.

Retained-history validation: 477 combined focused/adjacent tests passed, including 11 new history cases; the separate complete Runtime suite passed 210 tests with one skip (these sets overlap). The environment-override fixture now uses an absolute temporary directory on each platform instead of a POSIX root-relative path on Windows. The shared-history regression now checks honest excerpt receipts; ordinary complete short catch-up quotes preserve the existing Intake golden unchanged. Repository and whitespace checks passed. The complete gate still fails with exit 126: Windows Code Integrity event 3077 confirms that the first compiled C host test was denied by signing policy. Firmware build and device tests are NOT RUN; real-model/client first response within three seconds, connection recovery, multi-end synchronization and live upgrade/rollback are NOT RUN. Source remains Runtime 0.23.1 plus uncommitted changes and is not deployed.

**Background wake fencing (2026-10-25, backend source).** Reminder, progress and memory-upkeep model calls carry the original owner generation through each model phase, tool invocation, native-session/context receipt and publication/checkpoint boundary. Previously, upkeep could mutate memory after an owner change, interrupted reminders could still call tools, and late outputs could save a native session before the outer wake rejected them. Reminder publication also failed to carry the original generation after the model returned. The shared speaking helper and toolbox now accept the wake guard; stale calls are rejected before proceeding. Old callbacks become unusable when the wake completes, and decision-stage mutation callbacks close before the speaking phase starts. A tool that applied before invalidation is retained, with the original reminder uncertainty left for reconciliation. Existing queue, claim and storage formats remain; this is neither an automatic takeover protocol nor completion of all distributed execution fencing.

Wake-fencing validation: four regressions failed before the fix; 15 new fault cases and the final combined 492 focused/adjacent tests passed. The separate complete Runtime suite passed 210 tests with one skip; the sets overlap. Repository and whitespace checks passed. The complete gate passed the previously denied C host-test stage, then failed in the unchanged desktop-handoff symlink fixture with Windows error 1314 (missing symlink privilege). Running that suite against the isolated committed baseline `96c7c2b` reproduced the same error: 15 tests, one error and one skip. cJSON-dependent cases were skipped because ESP-IDF is not activated. These results do not pass the complete gate. Firmware build, device tests, real-model/client first response within three seconds, connection recovery, multi-end synchronization and live upgrade/rollback remain NOT RUN. Source remains Runtime 0.23.1 plus uncommitted changes and is not deployed; App, firmware, client APIs and saved formats are unchanged by this batch.

**Shared timer effects (2026-10-25, backend source).** Reminders and memory upkeep now use `Effects` for saved selections, compare-and-save claims and saved outcomes in their existing source records, without a new database or scheduler. Pending inputs resume without a model decision. Succeeded effects with a lost model reply produce a factual receipt; saved replies, silent decisions and stable delivery-card identity repair publication without regeneration. Resolved/dropped original reminders with unfinished delivery remain recoverable, and unresolved records survive normal retention. Running/unknown or legacy claims without saved inputs stay unconfirmed and block owner handoff in the original conversation. Numbered memory edits bind the saved revision and verify it under the memory lock before replacing/deleting. Source resets/revisions, changed reminder inputs and changed owner generations refuse stale work. Private details stay off client/Mesh projections. The existing request-effect review API does not yet dispose of timer uncertainty.

The reminder's outer claim stays `unknown` for older binaries; the upkeep marker advances with its pending effect checkpoint before invocation. An isolated `96c7c2b` probe loaded both pending sources without a model/tool call or changing the claim files; returning to the candidate resumed the two saved inputs with zero model calls and one effect each. This tests idle rollback/return, not a live upgrade. Preserve a state-directory copy before rollback: old cleanup can overwrite upkeep extensions after new turns arrive; retain/restore the candidate copy instead of merging those writes into pending work.

Timer-effect validation: three regressions failed before implementation; 28 new timer cases and the final combined 520 focused/adjacent tests passed. The separate complete Runtime suite passed 210 tests with one skip; the sets overlap. Repository checks passed over 676 text files and whitespace checks passed. The complete gate again ran the C host tests, then failed in the unchanged desktop-handoff symlink fixture with Windows error 1314, the same baseline failure documented above. cJSON cases were skipped without an activated ESP-IDF environment. Firmware build, device tests, real-model rendered response within three seconds, connection recovery, multi-end synchronization and live upgrade/rollback remain NOT RUN. Runtime source is 0.23.1 plus uncommitted changes, not deployed; client APIs and the default decision mode are unchanged.

**Owner review of timer effects (2026-10-25, backend source).** Authenticated `GET /v1/timers/effects` supplies a bounded outstanding list with revision-bound pagination; `GET/POST /v1/timers/<id>/effects` inspects and confirms retained reminder/upkeep selections. The capability is `timer_effect_review`. It reuses message review identity, exact revision, per-effect disposition, actor, owner-generation and permission checks. Busy original lanes refuse review. No model/effect executes or rolls back. A stable note identifies the result as the owner's confirmation; its card stays waiting until publication. The review is complete only after the note's durable publication and source archive, releasing that source's handoff block. Recorded reviews do not resume old timer effects. Retries, restart, lost projection/receipts and concurrent confirmation repair the same record. The original effects remain unchanged in the existing private promise/upkeep archives; normal future reminders/upkeep continue, without advancing a periodic schedule twice. Archived ids remain queryable after note retention. Legacy/damaged sources lacking selected inputs remain explicitly unavailable. See the [Runtime contract](../../runtime/README.md#owner-review-of-uncertain-timer-effects-source); no client screens or deployment are included here.

Timer-review validation: 28 new review cases and the final combined 548 focused/adjacent tests passed. The separate complete Runtime suite passed 210 tests with one skip; these sets overlap. Repository checks passed over 677 text files and whitespace checks passed. The latest complete gate failed with exit 126 on the first compiled display-rounding C test; Windows Code Integrity event 3077 confirms signing-policy denial. An earlier attempt was denied at the audio-recovery C test. The historical baseline symlink failure above was not reached in this latest run. An isolated synthetic idle rollback/return probe preserved recorded promise/upkeep reviews on baseline `96c7c2b` without a model/tool call; returning to the candidate published and archived both reviews without repeating effects. This is not live upgrade acceptance, and the state-copy rollback caveat above still applies. Firmware build, device tests, real-model rendered response within three seconds, connection recovery, multi-end synchronization and live upgrade/rollback remain NOT RUN. Source is Runtime 0.23.1 plus uncommitted changes, not deployed.

**Helper-result summary fencing (2026-10-25, backend source).** A queued helper-result summary captures the original owner generation before admission. Changed runs/conversations, cancellation, interruption, shutdown and unavailable/conflicting ownership refuse the old summary model, text callbacks, native-session/context receipts and publication. Text callbacks close after completion. Partial and final card writes compare the active run inside the existing card lock. The private copy used by guarded writes preserves absent optional entry fields, keeping the existing public-state golden unchanged. A source-owned delegated task still returns through its executing Runtime. If a summary becomes obsolete, the saved worker result survives and the same running dispatch becomes unknown with an explicit pending-delivery explanation; existing reconciliation or restart recovery publishes it without another model/action. A failed delivery-state checkpoint keeps the original result for restart. Read-only summary-provider failure keeps the existing factual fallback only while the original guard is valid. No new scheduler, store, wire API or persistence format was added. Other helper callbacks and actual external action boundaries still require the remaining R6 review; the owner snapshot here begins when the saved result is queued for summary, not before the original helper invocation.

Summary-fencing validation: six pre-fix fault cases reproduced stale publication/session or interruption failures. The final 18 new cases and combined 575 focused/adjacent tests passed; the complete Runtime stage in the final gate passed 210 tests with one skip (overlapping sets). Repository checks passed over 678 text files and whitespace checks passed. The final complete gate passed the C host tests and failed later in the unchanged desktop-handoff symlink fixture with Windows error 1314, matching the recorded baseline. cJSON cases were skipped without activated ESP-IDF. An earlier concurrent complete Runtime run hit `WorkWithApprovalTests.test_the_owner_says_yes`: the approval could become visible before the card notification finished. Delaying that existing notification reproduced the same gap on both committed baseline `96c7c2b` and candidate; neither approval source nor its assertions were changed. The failed run and baseline/candidate probe remain recorded, separate from the later passing Runtime stage. Firmware build, device tests, rendered first response within three seconds, connection recovery, multi-end synchronization and live upgrade/rollback remain NOT RUN. Source remains Runtime 0.23.1 plus uncommitted changes, not deployed.

**Helper-callback run fencing (2026-10-25, backend source).** Cancelling a task does not stop its helper thread at once, and the same card can be started again under a new run. Callbacks from the replaced run now compare the card's run before writing: native session, observed model and activity events require the same `run_id` (a cancelled run still records its own end), while queue start and supplement relay state require it to be the active run. A late native-session binding amends only its own attempt record. Each round tracks its own approvals; releasing a replaced run withdraws only those and leaves the new run's questions open. Cost accounting still adds to the card. The helper's terminal result compares the run inside the card write, so a result racing a cancel-and-restart cannot land on the new run. A stopped or replaced round can no longer ask the owner (direct questions and stall checks are refused), and its gate key refuses approval requests, capability tools and tool listing before it is revoked at release; questions asked through the gate count as that round's own approvals. No API or persistence format changed.

**Helper-start owner fencing (2026-10-26, backend source).** A helper task can wait in the parallel queue after admission, and while it waits its conversation can pass to another computer without a local handoff: the Mac writes the first main-conversation binding, or a two-writer conflict resolves. Previously the queued worker started anyway. Now, when its turn comes and before the claim and the initial execution binding, the Runtime checks the conversation owner with the same rule as startup recovery (owner is this Runtime or none yet, not unresolved or conflicting; delegated runs still belong to their source Runtime). If the check fails, the helper is not invoked: the card is released from the active set without a terminal result, the saved input stays `pending` under its original run identity, and the card says it is queued because another computer is responsible. A received owner record now also triggers task recovery, so the held input starts once, with no model call, when the conversation returns to this Runtime. A summary fenced by the same change is delivered from its saved result as before. The check and the claim are not one atomic step; ownership cannot change locally while the conversation has an active card (handoff is refused), and the remaining window covers only remote record arrival between them. No API, wire or persistence format changed.

Helper-start validation: three of four new cases (`test_dispatch_owner.py`, added to the static gate) failed before the change; all four passed three times after it. Every Runtime test file was run: ten files fail with the same failure ids on the unchanged baseline `6513e30` and on the candidate (artifacts, desktop approvals/handoff/hook/sync, iterate, model gateway, project brief, quick review — Windows host issues outside this change). The complete 210-test Runtime suite passed with one skip on the comparison run; one failure in an earlier concurrent run was not reproduced and is recorded here unexplained. Repository checks passed over 695 text files and whitespace checks passed. The complete gate, firmware build, devices, real two-computer ownership changes, three-second response and live upgrade/rollback were NOT RUN. Source is not deployed.

**Status check (2026-10-26).** Client screens for human review exist on both clients: the phone and the desktop client review uncertain request effects and timer effects, and ask the Runtime to recheck an unknown task run (`execution_reconciliation`). Claude Code helper runs stay without a native-result query on purpose. Their transcripts mark a turn's prompt (`promptId`) but carry no explicit turn-complete record: a turn ends with one or two `end_turn` or `stop_sequence` assistant messages followed by internal bookkeeping records, and an interrupted run can look the same. Under the rule above, a final-looking message cannot establish success, so an unknown Claude Code run goes to the owner's review instead of being settled from its transcript.

R6 remains incomplete: segment-summary policy, long-history relevance/latency acceptance and the default-flow cutover remain, and each needs an owner decision or real-model and client measurements. Owner checks now cover request, timer, summary and helper-start paths; later rounds of an already claimed run continue under that claim. The JSON compatibility path and `decide_first=false` are retained until real client/model acceptance permits cutover. R7 moved the files into layers and removed the import cycles (below); R8 removal of still-used client APIs has not started. Source is not deployed; real accounts, devices, three-second response and live cross-end recovery are not accepted. Preserve a state-directory copy before upgrading. Older programs ignore the new recovery and context fields; rollback must retain that state copy and must not overwrite pending inputs or reset boundaries.

**R7 file move (2026-10-26, backend source).** The owner chose to continue before the latency measurements. Modules moved, without code changes, into the six layer packages: `events/` (intake, record, message and operation logs, ledger, speech and image input, external reports, and the desktop-session adapters), `world/` (world, store, cards, conversations and their directory and metadata, artifacts, ownership, devices, identity, approvals, quick review, promises, memory, task titles, task audit, usage), `delivery/` (presentation, mesh, mesh shares), `loop/` (xiaoyou, loop, context, decision, voice tools, effects, proactive, recall, router, workspace, project brief), `helpers/` (agents, tasks, helper catalog, extras and models, capabilities, MCP client, permission MCP, Codex/Claude/dsh adapters, model gateway, desktop handoff) and `api/` (server, Service, conformance, conversation CLI, shared history). Config, firmware, releases, Windows file helpers, `__main__` and `iterate/` stay at the package root. Imports, tests, tools and documentation paths follow the new names; `git mv` keeps file history. Five old paths stay runnable because they are written into installed configuration or instructions: `python -m xiaoyou_runtime.desktop_hook` (hook commands, which are still generated with this name so re-merging does not duplicate them), `python -m xiaoyou_runtime.permission_mcp`, `python -m xiaoyou_runtime.conformance`, `runtime/xiaoyou_runtime/shared_history.py` (hook commands and AGENTS.md; `setup-history-sync` still recognizes its hooks) and `runtime/xiaoyou_runtime/project_brief.py` (helper `command` settings). No wire, API or persistence format changed.

Move validation: every module imports, and every old and new entry point runs. All Runtime test files were run on the committed baseline `07f4a9b` and on the candidate: the failure ids are identical except two HTTP tests in `test_runtime.py` that failed on the baseline run only (the known Windows connection aborts) and pass on the candidate. One candidate-only failure was found and fixed: the shared-history installer recognizes its hooks by the original reader path, so hooks keep naming it. Repository checks pass. The two import cycles that exist only through function-level imports (around `helpers/agents.py`, and `world/store.py` -> `loop/context.py` -> `world/cards.py` -> `world/artifacts.py` -> `events/intake.py` -> `events/record.py`) are not removed yet. Device tests, the complete gate and deployment were NOT RUN.

**R7 import cycles (2026-10-26, backend source).** Three cycles existed only through imports inside functions; all three are removed, and `test_import_layers.py` (in the static gate) now fails on any cycle, counting function-level imports. (1) `helpers/agents.py` held both the shared types and the concrete agents, and imported the protocol adapters that import those types back. The shared types (`Job`, `Outcome`, `Control`, `Watchdog`, `AgentError`, the command runners and the `Agent` base class) moved to `helpers/base.py`; `agents.py` re-exports them under the same names, and the adapters (`codex_app`, `dsh_app`, `dsh_stream`, `claude_gateway`, `loop/decision`, `loop/effects`) import them from `base`. `create()` moved to `helpers/factory.py`, because it needs `claude_gateway`, which subclasses `ClaudeCodeAgent`. (2) `world/store.py` built the loop's `ContextBuilder`: `loop/context.bind(store, cards, ...)` now builds it and hands it to `Store.use_context`. `world/artifacts.py` wrote attachments through Intake: that step is `events/intake.attach_artifacts`, used by the HTTP endpoint. (3) In `iterate/`, built-in plugins imported `Skip` from the plugin registry that loads them; `Skip` now lives in `iterate/objects.py` and `plugins.Skip` still names it. No behavior, API or stored format changed.

At that checkpoint, lower layers still imported upper ones without forming a cycle (addressed in the next batch): `events/desktop_content` -> `loop/project_brief`, `world/approvals` -> `helpers/capabilities`, `world/conversation_metadata` -> `helpers/model_gateway`, `world/memory` and `world/promises` -> `loop/effects`, and `world/usage` -> `helpers/agents`, `claude_gateway` and `codex_app`.

Cycle validation: every module imports and every entry point runs. The cycle check finds all three cycles on the previous commit `cf7e243` and none now. All Runtime test files were run again: the failure ids match the `07f4a9b` baseline run, apart from the same two HTTP tests in `test_runtime.py` that failed only on that baseline run and pass here. A first candidate run found test helpers that still called `agents.create`; they now use `factory.create`. `test_ttft_report` now stops the Runtime before comparing state files, after one unrepeated failure while the Runtime was still running. Repository checks pass. Device tests, the complete gate and deployment were NOT RUN.

### R7 dependency cleanup on macOS (2026-10-26)

Continued from Windows commit `d2121a8` in a separate checkout, without changing the running Runtime. All eight direct imports from `events/` or `world/` into `helpers/` or `loop/` are removed. `test_import_layers.py` now also rejects imports from these two lower layers into `helpers/`, `loop/`, `api/` or `delivery/`, including imports inside functions. Before implementation, the new assertion reported exactly the eight retained edges; the existing cycle assertion remains in place. This checks direct Python imports, not a complete transitive layering policy for root configuration modules.

Shared exception contracts live in `errors.py`; the original modules re-export the same classes, preserving exception identity and `EffectUncertain`'s `AgentError` inheritance. `context_markers.py` owns recap recognition, and `provider_binding.py` owns the unchanged DeepSeek binding predicate. The original helper script paths remain runnable without installing the package.

`world/usage.py` retains account classification, normalization, snapshots, persistence and refresh coordination. `helpers/usage.py` owns the unchanged Claude/Codex process and network probes; CLI composition injects them with `create_usage`. An active projection without injected probes fails explicitly instead of silently skipping quota checks. Passive projections still start no helpers. The service constructs the metadata gateway in `helpers/model_gateway.py` and injects it into `ConversationMetadata`, preserving opt-in routing, the Astra exclusion, two candidates and eight-second provider timeouts. Wire formats, stored data, permissions and the default decision mode are unchanged.

The two baseline macOS fixture failures were reproduced separately. The skill-path assertion now compares resolved paths. The connection fixture now sets POSIX mode `0600`; its actual failure was the permission check, not the temporary-directory alias. A separate regression proves that mode `0644` is still refused. An injected-probe test also proves that a quota refresh need not start a helper process. All 110 package modules import, original exception identities remain intact, and both old and new project-brief script paths run with matching help output.

The complete Runtime suite passed all 1,089 tests in 166.161 seconds. Source versions are Runtime `0.23.1` at `d2121a8` plus this batch and App `0.32.8` (68); these are not installed-version claims. Live model/client regression, rendered first response within three seconds, connection recovery, multi-end synchronization and device tests are NOT RUN. No running Runtime was restarted or redeployed. The batch adds no data migration: source rollback is the retained `d2121a8` checkout; the earlier R6 state-copy precautions still apply to an eventual upgrade or rollback. R6 live acceptance/default-mode switching and R8 client migration remain pending.

The complete repository gate and a separate debug-archive verification passed: host C tests, firmware-update interop, Runtime checks, firmware build, merged image and partition validation. The gate used ESP-IDF `5.5.3`, JDK `17.0.20.1`, and the existing host Python `3.9.6` with Pillow `11.3.0`; IDF kept its own Python environment. Earlier attempts stopped on the unselected JDK and missing Pillow in IDF's environment. The optional pinned Pillow `13.0.0` requires Python 3.11 and was not installed or tested here; no dependency pin was changed.

The validation-only firmware archive is `build/firmware/83a6c81b24f9c2d453ed1d077705616e571f01c1b63556b428918cec974cf806/`, with full-image SHA-256 `83a6c81b24f9c2d453ed1d077705616e571f01c1b63556b428918cec974cf806` and matching ELF SHA-256 `2f8b6418955ea38dbe31462f05b021ba156c03dc836187d8e94f1c32cda55a30`. Its descriptor reports `companion-build-66-19-gd2121a8-`, target ESP32-C3 and IDF `v5.5.3`. Firmware source was unchanged and no device was flashed. Build and host regression: PASS; device and live timing/recovery/synchronization acceptance: NOT RUN. The final focused check passed 77 tests after formatting and standalone-import cleanup (overlaps the full suite).


### R8 negotiated client migration (2026-10-26)

Runtime `0.24.0`, Android `0.33.0` (69), desktop `0.1.9` and remote helpers negotiate `sync_requests_v1`. Accepted requests join `/v1/sync` through an optional list of at most 32 ids. The projection exposes public request fields and canonical message snapshots, with a 512 KiB target budget, explicit missing/deferred ids and progress for a single oversized item. Current snapshots retain their revisions and do not advance the paginated history cursor. Stage-only changes wake the subscription. Requests remain bound to their Runtime and conversation; missing state never resubmits the accepted operation.

Android and desktop add accepted ids to their existing sync connection, interrupting only that sync read. Approval monitoring now merges sync deltas, retaining unrelated pending approvals and removing answered/withdrawn ones. Source identity, credential and connection-generation checks fence late responses. Older Runtime peers retain supported per-message reads; desktop confirms identity/capabilities before a rollback switches protocols. The server's feed/wait/stream adapters remain: R8 deletion still requires installed-client migration and an enforced minimum version. The R6 summary policy, long-history acceptance and default-flow cutover also remain; `decide_first=false` is unchanged.

Validation: the full Runtime suite passed **1,096 tests**, Android passed **218 tests** and all ABI APKs built, and desktop passed **87 tests** and its TypeScript/UI build. The complete repository/host/ESP-IDF gate passed. A newly generated frozen Runtime initially missed the existing eight-second fixture startup limit; the unchanged fixture then passed on retry. This cold-start failure is retained, not a timing acceptance pass. The macOS arm64 package contains the matching Runtime `0.24.0` bundle; it is an unsigned private candidate. The existing installed desktop was verified as `0.1.7` and was not replaced. Runtime source SHA-256 is `45812d6f6d392c22c3c6eef361a971676e356b8f0c284e8ce2cb33b37ce191ca`; desktop source SHA-256 is `c5196e60467c2212cd9521f018dcc0a68868978d104371b830c6016cd2f53890`. Firmware source is unchanged and no board was flashed. The validation image SHA-256 is `b4db812ad871b0a70efd2602402feb6a4d497c892a96cc26a4f5dd32fec25195`, with matching ELF SHA-256 `73bc127efec4731856d280899ea9353bbbeca4af543d3b8de8870bcf955eb14a`.

The idle Mac Runtime was upgraded from `0.23.1` to `0.24.0` after an offline state-directory clone, retaining the original config and host identity. A `d2121a8` source archive and the pre-upgrade state copy remain outside Git for recovery. Rollback must reconcile any work accepted since the backup; do not overwrite the live state with an old copy. The original checkout/branch was not switched. Android was published locally through `app publish`. After USB reconnection, the phone was found using the Windows entry; selecting its already-paired Mac HTTPS route made `0.33.0` available through the normal in-app download. Install and on-phone acceptance still await the owner's system confirmation.

Live evidence uses the existing dsh/DeepSeek configuration and loopback HTTP. Two isolated desktop core clients converged on one real-model final reply, with one POST, eight request-sync reads, zero per-message reads and 30 ms cache recovery on return; the reply reached the sender cache in 2,698 ms. A packaged two-window smoke test rendered its first complete meaningful bubble in 2,829 ms. A separate **20-send** packaged run converged on the same final records in both windows, but **three samples missed 3,000 ms**: 3,112 / 3,812 / 3,030 ms. TTFT p50 was 2,679.5 ms, nearest-rank p95 3,112 ms and maximum 3,812 ms. These are all attempts, with no dropped slow sample. The slowest request's first Runtime partial was at 3,452 ms and its server completion at 3,609 ms; server queueing was 21 ms. Desktop is additionally removing the unconditional 80 ms pause after changed sync pages; unchanged responses still back off and new sends can interrupt that pause. The recorded 20 samples precede that follow-up. A separate 20-send run of the final package recorded p50 2,580 ms, p95 3,210 ms and maximum 3,531 ms, with two misses (3,531 / 3,210 ms) and identical final records in both windows. Earlier failures remain part of the evidence. The real configured model is `deepseek-v4-pro`. Three process restarts of the final packaged client retained its pairing and all 20 final test replies, with launch-to-connected-rendered times of 1,623 / 467 / 411 ms and no repair. This is restart recovery on this Mac loopback path, not public/relay/phone recovery acceptance.

Build and host regression: PASS. Limited real-model desktop rendering/convergence: run with the above timing failures. Full first-response acceptance: FAIL. Phone text/voice, firmware/BLE, long histories, public/relay real-model timing, and three repetitions of all relevant transition/fault scenarios: NOT RUN. Host fault tests and a cache-recovery probe do not establish the full live recovery/multi-end contract. R6 and final R8 adapter deletion remain unaccepted.


### R6 real-provider cutover probe (2026-10-26)

Tested existing `decide_first=true` code from `c20e968` through the final packaged macOS desktop `0.1.9` and Runtime `0.24.0`. Each candidate used a fresh private state directory, loopback-only listener, a fresh credential, isolated dsh/session directories and the same persona/helper definitions. Mesh, desktop observation, usage polling and iteration were disabled to avoid duplicate background work. Production configuration, model selection, state, pairing and the live Runtime were unchanged. These short synthetic conversations do not establish long-history, phone, helper-effect, or multi-computer acceptance.

Two complete **20-send** runs rendered real model responses in two windows with matching final records. With `deepseek-v4-pro` for chat, all 20 exceeded 3,000 ms: p50 **4,044.5 ms**, nearest-rank p95 **5,278 ms**, maximum **5,346 ms**. A second isolated candidate used the existing managed-client setting `chat_model=deepseek-flash`, retaining `deepseek-v4-pro` for tasks; all 20 again exceeded 3,000 ms: p50 **3,512 ms**, p95 **4,597 ms**, maximum **5,171 ms**. In each run, all 20 decisions hit the existing 1.3-second timeout, then continued through its supported no-commitment speaking fallback. Thus this evidence does not validate executing commitments in the candidate path, even though reply-only finals converged. No attempt or timeout was excluded, no deadline was extended, and no model setting was promoted to production.

The isolated processes were stopped and their temporary configuration files removed. Sanitized sample/stage timing records remain in private acceptance artifacts outside Git. The default remains `decide_first=false`; R6 needs a decision/response path that passes both commitment behavior and rendered latency before cutover. After the owner asked to continue phone acceptance, USB discovery again showed no phone. Android `0.33.0` (69) installation and on-phone message/recovery checks remain unverified until the phone reconnects and installation is confirmed. This checkpoint adds measurements and documentation only; it does not change a build or replace the prior passing host-test results.

## 8. Lessons from Raft and DS-Bot-App

The owner asked to learn from the design logic of two open-source projects without copying them.
**Correction (2026-10-24):** the "Raft" the owner meant is [Raft](https://raft.build/), a platform where people and AI
agents work as one team, not the etcd-io/raft consensus library. 8.1 borrows from etcd-io/raft and stays
valid for what it covers, keeping data consistent across computers; what we take from raft.build is in 8.4.
This section has been corrected after [Codex's review](runtime-refactor-review.md): the conditions the
borrowing had left out are added, and statements such as "the epoch plays the role of the term" and
"a timeout allows a safe handover" are removed.

### 8.1 etcd-io/raft (data consistency across computers)

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

### 8.4 Raft (raft.build): coordination and capabilities

Raft's agents are visible teammates in shared channels who hand work to each other. We keep one voice
to the owner (P1); only what strengthens Xiaoyou's own coordination is taken now (decided by the owner
on 10-24). Collaboration between helpers and multi-agent rooms are deferred until there is a need;
today the helpers are Claude Code and Codex.

| Raft | Taken now | Step |
| --- | --- | --- |
| Managed MCP: registered once, connections kept by Raft and never seen by agents, per-agent grants, one injected proxy, versioned calls | Yes: a capability library per computer, see [helper capabilities](helper-capabilities.md) | Capabilities phase 1-3 |
| Skills stay with the runtime and are discovered and shown | Yes, plus optional per-helper skill directories | Capabilities phase 1, 3 |
| A task has one owner; "assign" and "I started" are separate; writes carry an expected revision | Xiaoyou assigns; a task shows as running only once the helper has actually started; task writes keep rejecting stale revisions | R6 (`Executor`) |
| A durable inbox; wake-ups carry no content; a batch is acknowledged by the next pull; repeats are expected | Wake rules read from the event log; a wake is idempotent and never carries the decision itself | R6 (wake rules) |
| The runtime reports `online / thinking / working / error / offline`; the newest report wins | Helper status is reported, not inferred, and ordered by the time it happened | R6 |
| An agent cannot create agents or widen its own permissions; it prepares a card for a human | Xiaoyou may propose adding a helper or a capability; the owner confirms | Capabilities phase 3 |
| Agents talk to each other, review each other, split work in parallel in shared rooms | Deferred | — |
