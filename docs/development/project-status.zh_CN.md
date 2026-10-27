<p align="right">
  <strong>简体中文</strong> · <a href="project-status.md">English</a>
</p>

# 项目进展与源码同步

快照日期：**2026-10-28，北京时间**。这是进度索引，不是发布或完整验收声明。
下方链接的各子系统文档仍负责记录实现和验证证据。

## 当前基线

| 部分 | 本次同步包含的已提交源码 | 运行或待整理证据 |
| --- | --- | --- |
| Runtime | **0.24.0**；至 `aba494e` 的三层迁移、持久执行与恢复、会话归属、协商同步、有界上下文及唤醒生命周期；`080d0f4` 加入帮手模型偏好 | 本次检查 Windows、Mac 的 `/healthz` 均报告 **0.24.0**。版本号相同不能证明二进制相同或验收通过 |
| 桌面客户端 | **0.1.16**，`080d0f4`：账号、公网中继、帮手模型选择；此前包含五入口、主人审阅和 Runtime 晚启动恢复；`3f1b905` 修复账号连接就绪前已退出的 peer 同步循环无法重启 | 独立读取 Mac 包元数据为 **0.1.16**。保留的 Windows 检查记录显示 0.1.16 已连 0.24.0、账号保留和模型选择可用；新重连修复已有源码回归覆盖，安装后的跨电脑验收仍未完成 |
| Android | **0.33.2 (71)**，`81f658a`：避免重复回放和保存未变历史；此前包含同步缓存及主线程优化 | 命名为 **0.33.4 (73)** 的 APK 已发布，但对应源码仍留在 Mac 账号/中继工作区未提交，不属于此源码基线；本次未检查手机实际安装 |
| 固件 | 现有已提交固件，本次同步不改动 | 不据此声明新的构建身份、刷机或真机验收 |

实现与交付说明：[Runtime](../../runtime/README.zh_CN.md)、
[桌面端](../../companion/desktop/README.zh_CN.md)、[Android](../../companion/README.zh_CN.md)、
[固件](../claude-pocket.zh_CN.md)。

## 本次核对与汇合

GitHub 默认分支已经是 **`feature/usage-history`**。同步前停在 `66e6ef7`，
Windows 主工作区为 `12bbd86`，有 **59 个未推送提交**，包含最新 Runtime 重构、
客户端迁移、桌面端 0.1.16 和跨电脑历史问题记录。远端 `claude/client-five-entries`
停在 `aba494e`，其历史已经包含在 `12bbd86` 中。

检查期间 Windows 又推进到 `b4002be`：`3f1b905` 加入 peer 同步重连修复，
随后再次记录相同的历史问题。本次也合入了这条更新的已提交主线，
并对汇合源码编译及运行桌面核心回归测试，24/24 通过。

通过 SSH 直接检查 Mac：

| 工作区 / 分支 | 检查时已提交位置 | 提交之外的内容 |
| --- | --- | --- |
| 主工作区，`feature/usage-history` | `a0e4dc4` | 对话实验修订、实验程序改动、用例与测试 |
| R7，`claude/client-five-entries` | `aba494e` | R6 说话期限、决策和主动唤醒候选及回归证据 |
| 账号/中继工作区，游离 HEAD | `aba494e` | 与 Windows `080d0f4` 重合的桌面端 0.1.16 工作，以及更新的 Android 0.33.4 和更新/连接工作；它是混合工作区，不是干净的发布版本 |

Mac 主线另有 Windows 缺少的两个已提交改动：`6349ba3`（对话自然度设计、
串行/推测并行实验方案和离线实验程序）、`a0e4dc4`（把模型调用成本留给驱动设计，
不作为本次实验的决策规则）。本次与 Windows 基线合并，保留两边历史，
并把导入设计中的源码链接更新到 R7 目录结构。实验运行与正式 Runtime 分开。

Mac 未提交工作原样保留，不纳入本次同步。下次提交前要与汇合后的基线比较，
不能把账号工作区整包再提交，否则会重复已经汇合的内容，并混进尚未完成的 Android 工作。
私人实验结果、聊天、凭据、缓存及安装包不上传。
Windows 原有的两个未跟踪 Python 缓存文件保留并排除。

## 进展与剩余验收

| 方向 | 已实现 / 已记录 | 还缺什么 |
| --- | --- | --- |
| Runtime R1-R5 | Intake/来源、日志与恢复、归属、有界快照/增量同步、主机与设备凭据、回复展示 | 支持路径上的真实跨电脑故障、恢复、权限及客户端收敛验收 |
| R6 | 执行/说话/进度分离、有界上下文、期限、回调隔离和生命周期工作 | **保留的真实模型证据中，首个有意义完整气泡 <=3 秒仍为 FAIL**；默认流程切换继续受门禁限制，Mac 候选未部署 |
| R7 | 六层目录和消除循环导入已提交 | 后续行为变化继续过依赖测试 |
| R8 | 协商展示/请求同步和客户端迁移已部分实现 | 客户端验收完成后才能删旧接口、适配器或强制新最低版本 |
| 桌面账号与中继 | 0.1.16 源码已提交，包有本地使用记录；已包含 peer 同步重启修复 `3f1b905`，核心测试通过 | 重连修复安装后的验收；关闭 Tailscale 后的公网路径；真实断网恢复及历史收敛 |
| 共享历史 H1-H4 | `12bbd86` 已记录问题 | 正确标明回答电脑；让小幽 recall 能读已复制的桌面历史；向用户呈现统一历史；排除自动化噪声 |
| 对话风格 | 串行/推测并行方案和实验程序已提交 | Mac 另有未提交的实验结果/设计改动。私有 30 题陪伴盲评交接记录 v3 胜 16、R7 胜 9、平 5（p 约 0.23，不显著）；该结果本身不构成修改正式提示词的依据 |

继续工作前阅读：[重构验收记录](runtime-refactor-design.zh_CN.md)、
[历史及交互问题](app-runtime-simplification.zh_CN.md)、
[对话设计](dialogue-naturalness-design.zh_CN.md)、[实验方案](decide-speak-experiment.zh_CN.md)。
导入的[实验交接](decide-speak-handoff.zh_CN.md)记录的是当时的交接，
其中较早的完成状态不能代表后来的私人结果和 Mac 未提交修订。

## 分支与 GitHub 入口

### 已发布附件和源码尚未对应

[App 0.33.4 (73)](https://github.com/yanyuliu01/ai-passport/releases/tag/app-0.33.4-73)
中有单独上传、带版本号的 APK。但该标签和
[`companion-build-80`](https://github.com/yanyuliu01/ai-passport/releases/tag/companion-build-80)
均指向 **`66e6ef7`**，其 Android 版本配置实际为 **0.31.2 (59)**。
标签触发且通过的 [Companion app 构建](https://github.com/yanyuliu01/ai-passport/actions/runs/40479262721)
验证的是那份旧的已提交源码，不是 Mac 未提交的 0.33.4 源码。
不能因为共用发布页或标签触发，就把自动生成的 `pocket-hub-debug.apk`、
固件附件或 GitHub Source code 压缩包当成与单独上传的 0.33.4 APK 对应。

本次核对了发布元数据、Git 引用和源码版本字段，未验证 APK 内部内容、可复现性或手机安装。
保留现有标签和附件，补充说明；后续应整理并提交 Mac 的准确源码，再从该提交创建
新版本、经过测试的发布。移动旧标签或替换附件会掩盖差异，也可能影响现有更新客户端。

| 分支 / PR | 用途 |
| --- | --- |
| `feature/usage-history` | 默认集成线，放最新已提交产品源码和本进度索引 |
| `claude/client-five-entries` | 重构/客户端开发线；远端位置 `aba494e` 已包含在集成线中 |
| `runtime/r4-recovery-review` | 恢复审查线；`9d6cc37` 经 `28f3244` 合入 |
| `feature/multi-end-sync` | 较早的 mesh/客户端里程碑 `5631087`，已包含在集成线中 |
| `feature/usage-effort`、`feature/xiaoyou-agent-router`、`feature/ble-firmware-update`、`feature/pocket-hub-app`、`feature/claude-pocket` | 历史开发阶段，分支名及其版本说明不代表当前发布 |
| `main` | `33d3d1d` 的上游硬件测试基线，不是当前产品或默认分支 |
| [PR #1](https://github.com/yanyuliu01/ai-passport/pull/1) | 指向 `feature/pocket-hub-app` 的历史未关闭草稿；Runtime 0.4.1 / App 0.5.0 说明不代表项目最新状态 |

本次同步不删分支/标签、不改写历史、不改默认分支名、不发布版本。
在有改动的工作区继续前，先检查并协调现有工作，不能 reset、clean 或直接 pull 覆盖；
源码审阅优先使用独立 worktree。同步源码不等于重启 Runtime、替换客户端、发布 APK 或刷机。

## 本次同步验证

| 检查 | 结果 | 证据 / 边界 |
| --- | --- | --- |
| 仓库检查 | PASS | `python tools/check_repo.py`；检查双语文件、链接、秘密模式和仓库规则 |
| 离线实验程序 | PASS | `run --dry-run --repeats 1 --pause 0`：46 题、两组，共 92 条不同的题/组/轮次记录；无真实模型或实际动作 |
| 桌面编译 / 核心回归 | PASS | `npm run build:main`；`node --test dist-main/test/core.test.js`：24/24，含相同端点的 peer 同步重启用例 |
| 完整门禁 / 整体主机测试 | FAIL | `bash tools/validate.sh` 已运行仓库、工作流、C 主机及多组 Runtime 检查，随后在 `test_helper_model_preferences` 发现的继承用例 `test_helpers.HttpTests.test_add_list_and_remove_over_http_and_across_a_restart` 中超时，清理又遇 Windows 文件占用。该五项套件在 Windows 基线和汇合源码上单独重跑均通过；保留首次完整门禁失败，后续套件未执行 |
| Build | NOT RUN | 未到固件构建阶段；未构建 APK、安装包或新固件包；桌面 TypeScript 编译单独报告 |
| Device tests | NOT RUN | 本次无手机/设备验收或刷机 |
| 回归 / 首回复 / 恢复 / 多端同步 | 部分 / 保留证据为 FAIL / NOT RUN / NOT RUN | 源码回归不能证明安装后的行为；已查运行中 Runtime 0.24.0 的健康接口，未新测客户端首渲染或真实网络验收 |

完整门禁中固件更新互操作测试因 shell 未带 JDK 而跳过。源码合并没有改动门禁所测的
Runtime 代码；单独重跑通过不能抹掉超时，也不能证明其原因。
Unverified：剩余完整门禁套件、固件构建、桌面/Android 包测试、安装后的 peer 重连、
手机/BLE、三秒首回复达标、公网恢复、历史收敛，以及 0.33.4 源码与附件的准确对应。
