<p align="right"><strong>简体中文</strong> · <a href="first-response-measurement.md">English</a></p>

# 在真实设备上测首响应

这是[跨端可靠性规则](cross-stack-reliability.zh_CN.md)里 3,000 毫秒首响应要求的记录方法，在默认流程切换（[Runtime 重构设计](runtime-refactor-design.zh_CN.md)中的 R6.3）之前使用。要求本身不变：在发出消息的那个客户端上，从按下发送到第一个有意义的完整气泡，每一个成功样本都不能超过 3,000 毫秒。平均值和分位数都不算数，慢样本和超时也不能剔除。

## 在哪里测什么

| 来源 | 给出什么 | 作用 |
| --- | --- | --- |
| 手机 logcat，标签 `XiaoyouTiming`（App 0.32.4 起） | `send`（发出）、`final`（完整回复到达手机）、`bubble`（第一个气泡画出），用手机自己的开机时钟，按随机客户端编号区分 | **测量本身** |
| Runtime 消息记录（`runtime/state/messages.json` 和日志） | 客户端编号 → Runtime 轮次编号、输入类型、这一轮是否失败 | 把两边对上；把错误气泡记为未达标 |
| Runtime 日志（`runtime/state/runtime-stderr.log`）中的 `Decide/speak` 行 | 一轮的决定耗时、模型首段正文耗时和说话总耗时 | 说明时间花在哪里 |

`tools/ttft_report.py` 按编号把它们拼起来，从不拿一台机器的时钟去减另一台的。它只打印和记录编号与时间，从不记录消息内容。`bubble_ms - final_ms` 是手机绘制的时间；Runtime 的数字是 Runtime 内部的耗时。

这套方法不覆盖的：桌面客户端还没有计时日志，设备（固件）语音路径也没有记录。这两项都记为未运行（NOT RUN）。

## 测量之前

1. `xiaoyou.decide_first` 只改变流式 DeepSeek 语音代理的路径。在 Runtime 配置里设 `"xiaoyou": {"decide_first": true}`，然后重启 Runtime。先保留一份状态目录副本；回退时改回 `false` 再重启。
2. 记下版本组合（不含密钥）：App 版本、Runtime 提交号（在 Runtime 那台电脑上执行 `git rev-parse --short HEAD`）和版本号、语音代理和模型、连接方式（同一局域网、Tailscale、Funnel），以及这段对话归哪台电脑负责。
3. 用 USB 调试连上手机，确认 `adb devices` 能列出它。

## 每个场景测一次

每个场景：先清空日志、开始记录，发至少五条消息，再按 Ctrl+C 停止记录：

```bash
adb logcat -c
```

```bash
adb logcat -s XiaoyouTiming > phone-cold-start.txt
```

然后在 Runtime 那台电脑上（或者用它的状态目录和日志副本）运行：

```bash
python3 tools/ttft_report.py phone-cold-start.txt --state runtime/state --runtime-log runtime/state/runtime-stderr.log --scenario cold-start --record ttft-results.jsonl --note "app=0.32.6 runtime=7db5b0d voice=deepseek transport=lan"
```

所有样本都达标时退出码为 0，有样本未达标或没出气泡时为 1，记录里没有样本时为 2。`ttft-results.jsonl` 放在仓库外面；它跨多次测量累积，每个样本一行。

| 场景 | 怎么造出来 |
| --- | --- |
| `cold-start` | 重启 Runtime，强行停止 App，再打开 App 发送 |
| `warm` | 在正在聊的对话里连着发 |
| `long-history` | 在留存了几百轮的对话里发 |
| `parallel-task` | 有帮手任务在跑时发 |
| `foreground-return` | 把 App 从后台切回来后马上发 |
| `source-switch` | 切换对话或 Runtime 后马上发 |
| `retry` | 发送过程中关掉 Wi-Fi，再打开 |
| `remote` | 手机不在局域网，经 Tailscale 或 Funnel 连 Runtime |
| `voice` | 按住说话；松开那一刻是起点 |
| `image` | 发一张照片加一个问题 |
| `side-conversation` | 在侧边对话里发，不在主对话 |

## 怎么看结果

- `PASS`：按下发送后 3,000 毫秒内出现气泡。
- `MISS`：气泡超过 3,000 毫秒。`MISS (error)`：出现的是错误回复，不算有意义的回应。`NO BUBBLE`：记录结束前没有出现气泡；算作未达标。如果是记录停得太早，就重测这一场景，不要删掉这一行。
- 每个未达标样本都看分阶段的列：`decide_s` 长说明慢在决定那一步，`body_s` 长说明慢在模型的首段正文，`final_ms` 和 `bubble` 差得多说明慢在手机绘制。
- 只有上面每个场景的每个样本都达标，才能提出切换。把结果（包括未达标的）连同版本组合记到 [decide-then-speak](decide-then-speak.zh_CN.md) 里。
