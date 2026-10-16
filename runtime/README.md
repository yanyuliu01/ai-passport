<p align="right">
  <a href="README.zh_CN.md">简体中文</a> · <strong>English</strong>
</p>

# Xiaoyou Runtime

Agents changing this project must first read the [root instructions](../AGENTS.md)
and [mandatory App/Runtime/firmware reliability rules](../docs/development/cross-stack-reliability.md).
They define regression, 3-second first-response, connection recovery, and
multi-end consistency acceptance. The capability descriptions below do not
establish that these requirements have passed validation.

Xiaoyou is the single voice the owner talks to, and an identity of her own: she
is not another name for one model. This runtime is the small, always-on service
behind her, and what is hers lives here: the persona, the transcript of her
conversations with the owner, the decision of who gets each message, and how
somebody else's result is reported back. Claude Code, Codex, Xiaoyou on another
computer, and a local model added later are all **agents** she can use.

Every request the owner makes is **one thing** with a card of its own: what he
said, what Xiaoyou answered and anything added later are kept on that card.
Xiaoyou herself only does what is quick: she understands the sentence, answers
at once when she can, and hands anything that takes time to a helper working in
the background. So an unfinished thing does not hold up the next sentence,
several things can be in progress at once, and a thing can be added to, changed
or cancelled while it runs. Whatever Xiaoyou says comes as a full reply, a
short brief for the Passport screen, and a mood for the pixel pet; Markdown
markup, code fences and emoji in a reply are turned into plain text first so
the small screen and terminals show it cleanly. A message
can be typed text or a voice recording; a recording is first turned into text
by a speech recognition engine chosen in the configuration.

```text
phone app / any client ──HTTP──> Xiaoyou Runtime
                                   │  persona · transcript · cards · routing · reporting back
                                   ├──> claude   (Claude Code command line; can answer as Xiaoyou)
                                   ├──> codex    (Codex command line; does work only)
                                   ├──> dsh      (DeepSeek Harness command line; can answer as Xiaoyou)
                                   ├──> any command (a local model, for example)
                                   └──> Xiaoyou Runtime on another computer
```

Status: verification stage, intended to run on your own computer. The same
code is meant to move to a virtual machine later; nothing in it assumes a
particular host. See [What is and is not verified](#what-is-and-is-not-verified).

## Requirements

- Python 3.9 or newer. No third-party packages, unless you enable the built-in
  speech recognition engine (see [Voice](#voice)). Pillow is optional for resized
  image thumbnails; attachment registration and downloads also work without it.
- Whatever each configured agent needs: a `claude_code` agent needs Claude Code
  installed and logged in on the same machine and user (`claude` must work in a
  terminal); a `codex` agent needs the Codex command line installed and logged
  in. `--check` names any agent whose command cannot be found.

## Quick start

```bash
cd runtime
cp config.example.json config.json
python3 -c "import secrets; print(secrets.token_urlsafe(32))"   # paste into server.token
python3 -m xiaoyou_runtime --config config.json --check
python3 -m xiaoyou_runtime --config config.json --once "hello"   # one turn, no server
python3 -m xiaoyou_runtime --config config.json                  # start the service
```

`config.json`, `state/`, and `workdir/` are ignored by Git. Never commit a real
token.

To try the whole path without calling any model, start it with the environment
variable `XIAOYOU_DEFAULT_AGENT=echo`: every message goes to an agent that
repeats it, and the configuration file stays as it is.

## View Runtime tasks in desktop apps

Set `"desktop_open": true` on a `claude_code` or `codex` agent to request that
its original session open in the native desktop app after a successful background
task finishes. Queued follow-up rounds finish first. Normal conversation replies,
failed tasks and cancelled tasks do not launch an app. This option defaults to
`false`; opening failures are reported in the Runtime log without discarding the
task result. A successful launcher exit means the request was sent, not that the
desktop UI has been verified. When an agent uses an isolated home, only that
completed session transcript is imported into the default desktop home; account
settings, credentials and unrelated conversations are never copied, and no app
database is edited. The import keeps the session ID and publishes a fully written
file atomically. Identical imports are idempotent; an existing different transcript
is never overwritten. This is a local snapshot: desktop continuations do not flow
back into the isolated Runtime session. A later Runtime round with different
content is reported as a conflict, not silently merged. Referenced external tool
artifacts and subagent files are not imported.

For retained tasks, from the repository root:

```bash
PYTHONPATH=runtime python3 -m xiaoyou_runtime --config runtime/config.json desktop list
PYTHONPATH=runtime python3 -m xiaoyou_runtime --config runtime/config.json desktop open --task c149 --agent codex
```

Use the configured agent name (for example `cc`, not necessarily `claude`).
Opening is refused while a task is working, waiting, or has an unknown state.
These commands only read Runtime state and do not run startup cleanup or invoke
a model. Use one owner at a time: once you start interacting in the desktop app,
do not also send work to that session through Runtime.

- **Claude Code:** uses `claude --desktop --resume <session-id>` with the agent's
  original working directory and `CLAUDE_CONFIG_DIR`. Requires a supported Claude
  Desktop installation, subscription login, and Claude Code 2.1.285 or later on
  macOS. The Runtime launcher currently supports macOS only; on Windows use the
  native command in an interactive terminal. Explicit API/third-party-provider
  agents are refused. The launcher clears ambient provider routing and uses the
  local subscription login without changing the worker environment. A session from a
  custom configuration directory is imported into `~/.claude/projects/` before
  opening. On macOS a temporary terminal is used because the CLI rejects redirected
  I/O; no prompt is sent and the launcher is bounded to 15 seconds. macOS has been
  exercised locally. This is a completed-session
  handoff, not live mirroring of `claude -p`.
  See [Claude's desktop documentation](https://code.claude.com/docs/en/desktop#coming-from-the-cli).
- **Codex:** opens `codex://threads/<session-id>` on macOS. An isolated
  `CODEX_HOME` transcript is imported into `~/.codex/sessions/` with its original
  date layout. The desktop app continues under its own account and settings;
  Runtime's configuration and login remain unchanged. Desktop source filters can
  still hide external sessions from the sidebar; opening by ID avoids relying on
  sidebar discovery. A successful import/read does not prove live display.

For a Codex service shared with a desktop client, optionally set
`"app_server_socket": "/absolute/path/to/app-server-control.sock"` and keep
`"mode": "app_server"`. Runtime uses `codex app-server proxy --sock <path>`;
the service must already be running and the desktop client must actually use
that same service and home. The socket is not auto-discovered, and starting a
separate daemon does not by itself connect the desktop app. A missing or
incompatible service fails visibly without silently launching a private fallback.
Only Runtime's proxy process is closed; cancellation sends an interrupt for this
task's turn, and events belonging to other threads are ignored. This optional
transport preserves Runtime's approval and steering protocol. Shared-service
support alone does not guarantee live display in the desktop app.
See the [App Server protocol](https://learn.chatgpt.com/docs/app-server).

After changing configuration, restart Runtime only when no task is running, or
obtain the owner's approval to interrupt work. This feature is separate from the
read-only observation described below.

## Sync existing desktop conversations

Start the Runtime with `--desktop-sync` to show conversations you are already
using in the Codex desktop app or Claude Code, including locally recorded Claude
Desktop sessions. Their visible conversation content and status join the phone
App's cards and can be forwarded to the Passport over the existing
App/Bluetooth connection:

```bash
cd runtime
python3 -m xiaoyou_runtime --config config.json --desktop-sync
```

The observer checks changes every two seconds and discovers new transcript files
every ten seconds. It reads the newest 32 local transcript files changed in the
last 24 hours, under `~/.codex/sessions` and `~/.claude/projects`. Subagents and
sessions already managed by this Runtime are excluded. To limit observation to
specific projects, repeat `--desktop-project`; the other two options select the
agent's configuration directory, which contains `sessions/` or `projects/`:

```bash
python3 -m xiaoyou_runtime --config config.json --desktop-sync \
  --desktop-project /absolute/project-a --desktop-project /absolute/project-b \
  --desktop-codex-dir /absolute/codex-config \
  --desktop-claude-dir /absolute/claude-config
```

Every integrated agent uses one canonical `card.title`: the upstream desktop
sidebar name takes priority, followed by an explicit local task name, a short
version of the latest human request, then a stable session/task fallback. A
result summary or assistant reply never replaces the task name. Original sidebar
and explicit task names retain their wording and internal spacing, with
recognizable credentials scrubbed and a 200-character limit; a request-derived
name is limited to 24 characters. `title_source` is `upstream`, `task`, `request`,
or `fallback`, and `title_truncated` marks a name shortened to its limit.

The Codex title adapter reads `threads.name` from its local SQLite state in
read-only mode, including live WAL updates; it never treats the original prompt
in `threads.title` as the sidebar name. Recognized session-index and legacy
sidebar-name metadata provide a fallback. The Claude adapter reads explicit
`custom-title` records, then the display name in its session index. Names refresh
even when no conversation event arrives, so a desktop rename updates the same
card. The current local collectors cover Codex and Claude only. Other agent
integrations share the canonical title policy but need an adapter-provided
upstream name to show their sidebar title; otherwise they use the same fallbacks.

The App's lists and detail view use that same `card.title`. Hardware task/history
names use it too, clipped to the existing 47-byte UTF-8 limit without an added
project or computer prefix; the source, project, and status have their own fields.

The observer synchronizes human requests, visible assistant replies and progress
comments, together with project/session metadata, model identifiers, timestamps,
status, and tool names. It excludes system/developer instructions, AGENTS
instructions, injected environment/browser context, reasoning, raw tool
arguments/results, and raw transcript records. Recognizable credential patterns are
redacted before storage and transmission; arbitrary private prose is not
guaranteed to be detected.

Each card holds a recent fragment, not the full transcript: at most 20 entries,
4,000 characters per entry, and 32 KiB of UTF-8 text per card. Initial discovery
reads at most the latest 2 MiB of a transcript. Cards carry
`content_scope: "recent"`; `content_truncated: true` indicates omitted older or
longer content. The phone shows the retained fragment with separate owner,
Codex, and Claude labels. The hardware shows the latest request and reply through
the existing `card` message: `said` is limited to 159 UTF-8 bytes and the App's
desktop `reply` to 480 bytes, within the device's shared 4 KiB text pool. Firmware
that already supports desktop cards needs no new flash for content sync.

A desktop conversation whose turn has stopped can be continued from the phone on
its page (since 0.8.1): the runtime opens a new task, titled after that conversation, and runs one turn in that
session's directory with its session id (Claude sessions go to a `claude_code`
agent, Codex sessions to a `codex` agent), without Xiaoyou's persona. The session is
not recorded as the runtime's own, so desktop observation continues and the owner
can go back to the computer. It is refused while the turn on the computer is still
running (`running`/`waiting` and not `stale`). Stopping stays in the desktop app. A live permission request can be answered separately from the phone
when the optional approval bridge below is installed and trusted; a status signal
or notification without that decision channel still requires desktop confirmation.
Ending a response means this turn ended, not that the whole project is complete.
An active session becomes `unknown` after five minutes without source events,
or when its transcript becomes unavailable.

The transcript adapters depend on formats that can change between agent
versions. Logs alone cannot reliably identify every approval wait. This feature
observes local records; cloud-only conversations without a local transcript are
not guaranteed to appear.

### Shared history for desktop agents

The shared-history reader lets Codex and Claude Code pick up Runtime work
without asking the owner to repeat it. It reads Runtime cards/conversations and
both agents' local visible transcripts, including their separate `config_dir`
directories from this Runtime configuration. The common schema includes source,
actor, task/card/session ID, original message timestamps, state, visible text,
attachments, and whether content is a recent fragment. Other clients can consume
the JSON output without depending on either agent's transcript format.

From the repository root, query the retained history without calling a model:

```bash
python3 runtime/xiaoyou_runtime/shared_history.py --context
python3 runtime/xiaoyou_runtime/shared_history.py --task c127
python3 runtime/xiaoyou_runtime/shared_history.py --format json
python3 runtime/xiaoyou_runtime/shared_history.py --export runtime/state/history-context/shared-history.md
```

Use `--config /absolute/config.json` and `--project /absolute/project` for another
Runtime/project, and repeat `--codex-dir` or `--claude-dir` for additional local
configuration directories. The reader never starts/resumes a helper, writes an
agent transcript/database, or turns a recorded approval into a new decision.
The repository's `AGENTS.md` startup rule uses the same reader, and `CLAUDE.md`
points to that rule as well.

Optional context hooks automate delivery on `SessionStart` (including resume
and compaction) and `UserPromptSubmit`. The first event supplies a bounded recap;
later prompts receive only changed tasks/dialogue. The current desktop session
and Runtime-owned helper transcripts are excluded from duplicate observation.
Injected history is filtered out when desktop transcripts are read again. Each
agent/session has a separate private fingerprint cursor. Both hooks use the
official `hookSpecificOutput.additionalContext` contract; they never block a
prompt or approve an operation, and failures return control to the desktop.

Prepare and install these two context hooks, preserving existing settings and
approval/status hooks:

```bash
python3 tools/setup-history-sync.py            # preview destination files
python3 tools/setup-history-sync.py --install  # merge; private backups retained
```

This updates `~/.codex/hooks.json` and `~/.claude/settings.json` for this project.
Codex requires reviewing/trusting the changed definition through `/hooks`.
Claude Code must load the updated settings in a new/resumed session. A hook
refreshes `state/history-context/shared-history.md` for people to inspect in a
desktop file panel. Context delivery is distinct from native chat bubbles:
neither adapter imports fabricated turns into the other app's conversation.
See the official [Codex hooks](https://learn.chatgpt.com/docs/hooks) and
[Claude Code hooks](https://code.claude.com/docs/en/hooks) contracts.

This is a view of retained visible history, not an unlimited archive. Runtime
card/transcript retention still applies. Per agent configuration directory the
reader selects the newest 32 project transcript files and uses the observer's
bounded recent-content adapter; old archived/cloud-only/deleted conversations
may be unavailable. Original messages retain their timestamps; a fresh import
does not make old work new. A task marked `done` indicates a completed turn and
does not prove deployment or device acceptance. Recognizable credentials are
redacted, but arbitrary personal prose remains private local content. Generated
views and delivery cursors stay in ignored `state/`, outside Git.

### Optional lifecycle hooks for approval waits

The optional hook receiver records status snapshots in the same `state_dir` as
the running Runtime. Lifecycle snapshots contain metadata and status, without
prompts, replies, raw tool arguments, or tool results. Permission requests and
permission notifications additionally create a private approval record with the
requested operation description and an operation fingerprint; recognizable
credentials in that description are redacted. The receiver reads one bounded
JSON object from standard input. Status-only events produce no standard output.
From `runtime/`, the receiver command is:

```bash
python3 -m xiaoyou_runtime.desktop_hook --state-dir /absolute/runtime-state --agent codex
```

For a hook launched from any project, use this reusable command handler. Replace
both absolute paths; `runtime-state` must be the Runtime's resolved `state_dir`:

```json
{
  "type": "command",
  "command": "PYTHONPATH=\"/absolute/ai-passport/runtime\" python3 -m xiaoyou_runtime.desktop_hook --state-dir \"/absolute/runtime-state\" --agent codex",
  "timeout": 2
}
```

Put the handler in each event's `hooks` list, inside a matcher group, for example
`"UserPromptSubmit": [{"hooks": [handler]}]` where `handler` means the JSON object
above. Use the same handler with `--agent claude` for Claude Code. These are the
receiver's event mappings:

| Event | Recorded status | Agent |
| --- | --- | --- |
| `SessionStart`, `SessionEnd` | `unknown` | Both |
| `UserPromptSubmit`, `PostToolUse` | `running` | Both |
| `PermissionRequest` | `waiting` | Both |
| `Stop` | `idle` (this response ended) | Both |
| `Interrupt` | `interrupted` | Codex |
| `StopFailure` | `failed` | Claude Code |
| `Notification` with `permission_prompt` | `waiting` | Claude Code |

Claude Code's permission notification also covers some prompts not represented
by `PermissionRequest`. Its matching hook group can be added as follows:

```json
{
  "hooks": {
    "Notification": [{
      "matcher": "permission_prompt",
      "hooks": [{
        "type": "command",
        "command": "PYTHONPATH=\"/absolute/ai-passport/runtime\" python3 -m xiaoyou_runtime.desktop_hook --state-dir \"/absolute/runtime-state\" --agent claude",
        "timeout": 2
      }]
    }]
  }
}
```

Merge these groups into existing hook configuration, preserving other handlers.
Codex discovers `~/.codex/hooks.json` or project `.codex/hooks.json`, and requires
reviewing and trusting the exact current hook definition through `/hooks` before
it runs. Claude Code accepts hooks in its settings files. See the official
[Codex hook reference](https://learn.chatgpt.com/docs/hooks) and
[Claude Code hook reference](https://code.claude.com/docs/en/hooks) for configuration
locations and lifecycle behavior. The Runtime does not install or modify global
hook configuration automatically. Build and host-test success do not establish
phone delivery, Bluetooth forwarding, or hardware rendering; test that path with
the connected App and device.

### Optional desktop permission bridge

For a synchronous `PermissionRequest` hook, add `--approval-bridge` and
`--approval-timeout 300` to the receiver command. It waits up to 300 seconds
(configurable, at most 600) for one `allow` or `deny` answer, then prints exactly
one official `hookSpecificOutput` decision. This answers that operation only.
Timeout, an unavailable Runtime, a closed session, or a lost hook lease produces
no decision and returns control to the desktop's normal permission prompt;
silence never grants permission.

Generate mergeable configuration for each agent instead of hand-editing timeout
values:

```bash
python3 -m xiaoyou_runtime.desktop_hook --state-dir /absolute/runtime-state \
  --agent codex --approval-timeout 300 --print-config
python3 -m xiaoyou_runtime.desktop_hook --state-dir /absolute/runtime-state \
  --agent claude --approval-timeout 300 --print-config
```

The generator prints JSON; it does not install anything. Merge it into the
agent's hook configuration, preserving existing handlers, and keep the hook's
outer timeout longer than the approval wait (the generated value is 310 seconds).
Start this Runtime with `--desktop-sync`, using the same resolved `state_dir`.
Codex must review and trust the exact installed definitions through `/hooks`;
Claude Code must load the matching settings for that session. Having the source
code or passing tests does not activate hooks in existing desktop sessions.

Requests live in the private `state_dir/desktop-approvals/` spool. Runtime checks
the owning hook's heartbeat, session/turn/tool identity, operation fingerprint,
and deadline before accepting an answer. Duplicate or stale answers are rejected.
Oversized descriptions, unsupported hook channels, and Claude's
`permission_prompt` notifications are notification-only: the App tells the owner
to confirm on the computer and exposes no remote allow/deny buttons.

Desktop approvals carry `source: "desktop"`, `session_id`, `project`,
`request_id`, `remote_actionable`, and `expires_at` (with optional turn/subagent
identifiers). `/v1/feed` includes them across conversations, so changing the
phone's chat does not hide a desktop request. The desktop card remains read-only
even when its separate, still-valid approval has `remote_actionable: true`.
The phone needs Android notification permission to alert in the background;
without it, the approval is still visible inside the App.

## Configuration

Relative paths are resolved against the directory of the configuration file.

| Key | Default | Meaning |
| --- | --- | --- |
| `server.host` | `127.0.0.1` | Address to listen on. `tailscale` (since 0.5.6) means only this machine's Tailscale address (100.x.y.z): it is looked up at start, so the configuration does not change when the address does, and `--pair` prints the pairing string with it; with Tailscale off the runtime refuses to start. The LAN address does not reach this runtime; it also listens on `127.0.0.1` (same port) for local forwarders such as Tailscale Funnel, which other hosts cannot reach. |
| `server.port` | `8765` | Port to listen on. |
| `server.name` | the machine's host name | What the phone app calls this runtime, at most 40 characters. |
| `server.token` | none | Shared secret, at least 16 characters. The placeholder is rejected. |
| `state_dir` | `state` | Where the session table, the cards and Xiaoyou's transcript are kept. |
| `persona_file` | `persona.txt` | Xiaoyou's persona. Given only to an agent that answers as Xiaoyou. |
| `brief_max_chars` | `120` | Upper bound for the small-screen brief (20 to 400). |
| `turn_timeout_seconds` | `0` | How long one thing in the background may take from start to end; `0` means no limit (the default since 0.5.4; it was `3600`). Used by agents that do not set their own `timeout_seconds` (`0`, or 10 to 86400). Whether a thing is stuck is judged by how long it has been silent, see [Long tasks](#long-tasks-watched-for-silence-not-for-total-time). |
| `idle_timeout_seconds` | `600` | When a thing in the background has produced no event for this long, the owner is asked whether to keep waiting (`0` turns this off, or 30 to 86400). |
| `idle_timeout_command_seconds` | `1800` | The same, while an operation has started and not finished (the event stream is quiet during a build or a test run anyway). Must not be smaller than the previous one. |
| `idle_answer_seconds` | `1800` | When nobody answers that question for this long, the thing is stopped (`0` waits forever, or 30 to 86400). |
| `agents.<name>` | none | An agent; see [Agents](#agents). At least one must be enabled. |
| `xiaoyou.default_agent` | the first agent | Who gets a message when nobody is named and the router has no opinion. |
| `xiaoyou.voice_agent` | the default agent, or the first one that speaks if the default only works | Who reports back, as Xiaoyou, after an agent that only does work. |
| `xiaoyou.max_parallel` | `0` | How many things may be in progress at once (0 to 64); `0` means no limit. The rest wait their turn. |
| `xiaoyou.voice_timeout_seconds` | `60` | How long Xiaoyou herself may take over one sentence (5 to 600). She gets no tools for this step; it is only understanding and dispatching. |
| `xiaoyou.max_handoffs` | `2` | A 0.4 setting that no longer has any effect; still read so that old configurations load. |
| `xiaoyou.router.type` | `mention` | `mention` or `command`; see [Who gets a message](#who-gets-a-message). |
| `xiaoyou.router.command` | `[]` | `command`: the command that decides. |
| `xiaoyou.router.timeout_seconds` | `10` | `command`: after this long the default agent is used instead (1 to 120). |
| `stt.engine` | `none` | Speech recognition: `none`, `sense_voice`, or `command`. See [Voice](#voice). |
| `stt.model_dir` | none | `sense_voice`: folder holding `model.int8.onnx` (or `model.onnx`) and `tokens.txt`. |
| `stt.language` | `auto` | `sense_voice`: `auto`, `zh`, `en`, `ja`, `ko`, or `yue`. |
| `stt.threads` | `2` | `sense_voice`: processor threads (1 to 16). |
| `stt.command` | `[]` | `command`: the command to run; one argument must contain `{audio}`. |
| `stt.timeout_seconds` | `60` | `command`: a recognition run is stopped after this long (5 to 600). |
| `firmware.repo` | none | The GitHub repository to fetch built device firmware from, as `owner/name`. See [Device firmware](#device-firmware). |
| `firmware.asset` | `FoloToy-AI-Passport-full.bin` | The name of the firmware file in a release. |
| `firmware.tag_prefix` | `firmware-build-` | Releases that carry firmware have a tag that starts with this. |
| `firmware.source_dir` | none | Where the firmware source is on this computer; used by `fetch --commit HEAD` and `build`. |
| `firmware.build_command` | `[]` | The command that builds the firmware on this computer, run in `source_dir`. Empty means this computer does not build it. |
| `firmware.build_output` | `build/FoloToy-AI-Passport.bin` | The firmware file a build produces, relative to `source_dir`. |
| `workspace.type` | `none` | `none` or `notion`. See [Shared workspace](#shared-workspace). |
| `workspace.token` | none | The Notion integration token; the environment variable `XIAOYOU_NOTION_TOKEN` also works. |
| `workspace.token_file` | none | A file that holds the token (a relative path is taken from the folder of the config file), read once at start; set this or `workspace.token`, not both. For keeping the token out of `config.json`. |
| `workspace.bus_database` | none | The id of the "bus" database (the 32 characters in its link). Required for `notion`. |
| `workspace.log_database` | none | The id of the "log" database. Leave it out to write the bus only. |
| `workspace.author` | `xiaoyou` | The name Xiaoyou signs with in the shared workspace. |
| `usage.enabled` | `true` | Whether the runtime asks for subscription quota on its own (since 0.6.0). With `false` no process is started for that; what helpers report while they work is still recorded. See [Usage](#usage-what-is-left-of-each-subscription). |
| `usage.refresh_seconds` | `300` | How often to ask again while a client is watching (60 to 86400). |

Environment variables override the file, so a container or virtual machine can
be configured without editing it: `XIAOYOU_CONFIG`, `XIAOYOU_HOST`,
`XIAOYOU_NAME`, `XIAOYOU_PORT`, `XIAOYOU_TOKEN`, `XIAOYOU_STATE_DIR`,
`XIAOYOU_DEFAULT_AGENT`, `XIAOYOU_CODEX_CONFIG_DIR` (applied to every `codex`
agent), `XIAOYOU_CLAUDE_CONFIG_DIR` (applied to every `claude_code` agent), `XIAOYOU_DSH_CONFIG_DIR`
(applied to every `dsh` agent),
and `XIAOYOU_NOTION_TOKEN`.

With `XIAOYOU_DEBUG` set (to anything), every sentence Xiaoyou takes adds one
line to standard error: which card she put it on and what she asked the
runtime to do.

A configuration written for 0.3 or earlier (`backend`, `claude_code`, `tools`)
still loads: it is read as a single agent named `claude`, a notice at start-up
says it is the old shape, and existing sessions continue. An entry under `tools`
that was enabled has to be rewritten by hand as an agent under `agents`.

## Agents

`agents` is an object whose keys are agent names (letters, digits, underscores
and hyphens, at most 24 characters). Every agent has:

| Key | Default | Meaning |
| --- | --- | --- |
| `type` | none | `claude_code`, `codex`, `dsh`, `command`, `remote`, or `echo`. |
| `enabled` | `true` | `false` makes the runtime treat it as absent. |
| `description` | empty | One sentence on what it is good at. Xiaoyou reads it to decide whether to hand work over, and so does the router. |
| `aliases` | `[]` | Other names it answers to when named in a message, for example how speech recognition tends to spell it. |
| `speaks` | by type | Whether it may answer as Xiaoyou. `claude_code`, `dsh`, `remote` and `echo` do by default; `codex` and `command` only do work. |
| `timeout_seconds` | `turn_timeout_seconds` | How long one run of this agent may take from start to end; `0` means no limit. Ways of running that have no event stream (`command`, and `codex` in `exec` mode) use 3600 seconds when this is `0`. |
| `idle_timeout_seconds`, `idle_timeout_command_seconds` | the top-level settings of the same name | How long this agent may be silent before it counts as possibly stuck. Only for ways of running that have an event stream: `claude_code`, `dsh`, and `codex` in `app_server` mode. |

Keys by type:

| Type | What it does | Keys |
| --- | --- | --- |
| `claude_code` | Runs the Claude Code command line non-interactively, with the login already present on this machine. | `command` (default `["claude"]`), `workdir` (default `workdir`), `config_dir`, `model`, `permission_mode` (default `manual`), `allowed_tools`, `add_dirs` (default `["~"]`), `extra_args`, `env`, `env_files`, `models`, `effort`, `efforts` (see [Tiers](#tiers-model-and-effort)) |
| `codex` | Runs the Codex command line. By default in its app-server mode (`codex app-server`): an operation that needs confirmation is put to the owner, and a sentence can be added while it works. With `mode` set to `exec` it uses `codex exec` (and `codex exec resume` to continue): one run to the end, and it never asks. | `command` (default `["codex"]`), `workdir`, `config_dir`, `mode` (default `app_server`), `approval_policy` (default `on-request`), `sandbox` (default `read-only`), `model`, `extra_args`, `models`, `effort`, `efforts` |
| `dsh` | Runs the DeepSeek Harness command line (`dsh`); see [DeepSeek Harness](#deepseek-harness-dsh) below. | `command` (default `["dsh"]`), `workdir`, `config_dir`, `sandbox` (default `workspace-write`), `stream` (default `true`), `model`, `extra_args`, `env`, `env_files`, `models`, `effort`, `efforts` |
| `command` | Any command. The text goes in on standard input and standard output is the result; an argument containing `{prompt}` receives the text instead. It has no session: every run starts fresh. | `command`, `workdir` |
| `remote` | Xiaoyou Runtime on another computer. That side has its own persona, agents and sessions; what comes back is already Xiaoyou's words. | `url`, `token` (that runtime's `server.token`) |
| `echo` | Repeats what it is given; calls no model. | none |

`permission_mode` is the permission mode Claude Code works under when it is
given a thing to do. The default is `manual`: whatever it would ask about in a
terminal is asked of the owner; see [Approvals](#approvals). `allowed_tools`
are rules that are approved in advance and never asked about; keep them
narrow. `add_dirs` are the directories it may touch besides the one it starts
in; the default is the whole home directory. To go back to "refuse whatever is
not allowed, ask nobody", set `permission_mode` to `dontAsk`. None of these
affect the one call in which Xiaoyou herself takes a sentence: that call never
has tools.

### Using another provider's model (for example DeepSeek V4)

A `claude_code` agent accepts `env`: extra environment variables set when Claude
Code is started. Point the endpoint and key at a service that speaks the Anthropic
API and the shell is still Claude Code (tools, sessions and the reply format are
unchanged) while the answering model is that provider's. The example configuration
carries a disabled `deepseek` agent:

```json
"deepseek": {
  "type": "claude_code",
  "config_dir": "~/.claude-xiaoyou-deepseek",
  "model": "deepseek-v4-pro",
  "env": {
    "ANTHROPIC_BASE_URL": "https://api.deepseek.com/anthropic"
  },
  "env_files": {
    "ANTHROPIC_AUTH_TOKEN": "~/.config/xiaoyou/deepseek.key"
  }
}
```

`env_files` sets a variable from the contents of a file (surrounding whitespace
removed), so the key stays out of `config.json`. The file is read each time Claude
Code is started, so a new key takes effect without a restart. `--check` reports a
missing or empty file, and a file that other users can read (`chmod 600` it). One way
to write the key without it reaching shell history:

```bash
mkdir -p ~/.config/xiaoyou && chmod 700 ~/.config/xiaoyou
read -rs "key?DeepSeek key: " && printf '%s\n' "$key" > ~/.config/xiaoyou/deepseek.key; unset key
chmod 600 ~/.config/xiaoyou/deepseek.key
```

(That is zsh; in bash write `read -rsp "DeepSeek key: " key`.)

Set `"enabled"` to `true`, put the key in place, and point `xiaoyou.default_agent` (and
`voice_agent`, if set) at it: Xiaoyou herself is then answered by that model. The
original `claude` stays as a helper, and switching back is pointing those two keys
at `claude` again. Give it its own `config_dir` so its sessions stay apart from the
subscription login; that directory needs no login. Writing the key directly into
`env` still works, but then it sits in `config.json`.

Not verified: this follows DeepSeek's API documentation and the requests Claude
Code actually sends, but no turn has been run with a real key. Claude Code's web
search is a server-side Anthropic tool and may not work on another service.

### DeepSeek Harness (dsh)

The `dsh` type drives DeepSeek's own command line, `dsh`, without the Claude Code
shell:

- Background jobs use `dsh --profile acp` (the Agent Client Protocol, JSON-RPC
  over standard input and output): progress lines, silence watching, and
  interruption all work.
- The one call in which Xiaoyou herself takes a sentence streams by default
  (`stream`, default `true`): dsh only hands over a reply once a step is
  committed, so this call goes straight to DeepSeek's Messages endpoint (the
  same one dsh uses: `DEEPSEEK_BASE_URL` or `https://api.deepseek.com/anthropic`,
  with `DEEPSEEK_API_KEY` from `env` / `env_files`) with `stream: true`. The
  reply so far appears as `partial` on `GET /v1/messages/<id>`, so the phone
  shows it while it is written. These turns keep their own short history in
  `<config_dir>/xiaoyou-stream-sessions/` (ids start with `api-`). If the stream
  fails, or with `stream: false`, the call falls back to
  `dsh --profile headless --json`: one question, one answer, with tools and
  thinking switched off for speed.
- Optional `chat_model` selects the model for plain conversation, routing, and
  result summaries; background tool jobs keep `model`, and an explicitly selected
  per-call model takes precedence. Without it behavior is unchanged. For example,
  `model: "deepseek-v4-pro"` and `chat_model: "deepseek-flash"` retain the persona,
  conversation history, task dispatch, and approval logic while separating models.
- Keepalive comments, empty lines, and invalid events count toward the same total
  deadline. A headless fallback only gets the remaining budget; an exhausted
  budget does not start another wait. Logs record model, first-text and total
  timing without recording the prompt, response, or credentials.
- There is no per-step approval channel like Claude Code's. Permissions follow
  `sandbox` only (`read-only`, `workspace-write`, `danger-full-access`, passed as
  `DSH_PERMISSION_MODE`): inside the sandbox it acts on its own; when it wants
  to go outside, it asks, and that question becomes the usual approval prompt
  on the device and phone. So `permission_mode`, `allowed_tools` and `add_dirs`
  do not exist for it and are rejected.
- Its effort levels are `off`, `low`, `high` and `max`. Without `efforts` the
  list is `[]`: nothing is passed and dsh's own default applies.
- `config_dir` becomes `DSH_HOME`, so it keeps its own profile and sessions and
  leaves your usual `~/.dsh` alone.
- Usage records only which model it used: it reports context use, not billed
  tokens.

Not tested and not tried for real: the protocol follows dsh 0.2.0-rc.2, and a
handshake with an unexpected version or method name says so plainly.

### Project brief

`xiaoyou_runtime/project_brief.py` is a small script to use as a `command`
helper for "what has been happening in this project": it reads this computer's
`git log` / `git status` and the recent sessions Claude Code
(`~/.claude/projects/<repo>/`) and Codex (`~/.codex/sessions/`, filtered by
working directory) keep locally, prints a summary, and Xiaoyou relays it. It
uses only the standard library and runs on its own as well:

```json
"brief": {
  "type": "command",
  "command": ["python3", "/Users/you/ai-passport/runtime/xiaoyou_runtime/project_brief.py", "~/ai-passport"],
  "description": "What changed in this project lately, and what was discussed in Claude Code and Codex"
}
```

Give the script an absolute path (the command runs in the helper's `workdir`); the repository path may use `~`. It reads local caches only: sessions on other computers and plain claude.ai
chats are out of its reach.

The example configuration ships a disabled `codex` agent; set `"enabled": true`
after installing and logging in to the Codex command line on the same machine,
then run `--check` again.

A local model is added later as a `command` agent with `"speaks": true`: it
receives the persona together with the message, and may answer in plain text or
with a JSON object `{"reply", "brief", "mood"}`.

### Who gets a message

The order is fixed:

1. The caller named an agent (`agent` in the interface): that one.
2. The owner named one at the start of the sentence: that one. Two forms are
   recognized: `@codex look at this`, and a Chinese "let / ask / use / hand to"
   word followed by a name or alias (the words are `ASK_WORDS` in
   [`xiaoyou_runtime/router.py`](xiaoyou_runtime/router.py); the Chinese version
   of this page lists them).
3. The router has an opinion: its choice.
4. Otherwise: the default agent.

Right after the name the owner may say which effort this one job runs at
(since 0.6.0): `@codex high look at this error`. The words are `low`,
`medium`, `high`, `xhigh`, `max`, `minimal` and their Chinese counterparts
(`EFFORT_WORDS` in [`xiaoyou_runtime/router.py`](xiaoyou_runtime/router.py);
the Chinese version of this page lists them, with the spoken form that puts a
"use" word in between). Only a word directly after the name counts, and it is
removed from what is handed to the helper. A level the helper may not use
falls back to its usual one. See [Tiers](#tiers-model-and-effort).

The first three rules all mean "it was decided who does this": the runtime
does not ask a model. It opens a card, Xiaoyou says one sentence to the effect
of "handed to codex", and that agent works in the background. The message
record gets a `handoff` event and `agent` is that agent. This includes the
agent that speaks as Xiaoyou: `@claude …` makes `claude` do the thing in the
background, with its tools and as Xiaoyou, and it is told that it was the one
named. When the default agent only does work, rule 4 is handled the same way.

The router is the replaceable part. `mention` adds nothing beyond rules 1, 2
and 4. `command` runs a command of yours: standard input is
`{"text", "default", "agents": [{"name", "type", "description"}]}` and the first
line of standard output is the chosen agent's name. Empty output, an unknown
name, an error or a timeout all fall back to the default agent: a broken router
must not leave the owner unable to talk. A small model or a classifier that
decides routing plugs in here.

### One thing, one card

A sentence that nobody was named for is taken by the agent that speaks as
Xiaoyou. For this step it gets **no tools** and at most
`voice_timeout_seconds`: it only has to understand. The runtime tells it which
helpers exist (its own agent included), which things there are at the moment
(number, title, who is on it, for how long), and which thing was on the
owner's screen when he spoke. Besides what to say, its reply states which card
the sentence belongs to (a new one, or an existing number) and what the
runtime is to do:

| Action | Meaning |
| --- | --- |
| `none` | She has answered directly. |
| `start` | Hand it to a helper in the background. She names the helper, a short title for the thing, and the task for the helper. |
| `amend` | The owner has more to say about a thing. `after`: when the current round is done, carry on with the addition. `redo`: the request changed. A helper that can be told mid-way is told; otherwise the round is stopped and started over in the same session with the new request. If the thing had already ended, another round runs in its old session. |
| `cancel` | Stop a thing that is in progress and drop its result. |

What cannot be done (no such number, no such helper, nothing left to cancel)
is put to her with the reason, and she answers once more; if it still cannot
be done the owner is told so as it is, and she does not get to present
something as done that was not.

Each thing in the background has a thread of its own and none waits for
another. One helper can be on several things at once, because a helper's
session is kept per thing and helper (`conversation/card` in
`state/sessions.json`). A helper that can speak as Xiaoyou works with the
persona, and its result is what Xiaoyou says. A helper that only does work
receives the task only, without the persona or the chat; its raw result comes
back to the conversation's own line and `voice_agent` reports it. If
`voice_agent` is unavailable at that moment, the owner gets the raw result
rather than nothing. When a helper fails, the card says so, with the reason.

A card is in one of the states `working` (a helper is on it), `waiting` (for
the owner's approval; see [Approvals](#approvals)),
`done`, `failed`, `cancelled`; `talking` is reserved. Cards are stored in
`state/cards.json`, the latest 50; the 15 most recent things that were handed
to a helper and have ended are always kept, whatever cards come after them
(since 0.6.1: the device's task history on its third screen relies on it).
Such a thing gets a `finished_at` when it ends; taking it up again clears
that until it ends again. Things that were in progress when the
runtime restarts cannot be continued; they are marked `failed` with a note
saying the runtime was restarted.

A card whose work went to a helper has three more fields (since 0.6.0):
`effort` is the effort the thing runs at, `model` the model actually used, and
`cost` what it has spent so far. The next two sections explain them.

### Task images and downloadable files

Task outputs are registered before the phone can display or download them. For
Runtime-managed Claude Code, Codex, and DSH tasks, the job includes its dedicated
`state_dir/artifact-inbox/<card-id>/` directory. Save finished files there and
write a relative-path manifest:

```json
{
  "artifacts": [
    {"path": "preview.png", "name": "Preview.png"},
    {"path": "design.pdf", "name": "Design.pdf"}
  ]
}
```

Use `manifest.json` in that directory. Do not list credentials, personal data, or
raw logs. Automatic import stays inside this task's inbox; it does not scan the
computer for paths mentioned in a reply. An authenticated explicit registration
request may also read the card's agent workspace, while hidden/private paths,
credential/configuration files, other Runtime state, symlinks, and directory
traversal are rejected. Each registration accepts at most 16 files.

The Runtime copies each file into `state_dir/artifacts/`, computes its SHA-256,
and assigns a stable attachment ID. Downloads use that ID, never a computer path.
Snapshots remain available after the source is changed or removed, and after a
Runtime restart. The limit is 64 MB per file and 1 GB of stored snapshots; a failed
import produces an attachment with `status: "error"` and a bounded explanation.
Removing a card does not automatically reclaim its snapshots.

Metadata appears in the card's aggregate `attachments` list and the final reply's
`entries[].attachments`, with `id`, `name`, `mime`, `size`, `kind` (`image` or
`file`), `width`, `height`, `sha256`, `source`, `created_at`, `status`, `error`, and
the boolean `thumbnail`. Adding attachments advances the card's `seq`. A helper
reply longer than the 8,000-character display limit is also saved as a UTF-8 text
attachment before display cleanup; the visible reply explains the truncation.
JSON `reply` wrappers are unpacked before display or saving the full text.

Pillow, when available to the Runtime's Python, creates a preview of at most
480 by 480 pixels. Without Pillow, a supported image no larger than 2 MB and one
megapixel can use its original bytes as the preview; larger images expose their
original content with `thumbnail: false`. Missing previews return `404`, letting
the client fall back to the original. Both endpoints require the Runtime token
and stream files in bounded chunks.

The companion App displays registered images and file cards and can download,
save, or open files through Android's system facilities. Cache ownership includes
the originating Runtime, attachment ID, and hash. This first version does not
upload phone attachments, fetch arbitrary external URLs, render PDF documents
inside the App, or transfer general files over Bluetooth. Desktop transcript
observation by itself does not import desktop attachments.

### Tiers: model and effort

Since 0.6.0 every `claude_code` and `codex` helper has a usual effort, and one
job can run at another:

| Key | Default | Meaning |
| --- | --- | --- |
| `effort` | `medium` | The effort this helper usually works at. |
| `efforts` | `["low", "medium", "high"]` | The levels a job may choose from. Claude Code knows `low`, `medium`, `high`, `xhigh`, `max`; Codex knows `minimal`, `low`, `medium`, `high`, `xhigh`. Add higher ones yourself. `[]` means the runtime passes no effort and the tool's own default applies. |
| `models` | `[]` | Models a job may switch to, besides `model`. Empty means no switching per job. |

A `claude_code` helper pointed at another service through `env` (DeepSeek, for
example) is the exception: without `efforts` in its configuration it gets
`[]`, because that service may not accept an effort. Write the levels once you
know it does. `command`, `remote` and `echo` have none of the three keys.
While `efforts` is not empty, `extra_args` must not carry `--effort` or
`model_reasoning_effort`.

**Upgrading to 0.6.0 makes these two kinds of helper work at medium effort by
default**, no longer at the tool's own default (which depends on the tool's
configuration and version, and is often higher). The same job may be done less
deeply and cost less. When that is not enough, tell her to redo the thing at
high effort, or raise the helper's `effort`.

How it reaches the tool: Claude Code gets `--effort <level>` and
`--model <model>`; Codex gets `-c model_reasoning_effort="<level>"` and
`--model` in `exec` mode, and `effort` and `model` in every `turn/start` in
`app_server` mode. The call in which Xiaoyou herself takes a sentence does not
carry the usual effort: it has to be quick.

The effort of a job is decided in this order:

1. The level the owner said right after the name (see
   [Who gets a message](#who-gets-a-message)).
2. Xiaoyou's choice in her reply: the `action` of `start` and `amend` may carry
   `effort` and `model`. Her prompt says: follow the owner when he names a
   level; otherwise use low for a quick look, write nothing for ordinary
   things, and use high only for what is clearly hard, saying so.
3. The caller's choice: `effort` and `model` in the interface.
4. None of these: the level this helper used the last time it worked on this
   thing, otherwise its usual one.

A level or model the helper does not offer counts as not chosen. Changing the
level while changing the request of a thing in progress stops the current
round and does it over at the new level, instead of adding to the turn.

The model a helper really runs on is reported by the helper: Claude Code says
it at the start of its event stream, Codex when the thread is opened. So the
model at work is known even when `model` is empty in the configuration. It is
known after the first run and kept in `state/usage.json`.

### Usage: what is left of each subscription

Since 0.6.0 the runtime keeps, for every subscription login, how much is left
in its two windows: the five-hour one and the weekly one. Three things use it:
Xiaoyou when she hands out work, the phone and the device for display, and the
cost recorded on a card.

Quota belongs to a **login**, not to a helper: helpers that share a
configuration directory share one quota. Only subscription logins count:
`claude_code` helpers that are not pointed at another service through `env`
and carry no key in `env`, and `codex` helpers. Other helpers have no quota;
only their model is recorded.

The numbers come from these places. The runtime reads no credentials itself;
it asks the tools, or reads the numbers they saved:

| Source | When | What it gives |
| --- | --- | --- |
| Claude Code's event stream while it works (`rate_limit_event`) | every job given to it | how much of both windows is used and when they reset; said again whenever a number moves |
| Claude Code's `get_usage` (`claude -p --input-format stream-json` with one control request; no model is called) | when the numbers are old; before a job starts; the `usage` command | the same numbers as `/usage` inside Claude Code |
| Codex's app-server (`account/rateLimits/read`, and `account/rateLimits/updated` while it works) | at the start and end of every turn; when the numbers are old | how much of both windows is used and when they reset |
| `cachedUsageUtilization` in Claude Code's own `.claude.json` | when `get_usage` carries no limits (newer Claude Code builds do this) | the same percentages; ignored once more than a day old |

Claude Code marks `get_usage` as experimental and the two windows in the event
stream as internal. If either stops matching, that login shows as unknown with
the reason in `note`, and nothing else is affected.

The state of a login is `ok`, `warn` (a window has less than 20% left), `out`
(a window is used up) or `unknown` (no number at all). A number is dropped once
its window has reset.

When Xiaoyou takes a sentence, each helper in her list carries a note in
Chinese after its description: the model, the usual effort and the levels to
choose from, then what is left of both windows and when each resets (the
Chinese version of this page shows one).

Her prompt says: when several helpers could do the job, give it to the one
with more left; for one that is nearly used up, go one level down unless the
owner named a level, and say so; do not give work to one that is used up
unless the owner named it; unknown does not mean used up. The paths that
decide who does a thing without asking a model (named in the interface, named
in the sentence, the router) are not affected: the runtime never swaps the
helper or lowers the level on the owner's behalf.

After every round the card's `cost` grows by that round: `in`, `out` and
`cached` are tokens (newly read, written, read from the cache), `d5` and `d7`
are the percentage points the two windows went down. When one login works on
several things at once, `d5` and `d7` cover all of them together and are only
a rough figure; quota percentages are whole numbers, so small jobs often
show 0.

The numbers can be read without starting the service:

```bash
python3 -m xiaoyou_runtime --config config.json usage
```

It asks now and prints what is left of each login, why a login could not be
asked, and each helper's model and effort. `--json` prints the raw data.

### Shared workspace

Xiaoyou and other agents (Claude on claude.ai, Codex, later others) share one
record, kept in Notion as two databases:

- **Bus**: for agents, and the source of truth. One row per entry, content
  verbatim (commands, paths and errors are not paraphrased).
- **Log**: for people. One summary when a thing ends, matched to its bus rows
  by the link column.

On Xiaoyou's side the runtime's code writes every row; nothing depends on a
model remembering to.

| When | Where | What |
| --- | --- | --- |
| Work is handed to an agent (a new thing, a named agent, another round on a finished thing) | Bus | Type task, written by `xiaoyou`, addressed to and taken by that agent, status taken, body is the exact text given to it |
| An addition or a changed requirement for work in progress | Bus | Type note; the first body line says what was actually done (steered midway, stopped and redone, queued for after) |
| An agent returns a result, or fails | Bus | Type result, written by that agent, status done or failed, body is its raw result or the reason |
| A cancellation | Bus, log | A bus note with status cancelled; one log row |
| A thing ends | Log | Title, who did it, outcome, conclusion (the small-screen brief), body is what Xiaoyou finally said; when she needs a decision from you, that sentence is also in the your-action column |

The link column is `runtime name/card id` (for example `mac/c12`). The one call
where Xiaoyou herself takes a sentence (the call without tools) is not a
handoff and is not written.

Writing to Notion goes over the network, so rows go through a queue and a
separate thread; neither Xiaoyou's replies nor background work wait for it.
Rate limits and server errors are retried three times. Rows that still cannot
be written (no network, a revoked token, a database not shared with the
integration) are appended to `state/workspace-unsent.jsonl` and reported on
standard error. The token is only sent in the request header and is never
written into a row. Task text and results are written verbatim: a secret
pasted into a task is written too, so keep secrets out of tasks.

Setup: create an internal integration in Notion and copy its token; share the
page that holds the two databases with that integration. The column and
option names in Notion are Chinese and must match exactly; they are listed in
[README.zh_CN.md](README.zh_CN.md) and in `xiaoyou_runtime/workspace.py`.

Other agents read and write the same record through Notion directly (Notion
MCP where they support MCP); they do not go through the runtime.

### Approvals

A thing given to Claude Code runs with `--permission-mode manual` and with the
runtime's own permission tool attached
(`xiaoyou_runtime/permission_mcp.py`, an MCP server with a single tool). Where
Claude Code would ask "may I?", it calls that tool instead; the tool hands the
operation to the runtime, the card of the thing becomes `waiting`, an entry
appears under `approvals` in `/v1/feed`, and it waits. When the owner answers
`allow` the step is carried out; with `deny` it is not, Claude Code is told
that the owner said no, and decides how to go on. If the thing is cancelled,
its request changes or it times out, approvals not yet answered lapse.

What the owner is shown comes from the tool call as it is, without passing
through a model: `tool` is "helper · tool name" (for example `claude · Bash`)
and `detail` is the command itself; for writing a file it is the path and the
content, for editing one the path with what is replaced and by what. Lines of
progress are made the same way: the tool name and its one most telling
argument.

The permission tool is a separate process. It does not use the interface the
phone connects to; it connects to an entrance the runtime opens on `127.0.0.1`
only, on a random port, with a key that is valid for this one thing and this
one round. The key is written to a temporary file under `state/` that only the
owner of the process can read, and the file is removed when the round ends.
The runtime's token is never written to any file.

With `--once` on the command line no phone or device is present: an approval
is asked in the terminal (`[y/N]`), and when there is no terminal the answer
is no.

A thing given to Codex asks in the same way, by a different route: Codex's
app-server sends its questions to the runtime itself (running a command,
changing files, wanting more permissions), so no permission tool is involved.
The default is a read-only sandbox with `approval_policy: "on-request"`: it
may read anywhere, and every change and every command that has to leave the
sandbox is asked about. `tool` is the helper's name with a short Chinese label
for a command, a file change or permissions (the labels are in
[`xiaoyou_runtime/codex_app.py`](xiaoyou_runtime/codex_app.py); the Chinese
version of this page lists them), and `detail` is the command
and its directory, each file's path and diff, or the permissions it wants as
they are, followed by the reason Codex itself gave. Anything else it asks
(wanting the owner to type something, for example) is refused. To loosen
this, change `sandbox` (with `workspace-write`, writing inside the working
directory is not asked about) or `approval_policy`; with `mode` set to `exec`
nothing is asked, and what the sandbox forbids simply cannot be done.

Changing a request (`redo`) is, for Codex, adding to the turn in progress:
the new sentence goes straight into it (`turn/steer`) and nothing is started
over. Only when that is not possible (the turn has just begun or just ended)
is the round stopped and run again, as with Claude Code. Cancelling first
interrupts the turn (`turn/interrupt`) and ends the process if it has not
stopped within 5 seconds.

Codex's help marks the app-server as experimental. `--check` shakes hands
with every `codex` agent in this mode (without calling a model); when that
fails it says why, and `mode` can be set to `exec` for the time being.

### Long tasks: watched for silence, not for total time

A thing in the background has no limit on its total time by default (since
0.5.4). A cut by the clock kills a thing that has been working properly for
forty minutes. What the runtime watches is how long it has been silent:

- Every line of Claude Code's event stream and every message from Codex's
  app-server counts as activity.
- Normally, `idle_timeout_seconds` (10 minutes by default) without a single
  line means it may be stuck. While an operation has started and its result
  has not come back (a command that is building or running tests), the event
  stream is quiet anyway, so `idle_timeout_command_seconds` (30 minutes by
  default) applies instead.
- Time spent waiting for the owner's yes does not count, and it does not count
  towards the total time either (if one is set): an owner who answers late
  does not lose the thing.
- When the time is up the thing is not stopped; the owner is asked first. Its
  card becomes `waiting` and an entry appears under `approvals` in `/v1/feed`
  with `kind` `stall`, a `tool` that names the helper and says it has gone silent, and a `detail` that says how
  long it has been silent and quotes its last step. It uses the same popup as
  an approval, so the device and the phone need no change: **yes means keep
  waiting, no means stop**.
- Yes: it goes on, and is asked about again only after twice as long. If it
  moves again by itself while the question is open, the question is taken
  back as if it had not been asked.
- No, or nobody answering for `idle_answer_seconds` (30 minutes by default):
  it is stopped, the card becomes `failed`, and the card says why.
- Stopping loses nothing: the helper's session was recorded at the start, so
  saying "go on" to that thing continues in the same session.

For a hard limit, give `turn_timeout_seconds` or an agent's `timeout_seconds`
a number. The `command` type and `codex` in `exec` mode have no event stream,
so nothing can be seen of them; they are still limited by total time only
(3600 seconds when set to no limit). The one call in which Xiaoyou herself
takes a sentence is not affected: that is still `voice_timeout_seconds`.

### Using a separate Claude login

Claude Code reads its login and settings from `~/.claude`. If that directory is
set up for something else (for example a company gateway through
`ANTHROPIC_BASE_URL` or an API key), give Xiaoyou its own directory instead of
changing it:

```bash
mkdir ~/xiaoyou-login && cd ~/xiaoyou-login      # any folder except your home folder
CLAUDE_CONFIG_DIR=~/.claude-xiaoyou claude       # log in with the subscription, check /status, quit
```

Then set `"config_dir": "~/.claude-xiaoyou"` on that `claude_code` agent. Do the login
from a folder other than your home folder: Claude Code also loads
`<current folder>/.claude/settings.json` as project settings, and in the home
folder that is the very file you are trying to avoid.

Codex is the same with `~/.codex`: a `config.toml` there that points at a
gateway decides where every request goes, whatever account is logged in. Give
Xiaoyou's Codex its own directory (`CODEX_HOME`); Codex does not create it:

```bash
mkdir -p ~/.codex-xiaoyou
CODEX_HOME=~/.codex-xiaoyou codex login
CODEX_HOME=~/.codex-xiaoyou codex login status
```

Then set `"config_dir": "~/.codex-xiaoyou"` on that `codex` agent.


## HTTP interface

Message snapshots optionally include Unix timestamps `processing_started_at` and `first_partial_at` to distinguish queue delay from the wait for visible text.

All requests except `/healthz` need `Authorization: Bearer <token>`. Bodies and
responses are JSON except the documented voice, firmware, and artifact binaries.

| Request | Result |
| --- | --- |
| `GET /healthz` | `{"ok": true, "version", "backend", "name"}`; no token needed. `backend` is the default agent's type. |
| `GET /v1/agents` | `{"default", "agents": [{"name", "type", "description", "speaks", "default", "model", "effort", "efforts", "models"}]}`: the agents Xiaoyou can use on this runtime. The last four came with 0.6.0: the model actually used (the configured one before the first run, `null` when neither is known), the usual effort, and the levels and models a job may choose. |
| `GET /v1/usage?refresh=1` | `{"rev", "at", "accounts": [...], "agents": [...]}` (since 0.6.0). `accounts` has one entry per subscription login: `{"id", "kind", "agents", "state", "windows": [{"kind": "5h" or "7d", "left", "resets_at"}], "plan", "source", "updated_at", "note"}`; `left` is the percentage that remains, `null` when unknown. `agents` has one entry per helper: `{"name", "type", "model", "effort", "efforts", "models", "account", "running", "now"}`; `running` is how many rounds it is working on and `now` the model of the one in progress. Without `refresh` the numbers at hand are returned (and asked for again in the background when they are old); with it they are asked for now, which takes a few seconds. See [Usage](#usage-what-is-left-of-each-subscription). |
| `POST /v1/messages` with `{"text", "conversation"?, "client_id"?, "agent"?, "card"?, "pin"?, "effort"?, "model"?}` | `202` and the message record, status `queued`. `effort` and `model` (since 0.6.0) are the effort and model chosen for this sentence, used when it goes to a helper; with `agent` they must be ones that helper offers, otherwise `400`. `agent` sends the message to that agent; `400` if there is no such agent. `card` is the number of the thing on the owner's screen when he said it. `pin: true` (since 0.5.3) means he opened that thing and said it from inside: the sentence is always filed on it, and any work continues that thing's own session instead of opening another card, also when the sentence names a helper (unless another helper is working on that thing right now). `client_id` deduplicates retries (since 0.7.0 per conversation, and it survives a restart): the same `client_id` in the same conversation returns the original message when the content matches and `409` when it differs. The message is written to disk before `202` is returned; see [Messages on disk and restarts](#messages-on-disk-and-restarts). |
| `POST /v1/voice?conversation=<name>&client_id=<id>&agent=<agent>&card=<number>&pin=1&effort=<level>&model=<model>` with a WAV file as the body | `202` and the message record, `kind` `voice`, empty `text`. `400` if the recording is not acceptable or no engine is configured. |
| `GET /v1/messages/<id>?wait=<seconds>&rev=<n>` | The message record. With `wait` (up to 60) the call returns as soon as Xiaoyou has dealt with the sentence. With `rev` as well (the `rev` of the record the caller already has) it returns as soon as anything in the record changes. While the reply is still being written, `partial` holds the whole reply so far (replace, do not append; an empty string takes it back); it is empty once the message is done. Since 0.7.0 the record survives a runtime restart (the latest 200 are kept). |
| `GET /v1/messages/<id>/stream?rev=<n>` | Authenticated `text/event-stream` snapshots on one connection. Each event has the same message ID and full `partial`/`rev` record; terminal states end the stream and comments provide keepalives. Message records advertise `event_stream: true`. After a stream failure, poll the same ID without resubmitting the job; older runtimes keep using long polling. |
| `GET /v1/feed?conversation=<name>&after=<seq>&wait=<seconds>&usage=<rev>` | `{"seq", "cards": [...], "approvals": [...], "usage": {...}}`: changed cards and pending approvals for the conversation, plus desktop monitoring cards and desktop approvals across conversations. Approvals have `id`, `card`, `conversation`, `agent`, `tool`, `detail`, `created_at`, and `kind` (`tool` or `stall`); desktop ones also identify their source and whether they are remotely actionable. Changes to an approval touch its card. `wait` is at most 60 seconds; a changed card, or changed usage when `usage=<rev>` is supplied, returns early. Send the returned `seq` as `after` next time; a lower returned sequence means records were replaced and the client must resync from 0. Since 0.7.0, `messages=1` adds `messages`: the message records of this conversation with a sequence above `after` (the same sequence as cards, including queued messages and replies still being written), so another phone sees what was just said; without it the response is unchanged. `epoch` identifies the record set: when it differs from the one last seen, the records were replaced and the client must resync from 0. |
| `GET /v1/info` | `{"runtime_id", "epoch", "protocol", "capabilities", "name", "version"}` (since 0.8.0): the runtime's fixed id, the record set id, the sync protocol version and capabilities. See the [unified message model](../docs/development/unified-message-model.md). |
| `GET /v1/sync?conversation=<name>&after=<seq>&epoch=<id>&wait=<seconds>&usage=<rev>` | Unified sync (since 0.8.0): `{"protocol", "runtime_id", "epoch", "seq", "reset", "more", "messages", "tasks", "approvals", "removed", "usage"}`. Since 0.8.1 it carries `usage` (as `/v1/feed` does) and returns early when `usage=<rev>` is stale; the capabilities include `sync_usage`. Since 0.8.2 a desktop session that finished its turn has `state` `turn_ended` here (`/v1/feed` and the card still say `done`). `messages` are the messages with a sequence above `after` (one per sentence, ids as in card entries, with `status`, `partial`, `author`, `source`, `rev`, `seq`); `tasks` are cards without `entries` and with `members`. `reset` is `true`, with a snapshot from 0, when the `epoch` differs or the cursor is above the current sequence or older than the log's `floor`. Conformance test: `python3 -m xiaoyou_runtime.conformance --url <address> --token <token>`. |
| `GET /v1/cards?conversation=<name>` | `{"cards": [...]}`: the latest 50 cards, including desktop monitoring cards when synchronization is enabled. |
| `POST /v1/cards/<number>/artifacts` with `{"artifacts": [{"path", "name"?}], "entry_index"?}` | The updated card with registered attachments. `entry_index` chooses an existing entry (zero-based); omitted means the latest assistant entry. Malformed input returns `400`, a missing card `404`; individual file failures remain visible as `status: "error"` attachments. |
| `GET /v1/artifacts/<id>/content` | The registered immutable file snapshot, streamed as binary with its MIME type and attachment filename. `404` if the ID or snapshot is unavailable. |
| `GET /v1/artifacts/<id>/thumbnail` | A binary image preview; `404` when no preview is available. Use `/content` as the fallback. |
| `POST /v1/cards/<number>/cancel` | Cancels that thing and returns the card; a thing that is no longer in progress is returned unchanged. `404` if there is no such card; `400` for a desktop monitoring card, which must be stopped on the desktop. |
| `POST /v1/approvals/<number>` with `{"decision": "allow" or "deny"}` | Answers an approval, including a live desktop request with `remote_actionable: true`. `404` if there is no such approval (or the runtime was restarted); `409` for an already answered, notification-only, expired, or otherwise stale request. |
| `POST /v1/conversations/<name>/history` with `{"turns": [{"id", "text", "reply", "at"?}]}` | `{"accepted": n}`: how many of the turns (at most 30) this runtime did not know. They are told to the agent that takes the next message. |
| `POST /v1/conversations/<name>/reset` | Starts that conversation over: every agent's session is forgotten and the transcript is cleared. |
| `GET /v1/firmware` | `{"rev", "versions": […], "target", "device", "log"}`: the firmware library, newest version first. See [Device firmware](#device-firmware). |
| `GET /v1/firmware/target?wait=<seconds>&rev=<n>` | The version the device should run, as one flat object: `id` (empty when there is none), `seq`, `build`, `size`, `sha256`, `note`, `reason`, `attempts`, `notice`, `notice_at`, `rev`. With `rev`, returns as soon as the library changes. |
| `GET /v1/firmware/<id>/image` | That version's application image (binary). |
| `GET /v1/app` | The phone app update published on this computer: `available`, `version_code`, `version_name`, `sha256`, `size`, `published_at`, `note`. |
| `GET /v1/app/apk` | That update's APK. |
| `GET /v1/app/packs` | Resource packs (since 0.8.3): `{"packs": [{"name", "published_at", "files": [{"path", "sha256", "size", "download_url"}]}]}`. The phone compares with what it has and downloads only files whose checksum changed. |
| `GET /v1/app/file?sha256=<hash>` | One resource pack file (since 0.8.3); the ETag is its checksum and a single Range resumes; 409 when no current pack lists it. |
| `POST /v1/firmware?note=<note>&push=1` with a firmware file as the body | `201` and the version's record; `push=1` also makes it the version to push. |
| `POST /v1/firmware/target` with `{"id": version or null, "reason"?, "force"?}` | Sets (or clears) the version the device should run; answers like `GET /v1/firmware/target`. |
| `POST /v1/firmware/device` with `{"event", "build", "state", "prev", …}` | What the phone app says about the device. `event` is `connected`, `progress`, `installed`, `failed` or `unsupported`; answers like `GET /v1/firmware/target`. |

With [desktop synchronization](#sync-existing-desktop-conversations) enabled,
`/v1/cards` and `/v1/feed` also include desktop monitoring cards across the
requested conversation. They carry `source: "desktop"`, `monitoring_only: true`,
`project`, `session_id`, `desktop_status`, `observed_at`, and `stale`, plus
`content_scope: "recent"` and `content_truncated`. Their bounded `entries` hold
visible human requests (`role: "you"`) and originating assistant output
(`role: "xiaoyou"`, labeled Codex or Claude in the App). Empty `entries` mean no
visible content has been collected. A desktop `waiting` state alone is only a
status signal. The optional permission bridge can supply a separate approval
whose `remote_actionable` field determines whether it can be answered remotely;
a stopped session can be continued with `POST /v1/messages` carrying `card` (this
observation card) and `pin: true` (see above); cancellation remains in the desktop
conversation.

### Messages on disk and restarts

Since 0.7.0 every accepted sentence is written to `state/messages.json` before
`202` is returned, and again whenever its status changes (started, finished,
failed). The reply that is still being written changes too fast and is not
written. After a runtime restart:

- typed messages that were still queued and never started are processed;
- messages already being processed, and voice recordings not yet transcribed,
  may have done part of their work, so they are not redone: they are marked
  `failed`, and the conversation gets a card with the original sentence and a
  note that the runtime restarted before it was finished, so it can be said
  again;
- a message whose reply already reached its card, but whose record was not yet
  updated, is recorded as `done` instead of being reported as unfinished;
- the sequence never goes back (a block of numbers is reserved on disk and a
  restart continues after it, skipping some numbers), so a client's `after`
  stays valid;
- asking for the old message id, or retrying with the old `client_id`, still
  finds that message.

If the message cannot be written (for example a full disk) the answer is
`503` and the message is not accepted; send it again with the same
`client_id`. Writes are flushed to disk before the rename, so a power loss
leaves either the old or the new file, never half of one.

Deduplication outlives the messages themselves (the latest 2000 `client_id`s):
a retry after the message has been trimmed returns the same id as a minimal
record with `expired: true` and is not run again. A broken `messages.json` does
not stop the runtime; the broken file is kept as `messages.json.broken`.

A message record says whether Xiaoyou has dealt with the sentence, not whether
the thing is finished: once she has answered or handed the work out, `status`
is `done`, and the progress and the result are on the card, to be waited for
with `/v1/feed`. Only a sentence passed on by another runtime (a `remote`
agent) waits until the thing has ended. A client that only reads message
records (phone app 0.5.0) can therefore still send and receive with this
version, but sees the "handed to codex" sentence and never the result from
the background.

A message record has `id`, `client_id`, `conversation`, `kind` (`text` or
`voice`), `status` (`queued`, `transcribing`, `running`, `done`, `failed`),
`text`, `reply`, `brief`, `mood` (`idle`, `busy`, `ask`, `happy`, `oops`),
`error`, `created_at`, and `finished_at`. For a voice message `text` is empty
until the recording has been transcribed.

The record also says who the sentence went to:

- `asked`: the agent the caller named, or `null`.
- `card`: the card the sentence was put on. Until it has been dealt with, this
  is the number the caller sent.
- `agent`: the agent that took the sentence, or the helper it was handed to.
- `stage`, `helper`: fields left from 0.4. They may hold a value for the few
  seconds Xiaoyou is dealing with the sentence and are `null` afterwards.
- `rev`: a counter that goes up every time the record changes.
- `events`: the steps taken, each `{"at", "kind", "agent", "text"}`. `kind` is
  `route` (who got it; `text` is the reason: `asked`, `mention`, `router`,
  `default`) or `handoff` (handed to a helper; `text` is what Xiaoyou said).

A card has `id` (`c1`, `c2`, …), `conversation`, `title` (the canonical name:
at most 200 characters, or 24 when derived from a request), `title_source`
(`upstream`, `task`, `request`, or `fallback`), `title_truncated`, `state`,
`agent` (who is or was on it; `null` when Xiaoyou
answered herself), `entries` (`[{"role": "you" or "xiaoyou", "text", "at", "attachments"?}]`,
the latest 40), `brief`, `mood`, `progress` (the latest 5 lines of progress,
starting over with each round), `started_at`, `edits` (how many times it was
added to or changed), `approval` (the number of the approval it is waiting
for, or `null`), `queued`
(waiting its turn under a limit on parallel things), `created_at`,
`updated_at`, `finished_at` (when a thing handed to a helper ended; `null`
while it is in progress and for what Xiaoyou answered herself), and `seq`.
The top-level `attachments` list aggregates the task's registered outputs; see
[Task images and downloadable files](#task-images-and-downloadable-files).

Sending the same `client_id` again returns the existing record instead of
dealing with the sentence twice, so a client may retry safely after a lost
response.

```bash
TOKEN=...   # the value of server.token
curl -s -X POST http://127.0.0.1:8765/v1/messages \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"text": "hello", "client_id": "demo-1"}'
curl -s "http://127.0.0.1:8765/v1/messages/<id>?wait=60" -H "Authorization: Bearer $TOKEN"
curl -s "http://127.0.0.1:8765/v1/feed?after=0&wait=60" -H "Authorization: Bearer $TOKEN"
```

## Device firmware

The device's firmware can be
[replaced over Bluetooth](../docs/claude-pocket.md#replacing-the-firmware-over-bluetooth).
The computer that runs the runtime keeps every version and records which one
the device should run. What writes firmware to the device is the phone app:
while it is connected to the device and can reach this runtime, it notices that
the two differ, fetches the image and sends it over Bluetooth; after the device
restarts it confirms the new firmware and reports the outcome back.

Each version's application image is stored as it is in
`<state_dir>/firmware/images/`, with the list in
`<state_dir>/firmware/index.json`. **Nothing is removed automatically** (a
version is about 1.7 MB). Adding a version checks the image's checksum and
reads its version string, build time and `build` (the first 16 hex digits of
the firmware ELF's SHA-256, which is also what the device reports). Every
version gets a number in the order it arrived, so "version 7" is enough to name
one.

```bash
python3 -m xiaoyou_runtime firmware list              # the versions; ● on the device, → waiting to be pushed
python3 -m xiaoyou_runtime firmware status            # what the device runs, what is pending, recent history
python3 -m xiaoyou_runtime firmware fetch --push      # take the newest build from GitHub, store it, push it
python3 -m xiaoyou_runtime firmware add FILE.bin      # store a firmware file (application or merged image)
python3 -m xiaoyou_runtime firmware restore 5         # put the device back on version 5
python3 -m xiaoyou_runtime firmware restore previous  # back to the version in the device's other slot (seconds, no transfer)
python3 -m xiaoyou_runtime firmware cancel            # do not push after all
python3 -m xiaoyou_runtime firmware remove 3          # delete version 3 from the library
```

A version is named by its number (`7` or `#7`), the first digits of its id,
`latest`, `current` (what the device runs) or `previous`. `push` and `restore`
are the same thing. These commands and the running service share one list, so
the service does not need a restart.

`fetch` takes firmware from the releases of `firmware.repo`: the newest without
arguments, a given tag, or with `--commit <commit>` only one built from that
commit (`HEAD` means the commit `firmware.source_dir` is on). `--wait 900`
waits up to 900 seconds for a build that is not there yet, and stops at once if
the build of that commit has already failed. `--detach` does the waiting and
fetching in the background and returns immediately; the log is
`<state_dir>/firmware/fetch.log`.

The builds are found with `git ls-remote` (each build is a tag on the commit it
was built from) and downloaded directly, so waiting for one does not use up
GitHub's hourly allowance for API queries. Only the question "has this build
already failed?" goes to the API, at most every 90 seconds. On a computer
without `git` everything goes through the API; without a login that is 60
queries an hour, and the `GITHUB_TOKEN` environment variable raises it.

On a computer with ESP-IDF, `firmware.build_command` can be set (for example
`["bash", "-lc", "source ~/esp/esp-idf/export.sh && idf.py build"]`), and
`firmware build --push` then builds locally and pushes without waiting for
GitHub.

Safeguards:

- A version that **cannot itself be replaced over Bluetooth** once installed
  (firmware from before this feature) is not pushed without `--force`.
- A version that fails to install twice in a row is not pushed again; `status`
  says why.
- A fetch that did not work (a failed build, a timeout) and a failed install
  are recorded in the history, and the "device firmware" line of the phone app
  shows them.
- Images are not signed. Whoever can use this runtime's interface (that is,
  knows the token) can have the phone push any image to the device.

### Changing the screen by saying so

Enable the agent called `tailor` in `config.example.json` and fill in
`firmware.repo` and `firmware.source_dir` (`..`, this repository). It is a
`claude_code` agent that only does work, with the firmware repository as its
working directory, and it follows
[`skills/pocket-screen-update`](../skills/pocket-screen-update/SKILL.md): change
the interface code, run the checks, commit locally, then build on this computer
and push the result to the device (when ESP-IDF is installed with
`tools/install_idf.sh` and `firmware.build_command` is set; GitHub is not needed
then), or else push to GitHub and have the runtime wait for the build there. Say "@tailor make the strip at the
top thinner" to the device (the example also gives it a Chinese alias), or
just say what should change and let Xiaoyou decide to hand it over. "Go back to the previous one" is its job as well.

What has to be in place first: the repository on this computer can `git push`;
`tailor` has `Bash` in `allowed_tools`, which lets it run commands in this
repository. That is what the job needs, but enable it only on your own
computer. From the spoken sentence to the device starting the update takes the
time Claude Code needs for the change (a minute or two) plus GitHub's build
(several minutes); the conversation waits for the change, and the build and the
push happen in the background.

## Windows

The runtime runs on Windows 10 or later with Python from
[python.org](https://www.python.org/downloads/windows/) (tick "Add python.exe
to PATH" in the installer) and Claude Code installed natively. In PowerShell:

```powershell
irm https://claude.ai/install.ps1 | iex        # Claude Code; then open a new window
claude                                         # log in once, check /status, quit
git clone -b feature/pocket-hub-app https://github.com/yanyuliu01/ai-passport.git
cd ai-passport\runtime
copy config.example.json config.json
python -c "import secrets; print(secrets.token_urlsafe(32))"   # paste into server.token
python -m xiaoyou_runtime --config config.json --once "hello"
$env:XIAOYOU_HOST = "0.0.0.0"; python -m xiaoyou_runtime --config config.json
```

Use `python`, not `python3`. The first time the service listens on the network,
Windows Defender Firewall asks whether to allow Python; allow it on private
networks, or the phone cannot connect. Save `config.json` as UTF-8 without a
byte order mark (Notepad's "UTF-8", not "UTF-8 with BOM").

For voice, the steps in [Voice](#voice) work in PowerShell as written, with
`python` in place of `python3` and `curl.exe` in place of `curl`.

## Several runtimes, one conversation

You can run a runtime on each of your computers and choose in the phone app
which one answers. Claude Code keeps each conversation on the machine that had
it, so the conversation itself cannot move. Instead the phone keeps the recent
turns and hands them to the runtime you switch to:

1. Before each message the app posts its most recent turns (up to 12) to
   `/v1/conversations/<name>/history`.
2. The runtime ignores turns it answered itself or has already been given,
   and keeps the rest.
3. With the next message of that conversation, the model receives those turns
   in front of the owner's words, once, with a note that they happened
   elsewhere.

What carries over is what was said: the owner's words and Xiaoyou's full
replies, each shortened to 500 and 1000 characters when quoted to the model.
What does not carry over is everything else the other session knew: files it
read, tool output, and anything older than the turns the phone still holds.

The companion App can pair with at most eight Runtimes and monitors their
pending approvals independently, even while a different computer is selected
for chat. Each computer uses a separate feed poll with a wait of up to 50 seconds.
The App merges pending requests by creation time and keeps their originating
Runtime bound to the request; a phone or device answer goes back to that computer,
not whichever one is currently selected. Task lists still show the selected
Runtime. Opening a notification shows its approval without changing the chat;
viewing the originating task is an explicit computer switch.

Losing a connection removes that computer's unverified pending approvals and
notifications; reconnecting fetches its current pending requests from sequence 0.
Network failures retry after 2 to 30 seconds. Monitoring requires the phone's
foreground Runtime connection service, and Android 13 or newer requires
notification permission for alerts. Pairing another computer does not copy
attachment snapshots there: file IDs and cached content keep their original
Runtime ownership, while the conversation-history endpoint transfers text only.

The other arrangement is not to switch at all: the phone talks to one runtime,
and the other computer is registered there as a `remote` agent. Work that
belongs on that computer is then handed over by Xiaoyou, or sent there when the
owner names it, and the transcript stays on the main runtime. A message that
another runtime has already passed on is not passed on again, so two runtimes
that list each other do not bounce it back and forth.

## Voice

A voice message is a recording: 16-bit mono WAV, 8 to 48 kHz, 0.2 to 120
seconds, at most 4 MB. The runtime checks the format, queues the message,
transcribes it when its turn comes, deletes the recording, and then handles the
text exactly like a typed message. An empty transcript fails the message with
a "did not catch that" error instead of sending nothing to the model.

Two engines are available.

**`sense_voice`** runs the SenseVoice model on this machine through
[sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx). Nothing leaves the
machine. The model is loaded on the first recording (a few seconds) and stays
in memory, about 250 MB.

```bash
python3 -m pip install sherpa-onnx        # same Python that runs the runtime
mkdir -p stt-models && cd stt-models
curl -L -O https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17.tar.bz2
tar xjf sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17.tar.bz2 && mv sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17 sense-voice && cd ..
```

Then set `"stt": {"engine": "sense_voice", "model_dir": "stt-models/sense-voice"}`
and check it with a recording of your own, or with one shipped with the model:

```bash
python3 -m xiaoyou_runtime --config config.json --stt stt-models/sense-voice/test_wavs/zh.wav
```

The download is about 160 MB. `stt-models/` is ignored by Git.

**`command`** runs any program you choose, for example a different model or a
cloud service: `"stt": {"engine": "command", "command": ["my-stt", "{audio}"]}`.
`{audio}` is replaced by the path of the WAV file, the command runs in the
folder of the configuration file, and whatever it prints on standard output is
the transcript. A non-zero exit code fails the message and reports the last
line of standard error.

With `stt.engine` set to `none`, voice messages are refused with an
explanation; typed messages are unaffected.

## Pairing the phone app

```bash
XIAOYOU_HOST=0.0.0.0 python3 -m xiaoyou_runtime --config config.json   # listen on the local network
python3 -m xiaoyou_runtime --config config.json --pair                 # prints http://<address>:<port>#<token>
```

Paste the printed line into the Pocket Hub app (see
[`companion/README.md`](../companion/README.md)). The line contains the token;
treat it like a password. The phone must be on the same network as this
machine, and the traffic is unencrypted HTTP, so do this only on a network you
trust. `--pair` guesses the machine's local address; if the machine has several
network interfaces, check that the address is the one the phone can reach.

### Reaching the runtime from anywhere (Tailscale Funnel)

When the computer and phone cannot connect directly over Tailscale (for example
the computer's network exits abroad and the phone is on mobile data), traffic
falls back to a distant relay and the app keeps dropping offline. Tailscale
Funnel gives the runtime a public HTTPS address without a server or domain:

1. Enable Funnel and HTTPS certificates for the tailnet (the first
   `tailscale funnel` run prints the link).
2. With `server.host` set to `tailscale`, run
   `tailscale funnel --bg http://127.0.0.1:8765`. Funnel on macOS cannot
   forward to the node's own tailnet address, so it targets the runtime's
   loopback listener.
3. Add `https://<machine>.<tailnet>.ts.net#<token>` in the app. The phone then
   needs no Tailscale. The runtime is reachable from the internet and protected
   only by its token, over HTTPS; turn it off with `tailscale funnel --https=443 off`.

The feed is gzipped and sent in 48 KB pages, so slow links still make progress.

### Updating the phone app through the runtime

The computer keeps the latest published APK; the phone downloads it through the
runtime (any address, Funnel included), checks its SHA-256 and hands it to
Android, which asks the owner to confirm:

```bash
python3 -m xiaoyou_runtime app publish companion/android/app/build/outputs/apk/debug/app-universal-debug.apk --note "what changed"
python3 -m xiaoyou_runtime app status
```

Incremental updates (since 0.8.3): large files that rarely change (speech models) stay out
of the APK and are published with `python3 -m xiaoyou_runtime app pack <name> <folder>` as a
resource pack, stored and served per file by SHA-256, so the phone downloads only what changed.
With `--github`, `app publish` and `app pack` first upload the same files to GitHub Releases
(the APK defaults to arm64 only, under tag `app-<version>-<code>`; pack files share the
`app-packs` release, named by checksum, and unchanged ones are not uploaded again) and put the
addresses into the manifest, so the phone can choose to download from the internet. The token
comes from `GITHUB_TOKEN`/`GH_TOKEN` or git's GitHub credential, is sent only to GitHub, and
only files are uploaded, never code; a failed upload publishes nothing locally.

The version code is read from Gradle's `output-metadata.json` next to the APK
(or `--version-code`). The app offers the update when the code is higher, or
when it is the same but the package differs and was published after the phone
installed its copy. It checks when opened (at most every ten minutes) and from
Connections and settings → App update. The first version with this feature
must be installed by hand.

App 0.13.1 adds ABI selection and resumable downloads. Publish the universal APK
as the default and add `--variant ABI=APK` for each matching Gradle ABI output;
see [the app shipping flow](../companion/README.md#shipping-an-app-change-developers-and-ai-agents).
For a private Runtime whose legacy phones are all known ARM64 devices, the ARM64
APK can be the default if the universal and other ABI variants are also supplied.
Unknown legacy phones should retain a universal default.

`GET /v1/app?abi=arm64-v8a` returns the selected package's ordinary version, size,
and checksum fields; an unavailable ABI falls back to the universal package.
Omitting `abi` preserves the published default. Download from
`GET /v1/app/apk?abi=arm64-v8a&sha256=<expected-hash>`; replacement of that release
returns 409 rather than mixing versions. Single byte ranges return 206 with
`Content-Range`, `Accept-Ranges`, and an ETag; invalid ranges return 416. If-Range
mismatches return the complete file, and If-Match mismatches return 412. All
requests retain Bearer authentication. Hash-named immutable APKs avoid replacing
bytes beneath an open transfer; storage keeps at most the current and previous
publication's hash-named packages. The old `app.apk` filename remains available
for older Runtime processes. New apps tolerate older servers ignoring Range.
These changes reduce transferred bytes and repeated work, but do not remove
Funnel or DERP relay bottlenecks; measure throughput on the phone's actual path.

From App 0.14.0, publish an uploaded APK with `--download-url HTTPS_URL` for
the default package and `--variant-download-url ABI=HTTPS_URL` for each hosted
variant. Upload exactly the verified local bytes first. Runtime still supplies
the trusted version, size, and SHA-256. The App never sends its Runtime token to
the host, follows only HTTPS redirects, and verifies the complete hash after
resuming. Variants without a URL keep their Runtime download path. The App and
Runtime limit is 512 MiB; older Apps retain their 128 MiB limit, so the first
large upgrade needs browser installation. This setting changes only APK delivery,
not conversation or recording destinations.

## How a sentence is processed

1. Messages queue per conversation: one sentence at a time within a
   conversation, and conversations do not wait for each other.
2. The order in [Who gets a message](#who-gets-a-message) says whether it was
   decided who does this. If so, a card is opened, the work goes to the
   background, and the sentence has been dealt with.
3. Otherwise the agent that speaks as Xiaoyou takes it. For Claude Code that is
   `claude -p --output-format json --append-system-prompt <persona + helpers +
   the things at the moment> --json-schema <reply, brief, mood, card, action>
   --tools "" --permission-mode dontAsk [--model ...] [--effort <a level the
   owner or caller named>] [--resume <session>]`.
   The owner's text is sent on standard input, never as a command-line
   argument. The runtime does what the `action` in her reply says; see
   [One thing, one card](#one-thing-one-card).
4. A thing in the background: for Claude Code that is `claude -p
   --output-format stream-json --verbose [--append-system-prompt <persona>
   --json-schema <reply, brief, mood>] --permission-mode <mode>
   --permission-prompt-tool mcp__xiaoyou__approve --mcp-config <a temporary
   file in state> --add-dir <directory> [--allowedTools ...] [--model ...]
   [--effort <level>] [--resume <the session of this thing>]`. It still starts in `workdir`. The
   runtime reads its events line by line: the session identifier is recorded
   right at the start, and every step becomes a line of progress. When the
   thing is cancelled or its request changes, it is ended together with every
   process it started (Claude Code runs commands in a session of their own, so
   the whole tree is found by parentage). The runtime does the same clean-up
   when it is itself killed.
5. Session identifiers are stored in `state/sessions.json`: per conversation
   and agent for Xiaoyou's own line, per conversation/card and agent for things
   in the background.
6. Every sentence and every result from the background is added to Xiaoyou's
   own transcript, `state/transcript.json`, together with whether the agent
   that speaks for her witnessed it. What it did not witness (results from the
   background, sentences that went straight to another agent, turns the phone
   carried over) it is told first, once, when it next takes a sentence.

The transcript is Xiaoyou's, not any agent's: when a different agent takes the
next message, Xiaoyou still knows what was just said. Each agent's own session
holds only the part it took part in.

## Running on another machine or a virtual machine

- Copy this directory, create `config.json` (or set the environment variables),
  and log in to Claude Code on that machine. Session identifiers are local to a
  machine: conversations do not follow the runtime when it moves.
- The service speaks plain HTTP. Keep it on `127.0.0.1` and reach it through an
  encrypted channel you control (a private network overlay, an SSH tunnel, or a
  reverse proxy with TLS). Listening on a public address directly is not safe.
- Pending and finished message records live in memory. After a restart, old
  message identifiers return `404`; conversations continue because the session
  table is on disk.

## What is and is not verified

Attachment host tests exercise authenticated streaming downloads, source-file
removal, restart persistence, symlink/credential/FIFO exclusions, storage limits,
and safe failure metadata. Stand-in agent tests cover task manifests, attachment
binding to the final entry, and preserving long replies without JSON wrappers.
Desktop approval tests cover one-shot decisions, expired/dead owners, duplicate
answers, notifications without a decision channel, and desktop approvals across
conversations. These are local tests; real phone downloads, system file opening,
background notifications, and trusted desktop hook operation still need an
end-to-end check on the intended computer and phone.

Verified by `runtime/tests/test_runtime.py` (run by `./tools/validate.sh
--static`): configuration checks and the old configuration shape; the exact
command line and standard input given to fake `claude` and `codex` executables,
result and error parsing, session continuation; naming an agent, the order of
routing rules and a command as the router; storing cards, their sequence numbers, writing them to
disk and marking unfinished ones as failed after a restart; Xiaoyou answering
directly, being free for the next sentence right after handing work out,
several things in progress at once, adding to a thing, changing its request
(including really stopping a process and running it again), cancelling
(including a grandchild process in a session of its own), the limit on
parallel things, recording, waiting for and answering approvals, the
permission tool itself and the entrance that listens on this machine only,
the whole path with a fake Claude Code from the question through the owner's
answer to the file being written or not, Codex's app-server mode (against a
stand-in: a turn, continuing, the three kinds of question, adding to a turn,
interrupting, the ways it fails), a helper that fails or does not exist, putting what
cannot be done to her once more, reporting back after a work-only helper,
telling her what she did not witness; the `remote` agent between two real
runtimes (with `echo`), including two that list each other; queueing per
conversation, retry by `client_id`, token checks, the HTTP interface
(including the long poll on the feed); and voice messages with a fake
recognition command (format checks, transcription, empty and failed
recognition, clean-up of recordings).

For the tiers and usage of 0.6.0 the tests verify: how levels and switchable
models are read from the configuration and which forms are rejected; `--effort`,
`model_reasoning_effort` and the app-server's `effort` on what is given to the
fake `claude` and `codex`; a level word after a name; collecting the model,
quota and tokens they report; the conversion of each of the three sources;
keeping quota per login, dropping expired numbers, writing to disk and
restarting; really asking a fake Claude Code and a fake app-server (including
the cases where nothing can be asked: not a subscription, a version that is
too old, not signed in); the difference between the start and the end of a
round; the lines in Xiaoyou's prompt; how the level she picks, the level the
caller picks and the level carried over from the last round reach the helper
and the card; redoing a round when the level changes with the request; usage
in the feed, and the feed returning early when usage changes.

On 2026-10-10 this was looked at in a cloud workspace against the real Claude
Code 2.1.294 and Codex 0.162.0, neither signed in with a subscription:
`claude --help` lists `--effort <level>` (low, medium, high, xhigh, max); a job
with `--model sonnet --effort medium` completed, its event stream named the
model actually used at the start, and the tokens in its result reached the
card; `get_usage` answered without a prompt in under a second, saying this
login has no quota to show; Codex's protocol definition has `effort` and
`model` in `turn/start` and `model` in the result of `thread/start`; signed
out, `account/rateLimits/read` answered that a login is required, and a turn
with `effort` was accepted (and then failed for lack of a login).

**Not verified**: the actual numbers from `get_usage` and from the event
stream under a subscription login (their shape follows the definitions shipped
in Claude Code 2.1.294; no real answer has been seen); Codex quota under a
subscription login; whether either matches the tool's own display; whether
`d5` and `d7` are useful on real jobs; whether the model that speaks as
Xiaoyou follows the prompt when it picks a level or weighs quota (only real
conversations can show that); whether Claude Code and Codex accept another
model or level within one continued session.

Version 0.5 was checked by hand on 2026-10-09 with Claude Code 2.1.295 on
Linux, in a cloud workspace rather than on the intended computer, with one
`claude_code` agent that had `Bash` allowed, through the HTTP interface:

- First sentence, "run sleep 20 and then count the .conf files under /etc":
  it had been dealt with after 7.6 seconds; Xiaoyou said she had handed it to
  Claude Code, and the card was `working`.
- Second sentence right after, "how many eggs are in a dozen": answered after
  13.7 seconds ("12", with a remark that the other thing was still running),
  on a card of its own. The first thing was still in progress.
- After 37 seconds the feed delivered the first card as `done`, with the count.
- Xiaoyou takes about 6 to 7 seconds over a sentence, most of it Claude Code
  starting up and answering.

The same day, in the same environment, with the default `manual` mode (only
the tools that read files approved in advance), through the HTTP interface:

- Asked to write one line into a file: two approvals came in turn (a `Bash`
  command, then a `Write`), the card became `waiting`, and the feed carried
  the content as it was. Both answered `deny`: the file was not written, and
  Xiaoyou said so. Answering the same approval again returned `409`.
- Asked again, both answered `allow`: the file was written with the right
  content, and no temporary file was left in `state/`.
- Separately, an approval was left unanswered for 100 seconds; Claude Code
  waited and then carried on as usual.
- Changing a request: asked to `sleep 45` and then read a file, and told after
  9 seconds to skip the sleep. Xiaoyou picked that card and `redo`; Claude
  Code was stopped, continued in the same session, and had the result about
  10 seconds later, without waiting out the 45 seconds.
- Cancelling: asked to `sleep 60`, then told to forget it. Xiaoyou picked that
  card and `cancel`; Claude Code and the `sleep` it had started both ended.
  Killing the runtime ends things in progress in the same way.
- Two things found and fixed on the way: the model often writes a card number
  `c3` as `3` (now understood), and Claude Code runs commands in a session of
  their own, so ending its process group alone left `sleep` behind (the whole
  tree is now ended by parentage).
- This cloud environment makes every `claude -p` share one session identifier
  unless it is started with a clean environment (`env -i HOME=$HOME
  PATH=$PATH`). The checks in this list were done with a clean environment;
  the earlier one about things in parallel was not.

Earlier the same day version 0.4 was checked by hand with Claude Code 2.1.295
on Linux, also in a cloud workspace, with one `claude_code` agent and a
stand-in helper of type `command`. In that version a helper finished within
the same turn, so the timings below do not apply to the current one:

- A direct answer took about 5 seconds, and a second turn continued the first.
- Xiaoyou deciding to hand over: asked to "find someone who reviews code",
  Claude Code produced the hand-over in the requested structure, the runtime ran
  the helper, and Claude Code summed it up; it noticed that the stand-in had
  answered a different question and told the owner so. Polling the HTTP
  interface showed `agent` and `stage` change. The turn took 18 to 21 seconds.
- Naming a work-only agent (`@name`): the helper ran first and Claude Code
  reported the result, in about 6 seconds.
- A configuration and a session table in the 0.3 shape loaded as before, with
  the old session identifier attributed to `claude`.

Checked by hand on 2026-10-08 with Claude Code 2.1.294 on Linux, in a cloud
workspace rather than on the intended computer:

- `--once` produced a reply, a brief, and a mood in the requested structure;
  a second turn recalled a detail from the first, so continuation works.
- Through the HTTP interface, a request without the token got `401`, and a
  request asking for a second opinion made the model run a stand-in tool, then
  summarize its output in its own words. (That was version 0.2, where other
  agents were listed under `tools` and run by Claude Code itself.)
- A simple turn took about six seconds end to end.

Checked by hand on 2026-10-08 on Linux with sherpa-onnx 1.13 and the int8
SenseVoice model, using the Mandarin sample shipped with the model (5.6 s):

- `--stt` printed the sentence; loading the model took about 3 s and
  recognition about 0.6 s.
- The same sample was encoded by the firmware's voice encoder built for the
  host, decoded by the phone app's decoder on a desktop Java runtime, and
  posted to `/v1/voice`. It was recognized, with one character different from
  the uncompressed result; with every fifteenth frame deliberately dropped it
  was still recognized.

Checked by hand on 2026-10-08 on Linux with two runtimes on one machine, each
driving the real Claude Code with its own session, using the phone app's
request code on a desktop Java runtime: a fact told to the first runtime was
recalled by the second after the turns were carried over, and something the
second said was recalled by the first after carrying them back.

Verified by hand on 2026-10-09 on Linux (in a cloud workspace), the firmware
library: `firmware fetch` took `firmware-build-2` from this repository's real
releases on GitHub, checked the published SHA-256, and read a version string
that is the commit it was built from; `--commit … --wait … --detach` fetched the
same version in the background; a locally compiled application image and its
merged image stored as the same version; `list`, `status`, `push`, `restore`,
`cancel` and `remove` behaved as described. `runtime/tests/test_firmware.py`
covers parsing, the library, the interface and the command line with images
assembled by hand.

Not verified:

- The firmware library together with the phone app and a real device: no
  version has actually been installed on a device over Bluetooth.
- The `tailor` agent: the whole loop of changing the interface, committing,
  pushing and waiting for the build has not been run with a real Claude Code.
  Whether this computer can `git push`, and whether Claude Code accepts the
  `allowed_tools` in the example, has to be confirmed on that computer.
- `firmware build`: tested with a stand-in command only, never against a real
  ESP-IDF.
- Windows: nothing in this directory has been run on Windows.
- Voice from a real device: microphone quality, Bluetooth throughput, and
  recognition of real speech in a real room.
- sherpa-onnx on macOS.

- A complete turn with the real Codex. The `codex` agent was written against
  codex-cli 0.162.0. In app-server mode the message shapes come from the
  protocol definition it generates itself; against the real command line the
  handshake, starting a thread, starting a turn, and reporting "cannot connect"
  within a bounded time when not logged in were checked. Approval requests,
  adding to a turn, interrupting, and a turn ending normally were only
  exercised against a stand-in written from that definition: there is no
  logged-in Codex in the cloud workspace. The options of `exec` mode come from
  its help output, and no turn has completed for real there either.
- The `remote` agent against another real computer over a real network.
- A local model: a `command` agent answering as Xiaoyou has only been
  exercised with a script in the tests.
- A virtual machine, and a long-running service over days.
- Version 0.5 on macOS and together with the phone app and the device. Phone
  app 0.5.0 and the current firmware do not know about cards: they can send
  and receive, but never see a result from the background. The matching app
  and firmware do not exist yet.
- Whether the model always picks the right `card` and `action`: only the
  sentences above were tried by hand.
- An owner who takes very long to answer an approval (100 seconds was the
  longest tried).
- "Ask when it goes silent" on a really long task: tried once in the cloud with
  a real Claude Code (limit set to 30 seconds, told to run `sleep 50`; asked at
  30 seconds, stopped after the answer "no"). Whether the defaults of ten and
  thirty minutes fit, whether Claude Code is asked about by mistake while it
  thinks for long, and whether Codex's app-server sends messages while a
  command runs, has not been seen on a real long task.
- Several approvals at once within one thing: the permission tool passes them
  on one at a time, the rest queue.
- Whether this computer and the account's usage limits cope with many things
  in progress at once.
- How the model behaves when a helper is slow or returns a large output.

Known risks:

- This relies on running Claude Code non-interactively with a subscription
  login. Claude Code documents a `--bare` mode that skips that login and states
  it is intended to become the default for `-p`; if that happens the
  `claude_code` agent needs another way to authenticate. Check the current Claude Code terms before
  offering this to anyone other than yourself.
- Every call counts against the usage limits of the logged-in account. Each
  sentence nobody was named for costs one call to the agent that speaks, and a
  work-only helper's result costs one more for the report. Parallel things are
  not limited by default; the owner keeps an eye on usage himself.
- With the default configuration a thing given to Claude Code can reach the
  whole home directory, and what keeps that safe is that everything that
  should be asked is asked. An operation the owner allows really happens; the
  device shows only the first 319 bytes of it, and the phone the whole.
- Phone app 0.5.0 and the current firmware do not show these approvals: until
  the matching app and firmware exist, a thing waiting for approval can only
  be answered through the HTTP interface, or waits until it times out.
- Two things changing the same folder at the same time are not protected from
  each other.
- The shared workspace has only been tested against a local stand-in (row
  contents, retries, keeping what could not be written); not one row has been
  written to the real Notion. It only writes: tasks other agents leave on the
  bus are not picked up by the runtime yet.
- Starting a conversation over (reset) neither stops things in progress nor
  removes cards.
- One shared token, no per-device identity, no rate limiting.

## Tests

```bash
python3 runtime/tests/test_runtime.py
python3 runtime/tests/test_firmware.py
python3 runtime/tests/test_desktop_sync.py
python3 runtime/tests/test_desktop_content.py
python3 runtime/tests/test_desktop_titles.py
python3 runtime/tests/test_task_titles.py
python3 runtime/tests/test_desktop_hook.py
python3 runtime/tests/test_desktop_approvals.py
python3 runtime/tests/test_artifacts.py
python3 runtime/tests/test_artifact_delivery.py
```
