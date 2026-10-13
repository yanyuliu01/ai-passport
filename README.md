<p align="right">
  <a href="README.zh_CN.md">简体中文</a> · <strong>English</strong>
</p>

<h1 align="center">Claude Pocket</h1>

<p align="center">
  <strong>Xiaoyou, a tiny pixel ghost, lives on your wrist.<br>
  You talk to her; she hands the work to her helpers.</strong>
</p>

<p align="center">
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-64748b?style=flat-square" alt="MIT License"></a>
  <img src="https://img.shields.io/badge/MCU-ESP32--C3-14b8a6?style=flat-square" alt="ESP32-C3">
  <img src="https://img.shields.io/badge/ESP--IDF-5.5.3-2563eb?style=flat-square" alt="ESP-IDF 5.5.3">
  <img src="https://img.shields.io/badge/link-BLE%20Secure-a78bfa?style=flat-square" alt="Bluetooth LE Secure Connections">
</p>

<p align="center">
  <img src="assets/images/readme/readme-hero.jpg" alt="Xiaoyou, a white pixel ghost with a heart, passes a dotted trail to three chibi helpers: a blue-haired whale maid, an orange-haired scholar and a silver-haired companion, all waving." width="100%">
</p>

**Claude Pocket** is firmware for a small, open ESP32-C3 wearable from
[FoloToy](https://ai-passport.folotoy.cn/en/). Hold the button and talk. Xiaoyou
answers on the screen. When the job is bigger than a chat, she hands it to a
coding agent, shows who is working and which step they're on, and asks you
before anything that needs your say.

The device talks over encrypted Bluetooth LE to a phone companion app. Without
the app, it also works with the Claude desktop app in Developer Mode.

## Meet Xiaoyou

<p align="center">
  <img src="assets/images/readme/readme-xiaoyou-moods.png" alt="Six moods of Xiaoyou, the pixel ghost: asleep, idle, busy with a sweat drop, asking with an exclamation mark, happy with a heart, and failed with crossed eyes." width="560">
</p>

She is drawn pixel by pixel in [`main/pocket_pet.c`](main/pocket_pet.c). She
sleeps while nothing is connected and bobs gently when idle. When she's busy,
three dots bounce over her head and a sweat drop shows. She raises an
exclamation mark when she needs you, gets a heart when things go well, and
crosses her eyes when something fails.

## Her helpers

Xiaoyou does not do the heavy lifting herself. She hands it to whichever agent
the host has configured. Each helper has a colour of its own on the device and
a character in the companion design pack:

| | Helper | Runs as |
| --- | --- | --- |
| 🐋 | **DSH**: the whale maid | DeepSeek Harness CLI (`dsh`) |
| 🧡 | **Claude**: the orange-haired scholar | Claude Code (`claude`, `tailor`) |
| 🌙 | **Codex**: the silver-haired companion | Codex app-server |

<p align="center">
  <img src="assets/images/readme/readme-crew.jpg" alt="Three rows, one per helper, each in four poses: thinking, typing on a laptop, waving while waiting for your approval, and cheering when the task is done." width="100%">
</p>

<p align="center"><sub>Thinking · working · waiting for your OK · done</sub></p>

And as pixel heads, sized for a small screen:

<p align="center">
  <img src="assets/images/readme/readme-pixel-heads.jpg" alt="Pixel-art heads of the three helpers, eight expressions each: neutral, happy, thinking, focused, puzzled, surprised, error, and sleeping." width="100%">
</p>

> The helper characters are unofficial, fan-inspired concept art, and the
> firmware does not use them yet. See the
> [design pack](docs/reference/yanyuliu01/ai-companion-design.md) for every sheet
> and its status.

## Three parts

```text
 Device (firmware)  ──encrypted BLE──>  Phone app "Pocket Hub"  ──HTTP──>  Xiaoyou Runtime (computer)
 push-to-talk, four screens,            relays voice, cards, usage,        persona · conversation · cards · routing
 approval prompts                       firmware updates                   ├─> claude / tailor (Claude Code)
                                                                           ├─> codex (Codex app-server)
                                                                           ├─> deepseek (Claude Code shell + DeepSeek API)
                                                                           ├─> dsh (DeepSeek Harness)
                                                                           └─> any command / another runtime
```

| Part | Directory | What it does | Docs |
| --- | --- | --- | --- |
| Firmware | `main/`, `components/` | Four screens (Xiaoyou / conversation / tasks / usage), push-to-talk, approval prompts, firmware updates over Bluetooth (two slots, automatic rollback if not confirmed) | [Claude Pocket](docs/claude-pocket.md) |
| Phone app | `companion/` | Android. Relays between the device and the runtime: voice, cards, tasks, approvals, usage, firmware; also chat, task views, and an effort picker of its own | [Pocket Hub](companion/README.md) |
| Runtime | `runtime/` | Xiaoyou herself: one card per request, several requests in parallel, add / change / cancel; picks effort tiers and watches subscription limits when handing work out; watches long tasks for silence; can write to a shared Notion workspace | [Xiaoyou Runtime](runtime/README.md) |

Claude desktop alone also works: in developer mode the same firmware connects directly
to Claude desktop's Hardware Buddy (no voice or cards in that mode).

## Where things stand (2026-10-12)

| Part | Version | Build | On hardware |
| --- | --- | --- | --- |
| Firmware | prerelease `firmware-build-19` | passes | Push-to-talk with the phone connected works on the device (`28119d8`); the three screens have been used on the device; the fourth screen and task history have not been checked yet |
| Phone app | 0.9.1, prerelease `companion-build-15` | passes | 0.2.0 paired and chatted on a phone; later versions have not been checked item by item on a phone |
| Runtime | 0.6.1 | all tests pass | Runs daily on the author's Mac; Claude Code subscription usage and Codex app-server approvals have not been tested there yet |

Each part's document ends with what is and is not verified. Development happens on
`feature/usage-history`; the other branches are:

| Branch | What it is |
| --- | --- |
| `feature/usage-history` | The current line: the latest code for all three parts |
| `feature/usage-effort`, `feature/xiaoyou-agent-router`, `feature/ble-firmware-update` | Earlier stages, already merged into the current line |
| `feature/claude-pocket` | The first Claude Pocket firmware (a desktop companion) |
| `main` | FoloToy's upstream hardware-test baseline, not a finished application |

## Upgrading: all three together

The protocol between firmware, app, and runtime changes in step, so upgrade all three:

1. Install the newest `companion-build-N` on the phone (`pocket-hub-debug.apk` under Releases).
2. On the computer, stop the runtime, pull the current line, and start it again:
   `cd runtime`, `python3 -m xiaoyou_runtime --config config.json`
3. Push the new firmware to the device over Bluetooth:
   `python3 -m xiaoyou_runtime firmware fetch --push`.
   A device still on the old single-slot firmware needs one flash by cable first:
   `bash tools/flash_pocket.sh`.

To go back to a version: `python3 -m xiaoyou_runtime firmware restore previous` (or a
number from `firmware list`).

## Where to go next

| I want to… | Start here |
| --- | --- |
| Learn what the device does, its controls, protocol, and memory | [Claude Pocket](docs/claude-pocket.md) |
| Install the phone app and pair the device | [Pocket Hub](companion/README.md) |
| Run Xiaoyou on a computer, register agents, configure tiers and usage | [Xiaoyou Runtime](runtime/README.md) |
| Change the firmware with an AI coding tool | [AGENTS.md](AGENTS.md) |
| Read about hardware, pins, and the memory budget | [Hardware guide](docs/hardware-design/AI_HARDWARE_DEVELOPMENT_GUIDE.md) |
| See the upstream baseline platform and the full documentation map | [docs/README.md](docs/README.md) |

## License

Code: [MIT](LICENSE). See [NOTICE](NOTICE) for third-party attributions. The
companion artwork has no separate art license yet; see
[assets/README.md](assets/README.md#ai-companion-design-pack).
