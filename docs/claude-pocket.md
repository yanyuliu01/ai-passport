<p align="right">
  <a href="claude-pocket.zh_CN.md">简体中文</a> · <strong>English</strong>
</p>

# Claude Pocket

Claude Pocket turns the FoloToy AI Passport into the wearable face of Xiaoyou,
a small ghost that is the one voice you talk to. The device connects over
encrypted Bluetooth LE to the phone companion; you hold a button, speak, and
her answer appears on the screen. When she hands the work to another agent,
the screen shows who she is talking to.

The link speaks the public *Hardware Buddy* BLE protocol documented by
Anthropic in
[`anthropics/claude-desktop-buddy`](https://github.com/anthropics/claude-desktop-buddy),
with a small extension for the phone companion. Without the extension the same
firmware still works as a companion to the Claude desktop app (macOS and
Windows with Developer Mode enabled; Anthropic describes that bridge as a maker
feature, not a supported product feature).

## What it does

| Capability | Behavior |
| --- | --- |
| The conversation | The home page: Xiaoyou, what you said, and what she says. Her mood is the pet's face |
| Handing work over | While another agent works for her, the page shows a letter travelling from Xiaoyou to that agent's name tag, and a timer. Each agent has its own colour |
| Reading | `DOWN` opens the whole answer (up to 959 bytes, about 320 Chinese characters) and scrolls it |
| Push-to-talk | Hold `OK` to record; the audio goes to the phone companion over Bluetooth; see [Push-to-talk](#push-to-talk) |
| Approvals | Xiaoyou asks; who wants what is shown verbatim; say yes or no on the device |
| Notices | The four most recent entries sent by the host, in the menu. While any are waiting, the idle home page says how many |
| Helpers | The agents Xiaoyou can hand work to, as the host lists them, in the menu |
| Pairing | LE Secure Connections with a six-digit passkey shown on the device |

Colour answers one question: who is doing the work. Xiaoyou is lavender; every
helper has a colour of its own, fixed for a given name. Green, red and yellow
mean yes, no and "look at this", and nothing else.

Connected to the Claude desktop app instead of the phone companion, the home
page shows what the desktop app reports: its status line while it works, and
its latest reply. There is no talking in that mode, because the desktop
protocol has no message for prompts or audio.

## Pairing

With the phone companion (the usual way): open the Pocket Hub app, start the
link, and type the six digits shown on the device into the phone's Bluetooth
prompt. See [`companion/README.md`](../companion/README.md).

With Claude desktop:

1. Choose **Help → Troubleshooting → Enable Developer Mode**.
2. Choose **Developer → Open Hardware Buddy…** and click **Connect**.
3. Pick the device named `Claude-XXXXXX`; the same name is shown on the
   sleeping home page and in the connection guide.
4. Type the six digits shown on the device into the operating system's
   Bluetooth prompt.

After bonding, the device reconnects on its own. If it does not appear in the
list, check that Bluetooth is switched on in the device menu.

## Controls

The three buttons are `UP`, `DOWN`, and `OK`. The bottom line of the screen
always shows what the keys do right now.

| Where | `UP` / `DOWN` click | `OK` click | `OK` hold | `UP` long press |
| --- | --- | --- | --- | --- |
| Home | `DOWN` opens the reader when there is an answer | — | Talk; release to send | Open the menu |
| Reader | Scroll nine lines | Back to Home | Talk; release to send | Open the menu |
| Menu, More settings | Move the selection (wraps) | Run the selected item | Return to Home | Return to Home |
| Notices, Helpers | — | Back to the menu | Return to Home | Return to Home |
| Connection guide | Scroll three lines | Back to More settings | Return to Home | Return to Home |
| Request for a yes or no | `UP` pages through the details; `DOWN` says no | Yes | — | — |
| Unpair / factory-reset confirmation | `DOWN` cancels | Confirm | — | — |
| Screen off | Any click turns the screen on and does nothing else | | Turns the screen on, then talks | Turns the screen on |

A request, a pairing passkey, or a confirmation takes over the screen and turns
it back on if it was off. So does the answer to something you just asked. A key
press only counts for what was on screen when the key went down; a press that
races a new request is dropped rather than applied to it. Each request accepts
one decision, and a failed send is shown as failed, never as approved.

Xiaoyou only phrases the question. Who is asking and what for come straight
from the host and are never paraphrased; a name too long for the bubble is
repeated in full at the top of the details card.

A quick double press counts as one click.

The menu offers notices, helpers, brightness (five steps), the Bluetooth
switch, screen off, and more settings: the connection guide, unpair, and
factory reset. Brightness returns to maximum after a restart.

When the device itself has something to say (a recording was too short, the
link is not ready, a Bluetooth switch failed) the line appears in place of the
key hints for six seconds.

## Text and fonts

All fixed interface text is Simplified Chinese and lives in
[`main/pocket_text.h`](../main/pocket_text.h). Fonts are generated from Noto
Sans SC by [`tools/gen_pocket_fonts.py`](../tools/gen_pocket_fonts.py); see the
[assets documentation](../assets/README.md#fonts).

Text that arrives from the host (what you said, Xiaoyou's words, helper names,
notices, who is asking and for what) is drawn with a font that covers printable
ASCII and all 6,763 GB2312 hanzi plus common punctuation. Anything outside that set, such as emoji,
traditional-only characters, or other scripts, is drawn as a placeholder box;
it is not dropped or replaced. Text longer than its area ends with an ellipsis;
details longer than 319 bytes and an answer longer than 959 bytes are marked
as cut. LVGL
does not apply CJK line-breaking rules, so a wrapped line can begin with a
punctuation mark.

## Push-to-talk

Hold `OK` on the home page or in the reader. Xiaoyou says to wait a moment,
then says she is listening: the level bars move with your voice, which is how
you know the microphone is live. Speak, and release to send. The home page then
shows that the recording is on its way until the host says what it heard. A press shorter than about a third of a second sends nothing. A
recording stops by itself at 30 seconds and is sent as it is.

The device only records and transmits. Turning speech into text and answering
is done by the host: the phone companion forwards the recording to the Xiaoyou
Runtime (see [`companion/README.md`](../companion/README.md) and
[`runtime/README.md`](../runtime/README.md)).

Voice is an extension to the desktop protocol and is off until the host asks
for it, so the Claude desktop app never receives a frame it does not
understand:

| Direction | Message | Meaning |
| --- | --- | --- |
| Host → device | `{"cmd":"hub","voice":true}` | The host accepts voice on this connection. Acknowledged with `{"ack":"hub","ok":true,"chat":true}`; `chat` says the firmware understands the messages under [The conversation](#the-conversation) |
| Device → host | `{"cmd":"voice","state":"start","rate":16000,"codec":"ima-adpcm"}` | A recording begins |
| Device → host | voice frames | One notification per frame; see below |
| Device → host | `{"cmd":"voice","state":"end","frames":N,"dropped":D,"ms":M}` | The recording is complete |
| Device → host | `{"cmd":"voice","state":"cancel"}` | Discard what was received |

A voice frame is one notification on the same TX characteristic as the text
lines: `0xFF`, a sequence byte, the encoder state before this frame (predictor,
two bytes little-endian, and step index), then IMA ADPCM data, low nibble
first. `0xFF` never occurs in UTF-8, so a host tells frames from text by the
first byte of a notification. Audio is 16 kHz mono at 4 bits per sample, about
8 KB/s. Because every frame carries its own decoder state, a lost frame costs
only its own 30 ms.

Holding `OK` while no host is connected, or while connected to a host that did
not announce voice (the Claude desktop app), shows a one-line explanation
instead of recording. The codec is initialized on first use and put to sleep
after every recording.

## The conversation

A host that introduced itself with `hub` reports the conversation with Xiaoyou
in two more messages. They are part of the same extension, so the Claude
desktop app never sends them, and an older firmware answers them with an error
ack that the host ignores.

| Direction | Message | Meaning |
| --- | --- | --- |
| Host → device | `{"cmd":"chat","phase":P,"said":"…","reply":"…","agent":"…","stage":"…","mood":M}` | Where the current turn stands. Only `phase` is required |
| Host → device | `{"cmd":"helpers","list":[{"name":"…","about":"…"}]}` | The agents Xiaoyou can hand work to; at most four are kept. An empty list clears them |

`phase` is `idle` (nothing said yet), `thinking`, `helper` (the agent named in
`agent` is working for her; `stage` is what she said when handing over), `done`
(her answer is in `reply`) or `failed` (`reply` says why). `mood` is `idle`,
`busy`, `ask`, `happy` or `oops` and sets the pet's face when the turn is done.
`said` is what the owner said, empty until a recording has been transcribed.
Limits are 159 bytes for `said` and `stage`, 23 for `agent` and a helper's
`name`, 63 for `about`, and 959 for `reply`; longer text is cut on a character
boundary. These messages are not acknowledged one by one; one that does not
parse gets `{"ack":"chat","ok":false,…}` and changes nothing.

A hub that sends `chat` leaves `msg` empty in its heartbeats and does not send
`turn` events. Once a hub has sent `chat` on a connection, the firmware ignores
its `turn` events and reads the heartbeat counters as notifications only, not
as Xiaoyou being busy. A hub that never sends `chat` (an older phone app) is
shown the way the desktop app is: busy while the counters are non-zero, and the
reply from `turn`.

## Stored data

The device stores its name, the owner name sent by the host, the Bluetooth
switch, approval and denial counters, and the BLE bond. The conversation,
notices, helper names, details of a request, and request identifiers stay in
RAM and are cleared when the link drops or no heartbeat arrives for 30 seconds.

## Not implemented

- Folder push (`char_begin` and related commands) is refused with an error ack.
- There is no "always allow" decision; the device sends `once` or `deny`.
- Earlier turns are not kept on the device: it shows the current one. The
  phone has the history.
- A turn in progress cannot be cancelled from the device.
- The speaker, Wi-Fi, and low-power sleep are not used by this application.
- Replies are shown as text only; nothing is read aloud.

## Memory

The board has no PSRAM. LVGL allocates from the system heap instead of a fixed
pool, so the interface, Bluetooth, and task stacks share one budget. The
firmware logs `heap: free=… min=…` once a minute (a warning below 12 KB); read
it during on-device acceptance.

The conversation costs memory the earlier interface did not need: the answer
buffer grew from 420 to 960 bytes in three places, and what was said, the
helper in use and the helper list are kept as well, about 4 KB of static
memory in all. The reader keeps its copy of the answer only while it is open.
This has not been measured on a device.

## Code map

| Path | Role |
| --- | --- |
| `main/main.c` | Queues, the application task, BLE and settings glue |
| `main/buddy_ble*.c` | NimBLE peripheral: Nordic UART Service, security, bonding |
| `main/buddy_line.c`, `main/buddy_protocol.c` | Line assembly and bounded JSON parsing/serialization |
| `main/buddy_state.c` | Link, approval, and navigation state machine |
| `main/buddy_orchestrator.c`, `main/buddy_app_logic.c`, `main/buddy_settings.c` | Command handling, queue policy, NVS settings |
| `main/pocket_view.c` | Pure view logic: which screen, what the home page shows, the pet's mood, helper colours, time formatting |
| `main/pocket_pet.c` | The pet's 20 × 20 pixel sprite and its moods (no LVGL) |
| `main/pocket_ui.c` | LVGL screens |
| `main/pocket_voice_core.c` | IMA ADPCM, voice-frame packing, the send queue, and the level meter (no ESP-IDF) |
| `main/pocket_voice.c` | The push-to-talk task: microphone, encoding, Bluetooth uplink |
| `tools/ui_preview/` | Host renderer that draws every screen with the real LVGL and fonts |

The BLE, protocol, and state layers are adapted from the `demo/claude-buddy-port`
branch; navigation was redesigned, and assistant-turn parsing, the hub
extension and the conversation were added. The interface is new and shares no
screens with that branch or with the baseline hardware-test menu.

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

1. Startup shows Xiaoyou asleep; the top bar shows the link dot and the battery.
2. Pairing: the passkey page appears, the passkey is accepted, and the device
   reconnects without a passkey after a restart of either side.
3. Hold `OK`, speak, release: the level bars move while speaking; the home page
   shows the recording on its way, then what was heard, then the answer. A
   long answer scrolls in the reader, nine lines at a time, with no half lines.
4. When Xiaoyou hands work over, the letter moves, the helper's name is in its
   colour, and the timer counts. A failed turn shows the reason.
5. Every page and every state renders without boxes, clipping, or overlap,
   including long mixed Chinese/Latin text.
6. A request takes over the screen; `OK` says yes, `DOWN` says no, `UP` pages
   long details; the host reflects each decision.
7. A request or an answer arriving while the screen is off turns it on.
8. Unpair and factory reset ask for confirmation and behave as described above.
9. Dropping the link returns to the sleeping page within 30 seconds and clears
   the conversation.
10. The minimum in the `heap:` log line keeps a comfortable margin with a long
    answer on screen and during a recording; free heap is stable over a long
    session; Bluetooth range and battery life are measured, not assumed.

## Roadmap

1. **Spoken replies.** Replies are text only. Playing them through the
   device speaker needs a downlink audio path and a speech synthesizer on the
   host.
2. **A leaner codec.** ADPCM needs about 8 KB/s of Bluetooth throughput. Opus
   would need a quarter of that at the cost of memory and processor time on a
   board without PSRAM; worth doing only if on-device tests show lost frames.
3. **Away from home.** The phone reaches the runtime over the local network
   only.
