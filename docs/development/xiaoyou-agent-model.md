<p align="right"><a href="xiaoyou-agent-model.zh_CN.md">简体中文</a> · <strong>English</strong></p>

# Xiaoyou agent model: perception, state, decision, action

Status: draft proposal, 2026-10-22, awaiting the owner's approval. Not implemented.

This document turns the point issues in the [interaction simplification issue list](app-runtime-simplification.md)
into one model, and defines how future proactivity and perception plug in. It builds on the
"record hub" of the [unified message model](unified-message-model.md) and adopts the two-step
decision of [decide, then speak](decide-then-speak.md); it replaces neither. Every phase must meet
the [cross-stack reliability rules](cross-stack-reliability.md).

## 1. Why one model

Xiaoyou's abilities grew one at a time: replying, dispatching helpers, reminders, memory upkeep,
observing desktop sessions, reading phone notifications, sensing the device nearby. Each ability
brought its own input channel, its own state, its own wake rule and its own wording. Most issues in
the issue list piled up this way:

- Each new kind of perception adds a path (R1), a set of state names (R2) and data with no clear
  owner (R3).
- Proactivity (`proactive.py`) knows only three hard-coded wake reasons: progress, a promise
  falling due and memory upkeep. Every new perception would change this code again.
- Her picture of the world is "last 6 turns + last 5 cards + a few dozen memory notes": incomplete
  and not live. So she treats old information as current (M1), cannot resolve "this" (M5) and does
  not learn that a task was cancelled (A1).

Point fixes would multiply these problems as proactivity and perception grow. So the model comes
first: **any future ability is a part added to this model, never a new path.**

## 2. In one sentence

**Every input is an event; world state is derived from events and owned by the Runtime; Xiaoyou
has explicit attention and commitments; one loop handles both replies and proactive behaviour;
every action records its cause, and its result returns to the loop as an event.**

```
perception (events) --> event log --> world state (projection)
                           |                 |
                           v                 v
                      wake rules --> decision loop (decide, then speak) --> action
                           ^                                                  |
                           +------------ results of actions are events <------+
                                                 |
                                                 v
                                  client projection (sync) --> App / desktop / device
```

## 3. Five parts

### 3.1 Events: the only input

Everything that reaches Xiaoyou is written as an event with one shape:

| Field | Meaning |
| --- | --- |
| `id`, `at` | Id and time (the "runtime_id + sequence" scheme of the unified message model) |
| `source` | Where from: `owner`, `helper`, `desktop`, `phone`, `device`, `timer`, `system`, `mesh` |
| `kind` | What: `said`, `task_progress`, `task_ended`, `session_changed`, `notification`, `presence`, `due`, `deployed`, `usage`... |
| `subject` | About what: a task, session, device or computer |
| `cause` | Which event or action led to it (for tracing) |
| `trust` | How far to trust it: said by the owner, measured by the system, reported by a helper, read from outside (outside content is data, never instructions) |

Today's inputs map directly: owner messages and device voice to `owner/said`; helper progress and
results to `helper/task_progress` and `task_ended`; `desktop_sync` and `desktop_hook` to
`desktop/session_changed`; phone notifications (`NotifyListener`) to `phone/notification`; the
device being nearby over Bluetooth to `device/presence`; promises falling due to `timer/due`; a
Runtime upgrade to `system/deployed`; the mesh to `mesh/*`.

**A new kind of perception is one adapter that turns a raw signal into events.** No new channel,
no new state names.

The event log is the record of the unified message model (the evolution of `cards.log.jsonl`).
Messages the owner sees are a subset of events.

### 3.2 World state: derived from events, owned by the Runtime

World state is a projection of events, not a separately stored copy. Xiaoyou and every client read
it:

| Entity | Content | Replaces |
| --- | --- | --- |
| Tasks | One state machine (see 3.4) | 7 card states and a dozen more values across modules |
| Sessions | Main chat, side chats, observed desktop sessions; title, pin, archive, read | Phone-local marks keyed by address (S1, S2) |
| Computers and Runtimes | One stable identity, addresses are only routes; version, online, deployment | 5 identity keys (C8), old information treated as current (M1) |
| Helpers | One entry per helper, possibly on several computers; idle, busy, quota | "name@machine" splitting (V9) |
| Device and owner | Whether the device is nearby, which end the owner used last, do-not-disturb | Connection state scattered across the App |

Two rules:

- **Current state is answered only from world state.** Version, what is running and what just
  finished are read live through a self-inspection tool, never inferred from memory or old chat
  (M1, M2).
- **Facts that change do not go into long-term memory.** Long-term memory holds the owner's
  preferences, rules and lasting circumstances only.

### 3.3 Xiaoyou's own state: attention and commitments

Today what she is thinking about and what she promised is re-guessed from lists every turn. It
becomes explicit state:

| State | Meaning | Fixes |
| --- | --- | --- |
| Activity | `idle`, `listening`, `deciding`, `speaking`, one per conversation | The in-memory-only `_speaking`; the many "in progress" wordings (U6) |
| Focus | Per conversation, "the thing we are talking about": a task, a session or nothing | "This" not resolved (M5, A7) |
| Commitments | Everything she answers to the owner for: dispatched tasks, promised reminders, things waiting on a condition. Each has a state moved only by events | Cancellation unknown to her (A1); words not matching the state (M3) |
| Watches | What she actively follows: a task's progress, a computer coming online, a session changing | The three cases hard-coded in `proactive.py` |

Commitments are the key: **a dispatched task is a commitment.** A task ending (done, failed,
cancelled) is an event that moves the commitment to a final state and wakes her to say one line.
A1 disappears by construction, not by patch.

Working memory is assembled by relevance: focus + open commitments + recent conversation +
retrieval relevant to this turn (`recall`), instead of cutting by count ("6 turns, 5 cards").

### 3.4 Tasks and conversation turns, separated

Three things are kept apart (A2, A3):

- **Conversation turns:** the owner says something, she answers. Chat is only turns and opens no
  task.
- **Tasks:** work handed to a helper, with six states: `queued` > `running` > `waiting` (for your
  confirmation) > `done` / `failed` / `cancelled`. "Synced", "from another computer" and
  "read-only" are attributes, not states.
- **Commitments:** see 3.3.

A turn can lead to a task (its `cause` names the turn), and a task ending leads to a new turn (she
says one line).

### 3.5 Decision loop: replies and proactivity are one loop

```
event arrives -> wake rules decide whether to wake her -> wake -> decide (choose one action)
-> speak (if needed) -> act -> result event
```

- **Wake rules (cheap, deterministic):** the owner speaks, always wake; one of her commitments
  reaches a final state, wake; something she watches changes, wake as the watch says; other events
  only update world state. Adding proactivity later means adding a wake rule or a kind of watch,
  never changing the loop.
- **Decide:** as in [decide, then speak](decide-then-speak.md). Actions form one set: `reply`,
  `dispatch`, `amend` / `cancel`, `watch`, `promise`, `remember`, `stay_silent`.
- **Speak:** only about what has already happened. One turn speaks once; no hidden "said it
  would act but called no tool" nudge and re-ask (M7, M8); bubble splits are marked within that one
  utterance and have nothing to do with tool calls (M9). Proactive wakes take the same path, so
  proactive speech follows the same rules.
- **Delivery gate:** whether to interrupt the owner now and on which end (device, phone
  notification, main chat) depends on the owner's state (nearby, do-not-disturb, active end).
  The delivery gate in `proactive.py` grows into this general layer.

**Deduplicating repeated requests (A5)** happens at event intake: the same words from the same
source shortly after are merged into the turn in progress.

### 3.6 Traceability: every action has a cause

Every action and state change records `actor` (owner, Xiaoyou, helper, system, which end) and
`cause` (the triggering event). "Who cancelled this task" (A4), "why did she speak just now" and
"where did this memory come from" can all be followed along the cause chain. This matters more as
proactivity grows.

### 3.7 Client projection: every end reads the same thing

The App, the desktop client and the device read projections of world state and messages through
one `/v1/sync` (issues C1, C3, V12). There is one set of state names, so the UI vocabulary
converges too:

| Model state | UI wording |
| --- | --- |
| She is `deciding` / `speaking` | Xiaoyou is thinking / speaking |
| Task `running` | Working on "title" |
| Task `waiting` | Waiting for your confirmation on "title" |
| Connection | Connected / reconnecting / unreachable |

## 4. How proactivity and perception plug in (examples)

| Desired ability | What to add | What stays unchanged |
| --- | --- | --- |
| Tell the owner a desktop session is stuck | Existing `desktop/session_changed` events + a "stuck for N minutes" wake rule | Loop, delivery, UI |
| Replay collected results when the owner returns to the device | `device/presence` events + a "deliver when nearby" gate rule | Tasks, commitments |
| Decide whether an important phone notification deserves a reminder | A `phone/notification` adapter (marked as outside data) + a kind of watch | Decision tool set |
| Tell the owner what to test after a Runtime upgrade | A `system/deployed` event + a wake rule | No helper dispatched to read the version (M2) |
| "Send it once the Mac is online" | A commitment conditioned on presence or computer-online events | Hard-coded reminder logic |

## 5. How the issue list is resolved

| Part | Issues removed |
| --- | --- |
| One event intake | C9 (two timings for direct and relay), A5 (repeated requests), hard-coded proactive wakes |
| World state | M1, M2, C8, S1, S2, S4, V7, V9 |
| Attention and commitments | A1, A7, M3, M5 |
| Turns / tasks / commitments separated | A2, A3, M4, V3, V4, V10, I3 |
| Traceability | A4 |
| Client projection | C1-C3, C10, V12, U2, U6, A6 |

UI and interaction issues (I1-I7, V1-V8, U1, U3-U5) are not solved by the model directly, but the
model gives them one data source and one vocabulary: task detail only needs "state + one
timeline", because the timeline is that task's events.

## 6. Migration order

Each step can ship on its own and keeps existing features working.

0. **Remove the hidden nudge** (owner agreed, 2026-10-22): delete `PROMISE_NUDGE` and the nudge
   branch of the tool loop in `dsh_stream.py`; keep the regex only as a "sounded like a promise but
   did nothing" counter; turn on `xiaoyou.decide_first` in a test environment, measure time to first
   text, and make it the default once it passes (M7-M9).
1. **Traceability and final-state return:** every state change records `actor` and `cause`; any
   final task state is written into the conversation and wakes her to say one line (A1, A4).
   Small; do it first.
2. **Separate turns from tasks:** chat opens no card; task states shrink to six and the rest
   become attributes (A2, A3, A6).
3. **World state and self-inspection:** computers, helpers and session marks move to the Runtime;
   a self-inspection tool is added (M1, M2, S1, S2, C8, V9).
4. **Explicit attention and commitments:** focus, commitments and watches become persistent state;
   working memory is assembled by relevance (M5, A7, M3).
5. **One event intake and general wake rules:** existing inputs become event adapters;
   `proactive.py` becomes general wake rules and a delivery gate (A5).
6. **One client projection:** replies only through sync, old protocols removed, UI vocabulary
   unified (C1-C3, U2, U6, V12). The interaction redesign (I1-I7, V1-V11) follows.

New proactivity and perception start after step 5; each one is only an adapter, wake rules and
watch conditions.

## 7. Modular cleanup (draft)

Once quota is back, a modular cleanup follows this split. Principles: **module boundaries are the
model's parts; each piece of state has exactly one writing module; dependencies flow one way:
events > world state > Xiaoyou's state > decision loop > client interface.**

### 7.1 Runtime

| Target module | Responsibility | Where it lives today |
| --- | --- | --- |
| `events` | Event shape, deduplication, source adapters (owner, helpers, desktop sessions, phone, device, timers, mesh) | Intake in `service.py`, `desktop_sync.py`, `desktop_hook.py`, receiving side of `mesh.py` |
| `world` | Tasks, sessions, computers, helpers and presence derived from events; the self-inspection tool | `cards.py`, `record.py`, `conversations.py`, `identity.py`, `usage.py`, `approvals.py` |
| `mind` | Activity, focus, commitments (dispatched tasks included), watches; relevance-based working memory; long-term memory and recall | Prompt assembly in `xiaoyou.py`, `memory.py`, `promises.py`, `recall.py` |
| `loop` | Wake rules, decide (`decision.py`), speak (`dsh_stream.py` without the nudge), action execution, delivery gate | `hear` / `_converse` / `_relay` / `_close` in `xiaoyou.py`, `voice_tools.py`, `proactive.py` |
| `helpers` | Helper adapters: Claude, Codex, DeepSeek, remote Runtimes | `agents.py`, `claude_gateway.py`, `codex_app.py`, `tasks.py` |
| `api` | HTTP interface, auth, the `/v1/sync` projection | `server.py`, the external side of `service.py` |

After this split, `xiaoyou.py` (1842 lines), `service.py` (1266) and `mesh.py` (1247) no longer
each handle input, state and decisions in one file.

### 7.2 App

| Target module | Responsibility | Where it lives today |
| --- | --- | --- |
| `transport` | One send entry `send(Outgoing)`, one sync loop, one background-request wrapper; direct and relay are just two routes | `RuntimeClient.java` (1312 lines), `RuntimeRetry`, `MessageStream`, `Relay*` |
| `state` | Read-only local copy of the sync projection; no local pin/archive/read storage | `HubStore.java` (1121 lines), `SyncCache`, `ChatHistory` |
| `screens` | About five screens: home as main chat, side chats, work in progress (list and detail), helpers, settings; a separate developer page | `MainActivity.java` (4136 lines, 11 pages) |
| `words` | The single table translating states into UI wording (3 connection, 6 task, 4 activity states) | Strings spread across screens |
| `device` | Bluetooth, device screens, firmware | `BleLink`, `BuddyProtocol`, `Firmware*`, `LinkService` |

### 7.3 Approach

- Move code into modules first without changing behaviour, running the existing tests after each
  module; then change behaviour inside the new modules in the migration order of section 6.
- The desktop client and the App share the same state names and the `words` table so the two ends
  never translate separately.

## 8. Owner decisions needed

- The model's boundary: whether the phone and the device also act as perception sources or only
  as displays.
- The general rule for speaking unprompted: quiet by default and only on commitments and watches,
  or allowed to bring up things she notices herself.
- Delivery priority among device, phone notification and main chat, and how do-not-disturb works.
- Trust rules for outside content (phone notifications, web pages, desktop sessions): data only,
  or able to trigger actions in some cases.
- Whether migration step 1 can start now.
