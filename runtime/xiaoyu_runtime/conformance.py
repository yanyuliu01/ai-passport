"""Moved to xiaoyu_runtime/api/conformance.py; ``python -m xiaoyu_runtime.conformance`` keeps working for installed commands."""
import sys

from .api.conformance import main

if __name__ == "__main__":
    sys.exit(main())
