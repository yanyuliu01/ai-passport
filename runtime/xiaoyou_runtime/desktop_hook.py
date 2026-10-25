"""Moved to xiaoyou_runtime/events/desktop_hook.py; ``python -m xiaoyou_runtime.desktop_hook`` keeps working for installed commands."""
import sys

from .events.desktop_hook import main

if __name__ == "__main__":
    sys.exit(main())
