#!/usr/bin/env python3
"""Count how Xiaoyu's tool-talking turns went, from the DeepSeek session files.

Read-only evidence for the decide-then-speak redesign
(docs/development/decide-then-speak.md). It prints counts and turn shapes only,
never message text, so the output can be pasted into a report.

Usage (on the computer that runs the Runtime):
  python3 tools/reminder_stats.py <sessions folder> [--log runtime/state/runtime-stderr.log]

The sessions folder is <agent config_dir or workdir>/xiaoyu-stream-sessions.
Each file keeps only the newest 40 messages (dsh_stream.trim), so older turns are gone.
"""

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

REMINDER = "（Runtime 提醒"
STREAM_LINE = re.compile(r"DeepSeek stream model=\S+ first_text=([\d.]+)s total=([\d.]+)s tools=(\S+)")
ACTIONS = {"start_task", "amend_task", "cancel_task"}


def blocks(item):
    content = item.get("content")
    return content if isinstance(content, list) else []


def turns(history):
    """Split one session into owner turns: a plain user message and what followed it."""
    current = None
    for item in history:
        if item.get("role") == "user" and isinstance(item.get("content"), str) \
                and not item["content"].startswith(REMINDER):
            if current is not None:
                yield current
            current = []
        elif current is not None:
            current.append(item)
    if current is not None:
        yield current


def shape(turn):
    """What happened in one turn, as counters."""
    seen = Counter()
    spoke_before_action = False
    acted = False
    for index, item in enumerate(turn):
        role = item.get("role")
        if role == "user" and isinstance(item.get("content"), str) and item["content"].startswith(REMINDER):
            seen["reminded"] += 1
            after = turn[index + 1:]
            names = {block.get("name") for later in after for block in blocks(later) if block.get("type") == "tool_use"}
            seen["reminder_then_tool" if names else "reminder_then_no_tool"] += 1
            if names & ACTIONS:
                seen["reminder_then_action"] += 1
            continue
        if role != "assistant":
            continue
        texts = [block for block in blocks(item) if block.get("type") == "text" and block.get("text", "").strip()]
        calls = [block.get("name") for block in blocks(item) if block.get("type") == "tool_use"]
        plain = isinstance(item.get("content"), str) and item["content"].strip() \
            and not item["content"].startswith("（")
        if set(calls) & ACTIONS:
            acted = True
            spoke_before_action = spoke_before_action or bool(texts)
        elif acted and (texts or plain):
            seen["spoke_again_after_action"] += 1
    if acted:
        seen["acted"] += 1
        if spoke_before_action:
            seen["spoke_then_acted"] += 1
    return seen


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("sessions", type=Path)
    parser.add_argument("--log", type=Path)
    args = parser.parse_args()
    total = Counter()
    files = sorted(args.sessions.glob("apt-*.json"))
    for path in files:
        try:
            history = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            total["unreadable_files"] += 1
            continue
        for turn in turns(history):
            total["turns"] += 1
            total.update(shape(turn))
    print("tool sessions: %d" % len(files))
    for key in ("turns", "acted", "spoke_then_acted", "spoke_again_after_action", "reminded",
                "reminder_then_tool", "reminder_then_action", "reminder_then_no_tool", "unreadable_files"):
        print("%-26s %d" % (key, total[key]))
    if args.log is not None:
        firsts, rounds = [], Counter()
        for line in args.log.read_text(encoding="utf-8", errors="replace").splitlines():
            found = STREAM_LINE.search(line)
            if found:
                firsts.append(float(found.group(1)))
                rounds[found.group(3)] += 1
        firsts.sort()
        if firsts:
            pick = lambda share: firsts[min(len(firsts) - 1, int(share * len(firsts)))]  # noqa: E731
            print("stream calls %d  first_text p50 %.2fs p95 %.2fs max %.2fs"
                  % (len(firsts), pick(.5), pick(.95), firsts[-1]))
            for tools, count in rounds.most_common(10):
                print("  tools=%-30s %d" % (tools, count))
    return 0


if __name__ == "__main__":
    sys.exit(main())
