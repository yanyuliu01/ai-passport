<p align="right"><strong>简体中文</strong> · <a href="windows-client-handoff-20261105.md">English</a></p>

# Windows 客户端集成交接（2026-11-05）

本分支是供审查和集成的源码检查点，不是发布版本，也不代表已在 Windows 安装。它保存基于 `9808e23` 的 Mac 工作区改动，并包含公开人设更新 `12d1e4d`。源码标记为桌面端 0.1.44、Android 0.41.1（versionCode 101）、Runtime 0.28.29；这些是源码版本，不代表 Windows 或手机当前安装的版本。Windows 可能已有独立进展，集成前先核对。

## 先保留，再比较

1. 在 Windows 阅读 `AGENTS.md`，检查 `git status --short --branch`、已有工作树、当前分支和已安装的桌面端／Runtime 版本；有配置时按规则读取共享历史。不要丢弃本地修改、替换配置、凭据、聊天记录或中断活动任务。
2. 获取 `github`，在独立工作树检查此分支，选择尚未使用的目录：

   ```powershell
   git fetch github
   git worktree add --detach ..\xiaoyu-mac-review github/codex/windows-handoff-20261105
   ```

   将其提交与 Windows 分支及未提交文件比较，按行为选择性集成和解决冲突，并运行测试；不要重置 Windows 工作区，也不要假定较大版本号已覆盖独立修复。保留已有安装包以便回退，不将真实状态或凭据提交到 Git。
3. 先前背景见[项目进展](project-status.zh_CN.md)和 [Windows Runtime 同步恢复交接](windows-runtime-recovery-handoff.zh_CN.md)；它们是历史检查点，不代表 Windows 当前状态。

## 需要核对的变化

- 桌面端：账号和中继恢复、设备证明、图片传输、微信连接与附件、帮手和 Runtime 启动，以及打包。集成前对照 Windows 原有生命周期和重连行为；查看 `companion/desktop/src/core/`、`src/relay/`、`src/ui/` 及其测试。
- Android 和 Runtime：历史同步恢复、媒体、渠道归属和微信投递、对话上下文与历史读取、中断和投递回执。这是一份跨端配合的源码快照，不能假定只更新桌面端就能获得同等行为。阅读 `runtime/README.md`、`runtime/xiaoyu_runtime/channels/` 和相关测试。
- Runtime 0.28.29 此前已用于 Mac 本地服务，并做过一次真实微信历史追问验证；当时桌面 App 内置 Runtime 据报告较旧。这是历史证据，改动前重新核对实际运行版本和包内版本。一次模型工具追问超过三秒，真实手机计时及完整跨设备恢复尚未验收。
- 分支只包含源码改动，不包含个人状态、凭据、已安装二进制或 Windows 实测证明。这是从较大工作区整理的检查点，需要审查；其他 Mac 工作树及根目录中较旧的未提交改动不在其中。这里使用的 `12d1e4d` 公开人设与 Mac 源码工作区中的人设不同，因此此分支并非已部署包的逐字节复刻。

## 在 Windows 构建和验证

使用独立的原生 Windows 工作树，准备 Node.js 24+ 和 Python 3.11+。先审查 `companion/desktop/tools/build-windows.ps1`，然后在仓库根目录运行：

```powershell
& .\companion\desktop\tools\build-windows.ps1
```

脚本在本地构建目录安装锁定依赖，打包原生 Runtime 与帮手，运行桌面测试，构建 NSIS 安装包并输出 SHA-256。确保 `package.json`、锁文件、Runtime manifest 和安装包对应同一份审查过的源码；Mac 二进制或跨平台预览包不能证明 Windows 原生运行。集成后的源码如果不同于已有发行版，应使用新桌面版本。安装或重启运行中的服务前，遵守项目规则要求的授权。

按实际提交和版本记录构建、测试结果。覆盖全新启动、已有数据和账号保留、帮手发现、Runtime／中继断线恢复、任务连续性、Windows／Mac／手机历史与媒体同步，以及需要时的微信链路。在真实客户端测量首个有意义回复，按跨栈契约报告 p50、p95、最大值和超时次数。明确列出未运行的检查，不能把构建成功当成设备或端到端验收。参照[跨栈可靠性规则](cross-stack-reliability.zh_CN.md)、[桌面端说明](../../companion/desktop/README.zh_CN.md)及[构建与测试](engineering/build-and-test.zh_CN.md)。
