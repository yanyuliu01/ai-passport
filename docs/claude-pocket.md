<p align="right">
  <a href="claude-pocket.zh_CN.md">简体中文</a> · <strong>English</strong>
</p>

# Claude Pocket

Claude Pocket turns the FoloToy AI Passport into a wearable companion for the
Claude desktop app. It connects over encrypted Bluetooth LE, shows what Claude
is doing, and lets you approve or deny a permission request with one button.

It speaks the public *Hardware Buddy* BLE protocol documented by Anthropic in
[`anthropics/claude-desktop-buddy`](https://github.com/anthropics/claude-desktop-buddy).
That bridge exists only in Claude for macOS and Windows with Developer Mode
enabled; Anthropic describes it as a maker feature, not a supported product
feature. The Claude mobile apps do not expose it, so the device works within
Bluetooth range of the computer that runs Claude.

## What it does

| Capability | Behavior |
| --- | --- |
| Status | Idle, working, waiting for a decision, link state, session counts |
| Latest reply | The leading text of Claude's most recent turn (first 419 bytes) |
| Recent activity | The four most recent transcript lines sent by the desktop app |
| Usage | Output tokens today and since app start, approvals, denials, uptime, battery |
| Approvals | The pet asks; the tool name and the argument hint are shown verbatim; approve once or deny on the device |
| Pairing | LE Secure Connections with a six-digit passkey shown on the device |

The interface is built around a pixel pet, a small ghost. Its face shows the
state on the home page: asleep while nothing is connected, idle, busy, asking
for you, pleased after an approval, and upset after a failed send. Permission
requests, pairing, and destructive confirmations are presented as the pet
talking to you in a chat bubble, and your key press appears as your reply.

The device cannot send prompts or audio to Claude: the protocol has no message
for that. See [Roadmap](#roadmap).

## Pair with Claude desktop

1. In Claude desktop choose **Help → Troubleshooting → Enable Developer Mode**.
2. Choose **Developer → Open Hardware Buddy…** and click **Connect**.
3. Pick the device named `Claude-XXXXXX`; the same name is shown on the device
   home page and in its connection guide.
4. Type the six digits shown on the device into the operating system's
   Bluetooth prompt.

After bonding, the device reconnects on its own. If it does not appear in the
list, check that Bluetooth is switched on in the device settings.

## Controls

The three buttons are `UP`, `DOWN`, and `OK`.

| Where | `UP` / `DOWN` | `OK` click | `OK` long press |
| --- | --- | --- | --- |
| Home, Latest reply, Recent activity, Usage | Previous / next page (wraps) | Return to Home | Open Settings |
| Settings | Move the selection (wraps) | Run the selected item | Return to Home |
| Connection guide | Scroll three lines | Back to Settings | Return to Home |
| Permission request | `UP` pages through the hint; `DOWN` denies | Approve once | — |
| Unpair / factory-reset confirmation | `DOWN` cancels | Confirm | — |
| Screen off | Any click turns the screen on and does nothing else | | |

A permission request, a pairing passkey, or a confirmation takes over the
screen and turns it back on if it was off. A key press only counts for the
request that was on screen when the key went down; a press that races a new
request is dropped rather than applied to it. Each request accepts one
decision, and a failed send is shown as failed, never as approved.

The pet only phrases the question. The tool name and the argument hint come
straight from the desktop app and are never paraphrased; a tool name too long
for the bubble is repeated in full at the top of the hint card.

A quick double press counts as one click.

Settings offers brightness (five steps), the Bluetooth switch, the connection
guide, screen off, unpair, and factory reset. Brightness returns to maximum
after a restart.

## Text and fonts

All fixed interface text is Simplified Chinese and lives in
[`main/pocket_text.h`](../main/pocket_text.h). Fonts are generated from Noto
Sans SC by [`tools/gen_pocket_fonts.py`](../tools/gen_pocket_fonts.py); see the
[assets documentation](../assets/README.md#fonts).

Text that arrives from the computer (status line, activity, reply, tool name,
hint) is drawn with a font that covers printable ASCII and all 6,763 GB2312
hanzi plus common punctuation. Anything outside that set, such as emoji,
traditional-only characters, or other scripts, is drawn as a placeholder box;
it is not dropped or replaced. Text longer than its area ends with an ellipsis,
and a hint the desktop sent longer than 319 bytes is marked as truncated. LVGL
does not apply CJK line-breaking rules, so a wrapped line can begin with a
punctuation mark.

## Stored data

The device stores its name, the owner name sent by the desktop app, the
Bluetooth switch, approval and denial counters, and the BLE bond. Replies,
activity lines, hints, and request identifiers stay in RAM and are cleared when
the link drops or no heartbeat arrives for 30 seconds.

## Not implemented

- Folder push (`char_begin` and related commands) is refused with an error ack.
- There is no "always allow" decision; the device sends `once` or `deny`.
- Sound, microphone, Wi-Fi, and low-power sleep are not used by this application.

## Memory

The board has no PSRAM. LVGL allocates from the system heap instead of a fixed
pool, so the interface, Bluetooth, and task stacks share one budget. The
firmware logs `heap: free=… min=…` once a minute (a warning below 12 KB); read
it during on-device acceptance.

## Code map

| Path | Role |
| --- | --- |
| `main/main.c` | Queues, the application task, BLE and settings glue |
| `main/buddy_ble*.c` | NimBLE peripheral: Nordic UART Service, security, bonding |
| `main/buddy_line.c`, `main/buddy_protocol.c` | Line assembly and bounded JSON parsing/serialization |
| `main/buddy_state.c` | Link, approval, and navigation state machine |
| `main/buddy_orchestrator.c`, `main/buddy_app_logic.c`, `main/buddy_settings.c` | Command handling, queue policy, NVS settings |
| `main/pocket_view.c` | Pure view selection, pet mood, and number/time formatting |
| `main/pocket_pet.c` | The pet's 20 × 20 pixel sprite and its moods (no LVGL) |
| `main/pocket_ui.c` | LVGL screens |
| `tools/ui_preview/` | Host renderer that draws every screen with the real LVGL and fonts |

The BLE, protocol, and state layers are adapted from the `demo/claude-buddy-port`
branch; navigation was redesigned and assistant-turn parsing was added. The
interface is new and shares no screens with that branch or with the baseline
hardware-test menu.

## Build, test, and preview

```bash
./tools/validate.sh            # host tests, firmware build, merged-image check
python3 tools/gen_pocket_fonts.py   # after editing main/pocket_text.h
cmake -S tools/ui_preview -B /tmp/ui_preview && cmake --build /tmp/ui_preview
/tmp/ui_preview/ui_preview /tmp/ui_preview   # writes one PPM image per screen
```

The protocol, state-machine, and orchestrator host tests need cJSON from
`IDF_PATH`; without it the static gate reports them as skipped.

## On-device acceptance

A successful build and the host preview are not device validation. Check on
hardware:

1. Startup shows the home page; the top bar shows link state and battery.
2. Pairing: the passkey page appears, the passkey is accepted, and the device
   reconnects without a passkey after a restart of either side.
3. Every page and every state renders without boxes, clipping, or overlap,
   including long mixed Chinese/Latin text.
4. A permission request takes over the screen; `OK` approves, `DOWN` denies,
   `UP` pages a long hint; the desktop app reflects each decision.
5. A request arriving while the screen is off turns it on.
6. Unpair and factory reset ask for confirmation and behave as described.
7. Link loss (walk out of range, quit the desktop app) returns to the waiting
   state within 30 seconds and clears the reply and activity pages.
8. The `heap:` log line shows a comfortable minimum with Claude connected and
   a permission request on screen, and free heap stays stable over a long session; Bluetooth range and battery
   life are measured rather than assumed.

## Roadmap

1. **Voice path.** The board has a microphone, but the ESP32-C3 has no
   Bluetooth Classic or LE Audio, so it cannot act as a headset, and the
   desktop protocol carries no audio. Voice input needs a separate channel
   that turns speech into text and hands it to Claude; that design is the next
   step.
2. **Phone companion.** A phone app is needed to keep the link when away from
   the computer, because the Claude mobile apps do not offer the BLE bridge.
