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
