[English](ai-companion-design.md) · **简体中文**

# AI 伙伴形象设计素材包

2026-10-12 使用内置图像生成工具生成。三位角色在固件像素风与 app Q 版画风中保留统一辨识特征：蓝色鲸鱼女仆、橙发 Claude 学者、银紫长发的优雅 Codex 伙伴。这些是非官方、参考社区形象的设计。

本轮以用户选中的优雅 Codex 图集为画风基准，DSH 和 Claude 的全身图、头像及大肥鱼情绪扩展均更新为 v2。推荐源图共 15 张、208 个绘制姿态（包含画风备选，不是 176 种不同情绪）；之前版本保留为草稿。

## 接近小幽质感的豆豆眼备选

这版沿用现有固件小幽的质感：大像素方块、豆豆眼或竖条眼、小嘴巴、极少颜色。[风格参考](../../../assets/images/ai-companions/reference-xiaoyou-style.png)直接从 `main/pocket_pet.c` 渲染，没有修改固件。

- [三角色豆豆眼对比](../../../assets/images/ai-companions/avatar-dot-eyes-elegant-v1.png)：每位八种表情，三行依次为大肥鱼、Claude、优雅 Codex。
- [大肥鱼全身豆豆眼概念](../../../assets/images/ai-companions/fw-whale-dot-eyes-v1.png)：十二个工作状态姿态。
- [优雅 Codex 豆豆眼头像](../../../assets/images/ai-companions/avatar-codex-dot-eyes-v1.png)：八种表情。

![豆豆眼备选](../../../assets/images/ai-companions/avatar-dot-eyes-elegant-v1.png)

![大肥鱼豆豆眼小精灵](../../../assets/images/ai-companions/fw-whale-dot-eyes-v1.png)

头像对比每行顺序为平常、开心、思考、专注、困惑、惊讶、出错、睡觉。它们是源设计，20×20 逻辑网格目标仍需要确定性的像素整理才能用于固件。

## 固件大头头像

三张 4×4 图集，共 48 个头像表情。大脸、阶梯轮廓和鲜明配色，用于小尺寸状态标识与对话头像。提示词中的 24×24 是像素设计目标，不代表已验证的原生精灵分辨率；交付 PNG 是高清源图集。

| 角色 | 源图集 |
| --- | --- |
| 大肥鱼女仆 | [16 个表情](../../../assets/images/ai-companions/avatar-whale-v2.png) |
| Claude 学者 | [16 个表情](../../../assets/images/ai-companions/avatar-claude-v2.png) |
| Codex 优雅伙伴 | [16 个表情](../../../assets/images/ai-companions/avatar-codex-elegant-v1.png) |

每张按从左到右、从上到下阅读：平常、开心、认真听、思考；专注、怀疑、困惑、惊讶；紧张、道歉、难过、烦躁；疲惫、睡觉、得意、害羞。生成表情是这些预期状态的视觉近似，状态名称不写入画面。

![大肥鱼大头头像](../../../assets/images/ai-companions/avatar-whale-v2.png)

![Claude 大头头像](../../../assets/images/ai-companions/avatar-claude-v2.png)

![Codex 大头头像](../../../assets/images/ai-companions/avatar-codex-elegant-v1.png)

## 固件全身像素概念

三张 4×3 图集，共 36 个工作姿态。这部分的像素细节比大头头像多，作为首页较大角色的可选源设计。

| 角色 | 源图集 |
| --- | --- |
| 大肥鱼女仆 | [12 个工作姿态](../../../assets/images/ai-companions/fw-whale-v2.png) |
| Claude 学者 | [12 个工作姿态](../../../assets/images/ai-companions/fw-claude-v2.png) |
| Codex 优雅伙伴 | [12 个工作姿态](../../../assets/images/ai-companions/fw-codex-elegant-v1.png) |

工作姿态按从左到右、从上到下阅读：待命、倾听、思考、敲键盘；查阅、检查、等待确认、困惑；庆祝、出错、重试、休息。

![固件大肥鱼工作姿态](../../../assets/images/ai-companions/fw-whale-v2.png)

## app Q 版形象

四张 4×3 图集，共 48 个姿态：三位角色各 12 个工作姿态，大肥鱼另有 12 个情绪扩展。平滑轮廓、更丰富的头发服装细节与面部表现，适合 app 伙伴区或任务卡片。

| 角色 | 源图集 |
| --- | --- |
| 大肥鱼女仆 | [12 个工作姿态](../../../assets/images/ai-companions/app-whale-v2.png) |
| Claude 学者 | [12 个工作姿态](../../../assets/images/ai-companions/app-claude-v2.png) |
| Codex 优雅伙伴 | [12 个工作姿态](../../../assets/images/ai-companions/app-codex-elegant-v1.png) |
| 大肥鱼情绪扩展 | [12 个额外姿态](../../../assets/images/ai-companions/app-whale-emotions-v2.png) |

大肥鱼扩展顺序：害羞、得意、烦躁、惊讶；疲惫、道歉、发现线索、认真读书；多任务忙乱、松口气、开心吃饭、打招呼。饭碗只是表情道具，不是提出喂食玩法。

![app 大肥鱼工作姿态](../../../assets/images/ai-companions/app-whale-v2.png)

![app Claude 工作姿态](../../../assets/images/ai-companions/app-claude-v2.png)

![app Codex 工作姿态](../../../assets/images/ai-companions/app-codex-elegant-v1.png)

![app 大肥鱼情绪扩展](../../../assets/images/ai-companions/app-whale-emotions-v2.png)

## DS 像素大头接入（2026-10-13）

用户选择之前的[鲸鱼像素大头](../../../assets/images/ai-companions/avatar-whale-v1.png)
用于 DS App 和固件，原图保持不变。逐个检查的头像边界提供八种 App 姿态和六种
固件情绪。固件采用 60×60 索引 C 图集（21,856 字节 Flash），没有 PNG 解码缓冲；
App 按源图边界绘制并禁用位图滤波。见[素材转换记录](../../../assets/README.zh_CN.md#ai-伙伴形象设计素材包)
和[伴侣说明](../../../companion/README.zh_CN.md#设备固件)。实际设备显示和手机验收仍待进行。

睡眠表情使用[单独修正的大头](../../../assets/images/ai-companions/avatar-whale-sleep-v5.png)，
保留[用户选定的脸颊和下巴轮廓](../../../assets/images/ai-companions/reference-whale-sleep-approved-chin.png)，
眉毛改为放松的小横线。睡觉、待命、询问、开心有轻微的 3.6 秒呼吸循环，固件首页
最多移动 4 显示像素。初版刷写和 35 秒启动观察已通过，但用户未接受该版本的睡眠
脸型；修正后的表情和动画需要重新上板验收。

## 原始设计阶段的来源与接入状态

- 图片为本项目生成，视觉方向来自[参考收集](ai-character-references.zh_CN.md)，没有将下载的第三方图片复制进素材包。情绪扩展以生成的 app 大肥鱼图集作为角色一致性参考。
- 大肥鱼和 Claude 参考社区拟人形象约定，Codex 改为用户提供的优雅长裙参考：银紫长发、深蓝蝴蝶结、米白深蓝层叠长裙和星空披肩，去掉龙角和尾巴。用户提供参考图用于视觉方向，原作者与再利用许可未知。生成图片不等于取得底层角色设计或商标的额外权利；不能称为官方美术，也不能以仓库代码许可代替美术授权。
- 完整提示词保存在 [prompts.json](../../../assets/images/ai-companions/prompts.json)，实际尺寸、透明通道检查和 SHA-256 保存在 [manifest.json](../../../assets/images/ai-companions/manifest.json)。
- 这些是静态源图集，不是连续动画帧或已编译的固件资源包。本轮没有修改固件/app 页面、runtime 情绪协议或 Git 历史。
- 接入前需要逐格提取和检查、清理边缘、统一锚点，将固件图转换到约定的整数像素网格和调色板，再转为选定的 LVGL 格式并测量 Flash/RAM。不要把整张高清源图集解码进固件 RAM。需上板检查小尺寸表情和对比度；app 也需要切图和边缘检查。

Build: NOT RUN（仅素材设计）。Host tests: FAIL（静态门禁在 macOS 链接器拒绝 `--gc-sections` 时停止，之前的测试通过）。Device tests: NOT RUN。Unverified: 切图、精确像素网格与调色板、透明边缘清理、运行时接入、动画和真机显示。

## ChatGPT app 大头模组（2026-10-16）

按用户提供的白龙形象重新设计，画风对齐 `app-whale-v2.png`，保留白发、浅紫瞳、白龙角、尖耳和结形发饰。仅设计 app 的高清 Q 版大头头像，4×4 共 16 个表情，不是全身角色或像素固件资源。表情顺序与大头头像章节一致。

![ChatGPT 大头头像](../../../assets/images/ai-companions/app-chatgpt-heads-v1.png)

[用户提供的角色参考](../../../assets/images/ai-companions/reference-chatgpt-dragon.png)原作者与再利用许可未知。素材有透明通道，接入前仍需逐格裁切、调整边距和检查透明边缘；尚未修改或发布 app。

## Codex app 像素头像替换（0.14.1）

用户确认以 ChatGPT 白龙形象替换 app 中的 Codex 头像，DS 与 Claude 保持原样。新增[像素源图](../../../assets/images/ai-companions/avatar-chatgpt-pixel-v1.png)，其中待命、开心、思考、专注四格经 `companion/android/tools/PreparePortraits.java` 生成透明 256×256 资源；支持尾随 `codex` 参数只生成这一角色。Codex 绘制使用最近邻缩放保留像素边缘，沿用原有角色身份、状态映射和呼吸动画。只改头像，不改连接、消息、审批或固件协议。

![Codex 像素头像源图](../../../assets/images/ai-companions/avatar-chatgpt-pixel-v1.png)
