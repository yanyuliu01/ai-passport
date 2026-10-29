"""Legacy installed command compatibility."""
import sys
from pathlib import Path
if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from xiaoyu_runtime.helpers.permission_mcp import main
    raise SystemExit(main())
