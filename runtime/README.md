<p align="right">
  <a href="README.zh_CN.md">简体中文</a> · <strong>English</strong>
</p>

# Xiaoyou Runtime

Xiaoyou is the single voice the owner talks to. This runtime is the small,
always-on service behind that voice: it receives a message, hands it to an
agent backend together with Xiaoyou's persona, and returns a full reply, a
short brief for the Passport screen, and a mood for the pixel pet. A message
can be typed text or a voice recording; a recording is first turned into text
by a speech recognition engine chosen in the configuration.

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

- Python 3.9 or newer. No third-party packages, unless you enable the built-in
  speech recognition engine (see [Voice](#voice)).
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
| `server.name` | the machine's host name | What the phone app calls this runtime, at most 40 characters. |
| `server.token` | none | Shared secret, at least 16 characters. The placeholder is rejected. |
| `state_dir` | `state` | Where the conversation-to-session table is kept. |
| `persona_file` | `persona.txt` | Xiaoyou's persona, appended to the backend's system prompt. |
| `backend` | `claude_code` | `claude_code` or `echo`. |
| `brief_max_chars` | `120` | Upper bound for the small-screen brief (20 to 400). |
| `turn_timeout_seconds` | `600` | A turn is stopped after this long (10 to 7200). |
| `claude_code.command` | `["claude"]` | Command to run, as a list. |
| `claude_code.workdir` | `workdir` | Working directory for Claude Code; created if missing. |
| `claude_code.config_dir` | `null` | Optional Claude Code configuration directory, passed to it as `CLAUDE_CONFIG_DIR`. See [Using a separate Claude login](#using-a-separate-claude-login). |
| `claude_code.model` | `null` | Optional model name passed as `--model`. |
| `claude_code.permission_mode` | `dontAsk` | Passed as `--permission-mode`. |
| `claude_code.allowed_tools` | `[]` | Rules passed as `--allowedTools`. |
| `claude_code.extra_args` | `[]` | Extra arguments appended to the command. |
| `tools[]` | `[]` | Agents Xiaoyou may delegate to: `name`, `description`, `allowed_tools`, `enabled`. |
| `stt.engine` | `none` | Speech recognition: `none`, `sense_voice`, or `command`. See [Voice](#voice). |
| `stt.model_dir` | none | `sense_voice`: folder holding `model.int8.onnx` (or `model.onnx`) and `tokens.txt`. |
| `stt.language` | `auto` | `sense_voice`: `auto`, `zh`, `en`, `ja`, `ko`, or `yue`. |
| `stt.threads` | `2` | `sense_voice`: processor threads (1 to 16). |
| `stt.command` | `[]` | `command`: the command to run; one argument must contain `{audio}`. |
| `stt.timeout_seconds` | `60` | `command`: a recognition run is stopped after this long (5 to 600). |

Environment variables override the file, so a container or virtual machine can
be configured without editing it: `XIAOYOU_CONFIG`, `XIAOYOU_HOST`,
`XIAOYOU_NAME`, `XIAOYOU_PORT`, `XIAOYOU_TOKEN`, `XIAOYOU_STATE_DIR`, `XIAOYOU_BACKEND`,
`XIAOYOU_CLAUDE_CONFIG_DIR`.

### Using a separate Claude login

Claude Code reads its login and settings from `~/.claude`. If that directory is
set up for something else (for example a company gateway through
`ANTHROPIC_BASE_URL` or an API key), give Xiaoyou its own directory instead of
changing it:

```bash
mkdir ~/xiaoyou-login && cd ~/xiaoyou-login      # any folder except your home folder
CLAUDE_CONFIG_DIR=~/.claude-xiaoyou claude       # log in with the subscription, check /status, quit
```

Then set `"config_dir": "~/.claude-xiaoyou"` under `claude_code`. Do the login
from a folder other than your home folder: Claude Code also loads
`<current folder>/.claude/settings.json` as project settings, and in the home
folder that is the very file you are trying to avoid.

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
| `GET /healthz` | `{"ok": true, "version", "backend", "name"}`; no token needed. |
| `POST /v1/messages` with `{"text", "conversation"?, "client_id"?}` | `202` and the message record, status `queued`. |
| `POST /v1/voice?conversation=<name>&client_id=<id>` with a WAV file as the body | `202` and the message record, `kind` `voice`, empty `text`. `400` if the recording is not acceptable or no engine is configured. |
| `GET /v1/messages/<id>?wait=<seconds>` | The message record. With `wait` (up to 60) the call returns as soon as the turn finishes. |
| `POST /v1/conversations/<name>/history` with `{"turns": [{"id", "text", "reply", "at"?}]}` | `{"accepted": n}`: how many of the turns (at most 30) this runtime did not know. They are told to the model with the next message. |
| `POST /v1/conversations/<name>/reset` | Forgets the session of that conversation; the next message starts fresh. |

A message record has `id`, `client_id`, `conversation`, `kind` (`text` or
`voice`), `status` (`queued`, `transcribing`, `running`, `done`, `failed`),
`text`, `reply`, `brief`, `mood` (`idle`, `busy`, `ask`, `happy`, `oops`),
`error`, `created_at`, and `finished_at`. For a voice message `text` is empty
until the recording has been transcribed.

Sending the same `client_id` again returns the existing record instead of
running the turn twice, so a client may retry safely after a lost response.

```bash
TOKEN=...   # the value of server.token
curl -s -X POST http://127.0.0.1:8765/v1/messages \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"text": "hello", "client_id": "demo-1"}'
curl -s "http://127.0.0.1:8765/v1/messages/<id>?wait=60" -H "Authorization: Bearer $TOKEN"
```

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
continuation, ordering, retry by `client_id`, token checks, the HTTP interface
with the `echo` backend, and voice messages with a fake recognition command
(format checks, transcription, empty and failed recognition, clean-up of
recordings).

Checked by hand on 2026-10-08 with Claude Code 2.1.294 on Linux, in a cloud
workspace rather than on the intended computer:

- `--once` produced a reply, a brief, and a mood in the requested structure;
  a second turn recalled a detail from the first, so continuation works.
- Through the HTTP interface, a request without the token got `401`, and a
  request asking for a second opinion made the model run a stand-in tool
  configured under `tools`, then summarize its output in its own words.
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

Not verified:

- Windows: nothing in this directory has been run on Windows.
- Voice from a real device: microphone quality, Bluetooth throughput, and
  recognition of real speech in a real room.
- sherpa-onnx on macOS.

- The real Codex command line; the delegation check used a stand-in script.
- A virtual machine, and a long-running service over days. On macOS only a
  single `--once` turn has been run (by the owner, with a separate
  `config_dir`); the HTTP service has not.
- Use from a phone. The app's request code was run on a desktop Java runtime
  against this service with the `echo` backend (send, wait, wrong token), but
  the app itself has not been run on a phone.
- How the model behaves when a delegated tool is slow, fails, or returns a
  large output.

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
