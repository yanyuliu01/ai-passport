<p align="right"><strong>简体中文</strong> · <a href="code-cleanup-plan.md">English</a></p>

# 代码清理执行方案（第一阶段）

状态：执行方案，2026-10-23，待主人确认后开始。依据是[小幽完整方案](xiaoyou-agent-model.zh_CN.md)和[交互简化问题清单](app-runtime-simplification.zh_CN.md)。

## 范围

这一阶段**只删不加，不搬文件**。按已定事项，模块化整理放在执行与恢复约定落实之后；这里只清掉已经没人用的代码和不再需要的旧版兼容。
唯一的行为变化是删除暗中追问（已定）。

规则：

- 先对齐、先跑基线，再动手。每批只做一类删除，单独提交，可以单独回退。
- 每批删完跑同样的验证，结果和基线对比：不能出现新的失败。
- 不重启正在运行的 Runtime；App 发布、推送都等主人确认。

## 第 0 批：本地与远端对齐

现状（2026-10-23 核对）：

- 本地和远端 `feature/usage-history` 从 `0010d2b` 分叉。远端多 3 个提交（`2a60890`、`0c4ee0e`、`66e6ef7`，Codex 从 Windows 推送），本地多 3 个文档提交。
- 本地 93 个未提交文件和远端那 3 个提交的内容逐文件一致，只有三处不同：本地的新文档（以本地为准）、3 个测试文件（远端是修好 Windows 兼容的新版，以远端为准）、两个 `.DS_Store`（不提交）。

步骤：

1. 备份：把所有未提交改动打包存到 `build/` 下，留作回退。
2. 复核：再逐文件比一次工作区和远端，差异只能是上面三处，多出任何一处就停下来问主人。
3. 清掉本地未提交改动（远端已有同样内容），把 `.DS_Store` 加进 `.gitignore`。
4. 把本地 3 个文档提交变基到远端之上。远端 `2a60890` 带了旧版的问题清单、完整方案和目录，冲突一律取本地新版；Windows 交接文档取远端。
5. 跑一遍基线验证（见“验证”），记下结果，作为后面每批的对照。已知旧环境下 `./tools/validate.sh` 会因为沙箱禁止回环端口而失败，以实际记录为准。
6. 推送等主人确认。

完成标志：`git status` 干净；和远端相比只多出文档提交；基线结果已记录。

## 第 1 批：删除暗中追问（Runtime）

| 删除 | 位置 |
| --- | --- |
| 追问提示词 `PROMISE_NUDGE` | `runtime/xiaoyou_runtime/voice_tools.py` |
| 工具循环里的 `nudged` 分支和对它的特殊处理 | `runtime/xiaoyou_runtime/dsh_stream.py` 约 280～325 行 |
| 测试里对追问的断言 | `runtime/tests/test_decision.py` |

保留：`promised()` 正则只作计数（`xiaoyou.py` 已经这样用）；`promises.mark_nudged` 是到点提醒，与追问无关，不动。

验证：Runtime 全部测试；新增一条测试——一句会被正则判成承诺、但没有调工具的回复，只调用一次模型，只产生一段回复。

## 第 2 批：App 里没人用的代码

| 删除 | 依据 |
| --- | --- |
| `DsView.java`（82 行） | 全仓库没有引用 |
| `LocalSpeechSession.java` 和它的测试 | 只有自己的测试在用 |
| `MainActivity` 的 `fillTaskHead`、`refreshTaskThread`、`refreshTaskPending`、`iconButton`（约 130 行） | 没有调用 |
| `HubStore.pendingMessages` | 没有调用 |
| `HubStore` 不带编号的旧重载：`chatAsked(text)`、`chatStreaming(text)`、`chatHeard(placeholder, heard)`、`chatAnswered` 的 3 参数和 4 参数版本 | 只用带编号的版本 |
| `RuntimeClient.sendImage`，以及 `send` 的 2、3、4 参数重载 | 只用 5 参数版本 |

验证：Android 单元测试、调试构建和 Lint；删掉的名字在仓库里搜不到引用。行为不变。

## 第 3 批：App 里对旧版 Runtime 的兼容

现在所有在用的 Runtime 都是 0.20.0 以上（Mac 0.23.1、Windows 0.20.0），都支持 `/v1/sync` 和其中的用量。

| 删除 | 位置 |
| --- | --- |
| 旧的 `fetchFeed` 和 `watchLoop` 里按能力切到 feed 的分支 | `RuntimeClient.java` |
| “返回 404 就当成旧版 Runtime”的处理 | `RuntimeClient.watchLoop` |
| 任务做完后再调一次 `fetchFeed` 刷新（问题 C2，sync 本来就会推送变化） | `RuntimeClient.submitScoped` |
| 对 `sync`、`sync_usage` 能力的判断（改为默认具备） | `RuntimeClient.watchLoop` |
| 资源包接口返回 404 就当成没有的处理（0.20.0 已有该接口） | `RuntimeClient.appPacks` |
| 只写不读的 `CAPABILITIES` 和只给 feed 用的 `HubStore.feedSeq()` | `RuntimeClient`、`HubStore` |

执行时的调整（2026-10-23）：`presentation_v1` 的判断**保留**。Windows 运行的 0.20.0 没有这项能力，当成默认具备会让手机每次重连都从头同步。

已知回退（2026-10-23 真机验证）：删掉“任务做完后立刻拉一次”后，手机在同步追完之前看不到任务结果（Mac 要追约 10 分钟，见问题清单 N10）。主人决定不在 App 里补回，留给 Runtime 侧统一修。Windows 的对话、用量、任务结果和 Mac 的对话、用量都已通过。

验证：Android 单元测试、调试构建和 Lint；然后真机连 Mac 和 Windows 各一次，确认对话、用量、任务结果都正常显示。装到手机走现有发布流程，需要主人确认。

## 暂不删

| 项目 | 原因 |
| --- | --- |
| Runtime 的 `/v1/feed`、单条消息等待和推送流接口；电脑客户端中继放行列表里的 `/v1/feed` | 已安装的旧 App 还在用，按完整方案第 6 节在客户端迁移完之后再删 |
| `RuntimeClient.turns()` 旧聊天记录迁移 | 要先确认手机上的旧数据已经迁完 |
| 手机反向上传聊天历史（`shareHistory`） | 等“一份状态”那一步再处理 |
| `runtime/backup-*` 等本机备份目录 | 已被 `.gitignore` 忽略，不在仓库里；是否清理由主人决定 |

## 验证命令

| 范围 | 命令 |
| --- | --- |
| 文档与仓库规则 | `python3 tools/check_repo.py` |
| Runtime | 在 `runtime/` 下 `python3 -m unittest discover -s tests` |
| Android | 在 `companion/android/` 下 `./gradlew testDebugUnitTest assembleDebug lint`（需先设好 `JAVA_HOME` 为 JDK 17、`ANDROID_HOME`） |
| 电脑客户端 | 在 `companion/desktop/` 下 `npm test` |
| 完整门禁 | `./tools/validate.sh` |

每批交付按[跨端可靠性规则](cross-stack-reliability.zh_CN.md)报告构建、主机测试、设备测试和未验证项。

## 回退

每批一个提交，用 `git revert` 单独撤回。第 0 批另有 `build/` 下的备份包。Runtime 不重启，App 未经确认不发布，所以回退不影响正在用的服务。

## 预计规模

第 1 批约 50 行，第 2 批约 300 行，第 3 批约 120 行，合计删掉约 470 行，不新增功能代码（只新增一条测试）。
