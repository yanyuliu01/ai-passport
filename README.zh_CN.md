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

设备通过加密的低功耗蓝牙连着手机伴侣 App。不带设备时，App 自己就能和她说话：
首页上的小幽跟着真实状态动，按住麦克风就能说。电脑上已经开着的 Codex、Claude Code
对话也能同步到手机和设备上。经 Tailscale Funnel，手机在任何网络下都连得回家里的电脑。
没装 App 时，设备也可以在开发者模式下直连 Claude 桌面端。

## 认识小幽

小幽就是 DeepSeek 的那条鲸鱼——大肥鱼女仆。她是你唯一要对话的对象，Runtime
通过 DeepSeek Harness 命令行（`dsh`）让她跑在 DeepSeek 上，她的回复边生成边显示。
App 和固件的文档里也叫她 DS。

<p align="center">
  <img src="assets/images/readme/readme-xiaoyou-idle.gif" alt="小幽的像素大头空闲时轻轻呼吸。" width="160">
  &nbsp;&nbsp;
  <img src="assets/images/readme/readme-xiaoyou-moods.png" alt="小幽像素大头的六种表情：睡着冒 Zz、空闲、忙碌头顶跳点、有事找你、开心带爱心、出错。" width="480">
</p>

在设备上她是一个 60 × 60 的像素大头，从设计包里的鲸鱼女仆原稿编译而来，有六种
表情：没连上时睡觉，空闲时轻轻呼吸，忙的时候头顶跳着三个点，有事找你、事情顺利、
出错各有自己的脸。除了表情，她的动作也跟着真实状态走，和手机首页同一套：你按住说话
时她左右轻晃，想的时候上下浮，回答到了蹦一下；活儿交给帮手时，一封信从她飞向那位
帮手的名牌。

> 上面两张图由 `main/pocket_pet.c` 直接渲染，和设备上画的是同一份像素。

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
 设备（固件）  ──加密低功耗蓝牙──>  手机 App「DS 中枢」  ──HTTP(S)──>  小幽 Runtime（电脑上）
 按住说话、四屏、授权弹窗           聊天、任务、帮手、审批            人设 · 对话 · 卡 · 路由
                                    转发语音、卡、用量、换固件          ├─> dsh（DeepSeek Harness，替小幽说话）
                                    局域网 / Tailscale Funnel          ├─> claude / tailor（Claude Code）
                                                                       ├─> codex（Codex app-server）
                                                                       ├─> deepseek（Claude Code 外壳 + DeepSeek 接口）
                                                                       ├─> 任意命令 / 另一台 Runtime
                                                                       └── 观察本机的 Codex、Claude Code 对话（--desktop-sync）
```

| 部分 | 目录 | 做什么 | 说明文档 |
| --- | --- | --- | --- |
| 设备固件 | `main/`、`components/` | 四屏（形象 / 对话 / 任务 / 用量）、按住说话、授权弹窗、经蓝牙换固件（双槽位，没被认可自动退回） | [Claude Pocket](docs/claude-pocket.zh_CN.md) |
| 手机 App | `companion/` | 安卓。侧边栏四处：和小幽聊天、任务、帮手、连接与设置。首页的小幽跟着状态动，按住麦克风说话（手机自己识别）；审批提醒（最多同时盯八台电脑）、任务图片和文件、用量；同时是设备和 Runtime 之间的中转；新版经 Runtime 推到手机 | [DS 中枢](companion/README.zh_CN.md) |
| Runtime | `runtime/` | 小幽本身：一件事一张卡，几件事并行，补充 / 改要求 / 取消；回复流式生成；派活时看档位和订阅余量；长任务看有没有动静；任务的图片和文件可下载；同步电脑上的 Codex、Claude Code 对话，可选审批桥；给电脑端 agent 共享历史；可写进 Notion 共享工作区 | [小幽 Runtime](runtime/README.zh_CN.md) |

只用 Claude 桌面端也行：同一份固件可以在开发者模式下直连 Claude 桌面端的 Hardware
Buddy（这时没有语音和卡）。

## 现在到哪了（2026-10-16）

| 部分 | 版本 | 构建 | 真机 |
| --- | --- | --- | --- |
| 固件 | 预发布 `firmware-build-22`（`8220106`，鲸鱼像素大头 + 跟着状态的动作） | 通过 | 连着手机按住说话已在设备上跑通（`firmware-build-19`）；三屏用过；鲸鱼的睡脸和呼吸在设备上确认过；跟着状态的动作、第四屏、任务历史还没人在设备上确认 |
| 手机 App | 0.11.0（版本号 17），预发布 `companion-build-26` | 通过 | 0.2.0 在手机上跑通过配对和打字；侧边栏版、手机按住说话、审批提醒、经 Runtime 更新都还没在手机上逐项验过 |
| Runtime | 0.8.0 | 463 个测试全过 | 在作者的 Mac 上日常运行；Claude Code 的订阅用量、Codex app-server 的授权、电脑端审批桥的真实往返还没在 Mac 上实测 |

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

1. 电脑上停掉 Runtime，拉主线，重新启动（先升 Runtime，免得新 App 碰上旧接口）：
   `cd runtime`，`python3 -m xiaoyou_runtime --config config.json`
2. 手机 App：0.11.0 起经 Runtime 更新。在 `runtime/` 下
   `python3 -m xiaoyou_runtime app publish <apk 路径> --note "改了什么"`，手机打开 App 或在
   「连接与设置 → App 更新」里点安装。手机上还是 0.11.0 之前的版本时，要先手动装一次
   最新的 `companion-build-N`（Releases 里的 `pocket-hub-debug.apk`）。
3. 新固件经蓝牙推到设备：`python3 -m xiaoyou_runtime firmware fetch --push`。
   设备上还是单槽位的旧固件时，要先插线刷一次：`bash tools/flash_pocket.sh`。

换回某一版：`python3 -m xiaoyou_runtime firmware restore previous`（或 `firmware list` 里
的编号）。

## 往下看哪里

| 我想… | 从这里开始 |
| --- | --- |
| 了解设备能做什么、按键、协议、内存 | [Claude Pocket](docs/claude-pocket.zh_CN.md) |
| 装手机 App、和设备配对 | [DS 中枢](companion/README.zh_CN.md) |
| 在电脑上跑小幽、登记 agent、配置档位和用量 | [小幽 Runtime](runtime/README.zh_CN.md) |
| 把电脑上的 Codex、Claude Code 对话同步到手机和设备 | [同步已有电脑端对话](runtime/README.zh_CN.md#同步已有电脑端对话) |
| 出门在外也连上家里的 Runtime | [Tailscale Funnel](runtime/README.zh_CN.md#在任何网络下连上-runtimetailscale-funnel) |
| 把 App 的新版推到手机上 | [App 更新](companion/README.zh_CN.md#app-更新) |
| 用 AI 编程工具改固件 | [AGENTS.zh_CN.md](AGENTS.zh_CN.md) |
| 看硬件、引脚、内存预算 | [硬件指南](docs/hardware-design/AI_HARDWARE_DEVELOPMENT_GUIDE.zh_CN.md) |
| 看上游基线平台和完整的文档地图 | [docs/README.zh_CN.md](docs/README.zh_CN.md) |

## 许可

代码：[MIT](LICENSE)。第三方版权信息见 [NOTICE](NOTICE)。伴侣角色图稿暂未单独
指定美术许可，见 [assets/README.zh_CN.md](assets/README.zh_CN.md)。
