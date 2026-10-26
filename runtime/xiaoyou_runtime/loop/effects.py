"""Claim a selected effect in its source's existing durable record before invoking it.

The supplied checkpoint owns serialization and durable storage. Replay never
invokes this runner: pending effects are explicitly resumed by the conversation
loop; running effects without a saved result remain uncertain.
"""

from copy import deepcopy
import hashlib
import json
import time

from ..errors import AgentError, EffectUncertain

MAX_EFFECTS = 32
MUTATIONS = {"remember", "replace", "forget", "track", "resolve", "task_action"}


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


class SavedWake:
    """Use Effects with a timer source's existing compare-and-save record.

    The outer unknown claim is understood by the previous timer implementation.
    Only saved pending effects resume; a missing model reply is never regenerated
    once a mutation has been selected.
    """

    def __init__(self, event_id, owner, source, saved, checkpoint, guard):
        self.guard, self._checkpoint = guard, checkpoint
        self.saved = deepcopy(saved)
        self.state = deepcopy(saved) if saved is not None else {
            "version": 1, "id": event_id, "state": "unknown", "at": time.time(),
            "owner": list(owner), "source": deepcopy(source), "effects": []}
        value = self.state
        if (not isinstance(value, dict) or type(value.get("version")) is not int or value.get("version") != 1
                or "review" in value
                or value.get("id") != event_id or value.get("state") != "unknown"
                or value.get("owner") != list(owner) or value.get("source") != source
                or not valid(value.get("effects"))
                or any(item["name"] == "task_action" or not item["id"].startswith(event_id + ":")
                       for item in value["effects"])
                or ("card" in value and (not isinstance(value["card"], str) or not value["card"]))
                or ("reply" in value and (not isinstance(value["reply"], str)
                    or any(item["state"] != "succeeded" for item in value["effects"])))):
            raise EffectUncertain("定时操作记录或原负责人已改变，需先核对。")
        self.effects = Effects(event_id, value["effects"], self._effects, guard)

    def save(self, **fields):
        self.guard()
        value = dict(self.state, **deepcopy(fields))
        self._checkpoint(self.saved, value)
        self.saved = deepcopy(value)
        self.state = value

    def _effects(self, records):
        if "reply" in self.state:
            raise EffectUncertain("这次唤醒已有结果，不能再执行工具。")
        check_transition(self.state["effects"], records, self.state["id"])
        self.save(effects=records)

    def resume(self, toolbox):
        if any(item["state"] in ("running", "unknown") for item in self.effects.records):
            raise EffectUncertain("上次定时操作的结果尚未确认，不能重新执行。")
        for item in list(self.effects.records):
            if (item["state"] == "pending" and item["name"] in ("replace", "forget")
                    and not isinstance(item["args"].get("_memory_revision"), str)):
                raise EffectUncertain("原记忆版本无法核对，不能恢复按编号的修改。")
            self.effects.run(item["name"], item["args"],
                lambda item=item: toolbox.call(item["name"], item["args"]), key=item["id"])
        return self.effects.facts()

    def finish(self, reply):
        if any(item["state"] != "succeeded" for item in self.effects.records):
            raise EffectUncertain("定时操作尚未确认，不能发布完成结果。")
        reply.encode("utf-8")
        self.save(reply=reply)


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


def owner_binding(message):
    owner = message.get("_effect_owner")
    return {key: value for key, value in owner.items() if key != "review"} if isinstance(owner, dict) else None


def review_record(message):
    owner = message.get("_effect_owner")
    return owner.get("review") if isinstance(owner, dict) else None


def review_revision(message):
    """Bind an owner review to the exact original selection and owner."""
    values = [message["id"], message["operation_id"], message["conversation"],
              message.get("card"), message.get("pin"), message.get("reply_scope"),
              owner_binding(message), message.get("_effects")]
    return hashlib.sha256(json.dumps(values, sort_keys=True, ensure_ascii=True,
                                    separators=(",", ":")).encode()).hexdigest()


def review_outcomes(records, outcomes):
    """Every unconfirmed effect needs an explicit disposition, never a retry."""
    needed = {item["id"]: item for item in records if item["state"] != "succeeded"}
    if not isinstance(outcomes, dict) or not needed or set(outcomes) != set(needed):
        raise ValueError("请逐项核对原请求中所有尚未确认的操作")
    for key, value in outcomes.items():
        if value not in ("applied", "not_applied"):
            raise ValueError("核对结果只能是 applied 或 not_applied")
        if needed[key]["state"] == "pending" and value != "not_applied":
            raise ValueError("尚未领取的操作只能确认未执行")
    return dict(outcomes)


def valid_review(message):
    review, records = review_record(message), message.get("_effects")
    if not valid(records) or not isinstance(review, dict) or set(review) != {
            "version", "id", "revision", "actor", "at", "outcomes", "published"}:
        return False
    if (type(review["version"]) is not int or review["version"] != 1
            or not isinstance(review["id"], str) or not 1 <= len(review["id"]) <= 64
            or not isinstance(review["actor"], str) or not review["actor"]
            or type(review["at"]) not in (int, float) or not 0 < review["at"] < 1e12
            or type(review["published"]) is not bool or review["revision"] != review_revision(message)):
        return False
    try:
        review_outcomes(records, review["outcomes"])
    except (ValueError, TypeError):
        return False
    return True


def review_text(message):
    outcomes = review_record(message)["outcomes"]
    lines = ["主人已人工核对这轮操作："]
    for item in message["_effects"]:
        if item["id"] in outcomes:
            lines.append("%s：主人确认%s。" % (item["name"],
                         "已生效" if outcomes[item["id"]] == "applied" else "未生效"))
    lines.append("已记录核对结果；没有重新执行或回滚原操作。以上是主人的确认，并非程序验证。")
    return "\n".join(lines)
