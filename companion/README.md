<p align="right">
  <a href="README.zh_CN.md">简体中文</a> · <strong>English</strong>
</p>

# Pocket Hub companion

Pocket Hub is an Android app that sits between the phone's AI assistants and
the Claude Pocket device. It reads the notifications of registered apps
(Claude and Codex by default), keeps the recent ones as a small feed, and sends
that feed to the device over Bluetooth LE. The device needs no firmware change:
the app speaks the same Hardware Buddy wire protocol that the Claude desktop
app does, so the pet shows phone-side messages the way it shows desktop state.

The app also has a chat box for talking to Xiaoyou. A message typed there goes
to the [Xiaoyou Runtime](../runtime/README.md) over HTTP; while the runtime
works the device shows the pet as busy, and when it answers the device gets the
short brief on the home page and the full reply on the latest-reply page.

The hub is its own participant, not a Claude feature. It knows nothing about
any particular assistant; a source is just an Android package name and a
display label, and more can be added in the app.

## What reaches the device

| On the phone | On the device |
| --- | --- |
| A notification from a registered source | Home summary line, recent-activity entry, and its full text on the latest-reply page |
| Notifications not yet dismissed | The waiting count; the pet asks for attention |
| A notification with both an allow-like and a deny-like button | The pet asks; `OK` presses the allow button, `DOWN` the deny button |
| A message sent to Xiaoyou from the chat box | Busy while the runtime works; then the brief, with the full reply on the latest-reply page |
| Nothing new | A keepalive every 10 seconds so the device stays connected |

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
`python3 -m xiaoyou_runtime --pair` there, and paste the printed line
(`http://<address>:<port>#<token>`) into the *Runtime* field of the app.

The device accepts one Bluetooth connection at a time. Disconnect it from the
Claude desktop app (or move away from that computer) before connecting the
phone.

## Privacy

Notification text of registered sources is held in memory, sent only to the
paired device over an encrypted Bluetooth link, and never written to storage or
sent over the network. Notifications from other apps are ignored.

The app uses the network for one thing: sending what you type in the chat box
to the runtime address you paired, and reading the answer. That connection is
plain HTTP, so anyone on the same network can read it; use it on a network you
trust. The runtime address and token are kept in the app's private storage.

## Layout

| Path | Role |
| --- | --- |
| `android/app/src/main/java/.../BuddyProtocol.java` | Wire format; plain Java, unit tested |
| `.../Sources.java` | Registry of sources (package name, label, enabled) |
| `.../HubStore.java` | In-memory feed, pending questions, log |
| `.../NotifyListener.java` | Notification listener service |
| `.../BleLink.java`, `.../LinkService.java` | BLE central and the foreground service that keeps it alive |
| `.../RuntimeClient.java` | Sends chat messages to the Xiaoyou Runtime and waits for the answer |
| `.../MainActivity.java` | Chat box, setup, source list, test buttons, log |

## Status

The protocol layer is unit tested and its output is accepted by the firmware's
parser. The app compiles against the Android 14 API, and its runtime request
code was run on a desktop Java runtime against a local runtime. It has not been
run on a phone yet: Bluetooth pairing, the chat box, background behavior on
vendor-modified Android systems, and real assistant notifications are all
unverified.
