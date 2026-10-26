"""Bounded, visible conversation text for the desktop observation adapter.

These helpers are deliberately independent of transcript readers and storage.
Callers must select human user messages and public assistant channels first;
content extraction never grants a message visibility based on its text alone.
Credential scrubbing covers recognizable forms, not arbitrary private prose.
"""

import re
import unicodedata

from ..context_markers import is_recap

MAX_MESSAGE_CHARS = 4000
TEXT_BLOCK_TYPES = frozenset({"text", "input_text", "output_text"})

_CONTEXT_NAMES = (
    r"environment_context|in-app-browser-context|external_codex_apps_open_page|"
    r"skills_instructions|app-context|user_instructions|system-reminder|"
    r"permissions(?:[_:-][\w:-]+)?|codex_apps_client_time_context|"
    r"codex_apps_open_page_instructions|send_user_message_question_reply|xiaoyou-history-context|"
    r"task-notification|agent-notification|xiaoyou-recap"
)
_CONTEXT_TOKEN = re.compile(
    r"<\s*(?P<close>/?)\s*(?P<name>" + _CONTEXT_NAMES + r")\b[^>]*(?:>|$)",
    re.IGNORECASE,
)
_AGENTS_HEADER = re.compile(r"(?im)^\s*#\s+AGENTS(?:\.zh_CN)?\.md instructions for[^\n]*")
_AGENTS_TOKEN = re.compile(r"<\s*(?P<close>/?)\s*(?P<name>INSTRUCTIONS)\b[^>]*(?:>|$)",
                           re.IGNORECASE)
_REQUEST_MARKER = re.compile(r"(?im)^\s*##\s+My request:\s*\n?")
_ANSI = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\))")
_PRIVATE_KEY = re.compile(
    r"-----BEGIN (?P<kind>(?:[A-Z0-9]+ )*PRIVATE KEY)-----.*?"
    r"(?:-----END (?P=kind)-----|$)", re.DOTALL,
)
_BEARER = re.compile(r"\bBearer[ \t]+[^\s\"'<>]+", re.IGNORECASE)
_SECRET_NAME = (
    r"(?:api[_-]?key|access[_-]?token|refresh[_-]?token|id[_-]?token|"
    r"password|passwd|pwd|client[_-]?secret|private[_-]?key|secret|token|"
    r"[a-z][\w-]*_(?:api_key|password|passwd|token|secret|access_key_id|secret_access_key))"
)
_SECRET_ASSIGNMENT = re.compile(
    r"(?P<prefix>(?<![\w])[\"']?" + _SECRET_NAME + r"[\"']?[ \t]*[:=][ \t]*)"
    r"(?P<value>\"(?:\\.|[^\"\\])*(?:\"|$)|'(?:\\.|[^'\\])*(?:'|$)|"
    r"[^\s,;}\])<>\[\{]+)", re.IGNORECASE,
)
_URL_SECRET = re.compile(
    r"(?P<prefix>[?&](?:token|access_token|refresh_token|api_key|apikey|password|"
    r"client_secret|jwt|signature|sig|x-amz-signature|x-amz-credential)=)"
    r"[^\s&#\"'<>\]]+", re.IGNORECASE,
)
_URL_PASSWORD = re.compile(r"(?P<prefix>https?://)[^/@\s:]+:[^/@\s]+@", re.IGNORECASE)
_API_KEY = re.compile(
    r"(?<![\w-])(?:sk-[a-zA-Z0-9_-]{16,}|gh[pousr]_[a-zA-Z0-9]{16,}|"
    r"github_pat_[a-zA-Z0-9_]{16,}|AKIA[0-9A-Z]{16})(?![\w-])"
)


def message_text(content: object) -> str:
    """Flatten only visible text blocks; do not inspect tools or reasoning.

    Plain strings are supported for Claude's human-message representation.
    Unknown wrappers and non-string text values fail closed.
    """
    if isinstance(content, str):
        return content.strip()
    blocks = content if isinstance(content, list) else [content]
    parts = []
    for block in blocks:
        if not isinstance(block, dict):
            continue
        kind = block.get("type")
        if not isinstance(kind, str) or kind not in TEXT_BLOCK_TYPES:
            continue
        value = block.get("text")
        if isinstance(value, str) and value.strip():
            parts.append(value.strip())
    return "\n".join(parts)


def _strip_blocks(text: str, tokens: re.Pattern) -> str:
    """Discard nested context blocks, including an unfinished trailing block."""
    parts, stack, offset = [], [], 0
    for token in tokens.finditer(text):
        name = token.group("name").lower()
        closing = bool(token.group("close"))
        if not stack:
            parts.append(text[offset:token.start()])
            if not closing and not token.group().rstrip().endswith("/>"):
                stack.append(name)
            offset = token.end()
        elif closing:
            # A mismatched close cannot make hidden context visible.
            if stack[-1] == name:
                stack.pop()
            offset = token.end()
        elif not token.group().rstrip().endswith("/>"):
            stack.append(name)
            offset = token.end()
    if not stack:
        parts.append(text[offset:])
    return "".join(parts)


def _credential_value(match: re.Match) -> str:
    value = match.group("value")
    quote = value[0] if value.startswith(("\"", "'")) else ""
    return match.group("prefix") + quote + "[REDACTED]" + quote


def redact_credentials(text: str) -> str:
    """Scrub recognizable credentials without rewriting ordinary text."""
    text = _PRIVATE_KEY.sub("[REDACTED PRIVATE KEY]", text)
    text = _BEARER.sub("Bearer [REDACTED]", text)
    text = _URL_SECRET.sub(lambda match: match.group("prefix") + "[REDACTED]", text)
    text = _URL_PASSWORD.sub(lambda match: match.group("prefix") + "[REDACTED]@", text)
    text = _SECRET_ASSIGNMENT.sub(_credential_value, text)
    return _API_KEY.sub("[REDACTED]", text)


def visible_text(text: str) -> str:
    """Strip injected contexts, scrub recognizable secrets and bound a message.

    An empty result means the adapter should skip the message. A generated
    ``## My request:`` suffix is preserved even after an incomplete context
    block, while ordinary Chinese, Markdown and code retain their line breaks.
    """
    if not isinstance(text, str):
        return ""
    if is_recap(text) and not text.lstrip().startswith("<xiaoyou-recap>"):
        return ""  # Legacy generated recap had no closing delimiter.
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _ANSI.sub("", text).replace("\t", "    ")
    text = "".join(char for char in text if char == "\n" or
                   unicodedata.category(char) not in {"Cc", "Cf", "Cs"})
    marker = _REQUEST_MARKER.search(text)
    if marker and (_CONTEXT_TOKEN.search(text[:marker.start()]) or
                   _AGENTS_HEADER.search(text[:marker.start()])) and not any(
                       token.group("close") for tokens in (_CONTEXT_TOKEN, _AGENTS_TOKEN)
                       for token in tokens.finditer(text[marker.end():])):
        # A marker inside a complete context is itself hidden context. Only
        # salvage the explicit suffix when it has no trailing context close.
        text = text[marker.end():]
    agents_header = _AGENTS_HEADER.search(text)
    if agents_header:
        if not _AGENTS_TOKEN.search(text[agents_header.end():]):
            # Plain AGENTS instruction payloads have no visible user request.
            text = text[:agents_header.start()]
        else:
            text = _AGENTS_HEADER.sub("", text)
            text = _strip_blocks(text, _AGENTS_TOKEN)
    text = _strip_blocks(text, _CONTEXT_TOKEN)
    text = _REQUEST_MARKER.sub("", text)
    text = redact_credentials(text).strip()
    if len(text) > MAX_MESSAGE_CHARS:
        return text[:MAX_MESSAGE_CHARS - 1].rstrip() + "…"
    return text
