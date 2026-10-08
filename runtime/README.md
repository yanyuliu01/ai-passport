<p align="right">
  <a href="README.zh_CN.md">简体中文</a> · <strong>English</strong>
</p>

# Xiaoyou Runtime

Xiaoyou is the single voice the owner talks to. This runtime is the small,
always-on service behind that voice: it receives a message, hands it to an
agent backend together with Xiaoyou's persona, and returns a full reply, a
short brief for the Passport screen, and a mood for the pixel pet.

Today the only real backend drives the **Claude Code command line** in
non-interactive mode, using the Claude login already present on the machine.
Other agents (for example Codex) are not separate voices: they are listed in the
configuration as tools that Xiaoyou may hand work to and then summarize.

```text
phone app / any client ──HTTP──> Xiaoyou Runtime ──runs──> claude -p (persona, tools)
                                                              └─> other agent CLIs as tools
```

Status: verification stage, intended to run on your own computer. The same
code is meant to move to a virtual machine later; nothing in it assumes a
particular host. See [What is and is not verified](#what-is-and-is-not-verified).

## Requirements

- Python 3.9 or newer. No third-party packages.
- For the `claude_code` backend: Claude Code installed and logged in on the same
  machine and user account (`claude` must work in a terminal).

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

To try the whole path without calling any model, set `"backend": "echo"`.

## Configuration

Relative paths are resolved against the directory of the configuration file.

| Key | Default | Meaning |
| --- | --- | --- |
| `server.host` | `127.0.0.1` | Address to listen on. |
| `server.port` | `8765` | Port to listen on. |
| `server.token` | none | Shared secret, at least 16 characters. The placeholder is rejected. |
| `state_dir` | `state` | Where the conversation-to-session table is kept. |
| `persona_file` | `persona.txt` | Xiaoyou's persona, appended to the backend's system prompt. |
| `backend` | `claude_code` | `claude_code` or `echo`. |
| `brief_max_chars` | `120` | Upper bound for the small-screen brief (20 to 400). |
| `turn_timeout_seconds` | `600` | A turn is stopped after this long (10 to 7200). |
| `claude_code.command` | `["claude"]` | Command to run, as a list. |
| `claude_code.workdir` | `workdir` | Working directory for Claude Code; created if missing. |
| `claude_code.model` | `null` | Optional model name passed as `--model`. |
| `claude_code.permission_mode` | `dontAsk` | Passed as `--permission-mode`. |
| `claude_code.allowed_tools` | `[]` | Rules passed as `--allowedTools`. |
| `claude_code.extra_args` | `[]` | Extra arguments appended to the command. |
| `tools[]` | `[]` | Agents Xiaoyou may delegate to: `name`, `description`, `allowed_tools`, `enabled`. |

Environment variables override the file, so a container or virtual machine can
be configured without editing it: `XIAOYOU_CONFIG`, `XIAOYOU_HOST`,
`XIAOYOU_PORT`, `XIAOYOU_TOKEN`, `XIAOYOU_STATE_DIR`, `XIAOYOU_BACKEND`.

### Adding an agent as a tool

A tool entry does two things: its `name` and `description` are added to
Xiaoyou's system prompt, and its `allowed_tools` rules are added to
`--allowedTools` so Claude Code may run it without asking. The example
configuration ships a disabled Codex entry; set `"enabled": true` after
installing and logging in to that command line on the same machine.

With `permission_mode` set to `dontAsk`, anything not covered by an allow rule
is refused instead of waiting for a person who is not there. Keep the allow
rules narrow: every rule is something Xiaoyou can do unattended.

## HTTP interface

All requests except `/healthz` need `Authorization: Bearer <token>`. Bodies and
responses are JSON.

| Request | Result |
| --- | --- |
| `GET /healthz` | `{"ok": true, "version", "backend"}`; no token needed. |
| `POST /v1/messages` with `{"text", "conversation"?, "client_id"?}` | `202` and the message record, status `queued`. |
| `GET /v1/messages/<id>?wait=<seconds>` | The message record. With `wait` (up to 60) the call returns as soon as the turn finishes. |
| `POST /v1/conversations/<name>/reset` | Forgets the session of that conversation; the next message starts fresh. |

A message record has `id`, `client_id`, `conversation`, `status` (`queued`,
`running`, `done`, `failed`), `text`, `reply`, `brief`, `mood` (`idle`, `busy`,
`ask`, `happy`, `oops`), `error`, `created_at`, and `finished_at`.

Sending the same `client_id` again returns the existing record instead of
running the turn twice, so a client may retry safely after a lost response.

```bash
TOKEN=...   # the value of server.token
curl -s -X POST http://127.0.0.1:8765/v1/messages \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"text": "hello", "client_id": "demo-1"}'
curl -s "http://127.0.0.1:8765/v1/messages/<id>?wait=60" -H "Authorization: Bearer $TOKEN"
```

## How a turn works

1. Messages are queued and handled one at a time.
2. The backend runs `claude -p --output-format json --append-system-prompt
   <persona + tool list> --json-schema <reply, brief, mood> --permission-mode
   <mode> [--allowedTools ...] [--model ...] [--resume <session>]`. The owner's
   text is sent on standard input, never as a command-line argument.
3. The session identifier returned by Claude Code is stored per conversation in
   `state/sessions.json` and passed back with `--resume` next time. The
   conversation transcript itself stays with Claude Code.

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

Verified by `runtime/tests/test_runtime.py` (run by `./tools/validate.sh
--static`): configuration checks, the exact command line and standard input
given to a fake `claude` executable, result and error parsing, session
continuation, ordering, retry by `client_id`, token checks, and the HTTP
interface with the `echo` backend.

Not verified:

- A successful turn through the real Claude Code. One attempt with Claude Code
  2.1.294 accepted every argument and returned a structured error (account
  usage limit), which the runtime reported correctly; no reply was produced.
- Whether the persona and reply schema make the model delegate to tools and
  summarize the way the persona asks.
- Any tool entry, including Codex.
- Use from the phone app (the app does not call this service yet) or from a
  virtual machine.

Known risks:

- This relies on running Claude Code non-interactively with a subscription
  login. Claude Code documents a `--bare` mode that skips that login and states
  it is intended to become the default for `-p`; if that happens this backend
  needs another way to authenticate. Check the current Claude Code terms before
  offering this to anyone other than yourself.
- Turns count against the usage limits of the logged-in account.
- One shared token, no per-device identity, no rate limiting.

## Tests

```bash
python3 runtime/tests/test_runtime.py
```
