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
import socket
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

from .agents import MAX_OUTPUT_CHARS, AgentError, Job, Outcome, clip, fields_from_text
from .config import AgentSpec
from .voice_tools import JOIN, PROMISE_NUDGE, TOOL_SESSION_PREFIX

BASE_URL = "https://api.deepseek.com/anthropic"
MAX_TOKENS = 8192
# 自己记的会话最多留这么多条消息、这么多字（从旧的丢）。dsh 会压缩，这里只截断。
MAX_HISTORY_MESSAGES = 40
MAX_HISTORY_CHARS = 60000
SESSION_PREFIX = "api-"
# api- 是写 JSON 说话的会话，apt- 是带工具说话的会话（voice_tools.TOOL_SESSION_PREFIX），两种不混用。
_SESSION_ID = re.compile(r"^ap[it]-[0-9a-f]{32}$")

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
    # 带工具的会话里，一条的内容可以是几块（文字、工具调用、工具结果）。
    return [item for item in data if isinstance(item, dict)
            and item.get("role") in ("user", "assistant")
            and isinstance(item.get("content"), (str, list))]


def _size(item: Dict[str, Any]) -> int:
    content = item["content"]
    return len(content) if isinstance(content, str) else len(json.dumps(content, ensure_ascii=False))


def trim(history: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """只留最近的：条数和字数都有上限，并且从主人说的一句话（不是工具结果）开始。"""
    kept = history[-MAX_HISTORY_MESSAGES:]
    while kept and sum(_size(item) for item in kept) > MAX_HISTORY_CHARS:
        kept = kept[2:]
    while kept and not (kept[0]["role"] == "user" and isinstance(kept[0]["content"], str)):
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


def events(lines: Iterable[bytes], check: Optional[Callable[[], None]] = None) -> Iterable[Dict[str, Any]]:
    """把 text/event-stream 一行一行读成事件（只要 data: 那一行的 JSON）。"""
    for raw in lines:
        if check is not None:
            check()
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


# 带工具时最多来回几轮（说话 → 调工具 → 看结果 → 接着说）。到了还要调就不再给结果，这一轮到此为止。
MAX_TOOL_ROUNDS = 4


def plain_history(history: List[Dict[str, str]]) -> List[Dict[str, str]]:
    """以前写 JSON 的那些回复只留 reply 的文字：说话改成直接写文字以后，模型看到自己
    以前满屏的 JSON 会照着学。"""
    out = []
    for item in history:
        if item["role"] == "assistant" and isinstance(item["content"], str):
            found = fields_from_text(item["content"])
            if found is not None and found["reply"].strip():
                item = {"role": "assistant", "content": found["reply"].strip()}
        out.append(item)
    return out


def run(spec: AgentSpec, job: Job, env: Dict[str, str], model: Optional[str],
        on_text: Callable[[str], None], opener: Opener = urllib.request.urlopen) -> Outcome:
    """问一次，文字每来一块就把到目前为止的全文交给 on_text。

    job.tools（voice_tools.Toolbox）不为空时：她写的文字就是说的话，要做事调工具；
    工具结果交回给她接着说，最多 MAX_TOOL_ROUNDS 轮。每一轮说的话是单独的一条消息
    （用 voice_tools.JOIN 连起来，手机按它分开）。这时不要 JSON，也不加回复结构的说明。"""
    key = env.get("DEEPSEEK_API_KEY", "").strip()
    if not key:
        raise AgentError("没有 DEEPSEEK_API_KEY，走不了流式")
    if not model:
        raise AgentError("没有配置模型，走不了流式")
    tools = job.tools
    folder = sessions_dir(spec)
    prefix = TOOL_SESSION_PREFIX if tools is not None else SESSION_PREFIX
    # 两种说话方式的会话不混用：换了方式就新开一个（接上前情由调用方用 recap 带进来）。
    wanted = job.session_id if job.session_id and job.session_id.startswith(prefix) else None
    history = _load(folder, wanted)
    session_id = wanted if history else prefix + uuid.uuid4().hex
    if tools is not None or job.schema is None:
        history = plain_history(history)
    system = "\n\n".join(part for part in (job.system or "", "" if tools is not None else schema_note(job.schema)) if part)
    messages: List[Dict[str, Any]] = history + [{"role": "user", "content": job.text}]
    if job.decision_messages:
        messages.extend(job.decision_messages)
    base = (env.get("DEEPSEEK_BASE_URL") or BASE_URL).rstrip("/")
    url = base if base.endswith("/v1") else base + "/v1"
    timeout = job.timeout or spec.timeout_seconds or 60
    began = time.monotonic()
    first_text: List[Optional[float]] = [None]
    cut = [False]  # 主人插话，这一次被打断了

    def ask(prefix: str, show: bool = True) -> Tuple[str, List[Dict[str, Any]], bool]:
        """问一轮。返回（这一轮写的文字，这一轮的工具调用，是不是停在要调工具）。
        prefix 是前几轮已经说过的话：交给 on_text 的是到目前为止的全文。"""
        body: Dict[str, Any] = {
            "model": model, "stream": True, "max_tokens": MAX_TOKENS,
            "thinking": {"type": "disabled"}, "messages": messages,
        }
        if system:
            body["system"] = system
        if tools is not None:
            definitions = tools.definitions()
            if definitions:
                body["tools"] = definitions
        request = urllib.request.Request(
            url + "/messages", data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            method="POST")
        request.add_header("Content-Type", "application/json")
        request.add_header("Accept", "text/event-stream")
        request.add_header("x-api-key", key)
        request.add_header("anthropic-version", "2023-06-01")
        said = ""
        calls: List[Dict[str, Any]] = []
        current: Optional[Dict[str, Any]] = None
        wants_tools = False
        stopped = False
        expired = threading.Event()
        timer: Optional[threading.Timer] = None

        def check_deadline() -> None:
            if expired.is_set() or time.monotonic() - began >= timeout:
                raise AgentError("DeepSeek 超过 %d 秒还没说完" % timeout)

        try:
            with opener(request, timeout=max(1, timeout - (time.monotonic() - began))) as response:
                check_deadline()

                def stop_reading() -> None:
                    expired.set()
                    # Shutdown wakes a blocked SSL read without waiting on BufferedReader's lock.
                    raw = getattr(getattr(response, "fp", None), "raw", None)
                    sock = getattr(raw, "_sock", None)
                    try:
                        if isinstance(sock, socket.socket):
                            sock.shutdown(socket.SHUT_RDWR)
                        else:
                            response.close()
                    except OSError:
                        pass

                timer = threading.Timer(max(0.001, timeout - (time.monotonic() - began)), stop_reading)
                timer.daemon = True
                timer.start()
                for event in events(response, check_deadline):
                    if job.interrupt is not None and job.interrupt.is_set():
                        cut[0] = True  # 主人插话了：不再往下读，已经说出的部分照样算
                        break
                    kind = event.get("type")
                    if kind == "error":
                        error = event.get("error")
                        raise AgentError("DeepSeek 流式接口出错：%s" % clip(str(
                            error.get("message") if isinstance(error, dict) else error), 200))
                    if kind == "content_block_start":
                        block = event.get("content_block")
                        if isinstance(block, dict) and block.get("type") == "tool_use":
                            current = {"id": block.get("id"), "name": block.get("name"), "json": ""}
                            calls.append(current)
                        else:
                            current = None
                    elif kind == "content_block_delta":
                        delta = event.get("delta")
                        if not isinstance(delta, dict):
                            continue
                        if delta.get("type") == "text_delta" and isinstance(delta.get("text"), str) \
                                and delta["text"]:
                            said += delta["text"]
                            if first_text[0] is None:
                                first_text[0] = time.monotonic() - began
                            if show:  # 被提醒后的那一轮不边说边给主人看（多半会被丢掉）
                                on_text((prefix + JOIN + said) if prefix else said)
                        elif delta.get("type") == "input_json_delta" and current is not None \
                                and isinstance(delta.get("partial_json"), str):
                            current["json"] += delta["partial_json"]
                    elif kind == "message_delta":
                        delta = event.get("delta")
                        if isinstance(delta, dict) and delta.get("stop_reason") == "tool_use":
                            wants_tools = True
                    elif kind == "message_stop":
                        stopped = True
                        break
                    if time.monotonic() - began > timeout:
                        raise AgentError("DeepSeek 超过 %d 秒还没说完" % timeout)
                check_deadline()
        except urllib.error.HTTPError as error:
            detail = ""
            try:
                detail = error.read().decode("utf-8", "replace")
            except OSError:
                pass
            raise AgentError("DeepSeek 流式接口返回 %d：%s" % (error.code, clip(detail, 200)))
        except (urllib.error.URLError, OSError, ValueError) as error:
            check_deadline()
            raise AgentError("连不上 DeepSeek 流式接口：%s" % error)
        finally:
            if timer is not None:
                timer.cancel()
        if cut[0]:
            return said.strip(), [], False
        if not stopped:
            raise AgentError("DeepSeek 流式接口没有说完就断了")
        return said.strip(), calls, wants_tools and bool(calls)

    spoken: List[str] = []
    nudged = False
    nudged_at = 0  # 提醒她时已经说了几段：提醒之后她调了工具的话，之后说的不再给主人（她前面已经说过了）
    rounds = MAX_TOOL_ROUNDS if tools is not None else 1
    for round_index in range(rounds):
        said, calls, wants_tools = ask(JOIN.join(spoken), show=not nudged)
        if said:
            spoken.append(said)
        if cut[0]:
            if tools is not None:
                messages.append({"role": "assistant", "content": (said or "……") + "（被主人打断了）"})
            break
        if tools is not None and not wants_tools and not nudged and round_index < rounds - 1 \
                and tools.promised(JOIN.join(spoken)):
            # 说了要去做却一个工具都没调：悄悄提醒一次，让她现在就调（或者改口）。
            nudged, nudged_at = True, len(spoken)
            messages.append({"role": "assistant", "content": said or "……"})
            messages.append({"role": "user", "content": PROMISE_NUDGE})
            continue
        if tools is None or not wants_tools:
            if tools is not None:
                messages.append({"role": "assistant", "content": said or "（没有再说话）"})
            break
        content: List[Dict[str, Any]] = ([{"type": "text", "text": said}] if said else [])
        results: List[Dict[str, Any]] = []
        acted = tools.action
        for call in calls:
            try:
                arguments = json.loads(call["json"]) if call["json"].strip() else {}
            except ValueError:
                arguments = None
            content.append({"type": "tool_use", "id": call["id"], "name": call["name"],
                            "input": arguments if isinstance(arguments, dict) else {}})
            if arguments is None:
                text, failed = "参数不是合法的 JSON，没有执行", True
            else:
                text, failed = tools.call(str(call["name"]), arguments)
            result: Dict[str, Any] = {"type": "tool_result", "tool_use_id": call["id"], "content": text}
            if failed:
                result["is_error"] = True
            results.append(result)
        messages.append({"role": "assistant", "content": content})
        messages.append({"role": "user", "content": results})
        if nudged:
            # 被提醒以后补调了工具：事情办了就够了。她回应提醒说的话、补完再说的一遍都不给主人
            # （主人已经看到她前面那段），也不用再问一轮。
            spoken = spoken[:nudged_at]
            messages.append({"role": "assistant", "content": "（补上了）"})
            break
        if acted is None and tools.action is not None and spoken:
            # 交出去了，交之前也已经跟主人说过：到此为止。再问一轮她多半把刚才的话换个说法再说
            # 一遍（“好，我让 X 去…”后面又跟一条“好嘞，已经让 X 去…”）。还没开口的才再问一轮。
            break
        if round_index == MAX_TOOL_ROUNDS - 1:
            break
    said = JOIN.join(spoken).strip()
    if not said and cut[0]:
        said = "……"
    if not said and (tools is None or tools.action is None):
        raise AgentError("DeepSeek 流式接口没有说完就断了")
    sys.stderr.write("DeepSeek stream model=%s first_text=%.3fs total=%.3fs tools=%s\n" % (
        model, first_text[0] or 0.0, time.monotonic() - began,
        ",".join(name for name, _ in tools.calls) if tools is not None else "-"))
    try:
        if tools is not None:
            # 带工具的会话把工具调用和结果也记下：只记说过的话，她会学成“说了就算做了”。
            if messages[-1]["role"] != "assistant":
                messages.append({"role": "assistant", "content": "（这一轮到此为止）"})
            _save(folder, session_id, trim(messages))
        else:
            _save(folder, session_id, trim(history + [{"role": "user", "content": job.text},
                                                      {"role": "assistant", "content": said or "……"}]))
    except OSError:
        session_id = None  # 记不下来：下一次当新会话开，这一次的回复照样用
    if tools is not None:
        return Outcome(said[:MAX_OUTPUT_CHARS], session_id, tools.fields(said[:MAX_OUTPUT_CHARS]))
    return Outcome(said[:MAX_OUTPUT_CHARS], session_id,
                   fields_from_text(said) if job.system is not None else None)
