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
to the [Xiaoyou Runtime](../runtime/README.md) over HTTP. The app follows the
turn step by step and reports it to the device: what you said, that Xiaoyou is
thinking, which agent she handed the work to and what she said when she did,
and then her whole answer. The chat box shows the same steps. The app also
tells the device which agents the selected runtime has.

Holding `OK` on the device records a voice message. The app receives the audio
over Bluetooth, forwards it to the runtime, and the runtime turns it into text
and answers it like a typed message. The app does not recognize speech itself.

The hub is its own participant, not a Claude feature. It knows nothing about
any particular assistant; a source is just an Android package name and a
display label, and more can be added in the app.

## What reaches the device

| On the phone | On the device |
| --- | --- |
| A message sent to Xiaoyou from the chat box | The home page: what you said, Xiaoyou thinking, the agent she handed the work to, then her answer (the first 959 bytes; the whole text stays on the phone) |
| A voice message recorded on the device | The same as a typed message; what was heard appears on the device and in the chat box once the runtime has transcribed it |
| The agents of the selected runtime | The helpers page in the menu (the first four) |
| A notification from a registered source | An entry on the notices page in the menu |
| Notifications not yet dismissed | The idle home page says how many |
| A notification with both an allow-like and a deny-like button | The pet asks; `OK` presses the allow button, `DOWN` the deny button |
| Nothing new | A keepalive every 10 seconds so the device stays connected |

A device with firmware from before the conversation pages does not announce
`chat` when the app introduces itself. The app then falls back to what it sent
before: the summary line and the full text of the latest message as a `turn`
event.

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
and does not carry over). The same stored turns refill the chat box after the
app is restarted.

The device accepts one Bluetooth connection at a time. Disconnect it from the
Claude desktop app (or move away from that computer) before connecting the
phone.

## Privacy

Notification text of registered sources is held in memory, sent only to the
paired device over an encrypted Bluetooth link, and never written to storage or
sent over the network. Notifications from other apps are ignored.

The app uses the network for one thing: sending what you type in the chat box
to the runtime address you paired, and reading the answer. Voice recordings from the device take
the same route and are not kept on the phone. That connection is
plain HTTP, so anyone on the same network can read it; use it on a network you
trust. The runtime addresses and tokens, and the 20 most recent turns of the
conversation, are kept in the app's private storage.

## Layout

| Path | Role |
| --- | --- |
| `android/app/src/main/java/.../BuddyProtocol.java` | Wire format; plain Java, unit tested |
| `.../Sources.java` | Registry of sources (package name, label, enabled) |
| `.../HubStore.java` | In-memory feed, the current turn with Xiaoyou, pending questions, log |
| `.../NotifyListener.java` | Notification listener service |
| `.../BleLink.java`, `.../LinkService.java` | BLE central and the foreground service that keeps it alive |
| `.../VoiceRecording.java` | Decodes voice frames from the device into a WAV recording; plain Java, unit tested |
| `.../RuntimeClient.java` | Sends chat messages and voice recordings to the Xiaoyou Runtime, follows each turn step by step, and asks which agents the runtime has |
| `.../MainActivity.java` | Chat box, setup, source list, test buttons, log |

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
reconnecting by itself after the app is
restarted, background behavior over hours, and real assistant notifications.
