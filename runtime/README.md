<p align="right">
  <a href="README.zh_CN.md">简体中文</a> · <strong>English</strong>
</p>

# Xiaoyou Runtime

Xiaoyou is the single voice the owner talks to, and an identity of her own: she
is not another name for one model. This runtime is the small, always-on service
behind her, and what is hers lives here: the persona, the transcript of her
conversations with the owner, the decision of who gets each message, and how
somebody else's result is reported back. Claude Code, Codex, Xiaoyou on another
computer, and a local model added later are all **agents** she can use.

For each message the runtime returns a full reply, a short brief for the
Passport screen, and a mood for the pixel pet. A message can be typed text or a
voice recording; a recording is first turned into text by a speech recognition
engine chosen in the configuration.

```text
phone app / any client ──HTTP──> Xiaoyou Runtime
                                   │  persona · transcript · routing · reporting back
                                   ├──> claude   (Claude Code command line; can answer as Xiaoyou)
                                   ├──> codex    (Codex command line; does work only)
                                   ├──> any command (a local model, for example)
                                   └──> Xiaoyou Runtime on another computer
```

Status: verification stage, intended to run on your own computer. The same
code is meant to move to a virtual machine later; nothing in it assumes a
particular host. See [What is and is not verified](#what-is-and-is-not-verified).

## Requirements

- Python 3.9 or newer. No third-party packages, unless you enable the built-in
  speech recognition engine (see [Voice](#voice)).
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

## Configuration

Relative paths are resolved against the directory of the configuration file.

| Key | Default | Meaning |
| --- | --- | --- |
| `server.host` | `127.0.0.1` | Address to listen on. |
| `server.port` | `8765` | Port to listen on. |
| `server.name` | the machine's host name | What the phone app calls this runtime, at most 40 characters. |
| `server.token` | none | Shared secret, at least 16 characters. The placeholder is rejected. |
| `state_dir` | `state` | Where the session table and Xiaoyou's transcript are kept. |
| `persona_file` | `persona.txt` | Xiaoyou's persona. Given only to an agent that answers as Xiaoyou. |
| `brief_max_chars` | `120` | Upper bound for the small-screen brief (20 to 400). |
| `turn_timeout_seconds` | `600` | Timeout for agents that do not set their own `timeout_seconds` (10 to 7200). |
| `agents.<name>` | none | An agent; see [Agents](#agents). At least one must be enabled. |
| `xiaoyou.default_agent` | the first agent | Who gets a message when nobody is named and the router has no opinion. |
| `xiaoyou.voice_agent` | the default agent, or the first one that speaks if the default only works | Who reports back, as Xiaoyou, after an agent that only does work. |
| `xiaoyou.max_handoffs` | `2` | How many times work may be handed over in one turn (0 to 5); 0 means never. |
| `xiaoyou.router.type` | `mention` | `mention` or `command`; see [Who gets a message](#who-gets-a-message). |
| `xiaoyou.router.command` | `[]` | `command`: the command that decides. |
| `xiaoyou.router.timeout_seconds` | `10` | `command`: after this long the default agent is used instead (1 to 120). |
| `stt.engine` | `none` | Speech recognition: `none`, `sense_voice`, or `command`. See [Voice](#voice). |
| `stt.model_dir` | none | `sense_voice`: folder holding `model.int8.onnx` (or `model.onnx`) and `tokens.txt`. |
| `stt.language` | `auto` | `sense_voice`: `auto`, `zh`, `en`, `ja`, `ko`, or `yue`. |
| `stt.threads` | `2` | `sense_voice`: processor threads (1 to 16). |
| `stt.command` | `[]` | `command`: the command to run; one argument must contain `{audio}`. |
| `stt.timeout_seconds` | `60` | `command`: a recognition run is stopped after this long (5 to 600). |

Environment variables override the file, so a container or virtual machine can
be configured without editing it: `XIAOYOU_CONFIG`, `XIAOYOU_HOST`,
`XIAOYOU_NAME`, `XIAOYOU_PORT`, `XIAOYOU_TOKEN`, `XIAOYOU_STATE_DIR`,
`XIAOYOU_DEFAULT_AGENT`, `XIAOYOU_CODEX_CONFIG_DIR` (applied to every `codex`
agent), and `XIAOYOU_CLAUDE_CONFIG_DIR` (applied to every
`claude_code` agent).

A configuration written for 0.3 or earlier (`backend`, `claude_code`, `tools`)
still loads: it is read as a single agent named `claude`, a notice at start-up
says it is the old shape, and existing sessions continue. An entry under `tools`
that was enabled has to be rewritten by hand as an agent under `agents`.

## Agents

`agents` is an object whose keys are agent names (letters, digits, underscores
and hyphens, at most 24 characters). Every agent has:

| Key | Default | Meaning |
| --- | --- | --- |
| `type` | none | `claude_code`, `codex`, `command`, `remote`, or `echo`. |
| `enabled` | `true` | `false` makes the runtime treat it as absent. |
| `description` | empty | One sentence on what it is good at. Xiaoyou reads it to decide whether to hand work over, and so does the router. |
| `aliases` | `[]` | Other names it answers to when named in a message, for example how speech recognition tends to spell it. |
| `speaks` | by type | Whether it may answer as Xiaoyou. `claude_code`, `remote` and `echo` do by default; `codex` and `command` only do work. |
| `timeout_seconds` | `turn_timeout_seconds` | How long one run of this agent may take. |

Keys by type:

| Type | What it does | Keys |
| --- | --- | --- |
| `claude_code` | Runs the Claude Code command line non-interactively, with the login already present on this machine. | `command` (default `["claude"]`), `workdir` (default `workdir`), `config_dir`, `model`, `permission_mode` (default `dontAsk`), `allowed_tools`, `extra_args`, `env` |
| `codex` | Runs the Codex command line non-interactively (`codex exec`, and `codex exec resume` to continue). | `command` (default `["codex"]`), `workdir`, `config_dir`, `sandbox` (default `read-only`), `model`, `extra_args` |
| `command` | Any command. The text goes in on standard input and standard output is the result; an argument containing `{prompt}` receives the text instead. It has no session: every run starts fresh. | `command`, `workdir` |
| `remote` | Xiaoyou Runtime on another computer. That side has its own persona, agents and sessions; what comes back is already Xiaoyou's words. | `url`, `token` (that runtime's `server.token`) |
| `echo` | Repeats what it is given; calls no model. | none |

With `permission_mode` set to `dontAsk`, anything in Claude Code not covered by
`allowed_tools` is refused instead of waiting for a person who is not there.
Keep the allow rules narrow: every rule is something this agent can do
unattended. The same goes for Codex's `sandbox`, which is read-only by default.

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
    "ANTHROPIC_BASE_URL": "https://api.deepseek.com/anthropic",
    "ANTHROPIC_AUTH_TOKEN": "your DeepSeek key"
  }
}
```

Set `"enabled"` to `true`, fill in the key, and point `xiaoyou.default_agent` (and
`voice_agent`, if set) at it: Xiaoyou herself is then answered by that model. The
original `claude` stays as a helper, and switching back is pointing those two keys
at `claude` again. Give it its own `config_dir` so its sessions stay apart from the
subscription login; that directory needs no login. The key lives only in
`config.json`, which is not committed.

Not verified: this follows DeepSeek's API documentation and the requests Claude
Code actually sends, but no turn has been run with a real key. Claude Code's web
search is a server-side Anthropic tool and may not work on another service.

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

An agent that was named (rules 1 and 2) is always shown as the one Xiaoyou
handed the work to: the message record gets a `handoff` event, and `helper` is
that agent while it works, so the phone and the device show who has it. This
includes the agent that speaks as Xiaoyou. With the default setup, `@claude …`
is answered by `claude` in a single step and in Xiaoyou's voice; it is told
that it was the one named, and that turn is not passed on to another helper.

The router is the replaceable part. `mention` adds nothing beyond rules 1, 2
and 4. `command` runs a command of yours: standard input is
`{"text", "default", "agents": [{"name", "type", "description"}]}` and the first
line of standard output is the chosen agent's name. Empty output, an unknown
name, an error or a timeout all fall back to the default agent: a broken router
must not leave the owner unable to talk. A small model or a classifier that
decides routing plugs in here.

### Handing work over and reporting back

When the agent that takes the message can answer as Xiaoyou, the runtime tells
it which helpers exist (the other agents' names and descriptions). It may answer
directly, or say in its reply who should do what. The runtime then runs that
helper, gives the raw result back, and the agent sums it up for the owner in
Xiaoyou's words. A helper that failed, or a helper that does not exist, is
reported back just the same, to be told to the owner as it is. A turn allows at
most `max_handoffs` hand-overs; after the last one the option is no longer
offered.

When the owner names an agent that only does work, it runs first (with the
recent turns it has not seen as background) and `voice_agent` reports the
result. If `voice_agent` is unavailable at that moment, the owner gets the raw
result rather than nothing.

A helper receives the task only, without the persona or the chat. It has a
session of its own in that conversation and continues it next time.

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

All requests except `/healthz` need `Authorization: Bearer <token>`. Bodies and
responses are JSON.

| Request | Result |
| --- | --- |
| `GET /healthz` | `{"ok": true, "version", "backend", "name"}`; no token needed. `backend` is the default agent's type. |
| `GET /v1/agents` | `{"default", "agents": [{"name", "type", "description", "speaks", "default"}]}`: the agents Xiaoyou can use on this runtime. |
| `POST /v1/messages` with `{"text", "conversation"?, "client_id"?, "agent"?}` | `202` and the message record, status `queued`. `agent` sends the message to that agent; `400` if there is no such agent. |
| `POST /v1/voice?conversation=<name>&client_id=<id>&agent=<agent>` with a WAV file as the body | `202` and the message record, `kind` `voice`, empty `text`. `400` if the recording is not acceptable or no engine is configured. |
| `GET /v1/messages/<id>?wait=<seconds>&rev=<n>` | The message record. With `wait` (up to 60) the call returns as soon as the turn finishes. With `rev` as well (the `rev` of the record the caller already has) it returns as soon as anything in the record changes, which is how a client follows a turn step by step. |
| `POST /v1/conversations/<name>/history` with `{"turns": [{"id", "text", "reply", "at"?}]}` | `{"accepted": n}`: how many of the turns (at most 30) this runtime did not know. They are told to the agent that takes the next message. |
| `POST /v1/conversations/<name>/reset` | Starts that conversation over: every agent's session is forgotten and the transcript is cleared. |

A message record has `id`, `client_id`, `conversation`, `kind` (`text` or
`voice`), `status` (`queued`, `transcribing`, `running`, `done`, `failed`),
`text`, `reply`, `brief`, `mood` (`idle`, `busy`, `ask`, `happy`, `oops`),
`error`, `created_at`, and `finished_at`. For a voice message `text` is empty
until the recording has been transcribed.

The record also says who the turn went to:

- `asked`: the agent the caller named, or `null`.
- `agent`: while running, the agent working on it now; afterwards, the agent
  that took the message.
- `stage`: while running, one sentence for a person to read, such as what
  Xiaoyou said when handing over; `null` when nothing was handed over or the
  turn is finished.
- `helper`: while Xiaoyou waits for an agent she handed the work to, that
  agent's name; otherwise `null`. Unlike `agent`, it is only set for a handoff.
- `rev`: a counter that goes up every time the record changes.
- `events`: the steps of the turn, each `{"at", "kind", "agent", "text"}`.
  `kind` is `route` (who got it; `text` is the reason: `asked`, `mention`,
  `router`, `default`), `handoff` (handed to a helper), `result` (the helper
  finished or failed), or `note`.

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

## How a turn works

1. Messages are queued per conversation: one at a time within a conversation,
   and conversations do not wait for each other.
2. The agent that takes the message is chosen in the order given under
   [Who gets a message](#who-gets-a-message).
3. If that agent can answer as Xiaoyou, it gets the persona, the reply format
   and the list of helpers. For Claude Code that is `claude -p --output-format
   json --append-system-prompt <persona + helpers> --json-schema <reply, brief,
   mood, optional handoff> --permission-mode <mode> [--allowedTools ...]
   [--model ...] [--resume <session>]`. The owner's text is sent on standard
   input, never as a command-line argument. If it hands work over, see
   [Handing work over and reporting back](#handing-work-over-and-reporting-back).
4. If that agent only does work, it runs, and the agent that speaks reports the
   result.
5. The session identifier each agent returns is stored per conversation and
   agent in `state/sessions.json` and used again next time.
6. The turn is added to Xiaoyou's own transcript, `state/transcript.json`,
   together with which agents witnessed it. When an agent that did not witness
   some turns takes a later message, it is told about them first, once.

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

Verified by `runtime/tests/test_runtime.py` (run by `./tools/validate.sh
--static`): configuration checks and the old configuration shape; the exact
command line and standard input given to fake `claude` and `codex` executables,
result and error parsing, session continuation; naming an agent and the order
of routing decisions, a command as the router; Xiaoyou handing work over, a
helper that fails or does not exist, the limit on hand-overs, reporting back
after a named work-only agent, catching an agent up on turns it missed; the
`remote` agent between two real runtimes (with `echo`), including two that list
each other; queueing per conversation, retry by `client_id`, token checks, the
HTTP interface; and voice messages with a fake recognition command (format
checks, transcription, empty and failed recognition, clean-up of recordings).

Checked by hand on 2026-10-09 with Claude Code 2.1.295 on Linux, in a cloud
workspace rather than on the intended computer, with one `claude_code` agent
and a stand-in helper of type `command`:

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

Not verified:

- Windows: nothing in this directory has been run on Windows.
- Voice from a real device: microphone quality, Bluetooth throughput, and
  recognition of real speech in a real room.
- sherpa-onnx on macOS.

- The real Codex command line. The `codex` agent was written against codex-cli
  0.162.0: the options come from its help output and the event format from a
  run that was not logged in and could not reach the service; no turn has
  completed for real. The hand-over check used a stand-in script.
- The `remote` agent against another real computer over a real network.
- A local model: a `command` agent answering as Xiaoyou has only been
  exercised with a script in the tests.
- A virtual machine, and a long-running service over days.
- Version 0.4 on macOS and together with the phone app. The owner reports that
  version 0.2 ran the path "phone app → runtime → device" on macOS; this
  version changes the internal structure and has not run there. The phone app
  does not show `stage` or `agent` yet and cannot name an agent.
- How the model behaves when a helper is slow or returns a large output.

Known risks:

- This relies on running Claude Code non-interactively with a subscription
  login. Claude Code documents a `--bare` mode that skips that login and states
  it is intended to become the default for `-p`; if that happens the
  `claude_code` agent needs another way to authenticate. Check the current Claude Code terms before
  offering this to anyone other than yourself.
- Turns count against the usage limits of the logged-in account. Each
  hand-over costs one more call to the agent that speaks, for the summary.
- A long task is not a background task: the conversation waits for as long as
  the helper runs, and it cannot be cancelled meanwhile.
- One shared token, no per-device identity, no rate limiting.

## Tests

```bash
python3 runtime/tests/test_runtime.py
```
