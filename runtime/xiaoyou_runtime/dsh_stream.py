"""小幽接话那一次的流式通道：直接调 DeepSeek 的 Messages 接口，边生成边交出文字。

dsh 自己的两条路（headless --json、ACP）都只在一步结束时交出整段回复，不逐字给，
所以只说话、不用工具的那一次绕过 dsh，用 dsh 同一个接口、同一把密钥、同一个模型
直接问，stream 打开。会话（之前聊过的几轮）由这里自己记在文件里：编号以 api- 开头，
跟 dsh 的会话编号分得开。

  POST <DEEPSEEK_BASE_URL 或 https://api.deepseek.com/anthropic>/v1/messages
  x-api-key: <DEEPSEEK_API_KEY>    stream: true    thinking: disabled
  ← text/event-stream：content_block_delta（text_delta）一块一块来，message_stop 结束

出了问题抛 AgentError，调用方退回 dsh 的老路。
"""

import json
import os
import re
import tempfile
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional

from .agents import MAX_OUTPUT_CHARS, AgentError, Job, Outcome, clip, fields_from_text
from .config import AgentSpec

BASE_URL = "https://api.deepseek.com/anthropic"
MAX_TOKENS = 8192
# 自己记的会话最多留这么多条消息、这么多字（从旧的丢）。dsh 会压缩，这里只截断。
MAX_HISTORY_MESSAGES = 40
MAX_HISTORY_CHARS = 60000
SESSION_PREFIX = "api-"
_SESSION_ID = re.compile(r"^api-[0-9a-f]{32}$")

Opener = Callable[..., Any]


def schema_note(schema: Optional[Dict[str, Any]]) -> str:
    """要求的回复结构，写进系统提示（和 headless 那条路追加在话后面的是同一段）。"""
    if not schema:
        return ""
    return ("请只输出一个 JSON 对象，不要代码围栏或额外说明。按以下 JSON Schema "
            "填写字段；reply 是对主人说的话。\n" + json.dumps(schema, ensure_ascii=False))


def sessions_dir(spec: AgentSpec) -> Path:
    base = spec.config_dir or spec.workdir or Path(tempfile.gettempdir())
    return Path(base) / "xiaoyou-stream-sessions"


def _load(folder: Path, session_id: Optional[str]) -> List[Dict[str, str]]:
    """这个会话之前的几轮；编号不是这里发的或者文件没了，就当新开。"""
    if not session_id or not _SESSION_ID.match(session_id):
        return []
    try:
        data = json.loads((folder / (session_id + ".json")).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(data, list):
        return []
    return [item for item in data if isinstance(item, dict)
            and item.get("role") in ("user", "assistant") and isinstance(item.get("content"), str)]


def trim(history: List[Dict[str, str]]) -> List[Dict[str, str]]:
    """只留最近的：条数和字数都有上限，并且从主人说的那一条开始。"""
    kept = history[-MAX_HISTORY_MESSAGES:]
    while kept and sum(len(item["content"]) for item in kept) > MAX_HISTORY_CHARS:
        kept = kept[2:]
    while kept and kept[0]["role"] != "user":
        kept = kept[1:]
    return kept


def _save(folder: Path, session_id: str, history: List[Dict[str, str]]) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / (session_id + ".json")
    handle, name = tempfile.mkstemp(prefix=".session-", dir=str(folder))
    with os.fdopen(handle, "w", encoding="utf-8") as stream:
        json.dump(history, stream, ensure_ascii=False)
    if os.name == "posix":
        os.chmod(name, 0o600)
    os.replace(name, target)


def events(lines: Iterable[bytes]) -> Iterable[Dict[str, Any]]:
    """把 text/event-stream 一行一行读成事件（只要 data: 那一行的 JSON）。"""
    for raw in lines:
        line = raw.decode("utf-8", "replace").strip()
        if not line.startswith("data:"):
            continue
        body = line[5:].strip()
        if not body or body == "[DONE]":
            continue
        try:
            event = json.loads(body)
        except ValueError:
            continue
        if isinstance(event, dict):
            yield event


def run(spec: AgentSpec, job: Job, env: Dict[str, str], model: Optional[str],
        on_text: Callable[[str], None], opener: Opener = urllib.request.urlopen) -> Outcome:
    """问一次，文字每来一块就把到目前为止的全文交给 on_text。"""
    key = env.get("DEEPSEEK_API_KEY", "").strip()
    if not key:
        raise AgentError("没有 DEEPSEEK_API_KEY，走不了流式")
    if not model:
        raise AgentError("没有配置模型，走不了流式")
    folder = sessions_dir(spec)
    history = _load(folder, job.session_id)
    session_id = job.session_id if history else SESSION_PREFIX + uuid.uuid4().hex
    system = "\n\n".join(part for part in (job.system or "", schema_note(job.schema)) if part)
    body: Dict[str, Any] = {
        "model": model, "stream": True, "max_tokens": MAX_TOKENS,
        "thinking": {"type": "disabled"},
        "messages": history + [{"role": "user", "content": job.text}],
    }
    if system:
        body["system"] = system
    base = (env.get("DEEPSEEK_BASE_URL") or BASE_URL).rstrip("/")
    url = base if base.endswith("/v1") else base + "/v1"
    request = urllib.request.Request(
        url + "/messages", data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        method="POST")
    request.add_header("Content-Type", "application/json")
    request.add_header("Accept", "text/event-stream")
    request.add_header("x-api-key", key)
    request.add_header("anthropic-version", "2023-06-01")
    said = ""
    stopped = False
    timeout = job.timeout or spec.timeout_seconds or 60
    began = time.monotonic()
    try:
        with opener(request, timeout=timeout) as response:
            for event in events(response):
                kind = event.get("type")
                if kind == "error":
                    error = event.get("error")
                    raise AgentError("DeepSeek 流式接口出错：%s" % clip(str(
                        error.get("message") if isinstance(error, dict) else error), 200))
                if kind == "content_block_delta":
                    delta = event.get("delta")
                    if isinstance(delta, dict) and delta.get("type") == "text_delta" \
                            and isinstance(delta.get("text"), str) and delta["text"]:
                        said += delta["text"]
                        on_text(said)
                elif kind == "message_stop":
                    stopped = True
                    break
                if time.monotonic() - began > timeout:
                    raise AgentError("DeepSeek 超过 %d 秒还没说完" % timeout)
    except urllib.error.HTTPError as error:
        detail = ""
        try:
            detail = error.read().decode("utf-8", "replace")
        except OSError:
            pass
        raise AgentError("DeepSeek 流式接口返回 %d：%s" % (error.code, clip(detail, 200)))
    except (urllib.error.URLError, OSError, ValueError) as error:
        raise AgentError("连不上 DeepSeek 流式接口：%s" % error)
    said = said.strip()
    if not stopped or not said:
        raise AgentError("DeepSeek 流式接口没有说完就断了")
    try:
        _save(folder, session_id, trim(body["messages"] + [{"role": "assistant", "content": said}]))
    except OSError:
        session_id = None  # 记不下来：下一次当新会话开，这一次的回复照样用
    return Outcome(said[:MAX_OUTPUT_CHARS], session_id,
                   fields_from_text(said) if job.system is not None else None)
