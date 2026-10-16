"""统一消息结构的一致性测试：对着任何一台 Runtime 的地址跑。

新的 Runtime（另一台电脑、虚拟机，或者另一种实现）接进来之前先跑通它：

    python3 -m xiaoyou_runtime.conformance --url http://127.0.0.1:8765 --token <令牌>

它会在一个单独的对话（默认 conformance-<随机>）里发一句很短的话（再带同一个 client_id 重发
两次，测去重和 409），所以会真的调用一次模型；不会动别的对话。

它测不了重启：重启前后编号、序号、修订号的规则由 Runtime 自己的单元测试覆盖。每一项打印 PASS / FAIL，有 FAIL 时退出码是 1。

检查的是 docs/development/unified-message-model 里的契约：身份、收下即有编号、按 client_id
去重和 409、快照加增量不漏、序号只增不减、同一条消息修订号不倒退、定稿不退回生成中、
epoch 不对和游标超前时要求重来。
"""

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
import uuid
from typing import Any, Dict, List, Optional, Tuple

Result = Tuple[str, bool, str]


class Client:
    def __init__(self, base: str, token: str):
        self.base = base.rstrip("/")
        self.token = token

    def call(self, method: str, path: str, body: Optional[Dict[str, Any]] = None,
             timeout: float = 70) -> Tuple[int, Any]:
        data = json.dumps(body).encode("utf-8") if body is not None else None
        request = urllib.request.Request(self.base + path, data=data, method=method)
        request.add_header("Authorization", "Bearer " + self.token)
        if data is not None:
            request.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.status, json.loads(response.read() or b"null")
        except urllib.error.HTTPError as error:
            try:
                return error.code, json.loads(error.read() or b"null")
            except ValueError:
                return error.code, None


def run(base: str, token: str, conversation: Optional[str] = None,
        reply_timeout: float = 120.0) -> List[Result]:
    client = Client(base, token)
    conversation = conversation or "conformance-" + uuid.uuid4().hex[:8]
    results: List[Result] = []

    def check(name: str, ok: bool, detail: str = "") -> bool:
        results.append((name, bool(ok), detail))
        return bool(ok)

    status, info = client.call("GET", "/v1/info")
    if not check("info", status == 200 and isinstance(info, dict), "status %s" % status):
        return results
    check("info.runtime_id", isinstance(info.get("runtime_id"), str) and len(info["runtime_id"]) >= 8)
    check("info.epoch", isinstance(info.get("epoch"), str) and bool(info["epoch"]))
    check("info.protocol", isinstance(info.get("protocol"), int) and info["protocol"] >= 1)
    check("info.capabilities", "sync" in (info.get("capabilities") or []))

    def sync(after: int, epoch: Optional[str] = None, wait: float = 0) -> Dict[str, Any]:
        path = "/v1/sync?conversation=%s&after=%d&wait=%s" % (conversation, after, wait)
        if epoch is not None:
            path += "&epoch=" + epoch
        code, body = client.call("GET", path)
        if code != 200 or not isinstance(body, dict):
            raise AssertionError("sync 返回 %s" % code)
        return body

    snapshot = sync(0)
    keys = ("protocol", "runtime_id", "epoch", "seq", "reset", "more", "messages", "tasks",
            "approvals", "removed")
    check("sync.shape", all(key in snapshot for key in keys),
          "缺少 %s" % [key for key in keys if key not in snapshot])
    check("sync.identity", snapshot.get("runtime_id") == info["runtime_id"]
          and snapshot.get("epoch") == info["epoch"])
    cursor = snapshot["seq"]

    client_id = "conf-" + uuid.uuid4().hex
    text = "一致性测试：请只回答“好”"
    status, accepted = client.call("POST", "/v1/messages", {
        "text": text, "conversation": conversation, "client_id": client_id})
    if not check("send.accepted", status == 202 and isinstance(accepted, dict)
                 and isinstance(accepted.get("id"), str), "status %s" % status):
        return results
    request_id = accepted["id"]
    you, reply = request_id + ":you", request_id + ":reply"

    status, again = client.call("POST", "/v1/messages", {
        "text": text, "conversation": conversation, "client_id": client_id})
    check("send.dedupe", status == 202 and (again or {}).get("id") == request_id)
    status, _ = client.call("POST", "/v1/messages", {
        "text": text + "（改过）", "conversation": conversation, "client_id": client_id})
    check("send.conflict_409", status == 409, "status %s" % status)

    # 收下的那句话 3 秒内出现在同步里。
    seen: Dict[str, Dict[str, Any]] = {}
    order_ok, revs_ok, final_ok, page_ok = True, True, True, True
    deadline = time.monotonic() + 3.0
    first_seen = None

    def absorb(body: Dict[str, Any], after: int) -> None:
        nonlocal order_ok, revs_ok, final_ok, page_ok
        items = body["messages"] + body["tasks"]
        if any(not after < item["seq"] <= body["seq"] for item in items):
            page_ok = False
        if body["seq"] < after:
            order_ok = False
        for message in body["messages"]:
            previous = seen.get(message["id"])
            if previous is not None:
                if message["rev"] < previous["rev"]:
                    revs_ok = False
                if previous["status"] in ("final", "failed", "cancelled") and \
                        message["status"] in ("queued", "streaming"):
                    final_ok = False
            seen[message["id"]] = message

    while time.monotonic() < deadline:
        body = sync(cursor, info["epoch"], wait=1)
        absorb(body, cursor)
        cursor = body["seq"]
        while body.get("more"):
            body = sync(cursor, info["epoch"])
            absorb(body, cursor)
            cursor = body["seq"]
        if you in seen:
            first_seen = seen[you]
            break
    check("sync.accepted_within_3s", first_seen is not None)
    check("message.owner_text", first_seen is not None and first_seen["text"] == text
          and first_seen["author"]["kind"] == "owner" and first_seen["conversation"] == conversation)

    # 等回答定稿（失败也算结束）。
    deadline = time.monotonic() + reply_timeout
    while time.monotonic() < deadline:
        if seen.get(reply, {}).get("status") in ("final", "failed"):
            break
        body = sync(cursor, info["epoch"], wait=5)
        absorb(body, cursor)
        while body.get("more"):
            cursor = body["seq"]
            body = sync(cursor, info["epoch"])
            absorb(body, cursor)
        cursor = body["seq"]
    answer = seen.get(reply)
    check("message.reply_finished", answer is not None and answer["status"] in ("final", "failed"),
          "最后的状态：%s" % (answer or {}).get("status"))
    check("message.reply_to", answer is not None and answer.get("reply_to") == you)
    check("sync.page_bounds", page_ok)
    check("sync.seq_monotonic", order_ok)
    check("message.rev_monotonic", revs_ok)
    check("message.final_is_final", final_ok)

    # 从头拉一次：和增量拼出来的应该一样。
    full = sync(0)
    pages = [full]
    while pages[-1].get("more"):
        pages.append(sync(pages[-1]["seq"]))
    rebuilt = {message["id"]: message for page in pages for message in page["messages"]}
    check("sync.snapshot_matches_increments",
          you in seen and reply in seen
          and all(key in rebuilt and (
              rebuilt[key]["rev"] > seen[key]["rev"]  # 拉快照前它又变了一次：更新的那份
              or all(rebuilt[key][field] == seen[key][field] for field in ("text", "status", "rev")))
              for key in (you, reply)))
    tasks = {task["id"]: task for page in pages for task in page["tasks"]}
    holders = [task for task in tasks.values() if reply in task.get("members", [])]
    task = (answer or {}).get("task")
    check("task.members", (task is None and not holders)
          or (task in tasks and reply in tasks[task]["members"]),
          "消息说归到 %s，事里登记的是 %s" % (task, [item["id"] for item in holders]))

    def snapshot_after_reset(body: Dict[str, Any]) -> bool:
        found = {message["id"] for message in body["messages"]}
        page = body
        while page.get("more"):
            page = sync(page["seq"])
            found.update(message["id"] for message in page["messages"])
        return body["reset"] is True and you in found and reply in found

    check("sync.epoch_mismatch_resets", snapshot_after_reset(sync(cursor, "not-this-epoch")))
    check("sync.cursor_ahead_resets", snapshot_after_reset(sync(cursor + 10 ** 9)))
    return results


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="统一消息结构的一致性测试")
    parser.add_argument("--url", required=True)
    parser.add_argument("--token", required=True)
    parser.add_argument("--conversation")
    parser.add_argument("--reply-timeout", type=float, default=120.0)
    options = parser.parse_args(argv)
    try:
        results = run(options.url, options.token, options.conversation, options.reply_timeout)
    except Exception as error:  # 回答缺字段、格式不对等：都算不通过，不打印堆栈
        print("FAIL  连不上或者回答不符合契约：%s: %s" % (type(error).__name__, error))
        return 1
    for name, ok, detail in results:
        print("%s  %s%s" % ("PASS" if ok else "FAIL", name, ("  " + detail) if detail and not ok else ""))
    return 0 if all(ok for _, ok, _ in results) else 1


if __name__ == "__main__":
    sys.exit(main())
