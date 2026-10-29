"""自动迭代流水线（见 docs/development/auto-iteration）。

采集 App 和对话里的使用信号 → 检测出问题 → 选一个 → 用模型网关写迭代方案 → （以后）改代码、
过门禁、发布、观察。每一步都是插件：内置的在 builtin/，自己写的放在 iterate.plugin_dirs 里，
配置 iterate.pipeline 决定启用哪些、按什么顺序。

  settings.py   读 iterate 一节配置
  objects.py    插件之间传的数据：Signal、Issue
  plugins.py    插件的声明、加载、权限
  store.py      App 事件、信号、问题簿、每一轮的目录
  pipeline.py   跑一轮
  cli.py        python3 -m xiaoyu_runtime iterate …
"""
