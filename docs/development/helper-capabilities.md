<p align="right">
  <a href="helper-capabilities.zh_CN.md">简体中文</a> · <strong>English</strong>
</p>

# Helper capabilities: MCP and skills

Status: 2026-10-24. Phase 1 (Runtime) is done in source (`capabilities.py`, `mcp_client.py`, `permission_mcp.py`, `helper_extras.py`, tested by `test_helper_capabilities`) and not deployed; phase 2 onward is not done. pi's flags were checked against its documentation only: pi is not installed on this computer and was never actually run.

Xiaoyou manages which external tools the helpers (Claude Code, Codex, dsh, pi) can use. The approach follows [Raft](https://raft.build/)'s managed MCP ([MCP Apps docs](https://github.com/botiverse/raft-docs/blob/main/content/features/apps/mcp/index.md)), adapted to our structure of one Runtime per computer with helpers running locally, and is not copied.

## 1. What we take from Raft

| Raft | Us |
| --- | --- |
| An MCP server is registered once and shared by the whole space; its tools become available to agents | Each computer has one **capability library**; a server is registered once and shared by the helpers on that computer |
| A "connection" is the account or key the server uses; Raft keeps it and **agents never see it** | Connections stay on this computer (a file only the local account can read), are not synced and are never given to helpers |
| After registration every agent can use it by default; access can be revoked or the connection changed per agent | Every helper on this computer can use it by default; access can be revoked per helper or limited to some tools |
| The local service injects one local MCP proxy into every runtime; tool names carry a server prefix | Xiaoyou injects **one** MCP server (the capability gateway) into each helper, listing the tools it is granted, with prefixed names |
| Every call carries a configuration version and an assignment version and is refused on mismatch | Every call is checked against the current grant; revocations and configuration changes take effect immediately |
| Unusable servers are listed too, so the agent can say "X needs reconnecting" | Unavailable servers are listed with the reason; Xiaoyou tells the owner in the conversation |
| At most 200 tools, results over 512 KB refused, a 20-second timeout per call | Similar limits, see section 4 |
| Skills stay on the runtime side; Raft only discovers and shows them | The same: discover and show the skills each harness already has; helpers may also be given skill directories |

**Differences:** Raft supports only remote (Streamable HTTP) MCP; we run locally and must also support MCP servers started as local commands (stdio). Raft's agents collaborate with each other; to the owner we have one voice, Xiaoyou (product principle P1), and collaboration between helpers is deferred.

## 2. Structure

```
Capability library (one per computer, an origin record)
  servers: id, name, kind (local command / network address), command and args or URL,
           which headers or environment variables it needs, enabled, configuration version
  connections: the header or environment values -> only in state/capability-secrets/ on this
               computer, mode 0600, never synced, never shown
  grants: helper -> enabled, allowed tools (empty = all), grant version
        |
        v
The capability gateway's Runtime end (loopback, the same server as the approval gate)
  GET  /mcp/tools  with this turn's key -> tools this helper may use + unavailable servers
  POST /mcp/call   with key, tool name, arguments, expected versions -> check the grant ->
                   call the real server on the helper's behalf
        |
        v
The capability gateway's entry, injected into each turn (python -m xiaoyou_runtime.permission_mcp, stdio)
  Claude Code: --mcp-config (together with the approve permission tool)
  Codex: -c mcp_servers.xiaoyou.command / args / env
  dsh: mcpServers in ACP session/new
  pi: only global `pi mcp add`; not injected yet (to be checked on a computer with pi installed:
      whether pi passes the environment to MCP servers)
```

Each turn gets its own key, revoked when the turn ends; the helper holds only the key, never the connection. Other computers can see which servers this computer's library has (without connections), to answer questions such as "can the Mac's helpers search Notion"; they cannot call this computer's servers.

## 3. Tool names and versions

- Tool name seen by a helper: `<server id>__<tool name>`, letters, digits, underscore and hyphen only, at most 64 characters; a short hash is added on collision.
- Server configuration version: incremented when the command, address, header names or enabled state change; the cached tool list is invalidated at the same time.
- Grant version: incremented whenever this helper's grant for this server changes.
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
- API key: written only to `state/helper-secrets/<name>.key` (readable by this account only); the helper reads it through the provider's own environment variable, and the key is never returned to clients. Codex supports only a subscription login for now, dsh only a key.
- Subscription login: uses this harness's own login on this computer; the endpoint records the choice and does not log in on anyone's behalf.
- Capability grants default to all for a new helper, as for the others.
- Not done: syncing the helper list across computers (origin records, phase 2) and starting a login from the UI.
