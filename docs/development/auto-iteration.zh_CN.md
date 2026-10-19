<p align="right">
  <strong>简体中文</strong> · <a href="auto-iteration.md">English</a>
</p>

# 自动迭代流水线（设计稿）

> 状态：2026-10-19。**已实现**：插件框架、App 使用事件上报、四个数据源、六个规则检测器、选题、模型网关和 `template_plan` 方案（P0、P1 和 P2 的大半）。**还没做**：改代码、门禁、发布、观察（P3、P4）。下面每一节标明了哪些是现在的代码、哪些还是设计。

## 一句话

Runtime 在本机持续收集 App 的使用信号，攒够一批后用**本地模型**找出问题、写出迭代方案、改 App 代码、跑完门禁，低风险的改动自动发布到手机，高风险的等主人一键确认；发布后继续观察，问题没解决或变差就自动撤回。每一步都是可插拔的插件。

## 设计前提

1. **模型弱，harness 重。** 不依赖 CC / Codex 这类能自主规划、读整个仓库的 agent。模型只做三件窄的事：给一段文本分类、按模板填一份方案、按给定上下文写一个小补丁。找文件、组织上下文、校验格式、编译、测试、判断风险、发布、回滚全由 Runtime 的确定性代码做。
2. **一切可插拔。** 数据源、问题检测、方案、改代码、验证、发布、观察、模型后端都是插件，按配置启用、排序、替换；拔掉某个插件，流水线退化而不是崩掉（例如拔掉"改代码"就只出方案报告）。
3. **建在现有记录上。** 对话和任务仍以[统一消息结构](unified-message-model.zh_CN.md)为准，不另起一套对话真相源；发布走现有 `app publish`；需要主人确认的走现有授权 / 快速审核。
4. **守住[跨端可靠性规则](cross-stack-reliability.zh_CN.md)。** 自动发布的版本同样要满足"不破坏已有功能、首次有效回复 3 秒内、多端一致"，门禁里有对应检查，过不了就不发。

## 流水线

```text
 ┌──────────── 采集（后台持续） ────────────┐
 │ App 事件 · 对话里的抱怨/纠正 · 卡片失败 · 同步/更新失败 · 延迟 │
 └──────────────────┬───────────────────────┘
                    ↓ 追加到 state/iterate/signals/*.jsonl
 检测（规则为主）→ 问题 Issue（指纹去重、计数、严重度、证据 ID）
                    ↓ 攒够一批 / 定时 / 主人手动
 分诊：打分排序，选 1 个问题进入本轮（一轮只修一件事）
                    ↓
 方案：模型按模板写 Plan（目标、改哪、验收标准、风险自评）→ harness 校验
                    ↓
 实现：在独立 git worktree 里，模型逐文件出补丁 → 应用 → 编译 → 把错误喂回 → 有限次重试
                    ↓
 验证门禁：单测 · 构建 · 仓库静态检查 · 改动范围/大小 · 中文与资源检查 · 回放用例
                    ↓
 策略：按风险分级 → 自动发布 / 等主人一键确认 / 只出报告
                    ↓
 发布：bump 版本 → app publish（默认只发本机）→ 记录来源提交
                    ↓
 观察：发布后 N 小时比较同一指纹的信号 → 关闭问题 / 自动撤回 / 重新排队
```

每一轮是一个 **Run**，有自己的编号、状态机和目录 `state/iterate/runs/<run>/`，里面存这一轮的输入快照、方案、补丁、门禁日志和结论。Run 同时在 App 里显示为一张只读卡（类似外部上报的卡），主人能看到"发现了什么、改了什么、发没发、怎么撤回"。

Run 状态：`collected → triaged → planned → implemented → verified → (awaiting_owner) → released → observing → closed | rolled_back | abandoned`。任何一步失败都停在当步并写明原因，不往下走；下一轮从头开始，不续跑半成品。

## 插件模型

### 插件种类

| 种类 `kind` | 输入 → 输出 | 默认插件 |
| --- | --- | --- |
| `source` | 外部数据 → `Signal` | `app_events`、`chat_feedback`、`card_failures`、`sync_health`、`update_health` |
| `detector` | `Signal[]` → `Issue[]` | `crash`、`error_rate`、`latency_3s`、`rage_tap`、`dead_end`、`complaint_cluster` |
| `triage` | `Issue[]` → 选中的 `Issue` | `score_by_impact` |
| `planner` | `Issue` + 代码上下文 → `Plan` | `template_plan` |
| `context` | `Plan` → 相关文件片段 | `java_symbol_index`、`layout_resource_index` |
| `implementer` | `Plan` + 上下文 → `Patch` | `search_replace_loop` |
| `gate` | worktree → 通过/失败 + 证据 | `gradle_unit`、`gradle_assemble`、`validate_static`、`diff_scope`、`strings_zh`、`replay` |
| `policy` | `Plan` + `Patch` + 门禁结果 → 风险级别和动作 | `risk_tiers` |
| `releaser` | 构建产物 → 发布记录 | `app_publish_local` |
| `observer` | 发布记录 + 新 `Signal` → 结论 | `fingerprint_compare` |

### 插件的样子

内置插件在 `runtime/xiaoyou_runtime/iterate/builtin/`；自己写的放在 `iterate.plugin_dirs` 里的目录，每个 `.py` 文件放一个 `PLUGINS` 列表。同名插件后加载的覆盖先加载的，所以自己写的可以替换内置的；加载失败的文件只记一条错误，不影响别的插件：

```python
def detect(ctx, signals):              # ctx 只开放这个插件声明过的权限
    ...
    return [Issue(fingerprint=..., title=..., severity=..., evidence=[...])]

PLUGINS = [{
    "name": "rage_tap", "kind": "detector", "version": 1,
    "consumes": ["app.rage_tap"],      # 只收这些类型的信号；以 . 结尾表示前缀
    "permissions": [],                 # 见下
    "run": detect,
}]
```

各种插件的 `run` 怎么调：source `run(ctx, cursor) -> (signals, new_cursor)`；detector `run(ctx, signals) -> [Issue]`；triage `run(ctx, issues) -> Issue | None`；planner `run(ctx, issue) -> plan`。插件抛 `Skip` 表示这次做不了（比如没有可用的模型），不算出错。

插件之间只通过数据对象说话（`Signal`、`Issue`、`Plan`、`Patch`、`GateResult`、`Release`），对象是带版本的 JSON，落盘在 Run 目录里。所以任何一步都能单独重跑、换插件对比、或者用存下来的输入做回放测试。

**权限**写在声明里，由 `ctx` 检查：`read_state`（状态目录）、`read_repo`（仓库路径）、`model`（模型网关）、以及以后的步骤要用的 `write_worktree`、`git_commit`、`publish`、`network`。没声明就拿不到，会抛 `PermissionDenied`。例如内置 detector 什么权限都没有；任何插件都拿不到 `git push`。插件和 Runtime 跑在同一个进程里，这只防"不小心越界"，不是安全沙箱：来路不明的插件不要放进 `plugin_dirs`。

### 配置

下面是现在代码认的写法，写出来的都是默认值（`iterate` 一节整个不写也行，只是不会自动跑）：

```json
"iterate": {
  "enabled": false,
  "trigger": {"min_new_signals": 50, "interval_hours": 24, "only_when_idle": true},
  "pipeline": {
    "source":   ["app_events", "chat_feedback", "card_failures", "reply_latency"],
    "detector": ["crash", "app_errors", "rage_tap", "slow_first_reply", "complaint_cluster", "task_failures"],
    "triage": "score_by_impact",
    "planner": "template_plan",
    "implementer": null
  },
  "plugin_dirs": [],
  "areas": ["app"],
  "allow": ["companion/android/app/src/main/**"],
  "deny": ["**/AndroidManifest.xml", "**/build.gradle"],
  "repo_dir": "..",
  "min_issue_count": 3, "window_days": 7, "retention_days": 30, "votes": 3, "max_classify": 40
}
```

把某个插件从列表里拿掉就是拔掉；`planner` 设成 `null` 时只找问题、不出方案。`areas` 决定选哪些方面的问题来改（`app`、`runtime`、`agent`），别的方面的问题照样进问题簿。`python3 -m xiaoyou_runtime iterate plugins` 列出装了哪些插件、哪些启用、每个的权限。`enabled` 为 true 时，Runtime 每 15 分钟看一次：主人那边没有正在做的事，就起一个子进程跑 `iterate run`，信号够不够、时间到没到由那一轮自己判断。

## 模型网关（和 Agent gateway 共用）

目标是省钱：**能用本机模型就用本机模型**，联网模型只做兜底、而且有每天的花费上限。这一层不属于自动迭代，是 Runtime 里所有"直接调一次模型"的地方共用的 `model_gateway.py`。

它对应 Agent gateway 方案（`docs/development/agent-gateway.zh_CN.md`，另一个会话在写，还没提交）里的 **Provider/model** 那一层：Agent gateway 管 harness/CLI（Claude Code、Codex、DSH 这些自带工具循环的程序）怎么安装、探测、启用；模型网关管推理端点在哪、凭据在哪、每个用途按什么顺序选、花了多少。两边共用同一份端点登记：

- 自动迭代这类"harness 很重、模型只做窄活"的调用，直接用网关的 `complete()` / `complete_json()`。
- 自带工具循环的 harness 用 `binding_env(provider)` 拿到指向同一个端点的环境变量（OpenAI 协议给 `OPENAI_BASE_URL`/`OPENAI_API_KEY`，Anthropic 协议给 `ANTHROPIC_BASE_URL`/`ANTHROPIC_AUTH_TOKEN`）。现在的 `claude_gateway.py` 就是手写的一个这样的绑定（Claude Code 接 DeepSeek）；以后 Agent 实例写 `"provider": "<名字>"` 就行，不必各自再写一份地址和密钥。**这一步还没接**：会动到 `agents.py` 和 `config.py` 里的 Agent 配置，等 Agent gateway 那边的注册表定下来一起改。

```json
"models": {
  "providers": {
    "local":    {"protocol": "openai", "base_url": "http://127.0.0.1:11434/v1"},
    "deepseek": {"protocol": "openai", "base_url": "https://api.deepseek.com/v1",
                 "key_file": "~/.config/xiaoyou/deepseek.key", "price": {"input": 2, "output": 8}}
  },
  "routes": {
    "default":          ["local/qwen2.5:7b"],
    "iterate.classify": ["local/qwen2.5:7b"],
    "iterate.plan":     ["local/qwen2.5-coder:14b", "deepseek/deepseek-v4-flash"]
  },
  "network": {"allowed": false, "daily_budget": 2}
}
```

- **协议**：`openai`（`/chat/completions`，Ollama、llama.cpp server、LM Studio、vLLM、DeepSeek、OpenAI 都认）和 `anthropic`（`/v1/messages`）。
- **本地还是联网**：`locality` 不写时按地址判断，本机、局域网、Tailscale（100.64/10）地址算本地。本地端点不计费；联网端点只有 `network.allowed` 为 true、今天的花费没到 `daily_budget` 时才会被选上。
- **选路**：用途找不到就退到上一级（`iterate.plan` → `iterate` → `default`）；候选按顺序试，连不上的本地端点 60 秒内不再试，直接走下一个。
- **JSON**：`complete_json` 从回答里取出 JSON（小模型常包一层 \`\`\`json），交给调用方的检查函数；不合格就把问题告诉模型重问，次数有上限。
- **用量**：每次调用在 `state/models/usage.jsonl` 记一行：用途、端点、模型、token、估算花费、耗时、成没成。不记提示词和回答。价格单位随主人定（元或美元），只用来算预算。
- 密钥用 `key_file` 或 `key_env`，不写进 `config.json`。

```bash
python3 -m xiaoyou_runtime models list           # 端点、每个用途的候选、今天花了多少
python3 -m xiaoyou_runtime models test iterate.plan   # 真的调一次，看走到了哪个端点
python3 -m xiaoyou_runtime models usage          # 今天每个端点/模型的次数、token、花费
```

## 数据从哪来

App 现在**没有**使用事件上报，这是要新增的第一块：

- **App 端**（已实现，`UsageEvents.java`）：事件追加到 App 私有目录里的一个小文件（最多 500 条），连上 Runtime 时每分钟、以及切到后台时发一批（最多 50 条）到 `POST /v1/app/events`，Runtime 收下之后才从文件里删，断线、崩溃都不丢。现在发的事件：`screen`（离开一个页面时：页面名、停留毫秒）、`error`（弹给主人的提示文字，最多 120 字）、`crash`（未捕获异常的类名和消息、栈顶 8 帧、线程名，进程退出前先写盘）、`rage_tap`（2 秒内在 48dp 范围内点了 4 次以上：页面名、3×6 网格里的格子、次数）。Runtime 还认 `slow`、`update` 两类，App 以后再发。每条带 `app_version`、`session`（每次启动换一个）、时间。不采集消息正文、输入内容、截图；Runtime 收的时候也只留每类登记过的字段，别的丢掉。旧 Runtime 没有这个接口时返回 404，App 留着下次再发。
- **Runtime 已有的**（已实现，`iterate/builtin/sources.py`）：`chat_feedback` 从卡片里读主人说的话，先用关键词粗筛像抱怨、纠正的句子，有本地模型时再让模型分类（同一句问 `votes` 次取多数），没有模型时按关键词粗分、可信度标低；电脑上旁观的会话不算。`card_failures` 读失败的卡。`reply_latency` 读每句话从 Runtime 收到到出第一个字的时间（这是 Runtime 一侧的近似，不等于跨端可靠性规则要求的"客户端发送到首次显示"）。第一次跑只回看 `window_days` 天，不把历史全送去分类。这些插件只读，不改记录。

信号统一写到 `state/iterate/signals/`，按天分文件、只追加；超过保留期（默认 30 天）删除原始信号，只留 Issue 级别的统计。手机、电脑都是主人自己的设备，数据不出本机；`chat_feedback` 交给模型分类时只发那一句话和前后各一句。

## 让本地小模型也跑得动：harness 承担的事

| 难点 | harness 怎么做 |
| --- | --- |
| 找问题需要判断力 | 能用规则的都用规则（崩溃、错误率、3 秒、连点、进入即退出）；模型只给对话里的抱怨分类，输出受 JSON Schema 约束，同一句跑 3 次取多数，不一致就标"不确定"不进入本轮 |
| 模型不知道改哪 | harness 预先建索引：Java 类/方法、布局 ID、字符串资源、事件里的控件 ID → 定义位置。Issue 带着控件 ID 和栈帧，直接定位到 1–3 个文件，只把这些片段给模型 |
| 方案写得散 | 方案是固定模板的 JSON：`问题`、`证据`、`改动点[{文件, 意图}]`、`验收标准`、`不改什么`、`风险自评`；harness 校验文件存在、在允许范围内、改动点不超过 3 个 |
| 补丁格式错 | 只接受 search/replace 块：`search` 必须在文件里**唯一命中**；不命中就把最接近的原文片段回给模型重写，最多 3 次 |
| 编译错误 | 编译失败时只截取报错行和对应代码前后 20 行回喂；总尝试次数、总耗时、总 token 都有上限，超了就放弃本轮 |
| 改着改着越改越大 | `diff_scope` 门禁：只能动方案里列出的文件，增删行数上限（默认 80 行），不许改权限、网络协议、同步、Manifest、构建脚本 |
| 判断"修好了没" | 不让模型说了算：发布后由 observer 比较同一指纹的信号前后数量；有回放用例的问题还要在 worktree 里跑用例 |

这些上限都在配置里，模型换强了可以放宽，换弱了可以收紧，流水线代码不变。

## 风险分级和发布

`risk_tiers` 策略把每个通过门禁的改动分成三级：

| 级别 | 典型改动 | 动作 |
| --- | --- | --- |
| **L1 自动发布** | 文案/中文提示、布局间距和对齐、空状态、已有按钮的显示条件、崩溃处的空值保护；只动允许范围内的 UI 文件；行数在上限内 | 门禁全过就发布 |
| **L2 一键确认** | 改交互流程、新增界面元素、动到网络请求或本地缓存的读取逻辑 | 生成待确认项，走现有授权 / 快速审核，主人在 App 里点一次就发 |
| **L3 只出报告** | 同步协议、授权、更新机制本身、Runtime 代码、固件、Manifest/权限、构建配置 | 只写方案和补丁，不发布；主人可以把它转给 CC/Codex |

**发布细节**（必须这样做，否则会出问题）：

- **从提交构建，不从工作区构建。** 每轮在 `git worktree` 里基于一个提交开工，提交留在本地分支 `auto/iter-<run>`，**不推送**。如果主人工作区里有未提交的 App 改动、而当前已发布的版本正是从这些未提交改动构建出来的，就不能自动发布——否则新版会悄悄丢掉那些改动。这种情况本轮降级为 L3，并提示主人先提交。
- **默认只发本机。** 现有 `app publish` 默认会传到 GitHub Releases（公开）；自动流水线用 `--local-only`，要不要公开托管由配置单独打开。
- **撤回要发更高版本号。** Android 不能装更低版本号的包，所以撤回 = 从上一个正常版本的提交重新构建、版本号 +1、再发布。现有 `app_release` 只保留最新一版，需要新增保留最近几版的来源提交和构建产物。
- **限流。** 一天最多自动发布 1 版；上一版还在观察期内不发下一版；连续 2 次撤回后自动停用自动发布，只出报告，等主人重新打开。
- **不重启 Runtime。** 第一阶段只改 App；Runtime 不动，所以不涉及"重启会掐断正在跑的任务"。

## 发布后观察

`fingerprint_compare` 在发布后等待一段时间（默认 24 小时，且新版至少被使用过若干次），比较：

- 本轮问题的指纹：信号数是否明显下降 → `closed`；
- 全局护栏：崩溃数、错误率、首次有效回复耗时、同步失败 → 任一变差超过阈值 → 自动撤回，问题标为"修复失败"并降低以后的优先级；
- 证据不足（新版用得太少）→ 延长观察，不宣称修好。

每轮的结论写进 Run 目录，也是下一轮分诊的输入：反复修不好的指纹会自动升级成 L3，交给主人决定。

## 在 App 里看到什么

- 每个 Run 一张只读卡：发现的问题（附证据次数）、方案摘要、改动文件、门禁结果、发布的版本号、观察结论。
- L2 的待确认项在现有授权入口里，一键通过 / 拒绝。
- 卡上有"撤回这一版"和"暂停自动迭代"两个动作，对应 `iterate rollback <run>` 和 `iterate.enabled=false`。

## 命令

```bash
# 现在有的
python3 -m xiaoyou_runtime iterate plugins            # 列插件、启用情况、权限、怎么写插件
python3 -m xiaoyou_runtime iterate run --dry-run --force   # 试跑：不存游标、信号、问题簿，看看效果
python3 -m xiaoyou_runtime iterate run [--force]      # 跑一轮；不加 --force 时信号不够、时间没到只采集
python3 -m xiaoyou_runtime iterate status             # 最近几轮
python3 -m xiaoyou_runtime iterate issues [--all]     # 问题簿
python3 -m xiaoyou_runtime iterate issue <编号前缀> --open|--ignore
# 以后的
python3 -m xiaoyou_runtime iterate replay <run> --stage implementer --plugin <别的插件>
python3 -m xiaoyou_runtime iterate rollback <run>
```

Runtime 运行时，后台线程只负责检查"开没开、主人那边忙不忙"，然后启动一个**独立子进程**跑这一轮；子进程崩了或超时不影响 Runtime 本身，也不占用发消息的路径。一轮出了方案（或出错）时，结果经 `POST /v1/reports` 在 App 里显示成一张来源为 `iterate` 的只读卡；完整内容在 `state/iterate/runs/<编号>/report.md`。

## 和之前方案的关系

10-16 和 Codex 讨论的是"采集主人和小幽的对话，批量优化主 Agent 的分工和 DSH loop"。这里的流水线是同一套骨架的第一个应用对象：`source`、`detector`、`observer` 是共用的；把 `scope` 换成 Runtime、`implementer` 换成改提示词或路由配置的插件，就是那套主流程优化。所以这次先把插件框架和 App 这条线做实，主流程优化以后作为另一组插件接进来，不另建系统。

## 分期

| 阶段 | 内容 | 完成标准 |
| --- | --- | --- |
| **P0 框架** ✅ | 插件加载、权限、数据对象、Run 目录与状态机、`iterate` 命令、后台调度；全部有单测 | 用假数据跑通 `run --dry-run`（`tests/test_iterate.py`） |
| **P1 采集** ✅ 代码 | App 端 `UsageEvents` + `POST /v1/app/events`；四个 source；六个规则 detector | 真机用一天后 `iterate issues` 能列出带证据的问题——**还没在手机上验证** |
| **P2 方案** 大半 | 模型网关 ✅、`template_plan` ✅（找代码现在是按线索搜文件，还不是符号索引） | 装好本地模型后只出报告模式稳定运行一周，主人看方案觉得靠谱 |
| **P3 实现+门禁** | worktree、`search_replace_loop`、全部门禁、L3 报告 | 对历史上修过的问题（头像偏右、首页英文等）回放，能产出通过门禁的补丁 |
| **P4 发布+观察** | `risk_tiers`、`app_publish_local`、版本保留与撤回、`fingerprint_compare`、App 里的 Run 卡 | 先只开 L2（每次一键确认）运行两周，再打开 L1 自动发布 |

每个阶段单独提交、单独可用；后面的阶段没做完时，前面的已经能产出价值（P1 之后就有问题清单，P2 之后就有方案报告）。

## 待主人确认

1. **本地模型用哪个。** 这台 Mac 上现在没有装 Ollama / LM Studio / llama.cpp。网关按 OpenAI 兼容接口写，装哪个都能接；没配模型时流水线照样找问题，只是不出方案。DeepSeek API 可以作为联网兜底，受每天预算限制。
2. **L1 自动发布的范围**是否按上表；要不要一开始就打开，还是先跑 L2 两周。
3. **事件采集范围**是否按"数据从哪来"里的几类，是否可以接受。
