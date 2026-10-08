<p align="right">
  <strong>简体中文</strong> · <a href="README.md">English</a>
</p>

# 小幽 Runtime

小幽是主人唯一的对话对象。这个 Runtime 是小幽背后那个一直开着的小服务：收到一句话，
连同小幽的人设一起交给后端的代理，再把三样东西还回来——完整回复、给 Passport 小屏幕
看的一两句简报、像素宠物此刻的表情。

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

- Python 3.9 或更新版本，不需要第三方包。
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

环境变量优先于配置文件，这样放进容器或虚拟机时不用改文件：`XIAOYOU_CONFIG`、
`XIAOYOU_HOST`、`XIAOYOU_PORT`、`XIAOYOU_TOKEN`、`XIAOYOU_STATE_DIR`、`XIAOYOU_BACKEND`、
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
| `GET /healthz` | `{"ok": true, "version", "backend"}`，不需要令牌。 |
| `POST /v1/messages`，请求体 `{"text", "conversation"?, "client_id"?}` | `202` 和这条消息的记录，状态为 `queued`。 |
| `GET /v1/messages/<id>?wait=<秒>` | 消息记录。带 `wait`（最多 60）时，这一轮一结束就返回。 |
| `POST /v1/conversations/<名字>/reset` | 忘掉这个对话的会话，下一句话从头开始。 |

消息记录包含 `id`、`client_id`、`conversation`、`status`（`queued`、`running`、`done`、
`failed`）、`text`、`reply`、`brief`、`mood`（`idle`、`busy`、`ask`、`happy`、`oops`）、
`error`、`created_at`、`finished_at`。

同一个 `client_id` 再发一次，返回的是已有的那条记录，不会把这一轮再跑一遍；所以客户端
没收到响应时可以放心重试。

```bash
TOKEN=...   # server.token 的值
curl -s -X POST http://127.0.0.1:8765/v1/messages \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"text": "你好", "client_id": "demo-1"}'
curl -s "http://127.0.0.1:8765/v1/messages/<id>?wait=60" -H "Authorization: Bearer $TOKEN"
```

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
顺序处理、按 `client_id` 重试、令牌校验，以及用 `echo` 后端跑的 HTTP 接口。

2026-10-08 用 Claude Code 2.1.294 在 Linux 上手动验证过（是在云端工作区里，不是在
预期运行的那台电脑上）：

- `--once` 按要求的结构给出了回复、简报和表情；第二轮能说出第一轮里的细节，说明
  续接对话可用。
- 通过 HTTP 接口：不带令牌的请求得到 `401`；一条“请别的工具给个第二意见”的请求，
  让模型调用了 `tools` 里配置的一个替身工具，并用自己的话总结了它的输出。
- 一轮简单对话端到端大约六秒。

没有验证：

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
