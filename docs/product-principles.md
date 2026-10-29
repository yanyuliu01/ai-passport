<p align="right">
  <a href="product-principles.zh_CN.md">简体中文</a> · <strong>English</strong>
</p>

# Xiaoyu product principles (draft)

> Status: v0.1 draft, written by AI on 2026-10-19 from the current code, documents and the owner's past feedback. **Not in effect until the owner edits and approves it.** Each principle lists its basis: the owner's own words or a rule already in the repository.

## What this document is for

- It states what Xiaoyu is, what it does not do and how it behaves, so people and AI changing the product share one yardstick.
- [Auto-iteration](development/auto-iteration.md) version plans must check against it item by item: a plan names the principles it relies on (by number, for example "per P8") and any it conflicts with. Plans with serious conflicts are sent back.
- The principles change with versions. Changes are recorded in the revision log at the end.

## In one sentence

Xiaoyu is the owner's pocket AI companion: the owner talks only to her, she arranges AI helpers on the owner's own computers, and she brings progress and decisions that need the owner to the phone and the pocket device.

## What it does not do

- **No model or tool picker.** The owner does not choose helpers, models or effort tiers in the UI; saying it is enough.
- **No interruption-driven product.** No pop-ups, no promotional notifications, no need to keep refreshing for progress.
- **No owner data to third parties.** No cloud account system; conversations and usage data are not uploaded to anyone else's service.
- **The device is not a standalone smart terminal.** It is Xiaoyu's face and ears; work happens on computers.
- **Not designed for the local network.** A solution that only works at home is not a solution.

## Principles

### 1. Roles and conversation

**P1 One voice.** The owner talks only to Xiaoyu (whose character is Xiaoyu); Claude Code, Codex, DeepSeek and future local models are her tools and never appear as separate roles in the conversation.
Basis: the persona file; the app guide's "no helper or effort picker".
Check: does a new screen let the owner pick a helper, model or tier? Does the conversation contain lines like "I'll transfer you to X"?

**P2 The owner's explicit requests are hard constraints.** Who does it, which model, "do it yourself", "don't use X" must be followed; "from now on never …" is a standing preference that applies to every later delegation.
Basis: "why did you give it to cc, can't you hand it over", "I said use ds v4 Pro", "don't let it use Astra again unless I say so".
Check: can the owner's constraints be produced and checked before delegating? Is a delegation that violates them stopped?

**P3 Do simple things yourself.** Xiaoyu has the fullest context; follow-ups, summaries, organizing and simple files are not delegated. Only heavy work such as multi-file code changes, builds and tests, or long debugging goes to helpers.
Basis: "just look yourself, don't send this kind of task to others", "you don't need it to sync, you can look yourself".
Check: of the work delegated, how much could Xiaoyu have finished in a few steps?

**P4 Conclusion first, complete and honest.** Lead with the conclusion, then the necessary detail; reports are structured and never truncated; say plainly what was not done; "done" needs evidence (file exists, tests pass, and device verification stated separately).
Basis: the persona file; "think it through, be complete, give me a structured report"; the repository rule against calling a build a hardware validation.
Check: is any reply truncated? For anything called done, can the evidence be pointed to?

### 2. Speed and reliability

**P5 A response within three seconds.** From the owner sending a sentence to the first meaningful reply on screen takes at most 3 seconds; "received" does not count.
Basis: [cross-stack reliability rules](development/cross-stack-reliability.md); the owner's "answers are slow".
Check: measured from the client as the rules require, not from server time.

**P6 Nothing lost, out of order, or broken.** Phone, device and computer show the same messages; reconnecting fills the gap; a new version does not break working features.
Basis: the reliability rules; "why isn't the app in sync?".
Check: verify both ends online, recovery after disconnect, and before/after an upgrade.

**P7 Slow work never blocks quick answers.** Long work runs in the background; the owner can keep talking, add to it or cancel it.
Basis: the runtime's "one thing, one card" design.
Check: while a long task runs, does a new sentence get a response within 3 seconds?

### 3. Interruption and control

**P8 Interrupt only for decisions, through one place.** Things needing the owner's consent gather in one place (the phone's approval bar, the device's confirmation); progress and results appear quietly on task cards, with no pop-ups.
Basis: the device guide's "the screen never switches by itself"; this session's discussion of how auto-iteration reaches the owner.
Check: does a new feature add a pop-up, a system notification, or a second "pending" entry point?

**P9 Fewer taps.** Similar confirmations can be batched or granted for a short time; anything that changes or deletes things, spends money or sends content outside still asks first.
Basis: "too many things need me to confirm one after another, I can't tap through them in the app"; the persona's confirmation rule.
Check: how many taps does a common task take, and which can be merged?

**P10 Works anywhere.** The phone reaches the computer from any network; local-network-only solutions are not accepted.
Basis: the owner explicitly rejected LAN-only solutions.
Check: does it work on 5G with the computer elsewhere?

### 4. Device and interface

**P11 The device is a glance.** The small screen shows only what one look needs: a one-line conclusion, who is working, whether consent is needed. The screen never jumps away; when an answer arrives the owner stays on the screen they were reading.
Basis: the device guide.
Check: does each device screen have one focus? Does anything switch screens automatically?

**P12 Chinese-only interface, checked on the real device.** Including error hints and how helpers' raw output appears on the device; Chinese glyphs are confirmed on hardware, not by a successful build.
Basis: the repository's Chinese font rules; "messages on the home page show English".
Check: does any new text leak English? Do device photos show missing glyphs?

**P13 Colour answers only "who is working".** Xiaoyu is sea blue and each helper has a fixed colour; green, red and yellow mean only yes, no and attention.
Basis: the device guide.
Check: does a new screen use green, red or yellow as decoration?

### 5. Cost

**P14 Save money first.** Use local models whenever possible; expensive models and tiers only when the owner asks or the step truly sets direction, and always under a budget cap.
Basis: "don't let it use Astra … it's too expensive", "never use Astra"; "the point of local is that I want to save cost".
Check: which model does a new feature use by default? What is the most it can spend in a day?

**P15 Spending is visible.** For each task the owner can see the tokens and quota it used.
Basis: the owner asked for per-task usage.
Check: after a task, can its cost be found?

### 6. Data and architecture

**P16 The data belongs to the owner.** Conversations, records and usage data stay on the owner's own devices; usage data never includes message text, typed input or screenshots. Publishing installers that the owner authorized is the exception.
Basis: repository safety rules; the usage event contract.
Check: does a new feature send data out? What, to whom, and does the owner know?

**P17 No centre, free to move.** Any computer can join or leave, and data and work in progress move with it; there is no "host that must stay on".
Basis: "I want no notion of a centre; I can migrate flexibly".
Check: with any one computer switched off, can the others carry on? Does a newly added computer get all the data?

**P18 Pluggable.** Helpers, models, data sources and every iteration stage can be added, replaced or removed; removing one degrades the whole instead of breaking it.
Basis: the owner's request that auto-iteration be plugin-based and pluggable; the agent gateway proposal.
Check: is a new capability hard-coded into the main flow, or registered?

**P19 Updates without cables.** The app reaches the phone through the runtime and firmware reaches the device over Bluetooth; the owner confirms at most once, and a bad version can be rolled back.
Basis: the repository's release flow; firmware that is not confirmed rolls itself back.
Check: how many steps until a new version is in the owner's hands? How does it go back if broken?

### 7. How to iterate

**P20 Change systematically by version, not by patches.** A batch of problems is grouped and its shared causes found before one complete version plan is written; a single issue is evidence, not the unit of change.
Basis: "these all feel like single-point fixes, not engineered enough", "point-to-point fixes will cause many problems".
Check: does every change in a plan trace to a theme and a root cause?

**P21 Say how far verification went.** Build passing, unit tests passing, device verification and owner confirmation are four different things and are reported separately.
Basis: the repository's delivery rules.
Check: does a report call "it compiles" "it works"?

## Open points for the owner

- P8 "no pop-ups": should a new release or a failed task be an exception that sends a system notification?
- P14 budget: what are the daily and monthly caps for network models?
- P3 boundary: does Xiaoyu judge what is "simple", or should there be a list?

## Revision log

| Version | Date | Change |
| --- | --- | --- |
| v0.1 | 2026-10-19 | Drafted by AI, awaiting the owner's edits |
