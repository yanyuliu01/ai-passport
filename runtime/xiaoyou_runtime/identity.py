"""这台 Runtime 是谁：runtime_id。

第一次启动时生成，存在 state 目录的 runtime-id 文件里，之后不变；换一台电脑或虚拟机就是
新的。客户端用“runtime_id + / + 消息编号”当全局编号（见 docs/development/unified-message-model）。
和 epoch 不同：记录丢了或者换了，epoch 会变，runtime_id 不变。
"""

import re
import uuid
from pathlib import Path

FILE = "runtime-id"
PROTOCOL = 1
# 这台 Runtime 支持的同步能力；客户端按它决定用哪些接口。
CAPABILITIES = ("messages", "sync", "approvals", "feed", "client_id_dedupe")


def runtime_id(state_dir: Path) -> str:
    path = Path(state_dir) / FILE
    try:
        value = path.read_text(encoding="utf-8").strip()
        if re.fullmatch(r"[0-9a-f]{32}", value):
            return value
    except OSError:
        pass
    value = uuid.uuid4().hex
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(value + "\n", encoding="utf-8")
    temporary.replace(path)
    return value
