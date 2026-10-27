<p align="right"><a href="decide-speak-handoff.zh_CN.md">简体中文</a> · <strong>English</strong></p>

# Handoff to Codex: run the serial / speculative parallel decide-then-speak experiment

Date: 2026-10-27. From: Claude Code. To: Codex, running on the owner's Mac.

## 1. Task

Following the [experiment plan](decide-speak-experiment.md), run the serial (S) vs speculative parallel (P) comparison, compile the results and record them in the measurements section of [decide then speak](decide-then-speak.md). The plan's cases, measures and decision rule are fixed. **Do not change them after seeing results.** If a case looks wrong, say so in the report instead of editing it.

All files are in `/Users/weihaoliu/ai-passport` on branch `feature/usage-history`, uncommitted at handoff:

| File | Purpose |
| --- | --- |
| `docs/development/decide-speak-experiment.md` | Plan: arms, measures, decision rule |
| `tools/experiments/decide_speak_cases.jsonl` | 46 cases, reviewed by the owner |
| `tools/experiments/decide_speak_compare.py` | Harness with `run`, `summarize` and `review` commands |

## 2. Do not

- Change Runtime code, configuration or persona; restart or replace the live Runtime; deploy.
- Write results or review files inside the repository (the harness refuses). Use something like `~/xiaoyou-experiments/decide-speak/`.
- Print, log or commit the key.
- Drop slow or failed samples, or rerun selected runs to improve the numbers.
- Open `blind-review-key.json`, or tell the owner which side is S or P, before the owner finishes the blind review.
- Use the Instinct chat export the owner shared; it is not needed, and the plan already summarizes its style.

## 3. Steps

### 3.1 Prepare

1. Python 3.9 or later: `python3 --version`. The harness uses only the standard library.
2. The key file exists: `~/.config/xiaoyou/deepseek.key`. The harness reads `DEEPSEEK_API_KEY` first, then this file.
3. Check the models. In the live `runtime/config.json`, `agents.deepseek.chat_model` is `deepseek-flash`. In R7's `helpers/agents.py`, talking calls (including the decision) use `chat_model`, so both steps default to `deepseek-flash`. If you find otherwise, set `--decide-model` and `--speak-model` and say so in the report.
4. Record the version combination: repository commit (`git rev-parse --short HEAD`), models and persona file. Put it in every result line with `--note`.

### 3.2 Offline dry run

```bash
python3 tools/experiments/decide_speak_compare.py run --dry-run --repeats 1 --pause 0 --decide-wait 3 --out ~/xiaoyou-experiments/decide-speak/dry.jsonl
```

Expect 92 lines (46 cases × 2 arms) and no `error`. This only checks the flow; the numbers mean nothing.

### 3.3 Live probe

```bash
python3 tools/experiments/decide_speak_compare.py run --only chat-02,task-01,run-03 --repeats 1 --out ~/xiaoyou-experiments/decide-speak/probe.jsonl --note "probe"
```

Check each line:
- `decision.fetch_error` is empty and `decision.usage` has numbers;
- `speech.text` is not empty and `speech.error` is empty;
- `task-01` decides `start_task` and `run-03` decides `cancel_task` (for timeouts, read `late_name`).

If the API rejects the request (for example `thinking` or the `tools` format), **stop and report**. Do not change the request format to work around it.

### 3.4 Full run

```bash
python3 tools/experiments/decide_speak_compare.py run --repeats 5 --out ~/xiaoyou-experiments/decide-speak/results.jsonl --note "commit=<commit> model=deepseek-flash persona=runtime/persona.txt"
```

- 460 runs of a few seconds each plus pauses, roughly an hour. Run it in the background and keep the Mac awake.
- If it stops, run the same command again: any (case, arm, repeat) already written is skipped, failed ones included.
- Avoid heavy use of the same key during the run, so it does not skew latency.

### 3.5 Summary

```bash
python3 tools/experiments/decide_speak_compare.py summarize ~/xiaoyou-experiments/decide-speak/results.jsonl
```

Prices default to the configured 2 (input) and 8 (output) per million tokens, in the configuration's currency; state this in the report.

### 3.6 Speech/action consistency (you label it)

Read `results.jsonl` line by line. Compare `decision.status`, `decision.facts` and `speech.text`, and label each run:

- `said_not_done`: says something was arranged, remembered or stopped, but no commitment was carried out this run (status `reply`, `timeout`, `unavailable` or `refused`);
- `done_not_said`: a commitment was carried out (`selected`) and the reply does not mention it;
- `overstated`: calls "queued" or "stop requested" "started", "finished" or "stopped";
- whether each `must` and `must_not` item of the case holds.

Write labels to `labels.jsonl` outside the repository, one line per run with `case`, `arm` and `repeat`. List every flagged run, plus a random 10% of unflagged runs, for the owner to review.

### 3.7 Style scores (you score them, as a secondary signal)

Score each trait in the two tables of plan section 2 as 0 or 1. Small-talk cases (small talk and feelings, and casual language-switch cases) use the Feiyu table; all other cases use the Instinct table. Score only the first run of each arm (`repeat` 0), without looking at which arm it is.

### 3.8 Owner blind review

```bash
python3 tools/experiments/decide_speak_compare.py review ~/xiaoyou-experiments/decide-speak/results.jsonl --out-dir ~/xiaoyou-experiments/decide-speak/review
```

Give `blind-review.md` to the owner. After the owner finishes, map the choices back to S and P with `blind-review-key.json`.

## 4. Report

Write it in the measurements section of `docs/development/decide-then-speak.md` and its Chinese version `decide-then-speak.zh_CN.md`, kept aligned:

- version combination: commit, models, persona file, date, computer;
- for each arm, `release_ms` median, p95, maximum and the number above 3,000 ms, listing every sample above 3,000 ms;
- decision timeout rate, median real decision time (`real_ms`), decision accuracy;
- P's discard share and extra cost, estimated at both the experiment share and the live share;
- counts of the three consistency errors and how the `must` / `must_not` items held;
- a verdict on each of the four decision-rule conditions. Condition 3 waits for the owner's blind review and condition 4 for the owner's cost limit; write "pending" where there is no result yet;
- known limits (section 5).

Use the delivery format of the [cross-stack reliability rules](cross-stack-reliability.md): Build NOT RUN (nothing was built), Host tests lists the commands run, Device tests NOT RUN (these latencies exclude transport and clients and do not replace a device first reply). Show the report to the owner first; do not commit or push it yourself.

## 5. Known limits

- The system prompt is only the persona plus the state each case gives, shorter than live, so absolute times are lower. Both arms use the same prompt, so the comparison holds.
- The recorder's fact wording follows the live meaning but is not word for word.
- Latency is Runtime-side only, without transport, queueing or client display.
- DeepSeek prefix caching may speed up later runs; alternating which arm goes first offsets part of that.
- The winning arm still needs a device measurement with the first-response measurement document on the R7 branch (`docs/development/first-response-measurement.md`).

## 6. Message to paste to Codex

> Please run the serial / speculative parallel decide-then-speak experiment following `docs/development/decide-speak-handoff.md`. Do 3.1 to 3.3 first and run the full set only if the probe is clean. Do not change the Runtime, the cases or the decision rule, and keep results outside the repository. When done, write the report as described in section 4 and show it to me before committing anything.
