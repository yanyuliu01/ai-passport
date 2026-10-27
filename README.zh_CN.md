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

## 整体由四部分组成

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
| 手机 App | `companion/android/` | 聊天、任务、帮手、状态、设置五入口；语音、主人审阅、图片文件，同时中转设备与 Runtime；新版经 Runtime 更新 | [DS 中枢](companion/README.zh_CN.md) |
| 桌面客户端 | `companion/desktop/` | Mac 和 Windows。五入口、账号登录、本地 Runtime 连接、直连/加密中继、主人审阅和帮手模型选择 | [小幽桌面端](companion/desktop/README.zh_CN.md) |
| Runtime | `runtime/` | 小幽本身：一件事一张卡，几件事并行，补充 / 改要求 / 取消；回复流式生成；派活时看档位和订阅余量；长任务看有没有动静；任务的图片和文件可下载；同步电脑上的 Codex、Claude Code 对话，可选审批桥；给电脑端 agent 共享历史；可写进 Notion 共享工作区 | [小幽 Runtime](runtime/README.zh_CN.md) |

只用 Claude 桌面端也行：同一份固件可以在开发者模式下直连 Claude 桌面端的 Hardware
Buddy（这时没有语音和卡）。

## 现在到哪了（2026-10-28）

| 部分 | 最新汇合源码 | 验证边界 |
| --- | --- | --- |
| 固件 | 现有固件，本次同步不改动 | 本次不刷机，不新增真机验收结论 |
| 手机 App | **0.33.2 (71)** | 命名为 **0.33.4 (73)** 的 APK 已发布，但对应源码仍在 Mac 未提交，标签指向更旧源码；详见进度索引；本次未核对手机安装 |
| 桌面客户端 | **0.1.16** | 账号、公网中继、帮手模型选择已提交；跨电脑重连与历史收敛仍待验收 |
| Runtime | **0.24.0** | Windows、Mac 健康接口均报告 0.24.0；保留的真实模型证据中 R6 首回复 <=3 秒仍失败，R8 迁移未完成 |

[项目进展与源码同步索引](docs/development/project-status.zh_CN.md)记录本次核对的
Windows/Mac 提交、未提交工作、分支用途和历史问题 H1-H4。源码汇合不代表发布或部署。

开发主线是 `feature/usage-history`，也是 GitHub 默认分支：

| 分支 | 用途 |
| --- | --- |
| `feature/usage-history` | 四部分最新已提交源码 |
| `claude/client-five-entries`、`runtime/r4-recovery-review` | 客户端/重构及恢复审查工作；检查时的远端提交已包含在集成线中 |
| `feature/multi-end-sync` | 较早的 mesh/客户端里程碑，已包含在集成线中 |
| `feature/usage-effort`、`feature/xiaoyou-agent-router`、`feature/ble-firmware-update`、`feature/pocket-hub-app`、`feature/claude-pocket` | 历史开发阶段 |
| `main` | 上游硬件测试基线，不是当前产品或默认分支 |

## 升级与源码同步

升级前检查[兼容性与可靠性规则](docs/development/cross-stack-reliability.zh_CN.md)
和各部分的交付说明。支持的客户端通过能力协商兼容，不要求更新源码时同时替换所有
已安装组件。保留配对、历史、权限和待处理请求。

1. 汇合 `feature/usage-history` 前先检查工作区，协调现有改动或使用独立 worktree。
   不要 pull 覆盖正在进行的工作；Runtime 有任务运行时，未经主人批准不能重启。
   启动方法见 [Runtime 指南](runtime/README.zh_CN.md)。
2. 手机 App：0.11.0 起经 Runtime 更新。在 `runtime/` 下
   `python3 -m xiaoyou_runtime app publish <apk 路径> --note "改了什么"`，手机打开 App 或在
   「连接与设置 → App 更新」里点安装。手机上还是 0.11.0 之前的版本时，要先手动装一次
   最新的 `companion-build-N`（Releases 里的 `pocket-hub-debug.apk`）。
3. 新固件经蓝牙推到设备：`python3 -m xiaoyou_runtime firmware fetch --push`。
   设备上还是单槽位的旧固件时，要先插线刷一次：`bash tools/flash_pocket.sh`。
4. 桌面客户端按[各平台说明](companion/desktop/README.zh_CN.md)构建与安装。
   同步源码不会替换已安装客户端或其内置 Runtime。

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
| 找其他文档 | [文档](#文档) |

## 文档

工程与协作文档定义规则，档案与参考提供背景。按当前任务选择入口。

| 入口 | 你可以找到 |
| --- | --- |
| [Claude Pocket](docs/claude-pocket.zh_CN.md) | 本分支的应用：配对、按键、文字覆盖范围、验收 |
| [小幽 Harness 审查](docs/assets/xiaoyou-harness-review.zh_CN.md) | Fork 架构审查、本地可靠性问题、接入能力边界、实验及渐进 PR 计划 |
| [项目进展与源码同步](docs/development/project-status.zh_CN.md) | 当前 Windows/Mac 基线、版本、未提交工作、分支及验收缺口 |
| [开发指南](docs/development/README.zh_CN.md) | AI 工作流、工程规范、CI 与发布流程 |
| [App／Runtime／固件可靠性](docs/development/cross-stack-reliability.zh_CN.md) | 强制回归、3 秒首回复、连接恢复与多端一致性契约 |
| [小幽桌面客户端方案](docs/development/xiaoyou-desktop-plan.zh_CN.md) | 周五前 Mac/Windows 私测交付目标、统一会话、可自建加密通信、依赖与验收 |
| [小幽桌面客户端实现](companion/desktop/README.zh_CN.md) | 当前客户端增量、原生构建、加密中继、测试命令与待完成验收 |
| [Agent gateway 方案](docs/development/agent-gateway.zh_CN.md) | App 控制 CLI 安装、能力适配、审计、生命周期与回退设计 |
| [自动迭代流水线](docs/development/auto-iteration.zh_CN.md) | 插件化闭环：发现 App 使用问题，本地模型改代码，过门禁后发布，出问题自动撤回 |
| [产品原则](docs/product-principles.zh_CN.md) | 草稿 v0.1：小幽是什么、不做什么，以及版本方案必须对照的编号原则 |
| [AI 技能](skills/README.zh_CN.md) | 开发、环境准备、构建、真机测试与故障诊断 |
| [硬件资料](docs/hardware-design/README.zh_CN.md) | 板卡事实、接口边界、验收清单与排障 |
| [中文字体](docs/development/engineering/lvgl-chinese-fonts.zh_CN.md) | 字形覆盖、控件字体选择，以及中文空白排查 |
| [社区作品与经验](docs/reference/README.zh_CN.md) | `docs/reference/<username>/` 下的应用档案和可复用知识 |
| [参与贡献](docs/contribution/README.zh_CN.md) | 文档、提交与 Pull Request 约定 |
| [设备图片](docs/brand/README.zh_CN.md) | 画设备时用的实物参考图 |
| [更新记录](docs/CHANGELOG.zh_CN.md) | 版本历史 |

AI 助手请从 [`AGENTS.md`](AGENTS.md) 开始，再按任务路由读取相关文档。社区：[参与贡献](.github/CONTRIBUTING.zh_CN.md) · [获取帮助](.github/SUPPORT.zh_CN.md) · [行为准则](.github/CODE_OF_CONDUCT.zh_CN.md) · [安全说明](.github/SECURITY.zh_CN.md)。

## 许可

代码：[MIT](LICENSE)。第三方版权信息见 [NOTICE](NOTICE)。伴侣角色图稿暂未单独
指定美术许可，见 [assets/README.zh_CN.md](assets/README.zh_CN.md)。
