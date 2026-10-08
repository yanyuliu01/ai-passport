#!/usr/bin/env python3
"""Glyph-coverage gate for the Claude Pocket UI fonts.

A successful build does not prove Chinese text renders: a character missing from
the font shows up as a placeholder box on the device. This test reads the
character maps straight out of the generated LVGL font sources and checks that

  * every font matches the code points recorded in assets/fonts/pocket_fonts.json,
  * every fixed UI string in main/pocket_text.h is fully covered by each text font,
  * the body font covers all of GB2312 level 1 (the documented dynamic-text set),
  * known-absent characters really are absent (so the check cannot pass blindly),
  * no other source file under main/ carries display text of its own.

It needs neither ESP-IDF nor LVGL.
"""

from __future__ import annotations

import json
import re
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tools"))

import gen_pocket_fonts  # noqa: E402

FONT_DIR = REPO / "assets" / "fonts"
TEXT_FONTS = ("pocket_font_14", "pocket_font_16", "pocket_font_22")
ALL_FONTS = TEXT_FONTS + ("pocket_font_num_44",)


def parse_array(source: str, name: str) -> list[int]:
    match = re.search(
        r"static const uint(?:8|16)_t " + re.escape(name) + r"\[\] = \{(.*?)\};",
        source,
        re.S,
    )
    if match is None:
        raise AssertionError(f"array {name} not found")
    return [int(token, 0) for token in re.findall(r"0x[0-9a-fA-F]+|\d+", match.group(1))]


def font_codepoints(path: Path) -> set[int]:
    """Decode lv_font_fmt_txt_cmap_t tables the way lv_font_fmt_txt.c does."""
    source = path.read_text(encoding="utf-8")
    block = re.search(
        r"static const lv_font_fmt_txt_cmap_t cmaps\[\] =\s*\{(.*?)\n\};", source, re.S
    )
    if block is None:
        raise AssertionError(f"{path.name}: cmaps table not found")
    points: set[int] = set()
    entries = re.findall(
        r"\.range_start = (\d+), \.range_length = (\d+), \.glyph_id_start = (\d+),\s*"
        r"\.unicode_list = (\w+), \.glyph_id_ofs_list = (\w+), \.list_length = (\d+), "
        r"\.type = (LV_FONT_FMT_TXT_CMAP_\w+)",
        block.group(1),
    )
    if not entries:
        raise AssertionError(f"{path.name}: no cmap entries parsed")
    for start, length, _glyph_start, unicode_list, ofs_list, list_length, kind in entries:
        start, length, list_length = int(start), int(length), int(list_length)
        if kind == "LV_FONT_FMT_TXT_CMAP_FORMAT0_TINY":
            points.update(range(start, start + length))
        elif kind == "LV_FONT_FMT_TXT_CMAP_SPARSE_TINY":
            offsets = parse_array(source, unicode_list)
            assert len(offsets) == list_length, (path.name, unicode_list)
            points.update(start + offset for offset in offsets)
        elif kind == "LV_FONT_FMT_TXT_CMAP_FORMAT0_FULL":
            offsets = parse_array(source, ofs_list)
            assert len(offsets) == length, (path.name, ofs_list)
            # Offset 0 maps to the range's first glyph; lv_font_conv only emits
            # it for index 0, every other zero marks a hole in the range.
            points.update(
                start + index
                for index, offset in enumerate(offsets)
                if index == 0 or offset != 0
            )
        elif kind == "LV_FONT_FMT_TXT_CMAP_SPARSE_FULL":
            offsets = parse_array(source, unicode_list)
            assert len(offsets) == list_length, (path.name, unicode_list)
            points.update(start + offset for offset in offsets)
        else:
            raise AssertionError(f"{path.name}: unknown cmap type {kind}")
    return points


def expand_ranges(text: str) -> set[int]:
    points: set[int] = set()
    for part in text.split(","):
        if "-" in part:
            low, high = part.split("-")
            points.update(range(int(low, 16), int(high, 16) + 1))
        else:
            points.add(int(part, 16))
    return points


def describe(points) -> str:
    listed = sorted(points)
    shown = " ".join(f"U+{cp:04X}({chr(cp)})" for cp in listed[:20])
    return shown + (f" … +{len(listed) - 20}" if len(listed) > 20 else "")


class PocketFontCoverage(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.manifest = json.loads((FONT_DIR / "pocket_fonts.json").read_text("utf-8"))
        cls.fonts = {name: font_codepoints(FONT_DIR / f"{name}.c") for name in ALL_FONTS}
        cls.ui = set(gen_pocket_fonts.ui_codepoints())

    def test_sources_match_the_recorded_inventory(self):
        for name in ALL_FONTS:
            recorded = expand_ranges(self.manifest["fonts"][name]["codepoints"])
            self.assertEqual(
                self.fonts[name], recorded,
                f"{name}.c and pocket_fonts.json disagree; rerun tools/gen_pocket_fonts.py",
            )
            self.assertEqual(len(recorded), self.manifest["fonts"][name]["count"])

    def test_every_fixed_string_is_covered_by_each_text_font(self):
        self.assertGreater(len(self.ui), 150)
        for name in TEXT_FONTS:
            missing = self.ui - self.fonts[name]
            self.assertFalse(
                missing,
                f"{name} lacks UI characters {describe(missing)}; "
                "rerun tools/gen_pocket_fonts.py after editing main/pocket_text.h",
            )

    def test_body_font_covers_gb2312_level1_and_ascii(self):
        body = self.fonts["pocket_font_16"]
        level1 = set(gen_pocket_fonts.gb2312_level1())
        self.assertEqual(len(level1), 3755)
        self.assertFalse(level1 - body, describe(level1 - body))
        self.assertFalse(set(range(0x20, 0x7F)) - body)
        for codepoint in (0x3001, 0x3002, 0xFF0C, 0xFF1A, 0xFF1F, 0x201C, 0x201D, 0x2026):
            self.assertIn(codepoint, body, f"U+{codepoint:04X}")

    def test_digit_font_covers_passkey_characters(self):
        digits = self.fonts["pocket_font_num_44"]
        self.assertFalse({ord(ch) for ch in "0123456789 "} - digits)

    def test_known_absent_characters_are_reported_absent(self):
        # U+9F98 is outside GB2312 and outside every UI string.
        for name in ALL_FONTS:
            self.assertNotIn(0x9F98, self.fonts[name], name)
        self.assertNotIn(0x4E2D, self.fonts["pocket_font_num_44"])
        # The UI subset fonts must not be mistaken for the full body font.
        self.assertLess(len(self.fonts["pocket_font_14"]), 1000)
        self.assertGreater(len(self.fonts["pocket_font_16"]), 7000)

    def test_display_text_lives_only_in_pocket_text_h(self):
        offenders = []
        for path in sorted((REPO / "main").glob("*.[ch]")):
            if path.name == "pocket_text.h":
                continue
            source = path.read_text(encoding="utf-8")
            code = re.sub(r"//[^\n]*|/\*.*?\*/", "", source, flags=re.S)
            for literal in re.findall(r'"((?:[^"\\\n]|\\.)*)"', code):
                if re.search(r"[^\x00-\x7F]", literal) or re.search(
                    r"\\x[89A-Fa-f][0-9A-Fa-f]", literal
                ):
                    offenders.append(f"{path.name}: {literal!r}")
        self.assertFalse(
            offenders,
            "non-ASCII display text outside main/pocket_text.h is invisible to the "
            "font generator: " + "; ".join(offenders),
        )


if __name__ == "__main__":
    unittest.main()
