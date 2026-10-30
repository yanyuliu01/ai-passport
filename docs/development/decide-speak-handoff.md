<p align="right"><a href="decide-speak-handoff.zh_CN.md">简体中文</a> · <strong>English</strong></p>

# Handoff to Codex: run the serial / speculative parallel decide-then-speak experiment

Date: 2026-10-27. From: Claude Code. To: Codex, running on the owner's Mac.

## 1. Task

Following the [experiment plan](decide-speak-experiment.md), run the serial (S) vs speculative parallel (P) comparison, compile the results and record them in the measurements section of [decide then speak](decide-then-speak.md). The plan's cases, measures and decision rule are fixed. **Do not change them after seeing results.** If a case looks wrong, say so in the report instead of editing it.

All files are in `/Users/weihaoliu/ai-passport` on branch `feature/usage-history`, committed as `6349ba3` (plan/harness) and `a0e4dc4` (cost rule):

| File | Purpose |
| --- | --- |
| `docs/development/decide-speak-experiment.md` | Plan: arms, measures, decision rule |
| `tools/experiments/decide_speak_cases.jsonl` | 46 cases, reviewed by the owner |
| `tools/experiments/decide_speak_compare.py` | Harness with `run`, `summarize` and `review` commands |

## 2. Do not

- Change Runtime code, configuration or persona; restart or replace the live Runtime; deploy.
- Write results or review files inside the repository (the harness refuses). Use something like `~/xiaoyu-experiments/decide-speak/`.
- Print, log or commit the key.
- Drop slow or failed samples, or rerun selected runs to improve the numbers.
- Open `blind-review-key.json`, or tell the owner which side is S or P, before the owner finishes the blind review.
- Use the Instinct chat export the owner shared; it is not needed, and the plan already summarizes its style.

## 3. Steps

### 3.1 Prepare

1. Python 3.9 or later: `python3 --version`. The harness uses only the standard library.
2. The key file exists: `~/.config/xiaoyu/deepseek.key`. The harness reads `DEEPSEEK_API_KEY` first, then this file.
3. Check the models. In the live `runtime/config.json`, `agents.deepseek.chat_model` is `deepseek-flash`. In R7's `helpers/agents.py`, talking calls (including the decision) use `chat_model`, so both steps default to `deepseek-flash`. If you find otherwise, set `--decide-model` and `--speak-model` and say so in the report.
4. The R7 checkout is `/Users/weihaoliu/ai-passport-r7`; it has local changes. Record its commit and dirty state separately from the harness checkout. The live configuration is in the main checkout, not necessarily the R7 checkout.
5. Record the version combination: repository commit (`git rev-parse --short HEAD`), models and persona file. Put it in every result line with `--note`.

### 3.2 Offline dry run

```bash
python3 tools/experiments/decide_speak_compare.py run --dry-run --repeats 1 --pause 0 --decide-wait 3 --out ~/xiaoyu-experiments/decide-speak/dry.jsonl
```

Expect 92 lines (46 cases × 2 arms) and no `error`. This only checks the flow; the numbers mean nothing.

### 3.3 Live probe

```bash
python3 tools/experiments/decide_speak_compare.py run --only chat-02,task-01,run-03 --repeats 1 --out ~/xiaoyu-experiments/decide-speak/probe.jsonl --note "probe"
```

Check each line:
- `decision.fetch_error` is empty and `decision.usage` has numbers;
- `speech.text` is not empty and `speech.error` is empty;
- `task-01` decides `start_task` and `run-03` decides `cancel_task` (for timeouts, read `late_name`).

If the API rejects the request (for example `thinking` or the `tools` format), **stop and report**. Do not change the request format to work around it.

### 3.4 Full run

```bash
python3 tools/experiments/decide_speak_compare.py run --repeats 5 --out ~/xiaoyu-experiments/decide-speak/results.jsonl --note "commit=<commit> model=deepseek-flash persona=runtime/persona.txt"
```

- 460 runs of a few seconds each plus pauses, roughly an hour. Run it in the background and keep the Mac awake.
- If it stops, run the same command again: any (case, arm, repeat) already written is skipped, failed ones included.
- Avoid heavy use of the same key during the run, so it does not skew latency.

### 3.5 Summary

```bash
python3 tools/experiments/decide_speak_compare.py summarize ~/xiaoyu-experiments/decide-speak/results.jsonl
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
python3 tools/experiments/decide_speak_compare.py review ~/xiaoyu-experiments/decide-speak/results.jsonl --out-dir ~/xiaoyu-experiments/decide-speak/review
```

Give `blind-review.md` to the owner. After the owner finishes, map the choices back to S and P with `blind-review-key.json`.

## 4. Report

Write it in the measurements section of `docs/development/decide-then-speak.md` and its Chinese version `decide-then-speak.zh_CN.md`, kept aligned:

- version combination: commit, models, persona file, date, computer;
- for each arm, `release_ms` median, p95, maximum and the number above 3,000 ms, listing every sample above 3,000 ms;
- decision timeout rate, median real decision time (`real_ms`), decision accuracy;
- P's discard share and extra cost, estimated at both the experiment share and the live share;
- counts of the three consistency errors and how the `must` / `must_not` items held;
- a verdict on each of the three decision-rule conditions. Condition 3 waits for the owner's blind review; write "pending" where there is no result yet. Report cost as numbers only, without a verdict;
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

## Owner-directed exploratory pass (2026-10-27)

The owner subsequently asked to remove the decision-phase deadline and first
measure average duration. This supersedes the 1.3-second cutoff for this
exploratory pass only; production remains unchanged. Run one repetition of all
46 cases in each arm before deciding on more repetitions:

```bash
python3 tools/experiments/decide_speak_compare.py run --deadline none --decide-wait 60 --repeats 1 --out ~/xiaoyou-experiments/decide-speak/20261027/unbounded-results.jsonl
```

Keep these results separate from the interrupted deadline-limited run; do not
pool them or describe this pilot as the original 460-run experiment. A 60-second
provider socket timeout handles network failures; there is no decision-phase
cutoff. Report mean decision duration as well as the median and tail. All
commitments still go only to the simulator, including successful `queued`
results: CC and Codex must never execute the synthetic tasks.

`review` also writes `public/index.html`, an offline annotation form with A/B/tie,
notes, browser-local saving and JSON export. Serve only `public/` if opening a
localhost review; keep the separate key outside that served directory and do
not read it until the owner finishes. The Markdown review remains editable.

## Latest handoff: owner blind review completed (2026-10-27)

**Read this section first. Do not rerun the model or treat earlier pending-review notes as current.**

### Completion and conclusion

- The no-decision-cutoff 46 × 2 single-repeat pilot is complete. Mean decision duration: 2.72 s;
  mean complete-bubble release: S 5.22 s, P 3.84 s. See the [measurement report](decide-then-speak.md).
- The owner completed all 46 cases and supplied `/Users/weihaoliu/Downloads/blind-review-answers.json`.
  Unique case IDs, complete choices and the matching review identifier were verified.
- Use the exported JSON. The earlier in-app browser showed 2/46 because it reflected that browser's
  local state. `owner-review-observed.json` is only that partial observation, not the final ballot.
- The key was opened only after all cases were completed: **S 26 wins, P 17 wins, 3 ties**.
  P wins or ties in **20/46 = 43.48%**, below the original 50% quality condition. Speed is provisionally
  favorable; consistency flags still require verification; the quality condition FAILS.
  Do not switch to speculative parallel on this evidence. Production remains unchanged.
  One repetition does not complete the original 460-run confirmatory experiment.
- All commitments were simulated. No real CC or Codex executed synthetic work. No live Runtime,
  configuration or persona change, restart, deployment, commit or push occurred. R7's pre-existing
  dirty changes were preserved and their diff hash stayed unchanged during the experiment.

### Main owner feedback (24 notes)

1. **Be short and say each thing once.** Answer a date directly; give arrival judgments in one or two
   sentences. Remove repeated commitments, unnecessary restrictions and self-explanation. Avoid
   roundabout contrastive phrasing. Admit a failed handoff and state the actual current status without
   a long account of internal mechanics.
2. **Use short bubbles at natural semantic boundaries.** Several notes explicitly request splitting
   bubbles. This does not authorize weakening the three-second requirement, counting typing animations,
   or arbitrary client-side chopping. This experiment did not compare a new bubble policy.
3. **Make conversation reciprocal.** Offer thoughts, feelings, real experiences or wishes, rather
   than only questions and acknowledgments. A wish to see the moon is possible; claiming to have seen
   it or to be busy requires evidence. The owner deferred sourced stories and autonomous activity to
   the drive design. Do not add a new set of rigid persona rules during this handoff.
4. **Respond to emotion as well as the task.** Frustration plus a request needs more than a task
   receipt. Mistakes deserve an appropriate apology; avoid repeatedly saying that she is present
   without a reason.
5. **Use ordinary speech and small expressive details.** Natural punctuation, a tilde or an expression
   may help, without turning every reply into a template. The owner's reminder example simply
   confirms Thursday and says she will remind them then. Preserve material truth about queueing,
   execution and whether cancellation has been confirmed.
6. Both English reminder replies were Chinese; the owner marked a tie. This is a shared defect,
   not evidence favoring either arm.

### Files, validation and continuation

Harness checkout: `/Users/weihaoliu/ai-passport`, branch `feature/usage-history`, baseline `a0e4dc4`,
with this work uncommitted. R7 checkout: `/Users/weihaoliu/ai-passport-r7`, `aba494e` plus retained
local changes. Do not overwrite or clean it. Harness changes include optional no decision cutoff,
means, full blind-review context and an offline annotation form; fixes cover omitted late-call
usage, broken streams falsely counted as completed bubbles, and discard cleanup delaying replacement
speech. Regression tests are in `tests/test_decide_speak_compare.py`, registered in `tools/validate.sh`.

Evidence directory: `/Users/weihaoliu/xiaoyou-experiments/decide-speak/20261027/`:

- `owner-review-answers.json`: copy of the complete owner export; `owner-review-summary.json`: decoded ballots and counts.
- `unbounded-results.jsonl`, `unbounded-summary.json`: this pilot; `results.jsonl`: 92 earlier cutoff-limited rows, kept separate.
- `labels.jsonl`, `audit-review.json`, `audit-review.md`: provisional Codex consistency labels and audit list, not owner-confirmed.
- `review/public/index.html`: offline blind form; current URL `http://127.0.0.1:8770/`.
  Serve only `public/`; the key stays in its parent directory. Restore the local server below if stopped.
- `r7-unittest.log`, `validate.log`, `static-final.log`, `source-identities.json`: checks and source identities.

```bash
python3 -m http.server 8770 --bind 127.0.0.1 --directory /Users/weihaoliu/xiaoyou-experiments/decide-speak/20261027/review/public
python3 tests/test_decide_speak_compare.py
python3 tools/check_repo.py
```

Build: PASS (complete gate and image verification). Host tests / regression: PASS (R7 1,190,
experiment 6). Device tests: NOT RUN. End-to-end three-second response, connection recovery and
multi-end synchronization: NOT RUN. Repeated sampling, real clients, cold starts, recovery,
synchronization, actual billing and consistency audit confirmation remain unverified.
Production is unchanged; this handoff needs no restart or data migration.

Next discuss and agree one small change: concise wording / bubble organization, or sourced active
expression and the drive design. Preserve the fixed cases and original data; any new prompt gets
separate experimental conditions and output files. Do not rerun paid models, deploy, restart the
Runtime, change the persona, enable real helper execution, commit or push just to resume this handoff.
