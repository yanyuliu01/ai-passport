<p align="right">
  <strong>简体中文</strong> · <a href="README.md">English</a>
</p>

# 小幽 Runtime

小幽是主人唯一的对话对象。这个 Runtime 是小幽背后那个一直开着的小服务：收到一句话，
连同小幽的人设一起交给后端的代理，再把三样东西还回来——完整回复、给 Passport 小屏幕
看的一两句简报、像素宠物此刻的表情。消息可以是打出来的文字，也可以是一段录音；录音先由
配置里选定的语音识别引擎变成文字。

目前唯一真正的后端是用非交互模式驱动本机的 **Claude Code 命令行**，用的是这台机器上
已经登录的 Claude 账号。其他代理（比如 Codex）不是另一个说话的角色：它们在配置里登记
为小幽的工具，小幽把活交给它们，再把结果总结给主人。

```text
手机 App / 任意客户端 ──HTTP──> 小幽 Runtime ──运行──> claude -p（人设、工具）
                                                        └─> 其他代理的命令行，作为工具
```

状态：验证阶段，先跑在自己的电脑上。同一份代码之后要搬到虚拟机，里面没有任何假设
某台机器的地方。见[哪些验证过、哪些没有](#哪些验证过哪些没有)。

## 需要什么

- Python 3.9 或更新版本。不需要第三方包，除非启用内置的语音识别引擎（见[语音](#语音)）。
- 用 `claude_code` 后端时：同一台机器、同一个用户下装好并登录了 Claude Code
  （终端里能直接运行 `claude`）。

## 快速开始

```bash
cd runtime
cp config.example.json config.json
python3 -c "import secrets; print(secrets.token_urlsafe(32))"   # 填进 server.token
python3 -m xiaoyou_runtime --config config.json --check
python3 -m xiaoyou_runtime --config config.json --once "你好"    # 只说一句，不启动服务
python3 -m xiaoyou_runtime --config config.json                 # 启动服务
```

`config.json`、`state/`、`workdir/` 已被 Git 忽略。不要提交真实的令牌。

想不调用任何模型先把整条链路跑通，把 `"backend"` 改成 `"echo"`。

## 配置

相对路径都相对于配置文件所在的目录。

| 配置项 | 默认值 | 含义 |
| --- | --- | --- |
| `server.host` | `127.0.0.1` | 监听地址。 |
| `server.port` | `8765` | 监听端口。 |
| `server.name` | 这台机器的主机名 | 手机 App 里怎么称呼这台 Runtime，最多 40 个字符。 |
| `server.token` | 无 | 共享令牌，至少 16 个字符；示例里的占位值会被拒绝。 |
| `state_dir` | `state` | 存“对话 → 会话编号”这张表的目录。 |
| `persona_file` | `persona.txt` | 小幽的人设，会追加到后端的系统提示里。 |
| `backend` | `claude_code` | `claude_code` 或 `echo`。 |
| `brief_max_chars` | `120` | 小屏幕简报的字数上限（20 到 400）。 |
| `turn_timeout_seconds` | `600` | 一轮超过这么久就停止（10 到 7200）。 |
| `claude_code.command` | `["claude"]` | 要运行的命令，写成列表。 |
| `claude_code.workdir` | `workdir` | Claude Code 的工作目录，不存在会自动创建。 |
| `claude_code.config_dir` | `null` | 可选，Claude Code 的配置目录，以 `CLAUDE_CONFIG_DIR` 传给它。见[使用单独的 Claude 登录](#使用单独的-claude-登录)。 |
| `claude_code.model` | `null` | 可选，作为 `--model` 传入。 |
| `claude_code.permission_mode` | `dontAsk` | 作为 `--permission-mode` 传入。 |
| `claude_code.allowed_tools` | `[]` | 作为 `--allowedTools` 传入的规则。 |
| `claude_code.extra_args` | `[]` | 追加在命令末尾的额外参数。 |
| `tools[]` | `[]` | 小幽可以把活交出去的代理：`name`、`description`、`allowed_tools`、`enabled`。 |
| `stt.engine` | `none` | 语音识别：`none`、`sense_voice` 或 `command`。见[语音](#语音)。 |
| `stt.model_dir` | 无 | `sense_voice`：放 `model.int8.onnx`（或 `model.onnx`）和 `tokens.txt` 的目录。 |
| `stt.language` | `auto` | `sense_voice`：`auto`、`zh`、`en`、`ja`、`ko` 或 `yue`。 |
| `stt.threads` | `2` | `sense_voice`：使用的处理器线程数（1 到 16）。 |
| `stt.command` | `[]` | `command`：要运行的命令，其中一个参数要包含 `{audio}`。 |
| `stt.timeout_seconds` | `60` | `command`：一次识别超过这么久就停止（5 到 600）。 |

环境变量优先于配置文件，这样放进容器或虚拟机时不用改文件：`XIAOYOU_CONFIG`、
`XIAOYOU_NAME`、`XIAOYOU_HOST`、`XIAOYOU_PORT`、`XIAOYOU_TOKEN`、`XIAOYOU_STATE_DIR`、`XIAOYOU_BACKEND`、
`XIAOYOU_CLAUDE_CONFIG_DIR`。

### 使用单独的 Claude 登录

Claude Code 从 `~/.claude` 读取登录信息和设置。如果那个目录已经配成别的用途（比如
通过 `ANTHROPIC_BASE_URL` 走公司网关，或者配了 API 密钥），不要改它，给小幽单独准备
一个目录：

```bash
mkdir ~/xiaoyou-login && cd ~/xiaoyou-login      # 任意文件夹，但不能是主目录
CLAUDE_CONFIG_DIR=~/.claude-xiaoyou claude       # 用订阅账号登录，/status 确认后退出
```

然后在 `claude_code` 下设置 `"config_dir": "~/.claude-xiaoyou"`。登录时不要待在主目录：
Claude Code 还会把“当前文件夹/.claude/settings.json”当作项目设置加载，而在主目录里，
这正是你想绕开的那个文件。

### 把一个代理接成工具

一条工具配置做两件事：`name` 和 `description` 会写进小幽的系统提示；`allowed_tools`
里的规则会并入 `--allowedTools`，让 Claude Code 不用询问就能运行它。示例配置里带了一条
没启用的 Codex；在同一台机器上装好并登录那个命令行之后，把 `"enabled"` 改成 `true`。

`permission_mode` 为 `dontAsk` 时，没有被放行规则覆盖的操作会被直接拒绝，而不是等一个
不在场的人来点头。放行规则要写窄：每一条都是小幽在没人看着时能做的事。

## HTTP 接口

除 `/healthz` 外，所有请求都要带 `Authorization: Bearer <令牌>`。请求体和响应都是 JSON。

| 请求 | 结果 |
| --- | --- |
| `GET /healthz` | `{"ok": true, "version", "backend", "name"}`，不需要令牌。 |
| `POST /v1/messages`，请求体 `{"text", "conversation"?, "client_id"?}` | `202` 和这条消息的记录，状态为 `queued`。 |
| `POST /v1/voice?conversation=<名字>&client_id=<编号>`，请求体是一个 WAV 文件 | `202` 和消息记录，`kind` 为 `voice`，`text` 为空。录音不合格或没有配置引擎时返回 `400`。 |
| `GET /v1/messages/<id>?wait=<秒>` | 消息记录。带 `wait`（最多 60）时，这一轮一结束就返回。 |
| `POST /v1/conversations/<名字>/history`，请求体 `{"turns": [{"id", "text", "reply", "at"?}]}` | `{"accepted": n}`：这些轮次（最多 30 轮）里有几轮是这台 Runtime 之前不知道的。它们会随下一句话告诉模型。 |
| `POST /v1/conversations/<名字>/reset` | 忘掉这个对话的会话，下一句话从头开始。 |

消息记录包含 `id`、`client_id`、`conversation`、`kind`（`text` 或 `voice`）、`status`
（`queued`、`transcribing`、`running`、`done`、`failed`）、`text`、`reply`、`brief`、`mood`
（`idle`、`busy`、`ask`、`happy`、`oops`）、`error`、`created_at`、`finished_at`。语音消息的
`text` 在识别完成之前是空的。

同一个 `client_id` 再发一次，返回的是已有的那条记录，不会把这一轮再跑一遍；所以客户端
没收到响应时可以放心重试。

```bash
TOKEN=...   # server.token 的值
curl -s -X POST http://127.0.0.1:8765/v1/messages \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"text": "你好", "client_id": "demo-1"}'
curl -s "http://127.0.0.1:8765/v1/messages/<id>?wait=60" -H "Authorization: Bearer $TOKEN"
```

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
python -m xiaoyou_runtime --config config.json --once "你好"
$env:XIAOYOU_HOST = "0.0.0.0"; python -m xiaoyou_runtime --config config.json
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

带得过去的是“说了什么”：主人的话和小幽的完整回复，转告模型时每轮分别截到 500 和 1000 个
字。带不过去的是那边会话知道的其他一切：读过的文件、工具的输出，以及比手机还记得的那几轮
更早的内容。

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
python3 -m xiaoyou_runtime --config config.json --stt stt-models/sense-voice/test_wavs/zh.wav
```

下载约 160 MB。`stt-models/` 已被 Git 忽略。

**`command`** 运行你指定的任意程序，比如换一个模型或接一个云服务：
`"stt": {"engine": "command", "command": ["my-stt", "{audio}"]}`。`{audio}` 会被换成 WAV
文件的路径，命令在配置文件所在的目录里运行，它打印到标准输出的内容就是识别结果。退出码
不为零时这条消息失败，并报告标准错误的最后一行。

`stt.engine` 为 `none` 时，语音消息会被拒绝并说明原因；打字的消息不受影响。

## 和手机 App 配对

```bash
XIAOYOU_HOST=0.0.0.0 python3 -m xiaoyou_runtime --config config.json   # 在局域网上监听
python3 -m xiaoyou_runtime --config config.json --pair                 # 打印 http://<地址>:<端口>#<令牌>
```

把打印出来的那一行粘贴到小幽中枢 App 里（见
[`companion/README.zh_CN.md`](../companion/README.zh_CN.md)）。这一行里带着令牌，要像
密码一样对待。手机要和这台机器在同一个网络里，而且传输是不加密的 HTTP，只在你信任的
网络里这样用。`--pair` 给出的本机地址是猜的；机器有多块网卡时，确认一下那是手机连得到
的地址。

## 一轮对话是怎么走的

1. 消息排队，一条一条处理。
2. 后端运行 `claude -p --output-format json --append-system-prompt <人设 + 工具清单>
   --json-schema <reply、brief、mood> --permission-mode <模式> [--allowedTools ...]
   [--model ...] [--resume <会话>]`。主人的话从标准输入送进去，不会出现在命令行参数里。
3. Claude Code 返回的会话编号按对话存到 `state/sessions.json`，下次用 `--resume` 带回去。
   对话记录本身留在 Claude Code 那边。

## 跑在别的机器或虚拟机上

- 把这个目录拷过去，建好 `config.json`（或设置环境变量），并在那台机器上登录
  Claude Code。会话编号只在本机有效：Runtime 搬家，之前的对话不会跟着走。
- 服务本身只有明文 HTTP。让它监听 `127.0.0.1`，再通过你自己掌握的加密通道访问
  （私有组网、SSH 隧道，或带 TLS 的反向代理）。直接监听公网地址不安全。
- 排队中和已完成的消息记录只在内存里。重启后旧的消息编号会返回 `404`；对话能接着聊，
  因为会话表在磁盘上。

## 哪些验证过、哪些没有

`runtime/tests/test_runtime.py`（`./tools/validate.sh --static` 会运行）验证了：配置
检查、交给假的 `claude` 可执行文件的完整命令行和标准输入、结果与错误的解析、会话续接、
顺序处理、按 `client_id` 重试、令牌校验、用 `echo` 后端跑的 HTTP 接口，以及用一条假的识别
命令跑的语音消息（格式检查、识别、识别为空和识别失败、录音的清理）。

2026-10-08 用 Claude Code 2.1.294 在 Linux 上手动验证过（是在云端工作区里，不是在
预期运行的那台电脑上）：

- `--once` 按要求的结构给出了回复、简报和表情；第二轮能说出第一轮里的细节，说明
  续接对话可用。
- 通过 HTTP 接口：不带令牌的请求得到 `401`；一条“请别的工具给个第二意见”的请求，
  让模型调用了 `tools` 里配置的一个替身工具，并用自己的话总结了它的输出。
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

没有验证：

- Windows：这个目录里的任何东西都还没有在 Windows 上运行过。
- 真实设备上的语音：麦克风音质、蓝牙吞吐，以及真实环境里真人说话的识别效果。
- macOS 上的 sherpa-onnx。

- 真实的 Codex 命令行；派活的验证用的是一个替身脚本。
- 虚拟机，以及连续运行多天的情况。macOS 上只由主人跑过一次 `--once`（用的是单独的
  `config_dir`），HTTP 服务还没有跑过。
- 从手机上调用。App 里发请求的那段代码在电脑的 Java 环境里对着本服务的 `echo` 后端
  跑过（发送、等待、错误令牌），但 App 本身还没有在手机上运行过。
- 被派出去的工具很慢、失败或输出很长时，模型的表现。

已知风险：

- 这条路依赖用订阅登录非交互地运行 Claude Code。Claude Code 的文档里有一个跳过这种
  登录的 `--bare` 模式，并说明它将来会成为 `-p` 的默认行为；到那时这个后端需要换一种
  认证方式。把它提供给自己以外的人之前，请先确认 Claude Code 当前的使用条款。
- 每一轮都计入所登录账号的用量限制。
- 只有一个共享令牌，没有按设备区分身份，也没有限流。

## 测试

```bash
python3 runtime/tests/test_runtime.py
```
