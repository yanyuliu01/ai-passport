<p align="right">
  <a href="README.zh_CN.md">简体中文</a> · <strong>English</strong>
</p>

# Assets

This directory stores reusable fonts, images, music, and sound effects, organized by asset type.

Keep each asset in the matching subdirectory and document its destination, naming, integration method, and source/license. Do not mix binary assets with Markdown documentation.

## Fonts

Store reusable font files and generated font sources in `fonts/`.

- Use descriptive names that include the family, weight, size, and format when relevant.
- Document the source, license, character range, conversion command, and expected destination.
- Check Flash and internal-RAM impact before adding a font; the ESP32-C3 has no PSRAM.
- Do not commit fonts whose license does not permit redistribution.

### Claude Pocket fonts

Generated LVGL bitmap fonts for the [Claude Pocket](../docs/claude-pocket.md)
interface. They are compiled into `main` through `main/CMakeLists.txt` and
declared in `main/pocket_fonts.h`; each label selects its font explicitly.

| File | Source and size | Characters | Used for |
| --- | --- | --- | --- |
| [`fonts/pocket_font_14.c`](fonts/pocket_font_14.c) | Noto Sans SC Regular, 14 px, 4 bpp | ASCII and every character of `main/pocket_text.h` | Key hints, captions, top bar |
| [`fonts/pocket_font_16.c`](fonts/pocket_font_16.c) | Noto Sans SC Regular, 16 px, 2 bpp | ASCII, all of GB2312 (6,763 hanzi and its symbols), extra punctuation; 7,545 glyphs | Body text and everything received from the computer |
| [`fonts/pocket_font_22.c`](fonts/pocket_font_22.c) | Noto Sans SC Medium, 22 px, 4 bpp | Same subset as the 14 px font | Titles and status words |
| [`fonts/pocket_font_num_44.c`](fonts/pocket_font_num_44.c) | Noto Sans SC Medium, 44 px, 4 bpp | Digits, space, and `: . , - %` | Pairing passkey |

- **Source and license.** Noto Sans SC from
  [`notofonts/noto-cjk`](https://github.com/notofonts/noto-cjk) at commit
  `f8d157532fbfaeda587e826d4cd5b21a49186f7c`, SIL Open Font License 1.1; the
  license text is kept in
  [`fonts/LICENSE-NotoSansSC.txt`](fonts/LICENSE-NotoSansSC.txt). The OTF
  sources are downloaded and SHA-256 checked by the generator, not committed.
- **Generation.** `python3 tools/gen_pocket_fonts.py` with `lv_font_conv` 1.5.3
  (uncompressed, no kerning). The exact code points of every font, the source
  hashes, and one requested character the source lacks (U+2717) are recorded in
  [`fonts/pocket_fonts.json`](fonts/pocket_fonts.json).
- **Coverage check.** `tests/test_pocket_fonts.py`, part of
  `./tools/validate.sh --static`, decodes the character maps of the generated
  sources and fails when a fixed string loses a glyph.
- **Cost.** About 0.6 MB of Flash in total (584 KB), almost all of it the 16 px body
  font; the data is read-only and does not use heap.

## Images

Store reusable source images and generated display assets in `images/`.

| File | Dimensions and format | Use and source |
| --- | --- | --- |
| [`images/home.jpg`](images/home.jpg) | 3840 × 2160, JPEG | Product hero image embedded in both project README files to foreground AI Passport and its open, maker-oriented identity. |
| [`images/readme-hardware-specs.png`](images/readme-hardware-specs.png) | 2172 × 724, PNG RGBA | Optional technical infographic retained as a reference asset; it is no longer used as the homepage hero. Generated for this repository with the built-in image generation tool on 2026-09-17; the six labels and values were checked against the documented hardware contract. |
| [`images/logo-wordmark.png`](images/logo-wordmark.png) | 1648 × 336, PNG RGBA | Transparent black wordmark extracted from the repository's original `images/logo.png`; embedded in both project README files for light backgrounds. |
| [`images/logo-wordmark-dark.png`](images/logo-wordmark-dark.png) | 1648 × 336, PNG RGBA | White version of the extracted wordmark, used by the README `<picture>` element when GitHub is in dark mode. |

- Use descriptive names and document dimensions, pixel format, conversion steps, and destination.
- Prefer formats suitable for the 240 × 320 RGB565 display and account for Flash and internal RAM.
- Preserve editable sources where licensing permits, and record the source and license.
- Never commit device QR secrets, credentials, or personal data in images.

## Music and sound effects

Store reusable music and sound-effect sources in `music/`.

- Document the source, license, sample rate, bit depth, channels, conversion command, and destination.
- Prefer 16 kHz, 16-bit mono PCM when it matches the current BSP audio path.
- Check Flash and internal-RAM cost before embedding audio; stream or chunk long recordings.
- Do not commit media without redistribution permission.
