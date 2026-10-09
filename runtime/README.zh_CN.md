<p align="right">
  <strong>简体中文</strong> · <a href="README.md">English</a>
</p>

# 小幽 Runtime

小幽是主人唯一的对话对象，也是一个独立的身份：她不是某个模型的别名。这个 Runtime 是
小幽背后那个一直开着的小服务，属于她的东西都在这里——人设、她和主人的对话记录、
“这句话交给谁”的判断，以及别人做完之后由她怎么转述。Claude Code、Codex、另一台电脑上
的小幽，还有以后接进来的本地模型，都只是她可以用的**代理**。

主人提的每个需求是**一件事**，有自己的一张卡：他说的话、小幽的回答、后来的补充都记在
这张卡上。小幽自己只做快的事——听懂、能马上答的答掉、要花时间的交给帮手在后台做——
所以一件事没做完不妨碍说下一句，几件事可以同时在做，做的过程中可以补充、改要求或
取消。小幽每说一段话带三样东西：完整回复、给 Passport 小屏幕看的一两句简报、像素宠物
此刻的表情。消息可以是打出来的文字，也可以是一段录音；录音先由配置里选定的语音识别
引擎变成文字。

```text
手机 App / 任意客户端 ──HTTP──> 小幽 Runtime
                                  │  人设 · 对话记录 · 卡 · 路由 · 转述
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
| `state_dir` | `state` | 存会话表、卡和小幽的对话记录的目录。 |
| `persona_file` | `persona.txt` | 小幽的人设。只交给以小幽的身份回话的代理。 |
| `brief_max_chars` | `120` | 小屏幕简报的字数上限（20 到 400）。 |
| `turn_timeout_seconds` | `3600` | 一件后台的事最长做多久；代理没有自己设 `timeout_seconds` 时用它（10 到 7200）。 |
| `agents.<名字>` | 无 | 一个代理，见[代理](#代理)。至少要有一个启用的。 |
| `xiaoyou.default_agent` | 第一个代理 | 没人点名、路由器也没有意见时，话交给谁。 |
| `xiaoyou.voice_agent` | 默认代理；它只干活时取第一个能说话的 | 只干活的代理做完之后，由谁用小幽的口吻转述。 |
| `xiaoyou.max_parallel` | `0` | 同时在做的事最多几件（0 到 64）；`0` 是不限。超过的排队。 |
| `xiaoyou.voice_timeout_seconds` | `60` | 小幽自己接一句话最长多久（5 到 600）。这一步不给工具，只是听懂和分派。 |
| `xiaoyou.max_handoffs` | `2` | 0.4 的设置，现在不起作用；留着只为旧配置能读。 |
| `xiaoyou.router.type` | `mention` | `mention` 或 `command`，见[这句话交给谁](#这句话交给谁)。 |
| `xiaoyou.router.command` | `[]` | `command`：用来判断的命令。 |
| `xiaoyou.router.timeout_seconds` | `10` | `command`：超过这么久就不等了，交给默认代理（1 到 120）。 |
| `stt.engine` | `none` | 语音识别：`none`、`sense_voice` 或 `command`。见[语音](#语音)。 |
| `stt.model_dir` | 无 | `sense_voice`：放 `model.int8.onnx`（或 `model.onnx`）和 `tokens.txt` 的目录。 |
| `stt.language` | `auto` | `sense_voice`：`auto`、`zh`、`en`、`ja`、`ko` 或 `yue`。 |
| `stt.threads` | `2` | `sense_voice`：使用的处理器线程数（1 到 16）。 |
| `stt.command` | `[]` | `command`：要运行的命令，其中一个参数要包含 `{audio}`。 |
| `stt.timeout_seconds` | `60` | `command`：一次识别超过这么久就停止（5 到 600）。 |

环境变量优先于配置文件，这样放进容器或虚拟机时不用改文件：`XIAOYOU_CONFIG`、
`XIAOYOU_NAME`、`XIAOYOU_HOST`、`XIAOYOU_PORT`、`XIAOYOU_TOKEN`、`XIAOYOU_STATE_DIR`、
`XIAOYOU_DEFAULT_AGENT`、`XIAOYOU_CODEX_CONFIG_DIR`（作用于所有 `codex` 类型的代理）、
`XIAOYOU_CLAUDE_CONFIG_DIR`（作用于所有 `claude_code` 类型的代理）。

设了 `XIAOYOU_DEBUG`（任意值）时，小幽每接一句话，标准错误里多一行：她把这句话归到了
哪张卡、要 Runtime 做什么。

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
| `timeout_seconds` | `turn_timeout_seconds` | 这个代理做一件事最多跑多久。 |

各类型另外认的配置项：

| 类型 | 做什么 | 配置项 |
| --- | --- | --- |
| `claude_code` | 非交互地运行 Claude Code 命令行，用这台机器上已经登录的账号。 | `command`（默认 `["claude"]`）、`workdir`（默认 `workdir`）、`config_dir`、`model`、`permission_mode`（默认 `manual`）、`allowed_tools`、`add_dirs`（默认 `["~"]`）、`extra_args`、`env` |
| `codex` | 非交互地运行 Codex 命令行（`codex exec`，接着聊用 `codex exec resume`）。 | `command`（默认 `["codex"]`）、`workdir`、`config_dir`、`sandbox`（默认 `read-only`）、`model`、`extra_args` |
| `command` | 任意命令。交给它的话从标准输入送进去，标准输出就是结果；参数里写了 `{prompt}` 时改为替换进参数。没有会话，每次从头开始。 | `command`、`workdir` |
| `remote` | 另一台电脑上的小幽 Runtime。那边有自己的人设、代理和会话，回来的已经是小幽的话。 | `url`、`token`（那台 Runtime 的 `server.token`） |
| `echo` | 原样复述，不调用任何模型。 | 无 |

`permission_mode` 是交给 Claude Code 做事时的权限模式，默认 `manual`：凡是它在终端里会问
“可以吗”的操作，都来问主人，见[授权](#授权)。`allowed_tools` 是预先放行、不用问的规则，
要写窄。`add_dirs` 是除了启动目录之外它还能碰的目录，默认是整个主目录。想回到“没放行的
一律拒绝、谁也不问”，把 `permission_mode` 设成 `dontAsk`。小幽自己接话的那一次调用不受
这几项影响：它永远没有工具。Codex 的 `sandbox` 同理，默认只读。

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

前三条都算“指定了由谁做”：Runtime 不问模型，直接开一张卡，小幽说一句“交给 codex 了”，
那个代理在后台做。消息记录里有一条 `handoff`，`agent` 是它。替小幽说话的那个代理也一样：
`@claude ……` 是让 `claude` 带着工具、以小幽的身份在后台做这件事，Runtime 会告诉它被点名的
就是它自己。默认代理只会干活时，第 4 条也这样处理。

路由器是可以换的那一块。`mention` 不额外判断，只靠上面的第 1、2、4 条。`command` 运行
你指定的命令：标准输入是 `{"text", "default", "agents": [{"name", "type", "description"}]}`，
标准输出的第一行是选中的代理名。输出为空、不认识的名字、出错或超时都退回默认代理——
路由器坏了不应该让主人说不了话。以后用一个小模型或分类器来做判断，就接在这里。

### 一件事一张卡

没指定由谁做的话，由替小幽说话的代理接。这一次**不给它任何工具**，并且最长
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
“事 + 帮手”记的（`state/sessions.json` 里的 `对话名/卡的编号`）。能以小幽的身份说话的帮手
带着人设做事，它的结果就是小幽的话。只会干活的帮手只拿到任务，看不到人设和聊天记录；
它的原始结果交回这个对话的线上，由 `voice_agent` 转述。`voice_agent` 这时不可用的话，原样
把结果给主人，不让这件事白做。帮手没做成，卡上照实写“没做成”和原因。

卡的状态有 `working`（帮手在做）、`waiting`（等主人点头，见[授权](#授权)）、`done`、
`failed`、`cancelled`；`talking` 是预留的。卡存在 `state/cards.json`，留最近 50 张。Runtime
重启时还在做的事没法接着做，会被标成 `failed`，原因写“Runtime 重启了，这件事没做完”。

### 授权

交给 Claude Code 的事以 `--permission-mode manual` 运行，并带上 Runtime 自带的权限询问工具
（`xiaoyou_runtime/permission_mcp.py`，一个只有一个工具的 MCP 服务）。Claude Code 要问
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
| `POST /v1/messages`，请求体 `{"text", "conversation"?, "client_id"?, "agent"?, "card"?}` | `202` 和这条消息的记录，状态为 `queued`。带 `agent` 表示点名交给这个代理；没有这个代理时返回 `400`。`card` 是主人说这句话时屏幕上那件事的编号。 |
| `POST /v1/voice?conversation=<名字>&client_id=<编号>&agent=<代理>&card=<编号>`，请求体是一个 WAV 文件 | `202` 和消息记录，`kind` 为 `voice`，`text` 为空。录音不合格或没有配置引擎时返回 `400`。 |
| `GET /v1/messages/<id>?wait=<秒>&rev=<n>` | 消息记录。带 `wait`（最多 60）时，小幽一接完这句话就返回。再带上 `rev`（调用方手里那份记录的 `rev`）时，记录只要有任何变化就返回。 |
| `GET /v1/feed?conversation=<名字>&after=<序号>&wait=<秒>` | `{"seq", "cards": [...], "approvals": [...]}`：这个对话里序号比 `after` 大的卡（完整内容），和这个对话里所有还在等回答的授权（`{"id", "card", "conversation", "agent", "tool", "detail", "created_at"}`）。授权出现或有了答案都会让那张卡变一次，所以等卡就等到了授权。带 `wait`（最多 60）时没有变化就等，一有变化就返回。客户端记住 `seq`，下次当作 `after` 带上；拿到的 `seq` 比手里的小，说明 Runtime 的记录换过了，从 0 重新同步。 |
| `GET /v1/cards?conversation=<名字>` | `{"cards": [...]}`：最近 30 张卡。 |
| `POST /v1/cards/<编号>/cancel` | 取消这件事，返回这张卡；它已经不在做了就原样返回。没有这张卡返回 `404`。 |
| `POST /v1/approvals/<编号>`，请求体 `{"decision": "allow" 或 "deny"}` | 回答一个授权。没有这个授权（或 Runtime 重启过）返回 `404`；已经回答过、或者那件事已经停了，返回 `409`。 |
| `POST /v1/conversations/<名字>/history`，请求体 `{"turns": [{"id", "text", "reply", "at"?}]}` | `{"accepted": n}`：这些轮次（最多 30 轮）里有几轮是这台 Runtime 之前不知道的。它们会随下一句话告诉接话的代理。 |
| `POST /v1/conversations/<名字>/reset` | 这个对话从头开始：所有代理的会话都忘掉，对话记录清空。 |

消息记录说的是“这句话小幽接住了没有”，不是“这件事做完了没有”：小幽答完或者把活派出去，
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
- `stage`、`helper`：0.4 留下的字段，小幽接话的那几秒里可能有值，处理完都是 `null`。
- `rev`：一个计数，记录每变一次就加一。
- `events`：走过的步骤，每项是 `{"at", "kind", "agent", "text"}`。`kind` 为 `route`
  （交给了谁，`text` 是原因：`asked`、`mention`、`router`、`default`）或 `handoff`（交给了
  帮手，`text` 是小幽那一句话）。

一张卡包含 `id`（`c1`、`c2`……）、`conversation`、`title`（最多 24 个字）、`state`、`agent`
（谁在做或做的；小幽自己答的是 `null`）、`entries`（`[{"role": "you" 或 "xiaoyou", "text",
"at"}]`，最近 40 条）、`brief`、`mood`、`progress`（最近 5 行进展，每一轮重新开始）、
`started_at`、`edits`（补充或改过几次）、`approval`（正在等的那个授权的编号，没有是
`null`）、`queued`（设了并行
上限、正在排队）、`created_at`、`updated_at`、`seq`。

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

## 一句话是怎么走的

1. 消息按对话排队：同一个对话里一句一句处理，不同的对话互不等待。
2. 按[这句话交给谁](#这句话交给谁)的顺序看有没有指定由谁做。指定了：开卡，交到后台，
   这句话就算接完了。
3. 没指定：替小幽说话的代理接这句话。对 Claude Code 是
   `claude -p --output-format json --append-system-prompt <人设 + 帮手清单 + 现在的事>
   --json-schema <reply、brief、mood、card、action> --tools "" --permission-mode dontAsk
   [--model ...] [--resume <会话>]`。主人的话从标准输入送进去，不会出现在命令行参数里。
   Runtime 照她回复里的 `action` 办，见[一件事一张卡](#一件事一张卡)。
4. 后台的事：对 Claude Code 是 `claude -p --output-format stream-json --verbose
   [--append-system-prompt <人设> --json-schema <reply、brief、mood>] --permission-mode <模式>
   --permission-prompt-tool mcp__xiaoyou__approve --mcp-config <state 里的临时文件>
   --add-dir <目录> [--allowedTools ...] [--model ...] [--resume <这件事的会话>]`。启动目录
   仍然是 `workdir`。Runtime 逐行读它的事件：一开始就记下会话编号，每一步操作记一行进展。
   取消或改要求时，它和它起的所有进程一起结束（Claude Code 跑命令时会另开会话，所以是按
   父子关系找出整棵树来结束的）。Runtime 自己被 `kill` 时也会先这样收尾。
5. 会话编号存到 `state/sessions.json`：小幽接话用的按“对话 + 代理”，后台的事按
   “对话/卡 + 代理”。
6. 每句话和每个后台结果都记进小幽自己的对话记录 `state/transcript.json`，并记下替她说话
   的代理听到了没有。没听到的（后台的结果、指定给别人的话、手机从别处带来的），下次它
   接话时先告诉它，只说一次。

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
错误的解析、会话续接；点名和路由的先后顺序、用一条命令做路由器；卡的存取、序号、落盘
和重启后把没做完的标成失败；小幽直接回答、交给帮手后立刻能接下一句、几件事同时做、
补充、改要求（包括真的停掉一个进程再重跑）、取消、并行上限、帮手失败或不存在、办不了
的事重说一次、只干活的帮手做完后的转述、补上她没听到的对话；两台真的 Runtime 之间的
`remote` 代理（用 `echo`）以及互相登记时不会来回转；按对话排队、按 `client_id` 重试、
令牌校验、HTTP 接口（包括 feed 的长轮询）；以及用一条假的识别命令跑的语音消息（格式
检查、识别、识别为空和识别失败、录音的清理）。

2026-10-09 用 Claude Code 2.1.295 在 Linux 上手动验证过 0.5 这一版（是在云端工作区里，
不是在预期运行的那台电脑上），配置是一个放行了 `Bash` 的 `claude_code` 代理，通过 HTTP
接口：

- 第一句“跑一下 sleep 20，然后数一数 /etc 下面有多少个 .conf 文件”：7.6 秒时这句话接完，
  小幽说交给 Claude Code 了，卡是 `working`。
- 紧接着第二句“一打鸡蛋是几个”：13.7 秒时答完（“12 个”，还提到那件事还在后台跑），
  在自己的一张卡上。这时第一件事还在做。
- 37 秒时 feed 给出第一张卡变成 `done`，内容是数出来的结果。
- 小幽接一句话约 6 到 7 秒，大部分是 Claude Code 自己启动和回答的时间。

同一天，同样的环境，默认的 `manual` 模式（只预先放行了读文件的几个工具），通过 HTTP 接口：

- 让它往一个文件里写一行字：先后来了两个授权（一条 `Bash` 命令、一次 `Write`），卡变成
  `waiting`，feed 里有内容原文。两个都答 `deny`：文件没有被写，小幽照实说没写成。同一个
  授权再答一次得到 `409`。
- 再说一次并都答 `allow`：文件写出来了，内容正确；`state/` 里没有留下临时文件。
- 单独试过让一个授权等 100 秒再回答，Claude Code 一直等着，之后照常继续。
- 改要求：先让它 `sleep 45` 再读一个文件，9 秒后说“不用 sleep 了”。小幽选了那张卡和
  `redo`；Claude Code 被停掉，用同一个会话接着做，约 10 秒后给出结果，没有等那 45 秒。
- 取消：让它 `sleep 60`，然后说“算了不用跑了”。小幽选了那张卡和 `cancel`；Claude Code
  和它起的 `sleep` 都结束了。`kill` 掉 Runtime 时正在做的事也一样被结束。
- 途中发现并改掉的两件事：模型常把卡的编号 `c3` 写成 `3`（现在认）；Claude Code 跑命令
  时会另开会话，只结束进程组留得下 `sleep`（现在按父子关系结束整棵树）。
- 这个云端环境会让所有 `claude -p` 共用同一个会话编号，除非用干净的环境变量启动
  （`env -i HOME=$HOME PATH=$PATH`）。上面这些是在干净环境里做的；早先“并行”那一段不是。

同一天稍早，0.4 版用 Claude Code 2.1.295 在 Linux 上手动验证过（同样是在云端工作区里），
配置是一个 `claude_code` 代理加一个 `command` 类型的替身帮手。那一版里帮手是在同一轮里
做完的，下面的时间不适用于现在：

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

没有验证：

- Windows：这个目录里的任何东西都还没有在 Windows 上运行过。
- 真实设备上的语音：麦克风音质、蓝牙吞吐，以及真实环境里真人说话的识别效果。
- macOS 上的 sherpa-onnx。

- 真实的 Codex 命令行。`codex` 代理是对着 codex-cli 0.162.0 写的：命令行选项来自它的
  帮助输出，事件的格式来自一次没有登录、连不上服务的运行；还没有一轮真正跑成功过。
  转交的验证用的是一个替身脚本。
- `remote` 代理对着另一台真实电脑、经过真实网络的情况。
- 本地模型：`command` 类型能以小幽的身份回话这条路只在测试里用脚本走过。
- 虚拟机，以及连续运行多天的情况。
- 0.5 这一版在 macOS 上、以及和手机 App、设备一起使用的情况。手机 App 0.5.0 和现在的
  固件还不认识卡：能发能收，但后台的结果看不到。配套的 App 和固件还没做。
- 模型会不会总是选对 `card` 和 `action`：只手动试了上面这几句。
- 主人很久不回答授权（上面最长试过 100 秒）。
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
- `codex` 代理还是只读沙箱的 `codex exec`，不会来问。
- 手机 App 0.5.0 和现在的固件不会显示这些授权：在配套的 App 和固件出来之前，等授权的事
  只能用 HTTP 接口回答，或者一直等到超时。
- 两件事同时改同一个文件夹时没有互相保护。
- 对话重新开始（reset）不会停掉正在做的事，也不会清掉卡。
- 只有一个共享令牌，没有按设备区分身份，也没有限流。

## 测试

```bash
python3 runtime/tests/test_runtime.py
```
