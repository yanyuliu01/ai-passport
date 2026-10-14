<p align="right">
  <strong>简体中文</strong> · <a href="README.md">English</a>
</p>

<h1 align="center">Claude Pocket</h1>

<p align="center">
  <strong>小幽是一只住在你手腕上的鲸鱼小女仆。<br>
  你只跟她说话，大活儿由她交给 Claude 和 Codex。</strong>
</p>

<p align="center">
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-64748b?style=flat-square" alt="MIT License"></a>
  <img src="https://img.shields.io/badge/MCU-ESP32--C3-14b8a6?style=flat-square" alt="ESP32-C3">
  <img src="https://img.shields.io/badge/ESP--IDF-5.5.3-2563eb?style=flat-square" alt="ESP-IDF 5.5.3">
  <img src="https://img.shields.io/badge/Xiaoyou-DeepSeek%20%C2%B7%20dsh-7cc8f2?style=flat-square" alt="小幽经 dsh 跑在 DeepSeek 上">
</p>

<p align="center">
  <img src="assets/images/readme/readme-hero.jpg" alt="蓝发鲸鱼女仆小幽在挥手，旁边是她带爱心的像素形象；一串虚线从她连向两位挥手的帮手：橙发学者 Claude 和银发伙伴 Codex。" width="100%">
</p>

**Claude Pocket** 是跑在 [FoloToy](https://ai-passport.folotoy.cn/) 一款开放式
ESP32-C3 小型可穿戴设备上的固件。按住按键说话，小幽在屏幕上回答你。遇到聊天
解决不了的事，她会把活儿交给编程 agent：屏幕上能看到是谁在做、做到第几步；
需要你点头的操作，她会先问你。

设备通过加密的低功耗蓝牙连着手机伴侣 App。没装 App 时，也可以在开发者模式下
直连 Claude 桌面端。

## 认识小幽

小幽就是 DeepSeek 的那条鲸鱼——大肥鱼女仆。她是你唯一要对话的对象，Runtime
通过 DeepSeek Harness 命令行（`dsh`）让她跑在 DeepSeek 上。

<p align="center">
  <img src="assets/images/readme/readme-xiaoyou-idle.gif" alt="小幽的像素形象空闲时上下浮动。" width="160">
  &nbsp;&nbsp;
  <img src="assets/images/readme/readme-xiaoyou-moods.png" alt="小幽像素形象的六种表情：睡着、空闲、忙碌头顶跳点、感叹号找你、开心带爱心、失败叉叉眼。" width="480">
</p>

在设备上她是一个 20 × 20 的像素小人。没连上时她在睡觉，空闲时轻轻上下浮动；
忙的时候头顶跳着三个点。有事找你时会冒出感叹号，事情顺利会冒爱心，失败了就
变成叉叉眼。

> 像素形象正从原来的小幽灵换成大肥鱼。这几帧是从还在开发中的
> `main/pocket_pet.c` 渲染出来的。

## 她的帮手

小幽自己能答的直接答，要花时间的交给 agent 在后台做。每位帮手在设备上都有
自己固定的颜色：

| | 是谁 | 背后跑的是 |
| --- | --- | --- |
| 🐋 | **小幽**：大肥鱼女仆，你唯一对话的对象 | DeepSeek（经 `dsh`） |
| 🧡 | **Claude**：橙发学者 | Claude Code（`claude`、`tailor`） |
| 🌙 | **Codex**：银发伙伴 | Codex app-server |

<p align="center">
  <img src="assets/images/readme/readme-crew.jpg" alt="三行：大肥鱼小幽、Claude、Codex，各四个姿势：思考、在笔记本上敲代码、挥手等你批准、任务完成欢呼。" width="100%">
</p>

<p align="center"><sub>思考中 · 干活中 · 等你点头 · 完成啦</sub></p>

还有适合小屏幕的像素头像版：

<p align="center">
  <img src="assets/images/readme/readme-pixel-heads.jpg" alt="小幽、Claude、Codex 的像素头像，每位八种表情：平静、开心、思考、专注、疑惑、惊讶、出错、睡觉。" width="100%">
</p>

> Q 版角色是非官方的同人风格概念图。全部图稿和各自的状态见
> [设计包](docs/reference/yanyuliu01/ai-companion-design.zh_CN.md)。

## 整体由三部分组成

```text
 设备（固件）  ──加密低功耗蓝牙──>  手机 App「小幽中枢」  ──HTTP──>  小幽 Runtime（电脑上）
 按住说话、四屏、授权弹窗           转发语音、卡、用量、换固件          人设 · 对话 · 卡 · 路由
                                                                       ├─> claude / tailor（Claude Code）
                                                                       ├─> codex（Codex app-server）
                                                                       ├─> deepseek（Claude Code 外壳 + DeepSeek 接口）
                                                                       ├─> dsh（DeepSeek Harness）
                                                                       └─> 任意命令 / 另一台 Runtime
```

| 部分 | 目录 | 做什么 | 说明文档 |
| --- | --- | --- | --- |
| 设备固件 | `main/`、`components/` | 四屏（形象 / 对话 / 任务 / 用量）、按住说话、授权弹窗、经蓝牙换固件（双槽位，没被认可自动退回） | [Claude Pocket](docs/claude-pocket.zh_CN.md) |
| 手机 App | `companion/` | 安卓。设备和 Runtime 之间的中转：语音、卡、任务、授权、用量、固件；自己也能聊天、看任务、选档位 | [小幽中枢](companion/README.zh_CN.md) |
| Runtime | `runtime/` | 小幽本身：一件事一张卡，几件事并行，补充 / 改要求 / 取消；派活时看档位和订阅余量；长任务看有没有动静；可写进 Notion 共享工作区 | [小幽 Runtime](runtime/README.zh_CN.md) |

只用 Claude 桌面端也行：同一份固件可以在开发者模式下直连 Claude 桌面端的 Hardware
Buddy（这时没有语音和卡）。

## 现在到哪了（2026-10-12）

| 部分 | 版本 | 构建 | 真机 |
| --- | --- | --- | --- |
| 固件 | 预发布 `firmware-build-19` | 通过 | 连着手机按住说话已在设备上跑通（`28119d8`）；三屏在设备上用过；第四屏、任务历史的显示还没人确认 |
| 手机 App | 0.9.1，预发布 `companion-build-15` | 通过 | 0.2.0 在手机上跑通过配对和打字；之后各版都没有逐项在手机上验过 |
| Runtime | 0.6.1 | 测试全过 | 在作者的 Mac 上日常运行；Claude Code 的订阅用量、Codex app-server 的授权还没在 Mac 上实测 |

每一部分“哪些验证过、哪些没有”写在各自的文档末尾。开发主线是
`feature/usage-history`；其余分支的用途见下表。

| 分支 | 是什么 |
| --- | --- |
| `feature/usage-history` | 当前主线：上面三部分的最新代码 |
| `feature/usage-effort`、`feature/xiaoyou-agent-router`、`feature/ble-firmware-update` | 已合进主线的前几段工作 |
| `feature/claude-pocket` | 最早的 Claude Pocket 固件（桌面端伴侣） |
| `main` | FoloToy 上游的硬件测试基线，不是成品应用 |

## 升级：三样一起

固件、App、Runtime 之间的协议是一起改的，升级时三样一起：

1. 手机装最新的 `companion-build-N`（Releases 里的 `pocket-hub-debug.apk`）。
2. 电脑上停掉 Runtime，拉主线，重新启动：
   `cd runtime`，`python3 -m xiaoyou_runtime --config config.json`
3. 新固件经蓝牙推到设备：`python3 -m xiaoyou_runtime firmware fetch --push`。
   设备上还是单槽位的旧固件时，要先插线刷一次：`bash tools/flash_pocket.sh`。

换回某一版：`python3 -m xiaoyou_runtime firmware restore previous`（或 `firmware list` 里
的编号）。

## 往下看哪里

| 我想… | 从这里开始 |
| --- | --- |
| 了解设备能做什么、按键、协议、内存 | [Claude Pocket](docs/claude-pocket.zh_CN.md) |
| 装手机 App、和设备配对 | [小幽中枢](companion/README.zh_CN.md) |
| 在电脑上跑小幽、登记 agent、配置档位和用量 | [小幽 Runtime](runtime/README.zh_CN.md) |
| 用 AI 编程工具改固件 | [AGENTS.zh_CN.md](AGENTS.zh_CN.md) |
| 看硬件、引脚、内存预算 | [硬件指南](docs/hardware-design/AI_HARDWARE_DEVELOPMENT_GUIDE.zh_CN.md) |
| 看上游基线平台和完整的文档地图 | [docs/README.zh_CN.md](docs/README.zh_CN.md) |

## 许可

代码：[MIT](LICENSE)。第三方版权信息见 [NOTICE](NOTICE)。伴侣角色图稿暂未单独
指定美术许可，见 [assets/README.zh_CN.md](assets/README.zh_CN.md)。
