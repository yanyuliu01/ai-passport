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
| 🧡 | **Claude**: the orange-haired scholar | Claude Code |
| 🌙 | **Codex**: the silver-haired companion | Codex CLI |

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

## What's on the wrist

| Screen | What you see |
| --- | --- |
| **Mascot** | Xiaoyou and what she is doing: listening, thinking, handing off (a letter flies to the helper's name tag), done, or failed |
| **Conversation** | What you said and what Xiaoyou answered, one card at a time |
| **Tasks** | Everything handed to helpers: what's running, what's waiting for you, and what finished, grouped by day |
| **Usage** | How much of each subscription's 5-hour and weekly allowance is left |

Approvals, notices, six-digit passkey pairing and firmware updates over
Bluetooth are covered in [Claude Pocket](docs/claude-pocket.md).

## Where to go next

| I want to… | Start here |
| --- | --- |
| Understand what it does and how pairing works | [Claude Pocket](docs/claude-pocket.md) |
| Install the phone companion app | [Pocket Hub (Android)](companion/README.md) |
| Run the host runtime that drives the helpers | [Runtime](runtime/README.md) |
| Build or modify the firmware with an AI coding tool | [AGENTS.md](AGENTS.md) |
| Explore the hardware, pins and memory budget | [Hardware guide](docs/hardware-design/AI_HARDWARE_DEVELOPMENT_GUIDE.md) |
| See the full documentation map | [docs/README.md](docs/README.md) |

## License

Code: [MIT](LICENSE). See [NOTICE](NOTICE) for third-party attributions. The
companion artwork has no separate art license yet; see
[assets/README.md](assets/README.md#ai-companion-design-pack).
