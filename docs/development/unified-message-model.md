<p align="right">
  <a href="unified-message-model.zh_CN.md">简体中文</a> · <strong>English</strong>
</p>

# Unified message model

Status: decided (2026-10-16, the owner asked to follow one unified structure). The same day it
was checked against the local code; this revision adds the single write path, source
connection and multiple runtimes on the phone, reorders the steps, and reserves interfaces for
Xiaoyu as one unified subject (not implemented yet). See the end for progress. It pairs
with the "Multi-end message consistency" contract in the
[cross-stack reliability rules](cross-stack-reliability.md): that document says what must hold,
this one says what the data looks like and how each end connects.

## In one sentence

Each runtime keeps one **message log** per conversation, and it is the only authoritative
record. Only one module, the **record hub**, may write it. Cards, the model context, the phone's
chat history and the device's screen window are **projections** computed from that log and can
be thrown away and rebuilt. Ends, and any runtime, agent or external source added later,
exchange content only through the log's format and the sync interface: **identity comes from
ids, never from text**.

## Why

Before this, one sentence was stored several times, each copy with its own ids, aligned by
merge rules:

| Where | Stored as | Identified by |
| --- | --- | --- |
| Runtime message record | one request (question, status, reply) | message id |
| Runtime card | two card entries | message id + `:you` / `:reply` |
| Runtime transcript | a "turn" used as model context | a separate turn id |
| Phone ChatHistory | its own timeline | entry id; older data guessed from equal text within 120 seconds |
| Phone legacy chat list | plain text lines with a "me:" prefix | no id |
| Phone ChatTurn | question/answer pairs carried to another computer | yet another id |
| Device | the latest 12 cards | short ids mapped by the phone |

As a result nobody was authoritative, equal texts could be merged wrongly, ids such as `c1`
were unique on one computer only, and every new view added another copy to keep in sync.

## Current architecture (checked against the code, 2026-10-16)

> This section records the state before runtime 0.9.0; the issues were addressed by the target architecture and the steps below (see the progress table at the end).

Is there one module that manages everything today? **There is one record, but not yet one way
to write it.**

```text
                        ┌──────────────── Runtime process ────────────────┐
 phone ─HTTP─► server.py ─► Service ──┬─► ledger.py   state/messages.json    accepted requests, dedupe
                                      │
                 xiaoyu.py (26 calls)┼─► Cards ─► MessageLog  state/cards.log.jsonl  authoritative log
                 service.py (18 calls)┤    │                   state/cards.json       cards (copy)
             desktop_sync.py (4 calls)┤    └─ issues sequence numbers, holds the lock
                artifacts.py (1 call) ┘
                                      ├─► store.py    state/transcript.json  model context (another copy)
                                      └─► approvals   in memory, no sequence number
                        └──────────────────────────────────────────────────┘
```

Already in place:

- `MessageLog` (`message_log.py`) is the authoritative record of every sentence, with
  `runtime_id`, `epoch` and `floor`.
- `Cards` (`cards.py`) is the log's only owner: its lock protects the log and it issues the
  sequence. Card `entries` are rebuilt from the log on startup.
- `/v1/info`, `/v1/sync` and the conformance test exist.

Not yet in place, and the reason the ends keep drifting:

1. **Writes are scattered.** Four modules call `Cards.open/update` directly, 49 calls in all.
   `update` takes about ten optional arguments, and every caller builds the message id, `kind`
   and `author` itself. One wrong call produces a message that is valid but does not line up,
   and the log cannot catch it.
2. **Part of the sequence is taken by hand.** `Service` calls `cards.bump()` in 7 places to number
   request records. A missed call means that change does not wake waiting syncs.
3. **Approvals are not in the log and take no sequence.** `/v1/sync` returns the full list of
   approvals still pending. An approval that appears and disappears between two syncs (for
   example answered on the computer) is never seen by the phone, and nothing records who answered
   it where. The previous version of this document said messages, tasks and approvals share one
   counter; the code does not do that for approvals.
4. **Two more records live next to the log.** `messages.json` (the request record) is used only
   to accept and deduplicate, which is fine; `transcript.json` (model context) is a separate copy.
   Until it is computed from the log, what Xiaoyu remembers can differ from what the phone shows.
5. **Computer sessions get the wrong state.** `desktop_sync.py` maps a computer session's `idle`
   (this turn has ended) to the card state `done`, so the phone shows "finished" when only the
   turn has ended.

## Target architecture: the record hub

Wrap `Cards` and `MessageLog` in one layer, the **record hub** (tentatively `Record` in
`record.py`). It is the only place in the runtime that may change records:

```text
   Sources (who produces content)           Record hub                        Projections (who reads)
 ┌────────────────────────────┐     ┌─────────────────────────────┐      ┌──────────────────────────┐
 │ messages from the phone     │─ev─►│ one lock, one counter        │─────►│ cards / legacy /v1/feed   │
 │ Xiaoyu (dsh)               │     │ builds ids, checks fields     │─────►│ /v1/sync                  │
 │ agents started by runtime   │     │ on disk before acknowledging  │─────►│ model context             │
 │ observed computer sessions  │     │ messages/tasks/approvals logged│─────►│ shared desktop history    │
 │ external reports (web etc.) │     │ runtime_id, epoch, floor      │─────►│ waiting syncs (wake-up)   │
 └────────────────────────────┘     └─────────────────────────────┘      └──────────────────────────┘
```

Rules:

- **It accepts events, not prebuilt records.** Callers say what happened; the hub decides the
  id, `kind`, `author` and sequence. The events are:

  | Event | Sent by | Result |
  | --- | --- | --- |
  | `accept(conversation, text, client_id)` | Service, on a phone message | an owner message, returned only once on disk |
  | `reply_started / reply_partial / reply_final / reply_failed` | Xiaoyu, agent adapters | a reply from Xiaoyu or a helper; `partial` is not written to disk |
  | `task_open / task_progress / task_state` | Service, agent adapters | a task and its state |
  | `approval_open / approval_closed(by, where)` | approval bridge, desktop approvals | approvals enter the log with a sequence; closing records who answered and where |
  | `observed(source, upstream_id, …)` | computer session observer | a read-only message whose id comes from the upstream record |
  | `reported(source, upstream_id, …)` | external report endpoint | a read-only message whose id comes from the reporter |
  | `note(conversation, text)` | the runtime itself | notes such as "the runtime restarted" |

- **Every change takes a sequence, appends to the log and wakes waiters under one lock.** There is
  no separate `bump()` for outside callers.
- **Projections are read-only.** Cards, transcript and shared history subscribe to the hub's
  changes and never write the log. They can be rebuilt from it.
- **`Cards` becomes a projection**; `MessageLog` and `ledger.py` become storage inside the hub and
  are no longer used directly from outside.

"Unified management" then lives in one module: to know where a sentence came from and why it has
its state, read the hub's events.

## The records

### Identity

- `subject_id`: the id of **Xiaoyu as one subject**, independent of the computer it runs on.
  Conversations, memory and persona belong to the subject, not to a runtime. There is one
  subject today; it is generated on first start and kept in the state directory, so it moves
  with the state directory to another computer. See "Unified subject (reserved)".
- `runtime_id`: generated on a runtime's first start, kept in its state directory, never
  changed. Another computer or virtual machine has its own. It says **where a record was
  made**, not whom it belongs to.
- `epoch`: identifies the record set. It changes when the records are lost, broken or
  replaced; a client that sees it change resyncs from scratch.
- Message id: unique within one runtime and generated by the record hub. The owner's sentence is
  `<request id>:you` and Xiaoyu's answer to it `<request id>:reply` (the ids card entries
  already use); background results and summaries follow rules such as
  `<task run id>-result:reply`; observed and reported messages combine the source and the
  upstream id. **Global id = `runtime_id` + `/` + message id**, which never collides across
  computers.
- `client_id`: generated by the phone when sending, used only to deduplicate submissions per
  conversation, and mapped to the message id once accepted. Messages the phone has not sent
  yet are known only by it.

### Message

| Field | Meaning |
| --- | --- |
| `id` | message id (above) |
| `conversation` | the conversation |
| `task` | the task (card id) it belongs to; none for chat |
| `scope` | `main` (main conversation) or `task` (said inside a task) |
| `author` | `{kind, name}`: `owner`, `xiaoyu`, `agent` (a helper), `desktop` (an observed computer session), `external` (an external report) |
| `kind` | `user`, `reply`, `handoff`, `result`, `summary`, `note` (for example "the runtime restarted") |
| `reply_to` | the message this answers |
| `text` / `partial` | final text / the whole reply so far (replaced, not appended; empty withdraws it) |
| `status` | `queued`, `streaming`, `final`, `failed`, `cancelled` |
| `source` | `{type, session_id?, runtime_id?, origin?}`: `runtime` (made here), `desktop` (observed, read-only, with `session_id`), `external` (reported, read-only, with `origin` and `session_id`), `imported` (history brought from another runtime, with its original `runtime_id`). **On disk every record stores the `runtime_id` that made it**, so a log can be moved or merged without ambiguity; the sync response may omit it for local messages because it carries it at the top level |
| `attachments` | attachment ids |
| `created_at`, `rev`, `seq` | creation time, revision of this record (only grows, also across restarts; implemented as the sequence), log sequence |

Runtime 0.12.0 adds `kind=activity` task events, outside conversation `members`
and model context. `phase` is `dispatch`, `message`, `tool_start`, `tool_result`,
`completed`, `failed`, or `cancelled`; the author is the actual helper and
`scope=task`. IDs combine round identity, phase and native event identity;
public message snapshots revise the same ID. The App groups them by task,
orders by creation time and ID, and deduplicates by revision; an event with a
missing task is never a pending chat bubble. Retention is 2,000 events separate
from chat; pruning still advances `floor`. Details beyond 4,000 characters are
labelled as excerpts. Provider reasoning is excluded.

### Task (today's card)

A task groups messages: id, conversation, title, state, who works on it, progress, the ids of
its messages, and usage. For existing clients a card still carries copies of its messages
(`entries`); those are a projection, not a second record.

States separate "this turn has ended" from "the whole task is finished":

| State | Meaning |
| --- | --- |
| `working` | in progress |
| `waiting` | waiting for an approval or for the owner |
| `turn_ended` | this turn has ended, the task has not (a computer session's `idle` maps here) |
| `done` / `failed` / `cancelled` | the task is over |
| `unavailable` | its state cannot be seen right now (for example an observed source stopped updating) |

Older apps do not know `turn_ended`; the `/v1/feed` projection still maps it to `done` for them,
while `/v1/sync` returns the real state. `unavailable` is returned only to clients that send
`states=unavailable`; others get the card's state with `stale: true`.

### Approval

An approval is a record in the log and takes a sequence on every change: id, task, who asked,
what for, state (`pending`, `allowed`, `denied`, `expired`, `withdrawn`) and who answered
(`phone`, `desktop`, `device`). The phone receives them incrementally by sequence and sees how an
approval ended even if it appeared and disappeared between syncs.

### Log and sequence

Every change to a message, task or approval takes a `seq` from one counter in the record hub.
Within an `epoch` the sequence only grows, also across restarts; it is increasing, not
necessarily contiguous.

The log is stored in `state/cards.log.jsonl` (one line per latest version of a record,
appended) and keeps up to 5000 messages. Beyond that the oldest messages that are no longer on
any card are dropped. **Dropping is not deletion**: no tombstone is sent and clients keep their
cached copies, otherwise the phone's chat history would vanish with the runtime's trimming. The
highest dropped sequence is kept as `floor`; a client whose cursor is older is asked to fetch a
snapshot again. Only an explicit deletion, should one exist later, appears in `removed`.

## Sync interface: a new runtime implements this to join

| Interface | Contract |
| --- | --- |
| `GET /v1/info` (token required) | returns `subject_id`, `runtime_id`, `epoch`, `protocol`, `capabilities`, `name` and `version`; `/healthz` needs no token, so it does not carry them |
| `POST /v1/messages` with `client_id` | `202` only after the message is on disk; same conversation and `client_id`: the original message when the content matches, `409` when it differs; `503` when it cannot be written |
| `GET /v1/sync?conversation=&after=&epoch=&wait=` | returns `{runtime_id, epoch, seq, messages, tasks, approvals, removed, more, reset}`; `after=0` is a snapshot pinned at the returned `seq` and delivered in one page. It holds only the present: every task still running or waiting for an answer; newest first, the latest 50 messages per conversation (decided in the design, section 6) and the latest 20 activity entries per task, up to about 128 KB; and the tasks those messages belong to. `snapshot: {seq, truncated}` says whether older items were left out (clients keep their own cache of older history instead of replaying it from the start). Increments are paged by sequence, at most 200 items and 48 KB per page; while `more` is true, ask again from the returned `seq`. `reset: true` (with a snapshot) when the `epoch` does not match, `after` is above the current `seq`, `after` is older than `floor` (the increments are no longer complete), or more than 1000 items changed since `after` (too far behind). A task is a card without `entries` and with the ids of its messages in `members`. `approvals` becomes incremental by sequence (above). Nothing may be lost between the snapshot and the following increments |
| `POST /v1/approvals/<id>` | answers an approval, unchanged |
| `POST /v1/reports` (token required, new) | an external source reports progress; see the next section |

Client rules: apply first, then advance the cursor; **store the cursor as an opaque string
and send it back unchanged**, never doing arithmetic or comparisons on it (it is a number
today and may become multi-part once several runtimes share one subject, without an app
change); merge by global id; the higher `rev`
wins; a final text is never replaced by a `partial`; unknown optional fields are ignored and an
unknown required structure asks for an upgrade. The existing `/v1/feed` stays as the projection
for older apps and gets nothing new.

## Source connection: which sessions can come in

Sessions from elsewhere that the phone can show come in three kinds with different abilities,
and the UI must say which:

| Source | How it comes in | What the phone can do | Today |
| --- | --- | --- | --- |
| Agents started by the runtime (`claude`, `codex`, `tailor`…) | agent adapters send events | view, steer, cancel, approve | exists |
| Claude Code and Codex sessions the owner opened on the computer | the observer reads local transcript files every second | read-only; optional approval bridge | exists; new sessions found in about 2 seconds, the `idle` mapping must change |
| **Sessions not on this computer**: claude.ai on the web, cloud Claude Code, other people's computers | there are no local transcript files, so the observer **cannot read them** | read-only, only if the source reports itself | **missing** |

The third kind is why the app cannot see what Claude on the web is doing: that conversation is
stored on claude.ai and the runtime has no way to read it. The fix is a report endpoint the
source calls itself:

- `POST /v1/reports`: `{origin, session_id, title?, state, text?, upstream_id, at}`. `origin` names
  the source (for example `claude-web`); `upstream_id` comes from the reporter, so repeated reports
  are not recorded twice. The hub records a read-only message with `source: external` on an
  "external session" task.
- How a source reports: when Claude on the web can run commands on the owner's Mac, its project
  instructions ask it to run `python3 -m xiaoyu_runtime.report …` when it starts, finishes a
  stage and ends. A source that cannot run commands cannot connect, and the phone does not pretend
  to see it.
- Reports are read-only and cannot be continued from the phone; a state that has not been updated
  for a while shows as `unavailable`, never as finished.

## Multiple runtimes on the phone

Today the phone follows only the selected runtime: switching clears the cards and fetches from
scratch (`selectedRuntime` and `resetCards` in `RuntimeClient.java`). The phone identifies a
computer by its address, so the same Mac's LAN address and Funnel address count as two
computers. New rules:

1. **Identify a computer by `runtime_id`, not by address.** When an address is added, call
   `/v1/info` first; addresses with the same `runtime_id` belong to one computer. If one address
   fails, try another without clearing data.
2. **One local cache and one cursor per computer**, keyed by `runtime_id`. Switching the computer
   on screen clears nothing; switching back continues incrementally.
3. **Fully sync only the selected computer at first; fetch only a summary from the others**
   (pending approvals, new results) and show a badge in the sidebar. Full concurrent sync of
   several computers comes later if it is needed.
4. A sentence exists only on the runtime it belongs to; the phone tells them apart by global id
   and never merges across computers.

## Unified subject (the Xiaoyu mesh, runtime 0.10.0)

The core product idea is that **Xiaoyu is one subject**. Whatever computer or cloud host runs
Xiaoyu's service, the owner talks to the same Xiaoyu in the same conversations. The
"Multiple runtimes" section above only lets the phone connect to several computers, each with
its own records; a unified subject means Xiaoyu on several computers is still one Xiaoyu.

It is built as the Xiaoyu mesh (`runtime/xiaoyu_runtime/delivery/mesh.py`, described for operators in
the runtime README, "The Xiaoyu mesh"). These reservations made that possible without a data
migration or an app change, and must stay intact:

| Reservation | What to do now | Why |
| --- | --- | --- |
| Subject id | keep `subject_id` separate from `runtime_id`; `/v1/info` returns both | the subject stays the same on another computer, and the phone identifies the subject |
| Records carry their origin | every log record stores the `runtime_id` that made it | a log can be moved or merged with another without colliding or misidentifying ids |
| Ids independent of the host | global id = origin `runtime_id` + message id; "the n-th message on this host" is never an identity | same |
| Opaque cursor | clients send the cursor back unchanged | the cursor can later become one part per origin without an app change |
| Sources send events | a remote runtime is just another source sending the hub the same events as a local agent (layer one) | one standard across computers and on one computer, no second protocol |
| Conversations and memory belong to the subject | transcript, session ids and persona are stored per subject, not per computer (done with step 4) | Xiaoyu remembers earlier conversations after moving |

The earlier plan had one home runtime and satellites. The owner asked instead that a runtime
started anywhere be a full member, so the mesh has no home; every member holds every record:

- **Membership**: runtimes with the same `mesh.secret` are one subject; `subject_id` is derived
  from the secret. A new runtime needs the secret and one member's address; members pass the
  list of members on. A runtime nobody can reach still joins by connecting out (it pushes its
  own records and pulls the others').
- **Single writer per record**: a task, message or approval is only changed by the runtime that
  made it (its origin), so there are no write conflicts and no merge rules. Each origin numbers
  its own records with its local sequence; members keep one cursor per origin (`epoch` + sequence)
  and pull incrementally, and pass records on, so a member that cannot reach the origin still gets
  them. A block for an origin whose `epoch` changed is accepted only from that origin itself.
- **Projection on each member**: another runtime's task becomes a local card (`cN`, with
  `mesh_origin`, `mesh_epoch`, `mesh_id`, `origin_name`); its messages keep their ids with the
  first 12 hex digits of the origin in front; its approvals are mirrored. The card's entries are
  built from the replicated messages, never derived back into the log. `/v1/sync`, `/v1/feed`, the
  device window and the shared desktop history read them like local records, so the phone and the
  device need no change. Final replies of Xiaoyu on another member are offered to the local model
  context under their original id, so the phone's own history hand-over does not count them twice.
- **Commands instead of remote writes**: speaking in another runtime's task, cancelling it and
  answering its approvals become command records addressed to the origin; they travel like any
  other record (also through other members and after a reconnect), are executed once, and the
  origin reports `accepted` and, for a sentence, `done` with the outcome. A sentence keeps its id
  on both sides: the local placeholder (`relay_to`) is replaced by the origin's record. Commands
  expire after ten minutes; a member not reached for five minutes is offline and a sentence for it
  is refused with the reason.
- **Helpers on other runtimes**: each online member's helpers are offered to Xiaoyu as
  `<helper>@<runtime name>` (never as her voice). The work runs there on a card stamped
  `delegated_from` (set when the card is opened, so it is never shown twice); the delegating card
  here gets `delegated_to`, progress and the result.
- **Settings, folders and desktop histories** (runtime 0.11.0, `mesh_share.py`): environment values
  the configuration names, folders matched by name (each computer maps the name to its own path,
  for example Claude Code's memory) and the full visible text of each computer's desktop sessions
  travel as `share` records. Settings and folders: latest write wins, deletions follow. Histories
  are mirrored per computer, read-only, and listed for desktop agents by the shared-history hook.
- **Not shared**: subscription quota, firmware and app releases, attachment content.

## Which projection each end is

| Projection | What it computes from the log | What it may write |
| --- | --- | --- |
| Runtime cards, legacy `/v1/feed` | messages and state grouped by task | nothing; the record hub writes the log |
| Model context (transcript) | recent final messages; `imported` ones keep their origin | nothing |
| Shared history for desktop agents | the same, for agents on the computer | nothing |
| Phone chat and tasks | one local cache of the log per runtime, keyed by global id, plus messages not sent yet | new messages, approval answers, cancellation |
| Device screen | the recent window from the phone; the phone maps global ids to the device's short ids | buttons and voice become messages through the phone |
| Observed computer sessions, external reports | messages with `source: desktop` / `external` | read-only |

## How to connect

1. **A new runtime** (another computer, a cloud virtual machine, or another implementation)
   implements the sync interface above. The phone needs no code for it and adds it like
   another computer; data is keyed by `runtime_id`, so ids never collide. Before connecting it,
   run the conformance test: `python3 -m xiaoyu_runtime.conformance --url <address> --token <token>`
   (it sends two sentences in a separate conversation, so it calls a model once).
2. **A new agent behind a runtime**: its adapter only sends the hub the events started, text so
   far, a progress line, an approval request, finished and failed (the existing `Control`
   callbacks), and declares what it supports (steering, cancellation, resuming a session). Ids,
   sequence, storage and sync belong to the record hub; the agent does not touch them.
3. **Observe-only sources**: when transcript files exist on this computer, the observer sends
   `observed` events; otherwise the source calls `/v1/reports`. Both are read-only and take their
   ids from upstream, never from text.

## What does not change

- Within one conversation sentences are handled one at a time in the order they were
  accepted; approval identity and expiry rules stay.
- Older apps keep using `/v1/feed`; it is not removed while they are in use.
- This is an internal structure and protocol, not a public SDK.

## Steps

| Step | Content | Where | Status |
| --- | --- | --- | --- |
| 1 | Accepted messages on disk, one sequence for messages and cards, `epoch`, `/v1/feed?messages=1` | runtime | deployed (0.8.0) |
| 2 | `runtime_id`, the message log becomes authoritative, `/v1/info`, `/v1/sync`, conformance test (runtime 0.8.0) | runtime | deployed (0.8.0); the 22 conformance checks pass |
| 3 | **Record hub**: add `Record`, turn the 49 card writes into events, remove outside `bump()` calls; approvals enter the log with a sequence; incremental approvals in `/v1/sync`; computer session `idle` → `turn_ended` (`/v1/feed` still says `done`; done early for `/v1/sync` in runtime 0.8.2); unified subject reservations: `subject_id`, every log record stores its origin `runtime_id` | runtime | deployed (runtime 0.9.0): `record.Record` is the only write path (enforced by a test); a request's revision, sequence and log write happen in one step; approvals take sequence numbers and keep their outcome, incremental in `/v1/sync`; `subject_id` and record origins. Cards and `MessageLog` remain the storage under the hub |
| 4 | Compute the transcript and the shared desktop history from the log (formerly 2b); `transcript.json` becomes a migration source only | runtime | deployed (runtime 0.9.0): the model's conversation record stores message ids and reads the text from the log; synthesized prompts, turns brought from other computers and older records still keep text. Runtime 0.9.1: the shared desktop history reads those turns the same way (text from the log, turns the log no longer has left out) |
| 5 | External reports: `/v1/reports`, the `xiaoyu_runtime.report` command, report cases in the conformance test | runtime | deployed (runtime 0.9.0, app 0.17.1): `POST /v1/reports`, `python3 -m xiaoyu_runtime report`; the conformance test checks reports with `--with-reports`; the app shows reported sessions read-only |
| 6 | Phone app: identify computers by `runtime_id` and merge their addresses; one cache and cursor per computer, cursor stored as is; use `/v1/sync`; legacy text matching only as a one-time migration; drop the plain "me:" list and compute ChatTurn from the cache; show `turn_ended`, `unavailable` and read-only marks for external sources | app | mostly done (app 0.16.0, runtime 0.8.2): `/v1/sync`, computers identified by `runtime_id` with merged addresses, cards and sync progress kept per computer (in memory; fetched again after an app restart), no history upload before sending, plain text list removed, messages not yet on a card shown, `turn_ended` understood, ChatTurn computed from every computer's chat history; reported sessions read-only (app 0.17.1); `unavailable` for observed and reported sessions that stopped updating (app 0.17.3, runtime 0.9.1, sent only to clients that declare `states=unavailable`). Not yet: the per-computer cache lives in memory and is fetched again after an app restart |
| 7 | Device: the phone maps global ids to short ids; the device protocol stays as is for now | app | in place: the device shows only the selected computer's cards, and switching computers clears its cards first (`cardClear`), so short card ids never meet across computers; ids longer than the firmware's limit are not sent |
| 8 | Full concurrent sync of several runtimes | app | not needed for content since 8b (every member holds every record); the app may still show members of one subject as one computer later |
| 8b | Unified subject: every runtime with the same mesh secret holds every record, single writer per record, commands for control, helpers on other runtimes | runtime | done (runtime 0.10.0, `mesh.py`); the original home/satellite plan was replaced by the peer mesh at the owner's request. On-device check with a second computer still open |
| 9 | Remove compatibility paths once every app has been upgraded | both | last |

Steps 3–5 change only the runtime and older apps keep working, so they can ship together as one
PR; steps 6 and 7 are one app PR.
