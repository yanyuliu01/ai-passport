<p align="right">
  <strong>简体中文</strong> · <a href="helper-capabilities.md">English</a>
</p>

# 帮手的能力：MCP 和 skill

状态：2026-10-24，仅源码变更。能力网关、桌面帮手登记、逐帮手授权编辑和 Magpie 模型服务绑定已实现。MCP 服务登记、连接编辑、OAuth 和跨电脑目录同步待做。这台电脑没装 pi，仅有适配器主机测试。

帮手（Claude Code、Codex、dsh、pi）能用哪些外部工具，由小幽统一管。做法参考 [Raft](https://raft.build/zh-cn/) 的托管 MCP（[MCP Apps 文档](https://github.com/botiverse/raft-docs/blob/main/content/features/apps/mcp/index.md)），按我们“每台电脑一个 Runtime、帮手跑在本机”的结构改写，不照搬。

## 1. 学 Raft 的什么

| Raft 的做法 | 我们怎么用 |
| --- | --- |
| MCP 服务登记一次，整个空间共用；它的工具对 agent 可用 | 每台电脑一个**能力库**，登记一次，这台电脑上的帮手共用 |
| “连接”是这个服务用的账号或 Key，由 Raft 保存，**agent 永远看不到** | 连接只存在这台电脑上（只有本机账号能读的文件），不进同步记录，不交给帮手 |
| 登记后默认所有 agent 都能用，可以按 agent 收回、换连接 | 默认这台电脑上的帮手都能用；可以按帮手收回，或只开放部分工具 |
| 本机服务给每个运行时注入同一个本地 MCP 代理，工具名带服务前缀 | 小幽给每个帮手注入**一个** MCP 服务（能力网关），列出它被授权的工具，名字统一加前缀 |
| 每次调用带配置版本号和授权版本号，对不上就拒绝 | 每次核对当前授权和版本；界面改授权、连接文件轮换影响下一次调用，config.json 修改需重启 Runtime |
| 用不了的服务也列出来，agent 能说“X 需要重新连接” | 同样列出不可用的服务和原因；小幽在对话里提示主人 |
| 工具最多 200 个，结果超过 512 KB 拒绝，单次调用 20 秒超时 | 同样设上限，数值见第 4 节 |
| skill 留在运行时自己那边，Raft 只找出来显示 | 同样：找出各 harness 已有的 skill 并显示；另外允许给帮手指定 skill 目录 |

**不一样的地方：** Raft 只支持远程（Streamable HTTP）MCP；我们是本机程序，本机命令启动的 MCP（stdio）也要支持。Raft 的 agent 之间可以互相协作；我们对主人只有小幽一个声音（产品原则 P1），帮手之间的协作以后再说。

## 2. 结构

```
能力库（每台电脑一份，产地记录）
  服务：编号、名字、方式（本机命令 / 网络地址）、命令和参数或地址、要哪些请求头或环境变量、启用、配置版本
  连接：请求头或环境变量的值 → 只存本机 state/capability-secrets/，权限 0600，不同步，不显示
  授权：帮手 → 启用、允许的工具（未填 = 全部，[] = 不给工具）、授权版本
        |
        v
能力网关在 Runtime 的一端（本机回环地址，和审批的小门是同一个服务）
  GET  /mcp/tools  带这一轮的钥匙 → 这个帮手能用的工具 + 不可用的服务
  POST /mcp/call   带钥匙、工具名、参数、期望的版本 → 核对授权 → 替帮手调用真正的服务
        |
        v
能力网关的入口：每一轮注入帮手（python -m xiaoyou_runtime.permission_mcp，stdio）
  Claude Code：--mcp-config（同时带权限询问工具 approve）
  Codex：-c mcp_servers.xiaoyou.command / args / env
  dsh：ACP session/new 的 mcpServers
  pi：只有全局 pi mcp add，暂不注入（等在装了 pi 的电脑上核对它会不会把环境变量带给 MCP）
```

钥匙每一轮一把，这一轮结束就作废；帮手拿到的只有钥匙，拿不到连接。别的电脑能看到这台的能力库有哪些服务（不含连接），用来回答“Mac 上的帮手能不能查 Notion”这类问题；它们不能调用这台的服务。

## 3. 工具名和版本

- 帮手看到的工具名：`<服务编号>__<工具名>`，只留字母、数字、下划线和连字符，最长 64 个字符；重名时加短哈希。
- 服务配置版本：启动配置和连接文件元数据的摘要。轮换连接文件会作废目录，下次发现时重连；config.json 修改在重启后加载。
- 授权版本：界面管理的帮手包含持久化修订号。撤回再授权不会恢复旧版本，旧调用者必须重新列工具。
- 调用时带上列出工具那一刻的两个版本；对不上就拒绝，并告诉帮手重新列工具。这样收回授权后，正在跑的帮手下一次调用就会被拒，不会用旧授权继续用。

## 4. 上限

| 项 | 上限 |
| --- | --- |
| 一台电脑的服务数 | 32 |
| 一个服务的工具数 | 200（超过就不登记） |
| 一次调用的结果 | 512 KB（超过就给帮手一个错误） |
| 一次调用的时间 | 30 秒 |
| 本机命令服务 | 每个服务最多一个常驻进程，空闲 10 分钟退出，下次调用再启动 |

## 5. 分阶段

| 阶段 | 内容 |
| --- | --- |
| 1（Runtime） | 能力库（先从 config.json 读）、连接文件、授权和版本；最小 MCP 客户端（stdio 和 Streamable HTTP）；能力网关两个接口；能力网关入口 注入 Claude Code、Codex、dsh；pi 加 `--skill` |
| 2 | 增删服务、改授权、写连接的接口（只在产地那台电脑上改）；能力库按产地记录同步到别的电脑（不含连接） |
| 3 | 电脑客户端界面；OAuth 登录；“需要重新连接”的授权卡片 |
| 4 | pi 注入（核对后）；按需要再考虑帮手之间共享能力 |

之前写的“每个帮手在配置里直接带 MCP 命令和密钥、原样传给 harness”的做法撤掉：密钥会进到帮手的进程里，收回也不能立刻生效。每个帮手的 skill 目录（`skills`）保留。

## 6. 添加帮手

电脑客户端 设置 › 帮手 › 添加帮手：选 harness（pi、dsh、Codex、Claude Code），选模型，再选接入方式。照 Raft 的规矩：帮手由人添加，帮手自己不能加帮手、不能给自己加能力。

- 接口：`GET /v1/helpers/options`、`POST /v1/helpers`、`POST /v1/helpers/<名字>/remove`（见 [Runtime README](../../runtime/README.zh_CN.md)）。
- 加的帮手记在 `state/helpers.json`，和 `config.json` 里的帮手用同一套校验，立刻能派活，重启后还在。
- 模型服务（默认）：从本机 Magpie 的实时列表选择模型，供应商、API Key、订阅和协议转换交给 Magpie，帮手只拿网关凭据，见第 7 节。
- 直连 Key：只写入 `state/helper-secrets/<名字>.key`，不回给客户端，通过选择的环境变量交给 harness。单独传变量不能完成协议转换，也不保证不受支持的服务商可用。dsh 没有原生订阅登录。
- 订阅登录：用这台电脑上这个 harness 自己的登录；接口只记下方式，不替人登录。
- 新帮手默认可用全部能力。设置中可按服务授权；`POST /v1/helpers/<名字>/capabilities` 也支持工具白名单。先保存再生效，每次调用都检查，等待连接后再检查一次。持久化修订号保证撤回再授权也不会恢复旧版本。已经在外部执行的效果不会被撤回。
- 还没做：多台电脑之间同步帮手列表（按产地记录，第 2 阶段）、在界面上发起登录。

## 7. Magpie 模型服务与生命周期加固

参考项目是 [yetone/magpie](https://github.com/yetone/magpie)，快照
`f3eb92924e56605860f6a85a3449247c26c0bd90`，MIT 声明保留于
`runtime/third_party/magpie.LICENSE`。`helper_models.py` 按它的
`internal/agent/codex.go`、`dsh.go`、`agents.go` 适配配置，协议转换直接交给 Magpie。
Raft 源码快照 `db5e399f017d584e49597e3a28757645d6807893` 用于研究版本核验、目录校验和
连接生命周期，没有引入它的 FSL 实现。

在这台电脑运行 Magpie，在里面添加供应商或登录订阅，再在添加帮手时选“模型服务”。从
`http://127.0.0.1:3425` 读取模型，也可明确填写其他端口的本机回环地址。需要时填写网关凭据，
这里不需要供应商 Key。添加时重新检查实时列表，模型已移除或网关不可用就报错。

`POST /v1/helpers/models` 接受 `{gateway_url, key?}`，返回 `{url, models}`。
`POST /v1/helpers` 支持 `access: "magpie"`、`gateway_url`、`model` 和可选网关 `key`。
发现请求超时为 3 秒，响应上限 1 MB，绕过环境代理、拒绝重定向，只连接字面量本机回环 HTTP 地址。
这只证明目录可读，不是推理或首响应验收。

| harness | Runtime 管理的绑定 |
| --- | --- |
| Claude Code | 独立配置目录、`ANTHROPIC_BASE_URL`、网关凭据文件 |
| Codex | 每次启动的 `-c` provider 表、Responses 协议和网关凭据环境变量引用；exec 和 app-server |
| dsh >= 0.1.5 | 每次启动的 llm-pi-ai provider 补丁、模型原生协议及匹配的 ACP provider；直接说话也使用同一网关 |
| pi | 独立 `PI_CODING_AGENT_DIR/models.json` provider、模型原生协议和带 provider 的模型名 |

原生登录和直连 Key 继续可用，不改写用户配置。这一批接入已运行的 Magpie，不安装它、复制订阅账号、
另建供应商库或启用回退组。真实推理、工具转换和 pi 进程兼容性仍需用实际安装版本验收。

验证：官方 Magpie `v0.1.1345`（文件 SHA-256
`793b9c6d78c9e4fef15f49b131f1bbd98127601d37ae790e19afca657d8ea98c`）使用独立
便携数据目录、关闭账号发现运行。本地测试模型成功列出，并分别通过 Chat Completions、Messages、
Responses 返回回复；四种帮手绑定成功生成。这验证了测试模型下的网关协议转换，不代表真实厂商
或完整工具循环验收。
本机 Codex 0.163.0（exec 和 app-server）及 dsh 0.2.0-rc.2（ACP）也通过 Magpie 返回了测试回复。
首次冷启动 Codex exec 探针在 12 秒超时，后续探针成功，未据此宣称渲染首响应达标。
努力程度只提供实时模型列表声明的档位；未知档位不传，dsh 只说话的路径也遵守这一点。

MCP 按服务串行建立连接，不在能力库全局锁里等待网络，初始化失败会关闭进程。
HTTP 事件边收边解析，匹配的结果直接返回，无需等流结束；只有心跳也受总时限约束。
删除帮手先保存登记再移除运行时对象，保存失败时两边都保留。
