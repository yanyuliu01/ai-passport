"""Bounded, non-streamed commitment selection. Late answers never execute tools."""

import json
import queue
import socket
import threading
import time
import urllib.request

from ..helpers.base import Outcome
from ..helpers.deadline import Deadline, DeadlineExceeded
from ..helpers import dsh_stream

DEADLINE_SECONDS = 1.3
COMMITMENTS = {"start_task", "amend_task", "cancel_task", "continue_session", "remember", "replace", "forget", "track", "resolve"}
RULES = (
    "这一阶段只决定这轮要落实什么，不给主人说话。只选一个工具；不用改变任务、记忆或约定时选 just_reply。"
    "查资料、查看任务详情留到下一阶段。不能仅凭说了要做就当作做了。"
)


def choose(spec, job, env, model, opener=urllib.request.urlopen):
    began = time.monotonic()
    budget = Deadline(min(DEADLINE_SECONDS, job.timeout or spec.timeout_seconds or 60))
    tools = job.tools
    definitions = [item for item in tools.definitions() if item["name"] in COMMITMENTS]
    definitions.append({"name": "just_reply", "description": "这轮不作承诺，只回答或询问。",
                        "input_schema": {"type": "object", "properties": {}}})
    allowed = {item["name"] for item in definitions}
    history = dsh_stream.plain_history(dsh_stream._load(dsh_stream.sessions_dir(spec), job.session_id))
    body = {"model": model, "stream": False, "max_tokens": 512,
            "thinking": {"type": "disabled"}, "tools": definitions,
            "system": (job.system or "") + "\n\n" + RULES,
            "messages": history + [{"role": "user", "content": job.text}]}
    # Prompt plus server-side validation works even when a provider ignores tool_choice.
    # Do not assume support for tool_choice=any without a live capability probe.
    base = (env.get("DEEPSEEK_BASE_URL") or dsh_stream.BASE_URL).rstrip("/")
    url = base if base.endswith("/v1") else base + "/v1"
    request = urllib.request.Request(url + "/messages", method="POST",
                                     data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
                                     headers={"Content-Type": "application/json", "Accept": "application/json",
                                              "x-api-key": env.get("DEEPSEEK_API_KEY", ""),
                                              "anthropic-version": "2023-06-01"})
    answers = queue.Queue(maxsize=1)
    response_holder = []
    cancelled = threading.Event()

    def fetch():
        try:
            with opener(request, timeout=budget.remaining()) as response:
                response_holder.append(response)
                if cancelled.is_set():
                    return
                raw = response.read(262145)
                if len(raw) > 262144:
                    raise ValueError("oversized decision")
                answers.put(json.loads(raw))
        except Exception:
            if not cancelled.is_set():
                answers.put(None)

    status, name, content, facts = "reply", "just_reply", [], "这轮没有启动任务、改记忆或新增约定。"
    applying = False
    try:
        budget.remaining()
        if job.interrupt is not None and job.interrupt.is_set():
            raise ValueError("interrupted decision")
        threading.Thread(target=fetch, daemon=True, name="xiaoyu-decision").start()
        answer = answers.get(timeout=budget.remaining())
        budget.remaining()
        calls = [item for item in (answer or {}).get("content", [])
                 if isinstance(item, dict) and item.get("type") == "tool_use"]
        if len(calls) > 1:
            raise ValueError("more than one commitment")
        if calls:
            call = calls[0]
            name = call.get("name")
            args = call.get("input")
            if name not in allowed or not isinstance(args, dict) or not isinstance(call.get("id"), str):
                raise ValueError("invalid commitment")
            if job.interrupt is not None and job.interrupt.is_set():
                raise ValueError("interrupted decision")
            failed = False
            if name != "just_reply":
                budget.remaining()
                applying = True
                facts, failed = tools.call(name, args)
                status = "refused" if failed else "selected"
            content = [{"role": "assistant", "content": [call]},
                       {"role": "user", "content": [{"type": "tool_result", "tool_use_id": call["id"],
                                                       "content": facts, "is_error": failed}]}]
        elif answer is None:
            status = "unavailable"
    except (queue.Empty, DeadlineExceeded):
        # A tool may already have changed durable state before its phase guard
        # reports expiry. Let the caller reconcile that effect; never describe
        # an applied/uncertain mutation as an empty timed-out selection.
        if applying:
            raise
        status, facts = "timeout", "决定阶段超时。这轮没有执行任何承诺，不能声称已经安排或记下。"
    except (ValueError, TypeError, AttributeError):
        status, facts = "refused", "决定无效或已被打断，没有执行任何承诺。"
    finally:
        cancelled.set()
        if status == "timeout" and response_holder:
            response = response_holder[0]
            sock = getattr(getattr(getattr(response, "fp", None), "raw", None), "_sock", None)
            if isinstance(sock, socket.socket):
                try:
                    sock.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
    fields = tools.fields("")
    fields["_decision"] = {"status": status, "name": name, "facts": facts,
                           "seconds": time.monotonic() - began, "messages": content}
    return Outcome("", None, fields)
