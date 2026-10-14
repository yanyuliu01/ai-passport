<p align="right">
  <a href="README.zh_CN.md">简体中文</a> · <strong>English</strong>
</p>

# DS companion (Pocket Hub)

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
and answers it like a typed message. The app does not recognize speech itself.
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

- **Chat with DS** — DS's own conversation. A request she hands off leaves a
  one-line receipt (helper portrait, title, current step) that opens the task.
  Conversations started on the computer never appear here.
- **Tasks** — work DS handed to a helper, newest first. A task's state never
  changes its position, so nothing moves while you read; pinned tasks stay on
  top. Long-press a task (or use ⋯ on its page) to pin, archive or stop it.
  Pins and archives are kept on the phone per computer; archived tasks can be
  restored from the "Archived" row. There is no rename: titles come from the
  runtime so the device, phone and computer agree.
- **Helpers** — one row per helper (Codex, Claude, then others) with its state.
  A helper's page lists what DS handed to it, each linking back to its task, and
  the conversations opened on the computer (read-only).
- **Connection and settings** — the device and computer connection, companion
  look, usage, firmware, notifications and preferences.

A task's page shows its steps (DS splits the request, the helper works with its
latest progress, DS reports back), then the task's conversation; messages sent
there continue that task. Desktop conversations are read-only and say so.
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
reconnecting by itself after the app is
restarted, background behavior over hours, and real assistant notifications.
