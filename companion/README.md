<p align="right">
  <a href="README.zh_CN.md">简体中文</a> · <strong>English</strong>
</p>

# DS companion (Pocket Hub)

Agents changing this project must first read the [root instructions](../AGENTS.md)
and [mandatory App/Runtime/firmware reliability rules](../docs/development/cross-stack-reliability.md).
They define regression, 3-second first-response, connection recovery, and
multi-end consistency acceptance. The capability descriptions below do not
establish that these requirements have passed validation.

The Android companion is built around DS: you talk to her, she hands work to helpers, and a left sidebar shows every task and helper. It sits between the phone's AI assistants and
the Claude Pocket device. It reads the notifications of registered apps
(Claude and Codex by default), keeps the recent ones as a small feed, and sends
that feed to the device over Bluetooth LE. The app speaks the same Hardware
Buddy wire protocol that the Claude desktop app does, plus the hub extension
described in the [firmware documentation](../docs/claude-pocket.md#the-conversation).

The app also has a chat box for talking to DS. A message typed there goes
to the [Runtime](../runtime/README.md) over HTTP. Every request you
make is one thing with a card on the runtime; the app keeps waiting for the
runtime to say which cards changed (`/v1/feed`), shows them, and reports them
to the device: what DS is doing right now, the card of every thing, the
things being worked on and their two latest steps. When a helper wants to do
something that needs your consent, the app shows a banner under the top bar and full
operation detail; the device also shows its prompt, and either side can answer. The app also tells
the device which agents the selected runtime has.

Holding `OK` on the device records a voice message. The app receives the audio
over Bluetooth, forwards it to the runtime, and the runtime turns it into text
and answers it like a typed message. This device-audio path is separate from the phone microphone, which by default transcribes on the phone (see below for when the computer recognizes it).
When a thing was on the device's screen at the press (the card on screen 2,
the selected one on screen 3), the device says the sentence is addressed to
it, and the app passes that on to the runtime unchanged.

The hub is its own participant, not a Claude feature. It knows nothing about
any particular assistant; a source is just an Android package name and a
display label, and more can be added in the app.

## Sidebar phone interface

DS is the main thread: she keeps you company, takes requests and hands them to
helpers. You only talk and watch, so the app offers no helper or effort picker;
say "let Codex do it, carefully" and DS arranges it. The ☰ button at the top
left opens a sidebar with every place in the app:

- **Chat with DS** — a persistent message timeline. Sent messages appear immediately;
  streamed replies update the same bubble. Hand-off replies stay visible with a task
  link, and completion summaries arrive as new messages without changing pages.
  Conversations started on the computer never appear here. Older messages can be
  loaded above the visible window; deleting or pruning a task card does not remove
  messages already saved on this phone.
- **Tasks** — work DS handed to a helper, grouped by creation date in the phone's
  time zone (Today, Yesterday, then calendar dates), newest first within each day.
  Each row shows its creation time. Pinned tasks have their own section with a full
  date and time. Completion, approvals and subsequent task runs never change the
  original date or position. Long-press a task (or use ⋯ on its page) to pin,
  archive or stop it. Pins and archives are kept on the phone per computer;
  archived tasks can be restored from the "Archived" row. Titles come from the
  runtime so the device, phone and computer agree.
- **Helpers** — one row per helper (Codex, Claude, then others) with its state.
  A helper's page lists what DS handed to it, each linking back to its task, and
  the conversations opened on the computer; its composer hands work to that helper
  directly, without DS arranging it.
- **Connection and settings** — the device and computer connection, companion
  look, usage, firmware, notifications and preferences.

The app opens on DS herself. Her portrait moves with what is really happening:
she breathes while idle, tilts her head with rings while listening, floats with
three dots while thinking, bounces lightly while her streamed reply appears,
and hops when a helper finishes. Tap her and she answers locally. A handed-off
request sends a small letter flying to the helper's chip, which spins while the
helper works and turns green when it is done. Hold the big microphone to talk: a bundled Chinese streaming model shows provisional text while recording;
SenseVoice performs final recognition after release. Only that final text is
sent to the runtime. It works without Android's speech service or
an offline language pack. Cancelling the gesture or leaving the screen discards
the recording and result. Recordings stay in memory and are limited to 30 seconds.
Capture prefers Android's voice-recognition source and falls back to the plain
microphone. When the phone cannot recognize the speech, or "Let the computer
recognize phone speech" is on under Preferences, the recording is sent to the
connected computer and recognized like device audio; otherwise it is never uploaded. Allow microphone permission;
microphone capture starts immediately while the model loads on a separate worker,
so speech spoken during model startup is retained. The recording hint appears
only once the microphone is capturing. A failed attempt keeps its specific
error visible below the microphone until the next press.
Typing can use the keyboard's own voice input;
the keyboard opens the conversation history for typing. With no computer added
yet, the home screen offers to connect one. The device page mirrors the wrist
screen, connects or disconnects it, links to firmware, and keeps a placeholder
for what later firmware adds.

A task's page shows its steps (DS splits the request, the helper works with its
latest progress, DS reports back), then the task's conversation; messages sent
there appear immediately and continue that task. A worker's original final result
and DS's summary are separate entries, with long originals saved as attachments.
Files and original results become available while the summary is still being
prepared; its streaming bubble is independent of any current chat reply. New
message IDs, scope and result metadata are additive and old runtimes remain
readable. Restart the runtime after upgrading its Python code to enable these
new result features. Previously discarded messages and originals cannot be
recovered by the phone. A conversation opened on the computer can be continued here once its turn has
stopped: the message goes to the matching helper, which resumes that session in its
own project directory, so the reply also appears on the computer; the work gets its
own task, titled after that conversation. While the turn is still running on the computer, sending is
refused so two sides never edit one session. Needs runtime 0.8.1.
Status is written as one coloured word: blue in progress, amber waiting for
you, nothing for ended tasks. The companion look only changes how DS appears in
the chat; it does not decide which helper gets work. The plus button for
attachments stays hidden until the runtime can receive files from the phone.
Draft text is kept separately for each computer and task.

DS, Codex and Claude portraits are transparent 256px crops from the retained
source atlases. `android/tools/PreparePortraits.java` regenerates them with JDK 17.
Claude/cc aliases share one image; other helpers get a named placeholder. Desktop
messages show their observed author, while DS relay replies retain DS's portrait.
Optional breathing animation stops for hidden/detached portraits.

Pending approvals show one banner under the top bar, only while something is waiting.
Their detail shows the original operation and source computer, with submission,
success and retry feedback. A waiting task alone never fabricates allow/deny
buttons. Desktop approvals require the live permission bridge; notification-only
requests remain readonly. Runtime-managed tasks can be stopped after confirmation.

Up to eight paired Runtimes are monitored for approvals simultaneously, including
while the App is in the background with its watch service active. Android's
approval notifications open the source request. Each answer returns to that
request's originating Runtime even while another computer is selected. The task
list, conversation, helpers, and firmware management continue to use the selected
Runtime. A disconnected approval source loses its actionable requests until a
fresh feed arrives. Grant Android notification permission to receive the alerts;
notification access for other apps is a separate permission.

Registered task images appear as previews. Tap an image for a full-screen view
of the downloaded original, then save it to a location chosen through Android or
open it in another app. File cards show the name, MIME type, and size, and offer
the same save/open actions. Downloads use the originating Runtime and registered
attachment ID, verify size and SHA-256, and cache the verified file. Failed
downloads can be retried. This iteration does not upload phone files or import
arbitrary paths or URLs mentioned in desktop transcripts; general attachments
are viewed on the phone, not transferred to the device over BLE.

The Runtime HTTP request and attachment fields are documented in
[HTTP interface](../runtime/README.md#http-interface) and
[task images and downloadable files](../runtime/README.md#task-images-and-downloadable-files).
Their implementations are `runtime/xiaoyou_runtime/server.py`, `cards.py`, and
`artifacts.py`; the agent reply schemas are `reply_schema()` and `lane_schema()`
in `runtime/xiaoyou_runtime/xiaoyou.py`. Display branding does not rename Python
modules, the Android package, JSON roles such as `xiaoyou`, or the BLE device name
`Claude-XXXXXX`.

Usage is available under Connection and settings. It shows the
selected computer, connection state, subscription windows and reset times,
helper models and tiers, and the age of the data. Missing values stay unknown;
real 0% remains 0%. Offline values are explicitly labelled as cached. Refresh
runs in the background and a result from a previously selected computer is
discarded. On hardware that announces `usage`, the app also sends an empty list
when the selected source has no data, clearing the previous computer's values.

## What reaches the device

| On the phone | On the device |
| --- | --- |
| A message sent to DS from the chat box | Screen 1: what you said, DS thinking, the agent she handed the work to, then a brief of a sentence or two |
| The cards on the runtime | Screen 2 pages through conversation only (what DS answered herself): the 8 most recent, plus every one still being worked on or on screen 3's list; on firmware that shows them apart a task's card carries that thing's latest exchange (at most 480 bytes), for the page opened from screen 3. Each holds the first sentence of the thing and the latest thing DS said (the first 959 bytes; the full exchange stays on the phone) |
| The things handed to helpers | Screen 3: at most four, those in progress first (who is doing it, for how long and the two latest steps, tool names and commands verbatim), followed on firmware that announced `threads` by the latest that ended (the brief of the conclusion); the count in the top bar. A sentence said while holding the key on screen 3 carries `pin`, which the app passes to the runtime as it is: it is always filed on the selected thing |
| Independently running desktop conversations discovered by the runtime | The same task/card pages show the agent, project, state and latest activity; opening a task shows the latest visible request and reply excerpts (at most 480 bytes). Continuing or stopping these sessions stays on the computer; a live bridged approval is a separate response channel |
| An operation a helper wants confirmed | DS asks, who wants what is shown verbatim; `OK` says yes, `DOWN` says no, and the answer goes back to the runtime |
| A voice message recorded on the device | The same as a typed message; what was heard appears on the device and in the chat box once the runtime has transcribed it |
| The agents of the selected runtime | The helpers page in the menu (the first four) |
| Usage from the selected runtime | Screen 4: subscription windows, reset times, helper models and tiers; the screen remains available before data arrives and after disconnect |
| A native Android notification from a registered source | An entry on the notices page in the menu; this source is independent of desktop Runtime hooks |
| Notifications not yet dismissed | The idle first screen says how many |
| A notification with both an allow-like and a deny-like button | The pet asks; `OK` presses the allow button, `DOWN` the deny button |
| Nothing new | A keepalive every 10 seconds so the device stays connected |
| A version of the device firmware chosen on the computer | The device shows DS "changing into something new" with a progress bar and restarts by itself when done; see [Device firmware](#device-firmware) below |

When the app introduces itself the device announces what it understands. One
that announces `cards` supports card and task screens and gets everything
above; one that announces only `chat` runs the previous version, and the app
sends it the current turn without cards or tasks; an older one announces
neither, and the app falls back to what it sent first: the summary line and
the full text of the latest message as a `turn` event. Approval requests work
on all three.

A runtime older than 0.5.0 has no cards and no approvals: the app's log says
it is an old version, and the chat is listed line by line as before.

### Conversation and tasks apart (0.8.0)

Messages sent from a task's page retain its task ID and `pin`, so they continue
that task. Runtime 0.5.3 or later is needed for task-scoped `pin`; older
Runtimes may decide the target themselves.

### Usage, tiers and task history (0.9.x)

- **"Usage" at the top of the settings page**: for each subscription the runtime
  reports, how much of the five-hour and weekly windows is left, when each resets,
  where the numbers came from, and the helpers running on it with their models.
  When the device declares `usage`, the same goes to its fourth screen (whenever
  it changes, and every 60 seconds to keep "updated N ago" right).
- **Effort button**: next to the input box; each tap cycles "default / low /
  medium / high". A sentence sent with a tier carries `effort` to the runtime,
  and the button returns to default afterwards.
- **Task details**: one line with who did it, which model, which tier, and roughly
  how much of the quota it used.
- **Task history for the device**: when the device declares `past`, recently
  finished things (up to five, grouped by day) are sent separately (`past`); when
  the device opens an old thing whose card it no longer has, it asks (`want`) and
  the app sends that card.
- **Device memory**: the free memory the device reports (`heap` and `low` in
  `fw info`) is passed to the runtime and shows in `firmware status`.

Only what a notification contains can be forwarded. Whether a given assistant
posts notifications for cloud sessions, and whether they carry action buttons,
depends on that assistant's app and has not been verified.

### Conversations running on the computer

When desktop observation is enabled on the paired runtime, independently
running Codex and Claude Code conversations appear on their helper's page. Each
uses the conversation title shown in the desktop sidebar, together with its
desktop source, project, current state and latest activity. The app preserves
the runtime's title verbatim; desktop renames update the same task. If the
desktop title is unavailable, the recent request or session identifier is
shown with an explicit fallback notice. Titles longer than the runtime's
200-character limit are marked as shortened. The hardware's task and history
lists devote their 47-byte UTF-8 title field to that same title, clipping at a
character boundary without prepending the project or source. All connected
agents use this same title rule; the app does not generate another title or
summary. Open one
to read the recent user requests and visible Codex or Claude replies, with the
actual assistant's name. The runtime supplies a recent window of at most 20
messages, at most 4,000 characters per message and 32 KiB of UTF-8 content per
session; the app identifies recent excerpts and reports omitted content.
The latest request and reply excerpts, plus the state, reach the device through
the existing cards/tasks Bluetooth messages within a 480-byte reply budget.
These cards are read-only: continue and cancel controls stay on the computer,
including when speaking from a selected desktop task on the device. A live
`PermissionRequest` from an installed and trusted Codex or Claude Code hook may
be answered separately from the phone or device. A permission notification or
status signal without that channel only asks you to confirm on the computer.
An ended turn does not mean the project is complete. The app does not show
private reasoning, tool output or a reply that has not yet been recorded as a
visible message. This view is recent conversation content, rather than a
complete conversation archive or a live character-by-character stream.

When observation or the phone's connection to the runtime is lost, the task
stays in the current list as unknown, keeping the last observed content without
a running timer; it does not
move into completed history. After reconnecting the app refreshes the states
from the runtime. Firmware announcing `desktop` displays unknown and ended
turns distinctly. Older firmware receives a stopped-state fallback, with the
actual status written in the card's reply and no running timer.
The runtime's [desktop observation documentation](../runtime/README.md)
describes which local sessions can be discovered and how to enable it.

## Install and set up

Each push that touches `companion/` builds a debug APK in GitHub Actions and
attaches it to a prerelease named *Pocket Hub debug build N*. Download
`pocket-hub-debug.apk` on the phone and allow installation from the browser.

In the app, in order:

1. Grant Bluetooth permission.
2. Enable notification access for the app in system settings.
3. Connect. When the device shows a six-digit passkey, type it on the phone.

To talk to DS, start the runtime on a computer on the same network, run
`python3 -m xiaoyou_runtime --pair` there, paste the printed line
(`http://<address>:<port>#<token>`) into the app, and add it. Several computers
can be added; tap one to choose which answers, long-press to remove it. The
app keeps the 20 most recent turns on the phone and hands the latest ones to
whichever runtime you send to, so the conversation continues when you switch
(see *Several runtimes, one conversation* in the runtime README for what does
and does not carry over). Cards are not stored on the phone: they live on the
runtime, and the app fetches them again after it starts or after you switch
computers.

The device accepts one Bluetooth connection at a time. Disconnect it from the
Claude desktop app (or move away from that computer) before connecting the
phone.

## Interaction and motion (0.21.1)

A pass modelled on the Claude mobile app. It changes only what the phone shows and how it responds to
gestures; the Runtime API is unchanged.

- **Chat and task pages only append**: new messages are added at the end and rise into place, older ones
  stay where they are and the scroll position no longer jumps; a streaming reply only changes the text in its
  own bubble. The list is redrawn only when older messages are loaded, a message disappears or the order
  changes. The task page updates its steps, conversation and in-progress replies as three separate parts.
- **Reply marker**: under the last reply, a breathing dot while thinking (after 8 seconds a line says what it
  is waiting for), a turning star while writing, and a still, faded star once done, next to "copy" and
  "ask again" (sends your previous message again).
- **Four composer button states**: on the chat page with no text it is hold-to-talk (release to send, slide off
  the button to cancel); with text it sends; while the companion is replying or the task is running it stops
  (this stops the Runtime task and still asks to confirm); after stopping it spins until the task ends. The
  Runtime cannot interrupt a reply the companion is still saying, so then it only shows a waiting state. Task
  and helper pages keep the arrow when empty.
- **Page changes**: going deeper (task, helper, settings) slides in from the right, going back from the left,
  same-level pages fade.
- **Sidebar**: swipe right on the home, chat or device page to pull it out, swipe left to close; both follow the
  finger.
- **Bottom sheets**: task actions, archived tasks, the approval list and detail, usage, execution records,
  download source and attachment choices slide up from the bottom and can be dragged down to close. Stop,
  firmware change and computer switch confirmations remain system dialogs.
- **Notices**: toasts are replaced by a small banner at the top of the page; errors are red, a tap hides it, and
  screen readers announce it.
- **First sync**: while the first history sync from a computer is still running, the chat page shows pulsing
  placeholders instead of flashing an empty page.
- **Haptics**: a light tap when hold-to-talk starts and ends, on send, on copy, and on a successful approval.
- With system animations turned off everything jumps straight to its end state. Not yet checked on a phone.

## Quick review (0.21.0)

Open **Quick review** in Connections and settings or tap the pending-approval
banner. The page names the selected computer, shows the exact pending tool
operations, and offers one button to allow the displayed batch (up to 100).
New requests arriving afterwards are excluded. Changed, expired, withdrawn, or
already answered requests are checked again; a repeated batch does not execute
the same operation twice. Individual allow/deny controls remain available.

**Enable automatic approval of read-only tools** is an explicit owner
preauthorization for file reads and directory/text searches from helpers dispatched
by the current main conversation on this computer. Only structured `Read`, `Glob`,
`Grep`, and `LS` tool identities from the permission adapter qualify. Commands,
file changes/deletions, spending, network tools, and unclassified operations remain
manual and can be reviewed in a displayed batch. It immediately covers matching
pending requests and subsequent requests while enabled. It does not perform an AI
risk assessment. Independent desktop sessions, other computers,
and stalled-task continuation questions remain manual. A live, remotely actionable
desktop permission request can still be included in a displayed batch.

The mode defaults to manual. App 0.21.0 with Runtime 0.13.0 offers **Until manually
disabled**, 15 minutes, 1/8/24 hours, 7 days, or a custom duration of 1 to 525,600
minutes. Select a duration and enable it; when already enabled, the separate
duration button applies the selected period starting now. It can be disabled
immediately. Settings survive Runtime restart: timed grants retain their original
deadline, and indefinite grants stay enabled until disabled. It continues working
while the phone is closed or disconnected. The page refreshes status every three seconds
while open and retains its original computer as the target. If that computer
is removed or the selection changes, reopen the page for the intended computer.

Runtime 0.12.0 supplies basic quick review with a temporary one-hour mode;
App 0.21.0 retains that behavior when duration capabilities are absent and asks
for a Runtime upgrade to configure duration. Older Runtime versions retain
individual review and report that quick review is unavailable. Automatic mode can only be
enabled when Runtime explicitly declares a supported read-only tool scope;
unknown or broader scopes still allow the owner to turn automatic mode off.
Enabling, disabling, changing duration, expiry, restart restoration, and storage
failures are recorded with mode/duration metadata. They also enter Runtime's
registered `review` operation events for the unified log. The page's visit time
uses the existing `screen` event. Recent mode changes and batch
or automatic decisions are recorded on the computer; the page shows the latest 50
entries for the conversation. The file retains at most 200 entries across
conversations, without command bodies or file contents. A storage failure stops
automatic approval and leaves a visible error; refresh to reconcile an interrupted
submission. Pairing, messages, and existing approval protocols are unchanged.
Build/host checks do not establish phone installation, real first-response timing,
or multi-client acceptance.

## Helper execution records and updates (0.20.0)

Task details show recent public helper activity and a paged record browser with
selectable details: dispatch, progress text, tool start/result and round end.
Runtime 0.12.0 advertises `task_activity` and sends `kind=activity` messages through
`/v1/sync`. Events are independent of conversation entries and the five-line
progress preview. The Runtime keeps at most 2,000 activity records separately
from chat retention; individual details are limited to 4,000 characters and
labelled when truncated. Only newly observed public events are available;
provider reasoning is excluded. Older Runtimes show their recent progress lines.

Internet downloads keep partial bytes and automatically try the paired Runtime
after transient network failures. Valid capped byte ranges continue to the next
range. Hash/protocol/authorization failures remain visible. The same trusted
size and checksum apply to both sources; Runtime credentials stay on its URL.

The [agent gateway proposal](../docs/development/agent-gateway.md) covers future
App-controlled CLI/harness installation; no installer is included in this version.

## App fixes (0.18.0)

- Computers in one Xiaoyou mesh (runtime 0.10.0 or newer, same `subject_id`) count
  as one: when the selected one cannot be reached, the app tries its other
  addresses and then another member, which holds the same conversation and tasks.
  Approvals are watched once per mesh (and once per computer for several addresses),
  so the same approval no longer notifies twice; if the watched member keeps
  failing, another member takes over.

## App fixes (0.17.4)

- What you say inside a computer session goes to Xiaoyou first: she explains or
  summarizes it herself, and the session only continues when you name its helper
  ("let cc continue…") or she decides it should do work. The composer no longer
  refuses while the computer is still running a turn; the hint and the notice
  explain this. Needs runtime 0.10.1.

## App fixes (0.17.3)

- A computer session or reported session that has stopped updating stays in the
  current list as "state unknown" instead of looking finished: the app asks `/v1/sync`
  for the `unavailable` state. Needs runtime 0.9.1; with an older runtime nothing
  changes.

## App fixes (0.17.1)

- Sessions reported through `/v1/reports` from elsewhere (claude.ai on the web, cloud
  sessions, other computers) appear under their helper read-only: no composer, and a
  note to continue where they run. Needs runtime 0.9.0.

## App fixes (0.17.0): incremental updates

- An update now has two parts: **the APK carries only code** (about 17 MB for arm64,
  down from about 194 MB) and **the speech models are the `speech` resource pack**.
  Packs are published per file by checksum; the phone compares with what it has and
  downloads only files that changed, so an update without model changes downloads no
  models. Other large resources can use the same mechanism later.
- Both the APK and the speech models can be downloaded from the internet (GitHub) or
  from the computer, chosen each time; without an internet address the computer is
  used directly. Both resume, and files replace the old ones only after verification.
- Download the speech models under Connections and settings → Phone speech models.
  Until then, holding the microphone only records and the computer recognizes it.
- Needs runtime 0.8.3 (`/v1/app/packs`, `/v1/app/file`). After upgrading from 0.16.0
  the models are downloaded once.
- Covered by build and host tests; downloading, loading and recognizing on a phone
  still need on-device acceptance.

## App fixes (0.16.0)

- Cards and sync progress are kept per computer: switching to another computer and
  back continues incrementally instead of starting over (kept while the app runs; an
  app restart fetches once from the start).
- Understands `turn_ended` from `/v1/sync` (runtime 0.8.2): a desktop session that
  finished a turn shows "turn ended", not a finished task.
- The recent turns carried to another computer are computed from every computer's
  chat history instead of a separate list; the list older versions kept is only read
  to fill gaps.
- Covered by build and host tests; keeping cards across a computer switch has no unit
  test and needs on-device confirmation.

## App fixes (0.15.0)

- The home chips, the sidebar and the helper page share one helper state; a session
  running on the computer shows as "running on the computer". The row scrolls
  sideways when there are many helpers. A ring around each portrait shows the quota
  left (the tighter of the 5-hour and weekly windows): full and green, shrinking as
  it is spent, amber then red near the end; home no longer shows energy text.
- "Sending" and "thinking" are small grey captions under the bubble; a reply with no
  text yet draws no empty bubble.
- The Codex pixel portrait is vertically centered in its frame.
- With a runtime that offers `sync_usage` (0.8.1 and later) the app uses `/v1/sync`:
  per-message increments, so another phone's new message and a reply still being
  written also appear. Addresses of one computer (LAN, Funnel) are recognized as one
  by `runtime_id`: switching keeps cards and chat, and a failing address falls over to
  another. Older runtimes keep `/v1/feed`.
- Sending no longer uploads other conversations first (that now happens in the
  background on connect); the unused plain-text chat list is gone.
- Covered by build and host tests; installation, real recording and two phones
  converging still need on-device acceptance.

## Companion fixes (0.13.0)

Conversation history is retained separately from the live task list. Streaming text
updates existing bubbles, and late partial text cannot replace a synced final reply.
The home and helper pages show real 5-hour and weekly quota as energy, including
shared-account, unknown, expired-window and offline-cache states. API billing is
shown without a fabricated percentage.

Phone microphone recognition in 0.13.0 includes its own Chinese model instead of
requiring a platform recognizer. It runs only on the phone and release sends text. Read requests and
message submissions carrying a deduplication ID retry transient transport errors
up to twice, preserving the original ID and body so a lost response cannot
submit the task again. Authentication and other HTTP errors are reported directly.
Foreground update checks retry after
connection failures and run every minute while online. Cached installers are
verified again before installation and releases stay bound to their Runtime.
These paths have build and host coverage; microphone interaction, history scroll
stability and the Android installation flow still require phone acceptance.

## Bundled phone speech dependencies

From 0.17.0 these models are no longer packaged in the APK; they are the `speech`
resource pack, downloaded once (see "App fixes (0.17.0)").
From 0.14.0, final recognition uses the same unchanged SenseVoice int8
2024-07-17 model as the computer's configured recognizer: automatic language
selection, inverse text normalization, and two CPU threads. The small Chinese
Zipformer CTC int8 2025-04-01 model remains for provisional streaming text.
Final recognition supports Mandarin, Cantonese, English, Japanese, and Korean;
using identical weights does not establish identical microphone quality,
accuracy, or latency on a phone.

Microphone capture starts independently of model loading and retains startup
audio. Utterances of at least 100 ms reach the model; empty taps and empty
recognition still report errors. Release sends one final result; cancellation
and leaving the screen discard it. The maximum recording remains 30 seconds.

The build runs `android/tools/prepare_phone_speech.py` with pinned URLs and
SHA-256 hashes in `android/tools/phone-speech.json`. It downloads sherpa-onnx
1.13.8 and both unchanged models into `app/build/phone-speech/`. The first build
needs network access; later builds can use verified caches. Both models are
inside the APK, with no first-use download or server transcription request.
The SenseVoice model alone is about 228 MiB before APK compression.

Upstream: [Android library](https://github.com/k2-fsa/sherpa-onnx/releases/tag/v1.13.8),
[SenseVoice model](https://k2-fsa.github.io/sherpa/onnx/sense-voice/pretrained.html),
[streaming model](https://k2-fsa.github.io/sherpa/onnx/pretrained_models/online-ctc/zipformer-ctc-models.html).
The engine and preview model use Apache-2.0. SenseVoice weights use the separate
[FunASR Model Open Source License Agreement](https://github.com/modelscope/FunASR/blob/main/MODEL_LICENSE).
Model names, authors, license texts, and ONNX Runtime's MIT license are included
in the APK's `licenses/` assets. Kotlin standard library 1.9.20 supports the
engine's Android binding.

## App updates

When the computer has published a newer build (`python3 -m xiaoyou_runtime app
publish`, see the runtime README), the app says so once and the sidebar footer
shows that a new app is available. Connections and settings → App update downloads it through
the runtime, checks its SHA-256 and opens Android's installer; the first time,
Android asks you to allow Pocket Hub to install apps. Installing always needs
your confirmation on the phone.

From 0.13.1, the build produces standalone APKs for `arm64-v8a`, `armeabi-v7a`,
`x86`, and `x86_64`, plus a universal APK. Each contains the same offline speech
model and assets. The app requests its native ABI, falling back to the universal
package when that variant is unavailable. Without an external download URL, downloads remain on the paired Runtime.
From 0.14.0, an explicitly published HTTPS download URL can serve the APK
from GitHub Releases or another host. The Runtime still supplies the trusted
version, size, and SHA-256; external hosts and their HTTPS redirects never
receive the Runtime token. Downloads remain resumable and hash-verified.

Interrupted downloads keep a hash-named partial file and resume with HTTP Range,
including after restarting the app. There are at most three attempts per download
action; another tap continues retained bytes. The expected SHA-256, ETag, byte
range, and size must agree before Android can install anything. An older Runtime
that returns the full file instead of a range is supported by overwriting the
partial. A replaced release requires checking for updates again. Older apps
cannot resume, even when the Runtime supports it.

0.14.0 raises the App and Runtime APK limit from 128 MiB to 512 MiB for the
bundled SenseVoice model. Older installed updaters still have the 128 MiB limit;
for this first large upgrade, use the published GitHub HTTPS download link in
the phone browser and confirm Android's installer. Later upgrades can use the
new in-app downloader. An external hosting change does not move conversations,
recordings, or Runtime credentials to that host.

### Shipping an app change (developers and AI agents)

Every finished change to `companion/android` reaches the owner's phone this
way, not as an APK file to sideload:

1. **Bump the version** in `companion/android/app/build.gradle`: `versionCode`
   +1, and `versionName` (patch for fixes, minor for features). The phone
   compares version codes; a forgotten bump still works only if the package
   differs and is published later than the phone's install.
2. **Build and test** in `companion/android` with JDK 17 and the Android SDK:
   `gradle testDebugUnitTest assembleDebug`. Both must pass; do not publish a
   failing build. The APK is `app/build/outputs/apk/debug/app-universal-debug.apk`, with
   Gradle's `output-metadata.json` beside it.
3. **Publish** it on the computer that runs the owner's runtime (from `runtime/`):

   ```bash
   apk_dir=../companion/android/app/build/outputs/apk/debug
   python3 -m xiaoyou_runtime app publish "$apk_dir/app-universal-debug.apk" \
     --variant "arm64-v8a=$apk_dir/app-arm64-v8a-debug.apk" \
     --variant "armeabi-v7a=$apk_dir/app-armeabi-v7a-debug.apk" \
     --variant "x86=$apk_dir/app-x86-debug.apk" \
     --variant "x86_64=$apk_dir/app-x86_64-debug.apk" \
     --note "one line the owner understands"
   python3 -m xiaoyou_runtime app status
   ```

   Publishing now uploads every supplied APK to GitHub Releases by default
   (the owner authorized public hosting). Asset names include the content hash,
   so republishing a version cannot reuse different old bytes. `--github ABI,...`
   limits the uploaded architectures; `--local-only` deliberately skips hosting.
   Upload failure preserves the previous local publication. This uploads files,
   never Git commits. Publish the speech pack again only when its files change:

   ```bash
   python3 -m xiaoyou_runtime app pack speech ../companion/android/app/build/phone-speech/assets/asr
   ```

   Pack files are stored and uploaded by checksum: unchanged files are never uploaded
   again, and the phone downloads only files that changed.

   The note is shown on the phone, so write what changed for the owner, not
   commit jargon. Publishing only replaces the file the running runtime serves;
   it needs no restart. A runtime older than this feature (no `/v1/app`) needs
   one restart, and only when no task is running.
4. **Tell the owner** the version and note, and that the phone will offer it on
   the next open (or from Connections and settings → App update). Report that
   the install on the phone has not been verified until they confirm it.
5. **Commit; push only when asked.** Publishing does not push to Git and a
   Git push does not publish.

For an already uploaded HTTPS release asset, add `--local-only --download-url HTTPS_URL` for the
default APK and `--variant-download-url ABI=HTTPS_URL` for each hosted variant.
Upload exactly the verified APK first; the Runtime records its local size/hash
and keeps local serving available for older clients. A variant without its own
URL uses its Runtime download path rather than another ABI's URL. Public hosting
needs owner authorization and a check that the APK contains no private assets.

The positional APK is the default used by older apps, which send no ABI. Use
the universal default unless every legacy phone on that private Runtime is known
to support a specific ABI. For a known ARM64 phone, the ARM64 APK may be the
default to reduce its first upgrade; also publish `--variant universal=...` and
the other ABI packages. This is an explicit deployment choice, not an automatic
assumption about unknown phones. Variant packages need matching Gradle metadata
(version and ABI). Restart an idle Runtime with variant/Range support before
publishing these packages. The CI download keeps its existing universal filename.

When a change also needs the runtime (new endpoints the app calls), restart
the runtime before publishing the app, or the new app will meet the old
runtime. The first build with this feature (0.11.0, version code 17) had to be
installed by hand; phones still on an older build need one manual install.

## Device firmware

The matching firmware and App use the retained `avatar-whale-v1.png` pixel head
portraits. Firmware compiles six expressions into a 60 by 60 indexed atlas
(21,856 Flash bytes, no decode buffer), keeping the existing 120, 40 and 60
display-pixel sizes and sleep, idle, busy, ask, happy, and oops moods. The sleeping
head follows the approved rounded face with relaxed brows. Sleep, idle, ask and
happy breathe over a 3.6-second loop, moving at most four display pixels on the
home screen.
The `hub` acknowledgement announces `desktopApproval: true`. A heartbeat
`prompt.actionable` defaults to `true` for compatibility; when `false`, new
firmware shows a read-only confirm-on-phone-or-computer notice without approval
actions. For
firmware without `desktopApproval`, the App sends that notice as a text summary
instead of an actionable prompt. Actual approvals continue to use the existing
`permission` response and are routed by their originating request.
A source or operation that does not fit is clipped and marked, and can still be
answered on the device; only `actionable` decides that.

The device is only connected to the phone, so the phone carries new firmware
across. The computer (the runtime) keeps every version and records which one
the device should run; the app asks the device which version it runs when it
connects, tells the computer, and keeps watching the computer after that. When
the two differ, the app fetches that version's image, checks its SHA-256, and
sends it to the device over Bluetooth, a minute or two. After the device
restarts the app connects again and confirms the new firmware (without that
within three minutes the device goes back to the previous version by itself)
and reports the outcome to the computer. If the connection drops halfway, the
transfer continues after reconnecting instead of starting over. When the
version the computer wants is still in the device's other slot, nothing is
transferred: the device switches back in seconds.

The "device firmware" section of the screen shows where things stand. Tap the
button that lists the versions kept on the computer and then one of the
versions to put the device on that version (it asks first); old versions that could not be replaced over Bluetooth
once installed are not offered. This is rarely needed: saying a sentence to the
device, or running `firmware restore` on the computer, does the same.

The device needs firmware that already has this feature. Older firmware is
recognised, and the app says that one flash by cable is needed first. The
protocol is in
[`docs/claude-pocket.md`](../docs/claude-pocket.md#replacing-the-firmware-over-bluetooth),
the computer's side in [`runtime/README.md`](../runtime/README.md#device-firmware).

## Privacy

Notification text of registered sources is held in memory, sent only to the
paired device over an encrypted Bluetooth link, and never written to storage or
sent over the network. Notifications from other apps are ignored.

The app uses the network for one thing: talking to the runtime address you
paired. It sends what you type in the chat box, reads the answer and the
cards (including recent visible desktop conversation content when observation
is enabled), and sends your answers to approval requests and your cancellations. Voice recordings from the device take
the same route and are not kept on the phone. That connection is
plain HTTP, so anyone on the same network can read it; use it on a network you
trust. The runtime addresses and tokens, and the 20 most recent turns of the
conversation, are kept in the app's private storage.

Device firmware is downloaded from the runtime over the same plain HTTP
connection, and the app only checks that it matches the SHA-256 in the
runtime's list; images are not signed. Whoever can change the firmware list on
the runtime can therefore have the app write any image to the device.

## Layout

| Path | Role |
| --- | --- |
| `android/app/src/main/java/.../BuddyProtocol.java` | Wire format; plain Java, unit tested |
| `.../Sources.java` | Registry of sources (package name, label, enabled) |
| `.../HubStore.java` | In-memory feed, what DS is doing right now, the runtime's cards and approvals, pending questions, log |
| `.../Card.java`, `.../Json.java` | The card of one thing and an approval, and the JSON parser that reads them; plain Java, unit tested |
| `.../NotifyListener.java` | Notification listener service |
| `.../BleLink.java`, `.../LinkService.java` | BLE central and the foreground service that keeps it alive |
| `.../VoiceRecording.java` | Decodes voice frames from the device into a WAV recording; plain Java, unit tested |
| `.../RuntimeClient.java` | Sends chat messages and voice recordings to the Runtime, waits for cards to change, answers approvals, cancels a thing, and asks which agents and which firmware versions the runtime has |
| `.../FirmwarePush.java` | One transfer of a firmware image to the device: what to send, when, and where to resend from; plain Java, unit tested |
| `.../FirmwareSync.java` | Keeps the device on the version the runtime names: asks the device, watches the runtime, fetches, transfers, confirms, reports |
| `.../CardViews.java` | Which cards are conversation and which are tasks, and how they are worded on the phone; plain Java, unit tested |
| `.../MainActivity.java` | Sidebar, chat, task and helper pages, connection and settings, approval detail and composer |
| `.../AppPresentation.java`, `.../AvatarView.java`, `.../NavIconView.java` | Tested role, sidebar order and helper rules, normalized portraits and native icons |
| `.../ChatPlan.java`, `.../Motion.java`, `.../ReplyMarker.java`, `.../Sheet.java` | Tested incremental list updates, shared motion timings, the reply marker and bottom sheets |

## Status

The sidebar redesign passed `assembleDebug` and the unit tests. It has not been
installed or visually accepted on a phone.

The chat-first Android UI passed `assembleDebug`, 22 unit tests and Lint (zero
errors). The complete repository gate also passed. It has not been installed or
visually accepted on a phone; keyboard layout, navigation, companion selection
and live approval round trips still need device acceptance.

For 0.10.0, the Android build, host tests, and complete repository validation
gate passed. DS attendance, task pinning, multi-Runtime approval routing, and
attachment integrity have host coverage. On the development computer, the
Codex and Claude Code hook handlers were merged while preserving existing
configuration; Codex still requires the user to review and trust the current
definition through `/hooks`. A live desktop approval round trip, Android
background delivery, image/file saving and opening on a phone, and the new
usage-page navigation and source-switch behavior remain device acceptance
checks. The earlier sleeping-face revision was flashed and its face shape and
breathing were confirmed by the user; that does not validate the new Usage page.

The protocol layer is unit tested and its output is accepted by the firmware's
parser. The app compiles against the Android 14 API, and its runtime request
code was run on a desktop Java runtime against a local runtime. The voice decoder is checked sample by sample against frames
produced by the firmware's encoder.

On a phone (2026-10-08, one Android device, version 0.2.0): Bluetooth pairing,
the connection to the device, and a typed message answered by the runtime and
shown on the device worked. Not verified: voice from the device and
switching between runtimes (versions 0.3.0 and 0.4.0 have not been run on a
phone), the conversation on the device's home page and the helpers list
(version 0.5.0, which needs the matching firmware; not run on a phone),
replacing the firmware over Bluetooth (version 0.6.0; the transfer itself was
run on a desktop Java runtime against the firmware's own protocol code,
including lost frames and resuming, but never on a phone against a real device,
and the speed Bluetooth actually reaches has not been measured),
cards, tasks, approvals and the three-screen firmware (version 0.7.0, which
needs runtime 0.5 or newer and the matching firmware; compiled in the build
only, not run on a phone),
the separate conversation and task pages and continuing inside a task
(version 0.8.0; the split and the request body are unit tested, the screen
was compiled in the build only and not run on a phone),
usage, tiers and task history (version 0.9.x; the protocol and the usage
summary are unit tested, the screens were compiled in the build only; the
device shows its fourth screen only after the phone has received usage from
the runtime during the current connection, and three screens otherwise),
reconnecting by itself after the app is
restarted, background behavior over hours, and real assistant notifications.
