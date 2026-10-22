<p align="right"><strong>简体中文</strong> · <a href="windows-public-node-handoff.md">English</a></p>

# Windows 家庭公网节点：Codex 交接

交接日期：2026-10-22，时区 Asia/Shanghai。本文是交接时的检查点，后续执行前重新核对实际版本与网络；不是公网已上线的证明。优先完成手机异网通信，前端美化和新增 Agent 管理暂缓。

## 直接给 Windows Codex 的启动指令

把配套源码包解压到一个新的本机目录，再把下面这段发给家里 Windows 上的 Codex：

> 请阅读 `docs/development/windows-public-node-handoff.zh_CN.md`，接着做小幽的家庭 Windows 公网节点。Mac 在公司，Windows 在家常开，手机需要在蜂窝网络通过扫码连接，并能保留多台电脑。先确认当前执行环境可以正常联网，再验证家庭公网条件；目前只有本地加密协议测试通过，真实公网入口尚未上线。复用现有客户端和指纹 TLS/HPKE，不依赖 Tailscale，不重新设计前端，不使用 Astra。先检查原工程的 Git 状态、已有 Runtime 和共享历史，再核对源码快照，所有构建在隔离目录进行。保留原配置、历史、密钥和正在运行的任务。遇到 EACCES/10013 要报告权限阻止，不要反复换命令或判断家庭网络不通。按文档顺序推进；需要主人操作时，只请求当前缺少的具体信息或权限，不要重新询问是否购买服务器。

本文保留原有授权范围，不授予额外的系统或网络权限。此前已授权使用现有 Windows 任务、客户端实现和隔离构建测试；最近两轮网络任务只授权只读检测，没有修改任何系统或网络设置。

## 1. 目标与现状

目标拓扑：

```text
手机（蜂窝网络） ──出站 WSS──┐
                            ├─ 家里常开 Windows：加密转发节点
公司 Mac        ──出站 WSS──┘
```

节点不执行转发来的任意命令，不代管模型密钥，不暴露 Runtime HTTP API。聊天内容由端点间 HPKE 加密，WSS 另行保护传输；Runtime 继续在各自电脑执行。扫码完成设备身份、一次性邀请和电脑侧确认，不能代替公网可达性。

| 部分 | 交接时版本及证据 | 仍未确认 |
| --- | --- | --- |
| 桌面源码 / Mac 候选包 | 0.1.5；Electron 44.9.3；52 项桌面测试通过 | Windows 安装包及原生运行 |
| Android | 0.30.1 / versionCode 56；161 项单元测试通过；已本地发布 | 手机是否已安装及真实扫码 |
| Mac 包内 Runtime | 冻结 0.22.0 | 不能把它标为中央服务的版本 |
| Mac 中央 Runtime | 最后健康检查为 0.22.1 | 执行前重新检查，禁止自行重启 |
| 家里 Windows Runtime | 管理通道最后通告 0.20.0，在线 | 原生客户端升级、实际配置兼容性 |
| 独立公网入口 | 未上线，真实入站未验证 | 路由器 WAN、映射、防火墙、外部连接 |

Mac 0.1.5 构建来源：

- 桌面源码 SHA-256：`6b9ab9b3e092038fee475e7aab1245a5cd188c9c84037dedc43d50ca06520d99`。
- 冻结 Runtime 源码 SHA-256：`21563f0cc92115a7d6f4b37fde947af30621a5b2c09afa1ee2ac921f4be94e28`。
- Mac DMG SHA-256：`c014b1f894ed30f7f48815e17b502efb765680f41aa027c0ffb3a161b7057e4c`；这是 Mac 文件摘要，不是 Windows 包的摘要。

已有原生 Mac 封包回归通过启停、关闭窗口后台运行、重启保留节点身份、无公网地址时阻止邀请、二维码生成及既有聊天/工作/审批流程。真实 Java/OkHttp 经本地 WSS 通过证书指纹校验、错误指纹在 HTTP 头发出前拒绝、同时连接两台电脑、丢失响应后重连且消息只执行一次。夹具结果不等于手机异网验收。模型首个有效回答 ≤3 秒没有真实样本。

## 2. Windows 检测的关键结论

此前通过已授权的 Runtime 任务 `c314` 做了两轮只读检查，未改源码、配置、路由器、端口映射、防火墙、Tailscale，也未安装或重启服务。

- 发现物理 Wi-Fi 网卡、家庭网关、全球 IPv6 地址；TCP 18795 当时空闲，三个防火墙配置均开启。需要重新确认，不能据此认定已开放入站。
- 首轮 IPv4/IPv6 curl 实际为 `curl (7)`。不要把更早一次的 `SEC_E_NO_CREDENTIALS` 当作本轮错误。
- 补查中，现有 Node 24 的 IPv4、IPv6和网关 HTTP 连接均为 `EACCES`；.NET IPv6 TCP 为 `AccessDenied / 10013`。TLS 握手尚未开始，当前执行环境拒绝了连接。
- IGD/UPnP 未发现、NAT-PMP 超时属于未确认结果。受限环境下的这些结果不能证明路由器不支持、运营商 CGNAT、家里无法上网或没有公网入口。
- 路由器品牌/型号、WAN IPv4、IPv4 入站与真实 IPv6 入站都未知。Mac 公司网络此前没有检测到全球 IPv6，因此只有家庭 IPv6 入站也不足以证明公司 Mac 可连接。

先在允许联网的正常 Windows 会话确认基本连接。若仍被权限或安全策略拒绝，记录实际错误，只请求合法的必要访问方式；不要绕过限制、修改沙箱、全局关闭防火墙、禁用证书校验或反复下载同一套工具。

## 3. 源码交接与原工程保护

这批桌面代码和部分 Runtime 变更在 Mac 工作树中尚未提交。不能假设 `git pull` 会拿到这些内容，也不要为传递本次交接而擅自提交或推送。

配套 `xiaoyou-windows-node-handoff-20261022.zip` 是可隔离构建的快照，包含桌面 0.1.5 源码、冻结 Runtime 0.22.0、必要规则/技能和本文。它不是完整 Git/固件工程，也不包含真实配置、聊天状态、系统凭据、设备 QR、私钥、依赖缓存、Mac 二进制或 Windows 安装包。

1. 用同名 `.sha256` 文件核对 ZIP 的 SHA-256，然后解压到新目录。
2. 检查包内 `SOURCE-MANIFEST.json`：逐文件摘要、桌面和 Runtime 源码摘要必须一致。
3. 先读原工程 `AGENTS.md`，执行 `git status --short --branch`，保存已有修改的清单。不要清理原工程或覆盖整个目录。
4. 原工程有本地配置时运行共享历史读取器；没有配置就跳过。历史只作为证据，不能当成新的授权。
5. 核对五个 `passport-*` 必需技能是否可用。缺失时遵照原工程规则自主安装；包内 `skills/` 提供项目来源。
6. 源码包中直接构建需要的根目录是解压后的 `xiaoyou-windows-node-handoff`，不是旧的构建缓存目录。不要把原配置/状态复制进交接包再对外传递。

原工程命令：

```powershell
git status --short --branch
if (Test-Path runtime/config.json) {
    python.exe runtime/xiaoyou_runtime/shared_history.py --config runtime/config.json --context
}
```

共享历史可能包含私人内容，留在本机，不要直接放进交付日志。

## 4. 接手时读这些文件

按根 `AGENTS.md` 路由阅读，不必遍历所有 README：

- [跨栈可靠性规则](cross-stack-reliability.zh_CN.md)、[AI 工作流](ai-guide.zh_CN.md)、[Companion 说明](../../companion/README.zh_CN.md)。
- [桌面检查点](../../companion/desktop/README.zh_CN.md)、[Runtime 说明](../../runtime/README.zh_CN.md)。
- `companion/desktop/src/core/relay-host.ts`：本机 TLS 节点、身份持久化、双栈监听、地址探测。
- `companion/desktop/src/core/pinned-tls.ts`、`relay.ts`：发送凭据前校验证书指纹；本机回环连接与邀请公网地址分离。
- `companion/desktop/src/core/peers.ts`：可信设备、HPKE、受限 RPC、重试和请求去重、跨端同步。
- `companion/desktop/src/main.ts`、`preload.cts`、`shared.ts`、`src/ui/app.tsx`：显式启用、恢复、后台运行、设置页与 QR。
- `companion/desktop/src/core/runtime-host.ts`、`runtime-layout.ts`、`setup.ts`：复用已有 Runtime、拥有型服务、身份核对与恢复。
- `companion/desktop/src/test/relay-host.test.ts`、`network.test.ts`、`runtime-interop.test.ts`：节点及跨端回归。
- `companion/desktop/tools/build-windows.ps1`、`bundle_runtime.py`、`check-package.cjs`：原生构建和平台/源码校验。

Android 指纹入口为 `RelayConnection.java`、`PinnedRelayTLS.java`、`RelayManager.java`，在原完整工程 `companion/android/app/src/main/java/com/yanyuliu/pockethub/` 中。此源码包不带 Android 工程；本轮优先复用已发布 App，不需要重新打 APK。

## 5. 执行顺序

### A. 先解除检测的不确定性

先核对 Windows 工具能正常启动和执行。只读命令示例：

```powershell
Get-Command node.exe, npm.cmd, python.exe -ErrorAction SilentlyContinue
node.exe -e "console.log(process.version); require('node:sqlite'); console.log('sqlite available')"
python.exe -c "import sys,ssl,ctypes,sqlite3; print(sys.version); print('native imports available')"
Get-NetIPConfiguration
Get-NetRoute -DestinationPrefix '0.0.0.0/0' -ErrorAction Continue
Get-NetRoute -DestinationPrefix '::/0' -ErrorAction Continue
Get-NetFirewallProfile | Select-Object Name,Enabled,DefaultInboundAction
Get-NetTCPConnection -State Listen -ErrorAction Stop | Where-Object LocalPort -eq 18795
```

命令有权限或原生模块错误时，保留错误码，不把失败当作地址不存在或端口空闲。对 IPv4、IPv6分别检查直连能力，HTTPS 保持正常 TLS 校验；单项网络超时约 4–5 秒，整轮控制在约 90 秒。无必要不再重复首次失败的探测。

若普通浏览器能打开实际家庭网关，由主人在本机自行登录，读取路由器品牌/型号、WAN IPv4 和 IPv6 入站设置。不索取密码、完整配置备份或已有登录凭据。公网出口 IP 不等于路由器拥有该地址，更不等于可入站。

### B. 确认可行的家庭公网路径

- 路由器 WAN 为可用公网 IPv4时，核对外部地址和入站策略，规划单个外部 TCP 端口到 Windows 稳定 LAN 地址的 18795 端口。不要覆盖已有映射。
- 全球 IPv6 需要单独验证路由和入站防火墙，并覆盖实际手机与公司 Mac 的地址族能力；不应只因有 IPv6 地址就结束验收。
- WAN 为私网/共享地址或存在多层 NAT 时，明确哪些条件未满足，再按实际设备与运营商能力推进。优先家庭自托管，不擅自购买服务器或改用 Tailscale。
- 进入部署阶段前，给出准确的目标网卡、内外端口、规则名称、作用范围和撤销步骤。根据已有授权执行；需要管理员/路由器权限、缺少授权或会影响现有映射时，仅请求必要的具体操作。第一轮检测不做这些修改。
- 不启用 DMZ，不把 Runtime 端口或本机 HTTP API 暴露到公网，不全局关闭防火墙，不操作公司路由器。端口空闲也不能代替公网可达性验证。

### C. 构建并启用 Windows 节点

先完成下面第 6 节的构建与原生检查，再在客户端确认本机服务、启用“常开电脑节点”、填写已核对的公网地址并检测。默认本机监听 TCP 18795，WSS 对外地址和外部端口由实际映射决定。

复用现有 TLS 指纹方案，不强制购买域名或申请公共 CA 证书。私钥、入口密钥、房间编号留在 OS 加密存储；禁止用明文文件回退系统凭据存储失败。邀请不携带本机 `dialURL`；宿主回环连接避免依赖路由器回流。

电脑侧公网探测成功后，再实际用手机蜂窝网络扫码、核对指纹、在电脑确认。邀请两分钟过期；一个手机可保留多台电脑。随后用公司 Mac 实测连接，同一局域网或回环成功不能代替这些步骤。不同既有空间不能靠改写空间/会话 ID 强行合并。

关闭窗口应继续转发，正常重启客户端应恢复原身份；登录自启动由用户启用。电脑休眠/关机会中断入口。公网地址变化后的自动发现、证书自动轮换、节点自动切换和完整 NAT 打洞未实现，不能对外承诺已经具备。

## 6. Windows 构建和已知问题

标准构建依赖 Node.js 24+、可正常导入原生模块的 Python 3.11+、锁定的 npm 依赖、PyInstaller 6.22.3 和 Pillow 11.3.0。在隔离源码根目录执行：

```powershell
& .\companion\desktop\tools\build-windows.ps1
```

脚本会安装隔离依赖、构建原生 Runtime、运行桌面测试、构建 NSIS 并计算摘要。已有依赖时可以审查后使用 `-SkipDependencyInstall`。若组织策略不允许运行脚本，报告阻碍并请求正常的允许方式；不全局修改执行策略。

旧的 Windows 0.1.0 构建曾遇到：官方 Node 26 启动 `0xC0000142`；NuGet Python 3.13/3.11 的 `_ctypes`/`_ssl` 加载失败；curl `SEC_E_NO_CREDENTIALS`；Electron 下载失败；双 Runtime 测试超时。已有 Node 24 曾可运行。这些是历史失败，需在当前正常会话复核，不能盲目重复下载，也不能当作当前源码已在 Windows 测试通过。

`tools/bundle_windows_embedded.py` 与 `package:win-preview` 是现有预览备选路线。仅在审查源码/锁定官方下载摘要后使用；该路线的 `native_execution_verified: false` 必须保留，直到真实原生启动和回归通过，不能把组装文件当作原生验证成功。

`check-package.cjs` 会核对目标 OS/架构、Runtime 源码摘要和桌面源码。禁止把 Mac `runtime-dist` 塞进 Windows 包或跳过该校验。源码包的 Runtime 是冻结 0.22.0；若改用其他版本，先重新构建、验证和记录版本，不能混用旧 manifest。

若改动了桌面源码/依赖，从 0.1.6 或更高未占用版本继续，核对 `package.json`、锁文件和显示版本一致。不要给不同源码继续贴同一平台的 0.1.5 标签。App 若必须修改，先协调其他任务和当前已发布版本，再按 Companion 流程递增、测试和本地发布，不覆盖已发布的更高版本。

## 7. 验收和回退

按实际版本记录以下结果，失败与未执行分别写清：

- Windows 安装、首次确认/复用已有 Runtime、托盘/后台/退出/登录恢复、原身份和历史保留；不自动重启已有活跃 Runtime。
- 节点无公网地址时不假装就绪；错误指纹在凭据发送前拒绝；过期邀请和 Runtime 身份变化明确失败。
- 手机蜂窝网络真实扫码/电脑确认、文本/语音/图片传输、可见的失败/恢复；单次二进制边界当前为 2.8 MB。
- 同一手机同时保留 Mac 和 Windows，确认各自来源与会话归属；真实跨端记录收敛、目录操作和审批只生效一次。
- 强制中断节点后恢复，丢响应请求沿用原编号、不得重复执行；缓存/待发请求不丢失，不能把中继当作离线邮箱。
- 记录真实模型首个有效回答从发送到渲染的样本数、p50/p95/max 和所有超过 3 秒/超时样本，不能用连接状态或 ACK 冒充回答。

工程门禁按根规则执行。源码包不是完整固件工程；完整门禁需要匹配的完整工程和 ESP-IDF 5.5.3。依赖/设备不具备时记录 NOT RUN，不为网络检测安装无关固件工具链，也不把 Mac 历史门禁当作 Windows 本轮结果。

已有 Runtime 的重启夹具曾因同目录两个实例共用 `cards.json.tmp` 失败；门禁重跑通过，但该既有竞态没有被通信增量修复。保留失败证据，不降低断言或扩大超时来掩盖问题。

回退时停止新增节点、撤销本次新增的特定端口映射/防火墙规则，恢复记录过的旧入口。保留原配置、OS 加密凭据、缓存、待确认请求和会话，不清空用户数据。不要把旧版不支持新自签名节点误判为损坏，也不要以关闭 TLS 校验“修复”连接。

最终交付：Windows 安装包及 SHA-256/源码与 Runtime manifest、脱敏检测和外部验收结果、具体未完成项与恢复办法。分别报告：

```text
Build: PASS / FAIL / NOT RUN
Host tests: PASS / FAIL / NOT RUN
Device tests: PASS / FAIL / NOT RUN
Regression / first response <=3s / connection recovery / multi-end synchronization:
  measured results, versions, scope, evidence, and unverified items
```

不要报告“完成异网通信”直到手机蜂窝网络及目标公司 Mac 真正连接并验证。
