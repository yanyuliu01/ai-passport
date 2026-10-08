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

The hub is its own participant, not a Claude feature. It knows nothing about
any particular assistant; a source is just an Android package name and a
display label, and more can be added in the app.

## What reaches the device

| On the phone | On the device |
| --- | --- |
| A notification from a registered source | Home summary line, recent-activity entry, and its full text on the latest-reply page |
| Notifications not yet dismissed | The waiting count; the pet asks for attention |
| A notification with both an allow-like and a deny-like button | The pet asks; `OK` presses the allow button, `DOWN` the deny button |
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

The device accepts one Bluetooth connection at a time. Disconnect it from the
Claude desktop app (or move away from that computer) before connecting the
phone.

## Privacy

The app has no network permission. Notification text of registered sources is
held in memory, sent only to the paired device over an encrypted link, and
never written to storage. Notifications from other apps are ignored.

## Layout

| Path | Role |
| --- | --- |
| `android/app/src/main/java/.../BuddyProtocol.java` | Wire format; plain Java, unit tested |
| `.../Sources.java` | Registry of sources (package name, label, enabled) |
| `.../HubStore.java` | In-memory feed, pending questions, log |
| `.../NotifyListener.java` | Notification listener service |
| `.../BleLink.java`, `.../LinkService.java` | BLE central and the foreground service that keeps it alive |
| `.../MainActivity.java` | Setup, source list, test buttons, log |

## Status

The protocol layer is unit tested and its output is accepted by the firmware's
parser. The app compiles against the Android 14 API. It has not been run on a
phone yet: Bluetooth pairing, background behavior on vendor-modified Android
systems, and real assistant notifications are all unverified.
