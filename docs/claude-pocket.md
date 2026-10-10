<p align="right">
  <a href="claude-pocket.zh_CN.md">简体中文</a> · <strong>English</strong>
</p>

# Claude Pocket

Claude Pocket turns the FoloToy AI Passport into the wearable face of Xiaoyou,
a small ghost that is the one voice you talk to. The device connects over
encrypted Bluetooth LE to the phone companion; you hold a button, speak, and
her answer appears on the screen. When she hands the work to another agent,
the screen shows who is doing it and which step it is on; when an agent wants
to do something that needs your consent, the device asks you.

The link speaks the public *Hardware Buddy* BLE protocol documented by
Anthropic in
[`anthropics/claude-desktop-buddy`](https://github.com/anthropics/claude-desktop-buddy),
with a small extension for the phone companion. Without the extension the same
firmware still works as a companion to the Claude desktop app (macOS and
Windows with Developer Mode enabled; Anthropic describes that bridge as a maker
feature, not a supported product feature).

## What it does

Three screens; a click on `OK` goes round them. The three small dots in the
middle of the top bar show which one you are on.

| Screen | What it shows |
| --- | --- |
| 1 Mascot | Xiaoyou, large, and what she is doing right now: transcribing, thinking, having handed the work to someone (a letter travels from Xiaoyou to that agent's name tag, next to a timer), done (a brief of a sentence or two), failed (the reason). When idle it says how many things are running in the background |
| 2 Conversation | Conversation only, that is what Xiaoyou answered herself; things handed to a helper are not paged through here (when the sentence just said went to a helper, this screen only says who it went to and that it is on the next screen). One thing per screen, with a card of its own, holding what you said and the latest thing Xiaoyou said about it. The strip at the top says how that thing stands; the small line below says which one it is, when it began and who did it |
| 3 Tasks | The things handed to helpers, at most four, those in progress first and then the latest that ended: who is doing it, the title, for how long (or that it waits for your consent, is queued, is done, failed or was cancelled). Below are the two latest steps of the selected one, with tool names and commands shown verbatim; when the selected one has ended, its conclusion is shown instead (the brief). A short press on `OK` opens the selected thing's own page: its latest exchange (what you said later and what Xiaoyou answered, opened at the newest end), paged with `UP` and `DOWN`, which at the ends go to the previous / next thing on the list; another short press on `OK` goes to screen 1 |

| Capability | Behavior |
| --- | --- |
| Push-to-talk | Hold `OK` to record; the audio goes to the phone companion over Bluetooth; see [Push-to-talk](#push-to-talk). Held on screen 2 or 3, the sentence is addressed to the thing on screen (or the selected one): an addition, a change of the request, or a cancellation |
| Approvals | When an agent wants to do something that needs consent, Xiaoyou asks; who wants what is shown verbatim; say yes or no on the device. The request covers whichever screen is showing |
| Notices | The four most recent entries sent by the host, in the menu. While any are waiting, the idle first screen says how many |
| Helpers | The agents Xiaoyou can hand work to, as the host lists them, in the menu |
| Pairing | LE Secure Connections with a six-digit passkey shown on the device |
| Firmware updates | New firmware arrives from the phone companion over Bluetooth, no cable; a version that is never confirmed is rolled back by itself. See [Replacing the firmware over Bluetooth](#replacing-the-firmware-over-bluetooth) |

Colour answers one question: who is doing the work. Xiaoyou is lavender; every
helper has a colour of its own, fixed for a given name. Green, red and yellow
mean yes, no and "look at this", and nothing else.

The screen never switches by itself: when an answer arrives you stay on the
screen you were reading. The count in the top bar is the number of things
still running in the background; it is hidden when there are none.

Connected to the Claude desktop app instead of the phone companion, the first
screen shows what the desktop app reports: its status line while it works, and
the beginning of its latest reply. The second screen holds that latest reply
and the third is empty. There is no talking in that mode, because the desktop
protocol has no message for prompts or audio.

## Pairing

With the phone companion (the usual way): open the Pocket Hub app, start the
link, and type the six digits shown on the device into the phone's Bluetooth
prompt. See [`companion/README.md`](../companion/README.md).

With Claude desktop:

1. Choose **Help → Troubleshooting → Enable Developer Mode**.
2. Choose **Developer → Open Hardware Buddy…** and click **Connect**.
3. Pick the device named `Claude-XXXXXX`; the same name is shown on the
   first screen while Xiaoyou sleeps and in the connection guide.
4. Type the six digits shown on the device into the operating system's
   Bluetooth prompt.

After bonding, the device reconnects on its own. If it does not appear in the
list, check that Bluetooth is switched on in the device menu.

## Controls

The three buttons are `UP`, `DOWN`, and `OK`. The bottom line of the screen
always shows what the keys do right now.

| Where | `UP` / `DOWN` click | `OK` click | `OK` double press | `OK` hold |
| --- | --- | --- | --- | --- |
| Screen 1, mascot | — | To screen 2 | Open the menu | Talk; release to send |
| Screen 2, conversation | Scroll seven lines within this thing; at the end, one more press goes to the previous / next thing | To screen 3 | Open the menu | Talk, addressed to the thing on screen |
| Screen 3, tasks | Select the previous / next one | To screen 1 | Open the menu | Talk, addressed to the selected thing |
| Menu, More settings | Move the selection (wraps) | Run the selected item | Return to screen 1 | Return to screen 1 |
| Notices, Helpers | — | Back to the menu | Return to screen 1 | Return to screen 1 |
| Connection guide | Scroll three lines | Back to More settings | Return to screen 1 | Return to screen 1 |
| Request for a yes or no | `UP` pages through the details; `DOWN` says no | Yes | Counts as one click | — |
| Unpair / factory-reset confirmation | `DOWN` cancels | Confirm | Counts as one click | — |
| Screen off | Turns the screen on, nothing else | Turns the screen on, nothing else | Turns the screen on, nothing else | Turns the screen on, then talks |

`UP` and `DOWN` have no long-press or double-press function: a long press
turns the screen on when it is off and does nothing otherwise, and two quick
presses count as one click. The power key only controls hardware power; the
firmware cannot read it.

The button library reports a single click only after the double-press window
has passed, so a click on `OK` changes the screen about 0.2 seconds after the
press.

A request, a pairing passkey, or a confirmation takes over the screen and turns
it back on if it was off. So does the answer to something you just asked. A key
press only counts for what was on screen when the key went down; a press that
races a new request is dropped rather than applied to it. Each request accepts
one decision, and a failed send is shown as failed, never as approved. Two
quick presses of `OK` on a request count as one yes: they are neither ignored
nor counted twice.

Xiaoyou only phrases the question. Who is asking and what for come straight
from the host and are never paraphrased; a name too long for the bubble is
repeated in full at the top of the details card.

Conversation and tasks are kept apart: the selection on screen 3 does not
change screen 2, and paging on screen 2 skips the things handed to helpers.
What you say while holding `OK` on screen 3, or on the page of a thing opened
from there, is said inside the selected thing
(one that has ended too): it is always filed on that thing and continues its
own session instead of becoming a new sentence in the conversation. What you
say on screen 2 is ordinary conversation, and the card on screen is only a
hint for Xiaoyou. After you say something new, screen 2 follows that sentence: while it
has no card yet the screen shows the turn itself (what you said, that she is
thinking), and once the card arrives it shows the card. After you page to
another card yourself, it stays on that card until you speak again.

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
ASCII and all 6,763 GB2312 hanzi plus common punctuation. Anything outside
that set, such as emoji, traditional-only characters, or other scripts, is
drawn as a placeholder box; it is not dropped or replaced. Text longer than
its area ends with an ellipsis; details longer than 319 bytes and an answer
longer than 959 bytes are marked as cut. LVGL does not apply CJK line-breaking
rules, so a wrapped line can begin with a punctuation mark.

## Push-to-talk

Hold `OK` on any of the three screens. Xiaoyou says to wait a moment, then
says she is listening: the level bars move with your voice, which is how you
know the microphone is live. Speak, and release to send. Screens 1 and 2 then
show that the recording is on its way until the host says what it heard. A
press shorter than about a third of a second sends nothing. A recording stops
by itself at 30 seconds and is sent as it is.

The device only records and transmits. Turning speech into text and answering
is done by the host: the phone companion forwards the recording to the Xiaoyou
Runtime (see [`companion/README.md`](../companion/README.md) and
[`runtime/README.md`](../runtime/README.md)).

Voice is an extension to the desktop protocol and is off until the host asks
for it, so the Claude desktop app never receives a frame it does not
understand:

| Direction | Message | Meaning |
| --- | --- | --- |
| Host → device | `{"cmd":"hub","voice":true}` | The host accepts voice on this connection. Acknowledged with `{"ack":"hub","ok":true,"chat":true,"cards":true,"threads":true}`; `chat` says the firmware understands `chat` and `helpers` under [The conversation](#the-conversation), `cards` says it understands `card` and `tasks`, and `threads` says it shows conversation and tasks apart (so `tasks` may carry things that ended) |
| Device → host | `{"cmd":"voice","state":"start","rate":16000,"codec":"ima-adpcm","card":"c12"}` | A recording begins. `card` is present only when a thing was on screen at the press (the card on screen 2, the selected one on screen 3): the sentence is addressed to it. Pressed on screen 3 it is followed by `"pin":true`: the sentence is said inside that thing and is always filed on it |
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
in the messages below. They are part of the same extension, so the Claude
desktop app never sends them, and an older firmware answers them with an error
ack that the host ignores. A host sends `card` and `tasks` only after it saw
`cards` in the acknowledgement.

| Direction | Message | Meaning |
| --- | --- | --- |
| Host → device | `{"cmd":"chat","phase":P,"said":"…","reply":"…","agent":"…","stage":"…","mood":M,"card":"c12","doing":N}` | What Xiaoyou is doing right now: this is what **screen 1** shows. Only `phase` is required |
| Host → device | `{"cmd":"card","id":"c12","at":"14:02","state":S,"agent":"…","edits":N,"said":"…","reply":"…"}` | Adds or updates the card of one thing: **screen 2**. `id` and `state` are required |
| Host → device | `{"cmd":"card","clear":true}` | Forget every card (the host is about to send them again) |
| Host → device | `{"cmd":"tasks","list":[{"id":"c12","agent":"…","title":"…","state":T,"secs":42,"p1":"…","p2":"…"}]}` | The things handed to helpers: **screen 3**. `T` is `working`, `waiting` or `queued`, and for one that ended `done`, `failed` or `cancelled` (the host sends those only to firmware that announced `threads`; `p1` is then the conclusion, and the `reply` of that thing's `card` is its latest exchange, one paragraph per sentence of the owner and of Xiaoyou, at most 480 bytes). At most four are kept; an empty list means there are none |
| Host → device | `{"cmd":"helpers","list":[{"name":"…","about":"…"}]}` | The agents Xiaoyou can hand work to; at most four are kept. An empty list clears them |

`chat`: `phase` is `idle` (nothing is being said), `thinking`, `helper` (she
handed the work to the agent named in `agent`; `stage` is the title of the
thing), `done` (`reply` holds her brief, a sentence or two) or `failed`
(`reply` says why). `mood` is `idle`, `busy`, `ask`, `happy` or `oops` and sets
the pet's face when the turn is done. `said` is what the owner said, empty
until a recording has been transcribed. `card` is the card this sentence was
filed under; `doing` is the number of things still running in the background.

`card`: `state` is `working` (a helper is on it), `waiting` (it waits for your
consent), `done`, `failed`, `cancelled` or `talking`. `agent` is who is doing
or did it, empty when Xiaoyou answered herself. `said` is the first sentence
of the thing, `reply` is the latest thing Xiaoyou said, `edits` is how many
times it was added to or changed, and `at` is when it began (hours and
minutes). A card that arrives again under the same `id` is updated in place;
the order of the cards does not change.

`tasks`: `state` is `working`, `waiting` or `queued`. `secs` is how many
seconds the work has taken so far; the device keeps counting from there. `p1`
and `p2` are the two latest steps: `p1` is the earlier, `p2` the newest; a
single step goes in `p1`.

Limits: 159 bytes for `said` and `stage`, 23 for `agent` and a helper's
`name`, 63 for `about`, 959 for `reply`, 47 for a task's `title`, and 63 each
for `p1` and `p2`; longer text is cut on a character boundary. A `reply` of 956 bytes or more has
no room for another character, so it counts as already cut by the host and is
marked as cut too. A card `id` is
at most 11 bytes; a longer one is refused, because cut short it would be a
different card. These messages are not acknowledged one by one; one that does
not parse gets `{"ack":"…","ok":false,…}` and changes nothing.

Approval requests need no new message: the host puts the operation to confirm
into the heartbeat's `prompt` (`tool` is the helper and the tool, `hint` is
the content verbatim, of which up to 319 bytes are shown), and the device
answers with the existing `permission` command.

### What the device keeps

Cards stay in RAM in the order they were opened: up to 12 cards and 4,096
bytes of text. When a new one does not fit, the oldest are dropped. A card
holds two pieces of text only, the first sentence and the latest thing Xiaoyou
said; the additions in between appear as a count, and the full exchange is on
the phone.

A lost link does not clear the cards: Xiaoyou is asleep and what was said
stays readable. The turn on screen 1 and the task list on screen 3 are
cleared, because nobody is reporting them any more. After reconnecting, the
host first sends `card clear` and then the most recent cards again.

A turn that has no card yet (the recording is being transcribed, she is still
thinking) and a turn that never gets one (the recording was not understood)
are shown on screen 2 as the turn itself. The same goes for a host that never
sends `card` (the Claude desktop app, an older phone app): screen 2 holds the
latest reply only.

A hub that sends `chat` leaves `msg` empty in its heartbeats and does not send
`turn` events. Once a hub has sent `chat` on a connection, the firmware ignores
its `turn` events and reads the heartbeat counters as notifications only, not
as Xiaoyou being busy. A hub that never sends `chat` is shown the way the
desktop app is: busy while the counters are non-zero, and the reply from
`turn`.

While a recording has just been sent and its transcription is awaited, the
host may report that something else ended (a background thing finished, or the
previous turn). The device then takes only the count of things running and
does not replace the turn it is waiting for.

## Replacing the firmware over Bluetooth

A change to the screen no longer needs a cable: the phone companion sends the
new application image over the existing encrypted Bluetooth link. The flash
holds two application slots of the same size (3.9 MB each, see
[Firmware layout](development/engineering/firmware-layout.md)). The running
one keeps working while the new image is written to the other; once it checks
out, the device restarts into it. A new image starts in a "pending" state: it
only counts when the phone connects and says `confirm`. Without that within
three minutes, or after another restart in the meantime, the device goes back
to the previous version. So firmware that does not start, or cannot hold a
Bluetooth connection, does not leave a device that only a cable can rescue.

This too is part of the hub extension: the Claude desktop app never sends
these, and older firmware answers with an error acknowledgement. Everything
the device sends is a flat JSON object.

| Direction | Message | Meaning |
| --- | --- | --- |
| Host → device | `{"cmd":"fw","op":"info"}` | Which version is this. The device answers `{"ack":"fw","ok":true,"op":"info","build":B,"ver":V,"state":"valid"\|"pending","slot":S,"prev":P,"max":N}`: `build` is the first 16 hex digits of the firmware ELF's SHA-256, `prev` is the `build` in the other slot (empty when there is none), `max` is the largest image a slot holds |
| Host → device | `{"cmd":"fw","op":"begin","size":N,"sha256":"…"}` | An image of N bytes is coming. The device answers `{"ack":"fw","ok":true,"op":"begin","offset":K,"chunk":C,"window":W}`: start at offset K (not 0 when picking up an interrupted transfer), at most C bytes of data per frame, and never send further than "bytes in flash + W" |
| Host → device | data frames | One per write; see below |
| Device → host | `{"evt":"fw","got":G,"done":D}` | Sent for every 1 KB written to flash, and once a second when nothing moves. `got` is the next offset wanted, `done` the bytes in flash. With `"rewind":true`, something in between is missing: send again from `got` |
| Host → device | `{"cmd":"fw","op":"end"}` | All sent. The device checks the SHA-256 and the image itself, answers `{"ack":"fw","ok":true,"op":"end"}` and restarts about a second later. If it does not have everything yet it answers `"ok":false,"error":"incomplete","got":G`; carry on from G |
| Host → device | `{"cmd":"fw","op":"confirm"}` | Accept the new firmware that is running |
| Host → device | `{"cmd":"fw","op":"rollback"}` | Switch to the version in the other slot and restart, without a transfer |
| Host → device | `{"cmd":"fw","op":"abort"}` | Give up on this transfer; what was written is discarded and the running firmware is untouched |

Data frames share the RX characteristic with text lines, one frame per write:
`0xFE`, a four-byte little-endian offset, then image data. `0xFE` never occurs
in UTF-8, so the first byte of a write tells the device a frame from text, the
same way `0xFF` marks a voice frame in the other direction. Frames are written
without response, and text lines may go between them, so heartbeats continue
during a transfer. Every frame carries its offset: a repeated one is dropped,
and a gap is answered with `rewind`. A lost connection is not a failure: the
device keeps a half-received image for five minutes, and a `begin` with the same
`size` and `sha256` after reconnecting is answered with the offset to continue
from.

When the device refuses, `error` is one of: `size` (too small, or larger than a
slot), `no slot` (the partition table has no second slot), `unconfirmed` (the
running firmware has not been confirmed; send `confirm` first), `link` (a write
carries too little), `memory`, `flash`, `sha256`, `image` (the image itself does
not check out), `restarting`, `busy`, `no previous` and `previous invalid`
(nothing to switch back to).

During a transfer the screen shows Xiaoyou and a progress bar; keys do nothing,
a request that needs an answer does not interrupt, and a dark screen lights up.
After a failure the usual page returns and the line at the bottom says so.

These messages are only accepted on a bonded, encrypted connection, so the only
thing that can replace the firmware is the phone (or computer) that was paired.
Images are not signed: the chain of trust is the runtime's token, then the
phone, then the Bluetooth bond. Whoever holds the runtime token can have the
phone push any image to the device; the runtime should only listen on a trusted
network.

**The partition table cannot be changed over Bluetooth.** Going from older
single-slot firmware to this one takes one flash by cable (see
[Flashing](#flashing)); every later version can be sent over Bluetooth as long
as `partitions.csv` stays the same.

Every version of the firmware is kept on the computer that runs the runtime,
and the device can be put back on any of them: see
[`runtime/README.md`](../runtime/README.md#device-firmware).

## Stored data

The device stores its name, the owner name sent by the host, the Bluetooth
switch, approval and denial counters, and the BLE bond. Notices, helper names,
details of a request, request identifiers, the turn in progress and the task
list stay in RAM and are cleared when the link drops or no heartbeat arrives
for 30 seconds. Cards stay in RAM until the device restarts, the host clears
them, or they are pushed out by newer ones; they are never written to flash.

## Not implemented

- Folder push (`char_begin` and related commands) is refused with an error ack.
- There is no "always allow" decision; the device sends `once` or `deny`.
  Rules that allow something for good belong in the settings of the respective
  command-line tool.
- Cards are kept in RAM only. A restart loses them until the phone reconnects
  and sends them again; anything older is on the phone.
- A thing cannot be cancelled with a key: hold `OK` and tell it to stop.
- The bootloader and the partition table cannot be replaced over Bluetooth,
  only the application image.
- Images are not signed, and are neither compressed nor sent as differences:
  every update is the whole application image (about 1.7 MB).
- The speaker, Wi-Fi, and low-power sleep are not used by this application.
- Replies are shown as text only; nothing is read aloud.

## Memory

The board has no PSRAM. LVGL allocates from the system heap instead of a fixed
pool, so the interface, Bluetooth, and task stacks share one budget. The
firmware logs `heap: free=… min=…` once a minute (a warning below 12 KB); read
it during on-device acceptance.

Compared with the long-conversation version, static memory grew by about 3 KB:
the card records (12, about 50 bytes each), the task list (4 entries of about
220 bytes, once in the state and once in the snapshot for the interface), and
one task list and one card in the event structure. A line received over
Bluetooth is now parsed into the application task's static event buffer
instead of an event structure of several KB on the task stack. Heap: screen 2
holds the two pieces of text of a single card, less than the copy of every
turn kept before. None of this has been measured on a device; in the host
renderer LVGL peaks at about 69 KB (a 64-bit host, where objects are larger
than in the firmware).

Replacing the firmware over Bluetooth keeps one task (a 5 KB stack) and two
small queues at all times; the buffer for incoming image data (2.3 KB) is
allocated the first time an image arrives and kept afterwards. Checking a
complete image is what uses the most stack: the `image accepted` log line
reports how much of that task's stack was left, worth a look on a device.

## Code map

| Path | Role |
| --- | --- |
| `main/main.c` | Queues, the application task, BLE and settings glue |
| `main/buddy_ble*.c` | NimBLE peripheral: Nordic UART Service, security, bonding |
| `main/buddy_line.c`, `main/buddy_protocol.c` | Line assembly and bounded JSON parsing/serialization |
| `main/buddy_state.c` | Link, approval, conversation, and three-screen navigation state machine |
| `main/buddy_cards.h` | Cards: a byte-budgeted list, updated in place by id, header-only |
| `main/buddy_orchestrator.c`, `main/buddy_app_logic.c`, `main/buddy_settings.c` | Command handling, queue policy, NVS settings |
| `main/pocket_view.c` | Pure view logic: which view, what screen 1 shows, how a thing scrolls, the pet's mood, helper colours, time formatting |
| `main/pocket_pet.c` | The pet's 20 × 20 pixel sprite and its moods (no LVGL) |
| `main/pocket_ui.c` | LVGL screens |
| `main/pocket_voice_core.c` | IMA ADPCM, voice-frame packing, the send queue, the level meter, and the line that starts a recording (no ESP-IDF) |
| `main/pocket_voice.c` | The push-to-talk task: microphone, encoding, Bluetooth uplink |
| `main/pocket_update_core.c` | The hardware-independent half of firmware updates: data frames, receiving by offset, when to report progress, the replies |
| `main/pocket_update.c` | The update task: writes the other slot, checks, switches, waits to be confirmed, rolls back when it is not |
| `tests/update_interop/` | The phone companion's transfer code run against the firmware's own protocol code on a host |
| `tests/update_emulator/` | The update task, unchanged, on an emulated ESP32-C3: the real bootloader and both slots, with a test program playing the phone |
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
`IDF_PATH`; without it the static gate reports them as skipped. The firmware
update interop test between the phone companion and the firmware needs a JDK
as well as cJSON, and is reported as skipped when either is missing.

The half of the firmware update that touches hardware (`main/pocket_update.c`:
writing the other slot, checking, switching, waiting to be confirmed, going back
when the deadline passes) cannot be tested on a host. It is tested in an
emulator:

```bash
tests/update_emulator/run.sh   # needs ESP-IDF 5.5 and Espressif's QEMU (qemu-system-riscv32)
```

The script builds that file, unchanged, into a small program and runs it on an
emulated ESP32-C3 with the real ESP-IDF bootloader and the product's partition
table. A test program plays the phone and checks, in order: requests that must
be refused are refused; an image whose hash does not match is not switched to
even though all of it arrived; a transfer with lost, repeated and reordered
frames and a dropped link that is resumed ends with the image accepted and a
restart into the new slot; a new image that is not confirmed goes back to the
previous one; a confirmed one stays; switching back to the version in the other
slot works. It is not part of `tools/validate.sh` because the emulator has to be
installed separately. The emulator has no Bluetooth, so the radio link, the real
transfer speed and the memory headroom still have to be checked on the device.
The emulator itself sometimes hangs (it loses timer interrupts during flash
operations); the script notices and starts it again on the same flash contents,
which to the firmware is a power cut.

## Flashing

Each push to a feature branch that touches the firmware builds it in GitHub
Actions and attaches the merged image to a prerelease named *Claude Pocket
firmware build N*. With the device switched on and connected over a USB data
cable:

```bash
./tools/flash_pocket.sh              # downloads the newest build and writes it
./tools/flash_pocket.sh image.bin    # writes a merged image you already have
```

The script finds the serial port (it stops when it sees none, or more than one
without `--port`), installs `esptool` into a private Python environment the
first time, shows the image, its SHA-256 and the port, and asks before
writing. It refuses a file that is not a merged image.

The merged image is written at `0x0` and resets the settings area: the device
forgets its name and its Bluetooth pairing. Remove the old pairing on the phone
and pair again. See
[flashing and stored data](development/engineering/firmware-layout.md#flashing-and-stored-data)
for the alternatives that keep settings.

Once the device runs a version with
[firmware updates over Bluetooth](#replacing-the-firmware-over-bluetooth), no
cable is needed after that: new versions come from the phone companion, and
settings and pairing are kept. A cable is still needed in three cases: the
device has older single-slot firmware, the partition table changed, or the
Bluetooth path itself was broken by a change and neither slot holds a usable
version. After flashing by cable, run
`python3 -m xiaoyou_runtime firmware fetch` on the computer that runs the
runtime to add the same version to its library, so the device can be put back
on it later.

## On-device acceptance

A successful build and the host preview are not device validation. This
version (three screens) has not run on hardware yet. Check:

1. Startup shows Xiaoyou asleep; the top bar shows the link dot, the three
   small dots and the battery.
2. Pairing: the passkey page appears, the passkey is accepted, and the device
   reconnects without a passkey after a restart of either side.
3. A click on `OK` goes round the three screens and the dots in the top bar
   follow; a double press opens the menu, and a double press in the menu
   returns to screen 1. Judge whether the short delay of the click is
   acceptable.
4. Hold `OK`, speak, release: the level bars move while speaking; screen 1
   shows transcribing, then thinking and what you said, then the brief or that
   the work was handed over.
5. Screen 2: the sentence just spoken first appears as a new thing and is
   replaced by its card when that arrives. A long answer starts at its
   beginning, `DOWN` scrolls seven lines at a time with no half lines at the
   top or bottom, and one more press at the end goes to the next thing; `UP`
   likewise.
6. When Xiaoyou hands work over, the letter moves, the helper's name is in its
   colour, and the timer counts; the count of running things appears in the
   top bar.
7. Screen 3: several things running at once are all listed, each with its own
   timer; `UP` and `DOWN` change the selection and the two steps below follow;
   things that ended stay on the list, and selecting one shows its conclusion
   below. Screen 2 does not follow the selection on screen 3, and paging there
   never shows a thing handed to a helper. Holding `OK` on a finished thing on
   screen 3 continues that thing.
8. Hold `OK` on screen 2 or 3 and ask for a change or tell it to stop: that
   thing is changed or cancelled, and no new one is opened.
9. When an agent wants to do something that needs consent, the request appears
   and turns the screen on; `OK` says yes, `DOWN` says no, `UP` pages long
   details; the host reflects each decision. Two quick presses of `OK` count
   once.
10. Every page and every state renders without boxes, clipping, or overlap,
    including long mixed Chinese/Latin text.
11. Unpair and factory reset ask for confirmation and behave as described
    above.
12. Dropping the link puts Xiaoyou to sleep within 30 seconds; the cards stay
    readable, and after reconnecting no card appears twice.
13. The minimum in the `heap:` log line keeps a comfortable margin with about
    ten cards and four running things, and during a recording; free heap is
    stable over a long session; Bluetooth range and battery life are measured,
    not assumed.
14. Firmware over Bluetooth: after a version is chosen on the runtime the
    device shows progress, restarts by itself when the transfer is complete,
    and after reconnecting the phone companion reports that the new firmware is
    installed, with the name and the pairing still there; note how long the
    whole thing took. Carrying the phone out of range and back in the middle
    of a transfer lets it continue.
15. Going back: choose the version in the device's other slot; the device
    restarts into it within seconds, without a transfer.
16. Automatic rollback: push a new version and, as it restarts, turn the
    phone's Bluetooth off for more than three minutes; the device should
    return to the previous version by itself, and with Bluetooth back on the
    phone companion reports that the install failed.
17. During and after an update, the minimum in the `heap:` log line and the
    task stack left in the `image accepted` line both keep a margin.

## Roadmap

1. **Spoken replies.** Replies are text only. Playing them through the
   device speaker needs a downlink audio path and a speech synthesizer on the
   host.
2. **A leaner codec.** ADPCM needs about 8 KB/s of Bluetooth throughput. Opus
   would need a quarter of that at the cost of memory and processor time on a
   board without PSRAM; worth doing only if on-device tests show lost frames.
3. **Away from home.** The phone reaches the runtime over the local network
   only.
