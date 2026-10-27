<p align="right"><strong>简体中文</strong> · <a href="decide-speak-handoff.md">English</a></p>

# 交接给 Codex：跑先定再说的串行／推测并行对比实验

日期：2026-10-27。交接方：Claude Code。接手方：Codex，在主人的 Mac 上执行。

## 1. 要做什么

按[实验方案](decide-speak-experiment.zh_CN.md)，跑完串行（S）和推测并行（P）的对比，整理结果，
写进[先定再说](decide-then-speak.zh_CN.md)的实测部分。方案里的用例、指标和判定标准都已经定好，
**不要在看到结果以后再修改它们**。觉得哪个用例有问题，就在报告里写出来，不要改。

需要用到的文件，都在 `/Users/weihaoliu/ai-passport` 的 `feature/usage-history` 分支上，交接时还没有提交：

| 文件 | 作用 |
| --- | --- |
| `docs/development/decide-speak-experiment.zh_CN.md` | 实验方案：两组怎么跑、指标、判定标准 |
| `tools/experiments/decide_speak_cases.jsonl` | 46 个用例，主人已过目 |
| `tools/experiments/decide_speak_compare.py` | 测试脚本：`run`、`summarize`、`review` 三个命令 |

## 2. 不能做的事

- 不改 Runtime 的代码、配置和人格文件，不重启或替换线上 Runtime，不部署。
- 结果和评审文件只放在仓库外（脚本会拒绝写进仓库）。建议放在 `~/xiaoyou-experiments/decide-speak/`。
- 不打印、不记录、不提交密钥。
- 不剔除慢样本和失败样本，不为了数字好看重跑某几次。
- 主人盲评完成之前，不打开 `blind-review-key.json`，也不告诉主人哪个是 S、哪个是 P。
- 不需要、也不要使用主人给的 Instinct 聊天导出；方案里的风格特点已经整理好了。

## 3. 步骤

### 3.1 准备

1. 确认 Python 是 3.9 或更新：`python3 --version`。脚本只用标准库。
2. 确认密钥文件存在：`~/.config/xiaoyou/deepseek.key`。脚本会先读环境变量 `DEEPSEEK_API_KEY`，没有再读这个文件。
3. 确认模型配置。线上配置 `runtime/config.json` 中，`agents.deepseek.chat_model` 是 `deepseek-flash`。
   R7 的 `helpers/agents.py` 里，只说话的调用（包括决定）用的是 `chat_model`，所以两步默认都用 `deepseek-flash`。
   如果你核对后发现不是这样，就用 `--decide-model`、`--speak-model` 改过来，并在报告里写明。
4. 记下版本组合：仓库提交号（`git rev-parse --short HEAD`）、模型名、所用的人格文件。用 `--note` 记进每一行结果。

### 3.2 不联网试跑

```bash
python3 tools/experiments/decide_speak_compare.py run --dry-run --repeats 1 --pause 0 --decide-wait 3 --out ~/xiaoyou-experiments/decide-speak/dry.jsonl
```

应该写出 92 行（46 个用例 × 2 组），没有 `error`。这一步只检查流程，数字没有意义。

### 3.3 真实接口探测

```bash
python3 tools/experiments/decide_speak_compare.py run --only chat-02,task-01,run-03 --repeats 1 --out ~/xiaoyou-experiments/decide-speak/probe.jsonl --note "probe"
```

逐行检查：
- `decision.fetch_error` 为空，`decision.usage` 有数字；
- `speech.text` 不为空，`speech.error` 为空；
- `task-01` 的决定应该是 `start_task`，`run-03` 应该是 `cancel_task`（超时的看 `late_name`）。

如果接口拒绝请求（比如不认 `thinking` 或 `tools` 的写法），**停下来报告**，不要改请求格式去绕过。

### 3.4 完整运行

```bash
python3 tools/experiments/decide_speak_compare.py run --repeats 5 --out ~/xiaoyou-experiments/decide-speak/results.jsonl --note "commit=<提交号> model=deepseek-flash persona=runtime/persona.txt"
```

- 共 460 次运行，每次大约几秒，加上间隔，预计一小时左右。建议在后台跑，并保持电脑不休眠。
- 中断了就用同样的命令再跑一次：已经写过的（用例、组、第几次）会跳过，包括失败的那几次。
- 跑的过程中不要用同一个密钥做别的大量请求，以免互相影响时延。

### 3.5 汇总

```bash
python3 tools/experiments/decide_speak_compare.py summarize ~/xiaoyou-experiments/decide-speak/results.jsonl
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
python3 tools/experiments/decide_speak_compare.py review ~/xiaoyou-experiments/decide-speak/results.jsonl --out-dir ~/xiaoyou-experiments/decide-speak/review
```

把 `blind-review.md` 交给主人。主人评完以后，再用 `blind-review-key.json` 还原成 S／P 的胜负。

## 4. 报告

写进 `docs/development/decide-then-speak.zh_CN.md` 和英文版 `decide-then-speak.md` 的实测部分，两种语言保持一致：

- 版本组合：提交号、模型、人格文件、日期、电脑；
- 两组的 `release_ms` 中位数、p95、最大值、超过 3,000 毫秒的个数，并逐个列出超过 3,000 毫秒的样本；
- 决定超时率、决定的真实耗时中位数（`real_ms`）、决定正确率；
- P 的丢弃比例和额外费用，分别按实验比例和线上比例估算；
- 三类说做不一致的次数，以及 `must`／`must_not` 的满足情况；
- 判定标准四条逐条给结论。第 3 条等主人盲评，第 4 条等主人给费用上限，没有结果的写“待定”；
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
