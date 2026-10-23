<p align="right"><a href="xiaoyou-agent-model.zh_CN.md">简体中文</a> · <strong>English</strong></p>

# Xiaoyou complete plan: a simple agent, connection and interface

Status: complete plan, 2026-10-23, approved by the owner (decisions in section 2). Implemented in
the phases of section 11; not started yet.

This is the single plan document. Issues and evidence live in the
[interaction simplification issue list](app-runtime-simplification.md) (the C, U, I, S, V, M, A
and N ids here refer to it). The plan builds on the record hub of the
[unified message model](unified-message-model.md), adopts [decide, then speak](decide-then-speak.md),
and keeps the transport encryption of section 4 of the [desktop client plan](xiaoyou-desktop-plan.md).
Every phase must meet the [cross-stack reliability rules](cross-stack-reliability.md): keep working
features, a meaningful first response within 3 seconds, and consistent messages across ends.

## 0. Overall principle: simplicity

The owner's convergence principle is **as simple as possible**. Every section and every change
passes these five rules first:

1. **Fewer concepts.** The user knows five things only: **Xiaoyou, chats, work in progress,
   helpers, device**. Addresses, routes, Runtimes, card ids and tokens never appear in the UI.
2. **One way to do each thing.** One entry point and one implementation path per feature; extras
   are deleted, not kept alongside.
3. **Right by default, no choices.** Whatever can be decided automatically is not an option;
   options exist only where the owner truly has a preference.
4. **Silent when fine.** A healthy UI is quiet; a prompt appears only when the owner must act,
   with exactly one action.
5. **Delete before adding.** A new ability first looks for something old to replace; if it cannot
   make things simpler, it waits.

## 1. Goal

**In one sentence:** the owner deals only with "Xiaoyou". She exists on all of the owner's
computers at once, sees what happens, knows what she promised, and decides herself when to speak,
where to speak and whom to hand work to; the owner only chats and confirms.

Today's problems come from abilities stacked one at a time: each ability added an input channel,
a piece of state, a wake rule and a UI wording (root causes R1-R4 in the issue list). More
proactivity and perception are coming, so one model comes first: **any future ability is a part
added to the model, never a new path.**

**Out of scope:** no rewrite of the device firmware protocol; no replacement of existing helpers
(Claude Code, Codex, DeepSeek); no change to the cryptographic design of encryption and pairing
(kept from the desktop plan); no new proactive features during these phases, only their interfaces.

## 2. Decided

| Decision | Date | Content |
| --- | --- | --- |
| Overall principle | 10-23 | As simple as possible (section 0) |
| Drop "which computer answers" | 10-23 | The phone connects only to Xiaoyou and she decides which computer does the work; the owner can name one in the chat |
| Remove the hidden nudge | 10-22 | Delete `PROMISE_NUDGE` and its re-ask; consistency between words and actions comes from decide, then speak |
| Do the phone and device act as perception sources | 10-23 | Yes: the device only reports "nearby"; phone notifications are outside data that may wake her but never trigger actions directly |
| General rule for speaking first | 10-23 | Quiet by default; speak only on commitments and watches (3.6) |
| Delivery priority and do-not-disturb | 10-23 | Device nearby, the device; App in the foreground, the main chat; otherwise push only confirmations and due reminders; during do-not-disturb push only confirmations |
| Trust in outside content | 10-23 | Always data only; any action needs her decision under rules the owner has authorized |
| Work records list only dispatched work | 10-23 | Yes (follows once chat opens no cards) |
| One helper on several computers | 10-23 | One helper; the computer is an attribute |
| Firmware version selection | 10-23 | Developer page only |

## 3. What the owner sees

### 3.1 Five concepts, five screens

| Screen | Content | Replaces |
| --- | --- | --- |
| **Home = main chat** | Xiaoyou and helper avatars at the top (lit only when busy); the conversation; the composer (text, voice, images) | The separate home and main-chat entries (I2) |
| **Side chats** | Flat in the drawer; "+" creates one titled from its first message; empty side chats are not kept | Rows of "New side chat" (V4, V10) |
| **Work in progress** | Only work handed to helpers. The list groups by waiting for you / running / done; detail = one status line + one timeline + one primary button "add requirement", the rest under "..." | Work records, unified directory, native session, shared history, activity records and so on (I1, I3, V1-V3) |
| **Helpers** | One entry per helper (even across computers); busy / idle, quota | "name@machine" splitting (V9) |
| **Settings** | Three rows: Xiaoyou (paired), device, add computer; preferences; a hidden "developer" entry | Devices-and-computers and preferences-and-log pages (V7, V8, V11) |

The desktop client uses the same five screens and the same wording. The device keeps its four
screens (avatar, chat, tasks, usage), with state text from the same vocabulary.

### 3.2 One vocabulary

All ends use only this wording (App, desktop client and device share the `words` table):

| State | Wording | Where |
| --- | --- | --- |
| Xiaoyou `deciding` / `speaking` | Xiaoyou is thinking... / the reply bubble being written | Chat only |
| Task `queued` / `running` | Working on "title" | Work in progress, helper avatars |
| Task `waiting` | Waiting for your confirmation on "title" | Notification, top of work in progress |
| Task `done` / `failed` / `cancelled` | Done / didn't work / stopped | Timeline; Xiaoyou says one line in the chat |
| Connection `reconnecting` | Reconnecting... | A thin line at the top |
| Connection `unreachable` | Cannot reach Xiaoyou [Retry] | Banner |
| Pairing expired | Pair again [Scan] | Banner |
| Version too old | Update needed [Update] | Banner |
| Message queued on the phone | Waiting to send | Small mark on the bubble |

Nothing says "online", "synced" or "idle" when all is well.

### 3.3 Connection: pair once, then forget it

**The owner does one thing: scan a code once when first pairing.** After that there is no
connection management (N1-N7, U1, U2, V7).

- **The phone connects to Xiaoyou, not to a computer.** One scan joins the Xiaoyou mesh; new
  computers and routes (LAN, Tailscale, Funnel, encrypted relay) reach the phone through the mesh.
- **Addresses never appear.** The phone tries all known routes at once, uses the fastest, switches
  when one fails and retires routes dead for 7 days.
- **Only three situations reach the owner:** unreachable (retry), pairing expired (scan), version
  too old (update). Other failed requests leave connection state alone and are reported where
  that feature is.
- **Sending works while disconnected.** Messages queue on the phone and go out once connected.
- **An "add phone" entry lives in the desktop client's settings:** opening it creates a one-time
  pairing code (security as in the desktop plan: single use, two-minute expiry); after the scan the
  owner confirms once on the computer. No agent is needed to generate it.
- **The device follows the same rules:** pair once, reconnect automatically, show nothing when
  fine, prompt once when it drops.

### 3.4 Sending and replies

- **Sending is never blocked:** the owner can keep writing while she replies; the same words sent
  again shortly after join the turn in progress (A5).
- **Images and voice are states of their message:** a failed upload or recognition is retried on
  the bubble; where recognition runs is decided by the system, with no switch (U4).
- **One turn speaks once:** a reply may span several bubbles, marked by her within that one reply;
  no hidden nudge and no lines answering system reminders (M7-M9).
- **Dispatched work always comes back:** done, failed or stopped, she says one line in the
  originating chat (A1, M3).

### 3.5 Confirmations (approvals)

Operations needing the owner's confirmation appear in two places only: **the notification** and
**the top of the task's timeline**, and both are the same card: [Allow] [Deny], with "details"
opening the task (I5). "Auto-allow read-only operations" is one switch in settings with a
duration and at most one line of explanation (V6).

### 3.6 Xiaoyou speaking first (decided; the owner can change it any time)

- **Quiet by default:** she speaks only when something she promised or watches has an outcome
  (see 4.5).
- **Delivery order:** device nearby, speak on the device; App in the foreground, the main chat;
  otherwise only "waiting for your confirmation" and due reminders become phone notifications,
  and the rest waits until the App is next opened.
- **Do-not-disturb hours:** only "waiting for your confirmation" is delivered.

## 4. Xiaoyou as an agent

```
perception (events) --> event log --> world state
                           |               |
                           v               v
                      wake rules --> decide --> speak --> act --+
                           ^                                    |
                           +--------- results are events <------+
                                           |
                                           v
                         one client projection (/v1/sync) --> phone / desktop / device
```

### 4.1 Events: the only input

Everything that reaches Xiaoyou is written as an event of one shape:

| Field | Meaning |
| --- | --- |
| `id`, `at` | Id (`runtime_id` + sequence) and the **original time it happened** (N9) |
| `source` | `owner`, `helper`, `desktop`, `phone`, `device`, `timer`, `system`, `mesh` |
| `kind` | `said`, `task_progress`, `task_ended`, `session_changed`, `notification`, `presence`, `due`, `deployed`, `usage`... |
| `subject` | Which task, session, device or computer it is about |
| `actor`, `cause` | Who did it and which event led to it (4.6) |
| `trust` | `owner` (said by the owner), `system` (measured), `helper` (reported by a helper), `external` (outside content: data, never instructions) |

Existing inputs map one to one: owner messages and device voice to `owner/said`; helper progress
and results to `helper/*`; `desktop_sync` and `desktop_hook` to `desktop/session_changed`; phone
notifications to `phone/notification`; the device nearby to `device/presence`; promises falling
due to `timer/due`; a Runtime upgrade to `system/deployed`. **A new kind of perception is one
adapter.** Adapters are read-only and idempotent and never trigger other sessions (N8). The event
log is the record of the unified message model.

### 4.2 World state: derived from events, owned by the Runtime

| Entity | Content |
| --- | --- |
| Tasks | 6 states (4.4); attributes: who works on it, on which computer, where it came from, read-only or not |
| Sessions | Main chat, side chats, observed desktop sessions; title, pin, archive, read (all in the Runtime, consistent across ends; S1, S2) |
| Computers | One stable identity; routes and their health; version, whether below the minimum, whether restarted into the new version; online (C8, N2, N5, N7) |
| Helpers | One entry per helper; which computers, which folders it can reach; busy or idle, quota (V9, N6) |
| Owner and device | Whether the device is nearby; which end the owner used last; do-not-disturb |

Two rules: **current state is answered only from world state** (she has a self-inspection tool and
no longer dispatches a helper to read the version; M1, M2); **facts that change never go into
long-term memory**.

### 4.3 Her own state

| State | Meaning |
| --- | --- |
| Activity | Per conversation: `idle`, `listening`, `deciding`, `speaking` |
| Focus | Per conversation, "the thing we are talking about"; "this" defaults to it, and the owner is never asked for an id (M5, A7) |
| Commitments | Everything she answers to the owner for: dispatched tasks, promised reminders, things waiting on a condition; moved only by events |
| Watches | What she actively follows: a task's progress, a computer coming online, a session changing |

**A dispatched task is a commitment.** A task ending is an event that moves the commitment to a
final state and wakes her to say one line, so "cancelled without her knowing" cannot happen by
construction. Working memory is assembled by relevance: focus + open commitments + recent
conversation + retrieval relevant to this turn, no longer cut at "6 turns, 5 cards". Long-term
memory holds preferences, rules and lasting circumstances only.

### 4.4 Turns, tasks and commitments, separated

- **Conversation turns:** the owner speaks, she answers. Chat opens no task (A2).
- **Tasks:** work handed to a helper, with states `queued > running > waiting > done / failed /
  cancelled` only; "synced", "from another computer" and "read-only" are attributes (A3, A6).
- **Commitments:** see 4.3.

### 4.5 Decision loop: replies and proactivity are one loop

```
event -> wake rules -> decide (choose one action) -> speak -> deliver -> act -> result event
```

- **Wake rules** (cheap, deterministic): the owner speaks, wake; one of her commitments reaches a
  final state, wake; something she watches meets its condition, wake; other events only update
  world state.
- **Decide:** choose one action from `reply`, `dispatch`, `amend`, `cancel`, `watch`, `promise`,
  `remember`, `stay_silent`.
- **Speak:** only about what has already happened, once per turn. Proactive wakes take the same
  path.
- **Deliver:** decide by the rules of 3.6 whether to speak now and on which end.
- **Which computer does the work:** chosen by her at `dispatch` from where the helper is, which
  folders are reachable, quota and who is online; a computer the owner names in the chat wins.

### 4.6 Traceability

Every action and state change records `actor` (owner, Xiaoyou, helper, system, which end) and
`cause`. "Who cancelled it", "why did she speak just now" and "where did this memory come from"
can all be followed along the cause chain (A4).

### 4.7 How proactivity and perception plug in

| Desired ability | Only add |
| --- | --- |
| Tell the owner a desktop session is stuck | A wake rule (no activity for N minutes) |
| Replay collected results when the owner returns to the device | A delivery rule (deliver when nearby) |
| Decide whether an important phone notification deserves a reminder | A `phone/notification` adapter + a kind of watch |
| Tell the owner what to test after a Runtime upgrade | A wake rule (`system/deployed`) |
| "Send it once the Mac is online" | A commitment conditioned on the computer coming online |

## 5. System structure

| Part | Responsibility | Does not |
| --- | --- | --- |
| **Runtime node** (one per computer) | Record hub, world state, Xiaoyou's loop, running helpers locally | Hold authoritative records of other nodes (copies only) |
| **Xiaoyou mesh** | Several Runtimes share one agent: sync records, hand work to each other, distribute routes | Need per-computer setup by the owner |
| **Desktop client** | The five screens; supervises the local Runtime (start at login, crash recovery); permanent "add phone" entry | Hold permissions beyond the Runtime token (as in the desktop plan) |
| **Phone App** | The five screens; route selection; offline queue; Bluetooth bridge to the device | Keep any authoritative state |
| **Device** | Four screens, push to talk; nearby events | Change its protocol |
| **Relay** | Forward ciphertext and help discovery | Store plaintext or grant membership |

## 6. Data and interfaces

### 6.1 Client interface convergence

The Runtime has about thirty endpoints today. After convergence clients use only these:

| Endpoint | Purpose | Absorbs |
| --- | --- | --- |
| `GET /v1/info` | Identity, version, **minimum client version**, capabilities | - |
| `GET /v1/sync` | **The only downstream channel:** events / messages (unfinished ones included), tasks, sessions, computers, helpers, quota, confirmations, Xiaoyou's activity | `feed`, `usage`, `agents`, the wait and stream of `messages/{id}`, reads of `directory` |
| `POST /v1/messages` | Send a message (text, voice, image references, conversation, `client_id` dedupe) | `voice` |
| `POST /v1/files` | Upload images and recordings | `images` |
| `POST /v1/actions` | **The only write entry:** confirm, stop, rename, pin, archive, mark read, new side chat, add requirement; with an idempotency key and `actor` | Writes of `approvals/*`, `cards/*/cancel`, `conversations/*`, `directory/*`, `review` |
| `GET /v1/tasks/{id}/timeline` | A task's full timeline (paged) | `tasks/*/activity`, shared history, native records |
| `GET /v1/artifacts/{id}` | Attachment download | - |
| `/v1/dist/*` | App and firmware distribution | `app/*`, `firmware/*` |

Node-to-node `/v1/mesh/*` and usage reporting `/v1/events` are not for clients. Old endpoints stay
during the transition and are deleted once every client has switched.

### 6.2 Minimum versions

`/v1/info` states `min_client`, and the App and desktop client state their own version. Whichever
side is below the minimum shows "update needed" instead of limping along; mesh nodes check each
other the same way (N5). When a Runtime's source is updated but not restarted into the new
version, world state marks that computer "restart pending" and Xiaoyou says so (N7).

### 6.3 Sync semantics

- Incremental by cursor, each page applied atomically; clients hold only a read-only copy.
- Unfinished replies also arrive through sync; clients no longer run a separate wait loop per
  message (C1, C9).
- State names are only those of section 3.2; clients translate, never infer.

## 7. Module split

Principle: **module boundaries are the model's parts; each piece of state has exactly one writing
module; dependencies flow one way: events > world state > Xiaoyou's state > decision loop >
client interface.**

### 7.1 Runtime

| Module | Responsibility | Where it lives today |
| --- | --- | --- |
| `events` | Event shape, dedupe, source adapters | Intake in `service.py`, `desktop_sync.py`, `desktop_hook.py`, receiving side of `mesh.py`, `project_brief.py` |
| `world` | Tasks, sessions, computers, helpers, presence; self-inspection; routes and versions | `cards.py`, `record.py`, `conversations.py`, `identity.py`, `usage.py`, `approvals.py` |
| `mind` | Activity, focus, commitments, watches; working memory; long-term memory and recall | Prompt assembly in `xiaoyou.py`, `memory.py`, `promises.py`, `recall.py` |
| `loop` | Wake, decide, speak, act, deliver | `hear` / `_converse` / `_relay` / `_close` in `xiaoyou.py`, `decision.py`, `dsh_stream.py`, `voice_tools.py`, `proactive.py` |
| `helpers` | Adapters for Claude, Codex, DeepSeek and remote nodes | `agents.py`, `claude_gateway.py`, `codex_app.py`, `tasks.py` |
| `api` | The interface of section 6, auth, the sync projection | `server.py`, the external side of `service.py` |

### 7.2 App and desktop client

| Module | Responsibility | Where it lives today |
| --- | --- | --- |
| `transport` | One `send(Outgoing)`, one sync loop, one background-request wrapper, route probing and the offline queue | `RuntimeClient.java`, `RuntimeRetry`, `MessageStream`, `Relay*` |
| `state` | Read-only copy of the sync projection | `HubStore.java`, `SyncCache`, `ChatHistory` |
| `screens` | The five screens of 3.1 + the developer page | `MainActivity.java` (4136 lines, 11 pages) |
| `words` | The vocabulary of 3.2 (one copy shared with the desktop client) | Spread across screens |
| `device` | Bluetooth, device screens, firmware | `BleLink`, `BuddyProtocol`, `Firmware*`, `LinkService` |

## 8. What gets deleted

Following "delete before adding", these go in the matching phase:

- The hidden nudge (`PROMISE_NUDGE`, the `nudged` branch, the re-ask role of the promise regex,
  which stays only as a counter).
- `/v1/feed` and all old-version compatibility branches; the per-message wait loop and stream.
- Cards opened by chat turns; surplus card state values.
- Phone-local pin, archive and read; "sync local marks".
- "Which computer answers", the address list, pasted connection strings, plain pairing.
- Manual refresh buttons (refresh directory, update status, refresh usage).
- Unified directory, test messages, logs and the firmware version list in the main flow (moved to
  the developer page).
- The phone uploading chat history (C10), replaced by sync.

## 9. Measures

| Item | Now | Target |
| --- | --- | --- |
| Concepts the user must know | A dozen or so (Runtime, connection string, card, side chat, work records, native session...) | 5 |
| Screens | 11 | 5 + a hidden developer page |
| Connection actions | Pick a computer, read addresses, paste, scan repeatedly | One scan, once |
| Connection prompts | Six or seven wordings | 3, each with one action |
| Downstream channels | Per-message loop + feed + sync | Sync only |
| Client endpoints | About thirty | 8 |
| Send entry points (code) | 7 + 4 overload layers | 1 |
| Task states | 7 card states + a dozen more across modules | 6 |
| Replies per request | Possibly two cards, two overlapping replies, answers to hidden reminders | One |
| Cards opened by chat | One per turn | 0 |
| Xiaoyou speaking after a task ends | Silent on cancel and failure | One line for any final state |
| Traceable cancellation | Only "was stopped" | Initiator and reason |
| First response | Often over 3 seconds | Within 3 seconds (reliability rules) |
| Manual refresh buttons | At least 3 | 0 |

## 10. Acceptance scenarios

Each phase reports in the delivery format of the reliability rules and covers at least:

1. After a new phone's first scan, switching Wi-Fi / cellular or another computer coming online
   needs no action from the owner to keep chatting.
2. Three messages sent while offline arrive in order once back, with no duplicates.
3. A dispatched task stopped from the App, from the desktop, and by her own decision: each time
   she says one line and the record shows who stopped it.
4. After ten chat turns, "finish that one" resolves to the task that just finished.
5. A sentence the old regex would take as a promise gets exactly one reply.
6. With one node below the minimum version, every end shows "update needed" and never a 400 or
   "cannot connect".
7. First response within 3 seconds (real model, real network, samples recorded).

## 11. Phases

Each step can ship on its own and keeps existing features working.

| Phase | Content | Issues removed |
| --- | --- | --- |
| 0 | Remove the hidden nudge; turn on `xiaoyou.decide_first` in a test environment, measure time to first text, make it the default once it passes | M7-M9 |
| 1 | Traceability and final-state return: every state change records `actor` and `cause`; any final task state is written into the chat and wakes her to say one line | A1, A4, M3, M10 |
| 2 | **Modular cleanup:** move code into the modules of section 7 without changing behaviour, running the tests after each module | C11, maintainability of the code layer |
| 3 | Separate turns from tasks; six task states; dedupe repeated requests | A2, A3, A5, A6, M4 |
| 4 | World state and connection: computers, helpers and session marks owned by the Runtime; self-inspection; automatic route probing and retirement, minimum versions, offline queue; drop "which computer answers" | M1, M2, S1-S4, C8, N1-N7, V7, V9 |
| 5 | Attention and commitments: focus, commitments and watches persisted; relevance-based working memory | M5, A7 |
| 6 | One event intake: existing inputs become adapters; general wake and delivery rules; recaps never nest; events keep their original time | N8, N9, C9 |
| 7 | Interface convergence: clients use only the 8 endpoints of section 6; old endpoints and compatibility code deleted | C1-C7, C10, S4, V12 |
| 8 | Interface redesign: five screens, the vocabulary, the developer page, confirmation cards, never-blocked sending | I1-I7, U1-U6, V1-V11, M6 |

Proactivity and new perception start after phase 8; each one is only an adapter, wake rules and
watch conditions.

## 12. Risks

- **Decide, then speak adds a model call:** the first response may exceed 3 seconds. Phase 0
  measures first; if it misses, use the timeout fallback or parallel start described in that
  document.
- **Old and new clients during the interface switch:** minimum versions force one upgrade; old
  endpoints stay only during the transition.
- **Consistency across the mesh:** each record is changed only by its origin (as in the unified
  message model), and new actions follow the same rule.
- **Data migration:** existing cards become tasks or turns; phone-local marks are uploaded once in
  phase 4 and then deleted.

## 13. Open decisions

None. The seven earlier questions were all settled as recommended on 10-23; see section 2.
