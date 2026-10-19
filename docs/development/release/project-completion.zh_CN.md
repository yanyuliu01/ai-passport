<p align="right">
  <strong>简体中文</strong> · <a href="project-completion.md">English</a>
</p>

# 项目开发完成流程

当一个项目的开发结束，项目完成流程提供两项可选的收尾动作。本页是唯一权威索引：说明触发时机、两项动作，以及共同的安全与同意门槛。

完成流程不是固定流水线，也不与发布绑定。开发者可以选其中一项或两项，顺序不限。每项动作只有在开发者确认后才会执行。

## 何时提供完成流程

出现以下任一信号时，提供这两项动作：

- 开发者说项目已完成（开发结束）。
- 开发者要求直接执行其中一项动作。

[主动邀请真机测试](../ai-guide.zh_CN.md#主动询问真机测试)独立于这里，
每次完整实现固件需求后都必须执行，不能推迟到发布或选择收尾动作时才询问。
询问是必需的，但实际刷写仍须用户同意。设备检测、未检测到设备时的开机／
数据线／电脑 USB 提示，以及未执行测试的报告方式，都遵循该交接流程。

## 两项动作

| 编号 | 动作 | 详情 |
| --- | --- | --- |
| A | 发布到 Git 并更新版本 | [动作 A](#动作-a发布到-git-并更新版本) |
| B | 更新根 README | [动作 B](#动作-b更新根-readme) |

## 共同的安全与同意门槛

每项动作都遵守下面这些不可协商的规则：

- 开始前确认同意。
- 推送或发布前确认已有可用的 GitHub 通道（GitHub MCP、GitHub skill 或 `gh`）；若都不可用，则准备好内容供手动使用并停止。
- 在开发者审查并授权之前，不推送、不打 tag、不发布。
- 没有要求时不在开发者当前分支上提交或修改；变更放在独立分支或 worktree 上承载。
- 永远不包含凭证、设备 QR 密钥、私密设备链接、个人数据或未脱敏日志。

## 动作 A：发布到 Git 并更新版本

本动作把固件或代码发布到项目仓库，并在需要时更新 GitHub release。

每一步都是需要授权的外部变更，须与开发者分开逐一确认；一次事前确认不能覆盖 commit、push、tag 和 release。

### 步骤

1. 准备仓库发布时，检查自上一版本以来已合并的用户可见变化，并按[自动构建与发布](../ci/CI-build-and-release.zh_CN.md)同时更新中英文变更日志。普通功能、应用和文档 PR 跳过此步骤。
2. 提交变更并推送——分开确认。
3. 创建并推送 tag 以触发 release 工作流——分开确认。
4. 让 tagged 构建产生合并固件 `.bin`。
5. 用产物创建或更新 GitHub release——分开确认。工作流把 release 标题默认设为版本/tag 名；release 发布后，把它改成功能名加版本号。
6. 用英文和简体中文写 release notes，覆盖新增内容、如何构建、如何使用。
7. 在真机验证发布的完整构建（见下）。

### 规则

- 遵守仓库[提交与 PR 规则](../../contribution/commit-and-pr.zh_CN.md)。
- tag 触发的构建运行 `build-firmware.yml`，它只为 tag 发布 release。见 [CI-build-and-release.zh_CN.md](../ci/CI-build-and-release.zh_CN.md)。
- 日常编译优先用 `idf.py build`（快、增量）；仅当需要合并、字节校验的 `0x0` 完整镜像时才用 `./tools/validate.sh --firmware`，例如发布或交付前。
- release notes 必须向没读过仓库的用户解释构建：新增内容、如何构建、如何使用。

### 发布后的真机验证

release 产出了合并完整构建时，在把项目视为完成前先到真机验证。下载该 release 的合并完整固件（`FoloToy-AI-Passport-full.bin`，从 `0x0` 烧录的完整构建），烧录到设备并确认正常运行。构建或上传成功不等于硬件验证。若不能运行，先停下修复，再收尾。产物与烧录见 [`CI-build-and-release.zh_CN.md`](../ci/CI-build-and-release.zh_CN.md)。

对发布产物同样执行[设备访问与同意检查](../ai-guide.zh_CN.md#主动询问真机测试)。同意发布不等于同意烧录。无法开展真机测试时，应明确保留未验证状态。

## 动作 B：更新根 README

本动作更新仓库根目录的 `README.md` / `README.zh_CN.md`，让它介绍新发布的应用，并保持其中“文档”一节是最新的。

### 规则

- 根 README 这一对是项目自己的总览和文档索引；中英文在同一个变更里一起改。
- 写清楚应用做什么、怎么用（交互、模式、按键、持久化与说明），不要只写一行简介加链接。
- 新增或移动的文档登记到“文档”一节。
- 遵守仓库语言规则：默认 `.md` 用英文、配对的 `.zh_CN.md` 用简体中文，并互相链接。

### 步骤

1. 确认同意。
2. 在承载应用的分支上更新这一对 README。
3. 提交；推送需要另外确认。

## 相关文档

- 提交与 PR 规则：[commit-and-pr.zh_CN.md](../../contribution/commit-and-pr.zh_CN.md)
- 文档规范：[doc-conventions.zh_CN.md](../../contribution/doc-conventions.zh_CN.md)
- tagged 固件构建与发布：[CI-build-and-release.zh_CN.md](../ci/CI-build-and-release.zh_CN.md)
