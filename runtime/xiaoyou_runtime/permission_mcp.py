"""Moved to xiaoyou_runtime/helpers/permission_mcp.py; ``python -m xiaoyou_runtime.permission_mcp`` keeps working for installed commands."""
import sys

from .helpers.permission_mcp import main

if __name__ == "__main__":
    sys.exit(main())
