<p align="right">
  <a href="README.zh_CN.md">简体中文</a> · <strong>English</strong>
</p>

# Development Guidelines

This directory contains AI Passport engineering rules and reusable workflows, grouped by purpose: the AI-assisted development workflow (`ai-guide.md`), engineering conventions (`engineering/`), CI documents (`ci/`), and the release/completion flow (`release/`). Rules should identify their trigger, required action, prohibited action, validation, and exceptions. Hardware facts belong in `docs/hardware-design/`; automatable requirements must also be enforced by tooling or CI.

## AI workflow

- [xiaoyou-system-prompt.md](xiaoyou-system-prompt.md): base prompt, full Grok Bot reference mapping, capability adaptations and acceptance boundaries.
- [cross-stack-reliability.md](cross-stack-reliability.md): mandatory App/Runtime/firmware regression, 3-second first-response, connection recovery, and multi-end consistency rules.
- [unified-message-model.md](unified-message-model.md): the unified message structure: one message log, id rules, the sync interface, and how a new runtime or agent connects.
- [conversation-presentation.md](conversation-presentation.md): proposed shared project/conversation presentation, source and directory bindings, title repair, event authorship and native-client boundaries.
- [app-runtime-simplification.md](app-runtime-simplification.md): confirmed issue list for simplifying App and Runtime interaction across code, experience and interaction, with root causes, targets and open decisions.
- [xiaoyou-agent-model.md](xiaoyou-agent-model.md): proposed unified model of Xiaoyou as an agent (events, world state, attention and commitments, one decision loop, traceability) that resolves the issue list and hosts future proactivity and perception.
- [windows-public-node-handoff.md](windows-public-node-handoff.md): Windows Codex handoff for the home public node, source snapshot, network-permission findings, native build and external acceptance.
- [xiaoyou-desktop-plan.md](xiaoyou-desktop-plan.md): Mac/Windows client proposal, shared conversations, encrypted self-hosted transport, Thursday candidate milestones and acceptance dependencies.
- [decide-then-speak.md](decide-then-speak.md): design draft that replaces the Runtime's promise reminder with a decide step before Xiaoyou speaks; also records the chat fixes of App 0.25.2 / Runtime 0.18.2 and how to hand the work over.
- [auto-iteration.md](auto-iteration.md): design draft of unified usage collection (all clients report to the home computer) and version-level auto-iteration with local-first models.
- [ai-guide.md](ai-guide.md): AI-assisted development workflow.
- [Core AI skills](../../skills/README.md): development, setup, build, device testing, and diagnosis; installation and usage examples.

## Engineering

- [game-demo-to-device-acceptance.md](engineering/game-demo-to-device-acceptance.md): shared C/WebAssembly and H5 playtesting, then physical-device acceptance.
- [environment-setup.md](engineering/environment-setup.md): clean-machine bootstrap for AI agents, including international and mainland China download routes.
- [build-and-test.md](engineering/build-and-test.md): ESP-IDF build and validation.
- [firmware-layout.md](engineering/firmware-layout.md): default and user-defined partition layouts and merged-artifact validation.
- [coding-conventions.md](engineering/coding-conventions.md): source-code and resource conventions, including Chinese font integration, blank/boxed text troubleshooting, and display acceptance.
- [lvgl-chinese-fonts.md](engineering/lvgl-chinese-fonts.md): step-by-step CJK configuration, font generation/linking, fallback examples, glyph checks, and troubleshooting.

## CI

- [CI-validation.md](ci/CI-validation.md): pull-request and main-branch checks.
- [CI-build-and-release.md](ci/CI-build-and-release.md): tagged firmware builds and releases.

## Release

- [project-completion.md](release/project-completion.md): project completion flow — a menu of optional closing actions.
