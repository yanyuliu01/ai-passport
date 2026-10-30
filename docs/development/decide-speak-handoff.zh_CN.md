<p align="right"><strong>简体中文</strong> · <a href="decide-speak-handoff.md">English</a></p>

# 交接给 Codex：跑先定再说的串行／推测并行对比实验

日期：2026-10-27。交接方：Claude Code。接手方：Codex，在主人的 Mac 上执行。

## 1. 要做什么

按[实验方案](decide-speak-experiment.zh_CN.md)，跑完串行（S）和推测并行（P）的对比，整理结果，
写进[先定再说](decide-then-speak.zh_CN.md)的实测部分。方案里的用例、指标和判定标准都已经定好，
**不要在看到结果以后再修改它们**。觉得哪个用例有问题，就在报告里写出来，不要改。

需要用到的文件，都在 `/Users/weihaoliu/ai-passport` 的 `feature/usage-history` 分支上，已提交为 `6349ba3`（方案／脚本）和 `a0e4dc4`（费用规则）：

| 文件 | 作用 |
| --- | --- |
| `docs/development/decide-speak-experiment.zh_CN.md` | 实验方案：两组怎么跑、指标、判定标准 |
| `tools/experiments/decide_speak_cases.jsonl` | 46 个用例，主人已过目 |
| `tools/experiments/decide_speak_compare.py` | 测试脚本：`run`、`summarize`、`review` 三个命令 |

## 2. 不能做的事

- 不改 Runtime 的代码、配置和人格文件，不重启或替换线上 Runtime，不部署。
- 结果和评审文件只放在仓库外（脚本会拒绝写进仓库）。建议放在 `~/xiaoyu-experiments/decide-speak/`。
- 不打印、不记录、不提交密钥。
- 不剔除慢样本和失败样本，不为了数字好看重跑某几次。
- 主人盲评完成之前，不打开 `blind-review-key.json`，也不告诉主人哪个是 S、哪个是 P。
- 不需要、也不要使用主人给的 Instinct 聊天导出；方案里的风格特点已经整理好了。

## 3. 步骤

### 3.1 准备

1. 确认 Python 是 3.9 或更新：`python3 --version`。脚本只用标准库。
2. 确认密钥文件存在：`~/.config/xiaoyu/deepseek.key`。脚本会先读环境变量 `DEEPSEEK_API_KEY`，没有再读这个文件。
3. 确认模型配置。线上配置 `runtime/config.json` 中，`agents.deepseek.chat_model` 是 `deepseek-flash`。
   R7 的 `helpers/agents.py` 里，只说话的调用（包括决定）用的是 `chat_model`，所以两步默认都用 `deepseek-flash`。
   如果你核对后发现不是这样，就用 `--decide-model`、`--speak-model` 改过来，并在报告里写明。
4. R7 工作区在 `/Users/weihaoliu/ai-passport-r7`，含本地改动。将其提交号和未提交状态与实验脚本工作区分别记录。线上配置在主工作区，不一定在 R7 工作区。
5. 记下版本组合：仓库提交号（`git rev-parse --short HEAD`）、模型名、所用的人格文件。用 `--note` 记进每一行结果。

### 3.2 不联网试跑

```bash
python3 tools/experiments/decide_speak_compare.py run --dry-run --repeats 1 --pause 0 --decide-wait 3 --out ~/xiaoyu-experiments/decide-speak/dry.jsonl
```

应该写出 92 行（46 个用例 × 2 组），没有 `error`。这一步只检查流程，数字没有意义。

### 3.3 真实接口探测

```bash
python3 tools/experiments/decide_speak_compare.py run --only chat-02,task-01,run-03 --repeats 1 --out ~/xiaoyu-experiments/decide-speak/probe.jsonl --note "probe"
```

逐行检查：
- `decision.fetch_error` 为空，`decision.usage` 有数字；
- `speech.text` 不为空，`speech.error` 为空；
- `task-01` 的决定应该是 `start_task`，`run-03` 应该是 `cancel_task`（超时的看 `late_name`）。

如果接口拒绝请求（比如不认 `thinking` 或 `tools` 的写法），**停下来报告**，不要改请求格式去绕过。

### 3.4 完整运行

```bash
python3 tools/experiments/decide_speak_compare.py run --repeats 5 --out ~/xiaoyu-experiments/decide-speak/results.jsonl --note "commit=<提交号> model=deepseek-flash persona=runtime/persona.txt"
```

- 共 460 次运行，每次大约几秒，加上间隔，预计一小时左右。建议在后台跑，并保持电脑不休眠。
- 中断了就用同样的命令再跑一次：已经写过的（用例、组、第几次）会跳过，包括失败的那几次。
- 跑的过程中不要用同一个密钥做别的大量请求，以免互相影响时延。

### 3.5 汇总

```bash
python3 tools/experiments/decide_speak_compare.py summarize ~/xiaoyu-experiments/decide-speak/results.jsonl
```

单价默认是配置里的每百万 token 输入 2、输出 8，单位沿用配置，报告里注明。

### 3.6 说和做是否一致（由你标注）

逐条读 `results.jsonl`：对照 `decision.status`、`decision.facts` 和 `speech.text`，对每次运行标注：

- `said_not_done`：说已经安排、记住、停止，但这次没有执行承诺（状态为 `reply`、`timeout`、`unavailable`、`refused`）；
- `done_not_said`：执行了承诺（`selected`），回复里却没提；
- `overstated`：把“已排队”“已请求停止”说成“已开始”“已完成”“已经停了”；
- 用例的 `must` 和 `must_not` 各条是否满足。

标注写到仓库外的 `labels.jsonl`，每次运行一行，带上 `case`、`arm`、`repeat`。
把所有被标出问题的运行，加上随机抽取的 10% 没有问题的运行，列成清单交给主人复核。

### 3.7 风格评分（由你打分，作为辅助参考）

按方案第 2 节的两张表逐项打 0 或 1 分：闲聊类用例（闲聊与情绪、语言切换的闲聊部分）对照肥鱼的表，
其他用例对照 Instinct 的表。只评每组第一次运行（`repeat` 为 0）。评分时不要看是哪一组。

### 3.8 给主人盲评

```bash
python3 tools/experiments/decide_speak_compare.py review ~/xiaoyu-experiments/decide-speak/results.jsonl --out-dir ~/xiaoyu-experiments/decide-speak/review
```

把 `blind-review.md` 交给主人。主人评完以后，再用 `blind-review-key.json` 还原成 S／P 的胜负。

## 4. 报告

写进 `docs/development/decide-then-speak.zh_CN.md` 和英文版 `decide-then-speak.md` 的实测部分，两种语言保持一致：

- 版本组合：提交号、模型、人格文件、日期、电脑；
- 两组的 `release_ms` 中位数、p95、最大值、超过 3,000 毫秒的个数，并逐个列出超过 3,000 毫秒的样本；
- 决定超时率、决定的真实耗时中位数（`real_ms`）、决定正确率；
- P 的丢弃比例和额外费用，分别按实验比例和线上比例估算；
- 三类说做不一致的次数，以及 `must`／`must_not` 的满足情况；
- 判定标准三条逐条给结论，第 3 条等主人盲评，没有结果的写“待定”；费用只报告数字，不下结论；
- 已知局限（见第 5 节）。

按[跨端可靠性规则](cross-stack-reliability.zh_CN.md)的格式交付：Build 写 NOT RUN（没有构建），
Host tests 写本次跑过的命令，Device tests 写 NOT RUN（这里的时延不含传输和客户端，不能代替真机首答）。
报告写完先给主人看，不要自行提交或推送。

## 5. 已知局限

- 系统提示只有人格文件加用例给出的状态，比线上短，绝对耗时会偏低；两组用的是同一份，比较仍然成立。
- 记录器返回的事实措辞是照线上意思写的，不是逐字相同。
- 时延只在 Runtime 一侧，不含传输、排队和客户端显示。
- DeepSeek 的前缀缓存可能让后面的运行变快；两组交替先后顺序，可以抵消一部分。
- 胜出的一组，还要按 R7 分支上的首响应测量文档（`docs/development/first-response-measurement.zh_CN.md`）到真机上再测。

## 6. 可以直接贴给 Codex 的话

> 请按 `docs/development/decide-speak-handoff.zh_CN.md` 跑先定再说的串行／推测并行对比实验。
> 先做 3.1～3.3，探测正常再做完整运行；不要改 Runtime，不要改用例和判定标准，结果放在仓库外。
> 跑完按第 4 节写报告，写完先给我看，不要提交。

## 主人追加的探索性试验（2026-10-27）

主人随后要求先取消决定阶段的期限，观察平均耗时。本次探索试验以此要求
替代 1.3 秒截断；线上配置保持不变。先将 46 个用例在两组各跑一次，再决定是否增加重复：

```bash
python3 tools/experiments/decide_speak_compare.py run --deadline none --decide-wait 60 --repeats 1 --out ~/xiaoyou-experiments/decide-speak/20261027/unbounded-results.jsonl
```

本轮结果与已中断的限时试验分开保存，不混合统计，也不称为原方案的 460 次完整实验。
网络故障仍由 60 秒的请求 socket 超时处理，决定阶段本身不截断。
除了中位数和慢样本，还要报告决定耗时的平均数。所有承诺仍只进入模拟器，
包括成功返回的 `queued`；不能让 CC 或 Codex 真正执行这些合成任务。

`review` 也会生成 `public/index.html`，可离线填写 A／B／平、备注，支持浏览器本地保存
和 JSON 导出。通过本机网页打开时，只提供 `public/` 目录，组别密钥放在该目录外，
主人评完前不要读取。Markdown 版盲评表仍可直接编辑。

## 最新交接状态：主人盲评已完成（2026-10-27）

**接手先读本节，不要重新跑模型或把早期“待盲评”当作当前状态。**

### 已完成与结论

- 不设决定期限的 46 × 2 单轮探索试验已完成。决定平均 2.72 秒；串行完整气泡放行平均
  5.22 秒，推测并行平均 3.84 秒。详见[实测报告](decide-then-speak.zh_CN.md)。
- 主人已完成全部 46 题，导出文件为 `/Users/weihaoliu/Downloads/blind-review-answers.json`。
  已核对题号唯一、选择完整、评审标识与本轮表格一致。
- 以导出 JSON 为准。先前当前内置浏览器只显示 2/46，是该浏览器本地状态，不代表主人只评了两题；
  早期 `owner-review-observed.json` 仅是局部观察，不能用于最终计票。
- 完整评完后才读取组别密钥：**S 26 胜，P 17 胜，平 3**。P 胜或平为 **20/46 = 43.48%**，
  低于原定 50% 质量门槛。速度一条初步有利，一致性候选标注仍需复核，质量一条 FAIL。
  本轮不建议切换到推测并行；现有线上流程保持原样。一次重复不是原定 460 次验证性实验。
- 全程只执行模拟承诺，没有让真实 CC 或 Codex 执行合成任务；没有改线上 Runtime、配置或人格，
  没有重启、部署、提交或推送。R7 原有未提交改动保留，实验期间其差异哈希未变化。

### 主人反馈的重点（24 条备注）

1. **短，意思说一次即可。** 问日期直接给日期，问来不来得及一两句说明。
   删去确认后的重复承诺、额外限制和自我解释；避免“不是……而是……”的绕行逻辑。
   承认没交成功并说明现在的真实状态即可，不需要长篇交代内部过程。
2. **按自然语义拆短气泡。** 多题明确要求拆气泡；这是内容组织需求，不授权降低三秒门槛、
   用打字动画充数或在客户端任意切碎文本。当前双组实验没有比较新的拆泡方案。
3. **对话应当有来有回。** 不要只反问和承接；可以分享自己的想法、感受、真实经历，
   或表达“也想看看月亮”这样的愿望。具体经历、当前活动必须有依据，不能编造“正在干活”或假装看过。
   主人将有来源的故事与自主活动留待驱动力设计，不要在本轮临时加一套人格硬规则。
4. **情绪与办事都要回应。** “烦死了，帮我跑测试”不能只回任务状态；
   出错时应有适当道歉，避免无缘由地反复“我在”。
5. **轻口语和小细节。** 允许自然的“～”、标点或表情，避免每句套同一模板；
   例如主人偏好的表达是“好，改成周四了，到时候提醒你一下～”。
   不要省略会影响任务真假、停止是否确认等重要状态。
6. 两组英文提醒都回复中文，主人标为平；这是共同缺陷，不能作为某组优胜的依据。

### 文件、验证与继续方式

工作区：`/Users/weihaoliu/ai-passport`，分支 `feature/usage-history`，基准 `a0e4dc4`，
本轮改动尚未提交。R7 工作区：`/Users/weihaoliu/ai-passport-r7`，`aba494e` 加原有本地改动，
不要覆盖、清理或替换。实验脚本新增了无决定期限选项、平均数、完整盲评上下文、离线标注页面，
修正了晚到调用费用遗漏、流中断误计完整气泡及丢弃清理阻塞重说计时。
回归测试在 `tests/test_decide_speak_compare.py`，已接入 `tools/validate.sh`。

仓库外目录：`/Users/weihaoliu/xiaoyou-experiments/decide-speak/20261027/`：

- `owner-review-answers.json`：主人完整导出原件副本；`owner-review-summary.json`：逐题还原和计票。
- `unbounded-results.jsonl`、`unbounded-summary.json`：本轮模型结果；`results.jsonl`：早期限时 92 条，勿混算。
- `labels.jsonl`、`audit-review.json`、`audit-review.md`：Codex 初步一致性标注与复核清单，尚非主人确认。
- `review/public/index.html`：离线盲评表；当前入口 `http://127.0.0.1:8770/`。
  该进程只提供 `public/`；如已停止，用下面命令恢复。密钥位于其父目录，不能一起提供网页。
- `r7-unittest.log`、`validate.log`、`static-final.log`、`source-identities.json`：测试与源码身份。

```bash
python3 -m http.server 8770 --bind 127.0.0.1 --directory /Users/weihaoliu/xiaoyou-experiments/decide-speak/20261027/review/public
python3 tests/test_decide_speak_compare.py
python3 tools/check_repo.py
```

Build：PASS（完整门禁及镜像核验）；Host tests／回归：PASS（R7 1,190 项，实验 6 项）；
Device tests：NOT RUN；端到端三秒、连接恢复、多端同步：NOT RUN。
剩余未验证包括重复采样、真实客户端、冷启动、恢复、同步、真实费用和一致性标注复核。
生产未改，无需为交接重启或迁移数据。

下一步先讨论并确认一项小改动：短句／气泡组织，或有真实来源的主动表达与驱动力设计。
保留本轮原始用例和结果，任何新提示方案另建试验条件，不覆盖旧结果。
不要因接手而重跑付费模型、部署、重启 Runtime、改人格、开启真实帮手执行，或自行提交／推送。
