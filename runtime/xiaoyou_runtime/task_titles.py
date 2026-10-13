"""One canonical task-title policy, independent of agent or client type."""

import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Dict

from .desktop_content import redact_credentials, visible_text

MAX_TITLE_CHARS = 200
MAX_REQUEST_TITLE_CHARS = 24
MAX_INPUT_CHARS = 16 * 1024
TITLE_SOURCES = frozenset({"upstream", "task", "request", "fallback"})
_ANSI = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\))")


@dataclass(frozen=True)
class TaskTitle:
    title: str
    source: str
    truncated: bool = False

    def fields(self) -> Dict[str, Any]:
        return {"title": self.title, "title_source": self.source,
                "title_truncated": self.truncated}


def canonical_title(value: object, source: str = "task") -> TaskTitle:
    """Keep source names, scrub credentials and bound their display size.

    Explicit names retain their spelling, internal spaces and emoji. A request
    fallback uses the existing short request convention, after injected
    context removal. A result or assistant response never becomes a title.
    """
    source = source if isinstance(source, str) and source in TITLE_SOURCES else "task"
    raw = value if isinstance(value, str) else ""
    # Adapters already enforce this bound. Keep the shared API safe as well;
    # rejecting an oversized input avoids publishing a partial credential.
    if len(raw) > MAX_INPUT_CHARS:
        return TaskTitle("", source, True)
    if source == "request":
        text = " ".join(visible_text(raw).split())
        return TaskTitle(text[:MAX_REQUEST_TITLE_CHARS], source,
                         len(text) > MAX_REQUEST_TITLE_CHARS)
    text = _ANSI.sub("", raw)
    text = "".join(" " if char in "\r\n\t" else char for char in text
                   if char in "\r\n\t" or unicodedata.category(char) not in {"Cc", "Cs"})
    text = redact_credentials(text).strip()
    truncated = len(text) > MAX_TITLE_CHARS
    if truncated:
        text = text[:MAX_TITLE_CHARS - 1].rstrip() + "…"
    return TaskTitle(text, source, truncated)


def select_title(*, upstream: object = "", task: object = "", request: object = "",
                 fallback: object = "（没有标题）") -> TaskTitle:
    """Upstream sidebar name > local task name > request > stable fallback."""
    for source, value in (("upstream", upstream), ("task", task),
                          ("request", request), ("fallback", fallback)):
        result = canonical_title(value, source)
        if result.title:
            return result
    return TaskTitle("（没有标题）", "fallback")
