<p align="right">
  <strong>简体中文</strong> · <a href="README.md">English</a>
</p>

# 资源目录（Assets）

本目录集中存放可复用的资源（字库、图片、音乐等），按资源类型分子目录管理。每个资源放在其类型对应的子目录，并记录放置路径、命名方式、集成方式与来源/许可。二进制资源（字体、图片、音频）不属于纯 markdown 文档，请勿与文档混放。涉及版权/授权的资源需注明来源与许可。

## 字库（fonts）

可复用的字库文件与生成的字库源码放在 `fonts/`。

- 命名要能反映字族、字重、字级与格式。
- 记录来源、许可、字符范围、转换命令与目标放置路径。
- 添加字库前评估 Flash 与内部 RAM 影响；ESP32-C3 无 PSRAM。
- 不提交许可不允许分发的字库。

### Claude Pocket 字库

为 [Claude Pocket](../docs/claude-pocket.zh_CN.md) 界面生成的 LVGL 位图字库。通过
`main/CMakeLists.txt` 编译进 `main`，在 `main/pocket_fonts.h` 中声明；每个标签都
显式指定自己的字库。

| 文件 | 来源与字号 | 字符范围 | 用途 |
| --- | --- | --- | --- |
| [`fonts/pocket_font_14.c`](fonts/pocket_font_14.c) | Noto Sans SC Regular，14 px，4 bpp | ASCII 和 `main/pocket_text.h` 里出现的全部字符 | 按键提示、小标签、顶栏 |
| [`fonts/pocket_font_16.c`](fonts/pocket_font_16.c) | Noto Sans SC Regular，16 px，2 bpp | ASCII、GB2312 全集（6763 个汉字及其符号）、补充标点，共 7545 个字形 | 正文，以及电脑发来的所有文字 |
| [`fonts/pocket_font_22.c`](fonts/pocket_font_22.c) | Noto Sans SC Medium，22 px，4 bpp | 与 14 px 字库相同的子集 | 标题和状态词 |
| [`fonts/pocket_font_num_44.c`](fonts/pocket_font_num_44.c) | Noto Sans SC Medium，44 px，4 bpp | 数字、空格和 `: . , - %` | 配对码 |

- **来源与许可。** 思源黑体（Noto Sans SC），取自
  [`notofonts/noto-cjk`](https://github.com/notofonts/noto-cjk) 的提交
  `f8d157532fbfaeda587e826d4cd5b21a49186f7c`，SIL Open Font License 1.1；许可全文
  保存在 [`fonts/LICENSE-NotoSansSC.txt`](fonts/LICENSE-NotoSansSC.txt)。OTF 源文件
  由生成脚本下载并校验 SHA-256，不提交到仓库。
- **生成方式。** `python3 tools/gen_pocket_fonts.py`，使用 `lv_font_conv` 1.5.3
  （不压缩、不含字距）。每个字库的精确码点、源文件哈希，以及一个请求了但源字体
  没有的字符（U+2717）都记录在 [`fonts/pocket_fonts.json`](fonts/pocket_fonts.json)。
- **覆盖检查。** `tests/test_pocket_fonts.py` 属于 `./tools/validate.sh --static`，
  它解析生成源码里的字符映射表，固定文案缺字时会失败。
- **开销。** 合计约 0.6 MB Flash（584 KB），几乎全部来自 16 px 正文字库；数据是只读的，
  不占用堆。

## 图片（images）

可复用的源图与生成的显示资产放在 `images/`。

| 文件 | 尺寸与格式 | 用途与来源 |
| --- | --- | --- |
| [`images/home.jpg`](images/home.jpg) | 3840 × 2160，JPEG | 嵌入中英文项目 README 的产品主图，突出 AI Passport 产品形象与开放、人人可创作的理念。 |
| [`images/readme-hardware-specs.png`](images/readme-hardware-specs.png) | 2172 × 724，PNG RGBA | 保留为可选技术参考图，不再用于首页主视觉。于 2026-09-17 使用内置图像生成工具为本仓库生成；已根据文档中的硬件能力契约核对图中的六项标签与参数。 |
| [`images/logo-wordmark.png`](images/logo-wordmark.png) | 1648 × 336，PNG RGBA | 从仓库原始 `images/logo.png` 中精确裁切并去除背景的黑色字标；用于中英文项目 README 的浅色主题。 |
| [`images/logo-wordmark-dark.png`](images/logo-wordmark-dark.png) | 1648 × 336，PNG RGBA | 提取字标的白色版本；README 使用 `<picture>` 在 GitHub 深色主题下显示。 |

- 使用描述性命名，并记录尺寸、像素格式、转换步骤与目标路径。
- 优先采用适合 240 × 320 RGB565 显示的格式，并纳入 Flash 与内部 RAM 考量。
- 许可允许时保留可编辑源文件，并记录来源与许可。
- 图片中不得包含设备二维码秘密、凭证或个人数据。

## 音乐与音效（music）

可复用的音乐与音效源码放在 `music/`。

- 记录来源、许可、采样率、位深、声道、转换命令与目标路径。
- 与当前 BSP 音频路径匹配时优先采用 16 kHz、16 位单声道 PCM。
- 嵌入音频前评估 Flash 与内部 RAM 成本；长录音应流式或分块。
- 无再分发许可不提交媒体文件。
