<p align="right">
  <a href="xiaoyu-system-prompt.zh_CN.md">简体中文</a> · <strong>English</strong>
</p>

# Xiaoyu system prompt

The maintained base prompt is [runtime/persona.txt](../../runtime/persona.txt).
The default configuration reads it at startup. The effective system prompt also
includes the current role, response format, available helpers, task context,
memory and promises, assembled by [xiaoyu.py](../../runtime/xiaoyu_runtime/loop/xiaoyu.py)
and [proactive.py](../../runtime/xiaoyu_runtime/loop/proactive.py). A custom
`persona_file` replaces the base prompt; it does not inherit this file automatically.

## Reference and adaptation

This revision adapts the overall behavior of the public, unofficial
[Grok Bot reconstruction](https://github.com/b-nnett/grok-bot-0.18-reconstructed/tree/a9f633e09d49a85829b8236331b9e21f7e612634).
The reviewed sources are its
[base system prompt](https://github.com/b-nnett/grok-bot-0.18-reconstructed/blob/a9f633e09d49a85829b8236331b9e21f7e612634/source/host/runner/system-prompt.ts)
and [prompt assembly](https://github.com/b-nnett/grok-bot-0.18-reconstructed/blob/a9f633e09d49a85829b8236331b9e21f7e612634/source/host/runner/system-prompt-assembly.ts).
This is an independently worded adaptation for Xiaoyu's capabilities, not an
official xAI prompt or a claim of identical model behavior.

| Reference area | Xiaoyu behavior |
| --- | --- |
| Turn rhythm; reply first | Answer directly, or state the concrete investigation before acting; deliver the eventual result. |
| Visible messages vs internal monologue | Use the current plain-text or JSON reply contract; keep internal reminders and dispatch details out of ordinary chat. |
| Tone; length and shape | Warm everyday language, varied sentences, brief answers and semantic message boundaries; respect requested detail and formats. |
| Progress and closure | Report meaningful findings and blockers; distinguish acknowledgement, scheduled work and completed work. |
| Showing work | Provide verified artifacts or supported previews when useful; never invent files or phone attachment support. |
| No fabricated data | Check facts, sources, statistics and UI paths; label examples and uncertainty. |
| Decisions; autonomy | Proceed with authorized routine choices; ask only for consequential actions or unresolved information. |
| Threads | Keep decisions and main results in the authorized conversation; use task details for technical bulk. |
| Work surfaces | Distinguish Runtime, phone, local and remote helpers; do not assume filesystem or connectivity equivalence. |
| Long commands; background delegation | Keep slow work with helpers, continue existing tasks and check actual status without busy polling. |
| Code work; cloud agents | Delegate with symptoms, constraints and acceptance criteria through available helpers; no Cursor cloud-agent dependency. |
| Plugins, connectors and accounts | Prefer available authorized service interfaces; verify account identity and read back uncertain writes. |
| Browser access and sign-in | Use only available, authorized capabilities; request the necessary human authentication step. |
| Debugging and app UI | Consult real evidence and current capabilities; do not invent recovery menus or controls. |
| Writing on the user's behalf | Match the recipient and authorized context; distinguish drafting from sending. |
| Initiative | Act on specific recurring work or a concrete gap, within scope; avoid unsolicited task expansion. |
| Approval and security | Preserve authorization boundaries; use safer scope or ask when blocked, without evasion or secret disclosure. |
| Dynamic profile, memory and routines | Retain Xiaoyu's identity, current memories and supported promise tools; stay quiet on non-actionable background wakes. |
| Other product-specific sections | Do not import Cursor Origin, box paths, tool names, widgets, profile editing or automation capabilities that Xiaoyu does not expose. |

## Compatibility and acceptance

The base prompt and shared speaking instructions change together. Shared
instructions reinforce message boundaries and preserve explicitly requested
original text, paths and evidence in helper relays. Existing tools, JSON schemas, memory files,
conversation identities, authorization gates and wire formats remain authoritative.
An isolated `---` line requests another message; ordinary paragraph breaks stay
inside the message. Runtime and clients must continue to handle that boundary
through their existing shared protocol.

In chat prompts, the common output instructions follow dynamic helper/task
context. Tool results are not new owner messages. Immediate memory changes use
the tool first and confirm once after success; they do not need an opening
acknowledgement. This adapts the reference's reply-first rule to Xiaoyu's actual
message path while avoiding premature completion claims.

Check prompt loading in chat, helper relay, background tasks and proactive turns.
Exercise short chat, explanation, requested lists, missing facts, task follow-up,
recall, action dispatch, permission boundaries and failures with an isolated
provider session. Such a sample is behavioral evidence, not a deterministic
guarantee or the required installed-client acceptance matrix.

Follow the [cross-stack delivery contract](cross-stack-reliability.md#validation-and-delivery)
for regression, first rendered response, connection recovery and multi-end sync.
Provider first text does not establish the first complete bubble rendered on a
phone. Reloading requires a Runtime restart: wait for no active work, or obtain
the owner's approval to interrupt it. For recovery, restore the prior persona
and reload under the same restart rule; no data migration is required.
