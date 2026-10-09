<p align="right">
  <strong>简体中文</strong> · <a href="README.md">English</a>
</p>

# 小幽 Runtime

小幽是主人唯一的对话对象，也是一个独立的身份：她不是某个模型的别名。这个 Runtime 是
小幽背后那个一直开着的小服务，属于她的东西都在这里——人设、她和主人的对话记录、
“这句话交给谁”的判断，以及别人做完之后由她怎么转述。Claude Code、Codex、另一台电脑上
的小幽，还有以后接进来的本地模型，都只是她可以用的**代理**。

收到一句话，Runtime 还回三样东西：完整回复、给 Passport 小屏幕看的一两句简报、像素宠物
此刻的表情。消息可以是打出来的文字，也可以是一段录音；录音先由配置里选定的语音识别
引擎变成文字。

```text
手机 App / 任意客户端 ──HTTP──> 小幽 Runtime
                                  │  人设 · 对话记录 · 路由 · 转述
                                  ├──> claude   （Claude Code 命令行，能以小幽的身份回话）
                                  ├──> codex    （Codex 命令行，只干活）
                                  ├──> 任意命令 （本地模型等）
                                  └──> 另一台电脑上的小幽 Runtime
```

状态：验证阶段，先跑在自己的电脑上。同一份代码之后要搬到虚拟机，里面没有任何假设
某台机器的地方。见[哪些验证过、哪些没有](#哪些验证过哪些没有)。

## 需要什么

- Python 3.9 或更新版本。不需要第三方包，除非启用内置的语音识别引擎（见[语音](#语音)）。
- 每个登记的代理各自要用的东西：`claude_code` 类型需要同一台机器、同一个用户下装好并
  登录了 Claude Code（终端里能直接运行 `claude`）；`codex` 类型需要装好并登录了 Codex
  命令行。`--check` 会指出哪个代理的命令找不到。

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

想不调用任何模型先把整条链路跑通，启动时加上环境变量 `XIAOYOU_DEFAULT_AGENT=echo`：
所有的话都交给一个原样复述的代理，不用改配置文件。

## 配置

相对路径都相对于配置文件所在的目录。

| 配置项 | 默认值 | 含义 |
| --- | --- | --- |
| `server.host` | `127.0.0.1` | 监听地址。 |
| `server.port` | `8765` | 监听端口。 |
| `server.name` | 这台机器的主机名 | 手机 App 里怎么称呼这台 Runtime，最多 40 个字符。 |
| `server.token` | 无 | 共享令牌，至少 16 个字符；示例里的占位值会被拒绝。 |
| `state_dir` | `state` | 存会话表和小幽的对话记录的目录。 |
| `persona_file` | `persona.txt` | 小幽的人设。只交给以小幽的身份回话的代理。 |
| `brief_max_chars` | `120` | 小屏幕简报的字数上限（20 到 400）。 |
| `turn_timeout_seconds` | `600` | 代理没有自己设 `timeout_seconds` 时用的超时（10 到 7200）。 |
| `agents.<名字>` | 无 | 一个代理，见[代理](#代理)。至少要有一个启用的。 |
| `xiaoyou.default_agent` | 第一个代理 | 没人点名、路由器也没有意见时，话交给谁。 |
| `xiaoyou.voice_agent` | 默认代理；它只干活时取第一个能说话的 | 只干活的代理做完之后，由谁用小幽的口吻转述。 |
| `xiaoyou.max_handoffs` | `2` | 一轮里最多转交几次（0 到 5）；0 表示不转交。 |
| `xiaoyou.router.type` | `mention` | `mention` 或 `command`，见[这句话交给谁](#这句话交给谁)。 |
| `xiaoyou.router.command` | `[]` | `command`：用来判断的命令。 |
| `xiaoyou.router.timeout_seconds` | `10` | `command`：超过这么久就不等了，交给默认代理（1 到 120）。 |
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

环境变量优先于配置文件，这样放进容器或虚拟机时不用改文件：`XIAOYOU_CONFIG`、
`XIAOYOU_NAME`、`XIAOYOU_HOST`、`XIAOYOU_PORT`、`XIAOYOU_TOKEN`、`XIAOYOU_STATE_DIR`、
`XIAOYOU_DEFAULT_AGENT`、`XIAOYOU_CODEX_CONFIG_DIR`（作用于所有 `codex` 类型的代理）、
`XIAOYOU_CLAUDE_CONFIG_DIR`（作用于所有 `claude_code` 类型的代理）。

0.3 及更早的配置（`backend`、`claude_code`、`tools`）仍然能读：按只有一个名叫 `claude`
的代理处理，启动时会提示这是旧写法，原来的会话接着用。`tools` 里启用过的条目需要手动
改成 `agents` 里的一个代理。

## 代理

`agents` 是一个对象，键是代理的名字（字母、数字、下划线和连字符，最多 24 个字符）。
每个代理都有这几项：

| 配置项 | 默认值 | 含义 |
| --- | --- | --- |
| `type` | 无 | `claude_code`、`codex`、`command`、`remote` 或 `echo`。 |
| `enabled` | `true` | `false` 时当它不存在。 |
| `description` | 空 | 一句话说明它擅长什么。小幽决定要不要把活交给它、路由器做判断，看的都是这句话。 |
| `aliases` | `[]` | 点名时除了名字之外还认的叫法，比如语音识别常写成的中文名。 |
| `speaks` | 随类型 | 能不能直接以小幽的身份回话。`claude_code`、`remote`、`echo` 默认能；`codex`、`command` 默认只干活。 |
| `timeout_seconds` | `turn_timeout_seconds` | 这个代理一次最多跑多久。 |

各类型另外认的配置项：

| 类型 | 做什么 | 配置项 |
| --- | --- | --- |
| `claude_code` | 非交互地运行 Claude Code 命令行，用这台机器上已经登录的账号。 | `command`（默认 `["claude"]`）、`workdir`（默认 `workdir`）、`config_dir`、`model`、`permission_mode`（默认 `dontAsk`）、`allowed_tools`、`extra_args`、`env` |
| `codex` | 非交互地运行 Codex 命令行（`codex exec`，接着聊用 `codex exec resume`）。 | `command`（默认 `["codex"]`）、`workdir`、`config_dir`、`sandbox`（默认 `read-only`）、`model`、`extra_args` |
| `command` | 任意命令。交给它的话从标准输入送进去，标准输出就是结果；参数里写了 `{prompt}` 时改为替换进参数。没有会话，每次从头开始。 | `command`、`workdir` |
| `remote` | 另一台电脑上的小幽 Runtime。那边有自己的人设、代理和会话，回来的已经是小幽的话。 | `url`、`token`（那台 Runtime 的 `server.token`） |
| `echo` | 原样复述，不调用任何模型。 | 无 |

`permission_mode` 为 `dontAsk` 时，Claude Code 里没有被 `allowed_tools` 放行的操作会被
直接拒绝，而不是等一个不在场的人来点头。放行规则要写窄：每一条都是这个代理在没人看着
时能做的事。Codex 的 `sandbox` 同理，默认只读。

### 换成别家的模型（例如 DeepSeek V4）

`claude_code` 类型的代理可以带一个 `env`：启动 Claude Code 时额外设置的环境变量。
把接口地址和密钥指向一个兼容 Anthropic 接口的服务，外壳还是 Claude Code（工具、会话、
回复格式都不变），回答的模型换成那一家的。示例配置里有一个没启用的 `deepseek`：

```json
"deepseek": {
  "type": "claude_code",
  "config_dir": "~/.claude-xiaoyou-deepseek",
  "model": "deepseek-v4-pro",
  "env": {
    "ANTHROPIC_BASE_URL": "https://api.deepseek.com/anthropic",
    "ANTHROPIC_AUTH_TOKEN": "你的 DeepSeek 密钥"
  }
}
```

把 `"enabled"` 改成 `true`，填上密钥，再把 `xiaoyou.default_agent`（和 `voice_agent`，
如果写了）指向它，小幽自己就由这个模型来回话；原来的 `claude` 留着当帮手，改回去只要
把这两项指回 `claude`。给它一个单独的 `config_dir`，这样它的会话不和订阅登录的那一套
混在一起；这个目录不用登录。密钥只写在 `config.json` 里（这个文件不进仓库）。

没有实测过：这一段是照 DeepSeek 的接口文档和 Claude Code 实际发出的请求写的，没有用
真实的密钥跑过一轮。Claude Code 的网页搜索是 Anthropic 那边的服务端工具，换了服务后
不一定可用。

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

被点名的代理（第 1、2 条）一律显示为“小幽把这件事交给了它”：消息记录里会有一条
`handoff`，它干活期间 `helper` 就是它，所以手机和设备上看得见是谁在做。替小幽说话的
那个代理也一样。按默认配置，`@claude ……` 由 `claude` 一步答完，用的还是小幽的口吻；
Runtime 会告诉它被点名的就是它自己，这一轮也不会再转给别的帮手。

路由器是可以换的那一块。`mention` 不额外判断，只靠上面的第 1、2、4 条。`command` 运行
你指定的命令：标准输入是 `{"text", "default", "agents": [{"name", "type", "description"}]}`，
标准输出的第一行是选中的代理名。输出为空、不认识的名字、出错或超时都退回默认代理——
路由器坏了不应该让主人说不了话。以后用一个小模型或分类器来做判断，就接在这里。

### 转交和转述

接话的代理能以小幽的身份回话时，Runtime 会告诉它现在有哪些帮手（其余代理的名字和
说明）。它可以直接回答，也可以在回复里写上“交给谁、做什么”。这时 Runtime 去运行那个
帮手，把原始结果交回来，再由它用小幽的话总结给主人。帮手失败了、或者点了一个不存在的
帮手，也照实交回去，由它如实告诉主人。一轮最多转交 `max_handoffs` 次，最后一次交回
之后不再有“转交”这个选项。

主人点名了一个只干活的代理时，先让它做（附上它没见过的最近几轮对话作为背景），结果交给
`voice_agent` 转述。`voice_agent` 这时不可用的话，原样把结果给主人，不让这一轮白做。

被转交的帮手只拿到任务，看不到人设和聊天记录；它在这个对话里有自己的会话，下次交给它
时接着用。

### 使用单独的 Claude 登录

Claude Code 从 `~/.claude` 读取登录信息和设置。如果那个目录已经配成别的用途（比如
通过 `ANTHROPIC_BASE_URL` 走公司网关，或者配了 API 密钥），不要改它，给小幽单独准备
一个目录：

```bash
mkdir ~/xiaoyou-login && cd ~/xiaoyou-login      # 任意文件夹，但不能是主目录
CLAUDE_CONFIG_DIR=~/.claude-xiaoyou claude       # 用订阅账号登录，/status 确认后退出
```

然后在这个 `claude_code` 类型的代理下设置 `"config_dir": "~/.claude-xiaoyou"`。登录时不要待在主目录：
Claude Code 还会把“当前文件夹/.claude/settings.json”当作项目设置加载，而在主目录里，
这正是你想绕开的那个文件。

Codex 的 `~/.codex` 也一样：那里的 `config.toml` 如果指向一个网关，不管登录的是哪个账号，
请求都会发到网关去。给小幽的 Codex 一个单独的目录（`CODEX_HOME`）；这个目录 Codex 不会
自己建：

```bash
mkdir -p ~/.codex-xiaoyou
CODEX_HOME=~/.codex-xiaoyou codex login
CODEX_HOME=~/.codex-xiaoyou codex login status
```

然后在这个 `codex` 类型的代理下设置 `"config_dir": "~/.codex-xiaoyou"`。


## HTTP 接口

除 `/healthz` 外，所有请求都要带 `Authorization: Bearer <令牌>`。请求体和响应都是 JSON。

| 请求 | 结果 |
| --- | --- |
| `GET /healthz` | `{"ok": true, "version", "backend", "name"}`，不需要令牌。`backend` 是默认代理的类型。 |
| `GET /v1/agents` | `{"default", "agents": [{"name", "type", "description", "speaks", "default"}]}`：小幽在这台 Runtime 上能用的代理。 |
| `POST /v1/messages`，请求体 `{"text", "conversation"?, "client_id"?, "agent"?}` | `202` 和这条消息的记录，状态为 `queued`。带 `agent` 表示点名交给这个代理；没有这个代理时返回 `400`。 |
| `POST /v1/voice?conversation=<名字>&client_id=<编号>&agent=<代理>`，请求体是一个 WAV 文件 | `202` 和消息记录，`kind` 为 `voice`，`text` 为空。录音不合格或没有配置引擎时返回 `400`。 |
| `GET /v1/messages/<id>?wait=<秒>&rev=<n>` | 消息记录。带 `wait`（最多 60）时，这一轮一结束就返回。再带上 `rev`（调用方手里那份记录的 `rev`）时，记录只要有任何变化就返回；客户端靠它一步一步跟着这一轮走。 |
| `POST /v1/conversations/<名字>/history`，请求体 `{"turns": [{"id", "text", "reply", "at"?}]}` | `{"accepted": n}`：这些轮次（最多 30 轮）里有几轮是这台 Runtime 之前不知道的。它们会随下一句话告诉接话的代理。 |
| `POST /v1/conversations/<名字>/reset` | 这个对话从头开始：所有代理的会话都忘掉，对话记录清空。 |
| `GET /v1/firmware` | `{"rev", "versions": […], "target", "device", "log"}`：固件仓库的清单，版本从新到旧。见[设备固件](#设备固件)。 |
| `GET /v1/firmware/target?wait=<秒>&rev=<n>` | 现在该推给设备的那一版，一层的对象：`id`（没有时是空串）、`seq`、`build`、`size`、`sha256`、`note`、`reason`、`attempts`、`notice`、`notice_at`、`rev`。带 `rev` 时清单一有变化就返回。 |
| `GET /v1/firmware/<编号>/image` | 这一版的应用镜像（二进制）。 |
| `POST /v1/firmware?note=<备注>&push=1`，请求体是固件文件 | `201` 和这一版的记录；`push=1` 同时把它设为要推的。 |
| `POST /v1/firmware/target`，请求体 `{"id": 版本或 null, "reason"?, "force"?}` | 指定（或取消）要推给设备的那一版；返回同 `GET /v1/firmware/target`。 |
| `POST /v1/firmware/device`，请求体 `{"event", "build", "state", "prev", …}` | 手机 App 转告设备的情况。`event` 为 `connected`、`progress`、`installed`、`failed` 或 `unsupported`；返回同 `GET /v1/firmware/target`。 |

消息记录包含 `id`、`client_id`、`conversation`、`kind`（`text` 或 `voice`）、`status`
（`queued`、`transcribing`、`running`、`done`、`failed`）、`text`、`reply`、`brief`、`mood`
（`idle`、`busy`、`ask`、`happy`、`oops`）、`error`、`created_at`、`finished_at`。语音消息的
`text` 在识别完成之前是空的。

这一轮交给了谁，也在记录里：

- `asked`：调用方点名的代理，没点名是 `null`。
- `agent`：处理中是现在在做这件事的代理；做完后是这一轮先接话的那个。
- `stage`：处理中给人看的一句话，比如小幽转交时说的“我让 codex 看看”；没有转交或已经
  结束时是 `null`。
- `helper`：小幽把活儿交出去、正在等的那个 agent 的名字；其他时候是 `null`。和 `agent`
  不同，它只在转交时才有值。
- `rev`：一个计数，记录每变一次就加一。
- `events`：这一轮走过的步骤，每项是 `{"at", "kind", "agent", "text"}`。`kind` 为 `route`
  （交给了谁，`text` 是原因：`asked`、`mention`、`router`、`default`）、`handoff`（转交给
  帮手）、`result`（帮手做完或没做成）、`note`。

同一个 `client_id` 再发一次，返回的是已有的那条记录，不会把这一轮再跑一遍；所以客户端
没收到响应时可以放心重试。

```bash
TOKEN=...   # server.token 的值
curl -s -X POST http://127.0.0.1:8765/v1/messages \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"text": "你好", "client_id": "demo-1"}'
curl -s "http://127.0.0.1:8765/v1/messages/<id>?wait=60" -H "Authorization: Bearer $TOKEN"
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
python3 -m xiaoyou_runtime firmware list              # 有哪些版本；● 设备现在跑的，→ 等着推的
python3 -m xiaoyou_runtime firmware status            # 设备上是哪一版、在等什么、最近的经过
python3 -m xiaoyou_runtime firmware fetch --push      # 取 GitHub 上最新构建的一版，入库并推给设备
python3 -m xiaoyou_runtime firmware add 文件.bin       # 入库一个固件文件（应用镜像或合并镜像）
python3 -m xiaoyou_runtime firmware restore 5         # 把设备换回第 5 版
python3 -m xiaoyou_runtime firmware restore previous  # 换回设备另一个槽位里的上一版（几秒钟，不用重传）
python3 -m xiaoyou_runtime firmware cancel            # 不推了
python3 -m xiaoyou_runtime firmware remove 3          # 从仓库里删掉第 3 版
```

“版本”可以写序号（`7` 或 `#7`）、编号的开头几位、`latest`、`current`（设备现在跑的）
或 `previous`。`push` 和 `restore` 是一回事。这些命令和正在运行的服务用的是同一份清单，
不用重启服务。

`fetch` 从 `firmware.repo` 的发布里取固件：不带参数取最新的，带标签取那一个，
`--commit <提交>` 只要从那个提交构建出来的（`HEAD` 表示 `firmware.source_dir` 现在所在
的提交）。`--wait 900` 表示还没构建出来就等，最多 900 秒；这个提交的构建要是已经失败
了会马上停下。`--detach` 把等和取放到后台，命令马上返回，日志在
`<state_dir>/firmware/fetch.log`。没登录时 GitHub 每小时只让查 60 次，设置环境变量
`GITHUB_TOKEN` 可以放宽。

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
改界面代码、跑检查、提交并推送，然后让 Runtime 去等 GitHub 的构建并推给设备。对设备
说“@裁缝 把顶上那条改细一点”，或者只说想改什么、让小幽自己决定交给它。“换回上一版”
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

另一种接法是不换台：手机只连一台 Runtime，把另一台电脑登记成它的一个 `remote` 代理。
这样“那台电脑上的事”由小幽转交过去，或者由主人点名，对话记录只在主的那一台上。已经
被别的 Runtime 转过手的话不会再往外转，所以两台互相登记也不会来回踢。

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

1. 消息按对话排队：同一个对话里一条一条处理，不同的对话互不等待。
2. 按[这句话交给谁](#这句话交给谁)的顺序选出先接话的代理。
3. 这个代理能以小幽的身份回话：把人设、回复格式和帮手清单一起给它。对 Claude Code 是
   `claude -p --output-format json --append-system-prompt <人设 + 帮手清单>
   --json-schema <reply、brief、mood、可选的 handoff> --permission-mode <模式>
   [--allowedTools ...] [--model ...] [--resume <会话>]`。主人的话从标准输入送进去，不会
   出现在命令行参数里。它要转交时，见[转交和转述](#转交和转述)。
4. 这个代理只干活：让它做，结果交给负责说话的代理转述。
5. 每个代理返回的会话编号按“对话 + 代理”存到 `state/sessions.json`，下次接着用。
6. 这一轮记进小幽自己的对话记录 `state/transcript.json`，并记下哪些代理听到了这一轮。
   下次轮到没听到的代理接话，先把它错过的几轮告诉它，只说一次。

对话记录属于小幽，不属于任何一个代理：换一个代理接话，小幽仍然记得刚才说过什么。
各个代理自己的会话里只有它参与过的部分。

## 跑在别的机器或虚拟机上

- 把这个目录拷过去，建好 `config.json`（或设置环境变量），并在那台机器上登录
  Claude Code。会话编号只在本机有效：Runtime 搬家，之前的对话不会跟着走。
- 服务本身只有明文 HTTP。让它监听 `127.0.0.1`，再通过你自己掌握的加密通道访问
  （私有组网、SSH 隧道，或带 TLS 的反向代理）。直接监听公网地址不安全。
- 排队中和已完成的消息记录只在内存里。重启后旧的消息编号会返回 `404`；对话能接着聊，
  因为会话表在磁盘上。

## 哪些验证过、哪些没有

`runtime/tests/test_runtime.py`（`./tools/validate.sh --static` 会运行）验证了：配置
检查和旧写法的兼容；交给假的 `claude`、`codex` 可执行文件的完整命令行和标准输入、结果与
错误的解析、会话续接；点名和路由的先后顺序、用一条命令做路由器；小幽转交、帮手失败或
不存在、转交次数上限、点名只干活的代理后的转述、换代理时补上错过的对话；两台真的
Runtime 之间的 `remote` 代理（用 `echo`）以及互相登记时不会来回转；按对话排队、按
`client_id` 重试、令牌校验、HTTP 接口；以及用一条假的识别命令跑的语音消息（格式检查、
识别、识别为空和识别失败、录音的清理）。

2026-10-09 用 Claude Code 2.1.295 在 Linux 上手动验证过（是在云端工作区里，不是在
预期运行的那台电脑上），配置是一个 `claude_code` 代理加一个 `command` 类型的替身帮手：

- 直接回答：一轮约 5 秒，第二轮能接上第一轮的内容。
- 小幽自己决定转交：一条“找个审查代码的看一下”的请求，Claude Code 按结构给出了
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

- 真实的 Codex 命令行。`codex` 代理是对着 codex-cli 0.162.0 写的：命令行选项来自它的
  帮助输出，事件的格式来自一次没有登录、连不上服务的运行；还没有一轮真正跑成功过。
  转交的验证用的是一个替身脚本。
- `remote` 代理对着另一台真实电脑、经过真实网络的情况。
- 本地模型：`command` 类型能以小幽的身份回话这条路只在测试里用脚本走过。
- 虚拟机，以及连续运行多天的情况。
- 0.4 这一版在 macOS 上、以及和手机 App 一起使用的情况。主人报告 0.2 版在 macOS 上
  跑通了“手机 App → Runtime → 设备”这条链路；这一版改动了内部结构，还没有在那里跑过。
  手机 App 目前不显示 `stage` 和 `agent`，也不能点名代理。
- 帮手很慢或输出很长时，模型的表现。

已知风险：

- 这条路依赖用订阅登录非交互地运行 Claude Code。Claude Code 的文档里有一个跳过这种
  登录的 `--bare` 模式，并说明它将来会成为 `-p` 的默认行为；到那时 `claude_code` 代理需要换一种
  认证方式。把它提供给自己以外的人之前，请先确认 Claude Code 当前的使用条款。
- 每一轮都计入所登录账号的用量限制。转交一次要多调用一次说话的代理来总结。
- 长任务没有做成后台任务：帮手跑多久，这个对话就等多久，期间不能取消。
- 只有一个共享令牌，没有按设备区分身份，也没有限流。

## 测试

```bash
python3 runtime/tests/test_runtime.py
python3 runtime/tests/test_firmware.py
```
