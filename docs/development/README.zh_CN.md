<p align="right">
  <strong>简体中文</strong> · <a href="README.md">English</a>
</p>

# 工程规范（Development）

本目录存放 AI Passport 的工程开发规范和可复用工作流，按用途分组：AI 开发工作流（`ai-guide.md`）、工程约定（`engineering/`）、CI 文档（`ci/`）、发布/完成流程（`release/`）。

## 收录标准

- 收录构建验证、代码风格、注释、测试、资源约束和 AI 开发流程。
- `ai-guide.md` 面向 AI 编程助手，允许包含本项目结构和硬件边界。
- 每条规则应写清触发条件、必须做什么、禁止做什么、验证方法和例外条件。
- 涉及本板具体硬件事实的结论引用 `docs/hardware-design/`，不重复。
- 可以由 lint、测试或脚本强制的要求，应同时落实到自动化检查，不能只靠 agent 阅读文字。
- 新增规约时在本文件更新索引。

## AI 工作流

- [project-status.zh_CN.md](project-status.zh_CN.md)：当前 Windows/Mac 基线、版本、未提交工作、分支用途及验收缺口。
- [xiaoyu-system-prompt.zh_CN.md](xiaoyu-system-prompt.zh_CN.md)：基础系统提示词、Grok Bot 整体参考映射、能力适配与验收边界。
- [cross-stack-reliability.zh_CN.md](cross-stack-reliability.zh_CN.md)：App／Runtime／固件强制准则，包含回归、3 秒首回复、连接恢复和多端一致性。
- [first-response-measurement.zh_CN.md](first-response-measurement.zh_CN.md)：默认流程切换前，在真实手机上测量并记录 3 秒首响应的方法。
- [unified-message-model.zh_CN.md](unified-message-model.zh_CN.md)：统一消息结构——唯一的消息日志、编号规则、同步接口，以及新的 Runtime 或 agent 怎么接入。
- [conversation-presentation.zh_CN.md](conversation-presentation.zh_CN.md)：项目／会话统一展示方案，包含来源与目录绑定、标题修复、事件署名与原生客户端边界。
- [app-runtime-simplification.zh_CN.md](app-runtime-simplification.zh_CN.md)：App 与 Runtime 交互简化的问题清单，覆盖代码、体验、交互三层，含根因、目标指标与待决定事项。
- [xiaoyu-agent-model.zh_CN.md](xiaoyu-agent-model.zh_CN.md)：小鱼完整方案：简洁原则、五个页面与统一词汇、配一次就不用管的连接、主体模型（事件、世界状态、注意力与承诺、统一决策循环）、8 个接口、模块划分与分阶段实施。
- [code-cleanup-plan.zh_CN.md](code-cleanup-plan.zh_CN.md)：第一阶段代码清理：先对齐本地与远端，再分批删除暗中追问、App 里没人用的代码和旧版 Runtime 兼容，每批单独验证。
- [runtime-refactor-design.zh_CN.md](runtime-refactor-design.zh_CN.md)：Runtime 三层重构设计（状态接收、回复消息、agent loop）：目标结构、接口、和现有代码的对应、迁移顺序 R1～R8。
- [helper-capabilities.zh_CN.md](helper-capabilities.zh_CN.md)：帮手能用哪些 MCP 和 skill，由小鱼按电脑统一管（参考 Raft 的托管 MCP）：能力库、连接、授权、注入的代理和分阶段。
- [runtime-refactor-review.zh_CN.md](runtime-refactor-review.zh_CN.md)：给 CC 的评审交接，修正 Raft/Xiaoyu-Bot-App 借鉴中的协调、重放与连接前提。
- [windows-public-node-handoff.zh_CN.md](windows-public-node-handoff.zh_CN.md)：家用公网节点的 Windows Codex 交接、源码快照、网络权限问题、原生构建与异网验收。
- [xiaoyu-desktop-plan.zh_CN.md](xiaoyu-desktop-plan.zh_CN.md)：Mac/Windows 客户端方案，包含统一会话、可自建加密通信、周四候选版里程碑与验收依赖。
- [decide-then-speak.zh_CN.md](decide-then-speak.zh_CN.md)：设计稿——小鱼说话前先定下做不做，替代 Runtime 的承诺提醒；也记录了 App 0.25.2 / Runtime 0.18.2 的聊天修复和交接方式。
- [dialogue-naturalness-design.zh_CN.md](dialogue-naturalness-design.zh_CN.md)：重构后再实施的陪伴／办事上下文提议，含相处偏好、真实任务回复和表达习惯的逻辑框架图。
- [decide-speak-experiment.zh_CN.md](decide-speak-experiment.zh_CN.md)：实验方案——先定再说的串行与推测并行对比，含 12 类 46 个用例、指标和事先定好的判定标准。
- [decide-speak-handoff.zh_CN.md](decide-speak-handoff.zh_CN.md)：交给 Codex 跑上述实验的步骤、禁止事项和报告格式。
- [auto-iteration.zh_CN.md](auto-iteration.zh_CN.md)：统一采集（所有客户端报给主电脑）与按版本自动迭代（本地模型优先）的设计稿。
- [ai-guide.zh_CN.md](ai-guide.zh_CN.md)：AI 开发工作流（面向 AI 编程助手：上下文建立、需求拆解、BSP 边界、验收交付格式）。
- [核心 AI 技能](../../skills/README.zh_CN.md)：开发、环境准备、构建、真机测试及诊断，包含安装和使用示例。

## 工程约定（engineering）

- [game-demo-to-device-acceptance.zh_CN.md](engineering/game-demo-to-device-acceptance.zh_CN.md)：共享 C/Wasm 游戏逻辑，先验收网页玩法，再验收真机表现。
- [environment-setup.zh_CN.md](engineering/environment-setup.zh_CN.md)：AI 在全新机器上的环境引导，包含国际与中国大陆下载线路。
- [build-and-test.zh_CN.md](engineering/build-and-test.zh_CN.md)：构建与验证（ESP-IDF 命令、逻辑测试、改动验证要求）。
- [firmware-layout.zh_CN.md](engineering/firmware-layout.zh_CN.md)：默认/用户自定义分区布局与合并产物验证。
- [coding-conventions.zh_CN.md](engineering/coding-conventions.zh_CN.md)：代码约定（语言风格、复用、注释、测试同步、资源约束等），包含中文字体接入、空白/方框排查与显示验收。
- [lvgl-chinese-fonts.zh_CN.md](engineering/lvgl-chinese-fonts.zh_CN.md)：CJK 配置、字体生成/链接、fallback 示例、缺字检查和故障排查的分步指南。

## CI（ci）

- [CI-validation.zh_CN.md](ci/CI-validation.zh_CN.md)：Pull Request 与 main 的自动仓库检查、host tests 和固件验证。
- [CI-build-and-release.zh_CN.md](ci/CI-build-and-release.zh_CN.md)：自动构建与发布说明（tag 触发自动编译固件并发布 Release）。

## 发布/完成流程（release）

- [project-completion.zh_CN.md](release/project-completion.zh_CN.md)：项目开发完成流程说明（一组可选收尾动作）。
- [persona-boundaries.zh_CN.md](persona-boundaries.zh_CN.md)：人设填写信息、固定做事规则与现有调度边界。
