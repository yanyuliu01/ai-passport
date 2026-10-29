#!/usr/bin/env python3
"""Report send-to-first-bubble times against the 3,000 ms first-response rule.

Read-only evidence for docs/development/first-response-measurement.md. The
phone's XiaoyuTiming logcat lines are the measurement: send and first bubble
on the phone's own uptime clock. The Runtime's message records and its
Decide/speak log lines only explain where the time went; they never replace the
phone's numbers, and no two machines' clocks are subtracted. Output carries
random ids and times only, never message text.

Usage:
  adb logcat -c
  adb logcat -s XiaoyuTiming > phone.txt        (stop with Ctrl+C after the run)
  python3 tools/ttft_report.py phone.txt \\
      [--state runtime/state] [--runtime-log runtime/state/runtime-stderr.log] \\
      [--scenario cold-start] [--record ttft-results.jsonl] [--note "..."]

Exit status: 0 when every sample passes, 1 when any sample misses or has no
bubble, 2 when the capture has no samples.
"""

import argparse
import json
import re
import sys
import time
from pathlib import Path

LIMIT_MS = 3000
PHONE = re.compile(r"\b(send|final|bubble) key=(\S+) t=(\d+)(?: elapsed_ms=(-?\d+))?")
SPEAK = re.compile(r"Decide/speak turn=(\S+) decision=(\S+) decision_s=([\d.]+) "
                   r"first_body_s=(\S+) total_s=([\d.]+)")
FAILED = ("failed", "cancelled")


def phone_samples(lines):
    """One sample per client key, in send order. A retry keeps the first send."""
    samples = {}
    for line in lines:
        match = PHONE.search(line)
        if not match:
            continue
        kind, key, at, elapsed = match.group(1), match.group(2), int(match.group(3)), match.group(4)
        if kind == "send":
            samples.setdefault(key, {"key": key, "sent": at, "final_ms": None, "bubble_ms": None})
            continue
        sample = samples.get(key)
        if sample is None:
            continue  # The send happened before this capture began.
        value = int(elapsed) if elapsed is not None else at - sample["sent"]
        if kind == "final" and sample["final_ms"] is None:
            sample["final_ms"] = value
        elif kind == "bubble" and sample["bubble_ms"] is None:
            sample["bubble_ms"] = value
    return sorted(samples.values(), key=lambda item: item["sent"])


def runtime_messages(state):
    """client_id -> the Runtime's record of that turn (read-only)."""
    if state is None:
        return {}
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "runtime"))
    from xiaoyu_runtime.events import ledger
    path = Path(state) / "messages.json"
    if not path.exists():
        raise SystemExit("No messages.json under %s" % state)
    found = {}
    for item in ledger.current(path)["messages"]:
        if item.get("client_id"):
            found[item["client_id"]] = item
    return found


def speak_lines(path):
    """Runtime turn id -> its last Decide/speak stage line."""
    if path is None:
        return {}
    stages = {}
    with open(path, encoding="utf-8", errors="replace") as stream:
        for line in stream:
            match = SPEAK.search(line)
            if match:
                stages[match.group(1)] = {
                    "decision": match.group(2), "decision_s": float(match.group(3)),
                    "first_body_s": None if match.group(4) == "none" else float(match.group(4)),
                    "total_s": float(match.group(5))}
    return stages


def judge(sample, message):
    if sample["bubble_ms"] is None:
        return "NO BUBBLE"
    if message is not None and (message.get("status") in FAILED or message.get("error")):
        return "MISS (error)"  # An error bubble is not a meaningful response.
    return "PASS" if sample["bubble_ms"] <= LIMIT_MS else "MISS"


def rows(samples, messages, stages):
    result = []
    for sample in samples:
        message = messages.get(sample["key"])
        turn = (message or {}).get("id")
        row = dict(sample, verdict=judge(sample, message), turn=turn,
                   kind=(message or {}).get("kind"), status=(message or {}).get("status"))
        row.update(stages.get(turn, {}) if turn else {})
        result.append(row)
    return result


def seconds(value):
    return "-" if value is None else "%.2f" % value


def show(found, scenario):
    print("Scenario: %s" % (scenario or "(unnamed)"))
    print("%-10s %-6s %8s %8s  %-12s %-10s %8s %8s %8s" % (
        "key", "kind", "final_ms", "bubble", "verdict", "decision", "decide_s", "body_s", "total_s"))
    for row in found:
        print("%-10s %-6s %8s %8s  %-12s %-10s %8s %8s %8s" % (
            row["key"][:10], row.get("kind") or "?",
            "-" if row["final_ms"] is None else row["final_ms"],
            "-" if row["bubble_ms"] is None else row["bubble_ms"],
            row["verdict"], row.get("decision") or "-", seconds(row.get("decision_s")),
            seconds(row.get("first_body_s")), seconds(row.get("total_s"))))
    timed = [row["bubble_ms"] for row in found if row["bubble_ms"] is not None]
    passed = sum(row["verdict"] == "PASS" for row in found)
    print()
    print("Samples: %d  pass: %d  miss: %d  max bubble: %s ms" % (
        len(found), passed, len(found) - passed, max(timed) if timed else "-"))
    print("Result: %s (every sample must be <= %d ms; none are excluded)" % (
        "PASS" if found and passed == len(found) else "FAIL" if found else "NO SAMPLES", LIMIT_MS))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("logcat", help="file captured with adb logcat -s XiaoyuTiming")
    parser.add_argument("--state", help="Runtime state folder, for kinds, failures and turn ids")
    parser.add_argument("--runtime-log", help="Runtime stderr log, for Decide/speak stage times")
    parser.add_argument("--scenario", default="", help="label for this run, such as cold-start")
    parser.add_argument("--record", help="append one JSON line per sample to this file")
    parser.add_argument("--note", default="", help="versions, transport and settings for the record")
    args = parser.parse_args(argv)
    with open(args.logcat, encoding="utf-8", errors="replace") as stream:
        samples = phone_samples(stream)
    found = rows(samples, runtime_messages(args.state), speak_lines(args.runtime_log))
    show(found, args.scenario)
    if args.record and found:
        recorded = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        with open(args.record, "a", encoding="utf-8") as out:
            for row in found:
                out.write(json.dumps(dict(row, scenario=args.scenario, note=args.note,
                                          recorded=recorded), ensure_ascii=False) + "\n")
        print("Recorded %d samples in %s" % (len(found), args.record))
    if not found:
        return 2
    return 0 if all(row["verdict"] == "PASS" for row in found) else 1


if __name__ == "__main__":
    sys.exit(main())
