<p align="right"><a href="app-runtime-simplification.zh_CN.md">简体中文</a> · <strong>English</strong></p>

# App and Runtime interaction simplification: issue list

Status: issue inventory, 2026-10-22. The owner confirmed the issues below; they are recorded
here and not yet implemented. The evidence is a reading of the Android App and Runtime source
(current working tree, uncommitted changes included) seven device screenshots from the owner
(sections 6 and 7), Runtime records and Windows session records (section 9). **Not every flow was walked through on the device.** Items marked "unverified" are inferred from code and must be reproduced before a fix.

The complete plan is the [Xiaoyou complete plan](xiaoyou-agent-model.md); this list records
only issues and evidence, and the plan decides how and in what order they are fixed.

Related: [unified message model](unified-message-model.md),
[conversation presentation across clients](conversation-presentation.md),
[cross-stack reliability rules](cross-stack-reliability.md) (every change must keep their
first-response and multi-end consistency requirements).

## 1. The converged problem

**The internal structure of the system leaks into upper-layer code and into the user's view:
one thing has several paths, several names, and several owners.**

Three root causes; every issue below maps to at least one:

| Root cause | Meaning | Examples |
| --- | --- | --- |
| R1 Several paths | One thing has two or three implementations or entry points, each with its own fallback | Two reply channels, feed and sync side by side, 7 send entry points, 5 "record" views, several approval entry points |
| R2 Several names | One thing is named differently across code and UI | 5 identity keys for one computer; 6-7 wordings for "not connected"; about 10 nouns for "a conversation" |
| R3 Unclear ownership | No decision on who owns a piece of state, so both sides store or support it | Pin/archive/read stored on the phone per address; the phone uploads history; old-Runtime compatibility kept indefinitely |
| R4 No presentation layer | Raw data, debug tools and explanatory text reach the screen without being shaped for the user | Tool-call ids, IP addresses, firmware hashes and Bluetooth retry logs on screen; long explanations instead of clear design; buttons without hierarchy |

Direction in one sentence: **one downstream channel (sync), one send entry point, one computer
identity, two kinds of things (chats / work in progress), one primary action per screen, and the
Runtime owns state.**

## 2. Code layer

| ID | Issue | Evidence | Root |
| --- | --- | --- | --- |
| C1 | One reply arrives by two paths: after sending, each message runs its own wait loop (SSE, then long poll, then 2-second polling) while `/v1/sync` also delivers unfinished messages; HubStore merges both and relies on clientId-to-id, epoch and sharedWith to avoid mix-ups | [RuntimeClient.submitScoped](../../companion/android/app/src/main/java/com/yanyuliu/pockethub/RuntimeClient.java), `SyncCache.pending`, `HubStore.applyPending` | R1 |
| C2 | After a reply completes, the old `fetchFeed` refreshes tasks even when sync is in use, mixing two sequence spaces (unverified) | `filed != null` branch in `submitScoped` | R1 |
| C3 | Feed and sync protocols coexist behind capability branches, plus 404-means-old and `presentation_v1` branches; they serve Runtime 0.6/0.8 while the current Runtime is 0.22+ | `watchLoop`, `fetchFeed`, `fetchSync` | R1 R3 |
| C4 | 7 send entry points and 4 layers of `submit` overloads pass 10 parameters through; request JSON is built by string concatenation and `substring` to splice fields before the closing brace | `send*`, `submit*`, `scopedMessageJson` | R1 |
| C5 | "Background request, back to main thread, check the computer did not change, call back" is copied 4 times; the file owns 6 executors | `directoryRequest`, `catalog`, `renameConversation`, `audit` | R1 |
| C6 | Five layers of HTTP wrappers; two response parsers (flat fields and full objects); the retry decision re-parses the request body to look for `client_id` | `request*`, `RuntimeRetry.safe` | R1 |
| C7 | Every HubStore `chat*` method exists with and without a key; the keyless versions are left over from the single-thread era | `HubStore.chatAsked/chatStreaming/chatHeard/chatAnswered/chatFailed` | R1 |
| C8 | One computer has 5 identities: url, `runtime_id`, `subject_id`, `historyKey`, `groupKey`; features pick different ones | `RuntimeClient.historyKey/subjectId/groupKey` | R2 |
| C9 | The encrypted relay has no event stream, so direct and relay connections use different wait logic and timing for the same feature | `MessageStream.open` throws for `relay://` | R1 |
| C10 | The phone uploads its local chat history to the Runtime (`shareHistory`), making the phone one of the history sources | `RuntimeClient.shareHistory` | R3 |
| C11 | `MainActivity` has 4136 lines: 11 pages, the drawer, approvals, voice, images and settings in one class, with views assembled in dense multi-statement lines | `MainActivity.java` | - |

## 3. Experience layer (states and wording the user sees)

| ID | Issue | Evidence | Root |
| --- | --- | --- | --- |
| U1 | The user must understand the system: pairing asks to paste an invite or a connection string; the empty state tells the user to run `python3 -m xiaoyou_runtime --pair`; pairing has a plain and an encrypted mode; the UI shows Runtime, token, relay, source computer, other end | Pairing and empty-state text in `MainActivity` | R2 |
| U2 | "Not connected" has 6-7 wordings; offline screens repeat "cached data" and "quota may have changed" disclaimers; switching from LAN to Funnel is only logged | `refreshDrawer`, usage page, `watchLoop` | R2 |
| U3 | Errors are written for developers: a Java exception after "cannot reach Runtime", "Runtime returned 500", "wrong token (401)", "old Runtime may not support this" | Exception text in `RuntimeClient` | R2 |
| U4 | Sending is blocked: while Xiaoyou replies, the composer says to wait for the previous reply although the transport is concurrent; images upload first and a failure must be retried or removed before sending; voice exposes where recognition runs and model download state, with a matching settings switch | Composer, image drafts and voice in `MainActivity` | R1 |
| U5 | Multi-end rules are explained only after the user acts: toasts saying a desktop session must continue on its source computer, desktop conversations are read-only, a request was handled elsewhere, a source approval is being verified | Several toasts | R3 |
| U6 | Too many "in progress" wordings: thinking, speaking, responding, helper stage text, waiting for your confirmation, working on a title | Chat and helper status | R2 |

## 4. Interaction layer (entry points, buttons, paths)

| ID | Issue | Evidence | Root |
| --- | --- | --- | --- |
| I1 | Task detail has 7 header buttons; 5 answer the same question "what did this task do" (activity, full activity, original session, shared history, full native record), plus "view all N records", "pending supplements" and "pending confirmations" | Task detail in `MainActivity` | R1 |
| I2 | Two entry points for talking to Xiaoyou: home (`TAB_HOME`) and main chat (`TAB_CHAT`) | Drawer avatar row and main-chat row | R1 |
| I3 | Too many nouns: main chat, side chat, work records, task, helper, native session, shared history, unified directory, execution directory, source, archived; to the user there are only "chats" and "work in progress" | Drawer and page titles | R2 |
| I4 | Management features sit in the main flow: unified directory and sync-local-marks on the work page, add-source on home, send/clear test messages | `MainActivity` | - |
| I5 | One confirmation has several paths: notification approval, allow/deny dialog, "pending confirmations" on the task page, "view source task" in the dialog, and a "verifying source approval" state | Approval code | R1 |
| I6 | Actions hide behind long press (rename side chat, task menu, message menu); side chats use "rename" while tasks use "unified naming" and the task menu comment says rename is not offered | `refreshDrawer`, `taskActions` | R2 |
| I7 | Page count: 11 TAB constants (chat, task, usage, settings, mine, companions, agent, home, device, work, native) | Constants in `MainActivity` | R1 |

## 5. New finding: state ownership

| ID | Issue | Evidence | Root |
| --- | --- | --- | --- |
| S1 | Pin and archive are stored on the phone keyed by **address** (`ds_ui`, key includes `runtimeUrl()`); only cards with a shared directory id use the Runtime's values. Switching between LAN and Funnel, or changing phones, loses marks on cards without a shared id (unverified) | `MainActivity.marksKey/marks` | R2 R3 |
| S2 | Side-chat read state is also stored per address on the phone (`conversation_read`); it does not sync and is lost when the address changes (unverified) | `refreshDrawer` | R2 R3 |
| S3 | S1 is why a "sync local marks" button exists, asking the user to push local marks to other ends; one source of I4 | `previewSharedMarks` | R3 |
| S4 | Usage arrives by three paths: inside feed, inside sync, and a separate `fetchUsage` with a refresh button | `fetchFeed/fetchSync/fetchUsage` | R1 |

## 6. Device screenshots (2026-10-22, App 0.30.x)

The owner supplied six device screenshots. They confirm I1 (seven buttons on task detail), I4
(unified directory and sync-local-marks at the top of the work page, test messages in settings),
U1 (paste an invite or connection string) and how C8 shows up on screen. New findings:

| ID | Screen | Issue | Root |
| --- | --- | --- | --- |
| V1 | Task detail | "Recent progress" shows internal events: whole Bash commands, `toolu_...` tool-call ids, paired "operation started / operation result" rows, second-level timestamps; the user cannot tell how far the task has got | R4 |
| V2 | Task detail | Source info (host name, agent, working directory, first prompt) sits between buttons; seven full-width buttons of equal size are stacked with no hierarchy | R1 R4 |
| V3 | Unified directory | The same items appear in three places: drawer side chats, work records and the unified directory (which also lists the main chat); the directory is a bottom sheet with a manual refresh button | R1 |
| V4 | Unified directory, drawer | Many untitled items: several "Untitled Codex session" rows (some with the same project) and several "New side chat" rows. Side chats should be titled from their first message (the Runtime's `conversations.py` has this logic) but this did not happen, or empty side chats are never cleaned up (unverified) | R2 |
| V5 | Unified directory | Each row repeats the host name; empty fields leave a trailing separator dot; the internal code name "DS" appears; rows are grey default system buttons, unlike the white outlined buttons elsewhere | R4 |
| V6 | Quick review | About ten lines of policy text in one dialog; "allow this batch only", "auto-allow read-only tools" and "confirm one by one" are mixed; a duration dropdown plus "turn off auto-allow" and "reapply with the chosen duration" control one switch; it says the page refreshes every 3 seconds yet offers an "update status" button; all controls show even when nothing is pending | R1 R4 |
| V7 | Devices and computers | The computer list shows raw IP and tailnet addresses, and the same Mac appears twice (by IP and by domain), which is C8 on screen; delete is a hidden long press; pairing has three ways: a text field, scan, and a paste-invite button | R2 R4 |
| V8 | Devices and computers | The firmware section shows build ids, hashes and free memory ("lowest ever 31 KB"), and the version list uses release notes as button text; the header lists Bluetooth permission, notification access and "unprocessed messages: 0" like a debug panel | R4 |
| V9 | Drawer | Helpers are split by "name@machine": Codex, cc@windows, codex@windows, deepseek@windows, tailor, tailor@windows and so on, so one helper appears once per computer; "project" is a directory name shown as a helper; most use placeholder avatars and all say idle | R2 |
| V10 | Drawer | The number 1 next to work records has no stated meaning; untitled "New side chat" rows fill most of the list | R2 R4 |
| V11 | Preferences and log | Preferences, test buttons and the raw log share one page; the log is mostly Bluetooth scan retries every 20 seconds; the voice switch needs a parenthetical to be understood | R4 |
| V12 | Several | Manual refresh is everywhere ("refresh directory", "update status", "refresh usage"), a sign that automatic sync is not trusted; same source as R1 | R1 |

## 7. Memory and conversational continuity (2026-10-22 screenshot and Runtime records)

The owner asked in the main chat to test the new Runtime. The screenshot and the Runtime record
(`cards.log.jsonl`, cards c348 and c349) show the following.

| ID | Issue | Evidence | Root |
| --- | --- | --- | --- |
| M1 | Old information treated as current: the reply said the last restart was 0.20.0 and the image work was not yet built, while the Runtime was already on 0.22.x and a new version had just gone live. Current-state facts (version, deployment, which machine runs what) have no "answer from a live query" rule and are inferred from old reports in chat and shared history; long-term memory `owner.md` holds only 3 notes, and such facts do not belong there anyway | c348 reply text | R3 |
| M2 | Asking about itself needs a helper: "which version is the Runtime" is something the Runtime knows, yet CC was dispatched to check on the Mac. There is no self-inspection (version, deployment state, running cards, recently finished cards) | c348 dispatched to cc | R1 |
| M3 | What is said does not match the actual state: c348 was cancelled, yet c349 still says CC was dispatched and Xiaoyou is watching. A helper's cancellation does not reach the conversation, so the owner waits for a result that will never come | c348 state cancelled, c349 reply | R1 |
| M4 | One request sent twice produced two cards and two overlapping replies: the first showed no progress, so the owner sent it again (different message ids); the first became c348 and its interim reply was written into the main chat as Xiaoyou, then the reply to the second said the same thing again | Two owner messages; c348 reply authored as xiaoyou | R1 |
| M5 | "This" is not resolved: asked to let Claude finish "this", Xiaoyou could not find the card and asked the owner for an id like "c3". The just-finished c340 was in the recent record; the conversation carries only the last 6-8 turns and references are not linked to recently active or finished cards; asking the user for a card id also hands internal ids to the user | Shared-history conversation; `BACKGROUND_TURNS`, `SEED_TURNS` in `xiaoyou.py` | R2 R4 |
| M6 | Under the bubble a link opens the source and attachments of the cancelled check card without showing that it was cancelled; the send button keeps spinning while the composer is empty | Screenshot | R4 |
| M7 | One message popped two bubbles, the second answering a hidden system nudge: the owner said they just wanted to see how she replies; her first segment said she would align on what to test "before acting", which the "said it would act but called no tool" regex took as a promise, so the Runtime silently appended a nudge the owner cannot see (`PROMISE_NUDGE`) and asked again. She called no tool and only said she would just chat and not touch any tools. The code drops post-nudge text only when a tool is called after the nudge; otherwise that line is joined into the reply (`JOIN`, i.e. `---`) and becomes a second bubble | `nudged` branch of the tool loop in `dsh_stream.py`; c352 reply | R1 |
| M8 | Slow: that turn made two serial model calls, and the second is not streamed (`show=False`), so the reply completes only after the second call and both bubbles appear together. Earlier logs already show 1.9-3.9 s to first text for one call; a second serial call clearly breaks the 3-second first-response rule | `first_text` in `restart-runtime-*.log`; same branch | R1 |
| M9 | Multi-bubble splitting relies on the prompt asking her to write `---`, and the tool loop uses the same joiner: one separator means both "she wants two messages" and "this is another model call's output", so the UI cannot tell them apart | Splitting prompt in `xiaoyou.py`, `voice_tools.JOIN` | R2 |
| M10 | In c351 she still says the Runtime status check has not come back, although c350 was already cancelled: A1 again | c351 reply | R1 R3 |

Conclusion (owner agreed, 2026-10-22): the hidden nudge is unsound in itself (guessing by regex
after the fact, injecting a hidden message into the session, an extra serial call, speaking first
and patching later), so it is removed entirely; see step 1 in section 6 of the
[complete plan](xiaoyou-agent-model.md).

Direction: remove the hidden "said it would act but called no tool" nudge and let
[decide, then speak](decide-then-speak.md) keep words and actions consistent; split bubbles with a
marker independent of the tool loop. Answer current state from live queries, and let the Runtime answer about itself; a
helper's cancellation or failure must return to the originating conversation as one line from
Xiaoyou; one request gets one reply; "this" defaults to the most recently active or finished
task, and the owner is never asked for an id.

## 8. Main agent (Xiaoyou) state

### 8.1 Inventory

What Xiaoyou knows and is doing is spread across several places with no single state model:

| Layer | Stored in | Values / content |
| --- | --- | --- |
| Cards (tasks) | `state/cards.json`, `cards.py` | `talking / working / waiting / done / failed / cancelled / unknown`, plus flags such as `monitoring_only`, `mesh_origin`, `queued`, `approval`, `run_id`; other modules also use `unavailable`, `turn_ended`, `relay_queued`, `closed`, `superseded`, `expired` and more |
| Messages | Unified message log `cards.log.jsonl`, `messages.json` | `queued / running / generating / done / failed / incomplete / interrupted`... |
| Conversation she sees | `transcript.json` | Last 6 turns per conversation (`BACKGROUND_TURNS`), 8 when catching up (`SEED_TURNS`) |
| Tasks she sees | Card list in each prompt | Active cards plus the last 5 finished cards (`RECENT_FINISHED_CARDS`) |
| Currently speaking | In-memory `_speaking` | One interrupt event per conversation, not persisted |
| Memory / promises | `memory/owner.md`, `promises.json` | Currently 3 memory notes |
| Helper sessions | `sessions.json`, `workspace` | Each helper's resumable session |

One thing has at least three records: card state, the message log and her `transcript`. Nothing
forces them to agree.

### 8.2 Issues

| ID | Issue | Evidence | Root |
| --- | --- | --- | --- |
| A1 | Cancellation is not told to her: `cancel()` only changes the card and stops the task; `_finished` and `_close` return early on a cancelled card, so `transcript` never gains a line saying the task was cancelled. Her view stays at "dispatched, waiting for the result" | `cancel`, `_finished`, `_close` in `xiaoyou.py`; the c349 reply after c348 was cancelled | R1 R3 |
| A2 | Every chat turn opens a card: when she dispatches nothing, `task_open` still opens a `done` card for the turn (c349 is such a card with no helper, its brief being the whole reply). 15 of the 100 retained cards are like this. Work records fill with chat, and because she sees only the last 5 finished cards, a few chat turns push the task that just finished out of view, which is likely why M5 could not resolve "this" (inferred) | `kind == "none"` branch in `xiaoyou.py`; `cards.json` | R2 |
| A3 | A conversation turn and a task are not separated: the owner's words, her reply, the dispatched task and its result hang on one card; in c348 the owner's message was filed under the task card and the helper's interim reply was written into the main chat as Xiaoyou | c348 messages in `cards.log.jsonl` | R2 |
| A4 | Cancellation does not record who initiated it: c348 was cancelled 11 seconds after it started and c350 after 19 seconds; the record only says Claude Code was stopped. Possible sources are the App stop button (behind a confirmation), her own decision, another Runtime in the mesh, the desktop client, or a test script calling `/v1/cards/{id}/cancel`; they cannot be told apart today | End of both cards in `state/audit` | R3 |
| A5 | The same request repeats: the owner sent "test the new runtime" three times (c348, c349, c350); each time she decided and dispatched again, and in the third reply she first said the check was still running and then, in the same reply, that it had been cancelled | Three owner messages; c350 reply | R1 |
| A6 | Too many state values, one set per module, so each screen translates them its own way (see the many "in progress" wordings in U6) | Inventory in 8.1 | R2 |
| A7 | What she sees has hard limits (6 turns, 5 cards) counted by number, not relevance; there is no explicit "current focus" state, so each turn guesses again from the list | Inputs of `lane_prompt` | R2 |

### 8.3 Direction

- **Separate three things:** conversation turns (the owner speaks, she answers), tasks (work
  handed to a helper), and reminders or promises. A chat turn is only a turn and opens no card;
  only dispatched work is a task.
- **One state set for tasks:** queued, running, waiting for you, then done / failed / cancelled.
  Other flags (synced, from another computer) are attributes, not states.
- **Every final state returns to the originating conversation:** done, failed and cancelled each
  leave one line from her in `transcript` and the main chat, and update related promises.
- **Cancellation records its initiator:** App, desktop client, herself, or another Runtime in the
  mesh, plus the reason.
- **An explicit current focus:** the conversation remembers "the task we are talking about" and
  uses it to resolve "this" instead of guessing from the last 5 cards each turn.
- **Deduplicate repeated requests:** now by request ID; identical text with a different ID is a
  new message, and repeat sends are avoided by always showing true state (complete plan section 8).

## 9. Connections and multiple computers (10-22 App chat and Windows records)

Sources: the App conversation on the evening of 10-22 (c353-c375; c370-c375 came from the Runtime
on Windows through the mesh) and the Codex session record on Windows
(`state/mesh/history/Windows-a620ba/`). The owner's impression: connections in the App are still
unstable.

| ID | Issue | Evidence | Root |
| --- | --- | --- | --- |
| N1 | Messages go out, yet the App says it cannot reach the Runtime and the remote returned 400 "rev should be an integer". The Runtime's `_wait_and_rev` answers 400 for a non-integer `rev`; any failed request in the App's reply-wait loop is reported as "cannot reach Runtime", so one auxiliary failure looks like a disconnect. The phone was on the Windows Runtime running 0.20.0 (source at 0.23.1 but not restarted) with App 0.31.2; why `rev` was not an integer is not yet known (unverified) | c372, c373; `_wait_and_rev` in `server.py`; `RuntimeClient.submitScoped` | R1 R2 |
| N2 | Dead routes stay: after Windows moved to the encrypted node, the phone kept calling the old Tailscale direct address (100.72.x:8765), timed out and showed "computer offline"; the owner concluded the Runtime did not start with the app, while it had been running all along. Failover only covers addresses with the same `runtime_id`; routes have no health and never expire; a new route needs a new QR scan | Windows record "connected, but still shows computer offline"; `RuntimeClient.watchLoop` | R2 R3 |
| N3 | Too many route kinds: Tailscale direct IP, Funnel domain, encrypted relay node (WSS over IPv6, needing a firewall rule for port 18795), LAN. Each behaves differently (the relay has no event stream), which one works depends on the network (apartment Wi-Fi, cellular, Mac egress in the US), and the user picks from raw addresses | "Devices and computers" screenshot; Windows record | R1 R4 |
| N4 | Pairing is fragile: the QR code lasts 2 minutes and must be regenerated by an agent on the computer; the 0.30.1 scan page crashed on open (AndroidX classes missing from the APK, fixed in 0.31.2), and the release flow has no check that the final APK contains required classes | Windows record | R4 |
| N5 | Versions differ across nodes and nothing stops it: Mac runs 0.23.1, Windows runs 0.20.0, the GitHub default branch is 0.20.0, and App update channels are published per Runtime (0.30.1 / 0.31.2). There is no version negotiation or minimum version between App and Runtime or among mesh peers | Windows record; version comparison record | R3 |
| N6 | Unclear which computer does the work: with the phone on Windows, Xiaoyou asked whether the code was on the Mac or Windows and said the Mac's codex cannot reach Windows (c375); the dispatched codex timed out reading source (c373). Which machine a helper runs on is not part of the state she sees | c373, c375 | R2 R3 |
| N7 | Updated without restart or notice: the source is updated while the running service stays old, mentioned only in an agent's report; the UI does not show that a computer runs an old version | End of the Windows record | R3 R4 |
| N8 | Desktop session observation has chain side effects: around 21:47 on 10-22, nine Codex sessions appeared at once (c359-c367, session ids starting `01a163dd`), opening with recap preambles ("the owner recently talked about this elsewhere...") nested 4-5 levels deep, plus other sessions' opening prompts (c365 carries the first prompt of this Claude Code session yet is labelled codex). `project_brief.recap_text` takes the latest session as recap material without excluding sessions that start with a recap, so it nests deeper each time; who started this batch is not yet known (unverified) | `project_brief.py`; c359-c368 in `cards.json` | R1 R3 |
| N9 | In the synced Windows session record every message carries the same time, 22:53 (import time), losing the original order and times | `state/mesh/history/Windows-a620ba/codex-01a1641a...md` | R4 |
| N10 | Sync restarts from zero and results cannot reach the phone until it catches up: after an App restart or a switch to another computer the sync cursor starts over (sync progress lives only in memory; `presentation_v1` can also reset it); the Mac needs 415 pages, about 10 minutes over Funnel. The App used to fetch the feed once right after a task finished, which bypassed this; once cleanup batch 3 removed that step, a Mac task result never reached the phone in the 10-23 device test although the Runtime had it. The owner decided not to restore it in the App and to fix it on the Runtime side (complete plan section 6, "one state / automatic connection"). It is also part of why "refresh is slow". **Fixed on the Runtime side in R4.1 (the snapshot comes in one page); not yet verified on the device** | 10-23 device test; a local catch-up from zero took 415 pages | R1 R3 |
| N11 | Observed desktop sessions leak into the phone's main-chat history: several replies from this Claude Code session and one owner question appear in the phone's record of the Windows conversation, two of them stuck at "generating". This is likely why the send button keeps spinning (the App thinks a reply is unfinished) (unverified) | Phone `conversation_history.xml`, 10-23 | R1 R3 |
| N12 | Words and state disagree again: the task had already finished when the owner said to stop it; Xiaoyou replied that it had stopped, and the task result then appeared in the chat | c379, c382 | R1 |
| N13 | Markdown is shown raw: `**`, backticks and similar appear in the home card and main chat instead of bold or code styling. **The Runtime part is fixed in R5 (runs no longer contain markers, and `presentation.plain` covers the home card); the App drawing the new styles and the device check are pending** | 10-23 device screenshots | R4 R5 |

The interaction design is in sections 1 (automatic connection after first pairing) and 3
(several computers still do the work once) of the [complete plan](xiaoyou-agent-model.md).

Direction: the user sees computers, not addresses; the App probes routes, switches by health and
retires dead routes automatically, and new routes arrive through the mesh without another scan;
the state distinguishes "this computer is offline" from "this route is down"; App and Runtime, and
mesh peers among themselves, enforce a minimum version and say clearly when an upgrade is needed;
an auxiliary failure never poses as a disconnect; recap material never quotes recap material.

### 9.1 History across computers (2026-10-27, Windows main chat)

In the main chat on the Windows Runtime the owner asked whether Xiaoyou could see the chats on
the Mac. She first said "only what is local to this Mac", then said a helper had to be sent to
the Mac, and finally asked the owner to choose "which computer". The owner's expectation: **after
signing in with one account, every end has the same history.** Evidence: Windows
`state/messages.jsonl`, `state/transcript.json`, `state/mesh/` and the Runtime source. Same kind
of problem as N6.

| ID | Problem | Evidence | Root cause |
| --- | --- | --- | --- |
| H1 | She gets her own location wrong: the main chat is shared by both Runtimes through the mesh, but a turn does not record which Runtime answered it. A 10-26 answer given on the Mac ("I can see the records kept on this Mac") entered the Windows transcript, and Xiaoyou on Windows repeated "this Mac". No statement of "which computer you are on now" was found in the prompt (inferred, not checked section by section) | The same turn in the Mac mesh log and the Windows `transcript.json`; replies at 10-27 23:0x | R2 R3 |
| H2 | She cannot see history that has already been synced: read-only copies of the Mac's Claude Code and Codex sessions are on Windows under `state/mesh/history/weihaos-MacBook-Air-b067ca/` (7 updated on 10-27), but only helpers are told about them, through `elsewhere()` in `api/shared_history.py`. Her own `recall` (`loop/xiaoyou.py`) searches only local transcript segments, memory and this chat's cards, so she said a helper had to look on the Mac. Same problem as M2 | `delivery/mesh_share.py`, `api/shared_history.py`, `recall` in `loop/xiaoyou.py` | R1 |
| H3 | "History" has several layers and she mixes them: the main chat (mesh-synced, on both), each computer's desktop sessions (read-only copies elsewhere), each computer's cards (copies only). She finally asked the owner to pick "the Mac's or the Windows'", handing the internal structure to the user | Replies at 23:1x | R2 R4 |
| H4 | Synced data is noisy: on 10-27 the Mac mesh log carried 821 messages, 327 of them automation `<heartbeat>` entries in the `__desktop__` conversation, which nobody said | `state/mesh/b067ca….jsonl` | R4 |

Direction: one history per account, with the computer as an attribute rather than an entry point
the user must choose; every turn records the Runtime that spoke, the transcript marks turns from
other computers, and the prompt states which computer she is on and which others she can see;
`recall` covers the mesh main chat and the `mesh/history` copies, so reading history dispatches no
helper; heartbeats and other automatic messages stay out of chats and sync.

## 10. Targets and measurable indicators

| Item | Now | Target |
| --- | --- | --- |
| Reply downstream channel | Per-message loop + sync | Sync only |
| Sync protocol | Feed + sync | Sync only, with a minimum Runtime version |
| Send entry points | 7 + 4 overload layers | One `send(Outgoing)` |
| Computer identity keys | 5 | 1 (a stable Runtime id); addresses are only routes |
| Connection states | 6-7 wordings | 3: connected / reconnecting / unreachable (with one action) |
| Pages | 11 | About 5: home as main chat, side chats, work in progress, helpers, settings |
| Task detail primary buttons | 7 | 1 ("add requirement"), the rest under "..."; records merged into one timeline |
| Approval entry points | 4-5 | 2: notification and the top of the task timeline, the same card |
| Pin / archive / read | Phone-local per address | Owned by the Runtime, consistent across ends |
| Task progress | Raw tool events to the second | One-line stage plus key outputs; raw events only under "..." > detailed record |
| Helper list | Split by "name@machine", 9 rows | One row per helper; the machine is an attribute |
| Untitled items | Many "New side chat" and "Untitled Codex session" rows | 0: automatic titles, empty side chats cleaned up |
| Manual refresh buttons | At least 3 | 0 (pull to refresh as a fallback) |
| Debug information | Log, test buttons, firmware hashes in user settings | Behind one hidden "developer" entry |
| Quick review | About 10 lines of text and 5 controls | One switch with a duration plus the pending list; at most one line of explanation |
| Current-state answers | From old records, possibly stale | From live queries; say so when unavailable |
| Helper cancel / failure | Invisible in chat; Xiaoyou still says it is checking | One line in the originating chat; promises updated |
| Replies per request | Possibly two cards and two overlapping replies | One |
| Cards opened by chat turns | One per turn | 0 |
| Task state values | 7 card states plus a dozen more across modules | 6 |
| Traceable cancellation | Only "was stopped" | Initiator and reason recorded |
| Connection choice shown to the user | Raw addresses, picked by hand | Computers only; routes probed, switched and retired automatically |
| Version mismatch | Unnoticed | Minimum versions between App and Runtimes, shown clearly in the UI |
| Auxiliary request failure | Shown as "cannot reach Runtime" | Leaves connection state alone; reported only where that feature is |
| History from other computers | She sends a helper there or asks the owner which computer | One per account, she reads it herself; every turn knows which computer spoke |

## 11. Suggested order

> Overall execution follows the implementation order in section 6 of the
> [complete plan](xiaoyou-agent-model.md); the order below applies to this list alone.

1. Low-risk cleanup: remove feed and old-version compatibility (C3, part of S4), unify the send
   entry point and the background request wrapper (C4-C7), unify the identity key (C8).
2. State ownership: the Runtime stores pin/archive/read (S1-S3); then remove "sync local marks".
3. Replies through sync only (C1, C2, C9): largest gain; touches the 3-second first response and
   multi-end consistency, so it needs regression evidence under the reliability rules.
4. Interaction and wording after step 3: U4 (never block sending), I1-I7, U1-U6; sketch first,
   change code after the owner approves. V1-V12 and M6 belong here too; V4 (untitled items), V9 (merged
   helpers) and V11 (moving debug information) do not depend on step 3 and can go first.
   M1-M5, A1-A7 and H1-H4 are Runtime-side and go with step 3; A1 (cancellation returns to the chat)
   and A4 (record the initiator) are small and can go first. once replies take one path, M3 and M4 are easier to
   fix at the root.

## 12. Owner decisions needed

- The minimum supported Runtime version (decides how much compatibility code can go).
- ~~Whether merging home and main chat (I2) conflicts with section 7 of
  [conversation presentation](conversation-presentation.md), which gives the phone home a voice
  bar and generated content, and which one wins.~~ Decided 10-24: one entry and one conversation
  in voice and text forms; see section 8 of the [complete plan](xiaoyou-agent-model.md).
- Whether sync delivers unfinished messages often enough to replace the per-message event stream
  (the precondition for C1; measure first).
- Whether the phone still needs to upload history (C10); remove it if not.
- Whether work records should list only dispatched work once chat turns stop opening cards (A2).
- Whether one helper on several computers counts as one helper or several (V9); whether firmware
  version selection moves to the developer entry only (V8).
