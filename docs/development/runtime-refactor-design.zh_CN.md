<p align="right"><strong>简体中文</strong> · <a href="runtime-refactor-design.md">English</a></p>

# Runtime 三层重构设计

状态：设计已确认，2026-10-23。按第 4 节 R1～R8 实施，尚未开始。

本文把[小幽完整方案](xiaoyou-agent-model.zh_CN.md)落到 Runtime 代码上，按三层写：**状态接收层**（东西怎么进来、存成什么）、**回复消息层**（回复怎么生成、怎么送到各端）、**agent loop**（小幽怎么决定、怎么做）。问题编号指[交互简化问题清单](app-runtime-simplification.zh_CN.md)。所有阶段都要满足[跨端可靠性规则](cross-stack-reliability.zh_CN.md)。

## 0. 总览

```
             ┌──────────── 1. 状态接收层 ────────────┐
输入适配器 ──▶ Intake.accept(事件) ──▶ 事件日志 ──▶ 当前状态（投影）
 (HTTP/帮手/电脑会话/                    │              │
  其他电脑/定时/授权)                    │              ▼
                                         │      ┌── 3. agent loop ──┐
                                         │      │ 唤醒 → 定 → 记下决定 │
                                         │      │ → 说 → 执行        │
                                         │      └────────┬─────────┘
                                         │               │ 结果也是事件
                                         ▼               ▼
                              ┌──── 2. 回复消息层 ────┐
                              │ 快照+增量 /v1/sync   │ ──▶ 手机 / 电脑 / 设备
                              │ 投递规则（通知、设备）│
                              └──────────────────────┘
```

三条贯穿全文的规则：

1. **只有 Intake 能写事件日志。** 其他模块只读当前状态、只通过提交事件改变它。
2. **先存后做。** 任何输入、决定、结果都先作为事件持久化，再触发后续动作；重启后从日志恢复，不重做已接收的事。
3. **客户端只看一份投影。** 消息、任务、会话、额度、授权、小幽在做什么，全部从 `/v1/sync` 一条路下发。

## 1. 状态接收层

### 1.1 目标结构

```
适配器（只读外部、只产出事件）
  HttpAdapter      你发的消息、语音、文件、写操作（/v1/messages、/v1/files、/v1/actions）
  HelperAdapter    帮手进展、结果、结束（取代 Tasks 里直接写卡）
  DesktopAdapter   观察电脑上的 Codex / Claude 会话（只读、幂等）
  MeshAdapter      其他电脑同步来的记录和指令
  TimerAdapter     到点提醒、关注条件
  SystemAdapter    额度、部署、版本、在线状态
        │
        ▼
Intake.accept(event) ── 去重、校验、写日志、推进当前状态、通知订阅者
        │
        ├── EventLog（cards.log.jsonl 的演进，唯一权威记录）
        └── World（当前状态投影：请求、任务、会话、电脑、帮手、授权、额度、焦点、承诺）
```

### 1.2 接口

**事件格式**（所有来源一致）：

| 字段 | 说明 |
| --- | --- |
| `id` | 全局唯一：来自客户端的请求用请求编号（`client_id`），其他用 `runtime_id` + 本地序号；重复提交同一 `id` 返回同一结果（已定：只按编号去重） |
| `seq` | 本机日志序号，单调递增，同步游标用它 |
| `at` | 原始发生时间（N9） |
| `source` | `owner` / `helper` / `desktop` / `mesh` / `timer` / `system` |
| `kind` | `said`、`action`、`task_progress`、`task_ended`、`session_observed`、`approval_requested`、`approval_answered`、`assistant_message`、`usage`、`deployed` … |
| `conversation`、`subject` | 属于哪个对话、关于哪个任务 / 会话 / 电脑 |
| `actor`、`cause` | 谁做的（`app`、`desktop`、`xiaoyou`、`mesh:<节点>`、`cli`、`system`）、因为哪个事件（A4） |
| `trust` | `owner` / `system` / `helper` / `external`；`external` 只当资料，永远不会变成主人的话 |
| `payload` | 各 `kind` 自己的内容 |

**Python 接口**（模块内）：

```python
Intake.accept(event) -> Accepted(id, seq, duplicate: bool)
World.get(kind, id) / World.list(kind, **filter)        # 只读
World.subscribe(after_seq) -> 事件流                     # agent loop 和回复层用
```

### 1.3 各来源的规则

- **你发的消息**：先 `accept`，再排进对话；回执只说“已收到，编号 X”。
- **帮手**：进展只更新任务的进展字段，不写主对话；结束一律产出 `task_ended`（完成 / 失败 / 已取消），交给 agent loop 收尾（A1）。
- **电脑会话**：只写进它自己的观察会话，永远不进主对话、不触发小幽说话；同一会话重复观察不产生新事件；接话材料不能引用接话材料（N8、N11）。
- **其他电脑**：每条记录只由产地修改；对方发来的指令（取消、确认、转交）带 `actor=mesh:<节点>` 进入同一个 `accept`。
- **取消、确认等写操作**：统一是 `kind=action`，带 `actor` 和原因（A4）。

### 1.4 和现有代码的对应

| 现在 | 以后 |
| --- | --- |
| `Service.submit` / `submit_voice` / `_enqueue`、`Ledger`（`messages.json`） | `HttpAdapter` + `World` 里的“请求”投影；`messages.json` 变成可重建的快照 |
| `record.exchange` / `reply` / `request_changed` / `task`、`cards.py` 直接写 | 全部改为 `Intake.accept`；`cards.json` 变成投影快照 |
| `tasks.Control.activity`、`Xiaoyou._finished` 写卡 | `HelperAdapter` 产出 `task_progress` / `task_ended` |
| `desktop_sync._publish`、`directory_discovery`、`project_brief.recap_text` | `DesktopAdapter`（只读、幂等、作用域限定在观察会话） |
| `mesh._apply_current` 分发到消息 / 任务 / 授权 / 指令 | `MeshAdapter`；指令走 `accept(kind=action, actor=mesh:…)` |
| `transcript.json`（小幽自己的对话记录） | 不再单独写，由 agent loop 从日志按需组装上下文 |
| `Service.cancel`、`Xiaoyou.cancel`、`__main__` 中断、`mesh` 取消指令 | 一个 `action:cancel` 事件，`actor` 分别为 `app` / `cli` / `mesh:…` / `xiaoyou` |

## 2. 回复消息层

### 2.1 目标结构

```
assistant_message 事件（小幽说的每一段：块、格式、状态 streaming/final、修订号）
        │
        ▼
Projection（唯一的显示投影：presentation_v1 的演进，含格式片段）
        │
        ├── /v1/sync：快照 + 增量，所有端唯一的下行通道
        ├── 投递规则：在身边→设备；前台→原对话；否则只推“等你确认”和到点提醒
        └── 其他电脑：经 Mesh 复制同一批事件
```

### 2.2 接口

**同步**：

```
GET /v1/sync?cursor=<游标>&wait=<秒>
  → { epoch, cursor, more, snapshot?, events[] }
```

- **游标为空或失效时给快照，不重放历史。** 快照只包含当前状态：每个对话最近 N 条消息、所有未结束任务、未处理授权、额度、小幽状态。然后从当前游标开始给增量。这样换电脑或重启后几百毫秒就能用，不再要追 415 页（N10）。更早的历史按需分页取（`GET /v1/conversations/{id}/history`）。
- **页大小按条数定**，不再是 48 KB；单条过大的内容（长回复、附件）只给摘要和引用。
- **客户端把游标存到本地**，重启后接着追。
- **正在生成的回复**也是 `assistant_message` 事件，`status=streaming`，每次整段替换；`final` 后旧片段不能覆盖。客户端不再为单条消息另开等待循环或推送流（C1、C9）。
- **长连接**：`wait` 有变化立即返回；以后要换成推送流（SSE / WebSocket）也只是换传输，事件格式不变。

**发送**：

```
POST /v1/messages { id, conversation, text | audio_ref | image_refs } → { id, accepted: true }
POST /v1/actions  { id, type, target, reason } → { id, accepted: true }
```

只返回“已收到”。处理状态、回复、结果全部经 `/v1/sync` 到达。

**显示**：投影给出块边界、格式片段（加粗、列表、代码）和作者，各端只按投影渲染，不自己解析 Markdown（N13）。

### 2.3 和现有代码的对应

| 现在 | 以后 |
| --- | --- |
| `Service._report("partial")`、`_update`、`_snapshot`、`get(wait, rev)` | 产出 `assistant_message` 事件；`get` 只为旧客户端保留 |
| `/v1/messages/{id}?wait`、`/v1/messages/{id}/stream`、App 的 `MessageStream` | 删除（客户端迁移后） |
| `Service.sync`、`cards.sync_since`、`FEED_BUDGET_BYTES = 48 KB` | 快照 + 增量；按条数分页 |
| `Service.feed`、`/v1/feed` | 删除（客户端迁移后） |
| `presentation.py` | 保留并成为唯一投影；补齐首页卡片等处的格式片段 |
| `proactive.py` 里“说不说、在哪说” | 投递规则（本层），“要不要开口”留在 agent loop |

## 3. Agent loop

### 3.1 目标结构

```
唤醒规则（订阅 World 的事件，确定、便宜）
  owner said ─────────────┐
  task_ended（我派出的） ──┤
  关注条件满足 ────────────┼─▶ 对话的一步 step(conversation, trigger)
  承诺到点 ────────────────┘
                             1. 组上下文：焦点 + 未完成承诺 + 最近对话 + 相关检索 + 自身状态
                             2. 定：一次调用，只选一个动作
                                reply / dispatch / amend / cancel / watch / promise / remember / stay_silent
                             3. 记下决定：accept(action 事件)（先存后做）
                             4. 说：一次流式调用，只有查看类工具，只说已经发生的事
                             5. 执行：派帮手（选电脑）、改任务、停任务……结果作为事件回到第 1 层
```

- **主动和被动是同一个 `step`**，只是触发不同。新增主动能力 = 加一条唤醒规则。
- **任务结束必然唤醒**：完成、失败、取消都会让她在原对话说一句（A1、M3、N12）。
- **一轮只说一次**：没有暗中追问（已删），没有“写 JSON 失败再问一次”的重说路径；动作不合法时照实说办不了。
- **并发**：同一对话的“定”串行；你在她说话时又发一句，打断正在说的那段（沿用现有打断），新消息排在下一步。不同对话互不阻塞。
- **在哪台电脑做**：`dispatch` 时按帮手所在、目录可达、额度、在线情况选择；你点名的优先。
- **自查工具**：版本、部署、在线、进行中的任务直接读 `World`，不派帮手去查（M1、M2）。

### 3.2 接口

```python
Loop.step(conversation, trigger: Event) -> None
ContextBuilder.build(conversation, trigger) -> Context
Decider.decide(context) -> Action            # 先定；超时走 decide-then-speak 文档里的退路
Speaker.speak(context, action_result) -> 流式 assistant_message 事件
Executor.apply(action) -> 事件               # dispatch / amend / cancel / watch / promise / remember
HelperPort.run(job, control) -> 进展与结果事件  # Claude Code、Codex、DeepSeek、远端节点
```

### 3.3 和现有代码的对应

| 现在 | 以后 |
| --- | --- |
| `Xiaoyou.hear`、`routing.decide`（点名、路由器、默认） | `Loop.step`；点名作为上下文里的约束，交给 `Decider` |
| `_converse` / `_converse_with`：带工具说话、写 JSON 老路、重问 | `Decider` + `Speaker` 两段；删除 JSON 老路和重问 |
| `_converse_decided`、`decision.py`（`decide_first`，默认关） | 成为唯一路径（实测首字时间达标后） |
| `_plan`、`_apply_conversation`、`_assign`、`_resume`、`_launch`、`_continue_desktop` | `Executor.apply` |
| `_finished`、`_relay`、`_close`、`cancel` | `task_ended` 事件 + 唤醒规则 + `Speaker` |
| `lane_prompt`、`BACKGROUND_TURNS = 6`、`RECENT_FINISHED_CARDS = 5` | `ContextBuilder`（焦点 + 承诺 + 相关性） |
| `proactive.Pulse`（20 秒一次，三种写死的理由） | 唤醒规则 |
| `memory.py`、`promises.py`、`recall.py`、`voice_tools.py`、`dsh_stream.py` | 保留，分别归 `ContextBuilder`、`Executor`、`Speaker` |
| `agents.py` 与 5 个适配模块的循环引用 | `HelperPort` 接口在下层，适配器只依赖它 |

## 4. 迁移顺序

原则：**旁路替换**，新层先包住旧代码、行为不变，再一处一处把调用搬过去；每一步单独提交、单独验证、可回退。和完整方案第 6 节的四步对应。

| 步骤 | 内容 | 验收 | 对应方案 |
| --- | --- | --- | --- |
| R1 | 事件加 `actor`、`cause` 字段（向后兼容）；取消产出 `task_ended` 并让小幽在原对话说一句 | 三种来源的取消都留下发起方；小幽都会说一句 | 说做一致（即原 a、b） |
| R2 | 引入 `Intake`，先作为 `Record` 的外壳；各写入点逐个改为 `accept`（HTTP → 帮手 → 电脑会话 → 其他电脑） | 日志内容与改造前逐条一致（对拍测试）；电脑会话不再进主对话 | 一份状态 |
| R3 | 引入 `World` 投影；`messages.json`、`cards.json` 改为可重建快照；`transcript.json` 停写 | 删掉快照后重启，状态完全恢复；不重做已接收的事 | 一份状态 |
| R4 | 同步改为“快照 + 增量”、按条数分页；客户端游标持久化 | 换电脑或重启后 1 秒内显示最新状态；任务结果不再等十分钟（N10） | 自动连接 |
| R5 | 回复改为 `assistant_message` 事件；投影补齐格式片段 | 新客户端只靠 `/v1/sync` 就能显示流式回复和格式（N13） | 一份状态 |
| R6 | Agent loop：实测并默认开启“先定，再说”；删 JSON 老路和重问；唤醒规则取代 Pulse；`ContextBuilder` | 首个回应 3 秒内；一句话只出一段；“这个”能接上 | 说做一致 |
| R7 | 按三层搬文件：`events/`、`world/`、`delivery/`、`loop/`、`helpers/`、`api/`，拆掉循环引用 | 只搬不改，测试全过 | 模块化整理（已定放在约定之后） |
| R8 | 客户端切到新接口后，删除 `/v1/feed`、单条消息等待和推送流、旧兼容 | 旧接口无人调用；版本门槛生效 | 收敛界面 |

R1 可以马上做；R2～R5 是主体；R6 需要真实模型实测；R7 在行为稳定后做；R8 等 App 和电脑客户端迁移完。

## 5. 兼容与风险

- **日志格式只增不改**：旧字段保留，新字段可缺省；旧版 App 在 R8 之前照常可用。
- **多台电脑版本不一**：其他电脑可能还是 0.20.0。新字段对它们是可忽略的；需要新能力时按 `/v1/info` 的能力判断，低于门槛时提示升级。
- **快照的正确性**：快照必须和增量来自同一个游标点，切换时不能丢事件；用“游标 + epoch”校验。
- **“先定，再说”的时延**：多一次模型调用，可能超过 3 秒；先实测，不达标用文档里的退路。
- **改造期间正在运行的 Runtime**：每一步都不要求重启才能回退；上线（重启）由主人决定。

## 6. 已定（2026-10-23）

- 快照里每个对话带最近 50 条消息。
- R4 先不换推送流，事件格式稳定后再说。
- R7 采用上面的六个目录名：`events/`、`world/`、`delivery/`、`loop/`、`helpers/`、`api/`。
