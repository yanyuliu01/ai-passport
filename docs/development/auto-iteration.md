<p align="right">
  <a href="auto-iteration.zh_CN.md">简体中文</a> · <strong>English</strong>
</p>

# Auto-iteration pipeline (design draft)

> Status: 2026-10-19. **Implemented**: the plugin framework, app usage event reporting, four sources, six rule detectors, triage, the model gateway and the `template_plan` planner (P0, P1 and most of P2). **Not yet**: code changes, gates, release, observation (P3, P4). Each section says what is current code and what is still design.

## In one sentence

The runtime keeps collecting usage signals from the app. Once enough have accumulated, a **local model** finds a problem, writes an iteration plan, changes the app code and passes the gates. Low-risk changes are published to the phone automatically; higher-risk ones wait for one tap from the owner. After release the runtime keeps watching and rolls back automatically if the problem is not fixed or things got worse. Every stage is a pluggable plugin.

## Premises

1. **Weak model, heavy harness.** The pipeline does not rely on agents such as CC or Codex that plan on their own and read the whole repository. The model does three narrow jobs: classify a piece of text, fill in a plan template, and write a small patch from context it is given. Finding files, assembling context, validating formats, building, testing, judging risk, publishing and rolling back are deterministic runtime code.
2. **Everything is pluggable.** Sources, detectors, planning, implementation, verification, release, observation and the model backend are plugins that configuration enables, orders and replaces. Removing a plugin degrades the pipeline instead of breaking it (without an implementer it only produces plan reports).
3. **Built on existing records.** Conversations and tasks still follow the [unified message model](unified-message-model.md); there is no second source of truth for conversations. Releases use the existing `app publish`; anything that needs the owner goes through the existing approvals / quick review.
4. **The [cross-stack reliability rules](cross-stack-reliability.md) still apply.** An automatically published version must also preserve working features, the 3-second first response and multi-end consistency. The gates check for this; a version that fails is not published.

## Pipeline

```text
 ┌──────────── Collect (continuous, background) ────────────┐
 │ app events · complaints/corrections in chat · failed cards · sync/update failures · latency │
 └──────────────────┬───────────────────────┘
                    ↓ appended to state/iterate/signals/*.jsonl
 Detect (mostly rules) → Issue (fingerprint dedup, count, severity, evidence IDs)
                    ↓ batch threshold / schedule / owner request
 Triage: score and pick ONE issue for this run (one fix per run)
                    ↓
 Plan: model fills a template (goal, where, acceptance, risk self-check) → harness validates
                    ↓
 Implement: in a separate git worktree, model patches file by file → apply → build → feed errors back → bounded retries
                    ↓
 Gates: unit tests · build · static repo checks · diff scope/size · Chinese text/resources · replay cases
                    ↓
 Policy: risk tier → auto-publish / one-tap owner approval / report only
                    ↓
 Release: bump version → app publish (local only by default) → record source commit
                    ↓
 Observe: compare signals with the same fingerprint N hours after release → close / roll back / requeue
```

Each pass is a **Run** with its own ID, state machine and directory `state/iterate/runs/<run>/`, holding the input snapshot, plan, patch, gate logs and conclusion. A run also appears in the app as a read-only card (like an external report card) showing what was found, what changed, whether it shipped and how to undo it.

Run states: `collected → triaged → planned → implemented → verified → (awaiting_owner) → released → observing → closed | rolled_back | abandoned`. A failing stage stops the run there with a reason; the next run starts from scratch and never resumes a half-finished one.

## Plugin model

### Kinds

| `kind` | Input → output | Default plugins |
| --- | --- | --- |
| `source` | external data → `Signal` | `app_events`, `chat_feedback`, `card_failures`, `sync_health`, `update_health` |
| `detector` | `Signal[]` → `Issue[]` | `crash`, `error_rate`, `latency_3s`, `rage_tap`, `dead_end`, `complaint_cluster` |
| `triage` | `Issue[]` → selected `Issue` | `score_by_impact` |
| `planner` | `Issue` + code context → `Plan` | `template_plan` |
| `context` | `Plan` → relevant file excerpts | `java_symbol_index`, `layout_resource_index` |
| `implementer` | `Plan` + context → `Patch` | `search_replace_loop` |
| `gate` | worktree → pass/fail + evidence | `gradle_unit`, `gradle_assemble`, `validate_static`, `diff_scope`, `strings_zh`, `replay` |
| `policy` | `Plan` + `Patch` + gate results → tier and action | `risk_tiers` |
| `releaser` | build output → release record | `app_publish_local` |
| `observer` | release record + new `Signal` → verdict | `fingerprint_compare` |

### What a plugin looks like

Built-in plugins live in `runtime/xiaoyou_runtime/iterate/builtin/`; your own go in directories listed in `iterate.plugin_dirs`, each `.py` file holding a `PLUGINS` list. A plugin with the same kind and name loaded later replaces the earlier one, so your own can replace built-ins; a file that fails to load records one error and does not affect other plugins:

```python
def detect(ctx, signals):              # ctx opens only the permissions this plugin declared
    ...
    return [Issue(fingerprint=..., title=..., severity=..., evidence=[...])]

PLUGINS = [{
    "name": "rage_tap", "kind": "detector", "version": 1,
    "consumes": ["app.rage_tap"],      # only these signal types; a trailing . means prefix
    "permissions": [],                 # see below
    "run": detect,
}]
```

How `run` is called per kind: source `run(ctx, cursor) -> (signals, new_cursor)`; detector `run(ctx, signals) -> [Issue]`; triage `run(ctx, issues) -> Issue | None`; planner `run(ctx, issue) -> plan`. Raising `Skip` means "cannot do it this time" (for example no model available) and is not an error.

Plugins talk only through data objects (`Signal`, `Issue`, `Plan`, `Patch`, `GateResult`, `Release`): versioned JSON saved in the run directory. Any stage can therefore be rerun alone, compared against another plugin, or replayed in tests from saved inputs.

**Permissions** are declared and checked by `ctx`: `read_state` (state directory), `read_repo` (repository path), `model` (model gateway), plus `write_worktree`, `git_commit`, `publish` and `network` for later stages. Anything undeclared raises `PermissionDenied`. Built-in detectors have no permissions; no plugin can `git push`. Plugins run in the runtime's process, so this guards against accidental overreach and is not a security sandbox: do not put plugins of unknown origin in `plugin_dirs`.

### Configuration

This is what the current code accepts; every value shown is the default (the whole `iterate` section may be omitted; it then just never runs automatically):

```json
"iterate": {
  "enabled": false,
  "trigger": {"min_new_signals": 50, "interval_hours": 24, "only_when_idle": true},
  "pipeline": {
    "source":   ["app_events", "chat_feedback", "card_failures", "reply_latency"],
    "detector": ["crash", "app_errors", "rage_tap", "slow_first_reply", "complaint_cluster", "task_failures"],
    "triage": "score_by_impact",
    "planner": "template_plan",
    "implementer": null
  },
  "plugin_dirs": [],
  "areas": ["app"],
  "allow": ["companion/android/app/src/main/**"],
  "deny": ["**/AndroidManifest.xml", "**/build.gradle"],
  "repo_dir": "..",
  "min_issue_count": 3, "window_days": 7, "retention_days": 30, "votes": 3, "max_classify": 40
}
```

Removing a plugin from a list unplugs it; `"planner": null` only finds issues and writes no plan. `areas` decides which kinds of issues are picked for change (`app`, `runtime`, `agent`); issues in other areas still go into the issue book. `python3 -m xiaoyou_runtime iterate plugins` lists installed plugins, which are enabled, and their permissions. With `enabled` true the runtime checks every 15 minutes: if the owner has nothing running it starts a subprocess for `iterate run`, which itself decides whether enough signals or time have accumulated.

## Model gateway (shared with the agent gateway)

The goal is cost: **use a local model whenever possible**; network models are only a fallback with a daily spending cap. This layer is not specific to auto-iteration: `model_gateway.py` serves every place in the runtime that calls a model directly.

It is the **Provider/model** layer of the agent gateway proposal (`docs/development/agent-gateway.md`, written in another session and not yet committed). The agent gateway manages how harnesses/CLIs with their own tool loop (Claude Code, Codex, DSH) are installed, probed and enabled; the model gateway manages where inference endpoints are, where their credentials are, which candidates each purpose uses in what order, and what was spent. Both share one endpoint registry:

- Calls where the harness is heavy and the model does narrow jobs, like auto-iteration, use the gateway's `complete()` / `complete_json()` directly.
- Harnesses with their own tool loop get environment variables pointing at the same endpoint from `binding_env(provider)` (`OPENAI_BASE_URL`/`OPENAI_API_KEY` for the OpenAI protocol, `ANTHROPIC_BASE_URL`/`ANTHROPIC_AUTH_TOKEN` for Anthropic). Today's `claude_gateway.py` is one such binding written by hand (Claude Code on DeepSeek); later an agent instance only needs `"provider": "<name>"` instead of its own address and key. **This is not wired yet**: it touches agent configuration in `agents.py` and `config.py` and should land together with the agent gateway's registry.

```json
"models": {
  "providers": {
    "local":    {"protocol": "openai", "base_url": "http://127.0.0.1:11434/v1"},
    "deepseek": {"protocol": "openai", "base_url": "https://api.deepseek.com/v1",
                 "key_file": "~/.config/xiaoyou/deepseek.key", "price": {"input": 2, "output": 8}}
  },
  "routes": {
    "default":          ["local/qwen2.5:7b"],
    "iterate.classify": ["local/qwen2.5:7b"],
    "iterate.plan":     ["local/qwen2.5-coder:14b", "deepseek/deepseek-v4-flash"]
  },
  "network": {"allowed": false, "daily_budget": 2}
}
```

- **Protocols**: `openai` (`/chat/completions`, understood by Ollama, llama.cpp server, LM Studio, vLLM, DeepSeek and OpenAI) and `anthropic` (`/v1/messages`).
- **Local or network**: without `locality` it is inferred from the address; loopback, private and Tailscale (100.64/10) addresses are local. Local endpoints cost nothing; a network endpoint is chosen only when `network.allowed` is true and today's spending is below `daily_budget`.
- **Routing**: an unknown purpose falls back to its parent (`iterate.plan` → `iterate` → `default`); candidates are tried in order, and a local endpoint that refused a connection is skipped for 60 seconds.
- **JSON**: `complete_json` extracts the JSON from the answer (small models often wrap it in \`\`\`json) and passes it to the caller's check; if it fails, the problem goes back to the model, with bounded retries.
- **Usage**: each call appends one line to `state/models/usage.jsonl`: purpose, endpoint, model, tokens, estimated cost, duration, success. No prompts or answers. The price unit is the owner's choice (CNY or USD) and only feeds the budget.
- Keys come from `key_file` or `key_env`, never from `config.json`.

```bash
python3 -m xiaoyou_runtime models list           # endpoints, candidates per purpose, spending today
python3 -m xiaoyou_runtime models test iterate.plan   # one real call, shows which endpoint answered
python3 -m xiaoyou_runtime models usage          # today's calls, tokens and cost per endpoint/model
```

## Where the data comes from

The app currently has **no** usage event reporting; this is the first new piece:

- **App side** (implemented, `UsageEvents.java`): events are appended to a small file in the app's private storage (at most 500), and a batch (at most 50) goes to `POST /v1/app/events` every minute while connected and when the app goes to the background. Lines are removed only after the runtime accepted them, so disconnects and crashes lose nothing. Events sent today: `screen` (on leaving a page: page name, dwell milliseconds), `error` (the hint text shown to the owner, at most 120 characters), `crash` (uncaught exception class and message, top 8 frames, thread name; written before the process exits), `rage_tap` (4 or more taps within 2 seconds inside 48dp: page name, cell of a 3×6 grid, count). The runtime also accepts `slow` and `update`, which the app will send later. Each carries `app_version`, `session` (new on every launch) and time. No message text, typed input or screenshots; the runtime also keeps only the registered fields of each type. An old runtime without the endpoint answers 404 and the app keeps the events for later.
- **Already in the runtime** (implemented, `iterate/builtin/sources.py`): `chat_feedback` reads what the owner said on cards, pre-filters sentences that look like complaints or corrections by keywords, and with a local model lets the model classify them (asking `votes` times and taking the majority); without a model it uses the keywords with low confidence. Observed desktop sessions are excluded. `card_failures` reads failed cards. `reply_latency` reads the time from the runtime receiving a message to its first text (a runtime-side approximation, not the client send-to-first-display measurement the cross-stack reliability rules require). The first run only looks back `window_days` days, so history is not sent for classification wholesale. These plugins only read and never change records.

Signals go to `state/iterate/signals/`, one append-only file per day. Raw signals older than the retention period (30 days by default) are deleted; only issue-level statistics remain. Phone and computer both belong to the owner and the data stays local; `chat_feedback` sends the model only the sentence and one message on each side.

## Making a small local model sufficient: what the harness does

| Difficulty | What the harness does |
| --- | --- |
| Finding problems needs judgment | Rules wherever possible (crashes, error rate, 3 seconds, repeated taps, immediate exits). The model only classifies complaints in chat, constrained by a JSON Schema, three samples with majority vote; disagreement is marked "uncertain" and excluded from the run |
| The model does not know where to edit | The harness builds indexes: Java classes/methods, layout IDs, string resources, control IDs from events → definitions. An issue carries control IDs and stack frames that locate 1–3 files; only those excerpts go to the model |
| Plans are vague | A plan is fixed-template JSON: `problem`, `evidence`, `changes[{file, intent}]`, `acceptance`, `out_of_scope`, `risk_self_check`. The harness checks that files exist and are in scope, with at most 3 changes |
| Malformed patches | Only search/replace blocks are accepted; `search` must match the file **exactly once**. On a miss the closest original excerpt goes back to the model, up to 3 times |
| Build errors | On failure only the error lines and 20 lines of surrounding code are fed back. Attempts, wall time and tokens are capped; over the cap the run is abandoned |
| Changes grow | The `diff_scope` gate: only files listed in the plan, a line limit (80 by default), no permissions, network protocol, sync, manifest or build scripts |
| Deciding whether it worked | Not the model's call: after release the observer compares signal counts for the same fingerprint; issues with replay cases also run them in the worktree |

All limits are configuration. A stronger model can loosen them and a weaker one tighten them without changing pipeline code.

## Risk tiers and release

The `risk_tiers` policy puts each change that passed the gates into one of three tiers:

| Tier | Typical change | Action |
| --- | --- | --- |
| **L1 auto-publish** | copy/Chinese hints, layout spacing and alignment, empty states, visibility conditions of existing buttons, null guards at a crash site; only in-scope UI files; within the line limit | publish once all gates pass |
| **L2 one-tap approval** | interaction flow changes, new UI elements, touching network request or local cache read logic | create a pending approval through the existing approvals / quick review; one tap in the app publishes it |
| **L3 report only** | sync protocol, approvals, the update mechanism itself, runtime code, firmware, manifest/permissions, build configuration | write the plan and patch, never publish; the owner can hand it to CC/Codex |

**Release details** (required, otherwise things break):

- **Build from a commit, not the working tree.** Each run starts in a `git worktree` from a commit; its commit stays on the local branch `auto/iter-<run>` and is **not pushed**. If the owner's working tree has uncommitted app changes and the currently published version was built from them, the run must not auto-publish, because the new version would silently drop those changes. The run is downgraded to L3 and asks the owner to commit first.
- **Local only by default.** The existing `app publish` uploads to GitHub Releases (public) by default; the pipeline uses `--local-only`, and public hosting is a separate configuration switch.
- **Rollback publishes a higher version code.** Android does not install a lower version code, so rollback means rebuilding the last good commit with version code +1 and publishing it. The existing `app_release` keeps only the latest version; it needs to retain source commits and build outputs of the last few.
- **Rate limits.** At most one automatic release per day; no new release while the previous one is still under observation; after two consecutive rollbacks automatic publishing turns itself off and only reports until the owner re-enables it.
- **No runtime restart.** The first phases only change the app; the runtime itself is untouched, so killing running tasks on restart is not an issue.

## Observation after release

After a waiting period (24 hours by default, and the new version must have been used a minimum number of times) `fingerprint_compare` checks:

- the run's issue fingerprint: clearly fewer signals → `closed`;
- global guardrails: crashes, error rate, first meaningful response time, sync failures. Any regression beyond its threshold → automatic rollback; the issue is marked "fix failed" and loses priority;
- insufficient evidence (new version barely used) → observe longer and never claim a fix.

Each verdict is saved in the run directory and feeds the next triage: a fingerprint that keeps failing is escalated to L3 for the owner.

## What the owner sees in the app

- One read-only card per run: the issue (with evidence count), plan summary, changed files, gate results, released version and observation verdict.
- L2 approvals in the existing approvals entry, approved or denied with one tap.
- "Roll back this version" and "Pause auto-iteration" on the card, mapped to `iterate rollback <run>` and `iterate.enabled=false`.

## Commands

```bash
# available now
python3 -m xiaoyou_runtime iterate plugins            # plugins, enabled state, permissions, how to write one
python3 -m xiaoyou_runtime iterate run --dry-run --force   # trial run: no cursors, signals or issue book saved
python3 -m xiaoyou_runtime iterate run [--force]      # one run; without --force it only collects until due
python3 -m xiaoyou_runtime iterate status             # recent runs
python3 -m xiaoyou_runtime iterate issues [--all]     # the issue book
python3 -m xiaoyou_runtime iterate issue <prefix> --open|--ignore
# later
python3 -m xiaoyou_runtime iterate replay <run> --stage implementer --plugin <other>
python3 -m xiaoyou_runtime iterate rollback <run>
```

While the runtime is running, a background thread only checks whether auto-iteration is enabled and the owner is idle, then starts a **separate subprocess** for the run. A crash or timeout there does not affect the runtime and never sits on the message path. When a run produces a plan (or fails), the result appears in the app as a read-only card with origin `iterate` through `POST /v1/reports`; the full text is in `state/iterate/runs/<id>/report.md`.

## Relation to the earlier proposal

The 10-16 discussion with Codex was about collecting the owner's conversations with Xiaoyou and batch-optimizing the main agent's delegation and the DSH loop. This pipeline is the first application of the same skeleton: `source`, `detector` and `observer` are shared; switching `scope` to the runtime and the `implementer` to a plugin that edits prompts or routing configuration gives that main-flow optimization. So the plugin framework and the app track come first, and main-flow optimization later joins as another set of plugins rather than a separate system.

## Phases

| Phase | Content | Done when |
| --- | --- | --- |
| **P0 framework** ✅ | plugin loading, permissions, data objects, run directory and state machine, `iterate` command, background scheduling; all unit-tested | `run --dry-run` passes on fake data (`tests/test_iterate.py`) |
| **P1 collection** ✅ code | app-side `UsageEvents` + `POST /v1/app/events`; four sources; six rule detectors | after one day of real use `iterate issues` lists issues with evidence — **not yet verified on the phone** |
| **P2 planning** mostly | model gateway ✅, `template_plan` ✅ (code lookup searches files by clues; no symbol index yet) | with a local model installed, report-only mode runs stably for a week and the owner finds the plans sensible |
| **P3 implementation + gates** | worktree, `search_replace_loop`, all gates, L3 reports | replaying previously fixed issues (avatar offset, English on the home page, …) yields patches that pass the gates |
| **P4 release + observation** | `risk_tiers`, `app_publish_local`, version retention and rollback, `fingerprint_compare`, run cards in the app | two weeks with L2 only (one tap each time), then L1 auto-publish |

Each phase is committed separately and useful on its own: after P1 there is an issue list, after P2 plan reports.

## Open decisions for the owner

1. **Which local model.** This Mac currently has no Ollama, LM Studio or llama.cpp. The gateway speaks the OpenAI-compatible protocol, so any of them works; without a model the pipeline still finds issues and only skips the plan. The DeepSeek API can be the network fallback under the daily budget.
2. **Scope of L1 auto-publish**: as in the table above, and whether it is on from the start or only after two weeks of L2.
3. **Event collection scope**: whether the types under "Where the data comes from" are acceptable.
