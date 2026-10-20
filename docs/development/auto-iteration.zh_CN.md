<p align="right">
  <strong>简体中文</strong> · <a href="auto-iteration.md">English</a>
</p>

# 自动迭代与统一采集（设计稿）

> 状态：2026-10-19。每一节都标了**已实现**（现在的代码）或**设计**（还没写）。已实现的部分：插件框架、统一采集（`/v1/events`、事件类型表、事件随 mesh 同步到每台电脑、`events` 命令，安卓 App 已改用新格式）、迭代租约与能力声明、四个数据源、六个规则检测器、模型网关、单问题方案插件（`template_plan`，会被版本方案取代）。其余都是设计。

## 一句话

所有客户端（手机 App、以后的电脑客户端、随身设备、Runtime 自己）用同一套约定把使用数据报给**任意一台电脑**，经 mesh 每台电脑都有全部数据；攒够一批后，由当时有空、有工具的那台电脑用**本地模型为主**把问题归并成几个主题，写出一份**版本级的迭代方案**，主人审一次；再按方案小步改代码、整版过门禁、低风险自动发布；发布后按主题看效果，变差就自动撤回。每一步都是可插拔的插件。

## 设计前提

1. **按版本迭代，不按单点修。** 一次处理一批问题：先归并、找共同的根因、写完整的版本方案，再拆成工作项去实现。单个问题只是证据，不是改动单位。
2. **模型弱，harness 重。** 不依赖 CC / Codex 这类能自主规划、读整个仓库的 agent。模型只做窄的事：分类一句话、分析一组问题、按模板写方案、按给定上下文写小补丁。找文件、组织上下文、校验、编译、测试、判断风险、发布、回滚都由确定性代码做。
3. **钱花在判断上，量留在本地。** 次数多的活（分类、写补丁、修编译错误）用本地模型；每批只做一次、决定方向的活（写版本方案）可以用联网的强模型，受每天预算限制。都经过同一个[模型网关](#模型网关已实现)。
4. **无中心，可迁移。** 没有必须开着的主机：每台电脑都有全部数据，哪台合适就由哪台跑下一步；电脑可以随时加入或撤掉（[产品原则](../product-principles.zh_CN.md) P17）。
5. **一切可插拔。** 采集的事件类型、检测、归并、方案、实现、门禁、发布、观察、模型后端都是插件或登记项，按配置启用和替换；拔掉一步，流水线退化而不是崩掉。
6. **建在现有系统上。** 对话和任务仍以[统一消息结构](unified-message-model.zh_CN.md)为准；客户端身份、能力声明、多台电脑互通复用 Runtime 现有的配对、`/v1/info` 和 mesh；发布走现有 `app publish`；要主人确认的走现有授权横条和快速审核。
7. **守住[跨端可靠性规则](cross-stack-reliability.zh_CN.md)。** 自动发布的版本同样要满足"不破坏已有功能、首次有效回复 3 秒内、多端一致"，过不了门禁就不发。

## 整体结构

```text
 客户端                    采集网关（任意一台）               自动迭代（持有租约的那台）
 ─────────                ──────────────────               ─────────────────────────────
 安卓 App ──┐              POST /v1/events                  外层：每批一次
 电脑客户端 ─┤  本地缓冲     ├ 按事件类型表过滤字段             归并 → 找根因 → 版本方案
 随身设备 ──┤（经手机转）    ├ 按 id 去重                      → 对照产品原则 → 主人审一次
 Runtime ───┤  → 收下才删   ├ 存进事件库（按天、只追加）            ↓
 各个 Agent ─┘              └ 经 mesh 同步到每台电脑          内层：每个工作项
                                     ↓                       小补丁 → 编译 → 整版门禁
                            信号 → 检测器 → 问题簿                  ↓
                                                            分级 → 发布 → 按主题观察 → 撤回
                    ┌────────────── 模型网关（本地优先，联网有预算）──────────────┐
```

## 统一采集（采集网关）

它和 Agent gateway 是对称的两层：Agent gateway 管 Runtime **向外**接各种 Agent，采集网关管各种客户端**向内**报数据。两者都把 Runtime 当网关，复用同一套身份、能力声明和 mesh。

| 概念 | Agent gateway | 采集网关 |
| --- | --- | --- |
| 接入方 | Agent / harness（Claude Code、Codex、DSH） | 客户端：`android`、`desktop`、`device`、`runtime`、`agent` |
| 接入方式 | 每种 harness 一个 adapter | 所有客户端遵守同一份上报约定，不需要 adapter |
| 注册表 | Agent 实例与能力 | 事件类型表：字段、长度、隐私级别、保留期 |
| 协商 | Runtime 声明 `gateway_v1` | Runtime 在 `/v1/info` 里声明 `events_v1` 和它认识的事件类型与版本；客户端只发对方认识的 |
| 存哪 | 记录中心 / 消息日志 | 单独的事件库。**不混进聊天记录**：保留和隐私规则不同 |

### 事件格式（已实现）

```json
{"id": "客户端生成的唯一编号", "at": 1800000000000, "type": "screen", "type_version": 1,
 "client": {"kind": "android", "id": "这台设备的编号", "version": "0.20.1", "platform": "android-14"},
 "session": "这次启动的编号", "fields": {"name": "home", "ms": 3200}}
```

- 接口是 `POST /v1/events`，一次最多 200 条，回复 `accepted`、`rejected`、`duplicates` 三个数。旧名字 `POST /v1/app/events` 是同一个接口；旧版 App 的扁平格式也认，按 `client.kind=android` 处理。代码在 `iterate/events.py`。
- **事件类型表**登记每种事件允许的字段和长度、隐私级别（`usage` 使用行为、`diagnostic` 错误与崩溃、`content` 含原话）、保留天数。Runtime 只留登记过的字段，别的丢掉，并按各类型的保留天数清理。内置 6 种；插件目录里的文件写 `EVENT_TYPES = [EventType(...)]` 可以加新的。`/v1/info` 里有 `events_v1` 能力和完整的类型表，客户端据此决定发什么。
- 不采集消息正文、输入内容、截图。`content` 级只用于 Runtime 自己产生的对话抱怨信号，原话本来就在电脑上，且只保留摘要。

### 上报约定（所有客户端相同）

1. 事件先写进客户端本地的缓冲文件，有上限（安卓是 500 条）。
2. 联网时成批发送；**Runtime 回复收下之后才从缓冲里删**，断线、崩溃都不丢。
3. 按 `id` 去重，所以重发是安全的。
4. 对方不支持某类事件（`/v1/info` 里没有，或者回 404）就先留着，不报错。
5. 崩溃要在进程退出前写盘。

安卓 App 已经这样做了（`UsageEvents.java`）。0.21.1 起发这些事件：`screen`（离开页面；设置页带上是哪一节，如 `settings:connection`）、`tap`（点了哪个控件）、`rage_tap`（连点，带控件名）、`notice`（普通提示）、`error`（真正的失败）、`update`（查更新、有新版、下载、安装每一步）、`crash`。控件名只用 App 自己起的名字（侧边栏各项、发送、麦克风、设置里固定文案的行、按钮文字里的数字换成 `#`）；显示任务标题、电脑名、执行记录这类内容的行只记成 `row`，**不记屏幕上的内容**，也不用无障碍描述（那里会带上任务标题）。

两条从试跑里学到的规则：**改版前后的使用单独看**（新版发布后 3 小时内、有新版还没装、或者那次使用里有更新事件，都不当成用不明白的证据）；**弹出的提示不都是错误**（旧版 App 把所有提示都记成 error，Runtime 只把读起来像失败的当错误）。电脑客户端以后照这份约定实现；**随身设备**不直接连 Runtime，事件经蓝牙交给手机，由手机标上 `client.kind=device` 转发；**Runtime 自己**的事件（回复耗时、任务失败、转派）改成在记录中心发生的那一刻直接写一条，不再事后读卡片推算；**各个 Agent** 的执行事件里和质量有关的部分（转派、失败、用时）由 Agent gateway 同样写一份进来。

### 无中心：每台都有全部数据，谁合适谁来跑（设计）

- **数据每台都有一份全的。** 使用事件和其他记录一样是"产地记录"：哪台收到，哪台就是产地，按产地序号经 mesh 同步给其他电脑（只追加，按 `id` 去重，只有产地能改，所以没有冲突）。问题簿、版本方案、审核结果、每一轮的状态也都是记录，同样同步。所以任何一台都能看到全貌。
- **迁移不用搬数据。** 新电脑配上同一个 mesh 口令，从任意一台拉齐全部记录；旧电脑随时可以关掉或撤掉，只要它的记录已经被至少一台别的电脑拿到（`events summary` 显示每台还有多少记录没被别人拿到）。
- **谁来跑靠租约，不靠指定。** 要跑某一步时，先写一条"迭代租约"记录（哪台、哪一步、到什么时候），随 mesh 同步。两台同时申请时，申请时间早的胜出，相同再按 `runtime_id` 比较，输的一方放弃。持有者边跑边续约；它关机或掉线，租约过期（默认 2 小时）后别的电脑可以接手。
- **能不能跑看能力。** 每台在 `/v1/info` 里声明自己能做哪几步：有没有这个仓库、有没有 JDK 和 Android SDK、能不能连上要用的模型。一台只申请自己能做的步骤：比如一台没有安卓工具链，它可以做归并、找根因、写方案，构建和发布留给有工具链的那台。
- **接手从最后完成的那一步开始。** 每一步的产物（问题组、根因、版本方案、审核结果、工作项、已完成工作项的补丁）都作为记录同步；补丁用 git bundle 的形式随记录传，接手的电脑在自己的 worktree 里应用后继续。只有正在做、还没提交的那个工作项需要重做。接手前要确认两台的仓库基于同一个提交，不一致就先停下提示主人。
- **哪台都能把新版本给手机。** 发布记录和安装包随 mesh 同步（大文件按校验值按需拉取），手机连的是哪台都能拿到同一个新版本；撤回也一样。
- 现在（**已实现**）：使用事件和迭代租约都随 mesh 同步（记录类型 `events`、`lease`，旧版 Runtime 会忽略它们）；每批事件是一条记录、一个序号，按 `id` 去重；`/v1/info` 的 `iterate.stages` 声明这台能做 `collect`、`detect`、`plan`（有仓库和可用模型）、`build`（有 JDK 和 Android SDK）。一轮开跑前向本机 Runtime 申请租约（`POST /v1/iterate/lease`），别的电脑持有就记为"别的电脑正在跑"并停下；这台写不了方案就跳过方案，留给能写的电脑。**还没做**：用 git bundle 在电脑之间接着做补丁、发布的安装包随 mesh 同步、Runtime 自己在记录中心直接写事件（现在仍从卡片推算）、设备事件经手机转发。

### 管理（已实现）

```bash
python3 -m xiaoyou_runtime events types        # 登记的事件类型、字段、隐私级别、保留期
python3 -m xiaoyou_runtime events summary      # 各客户端、各类型最近多少条，存在哪
python3 -m xiaoyou_runtime events export --since 7d
python3 -m xiaoyou_runtime events delete --type rage_tap --before 2026-10-01
```

## 外层循环：每批一个版本方案（设计）

触发条件和现在一样：新信号攒够一批，或者离上一次够久，并且主人那边空闲。区别是输出不再是"修一个问题"，而是一份版本方案。

```text
冻结这一批：最近一段时间问题簿里所有还没处理的问题
  ↓
① 重建使用路径：把同一个 session 的事件串起来
   （进了哪个页面 → 做了什么 → 在哪卡住 → 退出了），给问题补上"发生在哪条路径上"
  ↓
② 归并（规则）：按页面、使用路径、会改到的文件、在同一个 session 里先后出现，
   把问题聚成组。比如"首页连点 + 首页报错 + 抱怨首页找不到入口"归成"首页"一组
  ↓
③ 找根因（每组一次，本地模型）：这组现象是不是同一个原因？是流程设计问题还是代码问题？
   输出：根因假设、依据（指向具体证据）、把握程度
  ↓
④ 写版本方案（每批一次，可以用联网的强模型）：
   - 这一版的 2–3 个主题，每个主题覆盖哪些问题、对应哪个根因
   - 每个主题在流程层面怎么改，而不是"某个文件某一行加个判断"
   - 主题之间的依赖和先后
   - 这一版不做什么，为什么
   - 按主题的验收指标
   - 会波及哪些已有功能，要做哪些回归检查
  ↓
⑤ 对照产品原则：方案和《产品原则》文档逐条对照，有冲突的写出来，严重冲突的打回重写
  ↓
⑥ 主人审一次：一张卡，可以一键通过、打回（附一句意见）、或删掉某个主题
```

**[产品原则](../product-principles.zh_CN.md)**：写清楚这个产品是什么、不做什么、交互原则，每条有编号（P1–P21）。版本方案必须注明依据哪几条、和哪几条冲突。现在是 AI 起草的 v0.1，主人修改认可后生效。

没有可用的模型时，外层照样做 ①②，产出"问题地图"报告，只是不写方案。

## 内层循环：照方案小步实现（设计）

```text
版本方案 → 拆成有顺序的工作项（每项 1–3 个文件，标明依赖）
  ↓
开一个版本分支 auto/v<版本>，在独立的 git worktree 里，基于某个提交（不碰主人的工作区）
  ↓
对每个工作项（按依赖顺序）：
  harness 找相关代码 → 模型只写 search/replace 块 → search 必须唯一命中，否则带着最近的原文重写
  → 编译 → 失败只把报错行和前后 20 行喂回去 → 次数、时间、token 都有上限
  → 这一项单测通过才提交到版本分支
  ↓
整版门禁：改动范围与行数、全部单测、构建、仓库静态检查、中文字形、回放用例、
          方案里列出的回归检查
  ↓
分级 → 发布 → 观察（见下）
```

一个工作项做不出来时，不硬改：要么把整个主题从这一版里拿掉（其他主题照常发），要么整版停下，在卡上写明卡在哪里，主人可以转给 CC / Codex。

## 风险分级和发布（设计）

整版的级别取各工作项里最高的一级。

| 级别 | 典型改动 | 动作 |
| --- | --- | --- |
| **L1 自动发布** | 文案、中文提示、布局和对齐、空状态、已有按钮的显示条件、崩溃处的空值保护 | 方案主人已经审过，门禁全过就发布 |
| **L2 一键确认** | 改交互流程、新增界面元素、碰网络请求或本地缓存的读取逻辑 | 进授权横条，主人点一次才发 |
| **L3 只出报告** | 同步协议、授权、更新机制本身、Runtime 代码、固件、Manifest / 权限、构建配置 | 只写方案和补丁，不发布；主人可以转给 CC / Codex |

这一级由 harness 按改到的文件定下限（**已实现**：`planner.risk_floor`），模型自评更低不算。`QuickReview.java` 和 `QuickReviewDialog.java` 控制审批范围和主人决定，下限为 L3；自动迭代不能自动发布对它们的修改。

发布时必须注意：

- **从提交构建，不从工作区构建。** 版本分支只在本地，**不推送**。如果主人工作区里有未提交的 App 改动、而当前已发布的版本正是从这些改动构建出来的，就不能自动发布（新版会悄悄丢掉那些改动），整版降为 L3，并提示主人先提交。
- **默认只发本机。** 现有 `app publish` 默认会传到 GitHub Releases（公开）；自动迭代用 `--local-only`，公开托管要在配置里单独打开。
- **撤回要发更高的版本号。** Android 不能装版本号更低的包，所以撤回 = 用上一个正常版本的提交重新构建、版本号 +1、再发布。现有 `app_release` 只保留最新一版，需要改成保留最近几版的来源提交。
- **限流。** 一天最多自动发 1 版；上一版还在观察期就不发下一版；连续撤回 2 次后自动停掉自动发布，只出报告，等主人重新打开。
- **不重启 Runtime。** 先只改 App。

## 发布后观察（设计）

等一段时间（默认 24 小时，并且新版被使用过足够多次），按**主题**比较发布前后：

- 主题指标：比如"首页的挫败信号"（连点、报错、抱怨）整体是否下降。下降 → 主题关闭，它覆盖的问题一起关。
- 全局护栏：崩溃、错误率、首次有效回复耗时、同步失败，任一变差超过阈值 → 自动撤回，主题标为"修复失败"。
- 证据不足（新版用得太少）→ 继续观察，不宣称修好。

结论写进这一轮的目录，作为下一批的输入：反复修不好的主题会升为 L3，交给主人。

## 主人在 App 里看到什么

**不弹窗，也不发系统通知。** 只在需要主人时出现在现有入口里：

| 时机 | 怎么出现 | 状态 |
| --- | --- | --- |
| 一轮出了方案或出错 | 任务列表里一张来源为 `iterate` 的只读卡，内容是报告 | **已实现**（单问题方案；只采集没出方案的轮次不发卡） |
| 版本方案等主人审 | 一张卡：通过 / 打回附意见 / 删主题 | 设计 |
| L2 等主人放行 | 顶部的授权横条，支持快速审核 | 设计（复用现有入口） |
| 发布了 | 卡片写明这一版改了什么、怎么撤回；手机照常提示有新版本 | 设计 |
| 卡上的操作 | "撤回这一版"、"暂停自动迭代" | 设计 |

## 插件模型（已实现框架）

| 种类 `kind` | 输入 → 输出 | 插件 | 状态 |
| --- | --- | --- | --- |
| `source` | 事件库 / 记录 → `Signal` | `app_events`、`chat_feedback`、`card_failures`、`reply_latency` | 已实现 |
| `detector` | `Signal[]` → `Issue[]` | `crash`、`app_errors`、`rage_tap`、`slow_first_reply`、`complaint_cluster`、`task_failures` | 已实现 |
| `journey` | 事件 → 使用路径 | `session_paths` | 设计 |
| `grouper` | `Issue[]` → 问题组 | `by_screen_path_files` | 设计 |
| `analyst` | 问题组 → 根因假设 | `group_root_cause` | 设计 |
| `version_planner` | 问题组 + 根因 + 产品原则 → 版本方案 | `version_plan` | 设计 |
| `decomposer` | 版本方案 → 工作项 | `work_items` | 设计 |
| `implementer` | 工作项 + 代码 → 补丁 | `search_replace_loop` | 设计 |
| `gate` | 版本分支 → 通过/失败 | `diff_scope`、`gradle_unit`、`gradle_assemble`、`validate_static`、`strings_zh`、`replay` | 设计 |
| `policy` | 方案 + 补丁 + 门禁 → 级别 | `risk_tiers` | 设计（下限规则已实现） |
| `releaser` | 构建产物 → 发布 | `app_publish_local` | 设计 |
| `observer` | 发布 + 新信号 → 结论 | `theme_compare` | 设计 |
| `triage` / `planner` | 单问题选题 / 单问题方案 | `score_by_impact` / `template_plan` | 已实现，会被上面的外层循环取代，保留作单问题修复的备用 |

内置插件在 `runtime/xiaoyou_runtime/iterate/builtin/`；自己写的放在 `iterate.plugin_dirs` 里，每个 `.py` 放一个 `PLUGINS` 列表。同名插件后加载的覆盖先加载的；加载失败只记一条错误：

```python
def detect(ctx, signals):              # ctx 只开放这个插件声明过的权限
    ...
    return [Issue(fingerprint=..., title=..., severity=..., evidence=[...])]

PLUGINS = [{"name": "rage_tap", "kind": "detector", "version": 1,
            "consumes": ["app.rage_tap"], "permissions": [], "run": detect}]
```

**权限**写在声明里，由 `ctx` 检查：`read_state`、`read_repo`、`model`，以及以后的 `write_worktree`、`git_commit`、`publish`、`network`。没声明就拿不到。任何插件都拿不到 `git push`。插件和 Runtime 跑在同一个进程里，这只防不小心越界，不是安全沙箱。

插件之间只通过带版本的 JSON 数据对象说话，都存在这一轮的目录 `state/iterate/runs/<编号>/` 里，所以任何一步都能单独重跑、换插件对比、回放测试。

## 模型网关（已实现）

目标是省钱：**能用本机模型就用本机模型**，联网模型只做兜底，有每天的花费上限。它不属于自动迭代，是 Runtime 里所有"直接调一次模型"的地方共用的 `model_gateway.py`。

它对应 Agent gateway 方案（`docs/development/agent-gateway.zh_CN.md`，另一个会话在写，还没提交）里的 **Provider/model** 层：Agent gateway 管带工具循环的 harness 怎么装、探测、启用；模型网关管推理端点在哪、凭据在哪、每个用途按什么顺序选、花了多少。两边共用同一份端点登记：harness 用 `binding_env(provider)` 拿到指向同一端点的环境变量。**这一步还没接到 Agent 配置上**，等 Agent gateway 的注册表定下来一起改。

```json
"models": {
  "providers": {
    "local":    {"protocol": "openai", "base_url": "http://127.0.0.1:11434/v1"},
    "deepseek": {"protocol": "openai", "base_url": "https://api.deepseek.com/v1",
                 "key_file": "~/.config/xiaoyou/deepseek.key", "price": {"input": 2, "output": 8}}
  },
  "routes": {
    "default":              ["local/qwen2.5:7b"],
    "iterate.classify":     ["local/qwen2.5:7b"],
    "iterate.analyze":      ["local/qwen2.5-coder:14b", "deepseek/deepseek-v4-flash"],
    "iterate.version_plan": ["deepseek/deepseek-v4-pro", "local/qwen2.5-coder:14b"],
    "iterate.patch":        ["local/qwen2.5-coder:14b"]
  },
  "network": {"allowed": false, "daily_budget": 2}
}
```

- **协议**：`openai`（Ollama、llama.cpp server、LM Studio、vLLM、DeepSeek、OpenAI 都认）和 `anthropic`。
- **本地还是联网**：不写 `locality` 时按地址判断，本机、局域网、Tailscale（100.64/10）算本地。本地不计费；联网端点只有 `network.allowed` 为 true、今天花费没到 `daily_budget` 时才会被选。
- **选路**：用途找不到就退到上一级（`iterate.plan` → `iterate` → `default`）；候选按顺序试，连不上的本地端点 60 秒内跳过。
- **JSON**：从回答里取出 JSON，交给调用方检查；不合格就带着问题重问，次数有上限。
- **用量**：每次调用在 `state/models/usage.jsonl` 记一行（用途、端点、模型、token、估算花费、耗时、成没成），不记提示词和回答。
- 密钥用 `key_file` 或 `key_env`，不写进 `config.json`。

```bash
python3 -m xiaoyou_runtime models list
python3 -m xiaoyou_runtime models test iterate.plan
python3 -m xiaoyou_runtime models usage
```

## 让本地小模型也跑得动：harness 承担的事

| 难点 | harness 怎么做 |
| --- | --- |
| 找问题需要判断力 | 能用规则的都用规则（崩溃、错误率、3 秒、连点）；模型只给对话抱怨分类，同一句问几次取多数（**已实现**） |
| 整体判断小模型做不好 | 先用规则归并成组，每组材料很小再交给模型分析；整版方案这一步允许用联网强模型 |
| 模型不知道改哪 | 按线索（栈帧里的文件和行号、报错原文、页面名、抱怨里的词）找出 1–3 个文件，只给相关片段（**已实现**，以后加符号索引） |
| 方案写得散 | 固定模板的 JSON，harness 校验文件存在、在允许范围内、条目数量（**已实现**单问题版） |
| 补丁格式错 | 只接受 search/replace 块，必须唯一命中，否则带着最近的原文重写 |
| 编译错误 | 只回喂报错行和前后 20 行；次数、时间、token 有上限 |
| 越改越大 | 只能动工作项列出的文件，行数有上限，危险文件直接拒绝 |
| 判断修好没有 | 不让模型说了算：看发布后的主题指标 |

这些上限都在配置里，模型换强了可以放宽，换弱了可以收紧。

## 配置（已实现部分）

```json
"iterate": {
  "enabled": false,
  "trigger": {"min_new_signals": 50, "interval_hours": 24, "only_when_idle": true},
  "pipeline": {
    "source":   ["app_events", "chat_feedback", "card_failures", "reply_latency"],
    "detector": ["crash", "app_errors", "rage_tap", "slow_first_reply", "complaint_cluster", "task_failures"],
    "triage": "score_by_impact", "planner": "template_plan", "implementer": null
  },
  "plugin_dirs": [], "areas": ["app"],
  "allow": ["companion/android/app/src/main/**"],
  "deny": ["**/AndroidManifest.xml", "**/build.gradle"],
  "repo_dir": "..",
  "min_issue_count": 3, "window_days": 7, "retention_days": 30, "votes": 3, "max_classify": 40
}
```

`enabled` 为 true 时，Runtime 每 15 分钟看一次：主人没有正在做的事，就起一个子进程跑 `iterate run`，那一轮自己判断信号够不够、时间到没到。子进程崩了不影响 Runtime。设计里还会加迭代租约的过期时间、每台能做哪些步骤的声明，以及外层循环的插件名。

## 命令

```bash
# 已实现
python3 -m xiaoyou_runtime iterate plugins
python3 -m xiaoyou_runtime iterate run --dry-run --force   # 试跑，不存状态
python3 -m xiaoyou_runtime iterate run [--force]
python3 -m xiaoyou_runtime iterate status
python3 -m xiaoyou_runtime iterate issues [--all]
python3 -m xiaoyou_runtime iterate issue <编号前缀> --open|--ignore
python3 -m xiaoyou_runtime events types|summary|export|delete
# 设计
python3 -m xiaoyou_runtime iterate map                     # 问题地图：分组、根因
python3 -m xiaoyou_runtime iterate replay <run> --stage <步骤> --plugin <别的插件>
python3 -m xiaoyou_runtime iterate rollback <run>
```

## 和之前方案的关系

10-16 和 Codex 讨论的"采集主人和小幽的对话、批量优化主 Agent 的分工和 DSH loop"，用的是同一套骨架：同一个采集网关、同一个问题簿、同样的外层循环。把 `areas` 换成 `agent`，实现步骤换成改提示词、路由配置的插件，就是主流程优化。所以不另建系统。

## 分期

| 阶段 | 内容 | 完成标准 |
| --- | --- | --- |
| **P0 框架** ✅ | 插件加载、权限、数据对象、Run 目录、`iterate` 命令、后台调度 | `tests/test_iterate.py` |
| **P1 安卓采集** ✅ 代码 | `UsageEvents` + `/v1/app/events`；四个数据源；六个检测器 | 手机用一天后 `iterate issues` 有带证据的问题——**还没在手机上验证** |
| **P2 模型网关** ✅ | 本地优先、联网预算、JSON 重问、用量记录 | `tests/test_model_gateway.py`；还没装本地模型 |
| **P3 统一采集** 大半 ✅ | `/v1/events`、事件类型表、`/v1/info` 声明、Runtime 直接写事件、事件随 mesh 同步、迭代租约与能力声明、`events` 命令；安卓改用新格式 | 两台电脑切换使用一天，每台都有全部事件、没有重复；关掉正在跑的那台，另一台在租约过期后接手 |
| **P4 外层循环** | 使用路径、归并、找根因、版本方案、产品原则文档、方案审核卡 | 只出方案模式跑两批，主人认为方案是完整的而不是零散补丁 |
| **P5 内层循环** | 工作项、版本分支、补丁循环、整版门禁 | 用历史问题（头像偏右、首页英文等）回放，能产出通过门禁的版本 |
| **P6 发布与观察** | 分级、本机发布、版本保留与撤回、主题观察、卡上的撤回 / 暂停 | 先只开 L2 跑两周，再打开 L1 |

每个阶段单独提交、单独可用。

## 待主人确认

1. **本地模型用哪个**、**版本方案用不用联网强模型和每天预算**：放到实现时再定。网关按 OpenAI 兼容接口写，装哪个都能接。
2. **[产品原则](../product-principles.zh_CN.md) v0.1**：AI 已起草，等主人修改。
3. **L1 自动发布**是一开始就开，还是先只开 L2 跑两周。
