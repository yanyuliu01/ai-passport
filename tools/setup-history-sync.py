#!/usr/bin/env python3
"""Merge the shared-history context hooks without changing other handlers."""

import argparse
import copy
import json
import math
import os
import shlex
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "runtime"))
from xiaoyu_runtime.api.shared_history import hook_config, _write_private


def merge(existing, addition, project):
    if not isinstance(existing, dict) or not isinstance(existing.get("hooks", {}), dict):
        raise ValueError("Existing hook configuration is not an object")
    result = copy.deepcopy(existing)
    hooks = result.setdefault("hooks", {})
    # Codex deserializes command timeout as u64. The approval configuration
    # generator can serialize an integral duration as 310.0; preserving that
    # representation makes the entire hook source unreadable to the CLI.
    for groups in hooks.values():
        if not isinstance(groups, list):
            raise ValueError("Existing hook event is not a list")
        for group in groups:
            if not isinstance(group, dict) or not isinstance(group.get("hooks"), list):
                raise ValueError("Existing hook group is malformed")
            for handler in group["hooks"]:
                if isinstance(handler, dict):
                    timeout = handler.get("timeout")
                    if isinstance(timeout, float) and math.isfinite(timeout) \
                            and timeout >= 0 and timeout.is_integer():
                        handler["timeout"] = int(timeout)
    for event, groups in addition["hooks"].items():
        old = hooks.get(event, [])
        if not isinstance(old, list):
            raise ValueError("Existing hook event is not a list")
        kept = []
        for group in old:
            if not isinstance(group, dict) or not isinstance(group.get("hooks"), list):
                raise ValueError("Existing hook group is malformed")
            group = copy.deepcopy(group)
            handlers = []
            for handler in group["hooks"]:
                command = handler.get("command", "") if isinstance(handler, dict) else ""
                try:
                    args = shlex.split(command) if isinstance(command, str) else []
                except ValueError:
                    args = []
                reader = str((REPO / "runtime" / "xiaoyu_runtime" / "shared_history.py").resolve())
                ours = (reader in args and "--hook" in args
                        and "--project" in args and len(args) > args.index("--project") + 1
                        and args[args.index("--project") + 1] == str(Path(project).resolve()))
                if not ours:
                    handlers.append(handler)
            if handlers:
                group["hooks"] = handlers
                kept.append(group)
        hooks[event] = kept + copy.deepcopy(groups)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--home", type=Path, default=Path.home())
    parser.add_argument("--config", type=Path, default=REPO / "runtime" / "config.json")
    parser.add_argument("--project", type=Path, default=REPO)
    parser.add_argument("--install", action="store_true", help="Install; otherwise just report the proposed changes")
    args = parser.parse_args(argv)
    proposals = []
    try:
        # Validate both destinations before changing either configuration.
        for agent, path in (("codex", args.home / ".codex" / "hooks.json"),
                            ("claude", args.home / ".claude" / "settings.json")):
            # Preserve a user's symlink to a separately managed settings file.
            path = path.resolve()
            previous = path.read_text(encoding="utf-8") if path.exists() else None
            current = json.loads(previous) if previous is not None else {}
            updated = merge(current, hook_config(args.config, args.project, agent), args.project)
            changed = json.dumps(current, sort_keys=True) != json.dumps(updated, sort_keys=True)
            proposals.append((agent, path, previous, changed, updated))
        for agent, path, previous, changed, updated in proposals:
            if args.install and changed:
                if previous is not None:
                    suffix = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
                    backup = path.with_name(path.name + ".before-shared-history-" + suffix + ".bak")
                    _write_private(backup, previous)
                _write_private(path, json.dumps(updated, ensure_ascii=False, indent=2) + "\n")
            print("%s: %s — %s" % (agent, path,
                  "installed" if args.install and changed else "unchanged" if not changed else "ready to merge"))
        print("Codex requires trust review of the updated hooks through /hooks.")
        return 0
    except (OSError, ValueError, TypeError):
        print("Could not merge shared-history hooks; existing configuration was preserved where possible.",
              file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
