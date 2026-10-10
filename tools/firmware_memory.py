#!/usr/bin/env python3
"""List the largest fixed data (.bss / .data) of a firmware build from its linker map.

    python3 tools/firmware_memory.py build/FoloToy-AI-Passport.map [--min BYTES]

The board has no PSRAM: the interface, Bluetooth and the task stacks share what
the fixed data leaves of the internal memory. This prints the totals of the
data sections and every symbol of at least --min bytes (default 512), largest
first, so that two builds can be compared and a structure that grew is found
without a device.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

SYMBOL = re.compile(
    r"^ \.(bss|data|noinit)\.(\S+)\s*\n?\s+0x[0-9a-f]+\s+0x([0-9a-f]+)\s+(\S+)", re.M)
SECTION = re.compile(r"^(\.dram0\.bss|\.dram0\.data|\.iram0\.text|\.noinit)\s+0x[0-9a-f]+\s+0x([0-9a-f]+)",
                     re.M)


def report(text: str, minimum: int) -> str:
    lines = []
    for match in SECTION.finditer(text):
        lines.append("%8d  %s (whole section)" % (int(match.group(2), 16), match.group(1)))
    rows = []
    for match in SYMBOL.finditer(text):
        size = int(match.group(3), 16)
        if size >= minimum:
            rows.append((size, match.group(1), match.group(2), match.group(4).split("/")[-1]))
    for size, section, name, where in sorted(rows, reverse=True):
        lines.append("%8d  .%s  %s  (%s)" % (size, section, name, where))
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("map", type=Path)
    parser.add_argument("--min", type=int, default=512, dest="minimum")
    args = parser.parse_args()
    try:
        text = args.map.read_text(encoding="utf-8", errors="replace")
    except OSError as error:
        print("cannot read %s: %s" % (args.map, error), file=sys.stderr)
        return 1
    print(report(text, args.minimum))
    return 0


if __name__ == "__main__":
    sys.exit(main())
