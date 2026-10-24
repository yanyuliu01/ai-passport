"""Claim a selected effect in the existing request journal before invoking it.

The supplied checkpoint owns serialization and durable storage. Replay never
invokes this runner: pending effects are explicitly resumed by the conversation
loop; running effects without a saved result remain uncertain.
"""

from copy import deepcopy
import json

from .agents import AgentError

MAX_EFFECTS = 32
MUTATIONS = {"remember", "replace", "forget", "track", "resolve", "task_action"}


class EffectUncertain(AgentError):
    pass


class EffectFenceError(EffectUncertain):
    pass


class Effects:
    def __init__(self, request_id, saved, checkpoint, guard=lambda: None):
        self.request_id, self._checkpoint, self.guard = request_id, checkpoint, guard
        self.records = deepcopy(saved or [])
        self._next = 0

    @property
    def selected(self):
        return bool(self.records)

    @property
    def uncertain(self):
        return any(item["state"] in ("running", "unknown") for item in self.records)

    def run(self, name, args, invoke, key=None):
        self.guard()
        if name not in MUTATIONS:
            raise AgentError("Unsupported effect")
        if key is None:
            self._next += 1
            key = "%s:effect:%d" % (self.request_id, self._next)
        record = next((item for item in self.records if item["id"] == key), None)
        if self.uncertain and (record is None or record["state"] != "succeeded"):
            raise EffectUncertain("这一轮已有结果不明的操作，不能继续改变状态。")
        if record is not None:
            if record["name"] != name or record["args"] != args:
                raise EffectUncertain("操作身份对应的决定已经改变，需先核对原操作。")
            if record["state"] == "succeeded":
                return deepcopy(record["result"])
            if record["state"] != "pending":
                raise EffectUncertain("上次操作的结果尚未确认，不能重新执行。")
        else:
            if len(self.records) >= MAX_EFFECTS:
                raise AgentError("这一轮的操作过多，请先核对已保存的结果。")
            record = {"version": 1, "id": key, "name": name, "args": deepcopy(args), "state": "pending"}
            self._save(record)
        self.guard()
        self._save(dict(record, state="running"))
        try:
            self.guard()
            result = invoke()
            self.guard()
            self._save(dict(record, state="succeeded", result=deepcopy(result)))
        except Exception as error:
            try:
                self._save(dict(record, state="unknown"))
            except (OSError, EffectUncertain):
                pass  # The durable running claim already prevents replay.
            if isinstance(error, EffectFenceError):
                raise
            raise EffectUncertain("操作可能已生效，但结果没有完整保存；需先核对记录。") from error
        return result

    def _save(self, record):
        records = [deepcopy(item) for item in self.records if item["id"] != record["id"]]
        records.append(deepcopy(record))
        records = json.loads(json.dumps(records, ensure_ascii=False))
        self._checkpoint(records)
        self.records = records

    def facts(self):
        return facts(self.records)


def valid(records):
    return (isinstance(records, list) and len(records) <= MAX_EFFECTS
            and all(isinstance(item, dict) and type(item.get("version")) is int and item["version"] == 1
                    and isinstance(item.get("id"), str)
                    and item.get("name") in MUTATIONS and isinstance(item.get("args"), dict)
                    and item.get("state") in ("pending", "running", "succeeded", "unknown")
                    and (item["state"] != "succeeded" or (
                        isinstance(item.get("result"), dict) if item["name"] == "task_action" else
                        isinstance(item.get("result"), (list, tuple)) and len(item["result"]) == 2
                        and isinstance(item["result"][0], str) and isinstance(item["result"][1], bool))) for item in records)
            and len({item["id"] for item in records}) == len(records))


def check_transition(previous, records, request_id):
    if not valid(records):
        raise EffectUncertain("操作记录无法读取。")
    before, after = {item["id"]: item for item in previous}, {item["id"]: item for item in records}
    if not before.keys() <= after.keys():
        raise EffectUncertain("不能删除已保存的操作。")
    changed = [item for key, item in after.items() if before.get(key) != item]
    if len(changed) != 1:
        raise EffectUncertain("这个操作已经被领取或完成。")
    item = changed[0]
    prior = before.get(item["id"])
    if not item["id"].startswith(request_id + ":"):
        raise EffectUncertain("操作不属于这个请求。")
    if prior is None:
        allowed = item["state"] == "pending"
    else:
        allowed = (prior["name"] == item["name"] and prior["args"] == item["args"]
                   and item["state"] in {"pending": {"running"},
                       "running": {"succeeded", "unknown"}}.get(prior["state"], set()))
    if not allowed:
        raise EffectUncertain("操作状态不允许重复执行。")


def facts(records):
    lines = []
    for item in records:
        if item["state"] == "succeeded":
            result = item["result"]
            if item["name"] == "task_action":
                lines.append(result.get("_facts") or result.get("reply") or "任务已保存并交办，后续结果会回到原对话。")
            elif isinstance(result, (list, tuple)) and result and isinstance(result[0], str):
                lines.append(result[0])
        elif item["state"] in ("running", "unknown"):
            lines.append("上次操作的结果尚未确认，请先核对记录，不能重复执行。")
        else:
            lines.append("操作已保存，尚未执行。")
    return "\n".join(lines)
