<p align="right">
  <strong>简体中文</strong> · <a href="README.md">English</a>
</p>

<h1 align="center">Claude Pocket</h1>

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="assets/images/logo-wordmark-dark.png">
    <img src="assets/images/logo-wordmark.png" alt="FoloToy 字标" width="128">
  </picture>
</p>

<p align="center">
  <strong>戴在身上的小幽——你唯一要对话的 AI 伙伴。</strong>
</p>

<p align="center">
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-64748b?style=flat-square" alt="MIT License"></a>
  <img src="https://img.shields.io/badge/MCU-ESP32--C3-14b8a6?style=flat-square" alt="ESP32-C3">
  <img src="https://img.shields.io/badge/ESP--IDF-5.5.3-2563eb?style=flat-square" alt="ESP-IDF 5.5.3">
</p>

<p align="center">
  <img src="assets/images/home.jpg" alt="FoloToy AI Passport 可穿戴设备，正面、侧面、背面展示" width="100%">
</p>

这个仓库把 [FoloToy AI Passport](https://ai-passport.folotoy.cn/)（一块开放、可穿戴的
ESP32-C3 小设备）变成**小幽**随身的那张脸。小幽是一只像素小幽灵，也是你唯一要对话的
对象：她自己能答的直接答，要花时间的交给 Claude Code、Codex 等 agent 在后台做，做完由
她转述；agent 要做需要你确认的操作时，设备上弹出来问你。

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

[MIT](LICENSE)。第三方版权信息见 [NOTICE](NOTICE)。
