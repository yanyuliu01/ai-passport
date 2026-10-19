<p align="right">
  <a href="auto-iteration.zh_CN.md">简体中文</a> · <strong>English</strong>
</p>

# Auto-iteration and unified collection (design draft)

> Status: 2026-10-19. Every section is marked **implemented** (current code) or **design** (not written yet). Implemented: the plugin framework, Android app usage event reporting, four sources, six rule detectors, the model gateway, and a single-issue planner (`template_plan`, to be replaced by version plans). Everything else is design.

## In one sentence

All clients (the phone app, a future desktop client, the pocket device, the runtime itself) report usage data to the **home computer** under one contract. Once a batch has accumulated, the home computer, **mostly with local models**, groups the problems into a few themes and writes a **version-level iteration plan** that the owner reviews once. It then changes code in small steps following the plan, gates the whole version, publishes low-risk versions automatically, watches the themes after release and rolls back if things get worse. Every stage is a pluggable plugin.

## Premises

1. **Iterate by version, not by point fix.** Handle a batch of problems at once: group them, find shared root causes, write a complete version plan, then split it into work items. A single issue is evidence, not the unit of change.
2. **Weak model, heavy harness.** No reliance on agents such as CC or Codex that plan on their own and read the whole repository. The model does narrow jobs: classify a sentence, analyse one group of issues, fill a plan template, write a small patch from given context. Finding files, assembling context, validating, building, testing, judging risk, publishing and rolling back are deterministic code.
3. **Spend money on judgment, keep volume local.** Frequent jobs (classification, patches, build fixes) use local models; the once-per-batch job that sets direction (the version plan) may use a strong network model within a daily budget. Everything goes through the same [model gateway](#model-gateway-implemented).
4. **Data lives on the home computer.** Usage data, the issue book and iteration all live on one designated computer; phones and other computers only buffer and forward.
5. **Everything is pluggable.** Event types, detection, grouping, planning, implementation, gates, release, observation and model backends are plugins or registry entries that configuration enables and replaces; removing a stage degrades the pipeline instead of breaking it.
6. **Built on existing systems.** Conversations and tasks still follow the [unified message model](unified-message-model.md); client identity, capability declaration and multiple computers reuse the runtime's pairing, `/v1/info` and mesh; release uses the existing `app publish`; owner decisions go through the existing approval bar and quick review.
7. **The [cross-stack reliability rules](cross-stack-reliability.md) still apply.** An automatically published version must preserve working features, the 3-second first response and multi-end consistency, or it does not ship.

## Overall structure

```text
 Clients                   Collection gateway (home)          Auto-iteration (home)
 ─────────                ──────────────────────             ─────────────────────────────
 Android app ─┐            POST /v1/events                    Outer: once per batch
 Desktop app ─┤  local     ├ filter fields by type registry     group → root cause → version plan
 Device ──────┤  buffer    ├ deduplicate by id                  → check product principles → owner reviews once
 (via phone)  │ → delete   ├ store (daily, append-only)              ↓
 Runtime ─────┤  after ack └ not home: forward to home         Inner: per work item
 Agents ──────┘                      ↓                          small patch → build → whole-version gates
                            signals → detectors → issue book          ↓
                                                               tier → release → watch themes → roll back
                    ┌────────────── model gateway (local first, network within budget) ──────────────┐
```

## Unified collection (collection gateway)

It mirrors the agent gateway: the agent gateway connects the runtime **outward** to agents; the collection gateway takes data **inward** from clients. Both treat the runtime as the gateway and reuse the same identity, capability declaration and mesh.

| Concept | Agent gateway | Collection gateway |
| --- | --- | --- |
| Who connects | agents / harnesses (Claude Code, Codex, DSH) | clients: `android`, `desktop`, `device`, `runtime`, `agent` |
| How | one adapter per harness | every client follows one reporting contract; no adapters |
| Registry | agent instances and capabilities | event type registry: fields, lengths, privacy class, retention |
| Negotiation | runtime declares `gateway_v1` | runtime declares `events_v1` and its known event types and versions in `/v1/info`; clients send only those |
| Storage | record hub / message log | a separate event store, **not mixed into chat records**: retention and privacy rules differ |

### Event format (design)

```json
{"id": "client-generated unique id", "at": 1800000000000, "type": "screen", "type_version": 1,
 "client": {"kind": "android", "id": "device id", "version": "0.20.1", "platform": "android-14"},
 "session": "launch id", "fields": {"name": "home", "ms": 3200}}
```

- One endpoint, `POST /v1/events`, at most 200 events per request. The existing `POST /v1/app/events` (**implemented**) stays as an alias treated as `client.kind=android`, so installed apps keep working.
- The **event type registry** lists each type's allowed fields and lengths, privacy class (`usage` behaviour, `diagnostic` errors and crashes, `content` contains the owner's words) and retention days. The runtime keeps only registered fields (**implemented** as a fixed table in `iterate/store.py`; the design makes it a pluggable registry).
- No message text, typed input or screenshots. The `content` class is only for complaint signals the runtime derives itself; the words are already on the computer and only excerpts are kept.

### Reporting contract (same for every client)

1. Events go to a bounded local buffer first (500 on Android).
2. Batches are sent when online; **lines are deleted only after the runtime acknowledged them**, so disconnects and crashes lose nothing.
3. Deduplication is by `id`, so resending is safe.
4. If the runtime does not support a type (absent from `/v1/info`, or a 404), keep it and do not fail.
5. Crashes are written before the process exits.

The Android app already does this (`UsageEvents.java`). A desktop client will follow the same contract. The **pocket device** does not talk to the runtime; its events go to the phone over Bluetooth and the phone forwards them as `client.kind=device`. The **runtime's own** events (reply latency, task failures, delegation) are written by the record hub when they happen instead of being reconstructed later from cards. **Agents**' quality-related execution events (delegation, failure, duration) are written by the agent gateway as well.

### Home computer storage (design)

- Configuration names the home computer: `"iterate": {"home": "<runtime name or runtime_id>"}`. The event store, issue book and iteration exist only there.
- Other computers queue received events and forward them to the home computer over the existing mesh, deleting them after acknowledgement. While the home computer is off they wait and catch up later.
- Iteration runs only on the home computer, so two computers never change code or publish at the same time. If it is off, iteration waits; a delay does not matter.
- Today (**implemented**): the phone sends only to the selected runtime, and events stay in that runtime's `state/iterate/app-events/`. Switching between computers splits the data until forwarding exists.

### Management (design)

```bash
python3 -m xiaoyou_runtime events types        # registered types, fields, privacy class, retention
python3 -m xiaoyou_runtime events summary      # recent counts per client and type, and where they are
python3 -m xiaoyou_runtime events export --since 7d
python3 -m xiaoyou_runtime events delete --type rage_tap --before 2026-10-01
```

## Outer loop: one version plan per batch (design)

Triggers are unchanged: enough new signals or enough time, and the owner idle. The output is a version plan instead of a single fix.

```text
Freeze the batch: all unhandled issues in the issue book for the period
  ↓
① Rebuild journeys: chain each session's events
   (which page → what was done → where it got stuck → exit) and attach the journey to issues
  ↓
② Group (rules): by page, journey, files likely to change, co-occurrence in a session.
   "Repeated taps on home + error on home + complaint that an entry is hard to find" form one "home" group
  ↓
③ Root cause (once per group, local model): one cause behind these symptoms? Flow design or code?
   Output: hypothesis, evidence references, confidence
  ↓
④ Version plan (once per batch, may use a strong network model):
   - 2–3 themes for this version, the issues and root cause each covers
   - how each theme changes the flow, not "add a check on line N"
   - dependencies and order between themes
   - what this version does not do, and why
   - acceptance metrics per theme
   - which existing features are affected and which regression checks are required
  ↓
⑤ Check against product principles: list conflicts; serious ones send the plan back
  ↓
⑥ Owner reviews once: one card to approve, send back with a note, or drop a theme
```

**Product principles document** (design): a document in the repository, approved by the owner and updated with versions, stating what the product is, what it does not do, and its interaction principles (for example: only the approval bar interrupts the owner; first meaningful reply within 3 seconds; Chinese-only UI; the phone controls and computers do the work). Version plans must cite it. The first draft is written by AI from the current code and the owner's past feedback and takes effect after the owner edits it.

Without a model the outer loop still performs ① and ② and produces an "issue map" report without a plan.

## Inner loop: implement the plan in small steps (design)

```text
Version plan → ordered work items (1–3 files each, with dependencies)
  ↓
A version branch auto/v<version> in a separate git worktree, from a commit (never the owner's working tree)
  ↓
For each work item, in dependency order:
  harness finds the code → model writes only search/replace blocks → each search must match exactly once,
  otherwise rewrite with the closest original → build → feed back only error lines and 20 lines around them
  → attempts, time and tokens are capped → commit to the version branch only after the item's unit tests pass
  ↓
Whole-version gates: diff scope and size, all unit tests, build, static checks, Chinese glyphs,
                     replay cases, the plan's regression checks
  ↓
Tier → release → observe (below)
```

When a work item cannot be done, nothing is forced: either the whole theme is dropped from this version (other themes ship), or the version stops and the card says where it got stuck so the owner can hand it to CC / Codex.

## Risk tiers and release (design)

A version's tier is the highest tier among its work items.

| Tier | Typical change | Action |
| --- | --- | --- |
| **L1 auto-publish** | copy, Chinese hints, layout and alignment, empty states, visibility of existing buttons, null guards at crash sites | the owner already approved the plan; publish when all gates pass |
| **L2 one-tap approval** | interaction flow changes, new UI elements, network request or local cache read logic | goes to the approval bar; publishes after one tap |
| **L3 report only** | sync protocol, approvals, the update mechanism itself, runtime code, firmware, manifest / permissions, build configuration | plan and patch only, no release; the owner can hand it to CC / Codex |

The harness sets a floor from the files touched (**implemented**: `planner.risk_floor`); a lower self-assessment from the model does not count.

Release rules:

- **Build from a commit, not the working tree.** The version branch stays local and is **not pushed**. If the owner's working tree has uncommitted app changes and the published version was built from them, auto-publish is refused (the new version would silently drop them); the version becomes L3 and asks the owner to commit first.
- **Local only by default.** The existing `app publish` uploads to GitHub Releases (public) by default; auto-iteration uses `--local-only`, and public hosting is a separate configuration switch.
- **Rollback publishes a higher version code.** Android does not install lower version codes, so rollback means rebuilding the last good commit with version code +1. The existing `app_release` keeps only the latest version and must retain source commits of the last few.
- **Rate limits.** At most one automatic release per day; none while the previous version is under observation; after two consecutive rollbacks automatic publishing turns itself off until the owner re-enables it.
- **No runtime restart.** Only the app changes at first.

## Observation after release (design)

After a waiting period (24 hours by default, and enough use of the new version), compare **per theme**:

- Theme metrics, for example "frustration signals on the home page" (repeated taps, errors, complaints) as a whole. Down → the theme and its issues close.
- Global guardrails: crashes, error rate, first meaningful reply time, sync failures. Any regression beyond its threshold → automatic rollback; the theme is marked "fix failed".
- Insufficient evidence (new version barely used) → keep observing, never claim a fix.

Verdicts are saved in the run directory and feed the next batch; a theme that keeps failing becomes L3 for the owner.

## What the owner sees in the app

**No pop-ups and no system notifications.** Things appear only in existing places, and only when the owner is needed:

| When | Where | Status |
| --- | --- | --- |
| A run produced a plan or failed | a read-only card with origin `iterate` in the task list, showing the report | **implemented** (single-issue plans; runs that only collect post nothing) |
| A version plan awaits review | a card: approve / send back with a note / drop a theme | design |
| An L2 version awaits release | the approval bar at the top, with quick review | design (existing entry) |
| A version was published | the card says what changed and how to undo it; the phone shows the usual update prompt | design |
| Card actions | "Roll back this version", "Pause auto-iteration" | design |

## Plugin model (framework implemented)

| `kind` | Input → output | Plugins | Status |
| --- | --- | --- | --- |
| `source` | event store / records → `Signal` | `app_events`, `chat_feedback`, `card_failures`, `reply_latency` | implemented |
| `detector` | `Signal[]` → `Issue[]` | `crash`, `app_errors`, `rage_tap`, `slow_first_reply`, `complaint_cluster`, `task_failures` | implemented |
| `journey` | events → journeys | `session_paths` | design |
| `grouper` | `Issue[]` → issue groups | `by_screen_path_files` | design |
| `analyst` | group → root cause hypothesis | `group_root_cause` | design |
| `version_planner` | groups + causes + principles → version plan | `version_plan` | design |
| `decomposer` | version plan → work items | `work_items` | design |
| `implementer` | work item + code → patch | `search_replace_loop` | design |
| `gate` | version branch → pass/fail | `diff_scope`, `gradle_unit`, `gradle_assemble`, `validate_static`, `strings_zh`, `replay` | design |
| `policy` | plan + patches + gates → tier | `risk_tiers` | design (floor rule implemented) |
| `releaser` | build output → release | `app_publish_local` | design |
| `observer` | release + new signals → verdict | `theme_compare` | design |
| `triage` / `planner` | single-issue pick / single-issue plan | `score_by_impact` / `template_plan` | implemented; superseded by the outer loop and kept as a fallback for single fixes |

Built-ins live in `runtime/xiaoyou_runtime/iterate/builtin/`; your own go in `iterate.plugin_dirs`, each `.py` holding a `PLUGINS` list. A later plugin with the same kind and name replaces an earlier one; a file that fails to load records one error:

```python
def detect(ctx, signals):              # ctx opens only the permissions this plugin declared
    ...
    return [Issue(fingerprint=..., title=..., severity=..., evidence=[...])]

PLUGINS = [{"name": "rage_tap", "kind": "detector", "version": 1,
            "consumes": ["app.rage_tap"], "permissions": [], "run": detect}]
```

**Permissions** are declared and checked by `ctx`: `read_state`, `read_repo`, `model`, and later `write_worktree`, `git_commit`, `publish`, `network`. Undeclared means unavailable. No plugin can `git push`. Plugins run in the runtime's process: this guards against accidental overreach and is not a security sandbox.

Plugins exchange versioned JSON objects saved in `state/iterate/runs/<id>/`, so any stage can be rerun alone, compared against another plugin, or replayed in tests.

## Model gateway (implemented)

The goal is cost: **use a local model whenever possible**; network models are a fallback with a daily spending cap. It is not specific to auto-iteration: `model_gateway.py` serves every place in the runtime that calls a model directly.

It is the **Provider/model** layer of the agent gateway proposal (`docs/development/agent-gateway.md`, written in another session and not yet committed): the agent gateway manages how harnesses with their own tool loop are installed, probed and enabled; the model gateway manages where inference endpoints and credentials are, which candidates each purpose uses in what order, and what was spent. Both share one endpoint registry; harnesses get environment variables for the same endpoint from `binding_env(provider)`. **Agent configuration does not use it yet**; that lands together with the agent gateway's registry.

```json
"models": {
  "providers": {
    "local":    {"protocol": "openai", "base_url": "http://127.0.0.1:11434/v1"},
    "deepseek": {"protocol": "openai", "base_url": "https://api.deepseek.com/v1",
                 "key_file": "~/.config/xiaoyou/deepseek.key", "price": {"input": 2, "output": 8}}
  },
  "routes": {
    "default":              ["local/qwen2.5:7b"],
    "iterate.classify":     ["local/qwen2.5:7b"],
    "iterate.analyze":      ["local/qwen2.5-coder:14b", "deepseek/deepseek-v4-flash"],
    "iterate.version_plan": ["deepseek/deepseek-v4-pro", "local/qwen2.5-coder:14b"],
    "iterate.patch":        ["local/qwen2.5-coder:14b"]
  },
  "network": {"allowed": false, "daily_budget": 2}
}
```

- **Protocols**: `openai` (understood by Ollama, llama.cpp server, LM Studio, vLLM, DeepSeek and OpenAI) and `anthropic`.
- **Local or network**: without `locality` it is inferred from the address; loopback, private and Tailscale (100.64/10) addresses are local. Local costs nothing; a network endpoint is chosen only when `network.allowed` is true and today's spending is below `daily_budget`.
- **Routing**: an unknown purpose falls back to its parent (`iterate.plan` → `iterate` → `default`); candidates are tried in order, and an unreachable local endpoint is skipped for 60 seconds.
- **JSON**: the JSON is extracted from the answer and passed to the caller's check; failures go back to the model with bounded retries.
- **Usage**: each call appends a line to `state/models/usage.jsonl` (purpose, endpoint, model, tokens, estimated cost, duration, success), never prompts or answers.
- Keys come from `key_file` or `key_env`, never `config.json`.

```bash
python3 -m xiaoyou_runtime models list
python3 -m xiaoyou_runtime models test iterate.plan
python3 -m xiaoyou_runtime models usage
```

## Making a small local model sufficient: what the harness does

| Difficulty | What the harness does |
| --- | --- |
| Finding problems needs judgment | rules wherever possible (crashes, error rate, 3 seconds, repeated taps); the model only classifies complaints, asked several times with a majority vote (**implemented**) |
| Small models are bad at the big picture | rules group issues first so each analysis gets little material; the whole-version plan may use a strong network model |
| The model does not know where to edit | clues (file and line in stack frames, error text, page name, words in complaints) select 1–3 files and only relevant excerpts go to the model (**implemented**; a symbol index comes later) |
| Vague plans | fixed-template JSON; the harness checks files exist, are in scope, and item counts (**implemented** for single issues) |
| Malformed patches | only search/replace blocks that match exactly once, otherwise rewrite with the closest original |
| Build errors | only error lines and 20 surrounding lines go back; attempts, time and tokens are capped |
| Growing changes | only the work item's files, a line limit, dangerous files refused |
| Deciding whether it worked | not the model's call: theme metrics after release |

All limits are configuration; a stronger model can loosen them and a weaker one tighten them.

## Configuration (implemented part)

```json
"iterate": {
  "enabled": false,
  "trigger": {"min_new_signals": 50, "interval_hours": 24, "only_when_idle": true},
  "pipeline": {
    "source":   ["app_events", "chat_feedback", "card_failures", "reply_latency"],
    "detector": ["crash", "app_errors", "rage_tap", "slow_first_reply", "complaint_cluster", "task_failures"],
    "triage": "score_by_impact", "planner": "template_plan", "implementer": null
  },
  "plugin_dirs": [], "areas": ["app"],
  "allow": ["companion/android/app/src/main/**"],
  "deny": ["**/AndroidManifest.xml", "**/build.gradle"],
  "repo_dir": "..",
  "min_issue_count": 3, "window_days": 7, "retention_days": 30, "votes": 3, "max_classify": 40
}
```

With `enabled` true the runtime checks every 15 minutes: if the owner has nothing running it starts a subprocess for `iterate run`, which decides itself whether enough signals or time have accumulated. A crash there does not affect the runtime. The design adds `home` (the home computer) and the outer-loop plugin names.

## Commands

```bash
# implemented
python3 -m xiaoyou_runtime iterate plugins
python3 -m xiaoyou_runtime iterate run --dry-run --force   # trial run, saves no state
python3 -m xiaoyou_runtime iterate run [--force]
python3 -m xiaoyou_runtime iterate status
python3 -m xiaoyou_runtime iterate issues [--all]
python3 -m xiaoyou_runtime iterate issue <prefix> --open|--ignore
# design
python3 -m xiaoyou_runtime events types|summary|export|delete
python3 -m xiaoyou_runtime iterate map                     # issue map: groups and root causes
python3 -m xiaoyou_runtime iterate replay <run> --stage <stage> --plugin <other>
python3 -m xiaoyou_runtime iterate rollback <run>
```

## Relation to the earlier proposal

The 10-16 discussion with Codex about collecting the owner's conversations with Xiaoyou and batch-optimizing the main agent's delegation and the DSH loop uses the same skeleton: the same collection gateway, the same issue book, the same outer loop. With `areas` set to `agent` and implementation plugins that edit prompts and routing configuration, it becomes main-flow optimization, not a separate system.

## Phases

| Phase | Content | Done when |
| --- | --- | --- |
| **P0 framework** ✅ | plugin loading, permissions, data objects, run directories, `iterate` command, background scheduling | `tests/test_iterate.py` |
| **P1 Android collection** ✅ code | `UsageEvents` + `/v1/app/events`; four sources; six detectors | a day of phone use yields issues with evidence — **not yet verified on the phone** |
| **P2 model gateway** ✅ | local first, network budget, JSON retries, usage ledger | `tests/test_model_gateway.py`; no local model installed yet |
| **P3 unified collection** | `/v1/events`, event type registry, `/v1/info` declaration, runtime-written events, home computer and forwarding, `events` commands; Android moves to the new format | a day of switching between two computers: every event reaches the home computer, no duplicates |
| **P4 outer loop** | journeys, grouping, root causes, version plans, product principles document, plan review card | two batches in plan-only mode; the owner finds the plans complete rather than scattered patches |
| **P5 inner loop** | work items, version branch, patch loop, whole-version gates | replaying past issues (avatar offset, English on the home page, …) yields versions that pass the gates |
| **P6 release and observation** | tiers, local release, version retention and rollback, theme observation, rollback / pause on the card | two weeks with L2 only, then L1 |

Each phase is committed separately and useful on its own.

## Open decisions for the owner

1. **Which local model.** This Mac has no Ollama, LM Studio or llama.cpp yet. The gateway speaks the OpenAI-compatible protocol, so any of them works.
2. **Whether version plans use a strong network model**, and the daily budget.
3. **Which computer is home** (Mac suggested).
4. **Product principles document**: AI drafts and the owner edits, or the owner writes first.
5. **L1 auto-publish**: on from the start, or two weeks of L2 first.
