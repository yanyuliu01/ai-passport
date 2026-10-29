<p align="right"><a href="first-response-measurement.zh_CN.md">简体中文</a> · <strong>English</strong></p>

# Measuring the first response on real devices

This is the recording method for the 3,000 ms first-response rule in
[cross-stack reliability](cross-stack-reliability.md), used before the
default-flow cutover (R6.3 in the [Runtime refactor design](runtime-refactor-design.md)).
The rule is unchanged: every successful sample, measured on the client that
sent the message, from Send to the first meaningful complete bubble, must be
at most 3,000 ms. Averages and percentiles do not count, and no slow sample or
timeout may be left out.

## What is measured where

| Source | What it gives | Role |
| --- | --- | --- |
| Phone logcat, tag `XiaoyuTiming` (App 0.32.4 and later) | `send`, `final` (the complete reply reached the phone) and `bubble` (the first bubble was drawn), on the phone's uptime clock, keyed by the random client id | **The measurement** |
| Runtime message records (`runtime/state/messages.json` + journal) | Client id → Runtime turn id, input kind, and whether the turn failed | Joins the two sides; marks error bubbles as misses |
| Runtime log (`runtime/state/runtime-stderr.log`) `Decide/speak` lines | Decision time, provider first body time and total speaking time for one turn | Explains where the time went |

`tools/ttft_report.py` joins these by id. It never subtracts one machine's
clock from another's. It prints and records ids and times only, never message
text. `bubble_ms - final_ms` is the phone's drawing time; the Runtime numbers
are durations inside the Runtime.

Not covered by this method: the desktop client has no timing log yet, and the
device (firmware) voice path is not logged. Record both as NOT RUN.

## Before the run

1. `xiaoyu.decide_first` only changes the path for a streaming DeepSeek voice
   agent. Set `"xiaoyu": {"decide_first": true}` in the Runtime config and
   restart the Runtime. Keep a copy of the state folder first; to roll back, set
   it to `false` and restart.
2. Write down the version tuple without secrets: App version, Runtime revision
   (`git rev-parse --short HEAD` on the Runtime computer) and version, the voice
   agent and model, the transport (same LAN, Tailscale, Funnel) and which
   computer owns the conversation.
3. Connect the phone with USB debugging and check that `adb devices` lists it.

## One run per scenario

For each scenario, clear the log, start the capture, send at least five
messages, then stop the capture with Ctrl+C:

```bash
adb logcat -c
```

```bash
adb logcat -s XiaoyuTiming > phone-cold-start.txt
```

Then, on the Runtime computer (or with a copy of its state folder and log):

```bash
python3 tools/ttft_report.py phone-cold-start.txt --state runtime/state --runtime-log runtime/state/runtime-stderr.log --scenario cold-start --record ttft-results.jsonl --note "app=0.32.6 runtime=7db5b0d voice=deepseek transport=lan"
```

The exit status is 0 when every sample passes, 1 when any sample misses or got
no bubble, and 2 when the capture holds no samples. Keep `ttft-results.jsonl`
outside the repository; it accumulates one line per sample across runs.

| Scenario | How to produce it |
| --- | --- |
| `cold-start` | Restart the Runtime, force-stop the App, open it, send |
| `warm` | Messages in a row in an active conversation |
| `long-history` | A conversation with hundreds of retained turns |
| `parallel-task` | Send while a helper task is running |
| `foreground-return` | Send right after bringing the App back from the background |
| `source-switch` | Send right after switching conversation or Runtime |
| `retry` | Turn off Wi-Fi during a send, turn it back on |
| `remote` | Phone off the LAN, reaching the Runtime over Tailscale or Funnel |
| `voice` | Hold to talk; the start is the release |
| `image` | Send a photo with a question |
| `side-conversation` | Send in a side conversation, not the main one |

## Reading the result

- `PASS`: bubble at most 3,000 ms after Send.
- `MISS`: bubble later than 3,000 ms. `MISS (error)`: the bubble was an error
  reply, which is not a meaningful response. `NO BUBBLE`: the capture ended
  before any bubble; it counts as a miss unless the capture was cut short, in
  which case repeat the run instead of deleting the line.
- For each miss, look at the stage columns: a long `decide_s` points at the
  decision call, a long `body_s` at the provider's first text, a large gap
  between `final_ms` and `bubble` at the phone's drawing.
- The cutover may only be proposed when every scenario above passes on every
  sample. Record the result, including misses, in
  [decide-then-speak](decide-then-speak.md) with the version tuple.
