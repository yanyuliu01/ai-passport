<p align="right">
  <strong>简体中文</strong> · <a href="README.md">English</a>
</p>

# 小鱼 Runtime

修改本项目的 Agent 必须先读[根目录指令](../AGENTS.zh_CN.md)和
[App／Runtime／固件强制可靠性准则](../docs/development/cross-stack-reliability.zh_CN.md)。
其中规定回归、3 秒首回复、连接恢复和多端一致性验收。下文的能力说明不能
作为这些要求已经通过验证的证据。

小鱼是主人唯一的对话对象，也是一个独立的身份：她不是某个模型的别名。这个 Runtime 是
小鱼背后那个一直开着的小服务，属于她的东西都在这里——人设、她和主人的对话记录、
“这句话交给谁”的判断，以及别人做完之后由她怎么转述。Claude Code、Codex、另一台电脑上
的小鱼，还有以后接进来的本地模型，都只是她可以用的**代理**。

主人提的每个需求是**一件事**，有自己的一张卡：他说的话、小鱼的回答、后来的补充都记在
这张卡上。小鱼自己只做快的事——听懂、能马上答的答掉、要花时间的交给帮手在后台做——
所以一件事没做完不妨碍说下一句，几件事可以同时在做，做的过程中可以补充、改要求或
取消。小鱼每说一段话带三样东西：完整回复、给 Passport 小屏幕看的一两句简报、像素宠物
此刻的表情；回复里的 Markdown 标记、代码围栏和表情符号会先整理成纯文字，小屏幕和终端
都显示得干净。消息可以是打出来的文字，也可以是一段录音；录音先由配置里选定的语音识别
引擎变成文字。

```text
手机 App / 任意客户端 ──HTTP──> 小鱼 Runtime
                                  │  人设 · 对话记录 · 卡 · 路由 · 转述
                                  ├──> claude   （Claude Code 命令行，能以小鱼的身份回话）
                                  ├──> codex    （Codex 命令行，只干活）
                                  ├──> dsh      （DeepSeek Harness 命令行，能以小鱼的身份回话）
                                  ├──> 任意命令 （本地模型等）
                                  └──> 另一台电脑上的小鱼 Runtime
```

状态：验证阶段，先跑在自己的电脑上。同一份代码之后要搬到虚拟机，里面没有任何假设
某台机器的地方。见[哪些验证过、哪些没有](#哪些验证过哪些没有)。

## 小鱼身份与人设（0.27.0 源码）

正式包名为 `xiaoyu_runtime`。她称创造她的人为老板，重视自己的感受、意见与好奇，故事从真实互动和探索中逐渐积累；固定做事规则继续独立提供。旧命令、配置兼容及部署边界见[人设与迁移说明](../docs/development/persona-boundaries.zh_CN.md)。本次源码候选不会替换其他工作区或已安装 Runtime 的工作。

## 统一请求同步（0.24.0）

Android 0.33.0 和桌面端 0.1.9 可以通过 `/v1/sync` 与正式消息、任务、审批一起接收已受理请求的当前状态。Runtime 声明 `sync_requests_v1` 能力，消息受理响应也带此标记，供远端 Runtime 帮手选择协议。兼容客户端发送 `requests=<id>,<id>`，最多 32 个编号。可选的 `requests` 投影保留识别文字、阶段、完整替换的 partial、最终回复、小屏摘要、情绪、失败，以及原有的正式消息 `message_snapshot`。这里只返回明确列出的公开字段，不返回私有恢复输入；限定对话的同步不能读取另一段对话的请求。

当前请求快照不推进历史游标。只有请求阶段改变时，也会唤醒同一个订阅。请求投影每页按 512 KiB 限制，允许单个超大条目先前进，`requests_more` 列出延期编号；客户端轮转有数量限制的观察集合，有延期内容时立即继续。`requests_missing` 保留受理不确定状态，不重新提交写操作。手机独立的审批订阅也改为 `/v1/sync`，合并待审批／已回答／已撤回的增量，不会把每一页当成整份待审批清单。

已安装的旧客户端仍由 feed、逐消息等待和 stream 适配接口支持。新客户端保留经能力协商的旧 Runtime 读取路径；确认版本回退后仍使用原受理编号，绝不重发 POST。这些适配接口尚未删除：R8 切换还需要核验已安装客户端的迁移，并约定、执行最低版本策略。默认 `decide_first` 保持不变，等待单独的真实响应验收。源码和主机测试不能证明完整气泡三秒内显示、真实重连或多端验收。

## 模型选择的任务执行边界（R6 源码）

`loop/executor.py` 负责既有 `task_action` 的选择保存、执行声明、结果记录和恢复核对。正常对话动作与待执行动作的恢复共用 Executor 中的执行分支和明确的动作接口。私有请求日志保留原格式、操作编号、帮手配置指纹和目标修订号；没有增加存储、HTTP 接口或模型调用。选择／声明保存失败时不会进入适配器；已声明执行或结果未知的动作不会重做；已有成功结果直接复用，即使帮手后来改变也不重新执行。

待执行动作恢复仍核对原请求／对话、帮手配置和目标修订号。负责人检查继续由既有操作边界负责。显示打断标记不进入已保存选择／结果；远端动作收据也仍不等于远端已经执行。直接点名帮手的交办和补充也经过 Executor。对话上下文准备仍留在外部：先保存交办记录，再准备背景；补充只有恢复空闲任务时才准备背景。既有任务管理器、原生会话和远端控制适配器保留准入和执行机制；默认决定模式切换及其余 R6 迁移仍需单独验收。

## Speaker 模型调用边界（R6 源码）

`loop/speaker.py` 通过既有调用入口承接对话模型阶段、决定／回复阶段、帮手结果转述及定时回复，负责流式全文替换／节流、原生会话使用和已引用历史的回执。模型调用前捕获原负责人／操作检查，并在流式回调与返回后复核；这条路径拒绝过期输出写入原生会话或历史回执。临时调用跳过共享模型历史窗口、会话和回执；仅决定的调用不把说话历史标为已送达。调用方原有工具、打断、格式、引用预算和剩余时限均保留。

对话控制器仍负责选择流程和发布回复；旧路径中可修改状态的工具箱保留到决定模式切换。本次提取没有增加模型调用、降级路径、存储格式或权限；源码可用和温启动回复测试不代表完整冷启动、语音、真实帮手故障验收通过。

旧工具回话、JSON 降级和格式修正现共用一个单调时钟的 `voice_timeout_seconds` 截止时间。
上下文准备会消耗这份预算，过期或被打断后不能再启动下一次模型请求。dsh 流式转命令行的
降级及旧 headless 说话会话重连沿用剩余小数秒，不把不足一秒的时间向上补到一秒。
后台帮手控制仍保留单独的、由主人批准继续等待的策略。

每次 Speaker 调用在成功或失败后都会关闭文本／工具回调。工具检查到达修改状态的边界，
并与原有负责人／效果检查组合。过期前已经保存的修改继续以原记录为准，从保存的事实
报告，不再调用模型重复操作。迟到输出不能保存调用方的原生会话或引用回执。默认配置、
首答验收标准及持久化／API 格式均不变；有界超时或错误气泡不算三秒首答通过。

## 帮手重启恢复（R6.1 源码）

已保存但尚未领取的帮手输入，在启动策略和身份初始化后，沿用原运行编号及参数恢复。
原帮手缺失、配置改变或图片缺失／改变时保留排队，等待核对；可能已经开始执行的任务
保持 `unknown`，不自动重做。消息日志里已有完整执行结果时，使用稳定的总结编号，
将保存的结果一次送回原对话，不再次调用模型。私有恢复输入保存在现有任务日志，
不进入客户端或 Mesh 投影；没有这些输入的旧任务仍按原先行为标为未完成。
跨电脑交来的请求重启后继续等同一次执行，不把结果不明提前报成终态失败。
剩余工作及仅源码的验证范围见[重构交接](../docs/development/runtime-refactor-design.zh_CN.md#2026-10-24-核对与后续顺序)。

升级前保留状态目录副本。旧程序会忽略新增恢复字段，回退时不能覆盖保留的待执行输入。

Runtime 没保存到结果时，恢复流程可只读检查原 Codex 会话中明确完成的轮次。
核对最新执行尝试、原帮手配置、原生目录、会话及轮次；进展、部分输出或另一轮完成都不算。
执行尝试已变化时不能发布旧结果；较长的恢复回复将完整原文保存在附件。

在实际执行的 Runtime 上调用 `POST /v1/cards/<id>/reconcile`，请求体为
`{"run_id":"<original run>"}`，可重新核对一件结果不明的帮手任务。只有最终结果已持久发布，
才返回 `status: confirmed` 和卡；证据不足则返回 `unconfirmed`。旧运行编号、远端副本、
观察卡或仍在执行的任务返回 `409`；接口需要认证。它不会恢复原生会话、调用模型或重做操作。
`/v1/info` 以 `execution_reconciliation` 声明能力。本批只有后端查询接口，没有 App 核对页面，
也不能人工覆盖请求操作的不确定结果。未支持的 harness、缺失／歧义／分叉／损坏的来源，以及
超过 16 MiB 的原生文件，仍保留结果不明。

## 帮手结果转述隔离（源码）

帮手已保存的原始结果与模型生成的转述彼此独立。转述入队前捕获负责人代次，原执行轮次、取消状态、对话、负责人可用性／冲突、插话或关闭发生变化时拒绝旧转述。同一检查覆盖转述模型、流式回调、原生会话／上下文回执、格式化与发布；转述结束后关闭旧文本回调。最终任务写入还在既有卡片锁内比较当前活动轮次，旧总结不能完成替换后的新轮次。受托任务在执行 Runtime 交回结果时保留来源对话负责人语义。

转述过期后，帮手原结果仍保留；若原运行派发尚未取消或替换，则进入 `unknown`，明确说明等待核对投递。现有核对接口或重启恢复可以发布已保存的结果，不再调用模型或重复操作。待投递状态落盘失败时，原结果仍可供重启恢复。若只是只读转述模型失败，且原检查仍有效，继续使用已有的事实摘要回退。这不新增客户端接口、保存格式、调度器或自动接管，也不代表所有帮手回调和外部执行边界均已隔离。

## 请求结果不明时的人工核对（源码）

`/v1/info` 的 `request_effect_review` 声明人工核对能力。在处理原请求的 Runtime 上，
经认证调用 `GET /v1/messages/<id>/effects`，读取原 `operation_id`、不透明的 `revision`、
已选操作及核对记录。操作的私有输入不进入普通 feed／sync；这个接口供明确的主人核对页面使用，
不能用于模型或工具自动重试。

主人检查实际状态后，用 `POST /v1/messages/<id>/effects` 提交
`{operation_id, revision, review_id, outcomes}`。`outcomes` 将每个尚未成功保存结果的操作编号
对应到 `applied`（主人确认已生效）或 `not_applied`（主人确认未生效）；尚未领取的 `pending`
操作只能确认未执行。回执丢失时保留相同 `review_id` 和请求体重试。操作者由服务端认证的
设备身份确定，不能由请求体冒填；过渡期旧凭据仍可使用。

核对和投递回执保存在现有请求日志，放在基线已对客户端隐藏的私有负责人元数据内。用稳定编号
将一条说明发布到原请求指定的主对话或任务作用域（包括明确要求回主对话的任务补充），
明确它是主人的人工确认。此过程不调用模型、不执行、重试或回滚工具；原操作领取、
结果及请求的不确定状态仍保留审计。核对已持久发布后，解除这轮请求对空闲移交的阻挡；结果不明
的帮手执行仍须单独核对。普通聊天历史裁剪不会让已持久确认的核对重新变成待处理。

仍在处理／来自别处的请求、过期操作编号或版本、负责人代次变化、冲突核对和权限权威离线，
均拒绝（`409`）；输入格式错误为 `400`。存储／发布失败返回 `503`；已经追加成功的核对保留
原身份，重试只修复发布，不重做操作。`status` 区分 `needs_review`、`recorded`（已保存待发布）
和 `reviewed`。本批只有后端源码，客户端页面和真实跨端验收仍待完成。`96c7c2b` 基线程序忽略核对字段，
会保守地继续阻挡结果不明的工作；回退须保留状态目录副本。

## 定时操作结果不明时的人工核对（源码）

`timer_effect_review` 声明认证后的定时核对入口，沿用主人明确确认的规则，不调用模型、不重做
或回滚工具。`GET /v1/timers/effects` 列出待处理来源；可选 `conversation`、`limit`（1～100，
默认 50）及不透明 `cursor` 限定分页。列表改变时旧游标返回 `409`，从第一页刷新。摘要不包含
选定参数。旧领取或损坏来源缺少可核对明细时，显示 `reviewable: false` 及原因，不编造原操作结论。

`GET /v1/timers/<id>/effects` 返回原操作、来源、绑定原负责人的 `revision` 和核对记录。
同路径 `POST` 接收 `{operation_id, revision, review_id, outcomes}`，规则与消息核对一致：
覆盖全部非成功操作，未领取的 pending 只能确认 `not_applied`。操作者来自认证设备；丢失回执后
保留相同请求体、核对编号和凭据身份。`503` 用原身份重试，`409` 刷新。原对话仍在处理、权限
权威不可用或负责人代次改变时，拒绝新确认或发布。

保存后由 `needs_review` 变成 `recorded`；稳定的主人确认说明回到原主对话，且发布／归档回执
落盘后，才成为 `reviewed`。说明保存前，投递卡保持等待。完成前原来源继续阻挡移交，也不能
恢复旧操作；重试和重启只修复同一说明与回执。完成后退出待处理列表，但原编号仍可查询，
说明被普通历史裁剪也不会重新产生待核对操作。

原领取与结果保持不变，保存在承诺私有 `wake_reviews` 和整理进度私有 `reviews` 中。提醒核对
完成后，正常提醒／周期时间只推进一次；后续记忆整理保留之前的核对归档。普通 feed／sync／Mesh
不包含私有核对输入，不新增数据库或调度器。客户端页面、真实跨端验收和真实升级／回退仍待完成；
使用旧版前，按下文要求保留候选状态副本。

## 模型上下文与对话队列（R6.2 源码）

本机新对话历史从权威消息读取；聊天、帮手结果、检索和主动提醒不再另外维护一份 transcript。
`context.json` 只保存模型投递进度和上下文重置边界。客户端迁移期间保留旧 transcript
历史和 share/import 入口。聊天、结果回调与定时唤醒共用每个对话的一条有界队列。
旁聊提醒回到原对话；已经关闭或过期的唤醒不再启动模型调用。提醒可能已改变承诺时，
保留未确认的唤醒，不重复执行工具。决定迁移的剩余范围、准确部署源码身份和待验收项见
[源码交接](../docs/development/runtime-refactor-design.zh_CN.md#2026-10-24-核对与后续顺序)。

可读的留存对话按每 32 轮派生分段，段编号包含对话和权威正文版本；内容改变或重置后，
旧编号不能再取到那一段。`recall` 检索这份留存范围，包含兼容的最近 200 轮视图之外的内容。
分段不代表永久档案，也不是新增客户端分页接口；读取不另存历史文件，也不调用模型生成摘要。

接话引用沿用 8,000 字符预算，明确标注片段及覆盖范围。只有成功说话调用中实际完整引用的轮次
才记录投递回执；省略、截断、调用失败或仅做决定都不标为完整送达。仍遵守重置边界和旧历史导入
兼容规则，当前任务及承诺另从当前状态读取。

ContextBuilder 同时从这些版本化留存轮次、全局记忆日志和既有的对话内任务窗口组装 `recall` 证据。
重置时隐藏的消息也会从可识别的任务条目副本中排除，保留当前任务标题及全局记忆。
检索先选相关度最高的六条，再按时间展示；相关度相同时优先较新记录，摘录按不区分大小写的命中位置截取。
不增加模型调用、持久化索引或自动摘要。

`loop/context_window.py` 从同一份留存历史快照中选择并渲染近况引用和相关旧对话证据。
Speaker 把当前问题交给本地检索。最多两条相关轮次占用原字符预算的四分之一，近况保留
其余空间。每条引用都有版本化的分段／轮次出处。相关长文按命中位置摘录，不能获得完整
轮次的投递回执；只有成功说话调用中的完整引用才能获得回执。检索本身不写历史、记忆或
回执，继续遵守重置和对话边界。

词面检索只考察问题前 1,024 个字符，最多生成 64 个标识符／中文词项；出现在超过四分之一
留存轮次里的词项被忽略，避免反复引用套话。已引用／近期已交给模型的轮次不重复加入。
这是保守的证据选择，不是语义摘要，也不代表相关性完整；当前请求正文不会缩短，漏检时
仍可使用显式 `recall` 工具。上下文准备增加有界本地工作，不增加模型往返。

`loop/context_summaries.py` 为已结束的 32 轮历史段新增可丢弃、绑定版本的摘要缓存。
Pulse 沿用主对话的后台处理通道，在十分钟没有消息活动、没有待回复请求或待核对的整理操作时，
每拍最多尝试一段。调用配置的说话代理，使用隔离、无工具、最多十秒的模型调用；新消息会打断它。
输入使用完整原文，序列化 JSON 不超过 24,000 字符；超大段继续使用原文／recall，不截断后冒充完整摘要。
每个版本最多三次尝试，间隔至少十分钟；尝试次数先落盘，重启不重置。

有效摘要最多六个短要点，正文合计不超过 900 字符，每点引用该版本历史段中的一到四个轮次。
`context-summaries.json` 最多保存 64 项，与权威消息、记忆、重置边界和投递回执分开。
缓存损坏或写入失败不影响原历史可读。原记录修改、重置或不再可读时，对应摘要失效；
追加后续轮次不会使未变的已结束历史段失效。旧版 Runtime 忽略这个可选文件；
回退不需要删除或覆盖已接收消息或上下文重置数据。

Speaker 把缓存摘要作为本次调用中的引用背景提供，不写入原生的主人／助手消息历史，
避免每轮把同一份摘要再存成一条聊天消息。原文引用仍按正常规则记录投递回执。

聊天只读取已有且有效的摘要，最多两段，占用原有 8,000 字符窗口的四分之一以内。
当前任务／承诺事实和近期原文仍独立提供，相关原文引用及显式 recall 保留。
摘要明确标注为可能遗漏或误解的历史模型归纳，不是指令、授权、当前状态，也不算原文完整已读。
引用校验不能证明语义正确。通过现有 `recall(query)` 核对引用时，query 使用 `source:` 加 JSON 对象，
包含 `segment`、`turn`，可选非负整数 `offset`。返回最多 1,200 字符的原文，明确标注片段，
需要继续时提供精确的 `next` 查询。每页重新核对当前可读的段版本和对话；过期或格式错误的引用
明确返回不可用，不转去搜索另一份记录。缓存缺失时不调用模型补摘要，不以摘要替代历史档案，
不增加客户端接口或切换默认决定模式。自动生成目前沿用既有主对话整理范围；
旁聊缓存隔离已覆盖，但不包含旁聊的自动生成调度。

进展唤醒编号、原输入、领取、已生成回复、次数和冷却时间，现在保存在现有任务日志中。
原运行仍在执行时，通过其对话队列恢复待处理输入或补投已保存回复；重放本身不调用模型或工具。
中断的领取没有保存回复时，保留未确认。完整 Runtime 重启不会为了播报旧进展而复活中断的帮手。
换轮、负责人变化或任务结束后不能再发布过期通知；私有观察数据不进入客户端／Mesh 投影。

`loop/progress_wakes.py` 通过显式接口独立负责进展准入、领取／重试限额、已保存回复恢复和完成检查点。
Pulse 提供时钟、既有策略、负责人／模型／投递适配与共享队列；规则模块不导入 Pulse 或 Xiaoyu。
沿用现有任务日志、观察编号、提示词、时间策略和客户端格式。

`loop/timer_wakes.py` 独立管理到期提醒与记忆整理生命周期。提醒领取与核对历史仍在原承诺记录中；
整理模块管理既有进度文件、记忆版本检查、检查点与核对归档。Pulse 提供共享对话准入和模型／投递适配。
已保存输入恢复时不再重新做决定，已保存结果用于修复投递，未知领取保留人工核对，损坏的整理进度保持阻塞。
这次拆分不改变调度策略、工具权限、提示词、客户端接口或持久化格式。

后台唤醒在模型阶段、工具调用、原生会话／上下文回执以及提示／提醒／整理进度写入时，
持续核对原负责人代次。负责人变化、主人插话或 Pulse 关闭后，旧调用失效；同一 Runtime
以新代次重新负责也不能复活旧调用。完成后保留的旧工具回调不能再查询或修改状态，决定阶段
的写入回调在说话开始前关闭。失效前已经生效的操作仍保留，结果不明的提醒领取不清除、
不自动重放。沿用现有对话队列和存储，不改变客户端接口或持久化格式。

提醒与记忆整理的修改现在和主人请求共用 `Effects`：保存选定输入、比较并保存领取、执行，再保存
结果。私有明细留在既有承诺 `wake` 和整理进度文件中。未领取的输入恢复时不再问模型做决定；
已保存结果与回复补投到原对话，包括 `resolve` 已关闭原提醒的情况。`running/unknown` 和缺少
已保存输入的旧领取不自动重放。未确认的定时工作阻挡其原对话移交负责人；普通承诺裁剪保留这些记录。

按编号修改／删除记忆时，操作保存原记忆版本，实际修改在记忆锁内核对。记忆正文、历史来源、
提醒输入或负责人代次改变时先核对。外层提醒领取仍为旧版认识的 `unknown`；工具执行前，整理
进度已前移，因此旧版不会重新生成同一选定窗口。回退前保留状态目录副本：旧版继续做后续整理
可能覆盖进度扩展字段，返回候选版时应保留／恢复候选副本，不把旧整理写入合并进待处理工作。
宿主兼容探测只覆盖空闲回退后返回，不等于真实升级验收。消息核对接口只处理消息；保留原操作
明细的定时领取使用上面的独立入口。

## 请求操作持久化（R6 源码）

记忆／承诺工具和模型选定的任务动作，在宣布完成之前，先将决定、领取和结果保存到现有请求日志。
存储恢复／重启后，不再调用模型重做决定：未领取的输入继续执行，已保存的结果回到原对话。
结果不明的操作保留核对，阻止自动重做和负责人移交。工具执行后模型回复丢失，不会触发 JSON
退路或另一轮修改。CLI `--once` 沿用同一条 Service 路径；私有操作输入不进入客户端和 Mesh
投影。请求回复新增 `started` 元数据，表示是否曾把工作交给帮手。核对入口、剩余来源迁移和
仅源码的验收范围见[源码交接](../docs/development/runtime-refactor-design.zh_CN.md#2026-10-24-核对与后续顺序)。

## 当前请求快照（0.23.1）

接收结果及 `GET /v1/messages/<id>` 新增 `message_snapshot`，包含 `runtime_id`、
`subject_id`、`epoch` 及当前轮次的权威 `messages`。记录沿用 `/v1/sync` 的日志编号、
修订号和共用展示投影。按原有修订及终态规则合并，不能用这个子集推进历史同步游标。
这样历史仍在分页时，当前回复可以先显示。旧客户端可以忽略新增字段。

## 共用显示投影（0.23.0）

`presentation_v1` 扩展已有记录，不另建历史。Runtime 一次性确定作者名称、回合／内容块
身份、顺序、展示位置、整条消息边界、轻量格式片段及任务状态／分组。`/v1/sync`、消息
轮询／SSE、卡片条目及目录历史携带相同的新增 `presentation` 契约；`/v1/info` 和同步
声明 `presentation_policy`。旧 Runtime／记录仍可读取。规则首次可用时，客户端核对
并刷新已有缓存，保留历史、待处理请求及其身份。

Android 0.31.1 和桌面端 0.1.6 消费这些决定，不再各自拆分回复或解析其格式。
Python、Java 和 TypeScript 共用 `tests/fixtures/presentation-v1.json` 的固定样例。
原生 Codex／Claude 来源也能按相同投影读取，但尚未验证它们自己的窗口采用这套规则。
原生目录和规则明确将这项接入标为未完成。背景注入、恢复会话、协议方法存在，都不等于
原生窗口渲染验收。详见[展示契约](../docs/development/conversation-presentation.zh_CN.md)。

## 会话目录与公开审计（0.21.0）

记录中心维护 `state/conversations.json`，客户端不各自生成互相竞争的聊天编号或标题。
`side_chats`、`all_conversations` 和 `task_audit` 是 `/v1/info` 的增量能力。以下接口均
需要已有 Runtime 令牌：

- `GET /v1/conversations`：持久目录，包含稳定编号、标题、活动时间和可选的引用出处。
- `POST /v1/conversations`：`{id?: "side-...", from_message?: "message-id"}`。
  同编号重试返回同一聊天。引用必须对应保留的公开消息，不能制造主人新回合。
- `POST /v1/conversations/<id>/rename`：`{title}`。修订检查防止迟到的自动标题覆盖手动名称。
- `GET /v1/sync?conversation=default&all_conversations=1`：跨聊天任务、消息和目录，继续
  沿用游标、epoch 和分页。省略显式开关时仍只同步指定聊天。
- `POST /v1/messages` 和 `/v1/voice`：已有 `card`／`pin` 配合可选 `reply_scope=main`，
  补充仍出现在来源聊天，保持执行者及任务，不另建任务；服务核对来源聊天。
- `GET /v1/tasks/<card>/activity?before=<seq>`：最近 100 条保留的公开审计、续页游标及覆盖
  说明。`state/audit/` 的追加文件独立于临时的 2,000 条事件同步窗口；保存脱敏公开活动，
  不保存私有思考。

任务保留 `worker_session`、`worker_cwd`、`worker_agent`、`origin_message`、`relay_state`、
`supplements` 和 `last_meaningful_at`。收到或排队的补充不刷新执行者实质进展时间。
重启后等待转交的补充仍可查看，不自动重放。原生编号是关联信息，不代表所有平台均已
支持实时续聊。

可选的共享模型网关用途 `conversation.title` 仅在显式配置 `conversation.title` 或
`conversation` 路由时启用。它使用一个后台线程、有界待办和修订检查，不创建 agent 会话，
也不阻塞主人请求。没有路由时，共用的请求短标题可立即使用。目录元数据通过已认证 mesh
同步，不制造主人发言。缺父消息的远端结果保留真实署名，等父消息到达再关联。

## 需要什么

- Python 3.9 或更新版本。不需要第三方包，除非启用内置的语音识别引擎（见[语音](#语音)）。
  Pillow 可选，用于生成缩小的图片预览；没有它也能登记和下载附件。
- 每个登记的代理各自要用的东西：`claude_code` 类型需要同一台机器、同一个用户下装好并
  登录了 Claude Code（终端里能直接运行 `claude`）；`codex` 类型需要装好并登录了 Codex
  命令行。`--check` 会指出哪个代理的命令找不到。

## 快速开始

```bash
cd runtime
cp config.example.json config.json
python3 -c "import secrets; print(secrets.token_urlsafe(32))"   # 填进 server.token
python3 -m xiaoyu_runtime --config config.json --check
python3 -m xiaoyu_runtime --config config.json --once "你好"    # 只说一句，不启动服务
python3 -m xiaoyu_runtime --config config.json                 # 启动服务
```

`config.json`、`state/`、`workdir/` 已被 Git 忽略。不要提交真实的令牌。

想不调用任何模型先把整条链路跑通，启动时加上环境变量 `XIAOYU_DEFAULT_AGENT=echo`：
所有的话都交给一个原样复述的代理，不用改配置文件。

## 在桌面 App 中查看 Runtime 任务

在 `claude_code` 或 `codex` 代理上设置 `"desktop_open": true`，后台任务成功结束后，
Runtime 会请求原生桌面 App 打开原会话。排队的补充轮次会先做完；日常对话回复、失败或
取消的任务不会启动 App。默认是 `false`；打开失败会记在 Runtime 日志里，不丢弃任务
结果。启动命令成功只代表请求已发出，不代表已验证桌面显示。代理使用独立配置目录时，
只把这一条已完成会话的记录导入桌面端默认目录；不复制账号配置、凭证或其他对话，不改写
App 数据库。导入保留会话 ID，在完整写入后原子发布；重复导入相同内容没有副作用，已有
不同内容的会话绝不覆盖。这是本地快照，桌面端的后续操作不会回流到 Runtime 独立会话；
Runtime 之后继续该任务而内容不同时，会明确报告冲突，不静默合并。记录中引用的外部
工具产物和子代理文件不会导入。

已有任务可从仓库根目录操作：

```bash
PYTHONPATH=runtime python3 -m xiaoyu_runtime --config runtime/config.json desktop list
PYTHONPATH=runtime python3 -m xiaoyu_runtime --config runtime/config.json desktop open --task c149 --agent codex
```

使用配置中的代理名，例如 `cc`，不一定叫 `claude`。任务仍在工作、等待或状态未知时会拒绝
打开。这些命令只读取 Runtime 状态，不执行启动清理，也不调用模型。同一会话同时只交给
一方操作：在桌面 App 开始继续处理后，不要同时从 Runtime 给该会话派活。

- **Claude Code：**在代理原工作目录和 `CLAUDE_CONFIG_DIR` 下执行
  `claude --desktop --resume <session-id>`。需要安装受支持的 Claude Desktop、使用订阅
  登录，且 macOS 上的 Claude Code 至少为 2.1.285。Runtime 启动器目前只支持 macOS；
  Windows 请在交互式终端使用原生命令。明确配置了 API 或第三方服务的代理会被拒绝。
  启动器会移除继承的服务路由变量，使用本地订阅登录，不改变工作进程的环境。
  自定义配置目录的会话会先导入 `~/.claude/projects/` 再打开。
  macOS 使用临时终端，因为 CLI 不接受重定向输入输出；不会发送新提示词，启动限时
  15 秒。已在本机 macOS 验证。这是任务完成后的会话交接，
  不是实时镜像 `claude -p`。
  参见 [Claude 桌面文档](https://code.claude.com/docs/en/desktop#coming-from-the-cli)。
- **Codex：**在 macOS 打开 `codex://threads/<session-id>`。独立 `CODEX_HOME` 中的
  会话记录会按原日期目录导入 `~/.codex/sessions/`。桌面端继续操作时使用自己的账号和
  设置；Runtime 的配置和登录保持原样。桌面端的来源筛选仍可能隐藏外部会话，按 ID
  打开不依赖侧边栏发现。导入和读取成功不等于已验证实时显示。

若桌面客户端确实使用了同一个 Codex 服务，还可设置
`"app_server_socket": "/absolute/path/to/app-server-control.sock"`，并保留
`"mode": "app_server"`。Runtime 会使用 `codex app-server proxy --sock <path>`；
服务必须已运行，桌面端必须使用同一服务和配置目录。不会自动发现 socket；单独启动一个
daemon 也不等于桌面 App 已连接。服务缺失或不兼容会明确失败，不会悄悄改为私有进程。
只关闭 Runtime 自己的代理进程；取消时向当前任务的轮次发送中断，并忽略其他线程的事件。
这个可选连接保留 Runtime 的授权和中途追加协议，但共享服务本身不保证桌面 App 实时显示。
参见 [App Server 协议](https://learn.chatgpt.com/docs/app-server)。

改配置后，须等没有任务运行时再重启 Runtime；要打断任务须先取得主人批准。这个功能与
下面的只读观察是两个方向。

## 同步已有电脑端对话

启动 Runtime 时加上 `--desktop-sync`，就可以显示你正在 Codex 桌面 App 或 Claude Code
里使用的对话，也包括在本机记录的 Claude Desktop 会话。可见对话内容和状态会加入
手机 App 的卡片，并可通过现有的 App/蓝牙连接转发给 Passport：

```bash
cd runtime
python3 -m xiaoyu_runtime --config config.json --desktop-sync
```

想不管怎么拉起 Runtime（守护脚本、开机自启、服务管理器）都开着，就写在配置里（0.10.0 起）：
`"desktop_sync": true`，或者 `"desktop_sync": {"enabled": true, "projects": [...], "codex_dir": "...",
"claude_dir": "..."}`。命令行写了的优先。

观察器每两秒检查变化，每十秒发现新记录文件。它读取 `~/.codex/sessions` 和
`~/.claude/projects` 下最近 24 小时发生变化的最新 32 个本机会话记录文件；子代理和
已经由这台 Runtime 管理的会话会被排除。要只观察指定项目，可以重复添加
`--desktop-project`；另外两项指定代理的配置目录，目录下应包含 `sessions/` 或 `projects/`：

```bash
python3 -m xiaoyu_runtime --config config.json --desktop-sync \
  --desktop-project /absolute/project-a --desktop-project /absolute/project-b \
  --desktop-codex-dir /absolute/codex-config \
  --desktop-claude-dir /absolute/claude-config
```

所有已集成代理统一使用 `card.title` 作为任务名称：优先采用电脑侧栏原题，其次是明确的
本地任务名称，再其次是真实需求的短名称，最后是明确的未命名会话／任务提示。
0.21.1 起，电脑观察会话采用首条可读主人需求作为回退主题，后续补充随内容窗口轮换时
标题保持稳定；原生侧栏改名仍优先。注入的任务通知不算主人需求。缺少标题时，不再
把目录和编号拼成任务名；项目、原生会话编号和可获得的执行目录作为独立元数据保留，
目录元数据重启后仍在。旧技术标题的修复不改变原观察时间、状态或消息。
结果摘要或代理回复不会替换任务名称。侧栏原题和明确的任务名称保留原来的措辞及内部
空格，只遮盖可识别的凭证，并限制为 200 字符；从需求生成的名称最多 24 字符。
`title_source` 为 `upstream`、`task`、`request` 或 `fallback`，`title_truncated`
标明名称是否因长度上限被截短。

Codex 标题适配器以只读方式读取本机 SQLite 状态中的 `threads.name`，包括尚在 WAL
中的实时修改；不会把 `threads.title` 中的最初提示词当成侧栏名称。已识别的会话索引和
旧版侧栏名称元数据作为回退。Claude 适配器先读取明确的 `custom-title` 记录，再读取
会话索引中的显示名称。即使没有新的对话事件，名称也会刷新，因此电脑端改名会更新同一
张卡。目前自动观察本机会话的采集器只覆盖 Codex 和 Claude。其他代理集成同样遵循统一
标题规则，但要显示侧栏原题，需要适配器提供上游名称；没有时使用相同的回退规则。

App 的任务列表和详情使用同一个 `card.title`。硬件任务/历史列表也使用这个名称，按
现有 47 个 UTF-8 字节的上限截断，不额外添加项目或“电脑”前缀；来源、项目和状态各有
自己的字段。

观察器同步你实际提出的需求、可见的代理回复和进展说明，以及项目/会话元数据、模型标识、
时间、状态和工具名称。系统/开发者指令、AGENTS 指令、注入的环境/浏览器上下文、推理、
原始工具参数/结果和原始记录不会同步。可识别的凭证格式会在保存和发送前遮盖，但不保证
能识别任意形式的私人文字。

每张卡保存最近片段，不是完整记录：最多 20 条，每条最多 4,000 字符，每张卡的 UTF-8
文本最多 32 KiB。首次发现会话时，最多读取记录末尾的 2 MiB。卡片带有
`content_scope: "recent"`；`content_truncated: true` 表示较早或较长的内容已省略。
手机显示保留下来的片段，分别标注“你”、Codex、Claude。硬件通过现有的 `card` 消息显示
最近需求和回复：`said` 最多 159 个 UTF-8 字节，App 的电脑会话 `reply` 最多 480 字节，
仍使用设备所有卡共用的 4 KiB 文本池。已经支持电脑卡片的固件不需要为内容同步重新刷写。

电脑端会话停下（本轮结束）以后，可以从手机在会话页接着聊（0.8.1 起）。0.10.1 起，在会话页说的话先给
小鱼：她能看到这个会话最近的内容，解释、翻译、总结这类问题自己答，回答另记一张卡。只有主人点名能接这个
会话的代理（请求里的 `agent`，或者话里说出它的名字），或者小鱼判断主人要它接着干活（她对这张卡用
`amend`）时，才接着那个会话做：Runtime 另开一个「接着：……」
任务，在那个会话的目录里用它的会话编号跑一轮（Claude 会话交给 `claude_code` 类型的代理，Codex 会话
交给 `codex` 类型的），不加小鱼的人设。这个会话不记成 Runtime 自己的，电脑端照常观察，主人也能回到
电脑上接着聊。电脑上这一轮还在跑（`running`/`waiting` 且不是 `stale`）时不接着做，但可以问小鱼。停止仍在电脑端完成。安装并信任下面的可选
审批桥后，可以从手机单独回答有效的操作审批；只有状态信号、或者没有决策通道的通知，
仍需要在电脑端确认。
“本轮结束”只表示这次响应结束，不代表整个项目
已经完成。正在活动的会话连续五分钟没有来源事件，或记录文件变得不可用时，会显示
`unknown`（状态未知）。

记录文件的格式可能随代理版本改变，观察器需要适配；仅凭日志不能可靠识别所有等待确认
的情况。这个功能观察本机记录，没有本机会话记录的纯云端对话不保证能显示。

### 电脑端共享历史

共享历史读取器让 Codex 和 Claude Code 接上 Runtime 的工作，避免主人重复交代。
它读取 Runtime 任务卡／对话及两个 Agent 的本地可见消息，也包括 Runtime 配置中
各自独立的 `config_dir`。统一结构保存来源、发言者、任务／卡片／会话编号、原始
消息时间、状态、可见文字、附件，以及内容是否为近期片段。其他客户端可以直接消费
JSON 输出，不依赖某个 Agent 的原始记录格式。
0.9.1 起，Runtime 对话和给模型的上下文一样读：只存消息编号的一轮从消息日志
（`state/cards.log.jsonl`）取文字，日志里已经没有的那轮不再拿出来，已经在任务卡上
出现的一轮不重复给。

在仓库根目录查询保留的历史，不调用模型：

```bash
python3 runtime/xiaoyu_runtime/shared_history.py --context
python3 runtime/xiaoyu_runtime/shared_history.py --task c127
python3 runtime/xiaoyu_runtime/shared_history.py --format json
python3 runtime/xiaoyu_runtime/shared_history.py --export runtime/state/history-context/shared-history.md
```

其他 Runtime／项目使用 `--config /absolute/config.json` 和
`--project /absolute/project`；可重复指定 `--codex-dir` 或 `--claude-dir`
接入额外的本地配置目录。读取器不会启动／恢复帮手，不改 Agent 的对话记录或数据库，
也不会把历史中的授权当成新的审批决定。仓库 `AGENTS.md` 的接话规则使用同一入口，
`CLAUDE.md` 也引用该规则。

可选上下文 Hook 在 `SessionStart`（包括恢复会话和压缩）及 `UserPromptSubmit`
时自动投递。首次事件给出有长度上限的简报，之后只补有变化的任务／对话。当前电脑
会话和 Runtime 已管理的帮手记录不重复采集；再次读取桌面记录时过滤已注入的历史。
每个 Agent／会话有独立、私有的指纹游标。两边都使用官方
`hookSpecificOutput.additionalContext` 输出协议；不阻止提问、不批准操作，失败
时把控制权交回电脑端。

预览并安装两边的上下文 Hook，保留现有设置和审批／状态 Hook：

```bash
python3 tools/setup-history-sync.py            # 预览目标文件
python3 tools/setup-history-sync.py --install  # 合并配置，保留私有备份
```

这会为当前项目更新 `~/.codex/hooks.json` 和 `~/.claude/settings.json`。
Codex 需要通过 `/hooks` 审阅并信任更新后的定义；Claude Code 需要在新建／恢复会话
时加载新设置。Hook 刷新 `state/history-context/shared-history.md`，可以在电脑端
文件面板查看。上下文投递与原生聊天气泡是两种功能：两个适配器都不会向对方 App
伪造对话轮次。协议见官方 [Codex Hook](https://learn.chatgpt.com/docs/hooks) 和
[Claude Code Hook](https://code.claude.com/docs/en/hooks) 文档。

这是保留的可见历史视图，不是无限归档。Runtime 卡片／对话的保留上限仍适用；
每个 Agent 配置目录选取最新的 32 个项目记录文件，使用已有观察器的有界近期内容
适配器，已归档、仅云端或已删除的旧对话可能不可用。消息保留原始时间，重新导入
不会把旧任务算成新任务。`done` 只表示本轮结束，不代表部署成功或设备验收通过。
可识别的凭证会脱敏，但普通个人内容仍属于本地私有记录。生成的视图和投递游标放在
Git 忽略的 `state/`，不进入仓库。

### 用可选生命周期 Hook 同步等待确认

可选 Hook 接收器把状态快照写入正在运行的 Runtime 所用的同一个 `state_dir`。
生命周期快照只包含元数据和状态，不保存需求、回复、工具参数原文或工具结果。
操作审批和权限通知还会创建私有审批记录，保存请求的操作描述及其指纹；描述中可识别的
凭据会先脱敏。接收器从标准输入读取一个有大小上限的 JSON 对象。只同步状态的事件
不向标准输出打印内容。
在 `runtime/` 目录下，接收器的命令是：

```bash
python3 -m xiaoyu_runtime.desktop_hook --state-dir /absolute/runtime-state --agent codex
```

要让 Hook 从任意项目启动，可以复用这个命令处理器。把两个绝对路径换成实际值；
`runtime-state` 必须是 Runtime 解析后的 `state_dir`：

```json
{
  "type": "command",
  "command": "PYTHONPATH=\"/absolute/ai-passport/runtime\" python3 -m xiaoyu_runtime.desktop_hook --state-dir \"/absolute/runtime-state\" --agent codex",
  "timeout": 2
}
```

把处理器放进每个事件的匹配组中的 `hooks` 列表，例如
`"UserPromptSubmit": [{"hooks": [handler]}]`，其中 `handler` 指上面的 JSON 对象。
Claude Code 复用同一个处理器，把参数改成 `--agent claude`。接收器的事件映射如下：

| 事件 | 记录状态 | 代理 |
| --- | --- | --- |
| `SessionStart`、`SessionEnd` | `unknown` | 两者 |
| `UserPromptSubmit`、`PostToolUse` | `running` | 两者 |
| `PermissionRequest` | `waiting` | 两者 |
| `Stop` | `idle`（本次响应结束） | 两者 |
| `Interrupt` | `interrupted` | Codex |
| `StopFailure` | `failed` | Claude Code |
| 类型为 `permission_prompt` 的 `Notification` | `waiting` | Claude Code |

Claude Code 的权限通知还覆盖一部分没有 `PermissionRequest` 事件的提示。
可以添加下面的匹配组：

```json
{
  "hooks": {
    "Notification": [{
      "matcher": "permission_prompt",
      "hooks": [{
        "type": "command",
        "command": "PYTHONPATH=\"/absolute/ai-passport/runtime\" python3 -m xiaoyu_runtime.desktop_hook --state-dir \"/absolute/runtime-state\" --agent claude",
        "timeout": 2
      }]
    }]
  }
}
```

把这些匹配组合并到已有 Hook 配置里，保留原来的处理器。Codex 会发现
`~/.codex/hooks.json` 或项目的 `.codex/hooks.json`，但执行前需要通过 `/hooks` 审阅并
信任当前准确的 Hook 定义。Claude Code 的 Hook 放在它的设置文件里。
配置位置和生命周期行为见官方 [Codex Hook 文档](https://learn.chatgpt.com/docs/hooks)
和 [Claude Code Hook 文档](https://code.claude.com/docs/en/hooks)。Runtime 不会自动安装或
修改全局 Hook 配置。构建和主机测试通过不代表已经验证手机接收、蓝牙转发和硬件显示；
这条链路需要用已连接的 App 和设备实测。

### 可选电脑端审批桥

对同步执行的 `PermissionRequest` Hook，在接收器命令后添加 `--approval-bridge` 和
`--approval-timeout 300`。它最多等待 300 秒（可配置，上限 600 秒），收到一次
`allow` 或 `deny` 后，只输出一次官方 `hookSpecificOutput` 决策，仅回答这一步操作。
超时、Runtime 不可用、会话关闭或 Hook 租约失效时，不输出决策，交回电脑端正常的
权限询问流程；没有回答不会自动放行。

用下面的命令为每个代理生成可合并的配置，避免手动算错超时：

```bash
python3 -m xiaoyu_runtime.desktop_hook --state-dir /absolute/runtime-state \
  --agent codex --approval-timeout 300 --print-config
python3 -m xiaoyu_runtime.desktop_hook --state-dir /absolute/runtime-state \
  --agent claude --approval-timeout 300 --print-config
```

生成器只打印 JSON，不安装配置。把输出合并到代理的 Hook 配置中，保留已有处理器；
Hook 外层超时必须长于审批等待时间（生成值为 310 秒）。Runtime 要带 `--desktop-sync`
启动，并使用相同的、解析后的 `state_dir`。Codex 需要通过 `/hooks` 审阅并信任实际安装的
准确配置；Claude Code 要让相应会话加载这份设置。源码存在或测试通过，都不代表已有
电脑端会话的 Hook 已经启用。

请求放在私有的 `state_dir/desktop-approvals/` 暂存目录。Runtime 接受答案前会检查
所属 Hook 的心跳、会话/轮次/工具身份、操作指纹和有效期限；重复或过期答案会被拒绝。
操作描述过长、不支持的 Hook 通道，以及 Claude 的 `permission_prompt` 通知都只能提醒：
App 提示到电脑端确认，不提供远程“可以/不行”按钮。

电脑端审批带有 `source: "desktop"`、`session_id`、`project`、`request_id`、
`remote_actionable` 和 `expires_at`，并可带轮次、子代理标识。`/v1/feed` 跨对话返回这些
审批，手机切换聊天不会把电脑请求藏起来。即使独立审批仍有效并带有
`remote_actionable: true`，电脑端会话卡本身也仍然只读。
手机需要 Android 通知权限才能在后台提醒；没有该权限，审批仍可在 App 内查看。

## 先决定再说话（0.19.0，需主动开启）

`xiaoyu.decide_first` 默认是 `false`。使用流式 DeepSeek 作为小鱼的声音时，
开启后先用非流式请求选择至多一个承诺，检查并实际落实，再生成可见回复。
说话阶段只能查看任务详情或翻旧记录。记忆、约定和主动唤醒也采用同样的分离；
只输出 JSON 的代理及结果转述沿用原有路径。

决定阶段时限是 1.3 秒。超时就带着“本轮尚未落实承诺”的真实事实开始说话；
迟到的决定直接丢弃，不执行工具。拒绝原因作为事实传给说话阶段。说话失败时，
不会重做已经落实的操作。两阶段共用小鱼单次调用的总时限。工具结果作为协议
消息保留，不新增隐藏提醒提示。诊断只包含轮次编号、决定状态和耗时、服务端
首段正文、总耗时及“像承诺却未承诺”的计数，不包含消息原文；服务端首段正文
耗时不等于客户端实际显示的 TTFT。

在完成[切换方案](../docs/development/decide-then-speak.zh_CN.md)要求的 Mac
基线与真实 App／Runtime／设备计时、恢复验证前，保持关闭。Windows 单元测试
不能证明第一条完整气泡在三秒内出现。开关关闭时保留原提醒通道。

## 记忆、翻旧账、答应的事、主动说话、插话（0.18.0）

参考 Muse 的记忆和投递做法，这一版把“记住”“主动说”做成 Runtime 里的机制，模型负责判断：

- **记忆有出处和历史**：`state/memory/owner.md` 每条带类型（偏好、规矩、事实、承诺）、日期和主人原话；
  改口用 `replace`（新的取代旧的），不要了用 `forget`。每次记、改、删都追加到
  `state/memory/log/日期.md`，现行口径唯一，旧说法留在日志里。像密码、密钥、验证码的内容拒收。
  提示里写明：记忆是以前的情况，会变的事先核实；记得同意过某事不等于这次可以不问就做。
- **翻旧账 `recall`**：在聊过的话、记忆日志、最近的事和帮手结果里找。用 Python 自带 SQLite 的全文检索
  （trigram，中文不用分词器）；SQLite 太旧或词不到三个字时逐条找。以后换向量检索只改 `recall.py`。
- **答应的事 `track` / `resolve`**：不是交给帮手的那种（“晚上提醒我”“等 Mac 上线再发”），带到点时间，
  可以每天、每周重复；存在 `state/memory/promises.json`，每一轮放进提示。说了“到时候提醒你”却没调
  `track`，跟说了不做一样会被提醒。问句（问号结尾）不算答应。
- **投递闸（`proactive.py`）**：替代以前写死的“还在弄”。后台每 20 秒看一眼：帮手做了 90 秒以上、又有新
  进展（同一件事至少隔 4 分钟、最多 3 次），或者答应的事到点了，就叫醒她；她看到最近跟主人说过的话、
  记忆和答应的事，自己决定说不说，不说只回“（不说）”。
- **后台整理记忆**：对话安静 10 分钟、又有新的几轮时，让她只用记忆工具把漏记的偏好和规矩补上。
  第一次从当时最新的一轮算起，不翻以前所有的对话。
- **帮手也看到主人的偏好**：交给帮手的话后面附上记忆里的偏好和规矩。
- **被提醒后不重复**（0.18.1）：说了不做被提醒以后，她补调了工具就到此为止，回应提醒的话和再说一遍的话都不给主人。
- **交出去只说一次**（0.18.2）：她已经开口说了、接着把事交给帮手，这一轮就到此为止；不再拿工具结果问她一轮
  （那一轮多半把同一句话换个说法再说一遍：“好，我让 X 去…”后面又跟一条“好嘞，已经让 X 去…”）。
  交之前一句没说的，照旧再问一轮，让她说一句交给了谁。
- **插话**：她回话途中主人又说了一句，正在出的那段马上停下（说出的部分留着，记成被打断），
  先接主人的新话。转述帮手结果的那一轮不打断。需要 App 0.25.0（回话中也能发送）。0.23.1
  源码在 `10364f2` 之后，被打断的回复仍是 `status: final`，同时在同步消息、轮询／SSE 请求记录和
  `presentation` 上带 `interrupted: true`（`status_label` 为“已打断”）。只有说话的代理确实因此提前
  停下才标记；回复说完后才到的消息不算。旧客户端忽略这个字段，照常显示文字。

## 小鱼会记事（0.17.0）

小鱼有了记忆：`state/memory/owner.md`，一行一条，带记下的日期，每一轮都放进她的提示（转述帮手结果时
也看得到）。她用 `remember` 记、`forget` 按编号删；主人也可以直接打开这个文件改。只记偏好、规矩、要她
以后一直照做的话，不记流水账；最多 60 条，满了丢最旧的。

- 她说了“记下了”“以后按这个来”却没调 `remember`，跟“说了去做却没调工具”一样，Runtime 悄悄提醒她一次。（这种暗中追问已于 2026-10-23 删除，现在只记数，见代码清理方案第 1 批。）
- 去掉了要她“写出判断理由”（这句话含糊在哪、想到哪几种理解……）的那一段，换成一句：有把握就按理解办，
  没把握就简单问一句。之前回复总是先复述主人的意思、列几种可能让主人选，主要来自那一段。

## 说话交给她自己判断（0.16.0）

这一版取代 0.14.0 的“短消息、按长度拆”规则。Runtime 只告诉小鱼现在在哪说话（手机聊天，主人随时
可能插话，能显示加粗和列表），说几条、多长、什么语气由她定；只留三条底线：没调工具不说“已经去做了”，
不编进度和结果，说过的不换个说法再说。

- **分条由她定**：她单独写一行 `---` 的地方另起一条消息；Runtime 不再按长度切。带工具的一轮里，
  调工具前说的和看到结果后说的也是两条（不同时刻）。
- **轻量格式**：给手机的回复留着加粗和列表（`clean_chat`）；设备小屏的 brief 仍是纯文字。
- **说了要做却没调工具**：带工具说话改用单独的会话（`apt-` 开头），不接写 JSON 时期的旧会话，第一次
  把最近 8 轮当前情带进去；会话里连工具调用和结果一起记下。一轮里一个工具都没调、却说了“我这就去”
  “交给某某”这类话时，Runtime 悄悄提醒她一次：现在就调，或者改口。（已于 2026-10-23 删除：现在只调用一次模型、只给出一段回复，这种情况只记数。）

## 小鱼用工具做事（0.15.0）

说话的代理能用工具调用时（现在是 dsh 直连 DeepSeek 的那条路，`talks_with_tools()`），小鱼不再
写 JSON：她直接写的文字就是说给你的话，空一行是下一条消息；要做事调工具。工具定义在
`voice_tools.py`，由 Runtime 自己执行，跟哪家模型无关：

- `start_task`：交给一个帮手（帮手名、标题、交代的话，可以接着一件已有的事）。
- `amend_task`：对一件事补充或改要求（redo / after）。
- `cancel_task`：停掉一件事。
- `task_detail`：看一件事现在怎么样（状态、最近几步、最后一条话）。查过哪件，这句话就归到哪件。

做事的工具一调用就按原来的规则检查（帮手在不在、编号对不对……），办不了的当场把原因还给她，
她在同一轮里改，不用整轮重问。办得了的先记下，这一轮说完以后照原来的流程开卡、启动帮手。
一轮只办一件事，最多来回 4 次。转述帮手结果也是直接写话。会话里留着的旧 JSON 回复会按文字
交给她，免得她照着学。这条路出错（接口出错、超时）时退回写 JSON 的老路；不能用工具的代理
（比如让 Claude Code 当说话的）照旧写 JSON。

## 小鱼怎么说话（0.14.0）

小鱼像聊天那样回话：一次 1 到 4 条短消息，每条一两句，条与条之间空一行；第一条就是结论。
不用标题、编号、加粗、代码块，不贴文件路径、行号和命令，你要了再给。这些写在 Runtime 给
说话代理的提示里（`CHAT_STYLE`），换哪家模型都一样；模型还是写了一长段时，Runtime 自己在
句号处拆开（`segments`：每条约 60 字，一轮最多 6 条，列表留在同一条里），再交给手机。

- **转述帮手的结果**：第一条说结论，第二条说要你拿主意的事，需要时说完整的在哪；不复述过程、
  文件名和代码位置。模型在 JSON 前后多写了话、或者包进 ``` 里，也能认出 reply，不会把整段 JSON
  给你看；实在认不出时用写了一半的 reply，最后才用原文。
- **没有能说话的代理时**：不再把帮手原文整段贴进聊天，只给第一句和“完整结果在「…」里”，原文
  留在任务里。
- **帮手做得久时跟进**：做了 90 秒以后，小鱼在聊天里说一句它到哪了，之后至少隔 4 分钟，一件事
  最多 3 句。这句话用帮手报的最近一步拼出来，不再问一次模型；进展里是文件名、命令时只说做了多久。

## 配置

相对路径都相对于配置文件所在的目录。

| 配置项 | 默认值 | 含义 |
| --- | --- | --- |
| `server.host` | `127.0.0.1` | 监听地址。写 `tailscale`（0.5.6 起）表示只在这台机器的 Tailscale 地址（100.x.y.z）上监听：启动时自己找，地址变了不用改配置，`--pair` 打印的连接串也用它；Tailscale 没开时启动会报错。这时局域网地址连不上这台 Runtime；它同时在 `127.0.0.1`（同一端口）上监听，给 Tailscale Funnel 这类本机转发用，别的机器连不到。 |
| `server.port` | `8765` | 监听端口。 |
| `server.name` | 这台机器的主机名 | 手机 App 里怎么称呼这台 Runtime，最多 40 个字符。 |
| `server.token` | 无 | 共享令牌，至少 16 个字符；示例里的占位值会被拒绝。 |
| `state_dir` | `state` | 存会话表、卡和小鱼的对话记录的目录。 |
| `persona_file` | `persona.txt` | 小鱼的人设。只交给以小鱼的身份回话的代理。 |
| `brief_max_chars` | `120` | 小屏幕简报的字数上限（20 到 400）。 |
| `turn_timeout_seconds` | `0` | 一件后台的事从头到尾最长做多久，`0` 是不限（0.5.4 起的默认；之前是 `3600`）。代理没有自己设 `timeout_seconds` 时用它（`0`，或 10 到 86400）。卡没卡住看的是多久没动静，见[长任务](#长任务看有没有动静不看总时长)。 |
| `idle_timeout_seconds` | `600` | 后台的事连续这么久一行事件都没有，就去问主人还等不等（`0` 是不管，或 30 到 86400）。 |
| `idle_timeout_command_seconds` | `1800` | 同上，但用在有操作还没跑完的时候（编译、跑测试时事件流本来就是安静的）。不能比上一项小。 |
| `idle_answer_seconds` | `1800` | 问了“还等不等”之后这么久没人答，就停掉（`0` 是一直等，或 30 到 86400）。 |
| `agents.<名字>` | 无 | 一个代理，见[代理](#代理)。至少要有一个启用的。 |
| `xiaoyu.default_agent` | 第一个代理 | 没人点名、路由器也没有意见时，话交给谁。 |
| `xiaoyu.voice_agent` | 默认代理；它只干活时取第一个能说话的 | 只干活的代理做完之后，由谁用小鱼的口吻转述。 |
| `xiaoyu.max_parallel` | `0` | 同时在做的事最多几件（0 到 64）；`0` 是不限。超过的排队。 |
| `xiaoyu.voice_timeout_seconds` | `60` | 小鱼自己接一句话最长多久（5 到 600）。这一步不给工具，只是听懂和分派。 |
| `xiaoyu.decide_first` | `false` | 流式 DeepSeek 的先决定再说话通道，默认关闭，实测验收后再推广。 |
| `xiaoyu.max_handoffs` | `2` | 0.4 的设置，现在不起作用；留着只为旧配置能读。 |
| `xiaoyu.router.type` | `mention` | `mention` 或 `command`，见[这句话交给谁](#这句话交给谁)。 |
| `xiaoyu.router.command` | `[]` | `command`：用来判断的命令。 |
| `xiaoyu.router.timeout_seconds` | `10` | `command`：超过这么久就不等了，交给默认代理（1 到 120）。 |
| `stt.engine` | `none` | 语音识别：`none`、`sense_voice` 或 `command`。见[语音](#语音)。 |
| `stt.model_dir` | 无 | `sense_voice`：放 `model.int8.onnx`（或 `model.onnx`）和 `tokens.txt` 的目录。 |
| `stt.language` | `auto` | `sense_voice`：`auto`、`zh`、`en`、`ja`、`ko` 或 `yue`。 |
| `stt.threads` | `2` | `sense_voice`：使用的处理器线程数（1 到 16）。 |
| `stt.command` | `[]` | `command`：要运行的命令，其中一个参数要包含 `{audio}`。 |
| `stt.timeout_seconds` | `60` | `command`：一次识别超过这么久就停止（5 到 600）。 |
| `firmware.repo` | 无 | 从哪个 GitHub 仓库取构建好的设备固件，写成 `用户名/仓库名`。见[设备固件](#设备固件)。 |
| `firmware.asset` | `FoloToy-AI-Passport-full.bin` | 发布里固件文件的名字。 |
| `firmware.tag_prefix` | `firmware-build-` | 带固件的发布，标签以它开头。 |
| `firmware.source_dir` | 无 | 固件源码在这台电脑的哪里；`fetch --commit HEAD` 和 `build` 用。 |
| `firmware.build_command` | `[]` | 这台电脑自己构建固件的命令，在 `source_dir` 里运行。空着表示不会自己构建。 |
| `firmware.build_output` | `build/FoloToy-AI-Passport.bin` | 构建出的固件文件，相对于 `source_dir`。 |
| `workspace.type` | `none` | `none` 或 `notion`。见[共享工作区](#共享工作区)。 |
| `workspace.token` | 无 | Notion 集成的令牌；也可以用环境变量 `XIAOYU_NOTION_TOKEN`。 |
| `workspace.token_file` | 无 | 令牌放在这个文件里（相对路径以配置文件所在的目录为准），启动时读一次；和 `workspace.token` 只能留一个。令牌不想写进 `config.json` 时用它。 |
| `workspace.bus_database` | 无 | “总线”数据库的编号（链接里那 32 位）。`notion` 时必填。 |
| `workspace.log_database` | 无 | “日志”数据库的编号。不填就只写总线。 |
| `workspace.author` | `xiaoyu` | 小鱼在共享工作区里署的名字。 |
| `usage.enabled` | `true` | 要不要主动去问订阅额度（0.6.0 起）。`false` 时不为这件事另起进程，帮手干活时自己报上来的照记。见[用量](#用量订阅额度还剩多少)。 |
| `usage.refresh_seconds` | `300` | 有客户端在看的时候，隔多久去问一次（60 到 86400）。 |

环境变量优先于配置文件，这样放进容器或虚拟机时不用改文件：`XIAOYU_CONFIG`、
`XIAOYU_NAME`、`XIAOYU_HOST`、`XIAOYU_PORT`、`XIAOYU_TOKEN`、`XIAOYU_STATE_DIR`、
`XIAOYU_DEFAULT_AGENT`、`XIAOYU_CODEX_CONFIG_DIR`（作用于所有 `codex` 类型的代理）、
`XIAOYU_CLAUDE_CONFIG_DIR`（作用于所有 `claude_code` 类型的代理）、`XIAOYU_DSH_CONFIG_DIR`
（作用于所有 `dsh` 类型的代理）、`XIAOYU_NOTION_TOKEN`。

设了 `XIAOYU_DEBUG`（任意值）时，小鱼每接一句话，标准错误里多一行：她把这句话归到了
哪张卡、要 Runtime 做什么。

0.3 及更早的配置（`backend`、`claude_code`、`tools`）仍然能读：按只有一个名叫 `claude`
的代理处理，启动时会提示这是旧写法，原来的会话接着用。`tools` 里启用过的条目需要手动
改成 `agents` 里的一个代理。

## 代理

`agents` 是一个对象，键是代理的名字（字母、数字、下划线和连字符，最多 24 个字符）。
每个代理都有这几项：

| 配置项 | 默认值 | 含义 |
| --- | --- | --- |
| `type` | 无 | `claude_code`、`codex`、`dsh`、`command`、`remote` 或 `echo`。 |
| `enabled` | `true` | `false` 时当它不存在。 |
| `description` | 空 | 一句话说明它擅长什么。小鱼决定要不要把活交给它、路由器做判断，看的都是这句话。 |
| `aliases` | `[]` | 点名时除了名字之外还认的叫法，比如语音识别常写成的中文名。 |
| `speaks` | 随类型 | 能不能直接以小鱼的身份回话。`claude_code`、`dsh`、`remote`、`echo` 默认能；`codex`、`command` 默认只干活。 |
| `timeout_seconds` | `turn_timeout_seconds` | 这个代理做一件事从头到尾最多跑多久，`0` 是不限。看不到事件流的跑法（`command`、`codex` 的 `exec` 模式）写了 `0` 时按 3600 秒算。 |
| `idle_timeout_seconds`、`idle_timeout_command_seconds` | 顶层的同名项 | 这个代理多久没动静算可能卡住。只对看得到事件流的跑法有用：`claude_code`、`dsh`，和 `codex` 的 `app_server` 模式。 |

各类型另外认的配置项：

| 类型 | 做什么 | 配置项 |
| --- | --- | --- |
| `claude_code` | 非交互地运行 Claude Code 命令行，用这台机器上已经登录的账号。 | `command`（默认 `["claude"]`）、`workdir`（默认 `workdir`）、`config_dir`、`model`、`permission_mode`（默认 `manual`）、`allowed_tools`、`add_dirs`（默认 `["~"]`）、`extra_args`、`env`、`env_files`、`models`、`effort`、`efforts`（见[档位](#档位模型和努力程度)） |
| `codex` | 运行 Codex 命令行。默认用它的 app-server 模式（`codex app-server`）：需要确认的操作会来问主人，做的过程中可以追加一句话。`mode` 设成 `exec` 时用 `codex exec`（接着聊用 `codex exec resume`）：一次性跑完，不会来问。 | `command`（默认 `["codex"]`）、`workdir`、`config_dir`、`mode`（默认 `app_server`）、`approval_policy`（默认 `on-request`）、`sandbox`（默认 `read-only`）、`model`、`extra_args`、`models`、`effort`、`efforts` |
| `dsh` | 运行 DeepSeek Harness 命令行（`dsh`），见下面的 [DeepSeek Harness](#deepseek-harnessdsh)。 | `command`（默认 `["dsh"]`）、`workdir`、`config_dir`、`sandbox`（默认 `workspace-write`）、`stream`（默认 `true`）、`model`、`extra_args`、`env`、`env_files`、`models`、`effort`、`efforts` |
| `pi` | 每一轮运行一次 pi 编程助手（`pi -p`），会话编号由 Runtime 记着，下一轮接着聊。用 API 的模型，Key 放在 `env` / `env_files` 里，变量名用服务商自己的（比如 `OPENAI_API_KEY`）；订阅登录在 pi 里用 `/login`。pi 没有沙箱和审批：用 `extra_args` 里的 `--tools` 限定它能用的工具。 | `command`（默认 `["pi"]`）、`workdir`、`model`（`服务商/编号`）、`extra_args`、`env`、`env_files`、`skills`、`models`、`effort`、`efforts`（pi 的 `--thinking` 档位） |
| `command` | 任意命令。交给它的话从标准输入送进去，标准输出就是结果；参数里写了 `{prompt}` 时改为替换进参数。没有会话，每次从头开始。 | `command`、`workdir` |
| `remote` | 另一台电脑上的小鱼 Runtime。那边有自己的人设、代理和会话，回来的已经是小鱼的话。 | `url`、`token`（那台 Runtime 的 `server.token`） |
| `echo` | 原样复述，不调用任何模型。 | 无 |

`permission_mode` 是交给 Claude Code 做事时的权限模式，默认 `manual`：凡是它在终端里会问
“可以吗”的操作，都来问主人，见[授权](#授权)。`allowed_tools` 是预先放行、不用问的规则，
要写窄。`add_dirs` 是除了启动目录之外它还能碰的目录，默认是整个主目录。想回到“没放行的
一律拒绝、谁也不问”，把 `permission_mode` 设成 `dontAsk`。小鱼自己接话的那一次调用不受
这几项影响：它永远没有工具。Codex 的 `sandbox` 同理，默认只读。

### 换成别家的模型（例如 DeepSeek V4）

`claude_code` 类型的代理可以带一个 `env`：启动 Claude Code 时额外设置的环境变量。
把接口地址和密钥指向一个兼容 Anthropic 接口的服务，外壳还是 Claude Code（工具、会话、
回复格式都不变），回答的模型换成那一家的。示例配置里有一个没启用的 `deepseek`：

```json
"deepseek": {
  "type": "claude_code",
  "config_dir": "~/.claude-xiaoyu-deepseek",
  "model": "deepseek-v4-pro",
  "env": {
    "ANTHROPIC_BASE_URL": "https://api.deepseek.com/anthropic"
  },
  "env_files": {
    "ANTHROPIC_AUTH_TOKEN": "~/.config/xiaoyu/deepseek.key"
  }
}
```

`env_files` 里的变量取自文件的内容（去掉前后空白），密钥就不用写进 `config.json`。
每次启动 Claude Code 时现读这个文件，换了密钥不用重启。文件不存在、是空的，或者别的用户
也能读（要 `chmod 600`），`--check` 都会指出来。下面这样放密钥，密钥不会进 shell 历史：

```bash
mkdir -p ~/.config/xiaoyu && chmod 700 ~/.config/xiaoyu
read -rs "key?DeepSeek 密钥：" && printf '%s\n' "$key" > ~/.config/xiaoyu/deepseek.key; unset key
chmod 600 ~/.config/xiaoyu/deepseek.key
```

（这是 zsh 的写法；bash 里写 `read -rsp "DeepSeek 密钥：" key`。）

把 `"enabled"` 改成 `true`，放好密钥，再把 `xiaoyu.default_agent`（和 `voice_agent`，
如果写了）指向它，小鱼自己就由这个模型来回话；原来的 `claude` 留着当帮手，改回去只要
把这两项指回 `claude`。给它一个单独的 `config_dir`，这样它的会话不和订阅登录的那一套
混在一起；这个目录不用登录。密钥也可以直接写进 `env`，只是那样它就留在 `config.json` 里。

没有实测过：这一段是照 DeepSeek 的接口文档和 Claude Code 实际发出的请求写的，没有用
真实的密钥跑过一轮。Claude Code 的网页搜索是 Anthropic 那边的服务端工具，换了服务后
不一定可用。

### DeepSeek Harness（dsh）

`dsh` 类型直接驱动 DeepSeek 自己的命令行 `dsh`，不经过 Claude Code 外壳：

- 后台的事走 `dsh --profile acp`（Agent Client Protocol，标准输入输出上的 JSON-RPC）：
  有进展行、看动静、可以打断。
- 小鱼自己接话那一次默认流式（`stream`，默认 `true`）：dsh 只在一步结束时整段交出回复，
  所以这一次直接调 DeepSeek 的 Messages 接口（和 dsh 用的是同一个：`DEEPSEEK_BASE_URL`
  或 `https://api.deepseek.com/anthropic`，密钥是 `env` / `env_files` 里的
  `DEEPSEEK_API_KEY`），打开 `stream`。已经生成的部分出现在 `GET /v1/messages/<编号>`
  的 `partial` 里，手机边生成边显示。这几轮自己记一份简短的历史，放在
  `<config_dir>/xiaoyu-stream-sessions/`（编号以 `api-` 开头）。流式没成、或者设了
  `stream: false` 时，退回 `dsh --profile headless --json`：一问一答，关掉工具和思考，求快。
- 可选 `chat_model` 只用于小鱼接话、路由和结果转述；后台工具任务继续使用 `model`，
  按次明确指定的模型优先。未设置时沿用原有模型。例如可将 `model` 设为 `deepseek-v4-pro`，
  `chat_model` 设为 `deepseek-flash`；不会删掉人设、会话历史、派活或审批逻辑。
- 流式保活、空行和无效事件也受同一总时限约束。失败后的 headless 回退只使用剩余时间，
  总时限已用完时不再重试。日志记录模型、首字和总时长，不记录对话正文或密钥。
- 没有 Claude Code 那种“每一步都问”的通道，权限只看 `sandbox`（`read-only`、
  `workspace-write`、`danger-full-access`，作为 `DSH_PERMISSION_MODE` 传给它）：沙箱内它
  自己做；越出沙箱时它来问，这一问照常变成设备和手机上的授权弹窗。所以没有
  `permission_mode`、`allowed_tools`、`add_dirs` 这几项，写了会报错。
- 档位是 `off`、`low`、`high`、`max`。`efforts` 不写就是 `[]`：不传，用 dsh 自己的默认。
- `config_dir` 设成 `DSH_HOME`，让它用一套单独的配置和会话，不碰你平时的 `~/.dsh`。
- 用量只记它用的是哪个模型：它报的是上下文占用，不是计费的 token。

没有测试，也没有实测：协议是照 dsh 0.2.0-rc.2 写的，握手时版本或方法名对不上会直接说。

### 项目简报

`xiaoyu_runtime/project_brief.py` 是一个可以当 `command` 帮手用的小脚本，回答“这个项目
最近在干什么”：读这台电脑上的 `git log` / `git status`，以及 Claude Code
（`~/.claude/projects/<仓库>/`）和 Codex（`~/.codex/sessions/`，按工作目录过滤）留在本机的
最近会话，打印一段摘要，小鱼再转述。只用标准库，可以脱离 Runtime 单独跑：

```json
"brief": {
  "type": "command",
  "command": ["python3", "/Users/你/ai-passport/runtime/xiaoyu_runtime/project_brief.py", "~/ai-passport"],
  "description": "说说这个项目最近的改动和在 Claude Code、Codex 里聊过什么"
}
```

脚本要写绝对路径（命令在这个帮手的 `workdir` 里运行）；仓库路径可以用 `~`。只读本机的缓存：别的电脑上的会话、claude.ai 普通对话里聊的内容都查不到。

示例配置里带了一个没启用的 `codex`；在同一台机器上装好并登录 Codex 命令行之后，把
`"enabled"` 改成 `true`，再跑一次 `--check`。

以后接本地模型，就是加一个 `command` 类型的代理并把 `speaks` 设成 `true`：它会连同人设
一起收到这句话，回复可以是普通文字，也可以是 `{"reply", "brief", "mood"}` 这样一个
JSON 对象。

### 这句话交给谁

先后顺序是固定的：

1. 调用方指定了代理（接口里的 `agent`）——照办。
2. 主人在话的开头点了名——交给被点名的。认两种说法：`@codex 看看这个`，以及
   “让 / 叫 / 请 / 用 / 问 / 问问 / 找 / 交给”加上名字或叫法，比如“让 Codex 看看这个报错”。
3. 路由器有意见——听它的。
4. 都没有——交给默认代理。

点名时可以紧跟着说这一次用哪一档努力程度（0.6.0 起）：`@codex 高档 看看这个报错`、
“让 codex 用高档看看这个报错”。认 `低档`、`中档`、`高档`、`特高档`、`最高档`、`最低档`
和 `low`、`medium`、`high`、`xhigh`、`max`、`minimal`；只认紧跟在名字后面的，这个词会从
交给帮手的话里去掉。那个帮手不能选这一档时，按它平时的。见[档位](#档位模型和努力程度)。

前三条都算“指定了由谁做”：Runtime 不问模型，直接开一张卡，小鱼说一句“交给 codex 了”，
那个代理在后台做。消息记录里有一条 `handoff`，`agent` 是它。替小鱼说话的那个代理也一样：
`@claude ……` 是让 `claude` 带着工具、以小鱼的身份在后台做这件事，Runtime 会告诉它被点名的
就是它自己。默认代理只会干活时，第 4 条也这样处理。

路由器是可以换的那一块。`mention` 不额外判断，只靠上面的第 1、2、4 条。`command` 运行
你指定的命令：标准输入是 `{"text", "default", "agents": [{"name", "type", "description"}]}`，
标准输出的第一行是选中的代理名。输出为空、不认识的名字、出错或超时都退回默认代理——
路由器坏了不应该让主人说不了话。以后用一个小模型或分类器来做判断，就接在这里。

### 一件事一张卡

没指定由谁做的话，由替小鱼说话的代理接。这一次**不给它任何工具**，并且最长
`voice_timeout_seconds`：它只需要听懂。Runtime 告诉它有哪些帮手（包括它自己这个代理）、
现在有哪些事（编号、标题、谁在做、做了多久），以及主人说这句话时屏幕上是哪件事。它的
回复里除了要说的话，还写明这句话归到哪张卡（新的，或已有的编号）和要 Runtime 做什么：

| 做什么 | 含义 |
| --- | --- |
| `none` | 她已经直接答了。 |
| `start` | 交给一个帮手在后台做。她写明交给谁、这件事的短标题、交代给帮手的任务。 |
| `amend` | 主人对一件事有新的话。`after`：现在这一轮做完，接着做追加的部分。`redo`：要求变了——帮手支持中途追加就直接告诉它，不支持就停掉这一轮，在同一个会话里按新的要求重做。那件事已经结束时，接着它原来的会话再做一轮。 |
| `cancel` | 停掉一件正在做的事，结果不要了。 |

办不了的（没有这个编号、没有这个帮手、要取消的事已经不在做了）会告诉她原因并让她重说
一次；还是办不了，就照实告诉主人，不让她把没做的事说成做了。

后台的事每件一个线程，互不等待；同一个帮手可以同时做几件，因为帮手的会话是按
“事 + 帮手”记的（`state/sessions.json` 里的 `对话名/卡的编号`）。能以小鱼的身份说话的帮手
带着人设做事，它的结果就是小鱼的话。只会干活的帮手只拿到任务，看不到人设和聊天记录；
它的原始结果交回这个对话的线上，由 `voice_agent` 转述。`voice_agent` 这时不可用的话，原样
把结果给主人，不让这件事白做。帮手没做成，卡上照实写“没做成”和原因。

卡的状态有 `working`（帮手在做）、`waiting`（等主人点头，见[授权](#授权)）、`done`、
`failed`、`cancelled`；`talking` 是预留的。卡存在 `state/cards.json`，留最近 50 张；
交给过帮手、已经结束的事里最近的 15 件一定留着，不被后来的卡挤掉（0.6.1 起，设备第三屏
的任务历史靠它）。这样的事结束时记下 `finished_at`，又接着做的话清掉，再结束时重新记。Runtime
重启时还在做的事没法接着做，会被标成 `failed`，原因写“Runtime 重启了，这件事没做完”。

交给帮手做的卡另有三项（0.6.0 起）：`effort` 是这件事用的努力程度，`model` 是实际用的
模型，`cost` 是到现在花了多少，见下面两节。

### 任务图片和可下载文件

任务产物先登记，手机才能显示或下载。由 Runtime 管理的 Claude Code、Codex 和 DSH
任务会在指令中收到各自的 `state_dir/artifact-inbox/<card-id>/` 目录。把已完成的文件
保存在这里，再写一份相对路径清单：

```json
{
  "artifacts": [
    {"path": "preview.png", "name": "Preview.png"},
    {"path": "design.pdf", "name": "Design.pdf"}
  ]
}
```

清单文件名为该目录下的 `manifest.json`。不要列入凭据、个人数据或原始日志。
自动导入仅限这件事的输出目录，不会按回复中提到的路径扫描电脑。带鉴权的显式登记请求
也可以读取该卡代理的工作区，但会拒绝隐藏/私密路径、凭据或配置文件、Runtime 的其他
状态文件、符号链接和目录遍历。每次最多登记 16 个文件。

Runtime 把每个文件复制到 `state_dir/artifacts/`，计算 SHA-256 并分配稳定附件 ID。
下载只使用 ID，不使用电脑文件路径。源文件改动、删除或 Runtime 重启后，快照仍然可用。
单文件上限 64 MB，快照存储上限 1 GB；导入失败会留下 `status: "error"` 的附件和有长度
上限的说明。删除卡片不会自动回收其快照。

元信息同时保存在卡片汇总的 `attachments` 和最终回复的 `entries[].attachments` 中：
`id`、`name`、`mime`、`size`、`kind`（`image` 或 `file`）、`width`、`height`、
`sha256`、`source`、`created_at`、`status`、`error`，以及布尔值 `thumbnail`。
添加附件会推进卡片的 `seq`。帮手回复超过 8,000 字符显示上限时，也会在显示清理前保存
一份 UTF-8 全文附件，正文说明内容已截断。JSON 的 `reply` 包装会先解开，再显示或
保存完整正文。

运行 Runtime 的 Python 有 Pillow 时，会生成最大 480×480 像素的预览。没有 Pillow 时，
支持的图片不超过 2 MB 和约一百万像素，就使用原图字节作为预览；较大的图片保留原图
下载，并标记 `thumbnail: false`。预览不可用时返回 `404`，客户端可退回原图。
两个下载接口都要求 Runtime 令牌，并按有界的数据块流式传输。

配套 App 展示已登记的图片和文件卡片，通过 Android 系统能力下载、保存或打开文件。
缓存归属同时包含来源 Runtime、附件 ID 和哈希。这一版不包含手机附件上传、任意外部
URL 获取、App 内置 PDF 阅读或通用蓝牙文件传输。只观察电脑端会话记录，也不会自动
导入电脑会话附件。

### 能力：MCP 服务和 skill

MCP 服务按电脑登记在 `capabilities` 里，不按帮手配。除非 `grants` 另有写明，这台电脑上的帮手都能用；帮手拿不到服务的 Key。每一轮 Runtime 给帮手注入一个 MCP 服务——能力网关：它列出这个帮手被授权的工具，调用经过 Runtime，每次都核对授权。Claude Code、Codex、dsh 会注入；pi 暂时不注入（它只有全局的 `pi mcp add`）。见[帮手的能力](../docs/development/helper-capabilities.zh_CN.md)。

```json
"capabilities": {
  "mcp_servers": {
    "notion": {"name": "Notion", "url": "https://mcp.notion.com/mcp", "headers": ["Authorization"]},
    "files": {"name": "笔记", "command": "npx", "args": ["-y", "@modelcontextprotocol/server-filesystem", "~/notes"]}
  },
  "grants": {"codex": {"notion": false}, "claude": {"files": {"tools": ["read_file"]}}}
}
```

`headers` 和 `env` 只写名字。值（“连接”）放在 `state/capability-secrets/<编号>.json`，比如 `{"headers": {"Authorization": "Bearer ..."}}`，只有本机账号能读（`chmod 600`）。缺连接的服务会作为“用不了”告诉帮手，而不是直接消失。

`claude_code`、`dsh`、`pi` 帮手的 `skills` 写 skill 目录（每个里面有 `SKILL.md`）：Claude Code 拼成临时插件载入（`--plugin-dir`），dsh 经它的 `skill-filesystem` 插件，pi 用 `--skill`。Codex 还没有按次指定的办法，写了会报错。

### 档位：模型和努力程度

0.6.0 起，`claude_code` 和 `codex` 类型的帮手各有一档平时的努力程度，派活时可以换：

| 配置项 | 默认值 | 含义 |
| --- | --- | --- |
| `effort` | `medium` | 这个帮手平时用的努力程度。 |
| `efforts` | `["low", "medium", "high"]` | 派活时可以选的几档。Claude Code 认 `low`、`medium`、`high`、`xhigh`、`max`，Codex 认 `minimal`、`low`、`medium`、`high`、`xhigh`；想放开更高的自己加。写成 `[]` 表示 Runtime 不传努力程度，用工具自己的默认。 |
| `models` | `[]` | 派活时除了 `model` 之外还可以换成哪些模型。空着就是不能按次换。 |

用 `env` 指到别的服务的 `claude_code` 帮手（比如 DeepSeek）是例外：`efforts` 不写就是
`[]`，因为那边认不认努力程度不一定；确认它认之后自己写上。`command`、`remote`、`echo`
没有这三项。`efforts` 不是空的时候，`extra_args` 里不要再写 `--effort` 或
`model_reasoning_effort`。

**注意：升到 0.6.0 之后，这两类帮手默认按中档做事**，不再是工具自己的默认（那个默认随
工具的配置和版本不同，常常更高）。同样的事可能做得浅一些、也省一些；觉得不够时对那件事
说“用高档重做”，或者把这个帮手的 `effort` 改高。

传给工具的方式：Claude Code 是 `--effort <档>` 和 `--model <模型>`；Codex 的 `exec` 模式是
`-c model_reasoning_effort="<档>"` 和 `--model`，`app_server` 模式是每一轮 `turn/start` 里的
`effort` 和 `model`。小鱼自己接话的那一次不带平时那一档：它求的是快。

一件事用哪一档，按这个顺序定：

1. 主人点名时紧跟着说的档（见[这句话交给谁](#这句话交给谁)）。
2. 小鱼在回复里选的：`start` 和 `amend` 的 `action` 里可以写 `effort` 和 `model`。提示里
   告诉她：主人说了就按主人的；没说时，查一下、看一眼用低档，一般的事不写，明显难的才用
   高档并说一声。
3. 调用方选的：接口里的 `effort`、`model`。
4. 都没有：这件事上一次就是这个帮手做的，沿用上一次的；否则用它平时的。

选的档或模型那个帮手不认时，当作没选。对一件正在做的事改要求时换了档，这一轮会停掉、
按新的档重做（不走中途追加）。

帮手实际用的模型由它自己报：Claude Code 在事件流开头说，Codex 在开线程时说。配置里
`model` 空着也能看到真正在干活的是哪个。跑过一次才知道；记在 `state/usage.json` 里。

### 用量：订阅额度还剩多少

0.6.0 起，Runtime 记着每个订阅账号的两个窗口各还剩多少：5 小时的和一周的。它给三处用：
小鱼派活时看、手机和设备上显示、算一件事花了多少。

额度按**账号**记，不按帮手记：几个帮手用同一个配置目录（同一个登录）时，额度是一份。
只算订阅登录的：没有用 `env` 指到别的服务、也没有在 `env` 里放密钥的 `claude_code`
帮手，和 `codex` 帮手。别的帮手没有额度，只记它用的模型。

数从这几处来。Runtime 自己不读任何登录凭据，问的都是工具本身，或者工具自己存下的数：

| 来源 | 什么时候 | 给什么 |
| --- | --- | --- |
| Claude Code 干活时的事件流（`rate_limit_event`） | 每件交给它的事 | 两个窗口各用了多少、什么时候重置；数一变它就说一次 |
| Claude Code 的 `get_usage`（`claude -p --input-format stream-json` 加一个控制请求，不调用模型） | 太久没问过时；一件事开始前；`usage` 命令 | 和 Claude Code 里 `/usage` 同一份数 |
| Codex 的 app-server（`account/rateLimits/read`，和干活时的 `account/rateLimits/updated`） | 每一轮开始和结束；太久没问过时 | 两个窗口各用了多少、什么时候重置 |
| Claude Code 自己记下的 `.claude.json` 里的 `cachedUsageUtilization` | `get_usage` 没带额度时（较新的 Claude Code 会这样） | 同一份百分比；超过一天的不用 |

`get_usage` 在 Claude Code 里标着“实验性”，事件流里两个窗口的那部分标着“内部”：哪天
对不上了，那个账号就显示成“还不知道”，原因在 `note` 里，别的不受影响。

每个账号的状态：`ok`；`warn`（有窗口剩不到 20%）；`out`（有窗口用完了）；`unknown`
（一个数都没有）。过了重置时间的数不再作数。

小鱼接话时，帮手清单里每个帮手后面多一段：

```text
- codex：看代码、改代码〔gpt-… · 平时中档，可选 低/中/高；5 小时剩 58%（14:20 重置），本周剩 81%（周三 09:00 重置）〕
```

提示里告诉她：几个帮手都能做时交给剩得多的；写着“快用完了”的，主人没指定档位就降一档
并说一声；写着“用完了”的不派，除非主人点了名；“还不知道”不当成用完。指定了由谁做的
那几条路（接口里指定、话里点名、路由器）不问模型，所以不受这些影响：Runtime 不替主人换
帮手，也不替主人降档。

每一轮做完，卡上的 `cost` 加上这一轮的：`in`、`out`、`cached` 是 token（新读的、写的、从
缓存读的），`d5`、`d7` 是两个窗口各少了几个百分点。同一个账号同时在做几件事时，`d5`、`d7`
是几件合起来的，只能当大概的数；额度的百分比是整数，小事常常是 0。

不启动服务也能看：

```bash
python3 -m xiaoyu_runtime --config config.json usage
```

它现在就去问一次，打印每个账号还剩多少、查不到的原因、每个帮手的模型和档位。加
`--json` 打印原始数据。

### 共享工作区

小鱼和别的 agent（claude.ai 上的 Claude、Codex，以后还有别的）共用一份记录，放在 Notion
里，一式两份：

- **总线**：AI 之间互通，以它为准。一行一条，内容原样（命令、路径、报错不转述）。
- **日志**：给人看的。一件事结束时写一条摘要，用“关联”对到总线里的行。

小鱼这边都是 Runtime 的代码在写，不靠模型记得写：

| 什么时候 | 写到哪 | 写成什么 |
| --- | --- | --- |
| 把活交给一个代理（新开一件事、点名、对一件已经结束的事再做一轮） | 总线 | 类型“任务”，谁写的 `xiaoyu`，给谁和谁接了都是那个代理，状态“已接”，正文是交代给它的原话 |
| 对正在做的事补充或改要求 | 总线 | 类型“留言”，正文第一行写实际怎么办的（中途追加、停下重做、做完接着做） |
| 代理交回结果，或者没做成 | 总线 | 类型“结果”，谁写的是那个代理，状态“做完”或“没成”，正文是它的原始结果或没成的原因 |
| 取消 | 总线、日志 | 总线一条状态是“取消”的留言；日志一条 |
| 一件事结束 | 日志 | 标题、谁做的、结果、结论（小屏幕上那句简报）、正文是小鱼最后说的话；她要你拿主意时，那句话也写进“要你做的” |

“关联”写成 `Runtime 的名字/卡的编号`（例如 `mac/c12`），同一件事的行靠它对上。小鱼自己
接话的那一次（不给工具的那次）不算交给别人，不写。

写 Notion 走网络，所以排进一条队列由单独的线程写，小鱼说话和后台的事都不等它。遇到
限流或服务器出错会重试三次；还是写不进去的（断网、令牌失效、数据库没有共享给集成）追加到
`state/workspace-unsent.jsonl`，并在标准错误里说一声。令牌只放在请求头里，不会写进任何
一行。交代给代理的话和它的结果是原样写的：里面如果带了密钥，也会被写进去，所以不要在
交给帮手的话里贴密钥。

要做的准备：在 Notion 里建一个内部集成，拿到令牌；把放着总线和日志的那个页面共享给这个
集成；两个数据库的列名要和上表一致（总线：`标题`、`类型`、`谁写的`、`给谁`、`状态`、
`谁接了`、`关联`；日志：`标题`、`谁做的`、`结果`、`结论`、`要你做的`、`关联`）。

别的 agent 想读写同一份记录，各自接 Notion 就行（有 MCP 的配 Notion MCP），不经过 Runtime。

### 授权

从 0.12.0 起，快速审核新增需要认证的 `GET /v1/review?conversation=default`、
`POST /v1/review/batch`、`POST /v1/review/mode`，原有单条审批接口不变。GET 返回本进程的
`epoch`、模式 `revision`、当前 `mode`/`expires_at`、完整的可审核 `pending` 操作及不透明
指纹 `key`，以及最近审核记录。批量接口接受 `epoch`、`conversation` 和
`items: [{"id", "key"}]`（1 到 100 项），逐项核验快照指纹并返回各项 `status`；快照之后
新出现的请求不包含在内。重复提交已经处理的批次只返回当前结果，不再次授权。

模式设置接受 `epoch`、已看到的整数 `revision`、`conversation`、
`mode: "manual" | "auto_allow"`，以及可选的 `seconds`（默认 3,600）。从 0.13.0 起，
`0` 表示持续开启直到手动关闭；定时范围为 60 到 31,536,000 秒。GET 新增 `duration_options`
（持续开启支持、最短/最长秒数、重启后保留能力）、`duration_seconds` 和 `persistent`。
持续开启返回 `mode: "auto_allow"`、`persistent: true`、`expires_at: 0`。App 0.21.0 提供预设
和自定义分钟数，只向明确声明这些能力的 Runtime 发送扩展值。这是主人预授权，不是模型风险审核。只有当前对话本机帮手明确标识为
`Read`、`Glob`、`Grep` 或 `LS` 的结构化 `operation` 会自动允许。权限 Gate 提供这个字段；
无法提供明确只读类型的适配器仍为手动。命令、写入或删除、付费、联网工具和未知操作仍需手动
确认，可加入当前展示的批次。独立桌面会话、网络副本、其他对话和卡住后是否继续的询问都
不包含在内。可远程回答且
未过期的桌面请求可明确加入批量审核，现有实时响应通道仍会核验实际操作。旧进程标识或模式
版本返回 `409`，输入无效返回 `400`，审核记录写盘失败返回 `503`。所有接口都需要配对令牌。

从 0.13.0 起，预授权与审核元数据一起原子保存，Runtime 重启后保留。定时模式保留原绝对
截止时间，重启不会延长有效期；持续模式保持生效直到明确关闭。初始状态为手动，旧审核记录
不会生成预授权。待审请求和正常手动决定沿用原有行为。
审核元数据保存在 `state_dir/approval-review.json`（仅本人可读，保留最近 200 条，接口返回
当前对话最近 50 条），不写命令正文和文件内容。先记录决定意图，再执行允许，再记录结果。
最后一次写盘失败时，操作可能已经允许，应先刷新核实再重试。写盘失败会关闭自动通过，
并尝试写入独立的 `approval-review.disabled` 标记，防止替换失败后重启恢复旧授权。
成功明确设置模式后清除此标记。已保存的设置无效时，以手动模式启动并显示错误。
审批结果通过现有同步协议携带 `by: "phone_batch" | "auto_allow"`。模式状态从 GET 读取，
App 审核页打开时每三秒刷新。旧客户端忽略新增元数据，仍可逐条确认。Runtime 0.12.0 忽略
持久预授权，恢复其原有临时模式。回退前应先关闭自动通过以清空已保存策略，避免以后再次
升级时恢复持续授权；审核记录文件继续保留。

快速审核还将已登记的诊断事件类型 `review` 写入统一事件日志
（`state_dir/iterate/app-events/*.jsonl`，保留 90 天）。事件覆盖开启/关闭、调整时长、到期、
重启恢复、审核决定意图/结果、过期或无效请求和存储失败。字段包含模式、秒数、原时长、
截止时间、操作/结果/原因码及任务/审批标识；不包含命令正文、路径、文件内容或令牌。
Runtime 的来源及客户端元数据标明源电脑。事件共用同一事件库和现有 mesh 发布路径，
不会把授权策略复制到其他电脑。诊断日志失败时，以持久审批审计为准；失败会写入 Runtime
错误日志，不会改变已经提交的审批结果。

交给 Claude Code 的事以 `--permission-mode manual` 运行，并带上 Runtime 自带的权限询问工具
（`xiaoyu_runtime/permission_mcp.py`，一个只有一个工具的 MCP 服务）。Claude Code 要问
“可以吗”的时候改成调用这个工具；工具把操作交给 Runtime，这件事的卡变成 `waiting`，
`/v1/feed` 的 `approvals` 里多一项，然后一直等。主人回答 `allow`，这一步照做；回答 `deny`，
这一步不做，Claude Code 被告知“主人说不行”，接着决定怎么办。那件事被取消、改了要求或
超时，还没答的授权自动作废。

给主人看的内容原样来自这次工具调用，不经过模型：`tool` 是“帮手名 · 工具名”（例如
`claude · Bash`），`detail` 是命令原文；写文件是路径加内容，改文件是路径加被替换的和替换
成的。进展行也一样：工具名加上最关键的一个参数。

权限询问工具是另一个进程。它不走手机连的那个接口，而是连 Runtime 另开的一个只听
`127.0.0.1`、端口随机的入口，用的是只对这一件事、这一轮有效的钥匙；钥匙写在 `state/` 下
一个只有自己可读的临时文件里，这一轮结束就删。Runtime 的令牌不会写进任何文件。

命令行里用 `--once` 说一句话时没有手机和设备在场：有授权就在终端里问（`可以吗？[y/N]`），
不是终端就当作不行。

交给 Codex 的事也一样会来问，只是路不同：Codex 的 app-server 自己把询问发给 Runtime
（跑命令、改文件、要更多权限三种），不需要权限询问工具。默认是只读沙箱加
`approval_policy: "on-request"`：读哪里都行，任何改动、任何要出沙箱的命令都来问。`tool` 是
`codex · 命令`、`codex · 改文件`、`codex · 权限`，`detail` 是命令和所在目录、每个文件的
路径和改动、或者它要的权限原文，后面跟着 Codex 自己给的理由。它问别的东西（要主人填
内容之类）一律不答应。想放宽就改 `sandbox`（`workspace-write` 时在工作目录里写不用问）或
`approval_policy`；`mode` 是 `exec` 时没有询问，沙箱不让做的就是做不了。

改要求（`redo`）对 Codex 是中途追加：新的话直接送进正在做的这一轮（`turn/steer`），不用
停下重来。追加不进去时（这一轮刚开始或刚结束）才和 Claude Code 一样停下再做。取消是先
打断这一轮（`turn/interrupt`），5 秒内没停就结束进程。

app-server 在 Codex 的帮助里标着“实验性”。`--check` 会对每个这种模式的 `codex` 代理做一次
握手（不调用模型）；握手不成会说明原因，这时可以先把 `mode` 改成 `exec`。

### 长任务：看有没有动静，不看总时长

后台的事默认不设总时长（0.5.4 起）。按总时长一刀切，会把一件做了四十分钟、还在正常做
的事杀掉。Runtime 看的是它多久没动静：

- Claude Code 的事件流、Codex 的 app-server 消息，每来一行就算一次动静。
- 平时连续 `idle_timeout_seconds`（默认 10 分钟）没有一行，算可能卡住。一步操作已经开始、
  结果还没回来时（一条命令在编译、在跑测试），事件流本来就是安静的，这时按
  `idle_timeout_command_seconds`（默认 30 分钟）算。
- 停着等主人点头的时间不算，总时长（设了的话）也不算它：主人晚点回答，不会把事等没了。
- 到点了不直接停，先问主人：这件事的卡变成 `waiting`，`/v1/feed` 的 `approvals` 里多一项，
  `kind` 是 `stall`，`tool` 是“帮手名 · 没动静”，`detail` 里写着多久没动静和最后一步的原文。
  它和授权走同一个弹窗，所以设备和手机不用改：**可以 = 接着等，不行 = 停掉**。
- 回答“可以”：接着等，下一次要隔两倍的时间才再问。问的时候它自己又动了：问题自动收回，
  当没问过。
- 回答“不行”，或者 `idle_answer_seconds`（默认 30 分钟）没人答：停掉，卡变成 `failed`，
  上面写明原因。
- 停掉不丢进度：帮手的会话一开始就记下了，对这件事说“接着做”，会在同一个会话里续上。

想要一个硬上限的，给 `turn_timeout_seconds` 或某个代理的 `timeout_seconds` 写一个数。
`command` 类型和 `codex` 的 `exec` 模式没有事件流，看不出动静，仍然只按总时长（不限时按
3600 秒）。小鱼自己接话的那一次不受这些影响，还是 `voice_timeout_seconds`。

### 使用单独的 Claude 登录

Claude Code 从 `~/.claude` 读取登录信息和设置。如果那个目录已经配成别的用途（比如
通过 `ANTHROPIC_BASE_URL` 走公司网关，或者配了 API 密钥），不要改它，给小鱼单独准备
一个目录：

```bash
mkdir ~/xiaoyu-login && cd ~/xiaoyu-login      # 任意文件夹，但不能是主目录
CLAUDE_CONFIG_DIR=~/.claude-xiaoyu claude       # 用订阅账号登录，/status 确认后退出
```

然后在这个 `claude_code` 类型的代理下设置 `"config_dir": "~/.claude-xiaoyu"`。登录时不要待在主目录：
Claude Code 还会把“当前文件夹/.claude/settings.json”当作项目设置加载，而在主目录里，
这正是你想绕开的那个文件。

Codex 的 `~/.codex` 也一样：那里的 `config.toml` 如果指向一个网关，不管登录的是哪个账号，
请求都会发到网关去。给小鱼的 Codex 一个单独的目录（`CODEX_HOME`）；这个目录 Codex 不会
自己建：

```bash
mkdir -p ~/.codex-xiaoyu
CODEX_HOME=~/.codex-xiaoyu codex login
CODEX_HOME=~/.codex-xiaoyu codex login status
```

然后在这个 `codex` 类型的代理下设置 `"config_dir": "~/.codex-xiaoyu"`。


## HTTP 接口

消息快照的 `processing_started_at` 和 `first_partial_at` 是可选 Unix 时间戳，分别用于区分排队时间和首段可见文字的等待时间。

除 `/healthz` 外，所有请求都要带 `Authorization: Bearer <令牌>`。请求体和响应通常为
JSON；下列语音、固件和附件二进制接口除外。

| 请求 | 结果 |
| --- | --- |
| `GET /healthz` | `{"ok": true, "version", "backend", "name"}`，不需要令牌。`backend` 是默认代理的类型。 |
| `GET /v1/agents` | `{"default", "agents": [{"name", "type", "description", "speaks", "default", "model", "effort", "efforts", "models"}]}`：小鱼在这台 Runtime 上能用的代理。后四项是 0.6.0 加的：实际用的模型（没跑过是配置里写的，都没有是 `null`）、平时的努力程度、派活时可以选的档和模型。 |
| `GET /v1/helpers/options` | harnesses、界面添加的帮手、MCP 服务与当前授权；接入方式包括 `magpie`、`key`、`login`。 |
| `POST /v1/helpers` | `{name,harness,access,model?,effort?,provider?,env?,key?,gateway_url?}` 添加帮手。`magpie` 从模型服务选择模型，支持跨服务商接入；`key` 直接传变量，`login` 使用原生登录。返回 `201 {helper,problem}`。 |
| `POST /v1/helpers/<名字>/remove` | 移除界面添加的帮手及其 Key；0.24.1 也支持可恢复地停用配置中的非主对话帮手。 |
| `POST /v1/helpers/models` | `{gateway_url,key?}` 读取本机 Magpie 实时模型目录，不返回凭据。 |
| `POST /v1/helpers/<name>/capabilities` | `{server_id: true/false/{tools:[...]}}` 持久化界面所加帮手的授权，下次调用生效。 |
| `GET /v1/usage?refresh=1` | `{"rev", "at", "accounts": [...], "agents": [...]}`（0.6.0 起）。`accounts` 是每个订阅账号：`{"id", "kind", "agents", "state", "windows": [{"kind": "5h" 或 "7d", "left", "resets_at"}], "plan", "source", "updated_at", "note"}`，`left` 是剩下的百分比，不知道是 `null`。`agents` 是每个帮手：`{"name", "type", "model", "effort", "efforts", "models", "account", "running", "now"}`，`running` 是它正在做几轮，`now` 是正在做的那一轮用的模型。不带 `refresh` 时返回手头的数（太久没问过就在后台问一次）；带了就现在去问，要等几秒。见[用量](#用量订阅额度还剩多少)。 |
| `POST /v1/messages`，请求体 `{"text", "conversation"?, "client_id"?, "agent"?, "card"?, "pin"?, "effort"?, "model"?}` | `202` 和这条消息的记录，状态为 `queued`。`effort`、`model`（0.6.0 起）是给这句话选的努力程度和模型，交给帮手时用；带了 `agent` 时必须是那个帮手能选的，否则返回 `400`。带 `agent` 表示点名交给这个代理；没有这个代理时返回 `400`。`card` 是主人说这句话时屏幕上那件事的编号。`pin` 为 `true`（0.5.3 起）表示主人是打开那件事、在它里面说的：这句话一定归到它，要动手就接着它原来的会话做，不另开卡；话里点了帮手的名也一样（那件事正由别的帮手做着时除外）。`client_id` 用来防重复（0.7.0 起按对话算，并且重启后仍然有效）：同一个对话里同一个 `client_id` 再来，内容一样返回原来那条，不重做；内容不一样返回 `409`。这条消息落盘后才返回 `202`，见[消息落盘与重启](#消息落盘与重启)。 |
| `POST /v1/voice?conversation=<名字>&client_id=<编号>&agent=<代理>&card=<编号>&pin=1&effort=<档>&model=<模型>`，请求体是一个 WAV 文件 | `202` 和消息记录，`kind` 为 `voice`，`text` 为空。录音不合格或没有配置引擎时返回 `400`。 |
| `GET /v1/messages/<id>?wait=<秒>&rev=<n>` | 消息记录。带 `wait`（最多 60）时，小鱼一接完这句话就返回。再带上 `rev`（调用方手里那份记录的 `rev`）时，记录只要有任何变化就返回。回复还在生成时，`partial` 是到目前为止的整段回复（整段替换，不是追加；空串表示收回）；处理完后是空串。0.7.0 起 Runtime 重启后仍然查得到（最近 200 条）。 |
| `GET /v1/messages/<id>/stream?rev=<n>` | 经过鉴权的 `text/event-stream`，用一条持续连接推送消息快照。每段带相同消息 ID 与完整 `partial`/`rev`，结束状态关闭连接，注释维持心跳。消息记录用 `event_stream: true` 声明支持；连接失败后轮询相同 ID，不重新提交任务。旧 Runtime 继续使用长轮询。 |
| `GET /v1/timers/effects?conversation=<name>&limit=50&cursor=<cursor>` | 有界待核对定时摘要，不含选定参数；列表改变时旧不透明游标失效。能力名 `timer_effect_review`。 |
| `GET/POST /v1/timers/<id>/effects` | 查看或明确确认原定时操作，沿用消息操作的身份／版本／逐项结论规则，不重放或回滚。见上方定时核对契约。 |
| `GET /v1/feed?conversation=<名字>&after=<序号>&wait=<秒>&usage=<rev>` | `{"seq", "cards": [...], "approvals": [...], "usage": {...}}`：当前对话变更的卡和待回答授权，以及跨对话的电脑端观察卡和审批。授权包含 `id`、`card`、`conversation`、`agent`、`tool`、`detail`、`created_at` 和 `kind`（`tool` 或 `stall`）；电脑端审批还标记来源及是否可远程回答。授权变更会更新所属卡。`wait` 最多 60 秒；卡变更时提前返回，带 `usage=<rev>` 时用量变更也提前返回。下次把收到的 `seq` 作为 `after`；返回序号变小说明记录被替换，要从 0 重新同步。0.7.0 起：带 `messages=1` 时还多一项 `messages`，是这个对话里序号比 `after` 大的消息记录（和卡共用一条序号，包括还在排队、正在生成回复的），另一台手机能看到这边刚说的话；不带时和以前完全一样。返回里的 `epoch` 是这份记录的编号，和上次看到的不一样说明换了一份记录，也要从 0 重新同步。 |
| `GET /v1/info` | `{"runtime_id", "epoch", "protocol", "capabilities", "name", "version"}`（0.8.0 起）：这台 Runtime 的固定编号、这份记录的编号、同步协议版本和能力。见[统一消息结构](../docs/development/unified-message-model.zh_CN.md)。 |
| `GET /v1/sync?conversation=<名字>&after=<序号>&epoch=<编号>&wait=<秒>&usage=<rev>&states=unavailable` | 统一同步（0.8.0 起）：`{"protocol", "runtime_id", "epoch", "seq", "reset", "more", "messages", "tasks", "approvals", "removed", "usage"}`。0.8.1 起带 `usage`（同 `/v1/feed`），带上 `usage=<rev>` 时用量变了会提前返回；能力里有 `sync_usage`。0.8.2 起，说完一轮的电脑会话在这里的 `state` 是 `turn_ended`（`/v1/feed` 和卡上仍是 `done`）。0.9.0 起：`approvals` 改成序号比 `after` 大的授权变化，每项带 `status`（`pending`、`allowed`、`denied`、`withdrawn`）、`seq`，答了的带 `by`（`phone`、`computer`），收回的带 `reason`；回答里还有 `subject_id`（小鱼这个主体的编号，和 `runtime_id` 分开）。`messages` 是序号比 `after` 大的消息（一句话一条，编号沿用卡条目的编号，带 `status`、`partial`、`author`、`source`、`rev`、`seq`），`tasks` 是卡去掉 `entries`、换成 `members`。0.9.1 起，带了 `states=unavailable` 的客户端，对很久没更新、还没结束的观察会话或上报会话拿到的 `state` 是 `unavailable`（现在看不到它的状态，不是做完了）；没带的客户端照旧拿到卡上的状态和 `stale: true`。`epoch` 不对、游标比现在的序号大或者早于日志的 `floor` 时 `reset` 为 `true`，并从 0 给快照。一致性测试：`python3 -m xiaoyu_runtime.conformance --url <地址> --token <令牌>`。 |
| `GET /v1/cards?conversation=<名字>` | `{"cards": [...]}`：最近 50 张卡；启用电脑端同步时也包含电脑端观察卡。 |
| `POST /v1/cards/<编号>/artifacts`，请求体 `{"artifacts": [{"path", "name"?}], "entry_index"?}` | 返回包含已登记附件的卡。`entry_index` 为现有消息的下标，从 0 开始；省略时绑定最后一条代理回复。输入格式错误返回 `400`，没有卡返回 `404`；单个文件失败会保留为 `status: "error"` 的附件。 |
| `GET /v1/artifacts/<id>/content` | 已登记的不可变文件快照，按实际 MIME 和附件文件名流式返回二进制内容。ID 或快照不可用时返回 `404`。 |
| `GET /v1/artifacts/<id>/thumbnail` | 图片预览的二进制内容；没有可用预览时返回 `404`，可退回 `/content`。 |
| `POST /v1/cards/<编号>/cancel` | 取消这件事，返回这张卡；它已经不在做了就原样返回。没有这张卡返回 `404`；电脑端观察卡返回 `400`，需要到电脑端停止。 |
| `POST /v1/cards/<number>/reconcile`，请求体 `{"run_id":"<original run>"}` | 在原执行 Runtime 上核对结果不明的帮手任务，不重做；返回 `200 {status: "confirmed" 或 "unconfirmed", card}`。运行编号过期或卡不适用时返回 `409`。见前文帮手重启恢复。 |
| `POST /v1/approvals/<编号>`，请求体 `{"decision": "allow" 或 "deny"}` | 回答一个授权，也支持仍有效、`remote_actionable: true` 的电脑端审批。没有该授权（或 Runtime 重启过）返回 `404`；已回答、只能提醒、过期或其他失效请求返回 `409`。 |
| `POST /v1/conversations/<名字>/history`，请求体 `{"turns": [{"id", "text", "reply", "at"?}]}` | `{"accepted": n}`：这些轮次（最多 30 轮）里有几轮是这台 Runtime 之前不知道的。它们会随下一句话告诉接话的代理。 |
| `POST /v1/conversations/<名字>/reset` | 这个对话从头开始：所有代理的会话都忘掉，对话记录清空。 |
| `GET /v1/firmware` | `{"rev", "versions": […], "target", "device", "log"}`：固件仓库的清单，版本从新到旧。见[设备固件](#设备固件)。 |
| `GET /v1/firmware/target?wait=<秒>&rev=<n>` | 现在该推给设备的那一版，一层的对象：`id`（没有时是空串）、`seq`、`build`、`size`、`sha256`、`note`、`reason`、`attempts`、`notice`、`notice_at`、`rev`。带 `rev` 时清单一有变化就返回。 |
| `GET /v1/firmware/<编号>/image` | 这一版的应用镜像（二进制）。 |
| `GET /v1/app` | 这台电脑上发布的手机 App 更新：`available`、`version_code`、`version_name`、`sha256`、`size`、`published_at`、`note`。 |
| `GET /v1/app/apk` | 这个更新的安装包。 |
| `POST /v1/reports`，请求体 `{"origin", "session_id", "state"?, "text"?, "upstream_id"?, "title"?, "role"?}` | 不在这台电脑上的会话（claude.ai 网页版、云端会话、别的电脑）上报进展（0.9.0 起）。每个 `origin` + `session_id` 一张只读卡，和电脑端观察到的会话放在一起；`text` 按 `upstream_id` 去重；`state` 是 `working`、`waiting`、`turn_ended`、`done`、`failed`；十分钟没再报的“在做”显示状态未知。不能从手机接着聊。命令行：`python3 -m xiaoyu_runtime report --origin claude-web --session <编号> --state working --text "…" --upstream-id <编号>`。 |
| `GET /v1/app/packs` | 资源包清单（0.8.3 起）：`{"packs": [{"name", "published_at", "files": [{"path", "sha256", "size", "download_url"}]}]}`。手机比对已装好的文件，只下载校验值变了的。 |
| `GET /v1/app/file?sha256=<校验值>` | 资源包里的一个文件（0.8.3 起），ETag 是校验值，支持单段 Range 续传；不在现在发布的包里时 409。 |
| `POST /v1/firmware?note=<备注>&push=1`，请求体是固件文件 | `201` 和这一版的记录；`push=1` 同时把它设为要推的。 |
| `POST /v1/firmware/target`，请求体 `{"id": 版本或 null, "reason"?, "force"?}` | 指定（或取消）要推给设备的那一版；返回同 `GET /v1/firmware/target`。 |
| `POST /v1/firmware/device`，请求体 `{"event", "build", "state", "prev", …}` | 手机 App 转告设备的情况。`event` 为 `connected`、`progress`、`installed`、`failed` 或 `unsupported`；返回同 `GET /v1/firmware/target`。 |

启用[电脑端同步](#同步已有电脑端对话)时，`/v1/cards` 和 `/v1/feed` 还会在请求的对话里
包含跨对话可见的电脑端观察卡。它们带有 `source: "desktop"`、`monitoring_only: true`、
`project`、`session_id`、`desktop_status`、`observed_at` 和 `stale`，以及
`content_scope: "recent"` 和 `content_truncated`。有大小上限的 `entries` 保存实际提出的
需求（`role: "you"`）和来源代理的可见输出（`role: "xiaoyu"`，App 上标注 Codex 或
Claude）。`entries` 为空表示尚未采集到可见内容。电脑端的 `waiting` 本身只是状态信号。
可选审批桥可以提供独立的审批，是否可远程回答由 `remote_actionable` 决定；停下的会话可以用
`POST /v1/messages` 带 `card`（这张观察卡）和 `pin: true` 接着聊（见上文），取消仍在电脑端完成。

### 消息落盘与重启

0.7.0 起，收下的每句话先写进 `state/messages.json`，再回 `202`；状态变了（开始处理、处理完、
没成）也写。正在生成的那段回复变得太快，不落盘。Runtime 重启后：

- 还在排队、没开始处理的打字消息接着做；
- 已经在处理的、或者还没识别的录音，不知道做到了哪一步，不自动重做：标成没做完（`failed`），
  并在对话里留一张卡，写着原来那句话和“Runtime 重启了，这句话没处理完，可以再说一次”；
- 回复其实已经记在卡上、只是消息记录没来得及写完的，记成 `done`，不会误报没做完；
- 序号不会往回走（文件里预先占下一段号，重启后从那段的末尾往后数，会跳过一些号），客户端
  手里的 `after` 仍然有效；
- 拿着原来的消息编号来问、带原来的 `client_id` 重试，都还是那一条。

消息写不进盘（比如磁盘满了）时返回 `503`，这句话不算收下，可以用同一个 `client_id` 再发。
写盘先刷到磁盘再换名，断电时要么是旧的一份，要么是新的一份。

去重表比消息本身留得久（最近 2000 个 `client_id`）：消息被挤掉之后再重试，返回同一个编号、
`expired: true` 的一份最小记录，不会当成新的一句再做一遍。`messages.json` 坏了不会拒绝启动，
坏文件留在旁边的 `messages.json.broken`。

消息记录说的是“这句话小鱼接住了没有”，不是“这件事做完了没有”：小鱼答完或者把活派出去，
`status` 就是 `done`，后面的进展和结果在卡上，用 `/v1/feed` 等。只有别的 Runtime 转过来的
话（`remote` 代理）会等到那件事结束。所以只认消息记录的旧客户端（手机 App 0.5.0）配这一版
能发能收，但只看得到“交给 codex 了”这一句，看不到后台的结果。

消息记录包含 `id`、`client_id`、`conversation`、`kind`（`text` 或 `voice`）、`status`
（`queued`、`transcribing`、`running`、`done`、`failed`）、`text`、`reply`、`brief`、`mood`
（`idle`、`busy`、`ask`、`happy`、`oops`）、`error`、`created_at`、`finished_at`。语音消息的
`text` 在识别完成之前是空的。

这句话交给了谁，也在记录里：

- `asked`：调用方点名的代理，没点名是 `null`。
- `card`：这句话归到了哪张卡。处理完之前是调用方带来的那个编号。
- `agent`：接这句话的代理；交给了帮手时是那个帮手。
- `stage`、`helper`：0.4 留下的字段，小鱼接话的那几秒里可能有值，处理完都是 `null`。
- `rev`：一个计数，记录每变一次就加一。
- `events`：走过的步骤，每项是 `{"at", "kind", "agent", "text"}`。`kind` 为 `route`
  （交给了谁，`text` 是原因：`asked`、`mention`、`router`、`default`）或 `handoff`（交给了
  帮手，`text` 是小鱼那一句话）。

一张卡包含 `id`（`c1`、`c2`……）、`conversation`、`title`（统一任务名称：最多 200 字符，
从需求生成时最多 24 字符）、`title_source`（`upstream`、`task`、`request` 或 `fallback`）、
`title_truncated`、`state`、`agent`
（谁在做或做的；小鱼自己答的是 `null`）、`entries`（`[{"role": "you" 或 "xiaoyu", "text",
"at", "attachments"?}]`，最近 40 条）、`brief`、`mood`、`progress`（最近 5 行进展，每一轮重新开始）、
`started_at`、`edits`（补充或改过几次）、`approval`（正在等的那个授权的编号，没有是
`null`）、`queued`（设了并行
上限、正在排队）、`created_at`、`updated_at`、`finished_at`（交给过帮手的事是什么时候
结束的；还在做、或者是小鱼自己答的，是 `null`）、`seq`。
卡片顶层的 `attachments` 汇总这件事已登记的产物，见
[任务图片和可下载文件](#任务图片和可下载文件)。

同一个 `client_id` 再发一次，返回的是已有的那条记录，不会把这句话再处理一遍；所以客户端
没收到响应时可以放心重试。

```bash
TOKEN=...   # server.token 的值
curl -s -X POST http://127.0.0.1:8765/v1/messages \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"text": "你好", "client_id": "demo-1"}'
curl -s "http://127.0.0.1:8765/v1/messages/<id>?wait=60" -H "Authorization: Bearer $TOKEN"
curl -s "http://127.0.0.1:8765/v1/feed?after=0&wait=60" -H "Authorization: Bearer $TOKEN"
```

## 设备固件

设备可以[经蓝牙换固件](../docs/claude-pocket.zh_CN.md#经蓝牙换固件)。Runtime 所在的
这台电脑负责留着每一版，并且记着“设备现在该是哪一版”。真正把固件写进设备的是手机
App：它连着设备、又连得上这台 Runtime 时，发现两边不一样就取走镜像、经蓝牙传过去；
设备重启后它去认可新固件，并把结果报回来。

每一版的应用镜像原样存在 `<state_dir>/firmware/images/` 里，清单在
`<state_dir>/firmware/index.json`，**不会自动清理**（一版约 1.7 MB）。入库时会核对
镜像的校验和，并读出版本号、构建时间和 `build`（固件 ELF 的 SHA-256 前 16 位，设备
自己报的也是它）。每一版有一个按入库先后排的序号，说“第 7 版”就行。

```bash
python3 -m xiaoyu_runtime firmware list              # 有哪些版本；● 设备现在跑的，→ 等着推的
python3 -m xiaoyu_runtime firmware status            # 设备上是哪一版、在等什么、最近的经过
python3 -m xiaoyu_runtime firmware fetch --push      # 取 GitHub 上最新构建的一版，入库并推给设备
python3 -m xiaoyu_runtime firmware add 文件.bin       # 入库一个固件文件（应用镜像或合并镜像）
python3 -m xiaoyu_runtime firmware restore 5         # 把设备换回第 5 版
python3 -m xiaoyu_runtime firmware restore previous  # 换回设备另一个槽位里的上一版（几秒钟，不用重传）
python3 -m xiaoyu_runtime firmware cancel            # 不推了
python3 -m xiaoyu_runtime firmware remove 3          # 从仓库里删掉第 3 版
```

“版本”可以写序号（`7` 或 `#7`）、编号的开头几位、`latest`、`current`（设备现在跑的）
或 `previous`。`push` 和 `restore` 是一回事。这些命令和正在运行的服务用的是同一份清单，
不用重启服务。

`fetch` 从 `firmware.repo` 的发布里取固件：不带参数取最新的，带标签取那一个，
`--commit <提交>` 只要从那个提交构建出来的（`HEAD` 表示 `firmware.source_dir` 现在所在
的提交）。`--wait 900` 表示还没构建出来就等，最多 900 秒；这个提交的构建要是已经失败
了会马上停下。`--detach` 把等和取放到后台，命令马上返回，日志在
`<state_dir>/firmware/fetch.log`。

找构建用的是 `git ls-remote`（每个构建是打在它那个提交上的一个标签），找到后直接下载，
所以等构建不占 GitHub 接口每小时的查询次数。只有“这个构建是不是已经失败了”这一问要
走接口，最多每 90 秒问一次。这台电脑没有 `git` 时全部走接口：没登录每小时只让查
60 次，设置环境变量 `GITHUB_TOKEN` 可以放宽。

这台电脑装了 ESP-IDF 的话，可以配置 `firmware.build_command`（例如
`["bash", "-lc", "source ~/esp/esp-idf/export.sh && idf.py build"]`），然后用
`firmware build --push` 在本机构建并推送，不用等 GitHub。

几条保护：

- 装上之后**不能再经蓝牙换**的版本（加这个功能之前的固件）默认不让推，要 `--force`。
- 同一版连着两次没装成就不再自动重推，等人来看；`status` 里有原因。
- 取固件没取到（构建失败、超时）和没装成，都会记在经过里，手机 App 的“设备固件”
  那一行会显示。
- 镜像没有签名。能访问这台 Runtime 的接口（也就是知道令牌）的人，可以让手机把任意
  镜像推给设备。

### 说一句话就改界面

把 `config.example.json` 里叫 `tailor`（裁缝）的那个代理打开，并把 `firmware.repo` 和
`firmware.source_dir`（写 `..`，也就是这个仓库）填上。它是一个只干活的 `claude_code`
代理，工作目录是固件仓库，按
[`skills/pocket-screen-update`](../skills/pocket-screen-update/SKILL.zh_CN.md) 做事：
改界面代码、跑检查、在本地提交，然后在本机编译并推给设备（这台电脑用
`tools/install_idf.sh` 装了 ESP-IDF、配置了 `firmware.build_command` 时；这样不需要
GitHub），否则推送到 GitHub、让 Runtime 去等那边的构建。对设备
说“@裁缝 把顶上那条改细一点”，或者只说想改什么、让小鱼自己决定交给它。“换回上一版”
也归它。

要先具备的条件：这台电脑上的仓库能 `git push`；`tailor` 的 `allowed_tools` 里有
`Bash`，等于允许它在这个仓库里运行命令，这是这件事需要的，但请只在你自己的电脑上打开。
从说完话到设备开始换，要等 Claude Code 改完（一两分钟）加上 GitHub 构建（几分钟）；
这期间这个对话在等它改完，构建和推送在后台进行。

## Windows

Runtime 可以跑在 Windows 10 及以上，需要从
[python.org](https://www.python.org/downloads/windows/) 安装 Python（安装时勾选
“Add python.exe to PATH”），并原生安装 Claude Code。在 PowerShell 里：

```powershell
irm https://claude.ai/install.ps1 | iex        # 安装 Claude Code；装完新开一个窗口
claude                                         # 登录一次，/status 确认后退出
git clone -b feature/pocket-hub-app https://github.com/yanyuliu01/ai-passport.git
cd ai-passport\runtime
copy config.example.json config.json
python -c "import secrets; print(secrets.token_urlsafe(32))"   # 填进 server.token
python -m xiaoyu_runtime --config config.json --once "你好"
$env:XIAOYU_HOST = "0.0.0.0"; python -m xiaoyu_runtime --config config.json
```

命令是 `python`，不是 `python3`。服务第一次在网络上监听时，Windows Defender 防火墙会问
是否允许 Python；要在专用网络上允许，否则手机连不上。`config.json` 要保存成不带 BOM 的
UTF-8（记事本里选“UTF-8”，不要选“带有 BOM 的 UTF-8”）。

语音部分照[语音](#语音)一节的步骤在 PowerShell 里做即可，把 `python3` 换成 `python`，
`curl` 换成 `curl.exe`。

## 多台 Runtime，一段对话

可以在每台电脑上各跑一个 Runtime，在手机 App 里选由哪一台回答。Claude Code 把每段对话
存在发生它的那台机器上，对话本身搬不走。所以改由手机记着最近的几轮，换到哪台就交给哪台：

1. 每次发消息之前，App 把最近的几轮（最多 12 轮）发到 `/v1/conversations/<名字>/history`。
2. Runtime 跳过自己答过的和已经收到过的，其余的先存着。
3. 这个对话的下一句话到来时，模型会在主人的话前面看到这几轮，只看到一次，并被告知它们
   发生在别处。

带得过去的是“说了什么”：主人的话和小鱼的完整回复，转告模型时每轮分别截到 500 和 1000 个
字。带不过去的是那边会话知道的其他一切：读过的文件、工具的输出，以及比手机还记得的那几轮
更早的内容。

配套 App 最多配对八台 Runtime，并独立监控各台待回答的审批，即使当前聊天选中了另一台
电脑也一样。每台电脑独立请求 feed，一次最多等待 50 秒。App 按创建时间合并请求，并把
来源 Runtime 固定在请求上；手机或设备的答案回到原来的电脑，不会跟着当前选择走。
任务列表仍显示选中的 Runtime。点通知查看来源审批，不会改掉当前聊天；查看来源任务则
由主人明确切换电脑。

某台电脑断线时，会移除该来源尚未核实的审批和通知；重连后从序号 0 获取当前仍待回答
的请求。网络失败按 2 到 30 秒间隔重试。监控需要手机的前台 Runtime 连接服务，
Android 13 或更新版本的通知提醒需要通知权限。配对另一台电脑不会把附件快照复制过去：
文件 ID 和缓存内容保留来源 Runtime 归属，对话历史接口只传文字。

另一种接法是不换台：手机只连一台 Runtime，把另一台电脑登记成它的一个 `remote` 代理。
这样“那台电脑上的事”由小鱼转交过去，或者由主人点名，对话记录只在主的那一台上。已经
被别的 Runtime 转过手的话不会再往外转，所以两台互相登记也不会来回踢。

### 小鱼网络：每台 Runtime 都有全部记录（0.10.0 起）

上面两种接法都靠手机。小鱼网络让 Runtime 之间直接同步：在哪里拉起的 Runtime、手机正好
连上的是哪一台，看到的都是同一段对话、同样的任务、电脑会话和授权。同一个主人的每台
Runtime 写同一个口令，再写上网络里至少一台的地址：

```json
"mesh": {
  "secret_file": "~/.config/xiaoyu/mesh-secret",
  "peers": ["https://home.tail1234.ts.net"],
  "advertise": ["https://laptop.tail1234.ts.net"]
}
```

- `secret`（或 `secret_file`、环境变量 `XIAOYU_MESH_SECRET`）：至少 16 个字符，每台一样。
  没有就不加入。小鱼的 `subject_id` 由它算出，所以每台报的主体都相同。
- `peers`（或 `XIAOYU_MESH_PEERS`，逗号分隔）：先去找谁。连得上一台就够了，其余的互相转告。
- `advertise`（或 `XIAOYU_MESH_ADVERTISE`）：别人从哪里连这一台。不写就按 `server.host` 推；
  只监听 `127.0.0.1` 的不对外说地址，照样能加入：它主动连出去，推自己的记录、拉别人的
  （云上的沙盒、别人的网络里都能用）。
- `home`（`true` / `false`，默认 `false`）：主对话归这台负责。网络里只给一台写 `true`。
  有了负责人以后，别的电脑收到主对话里的话就转给它，它连不上时排队、不替它答；旁聊归创建
  它的那台。看和移交：`GET` / `POST /v1/conversations/<名字>/owner`（`{"to": "<电脑名字>"}`，
  两台都在线、这段对话空闲时才交）。

设备和权限：配置了 `home` 的那台也是权限权威。每台设备可以有自己的凭据：`POST /v1/devices`
（`{"name","kind"}`）回一次性凭据 `dev.<编号>.<密钥>`，之后用它代替共享令牌；`POST
/v1/devices/<编号>/revoke` 撤销，`POST /v1/mesh/members/<编号>/remove` 把一台电脑移出网络。回答
授权、增删设备和电脑、移交负责人：权威不在线时拒绝。`GET /v1/info?nonce=<随机数>` 回 `host.proof`
（用这次凭据的哈希做的 HMAC），客户端在任何线路上都能核对连到的是持有它凭据的 Runtime。
旧的共享令牌在过渡期照常可用。

交换接口 `POST /v1/mesh/exchange` 用网络口令，不用手机令牌；`GET /v1/mesh`（手机令牌）列出
网络里有谁、多久前联系上。成员之间长轮询，一般一秒内就同步到。

每台看到的样子：别的 Runtime 的事是这里的一张本地卡（`cN`，带 `mesh_origin`、`mesh_id`、
`origin_name`），它的消息编号前面加上产地编号的前 12 位，它的授权和本机的一样显示。记录
只由生成它的那台改，所以没有两边同时改的冲突；连不到产地的成员也能经别的成员拿到。每台
的小鱼接话前都会被告知别的电脑上说过什么。

在任意一台都能做的：在别的 Runtime 的事里说话、取消它、回答它的授权，会变成指令交给那台
去做（同一句话两边编号相同，只显示一次）。小鱼也能把事交给别的 Runtime 上的帮手，名字是
`帮手@那台的名字`，比如 `@codex@Mac 看看日志`；那台替这件事开的卡记着 `delegated_from`，进展
和结果回到这台的卡上。五分钟联系不上的成员算离线：它没做完的事显示看不到（`unavailable`），
它的帮手不再给小鱼选，交给它的话会说明原因被拒，不会悄悄丢掉。十分钟内没送到的指令作废。

不共享的：订阅额度（`/v1/usage`）、固件和 App 发布、附件文件（别的 Runtime 的事的附件会
列出来，但内容留在那台）。

**设置、文件夹和电脑会话记录（0.11.0 起）。** 另外三样也作为网络记录同步，离线的电脑重新
加入时会补上：

```json
"mesh": {
  "secret_file": "~/.config/xiaoyu/mesh-secret",
  "share": {
    "env": ["DEEPSEEK_API_KEY"],
    "folders": {"claude-memory": "~/.claude/projects/-Users-me-ai-passport/memory"}
  }
}
```

- `share.env`：只共享这里点名的，默认一个都不共享。值放在 `~/.config/xiaoyu/shared/<名字>`
  （只有本人可读；第一次从 Runtime 的环境里取），代理通过 `env_files` 读，比如
  `"env_files": {"DEEPSEEK_API_KEY": "~/.config/xiaoyu/shared/DEEPSEEK_API_KEY"}`。没点名的 Runtime
  不会写它。
- `share.folders`：一个名字对应这台电脑上的一个目录；同一个名字在每台电脑上可以是不同的路径
  （Claude Code 的记忆目录随用户名和系统不同）。只传文字文件，单个不超过 512 KB，每个目录最多 300 个。
- 两样都以最后写的为准（时间相同按 Runtime 编号），删掉也会同步。目录本身不在时不同步，
  免得把别处的文件当成被删了。
- 电脑会话记录：开着 `desktop_sync` 时，每台把最近的 Claude Code、Codex 会话的完整可见内容
  （每个会话最多 500 条、512 KB，去掉工具输出、思考过程和疑似密钥）导出到 `state/history/`。
  别的成员按电脑各存一份只读副本，放在 `state/mesh/history/<那台>/`，电脑端共享历史的 Hook 会在
  开会话时告诉 Claude Code、Codex 这些文件在哪。

## 语音

语音消息就是一段录音：16 位单声道 WAV，采样率 8 到 48 kHz，时长 0.2 到 120 秒，最大
4 MB。Runtime 先检查格式，把消息排进队列，轮到它时识别成文字并删掉录音，之后和打字的
消息走完全相同的流程。识别结果为空时，这条消息以“没听清”失败，不会把空内容交给模型。

有两种引擎。

**`sense_voice`** 通过 [sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx) 在本机运行
SenseVoice 模型，录音不离开这台机器。模型在收到第一段录音时加载（几秒），之后常驻内存，
约占 250 MB。

```bash
python3 -m pip install sherpa-onnx        # 要用运行 Runtime 的同一个 Python
mkdir -p stt-models && cd stt-models
curl -L -O https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17.tar.bz2
tar xjf sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17.tar.bz2 && mv sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17 sense-voice && cd ..
```

然后设置 `"stt": {"engine": "sense_voice", "model_dir": "stt-models/sense-voice"}`，
用自己的录音或模型自带的样例检查一下：

```bash
python3 -m xiaoyu_runtime --config config.json --stt stt-models/sense-voice/test_wavs/zh.wav
```

下载约 160 MB。`stt-models/` 已被 Git 忽略。

**`command`** 运行你指定的任意程序，比如换一个模型或接一个云服务：
`"stt": {"engine": "command", "command": ["my-stt", "{audio}"]}`。`{audio}` 会被换成 WAV
文件的路径，命令在配置文件所在的目录里运行，它打印到标准输出的内容就是识别结果。退出码
不为零时这条消息失败，并报告标准错误的最后一行。

`stt.engine` 为 `none` 时，语音消息会被拒绝并说明原因；打字的消息不受影响。

## 和手机 App 配对

```bash
XIAOYU_HOST=0.0.0.0 python3 -m xiaoyu_runtime --config config.json   # 在局域网上监听
python3 -m xiaoyu_runtime --config config.json --pair                 # 打印 http://<地址>:<端口>#<令牌>
```

把打印出来的那一行粘贴到小鱼中枢 App 里（见
[`companion/README.zh_CN.md`](../companion/README.zh_CN.md)）。这一行里带着令牌，要像
密码一样对待。手机要和这台机器在同一个网络里，而且传输是不加密的 HTTP，只在你信任的
网络里这样用。`--pair` 给出的本机地址是猜的；机器有多块网卡时，确认一下那是手机连得到
的地址。

### 在任何网络下连上 Runtime（Tailscale Funnel）

电脑和手机经 Tailscale 打不通直连时（比如电脑所在网络从海外出口、手机用移动数据），
流量会绕远处的中继，App 时常掉线。Tailscale Funnel 不用服务器、不用域名，就能给 Runtime
一个公网 HTTPS 地址：

1. 给这个 tailnet 开启 Funnel 和 HTTPS 证书（第一次运行 `tailscale funnel` 会打印链接）。
2. `server.host` 写 `tailscale` 时，运行 `tailscale funnel --bg http://127.0.0.1:8765`。
   macOS 上的 Funnel 转不进本机自己的 Tailscale 地址，所以转给 Runtime 的本机监听。
3. 在 App 里添加 `https://<机器名>.<tailnet>.ts.net#<令牌>`，手机不用再开 Tailscale。
   这样 Runtime 能从公网访问，只靠令牌保护，传输是 HTTPS；关掉用
   `tailscale funnel --https=443 off`。

feed 会 gzip 压缩并按 48 KB 分批发，慢线路上也能一点点收完。

### 经 Runtime 给手机 App 推更新

电脑上留着最新发布的一版安装包；手机经 Runtime（任何地址，包括 Funnel）取走，校验
SHA-256 后交给安卓安装，安装要主人在系统界面上点确认：

```bash
python3 -m xiaoyu_runtime app publish companion/android/app/build/outputs/apk/debug/app-universal-debug.apk --note "改了什么"
python3 -m xiaoyu_runtime app status
```

增量更新（0.8.3 起）：很少变的大文件（语音模型）不进安装包，用
`python3 -m xiaoyu_runtime app pack <名字> <目录>` 发布成资源包，按文件 SHA-256 存放和提供，手机只下载变了的。
`app publish` 和 `app pack` 默认先上传 GitHub Releases，上传成功后才在本机发布。
所有提供的 APK 都有公开下载地址；`--github ABI,...` 可限定架构，`--local-only` 明确跳过
上传。APK 使用 `app-<version>-<code>` 标签和带内容哈希的文件名；资源文件共用 `app-packs`
发布，未变化的不重传。令牌来自 `GITHUB_TOKEN`／`GH_TOKEN` 或 git 的 GitHub 凭据，只发给
GitHub。只上传文件，不推送 Git 提交；失败保留之前的本机发布。自选已有下载地址时加
`--local-only`。

Runtime 0.12.0 新增 `task_activity`：把帮手公开派发、回复、工具开始／结果和轮次结束持久
保存为任务范围的 `kind=activity`，经 `/v1/sync` 回放，也参与 mesh 复制。Claude 流式 JSON、
Codex app-server 与 DeepSeek ACP 采集来源提供的事件；普通命令适配器无法展示其内部步骤。
记录不占用卡片对话条目，也不受最近 5 行进展限制。最多保留 2000 条，与 5000 条聊天额度
分开；识别出的凭据会脱敏，单条限制 4000 字并标注截取。旧 App 忽略不属于任务成员的事件，
旧 Runtime 只提供最近进展；已经丢弃的历史步骤无法补回。
后续受管理 CLI 安装见 [gateway 方案](../docs/development/agent-gateway.zh_CN.md)，本版尚无安装 API。

版本号从 apk 旁边 Gradle 写的 `output-metadata.json` 读（或用 `--version-code`）。版本号更大，
或版本号相同但安装包不同、而且发布得比手机上那份装得晚时，App 提示更新。打开 App 时会问一次
（最多十分钟一次），也可以在「连接与设置 → App 更新」里手动查。带这个功能的第一版要手动装。

App 0.13.1 增加架构选择和断点续传。默认发布通用包，并用 `--variant ABI=APK` 提供
Gradle 输出的同版各架构包，见[App 发布流程](../companion/README.zh_CN.md#发布一次-app-改动开发者和-ai-agent)。
私有 Runtime 上的旧手机都已确认是 ARM64 时，可将 ARM64 包设为默认，并同时提供通用包
和其他架构包；未知的旧手机仍应使用通用默认包。

`GET /v1/app?abi=arm64-v8a` 仍以普通版本、大小和校验值字段返回选中的包；没有对应
架构时回退到通用包。不带 `abi` 则保持发布时的默认包。通过
`GET /v1/app/apk?abi=arm64-v8a&sha256=<expected-hash>` 下载；该版本已被替换时返回
409，不会混入不同版本。单个字节范围返回 206、`Content-Range`、`Accept-Ranges` 和 ETag；
非法范围返回 416。If-Range 不匹配时返回完整文件，If-Match 不匹配时返回 412。
所有请求继续要求 Bearer 鉴权。以哈希命名的不可变包避免正在传输的文件被替换；存储最多
保留当前和上次发布的哈希包。旧的 `app.apk` 文件名继续供旧 Runtime 进程使用。新 App
兼容忽略 Range 的旧服务端。这些改动减少传输量与重复下载，不能消除 Funnel 或 DERP
中继的线路瓶颈，仍需在手机实际链路上测速。

从 App 0.14.0 起，发布时可以用 `--download-url HTTPS_URL` 指定默认安装包的外部地址，
各架构用 `--variant-download-url ABI=HTTPS_URL` 分别指定。先上传与本地验证过的文件完全一致的
安装包；版本、文件大小与 SHA-256 仍由 Runtime 提供。App 不向外部地址发送 Runtime 令牌，
仅接受 HTTPS 跳转，并在续传后核对完整哈希。没有配置地址的架构继续从 Runtime 下载。
App 和 Runtime 的包体上限为 512 MiB；旧版 App 仍有 128 MiB 限制，首次大包升级需要浏览器安装。
此配置只改变安装包下载，不改变对话和录音的传输目标。

## 自动迭代和模型网关

Runtime 可以收集手机 App 的使用信号（`POST /v1/app/events`）、对话里的抱怨、失败的事和回复耗时，用规则检测器整理成问题簿，每轮选一个问题，让模型写一份经过校验的小迭代方案。调模型都经过 `model_gateway.py`：优先用本机端点（Ollama、llama.cpp、LM Studio、vLLM），联网端点只在每天预算内使用。每一步都是插件。改代码、门禁、自动发布还没做。见[自动迭代设计](../docs/development/auto-iteration.zh_CN.md)；命令：`python3 -m xiaoyu_runtime iterate --help` 和 `python3 -m xiaoyu_runtime models --help`。

## 一句话是怎么走的

1. 消息按对话排队：同一个对话里一句一句处理，不同的对话互不等待。
2. 按[这句话交给谁](#这句话交给谁)的顺序看有没有指定由谁做。指定了：开卡，交到后台，
   这句话就算接完了。
3. 没指定：替小鱼说话的代理接这句话。对 Claude Code 是
   `claude -p --output-format json --append-system-prompt <人设 + 帮手清单 + 现在的事>
   --json-schema <reply、brief、mood、card、action> --tools "" --permission-mode dontAsk
   [--model ...] [--effort <主人或调用方点的档>] [--resume <会话>]`。主人的话从标准输入送进去，不会出现在命令行参数里。
   Runtime 照她回复里的 `action` 办，见[一件事一张卡](#一件事一张卡)。
4. 后台的事：对 Claude Code 是 `claude -p --output-format stream-json --verbose
   [--append-system-prompt <人设> --json-schema <reply、brief、mood>] --permission-mode <模式>
   --permission-prompt-tool mcp__xiaoyu__approve --mcp-config <state 里的临时文件>
   --add-dir <目录> [--allowedTools ...] [--model ...] [--effort <档>] [--resume <这件事的会话>]`。启动目录
   仍然是 `workdir`。Runtime 逐行读它的事件：一开始就记下会话编号，每一步操作记一行进展。
   取消或改要求时，它和它起的所有进程一起结束（Claude Code 跑命令时会另开会话，所以是按
   父子关系找出整棵树来结束的）。Runtime 自己被 `kill` 时也会先这样收尾。
5. 会话编号存到 `state/sessions.json`：小鱼接话用的按“对话 + 代理”，后台的事按
   “对话/卡 + 代理”。
6. 每句话和每个后台结果都记进小鱼自己的对话记录 `state/transcript.json`，并记下替她说话
   的代理听到了没有。没听到的（后台的结果、指定给别人的话、手机从别处带来的），下次它
   接话时先告诉它，只说一次。

对话记录属于小鱼，不属于任何一个代理：换一个代理接话，小鱼仍然记得刚才说过什么。
各个代理自己的会话里只有它参与过的部分。

## 跑在别的机器或虚拟机上

- 把这个目录拷过去，建好 `config.json`（或设置环境变量），并在那台机器上登录
  Claude Code。会话编号只在本机有效：Runtime 搬家，之前的对话不会跟着走。
- 服务本身只有明文 HTTP。让它监听 `127.0.0.1`，再通过你自己掌握的加密通道访问
  （私有组网、SSH 隧道，或带 TLS 的反向代理）。直接监听公网地址不安全。
- 已接受的消息和请求去重记录保存在 `messages.jsonl`，`messages.json` 是检查点。
  重启后查询原消息编号；排队中的文字消息继续处理，已经开始但结果不明的工作先核对，
  不自动重做。帮手输入遵循上面的恢复规则。

## 哪些验证过、哪些没有

附件主机测试覆盖鉴权流式下载、源文件删除、重启后保留、符号链接/凭据/FIFO 排除、
存储上限和安全错误元信息。替身代理测试覆盖任务清单、产物绑定最终回复，以及长回复
全文保留和 JSON 包装解开。电脑端审批测试覆盖一次性决策、过期/失效的所属进程、重复
回答、没有决策通道的通知，以及跨对话返回审批。这些是本机测试；真实手机的下载、系统
文件打开、后台通知及已信任的电脑 Hook 操作，仍需在目标电脑和手机上完成端到端检查。

`runtime/tests/test_runtime.py`（`./tools/validate.sh --static` 会运行）验证了：配置
检查和旧写法的兼容；交给假的 `claude`、`codex` 可执行文件的完整命令行和标准输入、结果与
错误的解析、会话续接；点名和路由的先后顺序、用一条命令做路由器；卡的存取、序号、落盘
和重启后把旧格式中没做完的标成失败；小鱼直接回答、交给帮手后立刻能接下一句、几件事同时做、
补充、改要求（包括真的停掉一个进程再重跑）、取消（包括另开了会话的孙进程）、并行上限、
授权的登记、等待和回答、权限询问工具本身和那个只听本机的入口、交给假的 Claude Code 时
从询问到主人回答再到文件写或不写的整条路、Codex 的 app-server 模式（对着替身：一轮、
接着做、三种询问、中途追加、打断、各种失败）、帮手失败或不存在、办不了
的事重说一次、只干活的帮手做完后的转述、补上她没听到的对话；两台真的 Runtime 之间的
`remote` 代理（用 `echo`）以及互相登记时不会来回转；按对话排队、按 `client_id` 重试、
令牌校验、HTTP 接口（包括 feed 的长轮询）；以及用一条假的识别命令跑的语音消息（格式
检查、识别、识别为空和识别失败、录音的清理）。

0.6.0 的档位和用量，测试验证了：配置里的档位和可换的模型怎么读、哪些写法被拒绝；交给
假的 `claude`、`codex` 时命令行里的 `--effort`、`model_reasoning_effort` 和 app-server 的
`effort`；点名之后的档位词；它们报出来的模型、额度和 token 怎么收；三种来源各自的换算；
按账号记、过期清掉、落盘和重启；对着假的 Claude Code 和假的 app-server 真的问一次（包括
不是订阅、版本太旧、没有登录这几种问不到的情况）；一轮开始和结束之间的差值；小鱼的提示
里有没有那几行；她选的档、调用方选的档、沿用上一次的档怎么到帮手手里和卡上；改要求时
换档会重做；feed 里的用量和用量变了提前返回。

2026-10-10 在云端工作区里对着真的 Claude Code 2.1.294 和 Codex 0.162.0 看过（都没有订阅
登录）：`claude --help` 里有 `--effort <level>`（low、medium、high、xhigh、max）；一件带
`--model sonnet --effort medium` 的事正常做完，事件流开头报了实际的模型，结果里的 token
记到了卡上；`get_usage` 不发提示也有回答，1 秒之内，回的是“这个登录没有额度可看”；Codex
的协议定义里 `turn/start` 有 `effort` 和 `model`，`thread/start` 的结果里有 `model`，没有
登录时 `account/rateLimits/read` 回的是要先登录，带 `effort` 的一轮被它收下了（之后因为
没有登录而失败）。

**没有验证的**：订阅登录下 `get_usage` 和事件流里实际的数（形状是照 Claude Code 2.1.294
自带的定义写的，没有见过真的回答）；Codex 订阅登录下的额度；它们和各自官方界面里的数
对不对得上；`d5`、`d7` 在真实任务上有没有参考价值；替小鱼说话的模型会不会照着提示选档、
看着额度派活（这只能在真的对话里看）；在同一个会话里换模型或换档，Claude Code 和 Codex
会不会不认。

2026-10-09 用 Claude Code 2.1.295 在 Linux 上手动验证过 0.5 这一版（是在云端工作区里，
不是在预期运行的那台电脑上），配置是一个放行了 `Bash` 的 `claude_code` 代理，通过 HTTP
接口：

- 第一句“跑一下 sleep 20，然后数一数 /etc 下面有多少个 .conf 文件”：7.6 秒时这句话接完，
  小鱼说交给 Claude Code 了，卡是 `working`。
- 紧接着第二句“一打鸡蛋是几个”：13.7 秒时答完（“12 个”，还提到那件事还在后台跑），
  在自己的一张卡上。这时第一件事还在做。
- 37 秒时 feed 给出第一张卡变成 `done`，内容是数出来的结果。
- 小鱼接一句话约 6 到 7 秒，大部分是 Claude Code 自己启动和回答的时间。

同一天，同样的环境，默认的 `manual` 模式（只预先放行了读文件的几个工具），通过 HTTP 接口：

- 让它往一个文件里写一行字：先后来了两个授权（一条 `Bash` 命令、一次 `Write`），卡变成
  `waiting`，feed 里有内容原文。两个都答 `deny`：文件没有被写，小鱼照实说没写成。同一个
  授权再答一次得到 `409`。
- 再说一次并都答 `allow`：文件写出来了，内容正确；`state/` 里没有留下临时文件。
- 单独试过让一个授权等 100 秒再回答，Claude Code 一直等着，之后照常继续。
- 改要求：先让它 `sleep 45` 再读一个文件，9 秒后说“不用 sleep 了”。小鱼选了那张卡和
  `redo`；Claude Code 被停掉，用同一个会话接着做，约 10 秒后给出结果，没有等那 45 秒。
- 取消：让它 `sleep 60`，然后说“算了不用跑了”。小鱼选了那张卡和 `cancel`；Claude Code
  和它起的 `sleep` 都结束了。`kill` 掉 Runtime 时正在做的事也一样被结束。
- 途中发现并改掉的两件事：模型常把卡的编号 `c3` 写成 `3`（现在认）；Claude Code 跑命令
  时会另开会话，只结束进程组留得下 `sleep`（现在按父子关系结束整棵树）。
- 这个云端环境会让所有 `claude -p` 共用同一个会话编号，除非用干净的环境变量启动
  （`env -i HOME=$HOME PATH=$PATH`）。上面这些是在干净环境里做的；早先“并行”那一段不是。

同一天稍早，0.4 版用 Claude Code 2.1.295 在 Linux 上手动验证过（同样是在云端工作区里），
配置是一个 `claude_code` 代理加一个 `command` 类型的替身帮手。那一版里帮手是在同一轮里
做完的，下面的时间不适用于现在：

- 直接回答：一轮约 5 秒，第二轮能接上第一轮的内容。
- 小鱼自己决定转交：一条“找个审查代码的看一下”的请求，Claude Code 按结构给出了
  转交，Runtime 运行了帮手，再由它总结；它发现替身帮手答非所问，并如实告诉了主人。
  通过 HTTP 接口轮询时能看到 `agent` 和 `stage` 的变化。这一轮约 18 到 21 秒。
- 点名只干活的代理（`@名字`）：帮手先做，结果由 Claude Code 转述，约 6 秒。
- 一份 0.3 写法的配置和会话表照常读入，原来的会话编号算在 `claude` 名下。

2026-10-08 用 Claude Code 2.1.294 在 Linux 上手动验证过（是在云端工作区里，不是在
预期运行的那台电脑上）：

- `--once` 按要求的结构给出了回复、简报和表情；第二轮能说出第一轮里的细节，说明
  续接对话可用。
- 通过 HTTP 接口：不带令牌的请求得到 `401`；一条“请别的工具给个第二意见”的请求，
  让模型调用了一个替身工具，并用自己的话总结了它的输出。（当时是 0.2 版，别的代理还是
  写在 `tools` 里、由 Claude Code 自己去运行。）
- 一轮简单对话端到端大约六秒。

2026-10-08 在 Linux 上用 sherpa-onnx 1.13 和 int8 版 SenseVoice 模型手动验证过，用的是
模型自带的普通话样例（5.6 秒）：

- `--stt` 打印出了这句话；加载模型约 3 秒，识别约 0.6 秒。
- 同一段样例先用在电脑上编译的固件语音编码器编码，再用手机 App 的解码器在电脑的 Java
  环境里解码，然后发到 `/v1/voice`。识别成功，和未压缩时的结果差一个字；故意每 15 帧丢
  一帧后仍能识别。

2026-10-08 在 Linux 上手动验证过：同一台机器上跑两个 Runtime，各自用真实的 Claude Code
和各自的会话，请求用的是手机 App 的代码（在电脑的 Java 环境里运行）。告诉第一个 Runtime 的
一件事，把对话带过去之后第二个能说出来；第二个说过的话，带回去之后第一个也能说出来。

2026-10-09 在 Linux 上（云端工作区）手动验证过固件仓库：`firmware fetch` 从这个仓库在
GitHub 上的真实发布里取回了 `firmware-build-2`，核对了发布里的 SHA-256，读出的版本号
就是构建它的提交；`--commit … --wait … --detach` 在后台取回了同一版；用本机编译的
应用镜像和合并镜像入库，读出的内容一致；`list`、`status`、`push`、`restore`、`cancel`、
`remove` 都按说明工作。`runtime/tests/test_firmware.py` 用手工拼出的镜像验证了解析、
清单、接口和命令行。

没有验证：

- 固件仓库和手机 App、真实设备一起使用：没有一版固件真的经蓝牙装到过设备上。
- `tailor` 代理：没有用真实的 Claude Code 跑过“改界面、提交、推送、等构建”这一整圈；
  这台电脑上能不能 `git push`、Claude Code 是否接受示例里的 `allowed_tools` 写法，都要在
  那台电脑上确认。
- `firmware build`：只用一条替身命令测试过，没有对着真实的 ESP-IDF 跑过。
- Windows：这个目录里的任何东西都还没有在 Windows 上运行过。
- 真实设备上的语音：麦克风音质、蓝牙吞吐，以及真实环境里真人说话的识别效果。
- macOS 上的 sherpa-onnx。

- 真实的 Codex 跑完一轮。`codex` 代理是对着 codex-cli 0.162.0 写的。app-server 模式：消息的
  形状来自它自己生成的协议定义；对着真的命令行实测过握手、开线程、开始一轮，以及没有
  登录时在限定时间内报出“一直连不上”。授权请求、中途追加、打断、一轮正常结束，都只对着
  一个按协议定义写的替身测过——云端没有登录的 Codex。`exec` 模式的命令行选项来自帮助
  输出，同样没有真正跑成功过一轮。
- `remote` 代理对着另一台真实电脑、经过真实网络的情况。
- 本地模型：`command` 类型能以小鱼的身份回话这条路只在测试里用脚本走过。
- 虚拟机，以及连续运行多天的情况。
- 0.5 这一版在 macOS 上、以及和手机 App、设备一起使用的情况。手机 App 0.5.0 和现在的
  固件还不认识卡：能发能收，但后台的结果看不到。配套的 App 和固件还没做。
- 模型会不会总是选对 `card` 和 `action`：只手动试了上面这几句。
- 主人很久不回答授权（上面最长试过 100 秒）。
- “没动静就问”在真的长任务上：云端用真实的 Claude Code 试过一次（时限调到 30 秒，让它跑
  `sleep 50`，30 秒时来问，答“不行”后停掉）。十分钟、三十分钟这两个默认值合不合适，
  Claude Code 长时间思考时会不会被误问，Codex 的 app-server 在命令运行时发不发消息，都
  没有在真实的长任务上看过。
- 一件事里同时来几个授权：权限询问工具一次只送一个，后面的排着。
- 很多件事同时做时，这台电脑和账号用量撑不撑得住。
- 帮手很慢或输出很长时，模型的表现。

已知风险：

- 这条路依赖用订阅登录非交互地运行 Claude Code。Claude Code 的文档里有一个跳过这种
  登录的 `--bare` 模式，并说明它将来会成为 `-p` 的默认行为；到那时 `claude_code` 代理需要换一种
  认证方式。把它提供给自己以外的人之前，请先确认 Claude Code 当前的使用条款。
- 每一次调用都计入所登录账号的用量限制。没指定由谁做的每句话都要先调用一次说话的
  代理；只干活的帮手做完后还要再调用一次来转述。并行不设上限，用量由主人自己掌握。
- 默认配置下，交给 Claude Code 的事能碰整个主目录，靠的是“该问的都问”。主人点了“可以”
  的操作就真的做了；设备上只显示内容的前 319 字节，长的要到手机上看全。
- 手机 App 0.5.0 和现在的固件不会显示这些授权：在配套的 App 和固件出来之前，等授权的事
  只能用 HTTP 接口回答，或者一直等到超时。
- 两件事同时改同一个文件夹时没有互相保护。
- 共享工作区只对着本机的替身接口测过（行的内容、重试、写不进去时落盘）；没有对着真的
  Notion 写过一行。只写不读：别的 agent 留在总线里的任务，Runtime 现在不会去取。
- 对话重新开始（reset）不会停掉正在做的事，也不会清掉卡。
- 只有一个共享令牌，没有按设备区分身份，也没有限流。

## 测试

```bash
python3 runtime/tests/test_runtime.py
python3 runtime/tests/test_dispatch_safety.py
python3 runtime/tests/test_dispatch_recovery.py
python3 runtime/tests/test_firmware.py
python3 runtime/tests/test_desktop_sync.py
python3 runtime/tests/test_desktop_content.py
python3 runtime/tests/test_desktop_titles.py
python3 runtime/tests/test_task_titles.py
python3 runtime/tests/test_desktop_hook.py
python3 runtime/tests/test_desktop_approvals.py
python3 runtime/tests/test_artifacts.py
python3 runtime/tests/test_artifact_delivery.py
```

## 主人图片输入

图片上传需在 Runtime 环境执行 `python -m pip install -r requirements-images.txt`。
鉴权上传和消息协议、支持的看图路由与兼容边界见[图片消息](../docs/development/multimodal-images.zh_CN.md)。

## 统一目录增量（2026-10-22）

Android 0.30.0 / 版本码 55、Runtime 0.22.0、桌面端 0.1.4 增加共享会话目录。
手机入口为 **工作记录 → 统一会话目录**，桌面入口为侧边栏的 **统一会话目录**。
普通短消息留在同一段主对话或旁聊内；受管目标与真实原生来源分别保留身份。
已经关联任务的来源默认折叠，避免同一件事重复列出。可按原名、展示项目和来源电脑搜索，
不会因同名或相同目录名合并记录。

带鉴权的 Gateway 提供分页 `GET /v1/directory?q=&cursor=`、
带 `fields` 与 `revision` 的 `POST /v1/directory/<uid>`、分页
`GET /v1/directory/<uid>/history`，以及显式的 `POST .../metadata` 和
`POST .../open`。来源端负责统一别名、置顶、归档、恢复和展示项目分类；
原名与真实执行目录分别保留。远端待处理操作不算已经生效，过期版本的编辑会被拒绝。
手机可预览迁移旧的本机置顶、归档；已有共享修改的记录保持原值。
旧版按对话同步和旁聊改名接口继续兼容。整理资料损坏时保留原文件，停止覆盖写入。

`python3 -m xiaoyu_runtime conversations list --query <文字>` 以及
`show`、`history`、`rename`、`pin`、`unpin`、`archive`、`restore`、
`project`、`metadata`、`open` 共用同一套带鉴权的接口；用列表返回的确切 `--id` 选记录。
两个桌面代理都可以使用 `shared_history.py --directory` 或
`--conversation <uid> --cursor <cursor>`。读取不会启动代理或模型、补发主人消息、
修改原生数据库。原有上下文钩子仍按有界、失败放行的方式提供历史证据。

启用原生同步后，会增量扫描可读取的 Codex、CC 来源目录，包括实时任务卡窗口之外的旧会话，
并报告索引积压和来源缺失。公开文字按有界页面读取，仍排除推理和原始工具输出。
权威消息日志压缩前，以相同 JSONL 格式封存被挤出的公开记录；离开实时卡片窗口的任务保留
权威快照，但不会重新变成可执行任务。先前已经删除的内容无法恢复；缺口、文件不可用、
文件替换导致游标失效均明确反馈，客户端可以重新读取。

执行尝试保留真实会话编号和原生配置目录范围。Codex app-server 提供轮次编号时会保存；
没有轮次边界的适配器标为范围不可用。一个来源可以包含多个目标，后续原生输入不会自动
算到旧目标，也不会重发进主聊天。目录是现有记录的展示投影，不是另一套可写任务或消息账本。

标题、摘要建议复用原有模型 Gateway，通过单个有界后台队列生成，保存来源、输入版本和
修订校验。只有显式配置 `conversation.metadata` 或 `conversation` 用途才启用可选推理；
不会代入通用付费默认路由或 Astra。没有合适路由、供应方失败时，仍提供带来源的有界摘录。
建议不会自动覆盖主人名称，也不能把任务改为完成。来源端生成的建议经 Mesh 复制。

原生打开按能力启用。本增量可以请求跳转到 macOS 默认配置目录中、已经结束、可读取且由
外部控制的 Codex 原会话。启动器成功仅标为已请求，不能证明已经显示或继续成功。
受管、运行中、状态未知、隔离配置来源，以及 CC、Windows 启动器和未经验证的写入权交接，
均显示原因并保持不可用；公开历史仍可读。原有显式桌面交接 CLI 是另一条独立路径。
本增量**不代表**已经完成原生实时写入权转移、App 对话导入原生侧栏或原生后续对话回归目标；
设计中这些验收项仍未完成。

源码、构建测试和桌面夹具渲染不能证明手机安装、真实模型首个有效回复不超过三秒、
真机连接恢复或真实多端一致；交付时分别报告。回退时保留原生记录、凭据、配对和封存日志；
旧客户端忽略新增整理与历史文件，继续使用原有接口。

### 本机帮手默认模型

`GET /v1/helpers/options` 为可编辑的本机帮手返回 `model_settings`（`name`、`model`、`models`）。带认证的 `POST /v1/helpers/<name>/model`，请求体 `{"model":"gpt-6.1-sol"}`，原子保存默认模型并替换后续交办使用的帮手实例。正在运行的任务保留原实例。`/v1/agents` 增加 `configured_model`，用量接口继续独立报告实际观察到的模型。偏好存于 `state/helper-models.json`，重启后保留，不修改密钥或原生 CLI 设置。模型名称校验格式，可用性在执行时由服务商判断。

## 实时帮手清单（0.24.1）

`GET /v1/helpers/options` 新增 `agents`、`removable` 和 `restorable` 清单。
原移除接口也支持移除非主对话的配置帮手，通过原子保存到 `state/helpers.json`
的 `disabled` 覆盖层生效，保留 config.json、凭据、已受理执行和历史。
`POST /v1/helpers` 携带 `{"restore":"名称"}` 可恢复保留的配置和模型偏好。
主对话／语音帮手不能移除；界面添加的帮手仍沿用原来的移除与凭据清理行为。

清单／模型变化会更新用量投影与修订号，客户端通过已有 sync 更新帮手列表，
无需重启。停用账号的窗口缓存和已受理任务计量仍保留。账号认证的设备可通过
桌面 0.1.18 使用有边界的帮手管理接口。修改绑定原电脑，结果不确定时不得自动
重试。降级到旧 Runtime 时，它会忽略新的 `disabled` 字段，配置中的帮手会重新
出现；若仍需隐藏，需由用户在旧配置中停用。

Codex 额度查询遇到明确的 401／Unauthorized 时，通过原生 `account/read`
`{refreshToken:true}` 恢复一次登录，再在原查询截止时间内重试
`account/rateLimits/read`。不在配置目录间复制登录文件，也不把它们返回客户端。
协议见[官方 App Server 登录接口](https://learn.chatgpt.com/docs/app-server#auth-endpoints)。
刷新失败时保留旧窗口供详情标注，更新后的客户端将头像圈显示为未知，
不再把旧百分比当成当前数据。

## 删除旁聊缓存（0.25.0）

`POST /v1/conversations/<side-id>/delete` 检查进行中的请求和任务，清除各端
聊天缓存和列表项，同步不含正文的目录删除标记。规范消息、请求和任务审计日志
保留用于排查；公开历史和模型上下文不再包含此旁聊，旧记录导入不能恢复它。
支持此协议的端在重连时清理缓存，旧客户端须升级。删除没有恢复入口；归档保留
完整可见历史并可恢复。这是缓存删除，不是安全擦除模型工具记录或日志文件。
`POST /v1/helpers/<name>/rename` 接收 `{"title":"显示名称"}`，持久化显示别名，
不改变帮手 ID、凭据或任务路由。
