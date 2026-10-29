"""Legacy installed command compatibility."""
import sys
from pathlib import Path
if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from xiaoyu_runtime.events.desktop_hook import main
    raise SystemExit(main())
