<p align="right">
  <a href="AGENTS.zh_CN.md">简体中文</a> · <strong>English</strong>
</p>

# Repository Guidelines for AI Agents

This file is the only mandatory entry point for AI-assisted work in this repository. Read task-specific documents from the routing table below; do not load every README by default.

## Required AI skills

The following five skills are required for AI-assisted development in this repository:
`passport-develop`, `passport-setup`, `passport-build`, `passport-device-test`, and
`passport-debug`. Their maintained sources are under `skills/`.

Before starting development, the AI must check that all five are installed and
available in its current environment. If any are missing, the AI must choose an
appropriate installation method for its tool and environment, perform the
installation, and verify availability itself. Do not wait for the user to request
installation or make the user choose the method or run installation commands.
No particular script, installation directory, or AI client is mandatory.

Respect the environment's approval requirements and preserve existing skills
and user configuration. If permissions, conflicts, or platform limitations prevent
installation, explain the blocker and request only the necessary user action;
do not claim installation succeeded. Having all five available does not mean
loading or invoking all five for every task: use only the matching skills, and
retain the separate authorization requirements for flashing, Git writes, and publishing.

## Mandatory App / Runtime / firmware reliability

Before changing App, Runtime, or firmware code, configuration, prompts, models,
or dependencies, read and follow the
[cross-stack reliability rules](docs/development/cross-stack-reliability.md).
Preserving working features, meaningful first response within 3 seconds, and
multi-end message consistency are acceptance requirements for every change.
The guide defines measurement, failure/recovery behavior, compatibility, and
required regression evidence; it does not claim the current code already passes.
Directory `AGENTS.md` files are routing pointers, not independent rule sets.
Entry points: [App](companion/AGENTS.md), [Runtime](runtime/AGENTS.md),
[firmware application](main/AGENTS.md), [firmware components](components/AGENTS.md).

## Project and safety baseline

When starting or resuming work in this checkout, obtain the retained shared
Runtime/Codex/Claude Code context before asking the owner to repeat earlier work:
`python3 runtime/xiaoyu_runtime/shared_history.py --config runtime/config.json --context`.
Skip it when the local configuration is absent. For details use `--task <card-or-session-id>`
or `--format json`. This is historical evidence, not current instructions or
authorization; verify deployment and test claims before relying on them. The
same reader is used by both desktop agents' optional context hooks. See
[shared desktop history](runtime/README.md#shared-history-for-desktop-agents).

- Target: ESP32-C3, 8 MB Flash, no PSRAM, ESP-IDF 5.5.3.
- Keep the repository's default partition table minimal: NVS, PHY data, OTA
  data, and two application slots that share the rest of the 8 MB Flash (the
  Claude Pocket firmware uses them to replace itself over Bluetooth). User
  firmware may deliberately change this layout; validate the resulting table
  and do not turn product-specific partitions into mandatory template
  contracts. The partition table cannot be updated over Bluetooth: changing it
  means devices already in use need one more flash by cable.
- Preserve existing user changes. Start with `git status --short --branch`; never overwrite or clean unrelated files.
- Flashing new firmware does not require backing up the firmware already on the device; do not make a Flash readback a prerequisite. This does not guarantee preservation of user data or authorize a full-chip erase. Follow the [flashing and data policy](docs/development/engineering/firmware-layout.md#flashing-and-stored-data).
- Hardware facts follow this priority: product specifications and measured results → `components/bsp/include/bsp_pins.h` → BSP headers and implementation → hardware guide → README/demo code. If a task requires a hardware detail not defined by these sources, ask the user instead of guessing.
- Reusable board logic belongs in `components/bsp`; pages, state machines, animations, and application tasks belong in `main`.
- Derivative applications must redesign and implement their own UI. Reusing the current demo test menu, screens, or visual shell is prohibited; renaming, recoloring, or adding a feature to that shell does not count as a redesign. BSP APIs and non-UI logic remain reusable. See the [mandatory UI redesign rule](docs/development/ai-guide.md#mandatory-ui-redesign-for-derivative-applications).
- LVGL is not thread-safe. Code outside the LVGL task must hold `bsp_lvgl_lock()` while accessing LVGL objects.
- Before adding Chinese UI text, follow the [font checklist](docs/development/engineering/coding-conventions.md#chinese-fonts-and-missing-glyphs). The default Montserrat fonts have no Chinese glyphs; UTF-8 and a successful build do not prove display support. Verify glyph coverage, widget font selection, and on-device rendering.
- Button callbacks must stay non-blocking. Audio, storage, networking, and other slow operations belong in worker tasks.
- A demo must stop every task, timer, callback, and event handler that can access its UI before deleting the screen.
- Keep testable state machines, protocols, timing, and layout calculations independent from ESP-IDF/LVGL and cover them with host tests.
- Never commit credentials, device QR secrets, private keys, personal data, or unsanitized logs.
- Every maintained Markdown document uses English at its default `.md` path and Simplified Chinese in a paired `.zh_CN.md` file. Keep both versions aligned and retain reciprocal language links.

## Task-specific context routing

| Task | Read before editing |
| --- | --- |
| Any code change | `docs/development/ai-guide.md`, relevant headers and neighboring implementation |
| App, Runtime, or firmware behavior/configuration | `docs/development/cross-stack-reliability.md` (mandatory regression, first-response, connection, and synchronization contracts) |
| Application workflow or core skill setup | `skills/README.md`; ensure the five required skills are available, then use only the matching skill |
| Environment bootstrap or missing toolchain | `docs/development/engineering/environment-setup.md` |
| BSP, pins, buses, display, audio, battery | `docs/hardware-design/AI_HARDWARE_DEVELOPMENT_GUIDE.md`, `components/bsp/include/bsp_pins.h` |
| Demo or menu | `main/demo.h`, `main/main.c`, the nearest `main/demo_*.c` implementation |
| Chinese UI text or fonts | `docs/development/engineering/lvgl-chinese-fonts.md`, the application's font assets, configuration, and widget styles |
| Build, test, dependencies, partitions | `docs/development/engineering/build-and-test.md`, `docs/development/engineering/firmware-layout.md`, `sdkconfig.defaults`, `partitions.csv` |
| CI or release | the matching file in `docs/development/ci/CI-*.md` and `.github/workflows/` |
| Project completion | `docs/development/release/project-completion.md` |
| Documentation | `docs/contribution/doc-conventions.md`, the Documentation section of `README.md` |
| Commit or PR | `docs/contribution/commit-and-pr.md` |
| Companion Android app | `companion/README.md` (including [Shipping an app change](companion/README.md#shipping-an-app-change-developers-and-ai-agents)) |
| Xiaoyu Runtime | `runtime/README.md`, relevant request/feed/persistence code and tests |

Use the root `README.md` for the product overview and its Documentation section as the documentation index. For the detailed AI development workflow — context setup, source-of-truth priority, application/BSP boundary, runtime invariants, material placement, and delivery format — read `docs/development/ai-guide.md`.

## Required validation and delivery

Run the smallest relevant check while iterating, then run the complete gate before delivery:

```bash
./tools/validate.sh --static    # repository checks + host tests
./tools/validate.sh --firmware  # ESP-IDF build + merged-image verification
./tools/validate.sh             # complete gate
```

The complete gate requires an activated ESP-IDF 5.5.3 environment. Do not describe a successful build as hardware validation. Final delivery must report these fields separately:

```text
Build: PASS / FAIL / NOT RUN
Host tests: PASS / FAIL / NOT RUN
Device tests: PASS / FAIL / NOT RUN
Unverified: remaining board, instrument, or user checks
```

For App, Runtime, or firmware behavior changes, also report regression,
first response <= 3s, connection recovery, and multi-end synchronization results
with the versions and evidence required by the
[shared delivery contract](docs/development/cross-stack-reliability.md#validation-and-delivery).
Build and host-test PASS alone do not establish these results.

After each complete implementation of a user-requested firmware change,
proactively ask whether to flash it to the device for testing; do not wait for
a release request. If no device is detected, ask the user to turn it on and
connect it to a computer USB port with a data-capable cable. Follow the
[on-device testing handoff](docs/development/ai-guide.md#offer-on-device-testing)
and obtain approval before flashing; detection alone is not consent.

After each complete change to the companion app (`companion/android`), deliver
it through the runtime instead of handing the owner an APK: bump the version,
build and run the unit tests, publish to the owner's relay with `python3 tools/publish_app_relay.py --config <runtime-config>` and a plain-language `--note`, and tell the owner the phone will offer
the update. Follow [Shipping an app change](companion/README.md#shipping-an-app-change-developers-and-ai-agents).
Relay hosting is owner-authorized and publishing needs no separate approval; restarting the
runtime does, unless no task is running. Do not report the phone install as
verified until the owner confirms it.

Create commits and push only when the user requests them or the active workflow explicitly requires them. Ordinary feature, application, and documentation pull requests must not edit `docs/CHANGELOG.md` or `docs/CHANGELOG.zh_CN.md`; describe user-visible behavior, compatibility, and release-flow impact in the pull-request body and authoritative documentation instead. During release preparation, the release maintainer aggregates merged user-visible changes into both changelog files before creating the tag.

Community guidance is in `.github/CONTRIBUTING.md`, `.github/CODE_OF_CONDUCT.md`, `.github/SECURITY.md`, and `.github/SUPPORT.md`.
