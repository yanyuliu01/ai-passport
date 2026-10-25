#!/usr/bin/env python3
"""Moved to xiaoyou_runtime/loop/project_brief.py; this path stays runnable for configured helper commands."""
import runpy
from pathlib import Path

if __name__ == "__main__":
    runpy.run_path(str(Path(__file__).resolve().parent / "loop" / "project_brief.py"), run_name="__main__")
