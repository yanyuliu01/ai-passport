"""Recognize generated context without importing readers or model execution."""


def is_recap(text: str) -> bool:
    """Recognize our generated context, including the legacy untagged header."""
    head = text.lstrip()
    return head.startswith(("<xiaoyu-recap>", "<xiaoyou-recap>")) or (
        head.startswith("（主人最近（") and "当时用的不是这套会话" in head[:200])
