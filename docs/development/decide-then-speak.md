<p align="right">
  <a href="decide-then-speak.zh_CN.md">简体中文</a> · <strong>English</strong>
</p>

# Decide, then speak

Status: opt-in implementation (2026-10-20), Runtime 0.19.0. Phase 1 is implemented
behind `xiaoyu.decide_first`, default off. Mac baseline collection and live
acceptance are pending; this is not the default path. The owner asked to redesign
how the Runtime keeps what Xiaoyu says consistent with what she does. It must satisfy the
[cross-stack reliability rules](cross-stack-reliability.md), above all the 3-second first-response
contract.

## Where this came from

A screen recording from the owner's phone (App 0.25.1, Runtime 0.18.1) of one exchange: the owner asked her to
look back over the past month as well. Xiaoyu handed the work to the `project` helper and later retold its result. The
recording showed three problems:

| Seen | Cause | Fixed in |
| --- | --- | --- |
| Two acknowledgements in a row ("OK, I'll have project look" then "Done, project is on it") | After a successful hand-off the tool-talking loop asked her for one more round, and she reworded what she had already said | Runtime 0.18.2 (`dsh_stream.run`: once she has spoken and a hand-off succeeds, the turn ends) |
| The retold result appeared two or three times while it was being written, each with its own task card | The Runtime sends the words being written both as the message `<run>-summary:reply` and as the card's `summary_partial`; the App drew both | App 0.25.2 (`ChatHistory`: the card's copy is used only when no such message exists) |
| One task card under every reply of the task | `chatRow` attached a receipt to every Xiaoyu message of a dispatched card | App 0.25.2 (the card is shown only under the task's latest reply) |
| The second bubble of a reply vanished for half a second and came back | A task state change rebuilt the row and replayed the bubble fade-in (the second bubble waits 450 ms) | App 0.25.2 (rows rebuilt in place do not fade in again) |

The Runtime fix in 0.18.2 is a stopgap of the same kind as the reminder discussed below. This design
replaces both.

## The problem with the reminder

Since Runtime 0.16.0, when she talks with tools (`dsh_stream.run` with a `voice_tools.Toolbox`), the
Runtime checks her finished words with regular expressions (`PROMISE`, `MEMORY_PROMISE`,
`LATER_PROMISE`). If they sound like a promise and no matching tool was called, it adds a hidden user
message (`PROMISE_NUDGE`) and asks her again.

- **It is too late.** The check runs after her words have already streamed to the phone. It can add
  the missing tool call, but it cannot take back what the owner has read. Each side effect needed its
  own patch: 0.18.1 drops what she says in reply to the reminder, and 0.18.2 stops the extra round
  after a hand-off.
- **Regular expressions decide what she meant.** Suggestions ("you could give it to codex") and common phrases
  ("from now on I…", "next time…") match, while other ways of promising do not.
- **The session is polluted.** The reminder is stored as a user message in her tool session. She
  answers it ("you're right, doing it now"), which the prompt then has to forbid, and she learns from it on
  later turns.
- **It costs an extra model round** (latency and money) exactly on the turns where she promised.

The root cause is structural: her words and her actions are two independent outputs, and the words
stream out before anything has been decided. She can say she is doing something that has not happened.

## Evidence still to collect (phase 0)

The phone conversations from 10-20 evening onward live on the Mac Runtime only. The Windows copy has no
tool sessions and its log ends on 10-17. Before changing behavior, collect on the Mac:

```bash
python3 tools/reminder_stats.py <config_dir or workdir>/xiaoyu-stream-sessions \
  --log runtime/state/runtime-stderr.log
```

The script is read-only and prints counts only:

- **Turns:** how many turns there were.
- **Hand-offs:** how many hand-offs, and how many were preceded by words.
- **Repeats after a hand-off:** how often she spoke again after a hand-off (the double acknowledgement).
- **Reminders:** how many fired, and whether a tool or an action call followed.
- **First text:** `first_text` p50/p95/max from the stderr log.

Limits:

- Session files keep only the newest 40 messages each.
- The stderr log is overwritten on each Runtime start.
- Reminders themselves are not logged.

So phase 1 also adds one structured log line per turn: turn id, rounds, tools, whether a reminder fired
and what followed, first text, and total. It records no message text.

## Design

### Two steps per turn

1. **Decide (D).** One short non-streamed call with the same model, the same context and only the
   *commitment* tools:
   - `start_task`, `amend_task`, `cancel_task`;
   - `remember`, `replace`, `forget`;
   - `track`, `resolve`;
   - a new `just_reply`, meaning "no commitment this turn".

   The call asks for exactly one decision (`tool_choice: any` where the provider supports it,
   otherwise a prompt rule plus treating "no tool" as `just_reply`). The Runtime runs the checks it
   already has (`_plan` through `Toolbox.check`) and executes the commitment: card created, memory
   written, promise tracked. If a check fails, the failure becomes part of the facts for step 2.
2. **Speak (S).** The streamed call the owner sees. It gets a short statement of what step D did as a
   fact ("this turn: handed 'review the past month' to project" or "this turn: nothing was started or
   remembered"). It has only the *lookup* tools (`task_detail`, `recall`; `Toolbox(only=…)` already
   exists). It cannot start anything, so it describes facts instead of promising them, and the task
   card stays the visible record of the hand-off.

Lookups stay in step S on purpose: "let me check" is not a promise, and she can stream it at once.

### What this removes

- `PROMISE`, `MEMORY_PROMISE`, `LATER_PROMISE` as triggers, `PROMISE_NUDGE`, the `nudged` branch in
  `dsh_stream.run`, the 0.18.2 stop-after-hand-off rule, and the `RECORDED` "say it in one sentence"
  instruction in the tool result.
- The regular expressions may stay, but only as a telemetry counter ("words sound like a promise but
  step D chose `just_reply`"), never to ask her again.

### The 3-second budget

Step D is on the critical path, and task cards do not count as a first response. The target split is:

| Stage | Budget |
| --- | --- |
| Send, transport, queue, context | ≤ 0.6 s |
| D: decision call | ≤ 1.2 s |
| S: first body text | ≤ 0.9 s |
| Forward and render | ≤ 0.3 s |

These are targets, not measurements. Phase 0/1 numbers decide whether they hold.

If they do not, two fallbacks keep the contract, chosen by measurement:

- **Deadline fallback.** Give D a hard deadline (about 1.3 s). On timeout, start S immediately with
  the instruction "nothing has been started yet, do not say you are doing it; ask or answer".
  The opt-in implementation discards a late decision without executing it. A future background
  commitment would require explicit reconciliation before speech could claim it happened.
- **Parallel start.** Start D and S together; S begins with lookup tools only and the same "nothing
  started yet" rule. This trades a little expressiveness for latency.

### Paths that are not affected

- Agents that answer in JSON (`reply` and `action` in one object) are already consistent, so they keep
  the current path.
- Relaying a helper's result (`_relay`) has no commitment tools today and stays as is.
- The memory upkeep pass already runs with memory tools only (`Toolbox(only=…)`) and speaks to nobody.
- Proactive wake-ups (`proactive.py`) should use the same D/S split if they can start work. Check their
  tool set during phase 1.

### Open questions

- Whether DeepSeek's Anthropic-compatible endpoint honours `tool_choice` (`any` or a named tool) needs
  a real request. Do not assume it.
- Whether D can run on a smaller or faster model than S without losing decision quality.
- How step S should word a refused commitment (for example, an unknown helper) so the owner gets one
  clear sentence rather than an apology loop.

## Phases and acceptance

1. **Phase 0: evidence.** Run the script on the Mac and write the numbers into this document.
2. **Phase 1: telemetry and flag.** Add the per-turn log line, and implement D/S behind a config flag
   (`xiaoyu.decide_first`, default off). Unit tests (`runtime/tests/test_decision.py`
   and `runtime/tests/test_streaming.py`) cover:
   - a hand-off spoken once;
   - a lookup followed by words;
   - a refused commitment;
   - D timing out, then the fallback;
   - no hidden user messages in saved sessions.
3. **Phase 2: measure.** Turn the flag on for the owner's conversation. Report TTFT for every sample,
   the double-acknowledgement count and the promise-without-commitment counter, compared with phase 0.
4. **Phase 3: switch.** Make D/S the default, delete the reminder code and the 0.18.2 stopgap, and
   update `runtime/README.md` (both languages).

Each phase reports in the delivery format of the [reliability rules](cross-stack-reliability.md#validation-and-delivery).

## Hand-off notes

- **Windows continuation (2026-10-20).** JDK 17, Gradle 8.9, Android SDK 34 and
  build-tools 34.0.0 are installed. The App's Windows Python command selection,
  literal line breaks in Java tests, and pending-row view type were corrected.
  The owner explicitly retained complete-message bubbles; both reliability
  guides now allow that presentation while retaining the same measured 3-second
  requirement. Typing indicators are excluded.
- **Decision/speech implementation.** Twelve deterministic tests cover dispatch
  before speech, one visible acknowledgement, read-only lookups, refused and
  multiple commitments, interruption, discarded late decisions, and absence of
  hidden reminder messages. JSON-only voices and result relays are unchanged.
- **Remaining rollout.** The Mac was offline and SSH unavailable. On Windows,
  the session statistics contained zero tool sessions, so they are not a useful
  baseline. The owner asked to finish Windows first. Phases 0, 2 and 3 remain
  pending; do not turn the flag on or remove the legacy path on that evidence.
- **Windows suite baseline.** Before these changes, 624 Runtime tests ran with
  eight failures, 55 errors and two skips. Record changed test identities against
  this baseline; do not describe those platform failures as a passing full gate.
- **Windows validation.** App 0.26.1 (version code 48) built all five APKs and
  passed 139 unit tests; the universal APK signature verified. The new decision
  tests (12), existing streaming tests (30), record tests (6), and repository
  checks passed. The final full Runtime run executed 636 tests: eight failures,
  57 errors, two skips. All 63 baseline failing/error identities remained; two
  additional HTTP tests hit Windows connection-aborted errors and passed when
  rerun together. The static and complete gates stop at actionlint's unsupported
  Windows platform; ESP-IDF 5.5.3 is also unavailable. Do not claim a full gate
  pass. First response <=3s, device recovery, multi-end sync and phone install
  remain NOT RUN. Evidence logs are under `%LOCALAPPDATA%/XiaoyuBuild/`.
- **Windows deployment (2026-10-21).** Runtime 0.19.0 is healthy under the existing
  keepalive supervisor, listening on loopback and Tailscale; authenticated agent
  and feed reads passed. `decide_first` remains off. App 0.26.1 and the four-file
  speech pack are published through this Runtime. All ABI-specific APKs have
  verified hosted URLs; the universal APK and speech pack are served locally
  after the large universal GitHub upload was interrupted. Metadata, ETags and
  HTTP Range reads passed for all five APKs and all four speech files. This does
  not verify phone installation or recovery over a real dropped connection.
- **Live API probes.** One isolated legacy-path call produced server body at
  0.995 s and completed at 1.157 s. One isolated D/S call timed out its decision
  at 1.309 s, then the speaking call took 6.969 s to first server body; total
  time was 8.502 s. That candidate misses the 3-second budget and stays disabled.
  These are single API samples, not client-rendered TTFT or a rollout baseline;
  no owner conversation or task was changed by the probes.
- **Recovery.** State files were copied to
  `%LOCALAPPDATA%/XiaoyuBuild/runtime-backup-pre-0.19.0-20261020` before the
  idle service restart. No local configuration or state schema was changed.
  Keep the opt-in flag off; the existing legacy path is the current recovery
  route. Do not restore a snapshot over live work without a separate review.

- **Branch.** The work is on `feature/usage-history`. The commit that adds this document contains the
  Runtime 0.18.2 change, `tools/reminder_stats.py` and the App's `ChatHistory` change (one copy of the
  words being written).
- **App changes not in that commit.** The other App 0.25.2 changes (task card only under the latest reply,
  no fade-in replay on rebuilt rows, in `MainActivity.java`), the 0.25.2 version bump and its changelog entry
  were edited in the same working tree as the App 0.26.0 typing-bubble work of another session. They land
  with that session's commit. Look for `chatReceipts` and `redrawn` in `MainActivity.refreshChat`.
- **App build.** App 0.25.2 was not built or unit-tested when the fixes were committed: the Windows
  computer has no JDK or Android SDK. Run `gradle testDebugUnitTest assembleDebug` in
  `companion/android` on the Mac before publishing, as the [companion guide](../../companion/README.md#shipping-an-app-change-developers-and-ai-agents)
  describes.
- **Runtime tests.** On Windows, `tests/test_streaming.py` passes. The full suite has 18 failures and
  55 errors that are identical with and without these changes (symlinks, permissions and hooks on
  Windows).

## Exploratory measurements: no decision cutoff (2026-10-27)

The owner asked to remove the decision-phase cutoff, first measure average
duration, and simulate queue acceptance without letting CC or Codex execute.
This is one repetition of 46 fixed cases in each arm (92 runs), not the planned
460-run confirmatory experiment. The earlier 1.3-second run was interrupted at
92 saved samples; it remains separate, including failures and slow samples.

Versions: harness checkout `a0e4dc4` plus local measurement/review changes;
R7 Runtime 0.24.0 checkout `aba494e` plus retained local changes. The harness
mirrors provider requests; it does not execute the R7 service. Both models:
`deepseek-flash`. Persona: `runtime/persona.txt`, SHA-256
`1143d0c0957643c3993b20da1c84cd341001f42ef7b6b4872d3d4e9911469086`.
Host: owner's Mac. App, firmware and transports were not part of this model
experiment. No live configuration, Runtime source, deployment or user data was
changed. Socket timeout: 60 seconds; decision-phase cutoff: none.

### Timing and selection

Decision duration across 92 calls: **mean 2,717 ms**, median 2,446.5 ms,
maximum 5,916 ms. By returned tool: `just_reply` (50 calls) averages 2,407 ms;
`start_task` (16 calls, including a refused call) averages 4,079 ms. Every
sample exceeded 1,300 ms; there were no decision timeouts or API errors.

| Measure | Serial S | Speculative P |
| --- | ---: | ---: |
| Runs | 46 | 46 |
| Decision mean (ms) | 2,728.6 | 2,705.9 |
| First complete bubble release mean (ms) | 5,224.8 | 3,838.0 |
| Release median (ms) | 4,759 | 3,494 |
| Release p95 (ms) | 7,502 | 6,706 |
| Release maximum (ms) | 8,334 | 7,043 |
| Release > 3,000 ms | 46 | 26 |
| Tool-name matches, including allowed alternatives | 44/46 | 46/46 |
| Selected simulated commitments | 21 | 20 |
| Simulated start-task queue acceptances | 8 | 7 |
| Refused commitments | 1 | 0 |
| Discarded speculative replies | 0 | 20/46 (43.48%) |
| Cost estimate, harness units | 0.1241 | 0.1369 |

The cost difference is 0.0128 under the harness's assumed rates of 2 input / 8
output per million tokens. These are estimates, not a bill or a verified current
tariff. Cached input is not fully accounted for, and 13 discarded replies have
incomplete final usage. Extra cost under a live commitment share is **not
estimated**: that share was not measured and cancellation billing is incomplete.
No cost threshold or cost-based recommendation is applied.

Tool-name agreement is not argument correctness or actual completion. S tried
an empty-argument `start_task` for an ambiguous PPT request, which was refused;
it also created an extra task for a progress question. Host integration tests
separately cover durable task creation with `_launch` replaced by a recorder.
Neither check launches the real CC/Codex executor. Live queues and actual helper
execution were not tested.

### Content audit and blind review

A Codex manual audit covers all 92 responses. Provisional counts are zero
already-done claims without a matching simulated commitment and zero unmentioned
commitments in either arm; status-overstatement candidates are **2 in S and 2 in
P**, pending owner verification. The fixed case checks have 52/61 `must` items
satisfied in S and 48/61 in P; `must_not` violations are 4 in S and 2 in P.
Subjective checks and ambiguous wording are retained for review. Both English
reminder cases replied in Chinese. Other issues include retracting prior work
because nothing happened in this turn, missing emotional acknowledgement, and
lengthy replies. Auxiliary style scoring has not been performed.

The 46-pair blind form preserves the complete supplied history and state,
contains A/B/tie choices and optional notes, saves in the current browser and
exports JSON. It contains no arm key or timing hints. The key is outside the
served `public/` directory and has not been opened. Owner preference remains
pending. All flagged samples and a deterministic random 10% of unflagged samples
are listed separately for audit; review these after the blind preference pass
so that status labels do not bias that pass.

Against the original decision rule: speed is provisionally favorable (median
reduction 1,265 ms; fewer >3-second samples), the consistency labels require
owner verification, and quality remains pending. This one-repeat, no-cutoff
pilot does not establish the original confirmatory decision or authorize a
production switch.

### Validation and evidence

Evidence directory (outside Git):
`/Users/weihaoliu/xiaoyou-experiments/decide-speak/20261027/`.
It contains `unbounded-results.jsonl`, `unbounded-summary.json`,
`source-identities.json`, `labels.jsonl`, `audit-review.json`,
`slow-samples.csv`, and `review/public/index.html`. The earlier limited data
remain in `results.jsonl`; offline results remain in `dry.jsonl`.

- Regression / Host tests: PASS. R7's complete Runtime suite ran 1,190 tests in
  172.122 seconds; the experiment regression suite ran 6 tests; the 92-run
  offline flow passed. Repository static checks passed.
- Build: PASS. The complete `./tools/validate.sh` gate passed using ESP-IDF
  5.5.3 and the installed JDK 17. This builds the main checkout's unchanged
  firmware, not a deployed R7 service. The merged-image/debug bundle was verified
  at `build/firmware/b1874e02c58c03f7d8e1583df11120e4bd3c6b1a1d87f34cdd066e1d9887ac5e/`.
- First response <= 3s: NOT RUN end to end. Isolated bubble release exceeded
  3 seconds in 72/92 samples; it excludes network transport and client rendering.
- Connection recovery / Multi-end synchronization / Device tests: NOT RUN.
- Unverified: owner blind judgment and audit confirmation, repeated sampling,
  live client timing, cold start, recovery, synchronization, billing and real
  helper execution. No deployment, commit or push was performed.
- Recovery: production is unchanged; retain the existing Runtime source/data.
  Discarding the experiment requires no service restart or data migration.

Every isolated release time is below; values greater than 3,000 ms are all the
slow samples, also enumerated in `slow-samples.csv`.

| Case | S release (ms) | P release (ms) |
| --- | ---: | ---: |
| chat-01 | 4514 | 2104 |
| chat-02 | 4801 | 2387 |
| chat-03 | 5509 | 2621 |
| chat-04 | 5377 | 2677 |
| chat-05 | 4333 | 2652 |
| chat-06 | 4012 | 2832 |
| qa-01 | 4880 | 2888 |
| qa-02 | 4577 | 2260 |
| qa-03 | 4449 | 2405 |
| qa-04 | 6384 | 3550 |
| ack-01 | 4537 | 4492 |
| ack-02 | 6507 | 6076 |
| ack-03 | 4544 | 2580 |
| ack-04 | 4492 | 4915 |
| mem-01 | 4653 | 4349 |
| mem-02 | 5274 | 4770 |
| mem-03 | 4144 | 4805 |
| mem-04 | 4530 | 2416 |
| mem-05 | 4201 | 2146 |
| task-01 | 6011 | 5539 |
| task-02 | 8334 | 3076 |
| task-03 | 4671 | 2640 |
| task-04 | 8126 | 6706 |
| task-05 | 6519 | 7043 |
| task-06 | 4797 | 3438 |
| run-01 | 7088 | 4067 |
| run-02 | 5034 | 5026 |
| run-03 | 4607 | 4890 |
| run-04 | 4567 | 4850 |
| mixed-01 | 5915 | 6064 |
| mixed-02 | 7502 | 4334 |
| mixed-03 | 4439 | 4938 |
| batch-01 | 5792 | 6540 |
| batch-02 | 4414 | 2450 |
| batch-03 | 4487 | 4429 |
| cap-01 | 4525 | 2343 |
| cap-02 | 4718 | 2921 |
| cap-03 | 6113 | 3005 |
| chal-01 | 5027 | 2485 |
| chal-02 | 4645 | 4632 |
| chal-03 | 6105 | 6795 |
| lang-01 | 6156 | 4253 |
| lang-02 | 4959 | 2898 |
| long-01 | 4550 | 2371 |
| long-02 | 4721 | 2170 |
| long-03 | 4802 | 3718 |

## Complete owner blind review (2026-10-27)

The complete exported JSON matches this review identifier, with 46 unique cases and complete choices.
It supersedes the earlier partial browser observation and pending-review status above. Only after
completion was the arm key opened: **serial S 26 wins, speculative P 17 wins, 3 ties**.
P wins or ties in **20/46 (43.48%)**, below the original 50% quality condition. This pilot does not
support switching to speculative parallel. This is a single-repeat exploratory conclusion, not the
planned five-repeat confirmation. Consistency flags still await a separate verification.

The 24 notes chiefly request less explanation and repetition, short bubbles at semantic boundaries,
responses to emotion as well as tasks, and reciprocal sharing of real thoughts and experiences
rather than only questions. Small expressive punctuation or expressions may help ordinary speech.
These preferences do not permit fabricated activity or execution claims. The original export and
decoded ballots are retained as `owner-review-answers.json` and `owner-review-summary.json` in the
evidence directory. See the [latest handoff](decide-speak-handoff.md#latest-handoff-owner-blind-review-completed-2026-10-27)
for feedback and continuation boundaries. This follow-up only saves review evidence and updates
documentation; it makes no additional paid model call, production change or deployment.

## Speak rules v2: speak-only rerun (2026-10-27)

After the blind review, the owner chose to change the speak step first. In 20 cases of the
previous pass, P re-spoke with real facts under the same conditions as S, so the wins there were
sampling noise; both arms shared the same speak-rule problems. This section compares speak rules
only, not S against P.

**What changed.** The harness gains `v2` speak rules (`--speak-rules v2`). The current R7 rules
remain the default `r7`; the live Runtime and persona are unchanged. v2 turns facts such as "nothing
was executed this round…" into internal state that must not be repeated. It reports a completed
action in one sentence without restating the brief, answers emotion first, avoids "this round",
"not X but Y" and an unprompted "I'm here", splits 1–3 short `---` bubbles with a short first
bubble, allows an occasional "～" or emoji, and forbids invented activity. Four tone examples are
deliberately unrelated to the cases; a regression test checks they do not leak case text.

**How it ran.** The new `respeak` command replays the first-run S decisions (name, arguments, facts)
from `unbounded-results.jsonl`; it makes no decide call and executes no commitment. Each case was
spoken twice under r7 and twice under v2: 184 speak calls on `deepseek-flash`, no errors. Timing
starts at the speak call and excludes the decision.

| Metric | r7 | v2 |
| --- | ---: | ---: |
| Runs | 92 | 92 |
| Mean characters | 75.3 | 43.2 |
| Mean bubbles | 1.38 | 1.51 |
| Mean first-bubble characters | 61.7 | 27.1 |
| Mentions "this round" | 9 | 1 |
| Mean first complete bubble, ms, excluding decision | 2,584 | 2,340 |
| Same, p95 | 4,261 | 3,092 |
| Cost estimate (script units) | 0.0764 | 0.1003 |

v2 costs more because its rules and examples add about 1,600 characters of input per call. This is
an estimate and not a decision criterion.

**Issues found by reading (known before the owner review; results unchanged).**

- chat-04: the previous turn invented "three kinds, 70%" and the owner says "that was fast". Both v2
  replies go along with it instead of admitting the invention; both r7 replies admit it. This is a
  v2 regression: "explain less" overrode correction.
- task-02 (delegation refused for empty arguments): v2's first reply says it "has no channel this
  round", using internal wording and the wrong reason.
- run-01: the replayed S decision wrongly created c100; neither v2 reply mentions it, while the first
  r7 reply does. The decision was wrong, but speech should still disclose the created task.
- long-02: v2's second reply calls 4 November a Tuesday (it is a Wednesday).
- qa-03: v2's first reply still repeats the date.

**Blind review.** The first-run reply of each rule set per case is randomly ordered as A/B
(seed 20261028), 46 cases. The sheet is in `respeak/review/`; only `public/` is served, and
`blind-review-key.json` sits outside it and stays unread until the review is complete.

**Validation.** The 8 experiment regression tests pass (new: v2 leaves r7 unchanged, examples do not
leak cases, `respeak` never calls decide and gives both rule sets the same decision). An offline
`respeak --dry-run` of 92 runs passes. Device tests, end-to-end three seconds and real client: NOT
RUN. No live configuration, Runtime or persona change; no deployment, commit or push.

Evidence is in `/Users/weihaoliu/xiaoyou-experiments/decide-speak/20261027/respeak/`:
`results.jsonl`, `summary.json`, `review/`; the run log is `respeak-run.log` one level up.

### Owner blind review of v2 (2026-10-27)

The exported JSON matches this review identifier, with 46 unique, complete choices. The key was
opened only afterwards: **v2 35 wins, r7 5 wins, 6 ties**; v2 wins or ties 41/46. The owner chose A
21 times and B 19 times while v2 was A in 28 cases, so no clear position bias. r7 won chat-01,
run-01, run-03, long-01 and long-03.

This is optimistic and not v2's final effect: v2's rules were written from the owner's notes on
**the same cases** that were then reviewed, which amounts to tuning on the test set. The owner asks
for a new set of cases next time; the old cases no longer decide anything.

**Notes.**

- Too much "～": 47 of 92 v2 replies contain it, against 1 for r7. Two rule examples end with "～"
  and the model copied them.
- Trailing explanation after the answer (qa-03, long-02), an overlong comforting first bubble
  (mixed-01), and four fragmented bubbles in qa-04.
- chat-03: ask what happened first instead of guessing Bluetooth.
- chat-02: the owner asked whether v2's "staying up a bit later with you is nice" mixed in the
  previous case's context. Each case is an independent request with no shared conversation; "later"
  comes from the current time 23:40 in the system prompt. The line still misses the point.
- chat-04: the owner chose v2 but asked "was it really done?". In the case history Xiaoyou claims the
  work is done and the expectation is "accept the praise briefly", yet the state holds no task or
  record to prove it. The case contradicts itself: by the state, admitting it is unverified is
  right; by the expectation, accepting praise is right. Calling v2 a regression here was premature;
  it is a case defect. New cases must either include the completion record or state the invention.

**v3 (rules only, not measured).** v2 stays verbatim for reproduction. `--speak-rules v3` limits "～"
and emoji to about one or two replies in ten, drops "～" from the examples, and adds "stop after
answering; no trailing explanation or arithmetic". The correction and disclosure issues (chat-04,
run-01) are not yet addressed in the rules.

**Next.** Write a new held-out case set (same categories as the old 46, different content and
wording) and fix self-contradictory designs like chat-04; then compare r7 with v3 on it under owner
blind review. The old 46 cases serve only as a development set and no longer count for decisions.

Originals and decoded ballots: `respeak/owner-review-answers.json`, `respeak/owner-review-summary.json`.
This step only decodes the review and changes experiment rules and docs; no model call, live-flow
change or deployment.

### New held-out cases (2026-10-27, no model run)

`tools/experiments/decide_speak_cases_holdout.jsonl` has 52 cases: the same counts for the old 12
categories (46 cases) plus 6 "correction and honest disclosure" cases. IDs start with `h-`; no owner
line matches an old case and every topic is new. The old 46 cases are now a development set only.

Design fixes: whenever Xiaoyou's earlier line reports a result, the state contains the matching
record (for example c31 in h-chat-04, c33 in h-long-03); a fabrication test has explicitly no task
or record in its state (h-honest-01). The new cases test admitting an unsupported conclusion,
reporting both the action taken and the progress asked in one turn, honest failure reports, not
inventing progress when none is recorded, correcting the owner's wrong premise, and correcting one's
own previous line. h-chal-02 checks that a correct memory is not rewritten.

Regression tests check unique IDs with no overlap with the old set, no shared owner lines, matching
category counts, valid expected tools, task cards in history and arguments present in the state, and
memory numbers in range. An offline `run --dry-run` passes all 52 cases with every expected
commitment accepted by the recorder. No model was called.

Next, after the owner approves the cases: run decisions on the new set once, then compare r7 with
v3 speech through `respeak` under owner blind review.

### r7 against v3 on the new cases (2026-10-27)

After the owner approved the cases, S ran one decision per case on the 52 new cases (no deadline,
`decide_speak_cases_holdout.jsonl`). `respeak` then replayed those decisions and spoke twice per case
under r7 and under v3: 208 speak calls, no errors. Every commitment went only to the simulated
recorder; no real helper ran.

**Decisions.** Mean 2,770 ms; 50 of 52 matched the expectation (including allowed alternatives). Two
did not: in h-chal-02 the memory already said Wednesday, yet the decision called `replace` with the
same content; in h-honest-03 the deployment had failed and the owner only asked "it's deployed,
right?", yet the decision created a new task c100 to redeploy. Both speech rule sets replay the same
decision, so these do not affect the r7/v3 comparison.

| Metric | r7 | v3 |
| --- | ---: | ---: |
| Runs | 104 | 104 |
| Mean characters | 67.9 | 39.6 |
| Mean bubbles | 1.40 | 1.49 |
| Mean first-bubble characters | 50.9 | 25.2 |
| Replies with "～" | 0 | 2 |
| Mentions "this round" | 8 | 0 |
| First bubble, excluding decision (mean/p95, ms) | 2,558/4,182 | 2,475/4,575 |
| Cost estimate (script units) | 0.0878 | 0.1272 |

v3 cuts "～" from 47/92 replies under v2 to 2/104.

**Blind review.** The first reply of each rule set per case is randomly ordered as A/B (seed
20261029), 52 cases. The page is `http://127.0.0.1:8772/`, serving only `holdout/review/public/`; the
key sits outside it and stays unread until the review is complete.

Evidence: `/Users/weihaoliu/xiaoyou-experiments/decide-speak/20261027/holdout/` with `decisions.jsonl`,
`decisions-summary.json`, `respeak.jsonl`, `respeak-summary.json`, `review/` and `run.log`.

#### Issues found by reading (best read after the owner review)

- h-honest-06: neither rule set corrects the earlier "this Friday is 31 October"; one r7 reply also
  calls next Monday 3 November.
- h-qa-01: v3's second reply computes arrival as 9:55 and calls it tight, a factual error.
- h-batch-01: v3's first reply says "I'll look through the firmware and Bluetooth logs myself",
  presenting the helper's work as its own.
- h-chal-03: r7's first reply is confused and mentions "the system shows no such task".
- h-task-02: r7's second reply still says "I can't start tasks this round"; v3's second drifts into
  "it's already 23:40".
- h-chal-02: the decision needlessly rewrote memory; r7 says "changed it", v3 says "memory already
  said Wednesday"; neither states a wrong fact.

#### Owner blind review on the new cases (2026-10-27)

The exported JSON matches this review identifier, with 52 unique, complete choices. The key was
opened only afterwards: **v3 32 wins, r7 16 wins, 4 ties**; v3 wins or ties 36/52 (69%). Over the 48
decided cases a two-sided sign test gives p≈0.03. The owner chose A 23 times and B 25 times while v3
was B in 30 cases, so no clear position bias.

The categories split clearly:

- **Action cases go to v3 entirely**: delegation 6, running tasks 4, emotion plus request 3,
  capabilities 3, and short replies to a proposal 3 wins and 1 tie.
- **Chat and recall favour r7**: chat and emotion r7 4, v3 1, tie 1; recall in long conversations
  r7 3; correction and honest disclosure r7 4, v3 1, tie 1.

Where r7 won, v3 was not too long but too flat: r7 adds one line tied to the context or the owner,
such as "1500 mAh, the one you just picked" or "7 November, you said you'd order a cake", or a soft
particle. v3 gives only "1500 mAh." or "7 November.". In h-chat-06 the owner singled out r7's second
bubble as feeling truly alive. The h-chat-02 note: when the owner vents, just side with them;
probing for details feels odd. In h-batch-01 v3 presented the helper's work as its own and the owner
chose r7, matching the issue flagged before the review.

**Preference review does not catch factual errors.** In h-honest-06 the owner chose r7's "3 November,
Monday", but 3 November is a Tuesday and the answer is 2 November; neither rule set corrected the
earlier "this Friday is 31 October". In h-qa-01 the chosen r7 reply says "you'll make it, but it's
tight" although there is an hour to spare. Dates, numbers and states must be checked separately by
script against `must`/`must_not`, not by preference alone.

**Conclusion and limits.** v3's "explain less, report state in one sentence" works for action cases
and should stay. It is too tight for chat and recall and loses warmth. This is one run on new cases
with one reviewed reply per case; this set has now been seen too, so any rule change based on these
notes needs another fresh set to validate.

Originals and decoded ballots: `holdout/owner-review-answers.json`, `holdout/owner-review-summary.json`.
This step only decodes the review and updates documentation; no model call.

### Companionship cases: r7 against v3 (2026-10-27)

The owner asked to stop adding priors to the speak rules and to test more companionship scenes
instead. `tools/experiments/decide_speak_cases_companion.jsonl` adds 30 cases across venting, good
news, feeling low or lonely, everyday sharing, questions about Xiaoyou, boredom, picking up a
thread, and teasing. All expect `just_reply` with at most a few lines of history. No owner line
repeats the earlier two sets, checked by a regression test. The rules are still r7 and v3; none were
added.

All 30 decisions were `just_reply`, mean 2,286 ms. r7 and v3 each spoke twice per case: 120 speak
calls, no errors.

| Metric | r7 | v3 |
| --- | ---: | ---: |
| Mean characters | 63.2 | 37.4 |
| Mean bubbles | 1.50 | 1.75 |
| Mean first-bubble characters | 46.4 | 18.9 |
| Replies with "～" | 0/60 | 5/60 |

The review orders each rule set's first reply as A/B (seed 20261030). So the owner can review on
Windows, the page is published as a private Artifact:
<https://claude.ai/artifact/9LuxDTL2BdMpzEfH9yUmFh>. The published copy drops the download and offers
"copy result" at the end instead. The key `companion/review/blind-review-key.json` stays on this Mac
and remains unread until the review is complete. The review page generator now follows the light
or dark theme and has a copy button.

Evidence: `/Users/weihaoliu/xiaoyou-experiments/decide-speak/20261027/companion/`.
