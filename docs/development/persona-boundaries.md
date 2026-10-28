<p align="right"><a href="persona-boundaries.zh_CN.md">简体中文</a> · <strong>English</strong></p>

# Character and execution boundaries

## This split

`runtime/persona.txt` retains identity, relationship, language, personality, response length and voice examples.
`runtime/xiaoyou_runtime/loop/prompt_rules.py` owns the former operating, authorization, truthfulness, progress, memory, initiative and message framing rules.
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
