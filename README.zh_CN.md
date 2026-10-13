<p align="right">
  <strong>简体中文</strong> · <a href="README.md">English</a>
</p>

<h1 align="center">Claude Pocket</h1>

<p align="center">
  <strong>小幽是一只住在你手腕上的像素小幽灵。<br>
  你只跟她说话，活儿由她交给帮手去做。</strong>
</p>

<p align="center">
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-64748b?style=flat-square" alt="MIT License"></a>
  <img src="https://img.shields.io/badge/MCU-ESP32--C3-14b8a6?style=flat-square" alt="ESP32-C3">
  <img src="https://img.shields.io/badge/ESP--IDF-5.5.3-2563eb?style=flat-square" alt="ESP-IDF 5.5.3">
  <img src="https://img.shields.io/badge/link-BLE%20Secure-a78bfa?style=flat-square" alt="低功耗蓝牙安全连接">
</p>

<p align="center">
  <img src="assets/images/readme/readme-hero.jpg" alt="白色像素小幽灵小幽带着一颗爱心，一串虚线连向三位 Q 版帮手：蓝发鲸鱼女仆、橙发学者、银发伙伴，都在挥手。" width="100%">
</p>

**Claude Pocket** 是跑在 [FoloToy](https://ai-passport.folotoy.cn/) 一款开放式
ESP32-C3 小型可穿戴设备上的固件。按住按键说话，小幽在屏幕上回答你。遇到聊天
解决不了的事，她会把活儿交给编程 agent：屏幕上能看到是谁在做、做到第几步；
需要你点头的操作，她会先问你。

设备通过加密的低功耗蓝牙连着手机伴侣 App。没装 App 时，也可以在开发者模式下
直连 Claude 桌面端。

## 认识小幽

<p align="center">
  <img src="assets/images/readme/readme-xiaoyou-moods.png" alt="像素小幽灵小幽的六种表情：睡着、空闲、忙碌冒汗、感叹号找你、开心带爱心、失败叉叉眼。" width="560">
</p>

她在 [`main/pocket_pet.c`](main/pocket_pet.c) 里一格一格画出来。没连上时她在
睡觉，空闲时轻轻上下浮动；忙的时候头顶跳着三个点，还挂着一滴汗。有事找你时
会冒出感叹号，事情顺利会冒爱心，失败了就变成叉叉眼。

## 她的帮手

重活不由小幽自己干。她会交给主机上配置好的 agent。每位帮手在设备上都有自己
固定的颜色，在伴侣设计包里也有一个角色形象：

| | 帮手 | 背后跑的是 |
| --- | --- | --- |
| 🐋 | **DSH**：鲸鱼女仆 | DeepSeek Harness 命令行（`dsh`） |
| 🧡 | **Claude**：橙发学者 | Claude Code |
| 🌙 | **Codex**：银发伙伴 | Codex CLI |

<p align="center">
  <img src="assets/images/readme/readme-crew.jpg" alt="三行，每行一位帮手，各四个姿势：思考、在笔记本上敲代码、挥手等你批准、任务完成欢呼。" width="100%">
</p>

<p align="center"><sub>思考中 · 干活中 · 等你点头 · 完成啦</sub></p>

还有适合小屏幕的像素头像版：

<p align="center">
  <img src="assets/images/readme/readme-pixel-heads.jpg" alt="三位帮手的像素头像，每位八种表情：平静、开心、思考、专注、疑惑、惊讶、出错、睡觉。" width="100%">
</p>

> 帮手角色是非官方的同人风格概念图，固件目前还没有用上。全部图稿和各自的状态
> 见[设计包](docs/reference/yanyuliu01/ai-companion-design.zh_CN.md)。

## 手腕上有什么

| 屏幕 | 你会看到 |
| --- | --- |
| **小幽** | 小幽和她正在做的事：听写、思考、转交（一封信飞向帮手的名牌）、完成或失败 |
| **对话** | 你说了什么、小幽回了什么，一屏一张卡片 |
| **任务** | 交给帮手的所有事：进行中的、等你批准的、已结束的，按天分组 |
| **用量** | 每个订阅账号 5 小时额度和每周额度还剩多少 |

批准、通知、六位数配对码、蓝牙升级固件，详见 [Claude Pocket](docs/claude-pocket.zh_CN.md)。

## 往下看哪里

| 我想… | 从这里开始 |
| --- | --- |
| 了解它能做什么、怎么配对 | [Claude Pocket](docs/claude-pocket.zh_CN.md) |
| 安装手机伴侣 App | [小幽中枢(Android)](companion/README.zh_CN.md) |
| 运行驱动帮手的主机端 Runtime | [Runtime](runtime/README.zh_CN.md) |
| 用 AI 编程工具改固件 | [AGENTS.zh_CN.md](AGENTS.zh_CN.md) |
| 看硬件、引脚、内存预算 | [硬件指南](docs/hardware-design/AI_HARDWARE_DEVELOPMENT_GUIDE.zh_CN.md) |
| 看完整的文档地图 | [docs/README.zh_CN.md](docs/README.zh_CN.md) |

## 许可

代码：[MIT](LICENSE)。第三方版权信息见 [NOTICE](NOTICE)。伴侣角色图稿暂未单独
指定美术许可，见 [assets/README.zh_CN.md](assets/README.zh_CN.md)。
