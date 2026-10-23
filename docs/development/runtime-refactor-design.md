<p align="right"><a href="runtime-refactor-design.zh_CN.md">简体中文</a> · <strong>English</strong></p>

# Runtime three-layer refactor design

Status: design approved, 2026-10-23. Implemented in steps R1-R8 of section 4; not started yet.

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
                                              |      | speak -> act        |
                                              |      +---------+-----------+
                                              |                | results are events
                                              v                v
                                   +------ 2. Reply layer ------+
                                   | snapshot + delta /v1/sync | ---> phone / desktop / device
                                   | delivery rules            |
                                   +---------------------------+
```

Three rules hold throughout:

1. **Only Intake writes the event log.** Other modules read the current state and change it only
   by submitting events.
2. **Store before acting.** Every input, decision and result is persisted as an event before
   anything follows from it; after a restart the log is replayed and accepted work is not redone.
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
| `id` | Globally unique: client requests use their request id (`client_id`), everything else `runtime_id` + local sequence; resubmitting the same `id` returns the same result (decided: dedupe by id only) |
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

### 1.3 Rules per source

- **Owner messages:** `accept` first, then queue into the conversation; the receipt only says
  "received, id X".
- **Helpers:** progress only updates the task's progress fields and never writes the main chat;
  every ending emits `task_ended` (done / failed / cancelled) for the agent loop to finish (A1).
- **Desktop sessions:** written only into their own observed session, never into the main chat,
  never waking Xiaoyou; re-observing a session emits nothing new; recap material never quotes
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
  approvals, quota and Xiaoyou's state; deltas follow from the current cursor. A computer switch or
  restart becomes usable in well under a second instead of catching up 415 pages (N10). Older
  history is paged on demand (`GET /v1/conversations/{id}/history`).
- **Pages are sized by count**, no longer 48 KB; oversized items (long replies, attachments) carry a
  summary and a reference.
- **Clients persist their cursor** and resume after a restart.
- **Replies in progress** are `assistant_message` events with `status=streaming`, each replacing the
  whole text; once `final`, no older fragment may overwrite it. Clients no longer run a separate
  wait loop or stream per message (C1, C9).
- **Long polling:** `wait` returns as soon as anything changes; switching to SSE or WebSocket later
  only changes the transport, not the event format.

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
                             4. Speak: one streamed call, read-only tools, only about what happened
                             5. Act: dispatch a helper (pick the computer), amend, cancel...;
                                results return to layer 1 as events
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
Executor.apply(action) -> events             # dispatch / amend / cancel / watch / promise / remember
HelperPort.run(job, control) -> progress and result events  # Claude Code, Codex, DeepSeek, remote nodes
```

### 3.3 Mapping to existing code

| Now | Later |
| --- | --- |
| `Xiaoyou.hear`, `routing.decide` (named agent, router, default) | `Loop.step`; a named agent becomes a constraint in the context for `Decider` |
| `_converse` / `_converse_with`: tool talk, the JSON fallback, re-asking | Two stages, `Decider` + `Speaker`; the JSON fallback and re-asking are removed |
| `_converse_decided`, `decision.py` (`decide_first`, off by default) | Becomes the only path (once time to first text is measured and passes) |
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
| R2 | Introduce `Intake`, first as a wrapper around `Record`; move write sites to `accept` one by one (HTTP, then helpers, then desktop sessions, then other computers) | The log matches the pre-change log record by record (differential tests); desktop sessions no longer reach the main chat | One state |
| R3 | Introduce the `World` projection; `messages.json` and `cards.json` become rebuildable snapshots; stop writing `transcript.json` | Deleting the snapshots and restarting restores the full state; accepted work is not redone | One state |
| R4 | Sync becomes snapshot + delta with paging by count; clients persist their cursor | After a computer switch or restart the latest state shows within 1 second; task results no longer wait ten minutes (N10) | Automatic connection |
| R5 | Replies become `assistant_message` events; the projection completes formatting runs | A new client shows streamed replies and formatting from `/v1/sync` alone (N13) | One state |
| R6 | Agent loop: measure and default to decide, then speak; remove the JSON fallback and re-asking; wake rules replace Pulse; `ContextBuilder` | First response within 3 seconds; one request, one reply; "this" resolves | Words match actions |
| R7 | Move files into the layers: `events/`, `world/`, `delivery/`, `loop/`, `helpers/`, `api/`; remove import cycles | Move only, all tests pass | Modular cleanup (decided to follow the contracts) |
| R8 | After clients switch, remove `/v1/feed`, per-message wait and stream, and old compatibility | No callers of old endpoints; minimum versions enforced | Converge the interface |

R1 can start now; R2-R5 are the core; R6 needs real-model measurements; R7 follows once behaviour
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
- **The Runtime running during the change:** no step requires a restart to roll back; deploying
  (restarting) is the owner's call.

## 6. Decided (2026-10-23)

- The snapshot carries the latest 50 messages per conversation.
- R4 keeps long polling for now; a push stream waits until the event format is stable.
- R7 uses the six directory names above: `events/`, `world/`, `delivery/`, `loop/`, `helpers/`, `api/`.
