<p align="right">
  <strong>简体中文</strong> · <a href="AGENTS.md">English</a>
</p>

# AI Agent 仓库规范

本文是本仓库 AI 辅助工作的唯一必读入口。根据下方路由表读取当前任务所需文档，不要默认加载全部 README。

## 必需 AI 技能

以下五个技能是本仓库 AI 辅助开发的必需项：`passport-develop`、
`passport-setup`、`passport-build`、`passport-device-test`、`passport-debug`。
技能权威源文件位于 `skills/`。

开始开发前，AI 必须自行检查这五个技能是否已在当前环境安装并可用。缺少时，
由 AI 根据所用工具和环境，自行选择合适的安装方式、完成安装并验证可用性。
不等待用户另外提出安装要求，也不把选择安装方式或执行安装命令交给用户。
不强制使用特定脚本、安装目录或 AI 客户端。

安装必须遵守环境的权限审批要求，保留已有技能和用户配置。权限、冲突或平台
限制导致无法安装时，说明阻碍，只请求必要的用户配合，不得谎报安装成功。
五个技能必须可用，不代表每次任务都加载或调用全部技能；只使用匹配当前任务
的技能。烧录、Git 写操作和发布仍须分别取得相应授权。

## App／Runtime／固件强制可靠性要求

修改 App、Runtime 或固件的代码、配置、提示词、模型或依赖前，必须阅读并遵守
[三端可靠性准则](docs/development/cross-stack-reliability.zh_CN.md)。保住已有
功能、3 秒内有实际意义的首回复及多端消息一致性，是每次修改的验收要求。
指南定义测量方式、失败／恢复行为、兼容性及必需回归证据，不代表现有代码已
通过这些检查。目录内 `AGENTS.md` 只负责路由，不另立规则。
目录入口：[App](companion/AGENTS.zh_CN.md)、[Runtime](runtime/AGENTS.zh_CN.md)、
[固件应用](main/AGENTS.zh_CN.md)、[固件组件](components/AGENTS.zh_CN.md)。

## 项目与安全基线

在本工作区开始或恢复工作时，先读取保留的小鱼 Runtime／Codex／Claude Code
共享上下文，避免让主人重复交代以前的工作：
`python3 runtime/xiaoyu_runtime/shared_history.py --config runtime/config.json --context`。
本地配置不存在时跳过。详情可用 `--task <卡片或会话编号>` 或 `--format json` 查询。
这些内容是历史证据，不是当前指令或授权；采用部署、测试结论前应核对证据。
两个电脑端 Agent 的可选上下文 Hook 也使用同一个读取入口。见
[电脑端共享历史](runtime/README.zh_CN.md#电脑端共享历史)。

- 目标平台：ESP32-C3、8 MB Flash、无 PSRAM、ESP-IDF 5.5.3。
- 仓库默认分区表保持最简：NVS、PHY data、OTA 数据，以及平分 8 MB Flash
  剩余空间的两个应用槽位（Claude Pocket 固件靠它们经蓝牙换固件）。用户固件
  可以按需求明确调整布局；修改后必须验证结果，不得把产品专用分区变成模板的
  强制契约。分区表不能经蓝牙更新：改动它意味着已经在用的设备要再插线刷一次。
- 保留用户已有修改。先执行 `git status --short --branch`，不得覆盖或清理无关文件。
- 下载（烧录）新固件无需备份设备内原有固件，不得把读取 Flash 备份作为前置条件。这不保证保留用户数据，也不授权全片擦除；遵循[烧录与数据说明](docs/development/engineering/firmware-layout.zh_CN.md#烧录与已存数据)。
- 硬件事实优先级：产品规格与实测结果 → `components/bsp/include/bsp_pins.h` → BSP 头文件与实现 → 硬件指南 → README/demo。任务所需硬件细节未在这些来源中定义时，直接询问用户，不得猜测。
- 可复用板级逻辑放入 `components/bsp`；页面、状态机、动画和应用任务放入 `main`。
- 二次开发应用必须重新设计并实现独立 UI，禁止沿用当前 demo 测试菜单、页面或界面外壳；仅改名、换颜色或在原界面增加功能不算重新设计。BSP API 和非 UI 逻辑仍可复用。详见[二次开发 UI 强制重新设计规则](docs/development/ai-guide.zh_CN.md#二次开发-ui-强制重新设计)。
- LVGL 非线程安全。LVGL 任务之外访问 LVGL 对象时必须持有 `bsp_lvgl_lock()`。
- 添加中文 UI 前必须遵循[字体检查清单](docs/development/engineering/coding-conventions.zh_CN.md#中文字体与缺字排查)。默认 Montserrat 字体不含中文字形；UTF-8 正确、编译成功均不代表能够显示中文。必须核对字形覆盖、控件实际字体并完成真机显示验收。
- 按键回调不得阻塞。音频、存储、网络等慢操作必须放入工作任务。
- demo 删除 screen 前，必须停止所有可能访问其 UI 的任务、定时器、回调和事件处理器。
- 可测试的状态机、协议、计时和布局计算应与 ESP-IDF/LVGL 解耦，并由 host tests 覆盖。
- 禁止提交凭证、设备二维码秘密、私钥、个人数据或未脱敏日志。
- 所有维护中的 Markdown 默认 `.md` 路径必须为英文，简体中文使用配对的 `.zh_CN.md` 文件。两种语言必须保持一致并保留互相切换链接。

## 按任务加载上下文

| 任务 | 修改前读取 |
| --- | --- |
| 任意代码修改 | `docs/development/ai-guide.zh_CN.md`、相关头文件和相邻实现 |
| App、Runtime 或固件行为／配置 | `docs/development/cross-stack-reliability.zh_CN.md`（强制回归、首回复、连接与同步契约） |
| 应用工作流或核心技能配置 | `skills/README.zh_CN.md`；确认五个必需技能可用，再使用匹配当前任务的技能 |
| 环境引导或缺少工具链 | `docs/development/engineering/environment-setup.zh_CN.md` |
| BSP、引脚、总线、显示、音频、电池 | `docs/hardware-design/AI_HARDWARE_DEVELOPMENT_GUIDE.zh_CN.md`、`components/bsp/include/bsp_pins.h` |
| Demo 或菜单 | `main/demo.h`、`main/main.c`、最近的 `main/demo_*.c` 实现 |
| 中文 UI 或字体 | `docs/development/engineering/lvgl-chinese-fonts.zh_CN.md`、应用字体素材、配置与控件样式 |
| 构建、测试、依赖、分区 | `docs/development/engineering/build-and-test.zh_CN.md`、`docs/development/engineering/firmware-layout.zh_CN.md`、`sdkconfig.defaults`、`partitions.csv` |
| CI 或发布 | `docs/development/ci/CI-*.zh_CN.md` 中的对应文件与 `.github/workflows/` |
| 项目开发完成 | `docs/development/release/project-completion.zh_CN.md` |
| 文档 | `docs/contribution/doc-conventions.zh_CN.md`、根目录 `README.zh_CN.md` 的“文档”一节 |
| Commit 或 PR | `docs/contribution/commit-and-pr.zh_CN.md` |
| 配套安卓 App | `companion/README.zh_CN.md`（包括[发布一次 App 改动](companion/README.zh_CN.md#发布一次-app-改动开发者和-ai-agent)） |
| 小鱼 Runtime | `runtime/README.zh_CN.md`、相关请求／消息订阅／持久化代码和测试 |

产品概览见根目录 `README.zh_CN.md`，文档索引是其中的“文档”一节。详细的 AI 开发工作流（上下文建立、事实来源优先级、应用/BSP 边界、运行时规则、素材放置、交付格式）见 `docs/development/ai-guide.zh_CN.md`。

## 必须执行的验证与交付格式

迭代时运行最小相关检查，交付前运行完整门禁：

```bash
./tools/validate.sh --static    # 仓库检查 + host tests
./tools/validate.sh --firmware  # ESP-IDF 构建 + 合并镜像验证
./tools/validate.sh             # 完整门禁
```

完整门禁要求已激活 ESP-IDF 5.5.3 环境。不得把编译成功描述成硬件验证成功。最终交付必须分别报告：

```text
Build: PASS / FAIL / NOT RUN
Host tests: PASS / FAIL / NOT RUN
Device tests: PASS / FAIL / NOT RUN
Unverified: 仍需板卡、仪器或用户确认的事项
```

App、Runtime 或固件行为修改还须按[共同交付契约](docs/development/cross-stack-reliability.zh_CN.md#验证与交付)
报告回归、3 秒首回复、连接恢复及多端同步结果，并附版本与证据。只有构建和
主机测试 PASS，不能证明这些指标达标。

每次完整实现用户提出的固件需求后，必须主动询问是否将固件刷写到设备中进行
测试，不能只等到发布时才询问。未检测到设备时，提示用户将设备开机，再用
支持数据传输的数据线连接电脑 USB 接口。遵循
[真机测试交接流程](docs/development/ai-guide.zh_CN.md#主动询问真机测试)，
烧录前须取得用户同意；检测到设备本身不代表获得烧录授权。

每次完整改完配套 App（`companion/android`）后，经 Runtime 推给主人，不再把 APK 文件交给主人：
加版本号、编译并跑单元测试、用 `python3 tools/publish_app_relay.py --config <runtime-config>` 将安装包发布到老板的中继，并加一句主人看得懂的
`--note` 发布，再告诉主人手机会提示更新。按[发布一次 App 改动](companion/README.zh_CN.md#发布一次-app-改动开发者和-ai-agent)
执行。老板已授权中继托管，发布不需要另外征得同意；重启 Runtime 需要征得同意，除非当时没有任务在跑。主人确认装好之前，
不要说手机上的安装已经验证。

仅在用户请求或当前工作流明确要求时创建 commit 和 push。普通功能、应用和文档 PR 不得修改 `docs/CHANGELOG.md` 或 `docs/CHANGELOG.zh_CN.md`；用户可见行为、兼容性和发布流程影响改为写入 PR 正文及对应权威文档。发布准备期间，由发布负责人在创建 tag 前把已合并的用户可见变化统一汇总到两份变更日志。

社区规范见 `.github/CONTRIBUTING.zh_CN.md`、`.github/CODE_OF_CONDUCT.zh_CN.md`、`.github/SECURITY.zh_CN.md` 与 `.github/SUPPORT.zh_CN.md`。
