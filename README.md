<p align="right">
  <a href="README.zh_CN.md">简体中文</a> · <strong>English</strong>
</p>

<h1 align="center">Claude Pocket</h1>

<p align="center">
  <strong>Xiaoyou, a little whale maid, lives on your wrist.<br>
  You talk to her; she hands the big jobs to Claude and Codex.</strong>
</p>

<p align="center">
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-64748b?style=flat-square" alt="MIT License"></a>
  <img src="https://img.shields.io/badge/MCU-ESP32--C3-14b8a6?style=flat-square" alt="ESP32-C3">
  <img src="https://img.shields.io/badge/ESP--IDF-5.5.3-2563eb?style=flat-square" alt="ESP-IDF 5.5.3">
  <img src="https://img.shields.io/badge/Xiaoyou-DeepSeek%20%C2%B7%20dsh-7cc8f2?style=flat-square" alt="Xiaoyou runs on DeepSeek through dsh">
</p>

<p align="center">
  <img src="assets/images/readme/readme-hero.jpg" alt="Xiaoyou, a blue-haired whale maid, waves next to her pixel-art form with a heart; a dotted trail runs from her to two waving helpers, an orange-haired scholar (Claude) and a silver-haired companion (Codex)." width="100%">
</p>

**Claude Pocket** is firmware for a small, open ESP32-C3 wearable from
[FoloToy](https://ai-passport.folotoy.cn/en/). Hold the button and talk. Xiaoyou
answers on the screen. When the job is bigger than a chat, she hands it to a
coding agent, shows who is working and which step they're on, and asks you
before anything that needs your say.

The device talks over encrypted Bluetooth LE to a phone companion app. The app
also works on its own: its home screen shows Xiaoyou moving with what she is
really doing, and you hold the microphone to talk. Codex and Claude Code
conversations already open on your computer can be mirrored to the phone and
the device, and through Tailscale Funnel the phone reaches the computer at home
from any network. Without the app, the device also works with the Claude
desktop app in Developer Mode.

## Meet Xiaoyou

Xiaoyou is the DeepSeek whale, a whale maid nicknamed *Big Fat Fish*. She is
the only one you talk to, and the runtime runs her on DeepSeek through the
DeepSeek Harness CLI (`dsh`), streaming her replies as they are written. The
app and firmware docs also call her DS.

<p align="center">
  <img src="assets/images/readme/readme-xiaoyou-idle.gif" alt="Xiaoyou's pixel head breathing gently while idle." width="160">
  &nbsp;&nbsp;
  <img src="assets/images/readme/readme-xiaoyou-moods.png" alt="Six moods of Xiaoyou's pixel head: asleep with Zz, idle, busy with bouncing dots, asking for you, happy with a heart, and failed." width="480">
</p>

On the device she is a 60 × 60 pixel head compiled from the whale-maid artwork
in the design pack, with six moods: she sleeps while nothing is connected,
breathes gently when idle, has three dots bouncing over her head when she's
busy, and has a face of her own for needing you, for things going well, and
for something failing. On top of the moods she moves with the real state, the
same way as on the phone's home screen: she sways while you hold the button and
talk, floats while she thinks, and hops once when her answer arrives. When she
hands a job to a helper, a letter flies from her to that helper's badge.

> Both images are rendered straight from `main/pocket_pet.c`, the same pixels
> the device draws.

## Her helpers

Xiaoyou answers what she can herself and hands longer work to an agent in the
background. Each helper has a colour of its own on the device:

| | Who | Runs as |
| --- | --- | --- |
| 🐋 | **Xiaoyou**: the whale maid, the one you talk to | DeepSeek through `dsh` |
| 🧡 | **Claude**: the orange-haired scholar | Claude Code (`claude`, `tailor`) |
| 🌙 | **Codex**: the silver-haired companion | Codex app-server |

<p align="center">
  <img src="assets/images/readme/readme-crew.jpg" alt="Three rows: Xiaoyou the whale maid, Claude and Codex, each in four poses: thinking, typing on a laptop, waving while waiting for your approval, and cheering when the task is done." width="100%">
</p>

<p align="center"><sub>Thinking · working · waiting for your OK · done</sub></p>

And as pixel heads, sized for a small screen:

<p align="center">
  <img src="assets/images/readme/readme-pixel-heads.jpg" alt="Pixel-art heads of Xiaoyou, Claude and Codex, eight expressions each: neutral, happy, thinking, focused, puzzled, surprised, error, and sleeping." width="100%">
</p>

> The chibi characters are unofficial, fan-inspired concept art. See the
> [design pack](docs/reference/yanyuliu01/ai-companion-design.md) for every sheet
> and its status.

## Three parts

```text
 Device (firmware)  ──encrypted BLE──>  Phone app "Pocket Hub" ──HTTP(S)──>  Xiaoyou Runtime (computer)
 push-to-talk, four screens,            chat, tasks, helpers,                persona · conversation · cards · routing
 approval prompts                       approvals; relays voice,             ├─> dsh (DeepSeek Harness, speaks as Xiaoyou)
                                        cards, usage, firmware               ├─> claude / tailor (Claude Code)
                                        LAN / Tailscale Funnel               ├─> codex (Codex app-server)
                                                                             ├─> deepseek (Claude Code shell + DeepSeek API)
                                                                             ├─> any command / another runtime
                                                                             └── watches local Codex and Claude Code chats (--desktop-sync)
```

| Part | Directory | What it does | Docs |
| --- | --- | --- | --- |
| Firmware | `main/`, `components/` | Four screens (Xiaoyou / conversation / tasks / usage), push-to-talk, approval prompts, firmware updates over Bluetooth (two slots, automatic rollback if not confirmed) | [Claude Pocket](docs/claude-pocket.md) |
| Phone app | `companion/` | Android. A sidebar with four places: chat with Xiaoyou, tasks, helpers, connection and settings. The home screen shows Xiaoyou moving with her state, with hold-to-talk on the phone (transcribed on the phone); approval alerts (watching up to eight computers), task images and files, usage; also the relay between the device and the runtime; new versions arrive through the runtime | [DS companion (Pocket Hub)](companion/README.md) |
| Runtime | `runtime/` | Xiaoyou herself: one card per request, several requests in parallel, add / change / cancel; streamed replies; picks effort tiers and watches subscription limits when handing work out; watches long tasks for silence; downloadable task images and files; mirrors Codex and Claude Code conversations on the computer, with an optional approval bridge; shared history for desktop agents; can write to a shared Notion workspace | [Xiaoyou Runtime](runtime/README.md) |

Claude desktop alone also works: in developer mode the same firmware connects directly
to Claude desktop's Hardware Buddy (no voice or cards in that mode).

## Where things stand (2026-10-16)

| Part | Version | Build | On hardware |
| --- | --- | --- | --- |
| Firmware | prerelease `firmware-build-22` (`8220106`, whale pixel head plus state-driven motion) | passes | Push-to-talk with the phone connected works on the device (`firmware-build-19`); the three screens have been used; the whale's sleeping face and breathing were confirmed on the device; the state-driven motion, the fourth screen and task history have not been checked on the device yet |
| Phone app | 0.11.0 (version code 17), prerelease `companion-build-26` | passes | 0.2.0 paired and chatted on a phone; the sidebar version, phone hold-to-talk, approval alerts and updates through the runtime have not been checked item by item on a phone |
| Runtime | 0.8.0 | all 468 tests pass | Runs daily on the author's Mac; Claude Code subscription usage, Codex app-server approvals and a real round trip through the desktop approval bridge have not been tested there yet |

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

1. On the computer, stop the runtime, pull the current line, and start it again
   (runtime first, so a new app never meets an old API):
   `cd runtime`, `python3 -m xiaoyou_runtime --config config.json`
2. The phone app updates through the runtime from 0.11.0 on. Under `runtime/`, run
   `python3 -m xiaoyou_runtime app publish <path to apk> --note "what changed"`, then
   install from the app's prompt or from Connection and settings → App update. A phone
   still on a version before 0.11.0 needs the newest `companion-build-N` installed by
   hand once (`pocket-hub-debug.apk` under Releases).
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
| Install the phone app and pair the device | [DS companion (Pocket Hub)](companion/README.md) |
| Run Xiaoyou on a computer, register agents, configure tiers and usage | [Xiaoyou Runtime](runtime/README.md) |
| Mirror Codex and Claude Code conversations on the computer to the phone and device | [Sync existing desktop conversations](runtime/README.md#sync-existing-desktop-conversations) |
| Reach the runtime at home while you're out | [Tailscale Funnel](runtime/README.md#reaching-the-runtime-from-anywhere-tailscale-funnel) |
| Ship a new app version to the phone | [App updates](companion/README.md#app-updates) |
| Change the firmware with an AI coding tool | [AGENTS.md](AGENTS.md) |
| Read about hardware, pins, and the memory budget | [Hardware guide](docs/hardware-design/AI_HARDWARE_DEVELOPMENT_GUIDE.md) |
| Find any other document | [Documentation](#documentation) |

## Documentation

Engineering and contribution guides define the rules; archives and references provide background. Pick the entry for your task.

| Resource | What you will find |
| --- | --- |
| [Claude Pocket](docs/claude-pocket.md) | The application on this branch: pairing, controls, text coverage, acceptance |
| [Xiaoyou harness review](docs/assets/xiaoyou-harness-review.md) | Fork architecture audit, local reliability findings, connector boundaries, experiments and incremental PR plan |
| [Development](docs/development/README.md) | AI workflow, engineering conventions, CI, and release guidance |
| [App / Runtime / firmware reliability](docs/development/cross-stack-reliability.md) | Mandatory regression, 3-second first-response, connection recovery, and multi-end consistency contracts |
| [Auto-iteration pipeline](docs/development/auto-iteration.md) | Plugin-based loop that finds app usage problems, patches them with a local model, gates, publishes and rolls back |
| [Product principles](docs/product-principles.md) | Draft v0.1: what Xiaoyou is, what it does not do, and the numbered principles version plans must check against |
| [AI skills](skills/README.md) | Development, environment setup, builds, device testing, and debugging |
| [Hardware](docs/hardware-design/README.md) | Board facts, interface boundaries, acceptance checklists, and troubleshooting |
| [Chinese fonts](docs/development/engineering/lvgl-chinese-fonts.md) | Glyph coverage, widget font selection, and blank-text troubleshooting |
| [Community projects and experience](docs/reference/README.md) | Playbooks and reusable knowledge under `docs/reference/<username>/` |
| [Contributing](docs/contribution/README.md) | Documentation, commits, and pull-request conventions |
| [Device images](docs/brand/README.md) | Reference photos of the device for drawing it |
| [Changelog](docs/CHANGELOG.md) | Release history |

AI agents: start with [`AGENTS.md`](AGENTS.md) and follow its task-specific routing. Community: [Contribute](.github/CONTRIBUTING.md) · [Get help](.github/SUPPORT.md) · [Code of conduct](.github/CODE_OF_CONDUCT.md) · [Security](.github/SECURITY.md).

## License

Code: [MIT](LICENSE). See [NOTICE](NOTICE) for third-party attributions. The
companion artwork has no separate art license yet; see
[assets/README.md](assets/README.md#ai-companion-design-pack).
