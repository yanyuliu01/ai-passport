"""Legacy desktop hook path; retained for already configured clients."""
import sys
from pathlib import Path

if __name__ == '__main__':
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from xiaoyu_runtime.api.shared_history import main
    raise SystemExit(main())
