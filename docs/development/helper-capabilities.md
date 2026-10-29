<p align="right">
  <a href="helper-capabilities.zh_CN.md">简体中文</a> · <strong>English</strong>
</p>

# Helper capabilities: MCP and skills

Status: 2026-10-24, source changes only. The capability gateway, desktop helper registration, per-helper grant editor and Magpie model-service bindings are implemented. MCP server registration, connection editing, OAuth and cross-computer catalog replication remain pending. pi is not installed on this computer; its adapter has host tests only.

Xiaoyu manages which external tools the helpers (Claude Code, Codex, dsh, pi) can use. The approach follows [Raft](https://raft.build/)'s managed MCP ([MCP Apps docs](https://github.com/botiverse/raft-docs/blob/main/content/features/apps/mcp/index.md)), adapted to our structure of one Runtime per computer with helpers running locally, and is not copied.

## 1. What we take from Raft

| Raft | Us |
| --- | --- |
| An MCP server is registered once and shared by the whole space; its tools become available to agents | Each computer has one **capability library**; a server is registered once and shared by the helpers on that computer |
| A "connection" is the account or key the server uses; Raft keeps it and **agents never see it** | Connections stay on this computer (a file only the local account can read), are not synced and are never given to helpers |
| After registration every agent can use it by default; access can be revoked or the connection changed per agent | Every helper on this computer can use it by default; access can be revoked per helper or limited to some tools |
| The local service injects one local MCP proxy into every runtime; tool names carry a server prefix | Xiaoyu injects **one** MCP server (the capability gateway) into each helper, listing the tools it is granted, with prefixed names |
| Every call carries a configuration version and an assignment version and is refused on mismatch | Every call checks the current grant and versions; UI grant edits and connection-file rotation affect the next call, while config.json changes require a Runtime restart |
| Unusable servers are listed too, so the agent can say "X needs reconnecting" | Unavailable servers are listed with the reason; Xiaoyu tells the owner in the conversation |
| At most 200 tools, results over 512 KB refused, a 20-second timeout per call | Similar limits, see section 4 |
| Skills stay on the runtime side; Raft only discovers and shows them | The same: discover and show the skills each harness already has; helpers may also be given skill directories |

**Differences:** Raft supports only remote (Streamable HTTP) MCP; we run locally and must also support MCP servers started as local commands (stdio). Raft's agents collaborate with each other; to the owner we have one voice, Xiaoyu (product principle P1), and collaboration between helpers is deferred.

## 2. Structure

```
Capability library (one per computer, an origin record)
  servers: id, name, kind (local command / network address), command and args or URL,
           which headers or environment variables it needs, enabled, configuration version
  connections: the header or environment values -> only in state/capability-secrets/ on this
               computer, mode 0600, never synced, never shown
  grants: helper -> enabled, allowed tools (omitted = all, [] = none), grant version
        |
        v
The capability gateway's Runtime end (loopback, the same server as the approval gate)
  GET  /mcp/tools  with this turn's key -> tools this helper may use + unavailable servers
  POST /mcp/call   with key, tool name, arguments, expected versions -> check the grant ->
                   call the real server on the helper's behalf
        |
        v
The capability gateway's entry, injected into each turn (python -m xiaoyu_runtime.permission_mcp, stdio)
  Claude Code: --mcp-config (together with the approve permission tool)
  Codex: -c mcp_servers.xiaoyu.command / args / env
  dsh: mcpServers in ACP session/new
  pi: only global `pi mcp add`; not injected yet (to be checked on a computer with pi installed:
      whether pi passes the environment to MCP servers)
```

Each turn gets its own key, revoked when the turn ends; the helper holds only the key, never the connection. Other computers can see which servers this computer's library has (without connections), to answer questions such as "can the Mac's helpers search Notion"; they cannot call this computer's servers.

## 3. Tool names and versions

- Tool name seen by a helper: `<server id>__<tool name>`, letters, digits, underscore and hyphen only, at most 64 characters; a short hash is added on collision.
- Server configuration version: a digest of the startup configuration and connection-file metadata. Rotating a connection file invalidates the catalog and reconnects on the next discovery. Changes to config.json are loaded at restart.
- Grant version: includes a persisted revision for UI-managed helpers. Revoke-then-regrant never restores an older version; stale callers must list tools again.
- A call carries both versions as they were when the tools were listed; on mismatch it is refused and the helper is told to list tools again. A helper already running is refused on its next call after a revocation instead of continuing with the old grant.

## 4. Limits

| Item | Limit |
| --- | --- |
| Servers per computer | 32 |
| Tools per server | 200 (more is not registered) |
| Result of one call | 512 KB (over that, the helper gets an error) |
| Time of one call | 30 seconds |
| Local command servers | at most one resident process per server, exiting after 10 idle minutes and restarted on the next call |

## 5. Phases

| Phase | Content |
| --- | --- |
| 1 (Runtime) | Capability library (read from config.json first), connection files, grants and versions; a minimal MCP client (stdio and Streamable HTTP); the two gateway endpoints; capability gateway entry injected into Claude Code, Codex and dsh; `--skill` for pi |
| 2 | Endpoints to add or remove servers, change grants and write connections (only on the origin computer); the library synced to other computers as origin records (without connections) |
| 3 | Desktop client UI; OAuth sign-in; the "needs reconnecting" authorization card |
| 4 | pi injection (after checking); sharing capabilities between helpers if needed |

The earlier approach, where each helper's configuration carried MCP commands and secrets that were passed to the harness as is, is withdrawn: secrets would reach the helper's process, and revocation could not take effect immediately. Per-helper skill directories (`skills`) stay.

## 6. Adding helpers

Desktop client Settings › Helpers › Add helper: pick a harness (pi, dsh, Codex, Claude Code), a model, then how to reach the model. Following Raft: helpers are added by people; a helper cannot add helpers or grant itself capabilities.

- Endpoints: `GET /v1/helpers/options`, `POST /v1/helpers`, `POST /v1/helpers/<name>/remove` (see the [Runtime README](../../runtime/README.md)).
- Added helpers are kept in `state/helpers.json`, validated by the same rules as helpers in `config.json`, usable at once and kept across restarts.
- Model service (the default): select a model from the local Magpie catalog. Magpie manages providers, API keys, subscriptions and protocol translation. The helper receives only a gateway credential. See section 7.
- Direct API key: written to `state/helper-secrets/<name>.key`, never returned to clients, and passed through the chosen environment variable. An environment variable alone does not adapt protocols or make an unsupported provider work. dsh has no native subscription login.
- Subscription login: uses this harness's own login on this computer; the endpoint records the choice and does not log in on anyone's behalf.
- New helpers default to all capabilities. Settings offers per-server grants; `POST /v1/helpers/<name>/capabilities` also accepts tool allowlists. Changes are saved before becoming effective and checked on every call, including after waiting for a connection. A persisted revision prevents revoke-then-regrant from restoring an old version. In-flight external effects are not undone.
- Not done: syncing the helper list across computers (origin records, phase 2) and starting a login from the UI.

## 7. Magpie model service and lifecycle hardening

The reference is [yetone/magpie](https://github.com/yetone/magpie), snapshot
`f3eb92924e56605860f6a85a3449247c26c0bd90`. Its MIT notice is retained at
`runtime/third_party/magpie.LICENSE`. `helper_models.py` adapts configuration
shapes from `internal/agent/codex.go`, `dsh.go` and `agents.go`; the actual
protocol translation engine remains Magpie. Raft source snapshot
`db5e399f017d584e49597e3a28757645d6807893` was read for version checking,
catalog validation and connection lifecycle ideas; its FSL code is not vendored.

Run Magpie on this computer, add providers or subscription accounts there, then
choose Model service in Add helper. Read models from `http://127.0.0.1:3425`
(an explicit loopback address on another port works too). Supply a gateway
credential if required; no vendor key is needed here. Adding rechecks the live
list: a removed model or unavailable gateway is an error.

`POST /v1/helpers/models` accepts `{gateway_url, key?}` and returns `{url, models}`.
`POST /v1/helpers` accepts `access: "magpie"`, `gateway_url`, `model` and an optional
gateway `key`. Discovery has a three-second request timeout and a 1 MB response
limit, bypasses environment proxies, rejects redirects and permits only literal
loopback HTTP addresses. This is a catalog check, not an inference/TTFT test.

| Harness | Runtime-owned binding |
| --- | --- |
| Claude Code | Private config directory, `ANTHROPIC_BASE_URL`, gateway key file |
| Codex | Per-launch `-c` provider table, Responses wire API and gateway key environment reference; exec and app-server |
| dsh >= 0.1.5 | Per-launch llm-pi-ai custom-provider patch, the model's native wire API and matching ACP provider; direct speaking uses the same gateway |
| pi | Private `PI_CODING_AGENT_DIR/models.json` provider, native wire API and scoped model ID |

Native logins and direct-key helpers remain supported. User configuration is
not rewritten. This increment connects an already running Magpie; it does not
install it, copy subscription accounts, create a second provider registry or
enable fallback groups. Real inference, tool translation and pi process
compatibility require acceptance with the installed versions.

Validation: official Magpie `v0.1.1345` (asset SHA-256
`793b9c6d78c9e4fef15f49b131f1bbd98127601d37ae790e19afca657d8ea98c`)
ran with isolated portable data and account discovery disabled. A local fixture
model was listed and answered successfully through Chat Completions, Messages
and Responses. Four helper bindings were created. This verifies gateway protocol
translation with a fixture, not real-provider or full tool-loop acceptance.
Installed Codex 0.163.0 (exec and app-server) and dsh 0.2.0-rc.2 (ACP) also
returned the fixture reply through Magpie. An initial cold Codex exec probe
timed out at 12 seconds; subsequent probes succeeded. No rendered TTFT claim
is made. The model's live reasoning levels determine the selectable efforts;
unknown levels stay unset, including dsh's plain-speaking path.

MCP connection creation is serialized per server, without holding a library-wide
I/O lock. Failed initialization closes its process. HTTP events are parsed as
they arrive; the matching result returns without waiting for EOF, while
heartbeat-only streams have a total deadline. Helper deletion saves its registry
before removing the live agent, so a failed save leaves both intact.
