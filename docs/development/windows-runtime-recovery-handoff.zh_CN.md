<p align="right"><strong>简体中文</strong> · <a href="windows-runtime-recovery-handoff.md">English</a></p>

# Windows Runtime 同步恢复交接

这是源码交接，不是 Windows 安装包，也不代表两台电脑已经完成真实验收。先阅读根目录
agent 规则和共享历史；检查 Windows 当前工作区、运行进程、任务和配置时不要打印凭据。
保留 Windows 现有未提交工作、兼容的回退包和用户数据。

## 源码与当前边界

GitHub 的 `codex/runtime-sync-recovery-20261102` 分支以 Mac／Windows 集成分支为基础。
本分支包含同步恢复、Runtime 历史互通和目录性能修复，已安装在 Mac 上，Runtime 版本为
**0.27.7**，按文件名顺序计算的 Python 源码树 SHA-256 为
`9b428a6960574b6e76e1350f06ad35cd7f6d473933ee02bdd15c269392683e72`。
此前构建的版本号相同、源码摘要不同，不能仅凭版本号确认构建。该摘要取决于文件名和字节，Windows 的换行转换可能
改变摘要；还应核对 Git 提交和构建清单。

Mac 最后一次检查时主对话归 Mac，Windows 报告 Runtime **0.24.0** 且在线；操作前重新
核实。Mac 已设置 `mesh.home=true`，尚未启用 `mesh.require_owner`。本分支包含此修复
所需且经过测试的 Runtime 源码，包括从另行开发的性能候选移植的目录优化；不包含后续未提交的
Android 和桌面端工作。不要用这份较早的集成快照覆盖 Windows 桌面客户端或其未提交工作。

在已有仓库中先抓取分支，再创建独立工作区，不切换或重置现有工作区。如果建议的分支名或
目录已存在，请换一个未使用的名字：

```powershell
git status --short --branch
git remote -v
git fetch https://github.com/yanyuliu01/ai-passport.git refs/heads/codex/runtime-sync-recovery-20261102
git worktree add -b codex/windows-runtime-sync-review ..\ai-passport-runtime-sync-review FETCH_HEAD
```

集成前检查抓取到的提交及它与 Windows 工作的差异。不要把真实配置、令牌、设备密钥、
历史或日志提交、打包传输；不要清除或重新配对资料来掩盖升级问题。

## Windows 原生构建与分阶段升级

1. 记录已安装的 Runtime 和桌面版本、运行中的工作、负责人及成员状态，以及回退所需的
   Runtime 程序、清单和配置。本次 Runtime 交接不发布手机更新、不刷写设备、不替换桌面端。
2. 在 Windows 按 `companion/desktop/requirements-build.txt` 安装锁定依赖，使用
   `companion/desktop/tools/bundle_runtime.py` 原生打包；Mac 的 PyInstaller 可执行文件
   不能在 Windows 上使用。在独立工作区构建，核对生成的清单和依赖闭包。运行 Runtime
   测试，包括 `test_projection_recovery.py`、`test_replica_failure.py`、
   `test_owner_fencing_fault.py`、`test_runtime_conversation_sync.py`、`test_mesh.py`、
   `test_ownership.py`、`test_conversation_directory.py`，以及适用的仓库门禁。
   报告 Windows 专属失败和跳过项，不能把 Mac 测试当成 Windows 通过。
3. 保留 Windows Runtime 身份、现有 mesh 密钥、成员地址、历史和待处理工作。Mac 负责
   主对话期间，**不要在 Windows 设置 `mesh.home=true`**。分别核对两台运行中的实际负责人；
   配置文件中有设置，不代表进程已加载，也不代表另一台已收到绑定。
4. 仅按主人授权和仓库对运行中任务的规则安装或重启。检查进程健康、身份、客户端恢复连接
   和成员同步；在无需回退之前保留旧程序和数据。推送源码不会自动安装 Runtime。

`mesh.require_owner` 默认是 `false`。只有在**所有参与的 Runtime** 都支持该选项、且负责人
已经核实之后，才协调在各成员上开启，包括 Mac。开启后，负责人未知或冲突时，该成员会让
普通对话排队；旧版本或未开启的成员仍可能在本地回答。经授权重启后，应确认运行中的进程
确实加载了设置。不能因为 Windows 升级或 Mac 配成 home 就宣称双端防重答已完全生效。

## 故障、性能与验收

检查两台 Runtime 的主人和助手消息均在正确对话中各出现一次，重启后仍可访问。显式
`recall` 可以有界查询留存归档和已收到的远端副本；覆盖不全时会提示，未接收或未留存的
历史无法凭此找回。重置隐藏、代执行、被负责人隔离及其他对话的记录应继续排除。现有自动
本地上下文选择保持原样，其触发机制和预算需要另行设计。

Mac 安装候选的 Runtime 主机测试 1,392 项及完整仓库门禁通过。实际 Runtime `recall` 返回了
一条留存的 Windows Runtime 对话；客户端首答显示计时、
Windows 实际收敛和网络故障恢复仍未验证。安装包和先前版本一样提示缺少 `sherpa-onnx`，
语音尚未验证。

测试临时投影写入失败、重试和进程重启；命令接收回执及最终结果丢失；重复投递；负责人离线
与重新连接。无法确认是否已发生的副作用应保留为未知，核对目标状态前不能自动重做。回退到
不跟踪应用进度的旧版前，先处理待恢复投影并确认相关成员已同步完成。现有测试覆盖进程重启，
不代表已证明断电持久性。

在相同负载下对比修改前后的正常同步耗时及空闲／忙碌 CPU，不新增高频轮询或反复全量扫描。
在真实 Windows、Mac 和手机上检查同一消息的编号及最终状态、断线重连恢复，以及从点击发送
开始到首条有效回复显示的时间是否满足 3 秒要求。报告全部样本、版本、传输方式及剩余失败；
构建、模拟故障测试、成员在线状态和 GitHub 推送都不能代替端到端验收。
