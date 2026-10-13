<p align="right">
  <a href="README.zh_CN.md">简体中文</a> · <strong>English</strong>
</p>

<h1 align="center">Claude Pocket</h1>

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="assets/images/logo-wordmark-dark.png">
    <img src="assets/images/logo-wordmark.png" alt="FoloToy wordmark" width="128">
  </picture>
</p>

<p align="center">
  <strong>Xiaoyou, worn on you: the one AI companion you talk to.</strong>
</p>

<p align="center">
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-64748b?style=flat-square" alt="MIT License"></a>
  <img src="https://img.shields.io/badge/MCU-ESP32--C3-14b8a6?style=flat-square" alt="ESP32-C3">
  <img src="https://img.shields.io/badge/ESP--IDF-5.5.3-2563eb?style=flat-square" alt="ESP-IDF 5.5.3">
</p>

<p align="center">
  <img src="assets/images/home.jpg" alt="The FoloToy AI Passport wearable, shown from the front, side, and back" width="100%">
</p>

This repository turns the [FoloToy AI Passport](https://ai-passport.folotoy.cn/), an open,
wearable ESP32-C3 device, into the face of **Xiaoyou**. Xiaoyou is a small pixel
ghost and the only one you talk to: she answers what she can herself, hands longer work
to agents such as Claude Code and Codex in the background, and relays their results.
When an agent wants to do something that needs your approval, the device asks you.

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

[MIT](LICENSE). Third-party notices are in [NOTICE](NOTICE).
