<p align="right">
  <a href="README.zh_CN.md">简体中文</a> · <strong>English</strong>
</p>

# Pocket Hub companion

Pocket Hub is an Android app that sits between the phone's AI assistants and
the Claude Pocket device. It reads the notifications of registered apps
(Claude and Codex by default), keeps the recent ones as a small feed, and sends
that feed to the device over Bluetooth LE. The app speaks the same Hardware
Buddy wire protocol that the Claude desktop app does, plus the hub extension
described in the [firmware documentation](../docs/claude-pocket.md#the-conversation).

The app also has a chat box for talking to Xiaoyou. A message typed there goes
to the [Xiaoyou Runtime](../runtime/README.md) over HTTP. Every request you
make is one thing with a card on the runtime; the app keeps waiting for the
runtime to say which cards changed (`/v1/feed`), shows them, and reports them
to the device: what Xiaoyou is doing right now, the card of every thing, the
things being worked on and their two latest steps. When a helper wants to do
something that needs your consent, who wants what is shown verbatim at the top
of the app and on the device, and either side can answer. The app also tells
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

## What reaches the device

| On the phone | On the device |
| --- | --- |
| A message sent to Xiaoyou from the chat box | Screen 1: what you said, Xiaoyou thinking, the agent she handed the work to, then a brief of a sentence or two |
| The cards on the runtime | Screen 2 pages through conversation only (what Xiaoyou answered herself): the 8 most recent, plus every one still being worked on or on screen 3's list; on firmware that shows them apart a task's card carries that thing's latest exchange (at most 480 bytes), for the page opened from screen 3. Each holds the first sentence of the thing and the latest thing Xiaoyou said (the first 959 bytes; the full exchange stays on the phone) |
| The things handed to helpers | Screen 3: at most four, those in progress first (who is doing it, for how long and the two latest steps, tool names and commands verbatim), followed on firmware that announced `threads` by the latest that ended (the brief of the conclusion); the count in the top bar. A sentence said while holding the key on screen 3 carries `pin`, which the app passes to the runtime as it is: it is always filed on the selected thing |
| An operation a helper wants confirmed | Xiaoyou asks, who wants what is shown verbatim; `OK` says yes, `DOWN` says no, and the answer goes back to the runtime |
| A voice message recorded on the device | The same as a typed message; what was heard appears on the device and in the chat box once the runtime has transcribed it |
| The agents of the selected runtime | The helpers page in the menu (the first four) |
| A notification from a registered source | An entry on the notices page in the menu |
| Notifications not yet dismissed | The idle first screen says how many |
| A notification with both an allow-like and a deny-like button | The pet asks; `OK` presses the allow button, `DOWN` the deny button |
| Nothing new | A keepalive every 10 seconds so the device stays connected |
| A version of the device firmware chosen on the computer | The device shows Xiaoyou "changing into something new" with a progress bar and restarts by itself when done; see [Device firmware](#device-firmware) below |

When the app introduces itself the device announces what it understands. One
that announces `cards` runs the three-screen firmware and gets everything
above; one that announces only `chat` runs the previous version, and the app
sends it the current turn without cards or tasks; an older one announces
neither, and the app falls back to what it sent first: the summary line and
the full text of the latest message as a `turn` event. Approval requests work
on all three.

A runtime older than 0.5.0 has no cards and no approvals: the app's log says
it is an old version, and the chat is listed line by line as before.

### Conversation and tasks apart (0.8.0)

The app has three pages, chosen with the row of buttons at the top:

- **Conversation**: what Xiaoyou answered herself, as one continuous
  exchange. A thing handed to a helper takes a single line here (its number,
  helper, state and title); tapping it goes to that thing's own page.
- **Tasks**: the things that were handed to a helper, the ones still
  going first. Opening one shows all of it (what you said, what Xiaoyou said,
  the helper's latest steps). A sentence sent from inside an open task carries
  that thing's number and `pin`, so the runtime always files it on that thing
  and continues its own session; it does not show up in the conversation page
  and does not open another thing. Tapping the tasks button again, or Back,
  returns to the list.
- **Settings**: which computer answers, device firmware, the three
  setup steps, sources, tests and the log.

Operations waiting for consent are shown at the top of every page, with a
button that opens the thing they belong to. The split only looks at whether a
card has a helper: none means conversation, one means task. `pin` needs
runtime 0.5.3; an older runtime ignores it, and then Xiaoyou still decides
which thing a sentence said inside a task belongs to.

Only what a notification contains can be forwarded. Whether a given assistant
posts notifications for cloud sessions, and whether they carry action buttons,
depends on that assistant's app and has not been verified.

## Install and set up

Each push that touches `companion/` builds a debug APK in GitHub Actions and
attaches it to a prerelease named *Pocket Hub debug build N*. Download
`pocket-hub-debug.apk` on the phone and allow installation from the browser.

In the app, in order:

1. Grant Bluetooth permission.
2. Enable notification access for the app in system settings.
3. Connect. When the device shows a six-digit passkey, type it on the phone.

To talk to Xiaoyou, start the runtime on a computer on the same network, run
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
cards, and sends your answers to approval requests and your cancellations. Voice recordings from the device take
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
| `.../HubStore.java` | In-memory feed, what Xiaoyou is doing right now, the runtime's cards and approvals, pending questions, log |
| `.../Card.java`, `.../Json.java` | The card of one thing and an approval, and the JSON parser that reads them; plain Java, unit tested |
| `.../NotifyListener.java` | Notification listener service |
| `.../BleLink.java`, `.../LinkService.java` | BLE central and the foreground service that keeps it alive |
| `.../VoiceRecording.java` | Decodes voice frames from the device into a WAV recording; plain Java, unit tested |
| `.../RuntimeClient.java` | Sends chat messages and voice recordings to the Xiaoyou Runtime, waits for cards to change, answers approvals, cancels a thing, and asks which agents and which firmware versions the runtime has |
| `.../FirmwarePush.java` | One transfer of a firmware image to the device: what to send, when, and where to resend from; plain Java, unit tested |
| `.../FirmwareSync.java` | Keeps the device on the version the runtime names: asks the device, watches the runtime, fetches, transfers, confirms, reports |
| `.../CardViews.java` | Which cards are conversation and which are tasks, and how they are worded on the phone; plain Java, unit tested |
| `.../MainActivity.java` | Three pages: conversation, tasks (the list and the open one), settings; operations waiting for consent, the input box |

## Status

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
