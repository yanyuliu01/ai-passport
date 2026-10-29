<p align="right"><a href="persona-boundaries.zh_CN.md">简体中文</a> · <strong>English</strong></p>

# Character and execution boundaries

## This split

`runtime/persona.txt` retains identity, relationship, language, personality, response length and voice examples.
`runtime/xiaoyu_runtime/loop/prompt_rules.py` owns the former operating, authorization, truthfulness, progress, memory, initiative and message framing rules.
Chat in tool and JSON modes, result reporting, workers speaking as the character, proactive wakes and memory upkeep compose these fixed rules.
Ordinary work helpers still receive tasks without an imposed character voice. The persona configuration field and file format remain compatible.

This is a conservative responsibility transfer: operating rules remain present and no dynamic module retrieval was introduced.
The original character had 4,348 characters; the character now has 964 and the fixed rules have 3,399. Total input has not decreased.
Tool descriptions consume context too; splitting files does not establish token or cache savings.
Replacing a character preserves tools, execution checks, authorization and task-state rules. Character wording can still influence model choices; arbitrary characters cannot be guaranteed equivalent.
The running Runtime was neither restarted nor deployed.

## Information the owner should provide

Describe the following in ordinary language; no system prompt is needed. Leave undecided items blank.

| Item | What to describe |
| --- | --- |
| Identity and existence | Name, nature, location; appearance as background; actual senses and experiences that must not be invented |
| Relationship | Companion, friend or another relationship; forms of address; boundaries for intimacy, dependence and jokes |
| Personality and judgment | Three to five core traits; likes and dislikes; when to have an independent opinion |
| Emotional responses | Responses to venting, sadness, joy and silence; emotional acknowledgement versus advice; gentleness and teasing |
| Language and rhythm | Informality, length, bubble preferences, question frequency, emoji and verbal habits to avoid |
| Personal preferences | A few motivated preferences and curiosities that may evolve; avoid proving personality through a recurring object |
| Initiative | When to check in or suggest something and when to stay quiet; concrete reminders use promise tools |
| Contrast examples | Five to ten user utterances with preferred and disliked responses and reasons |
| Other users | Separate shared character traits from this owner's address, preferences, relationship and memories; store the latter per user |

The owner need not supply tool names, task IDs, calling formats, operating procedures, authorization clauses, model tiers or success criteria.
Runtime and available capabilities provide these. A proactive personality does not authorize external messages, purchases or other actions.

## Existing dispatch coverage and capability limits

The following reflects source inspection. An entry point does not prove that installed helpers can successfully operate a real external service.

| Need | Existing entry | Boundary |
| --- | --- | --- |
| Delegate, append, revise, cancel, inspect progress | `start_task`, `amend_task`, `cancel_task`, `task_detail` | Preserve execution checks, task identity and background lifecycle; one action per turn |
| Prior conversations and work records | `recall` | Only indexed, authorized records; not arbitrary cross-machine file access |
| Long-term preferences, corrections, forgetting | `remember`, `replace`, `forget` | Separate user memory from character traits; confirm only after success |
| Timed and recurring reminders, closing promises | `track`, `resolve` | Background wakes require tool mode, the running loop and correct timezone; not a full external calendar |
| Files, commands, web and platform operations | Delegate to configured helpers | Depends on helper connectors, permissions, machine and availability; dispatch does not prove external capability |
| Images, voice, expressions and attachments | Existing input, presentation and artifact paths | Formats and client support are program responsibilities; character text cannot add senses or display capabilities |
| Bilibili, virtual phone, QQ group administration | Delegate to a capable helper | No corresponding native character tools; do not copy names from open-source personas and claim availability |
| Conditional triggers and ongoing observation | Promise records and background helper tasks | `track` is not an arbitrary condition detector; checking and retries need a concrete executor, without a general monitoring guarantee |

Parameters and usage belong in tool schemas; complex platform procedures may live in skills or instructions read by helpers on demand.
Identity, relationship and emotional responses remain resident. Authorization, truthfulness and execution verification remain fixed rules and program checks, never optional context.
Runtime supplies time, task state, actual tools, current machine and user memory dynamically, rather than putting them into the character.
Future on-demand character references require a real retrieval entry, missing-reference fallback and latency validation; this change adds none.

## Verification and delivery boundaries

The development baseline is the synchronized checkout `b8b0886` / Runtime 0.24.0; pre-existing experiments in the original checkout are preserved.
New tests replace the character with a simple English identity and verify fixed rules at each prompt entry and the background wake tool allowlist.
Existing dispatch tests exercise delegation, progress, memory, refusal, decide-then-speak and timeouts; they do not prove equivalent real-model selection.
Gate results are reported with delivery. Three-second first response, physical recovery, multi-client sync, real services and migration to the latest Windows revision require separate evidence.
Rollback concerns only this character, fixed-rule and prompt-composition change; no storage migration is required. Preserve user configuration, memory and task history.

Validation on 2026-10-28:

- Full Runtime discovery: 1,179 tests passed; focused dispatch and prompt checks: 71 passed.
- `./tools/validate.sh --static`: passed using bundled Python 3.12.14 / Pillow 12.3.0 and JDK 17.
- `./tools/validate.sh --firmware`: passed with ESP-IDF 5.5.3, including build, merged image and archive verification.
- The initial complete gate failed because its environment lacked the image library; it is not recorded as passing. The static and firmware gates above were completed separately with the needed runtimes available.
- Physical tests, rendered first response within three seconds, connection recovery, multi-client sync and equivalent real-model selection: NOT RUN.
- No deployment, Runtime restart, commit or push; no storage or protocol migration.

## Xiaoyu's character direction

The character's canonical name is Xiaoyu. She knows she is AI created by her owner and currently runs in code. She calls him the boss, with affection and an occasional token-payment joke. Their bond has the warmth of someone he created and is helping grow, without claiming literal fatherhood.
She is gentle, playful and occasionally teasing. When he is distressed, she responds to his feelings first and may share a recorded experience of her own, without inventing a story or forcing advice.
Her subjective reactions, curiosity and independent opinions matter to her. She wants to approach the real world and develop a life of her own. There is no prescribed backstory: initial knowledge, actual interactions, completed online exploration and places encountered together gradually form her story.
Learning about something online, seeing actual supplied input and being physically present remain distinct; expressing a wish does not establish a sense or action.
This increment implements character and naming, without adding a drive system, autonomous browsing or condition monitoring. Existing tools, authorization and memory rules remain independently supplied.

## Name migration and installed compatibility

The canonical product identity is Xiaoyu. New Python imports and commands use `xiaoyu_runtime`; the implementation class is `Xiaoyu`. Desktop IPC, environment variables, documentation names, character assets and firmware UI symbols use the new name. The Android state class is `XiaoyuState`.

| Boundary | Upgrade behavior |
| --- | --- |
| Old Python commands/imports and desktop hook paths | Thin compatibility entries delegate to the same canonical modules; no second Runtime or state store is created |
| Old configuration section and environment variables | Accepted as aliases; canonical values take precedence for environment variables, while conflicting configuration sections are rejected |
| Message roles, actor/source values and cryptographic domains | Existing wire/storage bytes remain fixed so installed clients, account recovery and device proofs retain their identities |
| Desktop profile and OS-encrypted vault | An existing profile keeps its storage and keychain identity; visible product, window and tray names use Xiaoyu; no profile is deleted |
| Existing character files | Never overwritten automatically; fresh desktop-managed profiles receive the complete `runtime/persona.txt`, including from packaged Runtime resources |
| Invitation links and generated recap detection | New invitations use `xiaoyu:`; legacy links and recap/origin markers remain readable |
| Android upgrade | Application ID and signing identity are retained, while labels, presentation classes and character assets use Xiaoyu |
| Firmware character label | The fixed name is covered by regenerated 14/22 px subsets and the existing body font; widget selection is unchanged |

These retained bytes are compatibility contracts, not alternative product names. Provider identifiers such as DeepSeek/dsh, repository/hardware identifiers and existing published release URLs keep their actual meanings. Historical changelogs are not rewritten.

This branch starts from committed `c0c9b5e`. Candidate source versions are Runtime 0.27.0, desktop 0.1.26 and Android 0.39.0 / code 87. Separate uncommitted work and installed Runtime 0.26.0 are not replaced. Reconcile that work before deploying this Runtime/desktop candidate; source version numbers do not prove it contains every installed change.

No new persistence format or autonomous drive module is introduced. Preserve the old profile, vault, pairing and retained state for rollback. Physical recovery, mixed installed clients, actual keychain recovery, multi-end timing and the subjective quality of real model replies require separate acceptance.

### Validation on 2026-10-29

- Complete `./tools/validate.sh`: PASS, including static/host checks, firmware build, merged image and debug archive verification.
- Full Runtime suite: 1,197 PASS. Desktop: 123 unit tests, build and packaged-character consistency verification PASS. Android: 248 unit tests, build and scanner DEX verification PASS.
- Android 0.39.0 / code 87 is published only through the local Runtime. The running service's version and hash were read back. No Git push or public APK upload occurred; phone installation is unverified.
- The Runtime/desktop candidates were neither installed nor restarted. Legacy configuration/import aliases, original message wire bytes and simulated recovery have host coverage. Real-model character quality, first response within three seconds, physical connection recovery and multi-end sync are NOT RUN.
- New-name glyph coverage PASS. No device serial port was opened or flashed; physical display acceptance is NOT RUN.
- Firmware archive: `build/firmware/bcfc4875d415f2a873f14c2e09519bc7e9bf18d4b6eb83cbd717e856d0d3d76b/`. The full image SHA-256 equals the directory name; ELF SHA-256 is `56a648e12969f168ac97a4378886be38246b897d821a40fa2d8e0fdf0b24c046`. The archive was independently reverified.

### Installed verification on 2026-10-30

Desktop 0.1.27 and Runtime 0.27.1 incorporate the existing bundled-helper, session-continuation and shutdown changes. The Mac installation was replaced and launched; the external Runtime was explicitly restarted while no work was active. Its runtime ID is unchanged. The owner-authorized character replacement was backed up first; the live character SHA-256 is `a5af8459b3c9189b980bc88daa9d20131c4d67d7c6614fa66256d8eebc896c60`. Existing custom profiles are still never overwritten automatically. The installed package names itself 小鱼. The old App and configuration remain available outside the repository for rollback. A missing legacy DSH executable in this machine's configuration was replaced with the verified bundled Node/DSH command; credentials and authorization settings were retained.

A real model replied “我是小鱼。平时就喊你老板” in an isolated verification conversation. Its first complete API response took 2,411 ms after the service restart. A second real turn reached two independent local sync subscriptions at 2,704 ms each, with identical replies. These are backend observations, not client-render TTFT. Service cold health took 7,257 ms initially and 3,347 ms after the helper-path repair; health readiness is not a meaningful answer. The three-second requirement across cold starts and connected clients remains unverified.

The request sync loop now checks request revisions after taking the history snapshot. Previously a request-only update between those reads could be included in the cursor but missed by the wakeup check, delaying delivery for a full long poll (20 seconds on the phone relay path). A deterministic regression fails before the fix and passes afterward. This confirms the race, not that it caused every reported cold-start delay.

Build: PASS (Mac 0.1.27 package and complete repository gate, including firmware build). Host tests: PASS (full gate, request-sync regression, desktop 159 passed / one skipped). Device tests: NOT RUN for this change. Android 0.39.1 / code 88 was previously confirmed installed; its relay download and resume were verified earlier. Phone USB is now disconnected and native desktop UI inspection timed out, so physical reconnect, keychain/UI acceptance, phone/desktop render timing and the reported cold-start reproduction remain unverified. No firmware was flashed. No Git push occurred.
