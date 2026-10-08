#!/usr/bin/env python3
"""Generate the LVGL bitmap fonts used by the Claude Pocket UI.

Inputs
  * main/pocket_text.h      every fixed UI string (scanned for its characters)
  * Noto Sans SC (OFL 1.1)  downloaded at a pinned commit and checked by SHA-256

Outputs (all under assets/fonts/, committed)
  * pocket_font_14.c     UI subset, Regular 14 px, 4 bpp   hints and labels
  * pocket_font_16.c     GB2312 + ASCII, Regular 16 px, 2 bpp   dynamic text
  * pocket_font_22.c     UI subset, Medium 22 px, 4 bpp    titles
  * pocket_font_num_44.c digits, Medium 44 px, 4 bpp       passkey and figures
  * pocket_fonts.json    converter version, source hashes, exact code points

Requirements: Python 3 with fontTools, Node.js with npx (lv_font_conv is pinned
below and fetched by npx). Run from anywhere:

    python3 tools/gen_pocket_fonts.py [--font-dir DIR]

Re-run after changing main/pocket_text.h, then run tests/test_pocket_fonts.py.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
TEXT_HEADER = REPO / "main" / "pocket_text.h"
OUT_DIR = REPO / "assets" / "fonts"

LV_FONT_CONV = "lv_font_conv@1.5.3"
NOTO_COMMIT = "f8d157532fbfaeda587e826d4cd5b21a49186f7c"
NOTO_URL = (
    "https://raw.githubusercontent.com/notofonts/noto-cjk/"
    + NOTO_COMMIT
    + "/Sans/SubsetOTF/SC/{name}"
)
SOURCES = {
    "NotoSansSC-Regular.otf":
        "faa6c9df652116dde789d351359f3d7e5d2285a2b2a1f04a2d7244df706d5ea9",
    "NotoSansSC-Medium.otf":
        "7633f5a016d4dd95e685a69633d818aabc4644c4b08e26bd35b1b30c45ed5dda",
}

ASCII = list(range(0x20, 0x7F))
# Punctuation that host-supplied text commonly contains but GB2312 lacks.
EXTRA_PUNCTUATION = [
    0x00B7,  # ·
    0x2013, 0x2014, 0x2018, 0x2019, 0x201C, 0x201D, 0x2022, 0x2026,
    0x2190, 0x2191, 0x2192, 0x2193,  # arrows
    0x2713, 0x2717,  # check / cross
    0x3000,  # ideographic space
]


def string_literals(header: str) -> list[str]:
    """Return the decoded C string literals of pocket_text.h (comments removed)."""
    without_comments = re.sub(r"//[^\n]*", "", header)
    literals = []
    for match in re.finditer(r'"((?:[^"\\\n]|\\.)*)"', without_comments):
        body = match.group(1)
        if re.search(r"\\[^n]", body):
            raise SystemExit(
                f"pocket_text.h: only \\n escapes are allowed, found: {body!r}"
            )
        literals.append(body.replace("\\n", "\n"))
    return literals


def ui_codepoints() -> list[int]:
    chars = set()
    for literal in string_literals(TEXT_HEADER.read_text(encoding="utf-8")):
        chars.update(ord(ch) for ch in literal if ch != "\n")
    return sorted(chars | set(ASCII))


def gb2312_codepoints() -> list[int]:
    """Every character GB2312 defines (symbols rows 1-9 and hanzi rows 16-87)."""
    points = set()
    for row in list(range(0xA1, 0xAA)) + list(range(0xB0, 0xF8)):
        for cell in range(0xA1, 0xFF):
            try:
                text = bytes([row, cell]).decode("gb2312")
            except UnicodeDecodeError:
                continue
            points.add(ord(text))
    return sorted(points)


def gb2312_level1() -> list[int]:
    points = []
    for row in range(0xB0, 0xD8):
        for cell in range(0xA1, 0xFF):
            try:
                points.append(ord(bytes([row, cell]).decode("gb2312")))
            except UnicodeDecodeError:
                continue
    return points


def fetch_sources(font_dir: Path) -> dict[str, Path]:
    font_dir.mkdir(parents=True, exist_ok=True)
    paths = {}
    for name, expected in SOURCES.items():
        path = font_dir / name
        if not path.exists():
            print(f"downloading {name}", file=sys.stderr)
            urllib.request.urlretrieve(NOTO_URL.format(name=name), path)
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual != expected:
            raise SystemExit(f"{path}: sha256 {actual}, expected {expected}")
        paths[name] = path
    return paths


def present_in(font_path: Path, codepoints: list[int]) -> tuple[list[int], list[int]]:
    from fontTools.ttLib import TTFont

    cmap = TTFont(str(font_path), lazy=True).getBestCmap()
    present = [cp for cp in codepoints if cp in cmap]
    missing = [cp for cp in codepoints if cp not in cmap]
    return present, missing


def ranges(codepoints: list[int]) -> str:
    parts = []
    start = previous = None
    for cp in codepoints:
        if start is None:
            start = previous = cp
        elif cp == previous + 1:
            previous = cp
        else:
            parts.append((start, previous))
            start = previous = cp
    if start is not None:
        parts.append((start, previous))
    return ",".join(
        f"0x{a:X}" if a == b else f"0x{a:X}-0x{b:X}" for a, b in parts
    )


def convert(name: str, font: Path, size: int, bpp: int, codepoints: list[int]) -> Path:
    output = OUT_DIR / f"{name}.c"
    subprocess.run(
        [
            "npx", "--yes", LV_FONT_CONV,
            "--font", str(font),
            "--range", ranges(codepoints),
            "--size", str(size), "--bpp", str(bpp),
            "--format", "lvgl", "--no-compress", "--no-kerning",
            "--lv-font-name", name, "--lv-include", "lvgl.h",
            "--output", str(output),
        ],
        check=True,
    )
    # The converter echoes its full command line (with local paths) into the
    # header comment; keep the output reproducible and free of local paths.
    text = output.read_text(encoding="utf-8")
    text = re.sub(
        r"\* Opts: .*?\n",
        f"* Opts: generated by tools/gen_pocket_fonts.py ({font.name}, "
        f"{size} px, {bpp} bpp, {len(codepoints)} code points)\n",
        text,
        count=1,
        flags=re.S,
    )
    output.write_text(text, encoding="utf-8")
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument(
        "--font-dir",
        type=Path,
        default=REPO / "build" / "font-sources",
        help="where the Noto Sans SC sources are cached (default: build/font-sources)",
    )
    args = parser.parse_args()

    sources = fetch_sources(args.font_dir)
    regular = sources["NotoSansSC-Regular.otf"]
    medium = sources["NotoSansSC-Medium.otf"]

    ui = ui_codepoints()
    body_request = sorted(set(ui) | set(gb2312_codepoints()) | set(EXTRA_PUNCTUATION))
    digits = sorted({ord(ch) for ch in "0123456789 :.,-%"})

    plan = [
        ("pocket_font_14", regular, 14, 4, ui),
        ("pocket_font_16", regular, 16, 2, body_request),
        ("pocket_font_22", medium, 22, 4, ui),
        ("pocket_font_num_44", medium, 44, 4, digits),
    ]

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    manifest = {
        "converter": LV_FONT_CONV,
        "source_repository": "https://github.com/notofonts/noto-cjk",
        "source_commit": NOTO_COMMIT,
        "source_license": "SIL Open Font License 1.1",
        "source_sha256": SOURCES,
        "fonts": {},
    }
    failed = False
    for name, font, size, bpp, requested in plan:
        present, missing = present_in(font, requested)
        # A fixed UI string must never lose a glyph silently.
        ui_missing = [cp for cp in missing if cp in ui]
        if ui_missing and name != "pocket_font_num_44":
            print(
                f"{name}: source font lacks UI characters: "
                + " ".join(f"U+{cp:04X}" for cp in ui_missing),
                file=sys.stderr,
            )
            failed = True
        output = convert(name, font, size, bpp, present)
        manifest["fonts"][name] = {
            "source": font.name,
            "size_px": size,
            "bpp": bpp,
            "codepoints": ranges(present),
            "count": len(present),
            "requested_but_absent_in_source": [f"U+{cp:04X}" for cp in missing],
        }
        print(f"{name}: {len(present)} glyphs, {output.stat().st_size} bytes of C")

    level1 = set(gb2312_level1())
    body_present = set(present_in(regular, body_request)[0])
    manifest["gb2312_level1_covered_by_pocket_font_16"] = level1 <= body_present
    (OUT_DIR / "pocket_fonts.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
