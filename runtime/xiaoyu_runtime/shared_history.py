"""Moved to xiaoyu_runtime/api/shared_history.py; this path stays runnable for scripts and older instructions."""
import sys
from pathlib import Path

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from xiaoyu_runtime.api.shared_history import main
    sys.exit(main())
