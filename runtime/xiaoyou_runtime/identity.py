"""这台 Runtime 是谁：runtime_id；它跑的是哪个小幽：subject_id。

第一次启动时生成，存在 state 目录的 runtime-id 文件里，之后不变；换一台电脑或虚拟机就是
新的。客户端用“runtime_id + / + 消息编号”当全局编号（见 docs/development/unified-message-model）。
和 epoch 不同：记录丢了或者换了，epoch 会变，runtime_id 不变。

subject_id 是小幽这个主体的编号，和跑在哪台电脑上无关（见统一消息结构的“统一主体”）：现在
每台 Runtime 有一个，存在 state 目录的 subject-id 文件里；把 state 目录搬到另一台电脑，主体跟着走。
"""

import re
import uuid
from pathlib import Path

FILE = "runtime-id"
SUBJECT_FILE = "subject-id"
PROTOCOL = 1
# 这台 Runtime 支持的同步能力；客户端按它决定用哪些接口。
# sync_usage：/v1/sync 也带用量，并且接受 usage=<rev>（用量变了提前返回）
# reports：POST /v1/reports 接受不在这台电脑上的会话上报（reports.py）
CAPABILITIES = ("messages", "sync", "approvals", "feed", "client_id_dedupe", "sync_usage", "reports", "task_activity", "owner_images", "side_chats", "all_conversations", "task_audit", "conversation_directory_v1", "shared_history_pages", "presentation_v1")


def runtime_id(state_dir: Path) -> str:
    return _stable_id(Path(state_dir) / FILE)


def subject_id(state_dir: Path) -> str:
    return _stable_id(Path(state_dir) / SUBJECT_FILE)


def _stable_id(path: Path) -> str:
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
