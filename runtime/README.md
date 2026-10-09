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

Every request the owner makes is **one thing** with a card of its own: what he
said, what Xiaoyou answered and anything added later are kept on that card.
Xiaoyou herself only does what is quick: she understands the sentence, answers
at once when she can, and hands anything that takes time to a helper working in
the background. So an unfinished thing does not hold up the next sentence,
several things can be in progress at once, and a thing can be added to, changed
or cancelled while it runs. Whatever Xiaoyou says comes as a full reply, a
short brief for the Passport screen, and a mood for the pixel pet. A message
can be typed text or a voice recording; a recording is first turned into text
by a speech recognition engine chosen in the configuration.

```text
phone app / any client ──HTTP──> Xiaoyou Runtime
                                   │  persona · transcript · cards · routing · reporting back
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
| `state_dir` | `state` | Where the session table, the cards and Xiaoyou's transcript are kept. |
| `persona_file` | `persona.txt` | Xiaoyou's persona. Given only to an agent that answers as Xiaoyou. |
| `brief_max_chars` | `120` | Upper bound for the small-screen brief (20 to 400). |
| `turn_timeout_seconds` | `3600` | How long one thing in the background may take; used by agents that do not set their own `timeout_seconds` (10 to 7200). |
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

Environment variables override the file, so a container or virtual machine can
be configured without editing it: `XIAOYOU_CONFIG`, `XIAOYOU_HOST`,
`XIAOYOU_NAME`, `XIAOYOU_PORT`, `XIAOYOU_TOKEN`, `XIAOYOU_STATE_DIR`,
`XIAOYOU_DEFAULT_AGENT`, `XIAOYOU_CODEX_CONFIG_DIR` (applied to every `codex`
agent), and `XIAOYOU_CLAUDE_CONFIG_DIR` (applied to every
`claude_code` agent).

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
| `type` | none | `claude_code`, `codex`, `command`, `remote`, or `echo`. |
| `enabled` | `true` | `false` makes the runtime treat it as absent. |
| `description` | empty | One sentence on what it is good at. Xiaoyou reads it to decide whether to hand work over, and so does the router. |
| `aliases` | `[]` | Other names it answers to when named in a message, for example how speech recognition tends to spell it. |
| `speaks` | by type | Whether it may answer as Xiaoyou. `claude_code`, `remote` and `echo` do by default; `codex` and `command` only do work. |
| `timeout_seconds` | `turn_timeout_seconds` | How long one run of this agent may take. |

Keys by type:

| Type | What it does | Keys |
| --- | --- | --- |
| `claude_code` | Runs the Claude Code command line non-interactively, with the login already present on this machine. | `command` (default `["claude"]`), `workdir` (default `workdir`), `config_dir`, `model`, `permission_mode` (default `manual`), `allowed_tools`, `add_dirs` (default `["~"]`), `extra_args`, `env` |
| `codex` | Runs the Codex command line. By default in its app-server mode (`codex app-server`): an operation that needs confirmation is put to the owner, and a sentence can be added while it works. With `mode` set to `exec` it uses `codex exec` (and `codex exec resume` to continue): one run to the end, and it never asks. | `command` (default `["codex"]`), `workdir`, `config_dir`, `mode` (default `app_server`), `approval_policy` (default `on-request`), `sandbox` (default `read-only`), `model`, `extra_args` |
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
`state/cards.json`, the latest 50. Things that were in progress when the
runtime restarts cannot be continued; they are marked `failed` with a note
saying the runtime was restarted.

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
| `POST /v1/messages` with `{"text", "conversation"?, "client_id"?, "agent"?, "card"?}` | `202` and the message record, status `queued`. `agent` sends the message to that agent; `400` if there is no such agent. `card` is the number of the thing on the owner's screen when he said it. |
| `POST /v1/voice?conversation=<name>&client_id=<id>&agent=<agent>&card=<number>` with a WAV file as the body | `202` and the message record, `kind` `voice`, empty `text`. `400` if the recording is not acceptable or no engine is configured. |
| `GET /v1/messages/<id>?wait=<seconds>&rev=<n>` | The message record. With `wait` (up to 60) the call returns as soon as Xiaoyou has dealt with the sentence. With `rev` as well (the `rev` of the record the caller already has) it returns as soon as anything in the record changes. |
| `GET /v1/feed?conversation=<name>&after=<seq>&wait=<seconds>` | `{"seq", "cards": [...], "approvals": [...]}`: the cards of that conversation, in full, whose sequence number is above `after`, and every approval of that conversation still waiting for an answer (`{"id", "card", "conversation", "agent", "tool", "detail", "created_at"}`). An approval appearing or being answered changes its card, so waiting for cards is waiting for approvals too. With `wait` (up to 60) the call waits while nothing has changed and returns as soon as something does. A client keeps `seq` and sends it as `after` next time; a `seq` lower than the one it holds means the runtime's records were replaced, and it starts again from 0. |
| `GET /v1/cards?conversation=<name>` | `{"cards": [...]}`: the latest 30 cards. |
| `POST /v1/cards/<number>/cancel` | Cancels that thing and returns the card; a thing that is no longer in progress is returned unchanged. `404` if there is no such card. |
| `POST /v1/approvals/<number>` with `{"decision": "allow" or "deny"}` | Answers an approval. `404` if there is no such approval (or the runtime was restarted); `409` if it has been answered already or its thing has stopped. |
| `POST /v1/conversations/<name>/history` with `{"turns": [{"id", "text", "reply", "at"?}]}` | `{"accepted": n}`: how many of the turns (at most 30) this runtime did not know. They are told to the agent that takes the next message. |
| `POST /v1/conversations/<name>/reset` | Starts that conversation over: every agent's session is forgotten and the transcript is cleared. |

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

A card has `id` (`c1`, `c2`, …), `conversation`, `title` (at most 24
characters), `state`, `agent` (who is or was on it; `null` when Xiaoyou
answered herself), `entries` (`[{"role": "you" or "xiaoyou", "text", "at"}]`,
the latest 40), `brief`, `mood`, `progress` (the latest 5 lines of progress,
starting over with each round), `started_at`, `edits` (how many times it was
added to or changed), `approval` (the number of the approval it is waiting
for, or `null`), `queued`
(waiting its turn under a limit on parallel things), `created_at`,
`updated_at`, and `seq`.

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

## How a sentence is processed

1. Messages queue per conversation: one sentence at a time within a
   conversation, and conversations do not wait for each other.
2. The order in [Who gets a message](#who-gets-a-message) says whether it was
   decided who does this. If so, a card is opened, the work goes to the
   background, and the sentence has been dealt with.
3. Otherwise the agent that speaks as Xiaoyou takes it. For Claude Code that is
   `claude -p --output-format json --append-system-prompt <persona + helpers +
   the things at the moment> --json-schema <reply, brief, mood, card, action>
   --tools "" --permission-mode dontAsk [--model ...] [--resume <session>]`.
   The owner's text is sent on standard input, never as a command-line
   argument. The runtime does what the `action` in her reply says; see
   [One thing, one card](#one-thing-one-card).
4. A thing in the background: for Claude Code that is `claude -p
   --output-format stream-json --verbose [--append-system-prompt <persona>
   --json-schema <reply, brief, mood>] --permission-mode <mode>
   --permission-prompt-tool mcp__xiaoyou__approve --mcp-config <a temporary
   file in state> --add-dir <directory> [--allowedTools ...] [--model ...]
   [--resume <the session of this thing>]`. It still starts in `workdir`. The
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

Not verified:

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
- Starting a conversation over (reset) neither stops things in progress nor
  removes cards.
- One shared token, no per-device identity, no rate limiting.

## Tests

```bash
python3 runtime/tests/test_runtime.py
```
