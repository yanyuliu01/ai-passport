"""小幽网络：同一个主人的几台 Runtime 互相同步记录，并能把事交给对方（见 docs/development/unified-message-model 的“多台 Runtime”）。

谁在网络里：配置里写了同一个口令（mesh.secret）的 Runtime。小幽这个主体的编号（subject_id）
从口令算出来，每台都一样。拉起一台新的只要告诉它口令和网络里任意一台的地址（mesh.peers）：
它从那台知道其余的，其余的也从它那里知道它。外面连不进来的（只监听本机、在别人的网络里）
也能加入：它主动去连别人，把自己的记录推过去、把别人的拉回来。

记录怎么同步：每条记录只有生成它的那台（产地）能改，所以不会有两边同时改的冲突。每台给
自己的记录发序号（就是本机的序号），别人按“产地 + 产地序号”记游标，按游标增量地拉；拉到的
记录再转给别人（没法直接连到产地的也能拿到）。一份产地记录在这里落成：
  事   → 一张本地卡（编号是这里的 cN，mesh_origin 记着产地）；
  消息 → 消息日志里的一条（编号前面加产地编号的前 12 位，不会撞）；
  授权 → 授权表里的一项（编号同上）。
手机、设备、给模型的上下文都照常读这些，不用知道它们来自哪台。

怎么控制别的 Runtime 的事：在这里对副本说话、取消、回答授权，变成一条发给产地的“指令”，
同样随记录同步过去（产地离线时，等它回来再交）；产地执行后报回结果。小幽也能把事交给别的
Runtime 上的帮手（名字是“帮手@那台”），那台替这件事开的卡记着 delegated_from，这台就不再另外
显示它，进展和结果回到这台交出去的那张卡上。
"""

import hashlib
import hmac
import json
import re
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from . import __version__
from .agents import Agent, AgentError, Job, Outcome
from .config import AgentSpec
from .message_log import DESKTOP_CONVERSATION, KINDS, MAX_ACTIVITIES
from .mesh_share import Shares
from . import ownership

PROTOCOL = 1
SHORT = 12
# 一次交换最多带这么多字节的记录，其余的下一次接着给。
EXPORT_BUDGET = 256 * 1024
MAX_BODY_BYTES = 8 * 1024 * 1024
# 去别人那里等变化最多等多久（长轮询）。
POLL_SECONDS = 20
# 这么久没联系上的 Runtime 算离线：它的帮手不再给小幽选，在它那里还在做的事显示看不到。
OFFLINE_SECONDS = 300
# 指令这么久没被执行就作废：主人不会想让一句很久以前的话突然被做。
COMMAND_SECONDS = 600
RETRY_SECONDS = (2, 5, 10, 30, 60)
LIMITS = {"task": 300, "message": 5000, "approval": 300, "command": 1000, "result": 2000, "share": 2000,
          "events": 3000, "lease": 20, "owner": 2000, "device": 500, "member": 100}
# 自动迭代的租约（iterate/lease.py）：持有者多久不续就算放手。
LEASE_MAX_SECONDS = 6 * 3600
ID = re.compile(r"^[0-9a-f]{32}$")
REVERSE_KINDS = {value: key for key, value in KINDS.items()}


def scope_of(url: str) -> str:
    """地址能从哪里连到：loopback 本机、lan 局域网、private 私网（Tailscale）、public 公开入口。"""
    import ipaddress
    from urllib.parse import urlparse
    host = (urlparse(url).hostname or "").lower()
    if host in ("localhost",) or host.endswith(".localhost"):
        return "loopback"
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        if host.endswith(".local"):
            return "lan"
        if host.endswith(".ts.net") and urlparse(url).scheme == "http":
            return "private"  # Tailscale 网内的名字（Funnel 公开入口是 https）
        return "public"
    if address.is_loopback:
        return "loopback"
    if address.version == 4 and address in ipaddress.ip_network("100.64.0.0/10"):
        return "private"
    if address.is_private or address.is_link_local:
        return "lan"
    return "public"


class MeshError(Exception):
    pass


def subject_for(secret: str) -> str:
    """同一个口令算出同一个主体编号：网络里每台的小幽是同一个。"""
    return hashlib.sha256(("xiaoyou-subject\0" + secret).encode("utf-8")).hexdigest()[:32]


def local_id(origin: str, item_id: str) -> str:
    """别的 Runtime 的一条消息、一个授权在这里的编号。"""
    return "%s.%s" % (origin[:SHORT], item_id)


def _base(message_id: str) -> str:
    for suffix in (":you", ":reply"):
        if message_id.endswith(suffix):
            return message_id[:-len(suffix)]
    return message_id


class Replicas:
    """别的 Runtime 的记录（含这台转给别人的）：每个产地一份，落盘在 state/mesh/<产地>.jsonl。

    一行一条记录的最新样子 {"k", "id", "oseq", "data"}，第一行可以是 {"epoch"}。读的时候同一条
    以最后一行为准。这里只存，不决定怎么显示（那是 Mesh 的事）。"""

    def __init__(self, folder: Path):
        self._folder = Path(folder)
        self._lock = threading.Lock()
        # 产地 → {"epoch", "upto", "records": {(k, id): 记录}, "lines"}
        self._origins: Dict[str, Dict[str, Any]] = {}
        self._folder.mkdir(parents=True, exist_ok=True)
        for path in self._folder.glob("*.jsonl"):
            if ID.match(path.stem):
                self._load(path.stem, path)

    def _load(self, origin: str, path: Path) -> None:
        entry = {"epoch": "", "upto": 0, "records": {}, "lines": 0}
        try:
            with path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    entry["lines"] += 1
                    try:
                        item = json.loads(line)
                    except ValueError:
                        continue
                    if not isinstance(item, dict):
                        continue
                    if isinstance(item.get("epoch"), str):
                        entry.update(epoch=item["epoch"], records={}, upto=0)
                        continue
                    if isinstance(item.get("upto"), int):
                        entry["upto"] = max(entry["upto"], item["upto"])
                        continue
                    if isinstance(item.get("k"), str) and isinstance(item.get("id"), str) \
                            and isinstance(item.get("oseq"), int):
                        entry["records"][(item["k"], item["id"])] = item
        except OSError as error:
            sys.stderr.write("读不了网络记录 %s：%s\n" % (path, error))
        self._trim(entry)
        self._origins[origin] = entry

    def _path(self, origin: str) -> Path:
        return self._folder / (origin + ".jsonl")

    def origins(self) -> List[str]:
        with self._lock:
            return list(self._origins)

    def position(self, origin: str) -> Tuple[str, int]:
        with self._lock:
            entry = self._origins.get(origin)
            return (entry["epoch"], entry["upto"]) if entry else ("", 0)

    def get(self, origin: str, kind: str, item_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            entry = self._origins.get(origin)
            record = entry["records"].get((kind, item_id)) if entry else None
            return dict(record["data"]) if record else None

    def find(self, origin: str, kind: str, test: Callable[[Dict[str, Any]], bool]) -> List[Dict[str, Any]]:
        with self._lock:
            entry = self._origins.get(origin)
            found = [record for (k, _), record in (entry["records"].items() if entry else [])
                     if k == kind and test(record["data"])]
            found.sort(key=lambda record: record["oseq"])
            return [dict(record["data"]) for record in found]

    def since(self, origin: str, after: int) -> List[Dict[str, Any]]:
        with self._lock:
            entry = self._origins.get(origin)
            if entry is None:
                return []
            return sorted((dict(record) for record in entry["records"].values() if record["oseq"] > after),
                          key=lambda record: record["oseq"])

    def store(self, origin: str, epoch: str, records: List[Dict[str, Any]], upto: int) -> List[Dict[str, Any]]:
        """记下一批产地记录；epoch 和存着的不一样时先全部丢掉（那台换了一份记录）。返回真正变了的。"""
        changed: List[Dict[str, Any]] = []
        lines: List[str] = []
        with self._lock:
            entry = self._origins.get(origin)
            if entry is None or entry["epoch"] != epoch:
                entry = {"epoch": epoch, "upto": 0, "records": {}, "lines": 0}
                self._origins[origin] = entry
                lines.append(json.dumps({"epoch": epoch}))
                rewrite = True
            else:
                rewrite = False
            for record in records:
                key = (record["k"], record["id"])
                current = entry["records"].get(key)
                if current is not None and current["oseq"] >= record["oseq"]:
                    continue
                kept = {"k": record["k"], "id": record["id"], "oseq": record["oseq"], "data": record["data"]}
                entry["records"][key] = kept
                lines.append(json.dumps(kept, ensure_ascii=False))
                changed.append(kept)
            if upto > entry["upto"]:
                entry["upto"] = upto
                lines.append(json.dumps({"upto": upto}))
            self._trim(entry)
            entry["lines"] += len(lines)
            compact = entry["lines"] > 3 * max(len(entry["records"]), 100)
            try:
                if rewrite or compact:
                    self._rewrite(origin, entry)
                elif lines:
                    with self._path(origin).open("a", encoding="utf-8") as handle:
                        handle.write("\n".join(lines) + "\n")
            except OSError as error:
                sys.stderr.write("网络记录写不下去：%s\n" % error)
        return changed

    def _trim(self, entry: Dict[str, Any]) -> None:
        for kind, limit in LIMITS.items():
            keys = [key for key in entry["records"] if key[0] == kind]
            groups = [(keys, limit)]
            if kind == "message":
                activity = {key for key in keys if isinstance(entry["records"][key].get("data"), dict)
                            and entry["records"][key]["data"].get("kind") == "activity"}
                groups = [([key for key in keys if key not in activity], limit),
                          (list(activity), MAX_ACTIVITIES)]
            for members, maximum in groups:
                if len(members) > maximum:
                    members.sort(key=lambda key: entry["records"][key]["oseq"])
                    for key in members[:len(members) - maximum]:
                        del entry["records"][key]

    def _rewrite(self, origin: str, entry: Dict[str, Any]) -> None:
        path = self._path(origin)
        temporary = path.with_suffix(".tmp")
        with temporary.open("w", encoding="utf-8") as handle:
            handle.write(json.dumps({"epoch": entry["epoch"]}) + "\n")
            handle.write(json.dumps({"upto": entry["upto"]}) + "\n")
            for record in sorted(entry["records"].values(), key=lambda record: record["oseq"]):
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        temporary.replace(path)
        entry["lines"] = len(entry["records"]) + 2


class Peer:
    def __init__(self, runtime_id: str):
        self.runtime_id = runtime_id
        self.name = ""
        self.urls: List[str] = []
        self.agents: List[Dict[str, Any]] = []
        self.version = ""
        self.seen = 0.0  # 最近一次和它直接说上话
        self.heard = 0.0  # 最近一次知道它在线（直接或者别人转告）
        self.error = ""
        self.cursors: Optional[Dict[str, Dict[str, Any]]] = None  # 它手里各产地的游标
        self.good = ""  # 上次连得上的地址

    def view(self) -> Dict[str, Any]:
        return {"runtime_id": self.runtime_id, "name": self.name, "urls": list(self.urls),
                "version": self.version, "seen_at": max(self.seen, self.heard),
                "agents": [dict(agent) for agent in self.agents]}


class Mesh:
    def __init__(self, config: Any, service: Any, xiaoyou: Any,
                 opener: Callable[..., Any] = urllib.request.urlopen, clock: Callable[[], float] = time.time):
        if not config.mesh_secret:
            raise MeshError("没有 mesh.secret，这台不加入小幽网络")
        self._config, self._service, self._xiaoyou = config, service, xiaoyou
        self._open, self._clock = opener, clock
        self._secret = config.mesh_secret
        self.subject_id = subject_for(self._secret)
        self.runtime_id = service.runtime_id
        self.name = config.name
        self._writer = xiaoyou.intake.writer("mesh", actor="mesh")
        self._cards = xiaoyou.cards
        folder = Path(config.state_dir) / "mesh"
        self._replicas = Replicas(folder)
        self._state_path = folder / "state.json"
        self._lock = threading.RLock()
        self._apply_lock = threading.RLock()
        self._changed = threading.Condition(self._lock)
        self._stop = threading.Event()
        self._peers: Dict[str, Peer] = {}
        self._threads: Dict[str, List[threading.Thread]] = {}
        self._aliases: Dict[str, str] = {}  # 产地|epoch|产地卡号 → 本地卡号
        # 这台自己的指令和指令结果（产地是这台）：{(k, id): {"k","id","oseq","data"}}
        self._outbox: Dict[Tuple[str, str], Dict[str, Any]] = {}
        self._watching: Dict[str, threading.Thread] = {}  # 正在看着结果的指令
        self._relays: Dict[str, str] = {}  # 这台转交出去的话 → 交给的那台
        self._owner_cache: Optional[Dict[str, List[Dict[str, Any]]]] = None  # 对话 → 绑定（ownership.py）
        self._manual = {url.rstrip("/") for url in config.mesh_peers}  # 配置里手写的地址
        self._load()
        for url in config.mesh_peers:
            if not any(url in peer.urls for peer in self._peers.values()):
                peer = Peer("seed:" + url)
                peer.urls = [url]
                self._peers[peer.runtime_id] = peer
        store = getattr(service, "_store", None)
        # Runtime 自己起的帮手的会话：不当成电脑会话导出（它们已经在卡上了）。
        self.owned_sessions: Callable[[], Any] = store.session_ids if store is not None else (lambda: set())
        self.shares = Shares(self, config)
        service.subject_id = self.subject_id
        service.mesh = self
        service.publish_review_startup()
        xiaoyou.mesh = self
        xiaoyou.approvals.forward = self._forward_approval

    # ---- 落盘 ----

    def _load(self) -> None:
        try:
            raw = json.loads(self._state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raw = {}
        if not isinstance(raw, dict):
            raw = {}
        aliases = raw.get("aliases")
        if isinstance(aliases, dict):
            self._aliases = {key: value for key, value in aliases.items()
                             if isinstance(key, str) and isinstance(value, str)}
        for item in raw.get("peers", []) if isinstance(raw.get("peers"), list) else []:
            if isinstance(item, dict) and isinstance(item.get("runtime_id"), str) \
                    and ID.match(item["runtime_id"]) and item["runtime_id"] != self.runtime_id:
                peer = Peer(item["runtime_id"])
                peer.name = str(item.get("name") or "")[:40]
                peer.urls = [url for url in item.get("urls", []) if isinstance(url, str)][:8]
                peer.heard = float(item.get("seen_at") or 0)
                self._peers[peer.runtime_id] = peer
        for item in raw.get("outbox", []) if isinstance(raw.get("outbox"), list) else []:
            if isinstance(item, dict) and item.get("k") in ("command", "result") \
                    and isinstance(item.get("id"), str) and isinstance(item.get("oseq"), int):
                self._outbox[(item["k"], item["id"])] = item

    def _save(self) -> None:
        """调用时拿着锁。停下以后不再写（关掉时后台线程可能还在收尾）。"""
        if self._stop.is_set():
            return
        outbox = sorted(self._outbox.values(), key=lambda item: item["oseq"])[-LIMITS["result"]:]
        data = {"aliases": self._aliases, "outbox": outbox,
                "peers": [peer.view() for peer in self._peers.values() if ID.match(peer.runtime_id)]}
        temporary = self._state_path.with_suffix(".tmp")
        try:
            temporary.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            temporary.replace(self._state_path)
        except OSError as error:
            sys.stderr.write("网络状态写不下去：%s\n" % error)

    # ---- 这台是谁 ----

    def info(self) -> Dict[str, Any]:
        agents = [{"name": agent.name, "description": agent.description, "type": agent.type,
                   "speaks": agent.speaks}
                  for agent in self._xiaoyou.agents() if agent.type not in ("mesh", "remote")]
        return {"protocol": PROTOCOL, "runtime_id": self.runtime_id, "subject_id": self.subject_id,
                "name": self.name, "version": __version__, "urls": list(self._config.mesh_advertise),
                "agents": agents}

    def status(self) -> Dict[str, Any]:
        """给手机和命令行看的：网络里有谁、多久前联系上的。"""
        now = self._clock()
        with self._lock:
            peers = [dict(peer.view(), online=self._online(peer, now), error=peer.error,
                          routes=self.routes(peer.urls))
                     for peer in self._peers.values() if ID.match(peer.runtime_id)]
            seeds = [peer.urls[0] for peer in self._peers.values() if not ID.match(peer.runtime_id)]
        return {"runtime_id": self.runtime_id, "subject_id": self.subject_id, "name": self.name,
                "urls": list(self._config.mesh_advertise), "routes": self.routes(self._config.mesh_advertise),
                "peers": peers, "unresolved": seeds, "authority": self.authority(),
                "removed": sorted(self._service.devices.removed_members())}

    def routes(self, urls: List[str]) -> List[Dict[str, Any]]:
        """地址连同它能从哪里连到、是不是手写的。"""
        return [{"url": url, "scope": scope_of(url), "manual": url in self._manual} for url in urls]

    def authorized(self, header: str) -> bool:
        return hmac.compare_digest(header.encode("utf-8"), ("Bearer " + self._secret).encode("utf-8"))

    def _online(self, peer: Peer, now: float) -> bool:
        return now - max(peer.seen, peer.heard) < OFFLINE_SECONDS

    # ---- 交换（对方连进来，或者这台连出去，都走这个格式） ----

    def exchange(self, body: Any) -> Dict[str, Any]:
        """对方带来：它是谁、它手里各产地的游标、推给这台的记录、它知道的别的 Runtime；
        回给它：这台是谁、这台的游标、它还没有的记录、这台知道的别的 Runtime。"""
        if not isinstance(body, dict):
            raise MeshError("请求体应该是一个对象")
        raw = body.get("from")
        if isinstance(raw, dict) and isinstance(raw.get("runtime_id"), str) \
                and self._service.devices.removed(raw["runtime_id"]):
            raise MeshError("这台电脑已经被移出小幽网络")
        caller = self._learn(raw, direct=True)
        if caller is None:
            raise MeshError("from 不对")
        if caller.runtime_id == self.runtime_id:
            raise MeshError("连到了自己")
        self._learn_peers(body.get("peers"))
        self._take(body.get("push"), caller.runtime_id)
        cursors = body.get("cursors") if isinstance(body.get("cursors"), dict) else {}
        with self._lock:
            caller.cursors = cursors
        wait = body.get("wait", 0)
        wait = min(float(wait), POLL_SECONDS + 5) if isinstance(wait, (int, float)) and wait > 0 else 0.0
        deadline = time.monotonic() + wait
        while True:
            seq = self._cards.seq
            origins = self._export(cursors, skip=caller.runtime_id)
            remaining = deadline - time.monotonic()
            if origins or remaining <= 0 or self._stop.is_set():
                break
            self._cards.wait_beyond(seq, min(remaining, 1.0))
        return {"from": self.info(), "cursors": self.cursors(), "origins": origins,
                "peers": self._peer_list()}

    def cursors(self) -> Dict[str, Dict[str, Any]]:
        """这台手里各产地的游标（包括这台自己）。"""
        found = {self.runtime_id: {"epoch": self._cards.epoch, "seq": self._cards.seq}}
        for origin in self._replicas.origins():
            if origin == self.runtime_id:
                continue  # 这台自己共享的设置也存在那里，但游标以这台的序号为准
            epoch, upto = self._replicas.position(origin)
            found[origin] = {"epoch": epoch, "seq": upto}
        return found

    def _peer_list(self) -> List[Dict[str, Any]]:
        with self._lock:
            return [peer.view() for peer in self._peers.values() if ID.match(peer.runtime_id)]

    def _learn(self, raw: Any, direct: bool) -> Optional[Peer]:
        if not isinstance(raw, dict) or not isinstance(raw.get("runtime_id"), str) \
                or not ID.match(raw["runtime_id"]):
            return None
        if raw.get("subject_id") not in (None, self.subject_id):
            raise MeshError("不是同一个小幽（口令一样但主体不同）")
        runtime_id = raw["runtime_id"]
        if runtime_id == self.runtime_id:
            return Peer(runtime_id)
        if self._service.devices.removed(runtime_id):
            with self._lock:
                self._peers.pop(runtime_id, None)  # 移出的电脑：不再认、不再连
            return None
        now = self._clock()
        with self._lock:
            peer = self._peers.get(runtime_id)
            fresh = peer is None
            if peer is None:
                peer = Peer(runtime_id)
                self._peers[runtime_id] = peer
            if isinstance(raw.get("name"), str) and raw["name"].strip():
                peer.name = raw["name"].strip()[:40]
            urls = [url.rstrip("/") for url in raw.get("urls", []) if isinstance(url, str)
                    and url.startswith(("http://", "https://"))][:8] if isinstance(raw.get("urls"), list) else None
            if urls is not None and (direct or not peer.urls):
                # 配置里手写的地址不被自动发现的覆盖，排在最前面（设计第 6 节）。
                manual = [url for url in peer.urls if url in self._manual]
                peer.urls = manual + [url for url in urls if url not in manual]
            if isinstance(raw.get("agents"), list):
                peer.agents = [{"name": str(item.get("name"))[:24], "description": str(item.get("description") or "")[:200],
                                "type": str(item.get("type") or ""), "speaks": item.get("speaks") is True}
                               for item in raw["agents"][:16] if isinstance(item, dict) and item.get("name")]
            if isinstance(raw.get("version"), str):
                peer.version = raw["version"][:32]
            if direct:
                peer.seen, peer.error = now, ""
            elif isinstance(raw.get("seen_at"), (int, float)):
                peer.heard = max(peer.heard, min(float(raw["seen_at"]), now))
            # 种子地址认出了是谁：并到这一台上。
            for key in [key for key, item in self._peers.items() if not ID.match(key)
                        and set(item.urls) & set(peer.urls)]:
                del self._peers[key]
            self._save()
        if fresh:
            self._refresh_agents()
        self._ensure_threads()
        return peer

    def _learn_peers(self, raw: Any) -> None:
        for item in raw if isinstance(raw, list) else []:
            if isinstance(item, dict) and item.get("runtime_id") != self.runtime_id:
                try:
                    self._learn(item, direct=False)
                except MeshError:
                    continue

    # ---- 给出去：这台自己的记录，和转给别人的 ----

    def _export(self, cursors: Dict[str, Any], skip: str = "") -> Dict[str, Any]:
        origins: Dict[str, Any] = {}
        budget = [EXPORT_BUDGET]
        own = self._export_own(cursors.get(self.runtime_id), budget)
        if own is not None:
            origins[self.runtime_id] = own
        for origin in self._replicas.origins():
            if origin in (skip, self.runtime_id) or budget[0] <= 0:
                continue
            block = self._export_replica(origin, cursors.get(origin), budget)
            if block is not None:
                origins[origin] = block
        return origins

    @staticmethod
    def _cursor(raw: Any) -> Tuple[str, int]:
        if isinstance(raw, dict) and isinstance(raw.get("seq"), int) and not isinstance(raw.get("seq"), bool):
            return str(raw.get("epoch") or ""), max(0, raw["seq"])
        return "", 0

    def _pack(self, records: List[Dict[str, Any]], budget: List[int], upto: int) -> Tuple[List[Dict[str, Any]], int, bool]:
        records.sort(key=lambda record: record["oseq"])
        taken: List[Dict[str, Any]] = []
        for record in records:
            size = len(json.dumps(record, ensure_ascii=False).encode("utf-8"))
            if taken and size > budget[0]:
                return taken, taken[-1]["oseq"], True
            budget[0] -= size
            taken.append(record)
        return taken, upto, False

    def _export_own(self, raw: Any, budget: List[int]) -> Optional[Dict[str, Any]]:
        epoch, after = self._cursor(raw)
        mine = self._cards.epoch
        reset = bool(after) and (epoch != mine or after > self._cards.seq or after < self._cards.log_floor)
        if reset or not after or epoch != mine:
            after = 0
        seq, tasks, messages = self._cards.own_since(after)
        if after == seq and not reset:
            return None
        records = [{"k": "task", "id": task["id"], "oseq": task["seq"], "data": task} for task in tasks]
        records += [{"k": "message", "id": message["id"], "oseq": message["seq"], "data": message}
                    for message in messages if message["status"] != "queued" or message.get("text")]
        records += [{"k": "approval", "id": item["id"], "oseq": item["seq"], "data": item}
                    for item in self._xiaoyou.approvals.own_since(after) if item["seq"] <= seq]
        with self._lock:
            records += [dict(item) for item in self._outbox.values() if after < item["oseq"] <= seq]
        # 这台共享的设置、文件、会话记录（mesh_share.py），存在以这台为产地的那一份里。
        stored, _ = self._replicas.position(self.runtime_id)
        if stored == mine:
            records += [record for record in self._replicas.since(self.runtime_id, after)
                        if record["k"] in ("share", "events", "lease", "owner", "device", "member")
                        and record["oseq"] <= seq]
        if not records:
            # 序号涨了但都不是这台自己的记录（比如别人的副本）：没东西给，游标落后一点也无妨。
            return None
        taken, upto, more = self._pack(records, budget, seq)
        return {"epoch": mine, "upto": upto, "records": taken, "reset": reset or after == 0, "more": more,
                "origin_name": self.name}

    def _export_replica(self, origin: str, raw: Any, budget: List[int]) -> Optional[Dict[str, Any]]:
        epoch, after = self._cursor(raw)
        stored, upto = self._replicas.position(origin)
        if not stored or not upto:
            return None
        reset = epoch != stored
        if reset:
            after = 0
        if after >= upto:
            return None
        records = self._replicas.since(origin, after)
        if not records:
            return None
        taken, upto, more = self._pack(records, budget, upto)
        with self._lock:
            peer = self._peers.get(origin)
            name = peer.name if peer else ""
        return {"epoch": stored, "upto": upto, "records": taken, "reset": reset, "more": more,
                "origin_name": name}

    # ---- 拿进来：别人的记录落成这里的卡、消息、授权；发给这台的指令照做 ----

    def _take(self, origins: Any, via: str) -> None:
        if not isinstance(origins, dict) or self._stop.is_set():
            return
        for origin, block in origins.items():
            if not isinstance(origin, str) or not ID.match(origin) or origin == self.runtime_id \
                    or not isinstance(block, dict) or not isinstance(block.get("epoch"), str):
                continue
            records = [record for record in block.get("records", []) if isinstance(record, dict)
                       and record.get("k") in LIMITS and isinstance(record.get("id"), str)
                       and isinstance(record.get("oseq"), int) and isinstance(record.get("data"), dict)]
            upto = block.get("upto") if isinstance(block.get("upto"), int) else 0
            stored, _ = self._replicas.position(origin)
            if stored and stored != block["epoch"] and via != origin:
                # 别人转来的是那台的另一份记录：只信那台自己说的，免得被旧的那份换回去。
                continue
            if isinstance(block.get("origin_name"), str) and block["origin_name"]:
                with self._lock:
                    peer = self._peers.get(origin)
                    if peer is None:
                        peer = self._peers[origin] = Peer(origin)
                    if not peer.name:
                        peer.name = block["origin_name"][:40]
            changed = self._replicas.store(origin, block["epoch"], records, upto)
            if changed:
                self._apply(origin, block["epoch"], changed)
            with self._lock:
                peer = self._peers.get(origin)
                if peer is not None:
                    peer.heard = max(peer.heard, self._clock() if via == origin else peer.heard)
                self._changed.notify_all()

    def _apply(self, origin: str, epoch: str, records: List[Dict[str, Any]]) -> None:
        # Push and pull may deliver concurrently. Durable records already reject
        # old revisions; serialize their projections too, using the newest
        # stored value so a delayed batch cannot undo a terminal task state.
        with self._apply_lock:
            if self._replicas.position(origin)[0] != epoch:
                return
            self._apply_current(origin, epoch, records)

    def _apply_current(self, origin: str, epoch: str, records: List[Dict[str, Any]]) -> None:
        touched: Dict[str, None] = {}
        owners = any(record["k"] == "owner" for record in records)
        if owners:
            with self._lock:
                self._owner_cache = None  # 先认新绑定，再按它判断同一批里的回复
        for record in records:
            kind = record["k"]
            data = self._replicas.get(origin, kind, record["id"])
            if data is None:
                continue
            if kind in ("message", "task") and self._fenced(origin, kind, data, record["oseq"]):
                sys.stderr.write("「%s」已经不负责这段对话，丢掉它之后写的 %s/%s\n"
                                 % (self._peer_name(origin), kind, record["id"]))
                continue
            try:
                if kind == "owner":
                    pass  # 存下就算数：负责人按记录现推（owner_of）
                elif kind in ("device", "member"):
                    if origin == self.authority():  # 设备名单、移出的电脑只认权威写的
                        if kind == "device":
                            self._service.devices.merge(data)
                        else:
                            self._service.devices.merge_removed(record["id"], data)
                elif kind == "message":
                    task = self._message(origin, epoch, data)
                    if task:
                        touched[task] = None
                elif kind == "task":
                    touched.pop(data.get("id", ""), None)
                    self._task(origin, epoch, data)
                elif kind == "approval":
                    self._approval(origin, epoch, data)
                elif kind == "command":
                    self._command(origin, data)
                elif kind == "result":
                    self._result(origin, data)
                elif kind == "share":
                    self.shares.apply(origin, self._peer_name(origin), data)
                elif kind == "events":
                    self._event_store().ingest(data.get("events") or [], origin)
            except Exception as error:  # 一条坏记录不能挡住后面的
                sys.stderr.write("网络记录 %s/%s 没落成：%s: %s\n" % (kind, record["id"], type(error).__name__, error))
        for task_id in touched:
            stored = self._replicas.get(origin, "task", task_id)
            if stored is not None:
                self._task(origin, epoch, stored)
        if owners:
            self._service.retry_parked()  # 负责人变了：在等它的话再看一次

    def _peer_name(self, origin: str) -> str:
        with self._lock:
            peer = self._peers.get(origin)
            return (peer.name if peer and peer.name else origin[:6])

    def _hidden(self, data: Dict[str, Any]) -> bool:
        """这件事是那台替这台做的：这台交出去的那张卡已经代表它了，不另外显示。"""
        delegated = data.get("delegated_from")
        return isinstance(delegated, dict) and delegated.get("runtime") == self.runtime_id

    def _alias(self, origin: str, epoch: str, card_id: Optional[str]) -> Optional[str]:
        if not card_id:
            return None
        with self._lock:
            alias = self._aliases.get("%s|%s|%s" % (origin, epoch, card_id))
        if alias is not None:
            return alias
        stored = self._replicas.get(origin, "task", card_id)
        if stored is not None and self._hidden(stored):
            return stored["delegated_from"].get("card") or None
        return None

    def _message_id(self, origin: str, message_id: str) -> str:
        """那台的消息编号在这里叫什么：这台转交过去的那句话沿用这里的编号，其余的加上产地。"""
        current = self._cards.message(message_id)
        if current is not None and origin in (current.get("relay_to"), current.get("mesh_origin")):
            return message_id  # 转交的占位，或者已经被那台的记录替换过的占位
        return local_id(origin, message_id)

    def _message(self, origin: str, epoch: str, data: Dict[str, Any]) -> Optional[str]:
        if self._hidden(data):
            return None
        task = data.get("task")
        stored_task = self._replicas.get(origin, "task", task) if task else None
        if stored_task is not None and self._hidden(stored_task):
            return None  # 替这台做的那件事里的话：结果回到这台自己的卡上
        record = dict(data)
        record["id"] = self._message_id(origin, data["id"])
        if data.get("reply_to"):
            record["reply_to"] = self._message_id(origin, data["reply_to"])
        record["task"] = self._alias(origin, epoch, task)
        source = dict(data.get("source") or {})
        source.setdefault("runtime_id", origin)
        record["source"] = source
        record["mesh_origin"] = origin
        record.pop("relay_to", None)
        for key in ("seq", "rev"):
            record.pop(key, None)
        self._writer.by(actor="mesh:" + origin, cause=data["id"]).replica_message(record)
        self._remember_turn(origin, data)
        if (data.get("author") or {}).get("kind") == "owner":
            for item in self._replicas.since(origin, 0):
                reply = item.get("data") or {}
                if item.get("k") == "message" and reply.get("reply_to") == data["id"]:
                    self._remember_turn(origin, reply)
        return task

    def _remember_turn(self, origin: str, data: Dict[str, Any]) -> None:
        """那台小幽答完的一句：记进这台的对话记录，这台的小幽下次接话时就知道。"""
        author = data.get("author") or {}
        if (data.get("status") != "final" or author.get("kind") != "xiaoyou"
                or data.get("conversation") == DESKTOP_CONVERSATION or data.get("delegated_from")
                or not data.get("text")):
            return
        asked = self._replicas.get(origin, "message", data["reply_to"]) if data.get("reply_to") else None
        if not asked or (asked.get("author") or {}).get("kind") != "owner":
            return  # The result remains attributed in the record hub until its parent arrives.
        said = asked.get("text") or ""
        try:
            self._xiaoyou.share(data.get("conversation") or "default", [{
                "id": _base(data["id"]), "text": said, "reply": data["text"],
                "at": data.get("created_at") or 0}])
        except Exception as error:
            sys.stderr.write("对话记录没记上：%s\n" % error)

    def _entry(self, origin: str, message: Dict[str, Any]) -> Dict[str, Any]:
        author = message.get("author") or {}
        owner = author.get("kind") == "owner"
        return {"id": self._message_id(origin, message["id"]), "role": "you" if owner else "xiaoyou",
                "text": message.get("text") or "", "at": message.get("created_at") or 0,
                "scope": message.get("scope") or "main",
                "kind": REVERSE_KINDS.get(message.get("kind"), message.get("kind") or ("user" if owner else "reply")),
                "author": "you" if owner else (author.get("name") or "xiaoyou"),
                "attachments": message.get("attachments") or []}

    def _task(self, origin: str, epoch: str, data: Dict[str, Any]) -> None:
        if self._hidden(data):
            return
        key = "%s|%s|%s" % (origin, epoch, data["id"])
        with self._lock:
            alias = self._aliases.get(key)
        entries = []
        for member in data.get("members", []):
            message = self._replicas.get(origin, "message", member)
            if message is not None and message.get("text"):
                entries.append(self._entry(origin, message))
        fields = {name: value for name, value in data.items()
                  if name not in ("id", "seq", "members", "entries", "updated_at", "approval")}
        if fields.get("state") == "turn_ended":
            fields["state"] = "done"  # 这里的同步接口会按 desktop_status 再给出 turn_ended
        if fields.get("state") not in ("talking", "working", "waiting", "done", "failed", "cancelled", "unknown"):
            fields["state"] = "unknown"
        approval = data.get("approval")
        fields.update(mesh_origin=origin, mesh_epoch=epoch, mesh_id=data["id"],
                      origin_name=self._peer_name(origin),
                      approval=local_id(origin, approval) if isinstance(approval, str) and approval else None)
        card = self._writer.by(actor="mesh:" + origin, cause=data["id"]).replica_task(alias, fields, entries)
        if alias is None:
            with self._lock:
                self._aliases[key] = card["id"]
                self._save()
            # 一件事只发最新的样子，它的授权可能比它先到：卡有了，补上它们。
            for item in self._replicas.find(origin, "approval", lambda item: item.get("card") == data["id"]):
                self._approval(origin, epoch, item)

    def _approval(self, origin: str, epoch: str, data: Dict[str, Any]) -> None:
        card = self._alias(origin, epoch, data.get("card"))
        if card is None:
            return  # 那件事在这里还没有：等它到了再显示（授权本身随那件事的下一次变化重来）
        fields = dict(data, card=card, mesh_origin=origin, mesh_id=data["id"], origin_name=self._peer_name(origin))
        for key in ("seq",):
            fields.pop(key, None)
        self._xiaoyou.approvals.mirror(local_id(origin, data["id"]), fields)

    # ---- 指令：在这里对别的 Runtime 的事做的操作，交给那台去做 ----

    def _post(self, kind: str, item_id: str, data: Dict[str, Any]) -> None:
        with self._lock:
            oseq = self._writer.sequence()
            self._outbox[(kind, item_id)] = {"k": kind, "id": item_id, "oseq": oseq, "data": data}
            self._save()
            self._changed.notify_all()

    def command(self, target: str, kind: str, args: Dict[str, Any]) -> str:
        command_id = uuid.uuid4().hex
        now = self._clock()
        self._post("command", command_id, {"id": command_id, "target": target, "kind": kind, "args": args,
                                           "from": self.runtime_id, "created_at": now,
                                           "expires_at": now + COMMAND_SECONDS})
        return command_id

    def _reachable(self, target: str) -> Optional[str]:
        """指令能不能送到：不认识或者离线太久时返回原因。"""
        with self._lock:
            peer = self._peers.get(target)
            if peer is None:
                return "不认识那台电脑"
            if not self._online(peer, self._clock()):
                ago = int((self._clock() - max(peer.seen, peer.heard)) // 60) if max(peer.seen, peer.heard) else 0
                return "「%s」现在离线%s，这句话没发出去" % (peer.name or target[:6],
                                                          "（%d 分钟前还在）" % ago if ago else "")
        return None

    def relay(self, message: Dict[str, Any]) -> Optional[str]:
        """在别的 Runtime 的事里说的一句（Service 调用）：交给那台。送不出去时返回原因。"""
        target = message.get("relay_to") or ""
        problem = self._reachable(target)
        if problem:
            return problem
        with self._lock:
            self._relays[message["id"]] = target
        self.command(target, "message", {
            "conversation": message["conversation"], "text": message["text"], "card": message.get("relay_card"),
            # 按负责人转的是对话里的一句，不是在某件事里面说的
            "pin": not message.get("routed"), "agent": message.get("asked"), "effort": message.get("effort"), "reply_scope": message.get("reply_scope"),
            "model": message.get("model"), "message_id": message["id"],
            **({"routed_generation": message.get("owner_generation", 0)} if message.get("routed") else {})})
        return None

    def amend(self, card: Dict[str, Any], text: str, continue_desktop: bool = False) -> bool:
        """小幽决定对别的 Runtime 的一件事补充一句，或者让那台电脑上的会话接着做（xiaoyou._converse）。"""
        target = card.get("mesh_origin") or ""
        if self._reachable(target):
            return False
        self.command(target, "message", {"conversation": card.get("conversation") or "default", "text": text,
                                         "card": card.get("mesh_id"), "pin": True,
                                         "continue_desktop": continue_desktop,
                                         "message_id": uuid.uuid4().hex})
        return True

    def cancel(self, card_id: str, actor: str = "unknown", reason: str = "", announce: bool = True) -> bool:
        card = self._cards.get(card_id)
        if card is None or not card.get("mesh_origin"):
            return False
        if self._reachable(card["mesh_origin"]):
            return False
        self.command(card["mesh_origin"], "cancel", {"card": card.get("mesh_id"), "by": actor,
                                                     "reason": reason, "announce": announce})
        return True

    def _forward_approval(self, item: Dict[str, Any], decision: str) -> bool:
        target = item.get("mesh_origin") or ""
        if self._reachable(target):
            return False
        self.command(target, "approve", {"approval": item.get("mesh_id"), "decision": decision})
        return True

    def _command(self, origin: str, data: Dict[str, Any]) -> None:
        """一条指令到了：是给这台的就照做一次，结果报回去。"""
        if data.get("target") != self.runtime_id or not isinstance(data.get("id"), str):
            return
        command_id = data["id"]
        with self._lock:
            if ("result", command_id + ":accepted") in self._outbox:
                return
        args = data.get("args") if isinstance(data.get("args"), dict) else {}
        kind = data.get("kind")
        ok, error, extra = True, "", {}
        try:
            expires = data.get("expires_at")
            if isinstance(expires, (int, float)) and self._clock() > expires:
                raise MeshError("指令过期了（%d 分钟内没送到）" % (COMMAND_SECONDS // 60))
            if kind == "message":
                extra = self._do_message(data, args)
            elif kind == "cancel":
                card = args.get("card")
                by = args.get("by") if isinstance(args.get("by"), str) else ""
                actor = "mesh:%s" % self._peer_name(origin) + ("/%s" % by if by else "")
                if not isinstance(card, str) or self._service.cancel(
                        card, actor=actor, reason=str(args.get("reason") or ""),
                        announce=args.get("announce") is not False) is None:
                    raise MeshError("这台没有这件事")
            elif kind == "directory":
                extra = self._service.directory_edit(args.get('uid'), {k: args[k] for k in ('fields', 'revision') if k in args})
            elif kind == 'directory_metadata':
                extra = self._service.directory_metadata(args.get('uid'))
            elif kind == 'directory_history':
                extra = {'history': self._service.directory_history(args.get('uid'), args.get('cursor'))}
            elif kind in ("device_add", "device_revoke", "member_remove"):
                extra = self._service.permission_command(kind, args)
            elif kind == "handoff":
                extra = self._service.handoff(args.get("conversation"), args.get("to"))
                if extra.get("status") != "applied":
                    raise MeshError(extra.get("reason") or "没交成")
            elif kind == "approve":
                answered = self._service.approve(str(args.get("approval")), args.get("decision"))
                if answered is None:
                    raise MeshError("这台没有这个授权（那件事可能已经结束了）")
                if answered is False:
                    raise MeshError("这个授权已经回答过了")
            else:
                raise MeshError("不认识的指令 %s" % kind)
        except Exception as failure:
            ok, error = False, str(failure) or type(failure).__name__
        self._post("result", command_id + ":accepted", {"id": command_id, "phase": "accepted",
                                                         "from": self.runtime_id, "to": data.get("from"),
                                                         "ok": ok, "error": error, **extra})
        if ok and kind == "message":
            self._watch(command_id, extra["message"])

    def _do_message(self, data: Dict[str, Any], args: Dict[str, Any]) -> Dict[str, Any]:
        message_id = args.get("message_id")
        if not isinstance(message_id, str) or not ID.match(message_id):
            raise MeshError("message_id 不对")
        card = args.get("card")
        agent = args.get("agent")
        if card is not None:
            local = self._cards.get(card) if isinstance(card, str) else None
            if local is None or local.get("mesh_origin"):
                raise MeshError("这台没有这件事（可能已经清掉了）")
            if args.get("continue_desktop") and local.get("monitoring_only"):
                # 别的电脑上的小幽判断要这台的电脑会话接着做：点名这台能接它的帮手，
                # 不再交给这台的小幽重新判断一遍。
                heir = self._xiaoyou._desktop_agent(local)
                if heir is None:
                    raise MeshError("这台没有能接这个会话的帮手")
                agent = heir.name
        delegated = args.get("delegated_from")
        mesh = {"message_id": message_id}
        if isinstance(delegated, dict) and delegated.get("runtime") == data.get("from"):
            mesh["delegated_from"] = {key: str(value)[:64] for key, value in delegated.items()
                                      if key in ("runtime", "card", "name")}
        routed = args.get("routed_generation")
        if isinstance(routed, int) and not isinstance(routed, bool):
            mesh["routed_generation"] = routed  # 别的电脑按负责人转过来的：这里不再随便转回去
        hop = args.get("hop", 0)
        snapshot = self._service.submit(
            args.get("text"), args.get("conversation") or "default", client_id=message_id,
            agent=agent, hop=hop if isinstance(hop, int) and not isinstance(hop, bool) else 0,
            card=card, pin=args.get("pin") is True, effort=args.get("effort"), model=args.get("model"), reply_scope=args.get("reply_scope"),
            mesh=mesh)
        return {"message": snapshot["id"]}

    def _watch(self, command_id: str, message_id: str) -> None:
        """看着这条指令带来的那句话，处理完了把结果报回去（交代的一方在等它）。"""
        def run() -> None:
            while not self._stop.is_set():
                current = self._service.get(message_id, wait=2)
                if self._stop.is_set():
                    return
                if current is not None and current["status"] not in ("done", "failed"):
                    continue
                if current is None:
                    outcome = {"ok": False, "error": "这台的消息记录里已经没有这句话了"}
                    break
                if current["status"] in ("done", "failed"):
                    outcome = {"ok": current["status"] == "done", "reply": current.get("reply") or "",
                               "error": current.get("error") or "", "card": current.get("card"),
                               "brief": current.get("brief"), "mood": current.get("mood")}
                    break
            else:
                return
            self._post("result", command_id + ":done", dict(outcome, id=command_id, phase="done",
                                                             **{"from": self.runtime_id}))
            with self._lock:
                self._watching.pop(command_id, None)

        with self._lock:
            if command_id in self._watching:
                return
            thread = threading.Thread(target=run, daemon=True, name="mesh-watch-%s" % command_id[:6])
            self._watching[command_id] = thread
        thread.start()

    def _result(self, origin: str, data: Dict[str, Any]) -> None:
        """这台发出去的指令有了结果。"""
        command_id = data.get("id")
        with self._lock:
            mine = self._outbox.get(("command", command_id)) if isinstance(command_id, str) else None
            self._changed.notify_all()
        if mine is None:
            return
        message_id = (mine["data"].get("args") or {}).get("message_id")
        with self._lock:
            relayed = message_id in self._relays
        if not relayed or mine["data"].get("kind") != "message":
            return
        if data.get("phase") == "accepted" and not data.get("ok"):
            self._finish_relay(message_id, False, data.get("error") or "那台没接这句话")
        elif data.get("phase") == "done":
            self._finish_relay(message_id, data.get("ok") is True,
                               data.get("reply") if data.get("ok") else data.get("error") or "")

    def _finish_relay(self, message_id: str, ok: bool, text: str) -> None:
        with self._lock:
            self._relays.pop(message_id, None)
        self._service.relay_done(message_id, ok, text)

    def outcome(self, command_id: str, target: str, phase: str) -> Optional[Dict[str, Any]]:
        """一条指令在 target 那里的结果（没有时 None）。"""
        return self._replicas.get(target, "result", command_id + ":" + phase)

    def publish_share(self, key: str, data: Dict[str, Any]) -> None:
        """这台共享的一样东西变了（mesh_share.py）：取一个序号，记下，等着交给别人。"""
        with self._lock:
            oseq = self._writer.sequence()
            self._replicas.store(self.runtime_id, self._cards.epoch,
                                 [{"k": "share", "id": key, "oseq": oseq, "data": data}], oseq)
            self._changed.notify_all()

    # ---- 对话负责人（ownership.py） ----

    def _bindings(self, conversation: str) -> List[Dict[str, Any]]:
        """网络里各台写的这段对话的绑定（by 是写它的那台）。按对话缓存：每条同步来的记录都要查。"""
        with self._lock:
            cache = self._owner_cache
        if cache is None:
            cache = {}
            for origin in self._replicas.origins():
                for item in self._replicas.find(origin, "owner", lambda data: True):
                    if isinstance(item.get("conversation"), str):
                        cache.setdefault(item["conversation"], []).append(dict(item, by=origin))
            with self._lock:
                self._owner_cache = cache
        return [dict(item) for item in cache.get(conversation, [])]

    def owner_of(self, conversation: str) -> Dict[str, Any]:
        """这段对话现在归谁：{"owner", "generation", "conflict", "by", "name", "online"}。"""
        main = ownership.resolve(ownership.MAIN, self._bindings(ownership.MAIN))
        if conversation == ownership.MAIN:
            view = main
        else:
            item = self._cards.conversations.get(conversation) or {}
            view = ownership.resolve(conversation, self._bindings(conversation),
                                     creator=item.get("owner"), main_owner=main["owner"])
        owner = view["owner"]
        with self._lock:
            peer = self._peers.get(owner) if owner else None
            online = owner == self.runtime_id or (peer is not None and self._online(peer, self._clock()))
        name = self.name if owner == self.runtime_id else (peer.name if peer is not None else "")
        return dict(view, name=name or (owner or "")[:6], online=bool(owner) and online)

    def _publish_owner(self, conversation: str, owner: str, generation: int) -> Dict[str, Any]:
        """写一代绑定（只有现在的负责人调用）。返回写下的那条。"""
        with self._lock:
            oseq = self._writer.sequence()
            data = dict(ownership.binding(conversation, owner, generation, self.runtime_id), oseq=oseq,
                        at=self._clock())
            data.pop("by")  # 写它的是谁由产地决定，不由内容自称
            self._replicas.store(self.runtime_id, self._cards.epoch,
                                 [{"k": "owner", "id": "%s:%d" % (conversation, generation), "oseq": oseq,
                                   "data": data}], oseq)
            self._owner_cache = None
            self._changed.notify_all()
        return data

    def claim_home(self) -> None:
        """配置了 mesh.home：主对话还没有负责人时，写下第 1 代归这台。"""
        if getattr(self._config, "mesh_home", False) and \
                ownership.resolve(ownership.MAIN, self._bindings(ownership.MAIN))["owner"] is None:
            self._publish_owner(ownership.MAIN, self.runtime_id, 1)

    def peer_id(self, name_or_id: Any) -> Optional[str]:
        if not isinstance(name_or_id, str) or not name_or_id:
            return None
        if name_or_id in (self.runtime_id, self.name):
            return self.runtime_id
        with self._lock:
            for key, peer in self._peers.items():
                if ID.match(key) and name_or_id in (key, peer.name):
                    return key
        return None

    def hand_off(self, conversation: str, target: str, generation: int) -> Dict[str, Any]:
        """Service 已经在锁里确认这台是负责人、这段对话空闲：写下一代给 target。"""
        return self._publish_owner(conversation, target, generation + 1)

    def request_handoff(self, owner: str, conversation: str, target: str) -> Optional[str]:
        """不是负责人：请负责人移交。送不出去时返回原因。"""
        problem = self._reachable(owner)
        if problem:
            return problem
        self.command(owner, "handoff", {"conversation": conversation, "to": target})
        return None

    def online(self, runtime: str) -> bool:
        with self._lock:
            peer = self._peers.get(runtime)
            return runtime == self.runtime_id or (peer is not None and self._online(peer, self._clock()))

    def authority(self) -> Optional[str]:
        """权限权威（ownership.authority）；还没有人配置 mesh.home 时 None。"""
        return ownership.authority(self._bindings(ownership.MAIN))

    def publish_permission(self, kind: str, item_id: str, data: Dict[str, Any]) -> None:
        """权威记下的设备、移出的电脑：作为这台的记录随网络同步。"""
        with self._lock:
            oseq = self._writer.sequence()
            self._replicas.store(self.runtime_id, self._cards.epoch,
                                 [{"k": kind, "id": item_id, "oseq": oseq, "data": data}], oseq)
            if kind == "member":
                self._peers.pop(item_id, None)
            self._changed.notify_all()

    def _fenced(self, origin: str, kind: str, data: Dict[str, Any], oseq: int) -> bool:
        """origin 把这段对话交出去以后又写的回复、新事：不收（替别的电脑执行的派活不算）。"""
        conversation = data.get("conversation")
        if not isinstance(conversation, str) or conversation == DESKTOP_CONVERSATION:
            return False
        if kind == "task" and data.get("delegated_from"):
            return False
        if kind == "message":
            author = data.get("author") if isinstance(data.get("author"), dict) else {}
            if author.get("kind") == "owner":
                return False  # 主人说的话照收，只拦小幽替这段对话做的决定
            task = self._replicas.get(origin, "task", data.get("task")) if isinstance(data.get("task"), str) else None
            if task is not None and task.get("delegated_from"):
                return False
        bindings = self._bindings(conversation)
        if not any(item["by"] == origin for item in bindings):
            return False
        current = self.owner_of(conversation)["owner"]
        gave = ownership.handed_away(origin, conversation, bindings, current)
        return gave is not None and oseq > gave

    # ---- 使用事件和自动迭代的租约（见 docs/development/auto-iteration 的"无中心"） ----

    def _event_store(self) -> Any:
        if getattr(self, "_events", None) is None:
            self._events = self._service.events
        return self._events

    def publish_events(self, events: List[Dict[str, Any]]) -> None:
        """这台刚收下的一批使用事件：一批一条记录、一个序号，随网络同步给每台。"""
        if not events:
            return
        with self._lock:
            oseq = self._writer.sequence()
            self._replicas.store(self.runtime_id, self._cards.epoch,
                                 [{"k": "events", "id": uuid.uuid4().hex, "oseq": oseq,
                                   "data": {"events": events}}], oseq)
            self._changed.notify_all()

    def leases(self) -> List[Dict[str, Any]]:
        """网络里每台最新的迭代租约（含这台）。"""
        found = []
        for origin in self._replicas.origins():
            data = self._replicas.get(origin, "lease", "iterate")
            if data is not None:
                found.append(dict(data, holder=origin))
        return found

    def lease(self, action: str, stage: str, seconds: float) -> Dict[str, Any]:
        """申请、续约或放手。申请时别的电脑持有没过期的租约就不给：先申请的赢，同时申请按编号比。"""
        now = self._clock()
        seconds = max(60.0, min(float(seconds), LEASE_MAX_SECONDS))
        with self._lock:
            mine = self._replicas.get(self.runtime_id, "lease", "iterate") or {}
            holding = mine.get("until", 0) > now
            if action == "release":
                if not holding:
                    return {"granted": True, "holder": self.runtime_id}
                data = dict(mine, until=0, stage=stage)
            else:
                applied = mine["applied_at"] if holding else now
                for other in self.leases():
                    if other["holder"] == self.runtime_id or other.get("until", 0) <= now:
                        continue
                    if (other.get("applied_at", now), other["holder"]) < (applied, self.runtime_id):
                        return {"granted": False, "holder": other["holder"], "holder_name": other.get("name", ""),
                                "stage": other.get("stage", ""), "until": other["until"]}
                data = {"name": self.name, "stage": stage, "applied_at": applied, "until": now + seconds}
            oseq = self._writer.sequence()
            self._replicas.store(self.runtime_id, self._cards.epoch,
                                 [{"k": "lease", "id": "iterate", "oseq": oseq, "data": data}], oseq)
            self._changed.notify_all()
        return {"granted": True, "holder": self.runtime_id, "until": data["until"]}

    def wait(self, seconds: float) -> None:
        with self._changed:
            self._changed.wait(seconds)

    # ---- 网络里别的 Runtime 的帮手 ----

    def _refresh_agents(self) -> None:
        now = self._clock()
        agents: List[Agent] = []
        with self._lock:
            peers = [peer for peer in self._peers.values() if ID.match(peer.runtime_id)]
            names = {}
            for peer in peers:
                if not self._online(peer, now):
                    continue
                for item in peer.agents:
                    label = peer.name or peer.runtime_id[:6]
                    name = "%s@%s" % (item["name"], label)
                    if name in names:
                        name = "%s@%s" % (item["name"], peer.runtime_id[:6])
                    names[name] = None
                    agents.append(MeshAgent(self, peer.runtime_id, label, item, name))
        self._xiaoyou.set_remote_agents(agents)

    # ---- 连出去 ----

    def start(self) -> None:
        self.claim_home()
        for relay in self._service.relayed():
            with self._lock:
                self._relays[relay["id"]] = relay["relay_to"]
        # 重启前接下、还没报完结果的指令：接着看。
        with self._lock:
            pending = [(item["data"]["id"], item["data"].get("message"))
                       for (kind, item_id), item in self._outbox.items()
                       if kind == "result" and item_id.endswith(":accepted") and item["data"].get("ok")
                       and item["data"].get("message")
                       and ("result", item["data"]["id"] + ":done") not in self._outbox]
        for command_id, message_id in pending:
            self._watch(command_id, message_id)
        self._ensure_threads()
        try:
            self.shares.scan()
        except Exception as error:
            sys.stderr.write("共享设置第一次扫描出错：%s: %s\n" % (type(error).__name__, error))
        threading.Thread(target=self._sweep_loop, daemon=True, name="mesh-sweep").start()

    def close(self) -> None:
        self._stop.set()
        with self._changed:
            self._changed.notify_all()
            threads = [thread for group in self._threads.values() for thread in group]
            threads += list(self._watching.values())
        # 只等一小会儿：停下以后不再写盘，还在等对方回答的线程自己会结束。
        deadline = time.monotonic() + 0.5
        for thread in threads:
            if thread is not threading.current_thread():
                thread.join(max(0.0, deadline - time.monotonic()))

    def _ensure_threads(self) -> None:
        if self._stop.is_set():
            return
        with self._lock:
            wanted = [key for key, peer in self._peers.items() if peer.urls and key != self.runtime_id]
            for key in wanted:
                if key in self._threads and all(thread.is_alive() for thread in self._threads[key]):
                    continue
                threads = [threading.Thread(target=self._pull_loop, args=(key,), daemon=True,
                                            name="mesh-pull-%s" % key[:12]),
                           threading.Thread(target=self._push_loop, args=(key,), daemon=True,
                                            name="mesh-push-%s" % key[:12])]
                self._threads[key] = threads
                for thread in threads:
                    thread.start()

    def _call(self, peer: Peer, body: Dict[str, Any], timeout: float) -> Dict[str, Any]:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        urls = ([peer.good] if peer.good in peer.urls else []) + [url for url in peer.urls if url != peer.good]
        problem = "没有地址"
        for url in urls:
            request = urllib.request.Request(url + "/v1/mesh/exchange", data=data, method="POST")
            request.add_header("Authorization", "Bearer " + self._secret)
            request.add_header("Content-Type", "application/json; charset=utf-8")
            try:
                with self._open(request, timeout=timeout) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                if not isinstance(payload, dict):
                    raise ValueError("回答看不懂")
                peer.good = url
                return payload
            except urllib.error.HTTPError as error:
                problem = "%s 返回 %d" % (url, error.code)
            except (urllib.error.URLError, OSError, ValueError) as error:
                problem = "%s 连不上：%s" % (url, error)
        raise MeshError(problem)

    def _round(self, key: str, wait: float) -> bool:
        """和一台交换一次。返回有没有拿到或推出东西。"""
        with self._lock:
            peer = self._peers.get(key)
            if peer is None or self._stop.is_set():
                return False
            known = peer.cursors
        push = self._export(known, skip=key) if known is not None else {}
        # 还不知道对方手里有什么（第一次）或者有东西要推：不等，马上回；之后才长轮询。
        wait = 0 if push or known is None else wait
        body = {"from": self.info(), "cursors": self.cursors(), "push": push, "peers": self._peer_list(),
                "wait": wait}
        reply = self._call(peer, body, wait + 30)
        caller = self._learn(reply.get("from"), direct=True)
        if caller is None or caller.runtime_id == self.runtime_id:
            raise MeshError("对方不是网络里的 Runtime（或者连到了自己）")
        with self._lock:
            # 种子地址认出是谁了：后面用真的编号；手写的地址并过去，排在前面。
            if caller.runtime_id != key:
                seed = self._peers.pop(key, None)
                if seed is not None:
                    manual = [url for url in seed.urls if url in self._manual]
                    caller.urls = manual + [url for url in caller.urls if url not in manual]
            caller.cursors = reply.get("cursors") if isinstance(reply.get("cursors"), dict) else None
        self._learn_peers(reply.get("peers"))
        self._take(reply.get("origins"), caller.runtime_id)
        self._service.retry_parked()  # 联系上了：排着等它的话可以交过去了
        return bool(push) or bool(reply.get("origins"))

    def _pull_loop(self, key: str) -> None:
        failures = 0
        while not self._stop.is_set():
            with self._lock:
                if key not in self._peers:
                    return
            try:
                self._round(key, POLL_SECONDS)
                failures = 0
            except MeshError as error:
                with self._lock:
                    peer = self._peers.get(key)
                    if peer is not None:
                        peer.error = str(error)[:200]
                self._stop.wait(RETRY_SECONDS[min(failures, len(RETRY_SECONDS) - 1)])
                failures += 1
            except Exception as error:
                sys.stderr.write("小幽网络出错：%s: %s\n" % (type(error).__name__, error))
                self._stop.wait(RETRY_SECONDS[-1])

    def _push_loop(self, key: str) -> None:
        """这台有了新东西：马上推给它（它连不进来这台时只能靠推）。"""
        seq = self._cards.seq
        while not self._stop.is_set():
            seq = self._cards.wait_beyond(seq, 30)
            with self._lock:
                peer = self._peers.get(key)
                if peer is None:
                    return
                known = peer.cursors
            if known is None or not self._export(known, skip=key):
                continue
            try:
                self._round(key, 0)
            except MeshError:
                self._stop.wait(RETRY_SECONDS[1])
            except Exception as error:
                sys.stderr.write("小幽网络出错：%s: %s\n" % (type(error).__name__, error))
                self._stop.wait(RETRY_SECONDS[-1])

    def _sweep_loop(self) -> None:
        while not self._stop.wait(30):
            try:
                self.sweep()
            except Exception as error:
                sys.stderr.write("小幽网络整理出错：%s: %s\n" % (type(error).__name__, error))

    def sweep(self) -> None:
        """离线太久的 Runtime：它那里还在做的事显示看不到，它的帮手不再给小幽选；转给它的话作废。"""
        now = self._clock()
        with self._lock:
            offline = {key for key, peer in self._peers.items()
                       if ID.match(key) and not self._online(peer, now)}
            relays = [(message_id, target) for message_id, target in self._relays.items() if target in offline]
        for card in self._cards.recent(None, 10 ** 6, include_desktop=True):
            if card.get("mesh_origin") in offline and card["state"] in ("talking", "working", "waiting") \
                    and not card.get("stale"):
                entries = card["entries"]
                self._writer.replica_task(card["id"], {"mesh_origin": card["mesh_origin"], "stale": True}, entries)
        for message_id, target in relays:
            # 已经交过去了、那台却没了音讯：做没做不知道（unknown），不在这里重做。
            with self._lock:
                self._relays.pop(message_id, None)
            self._service.relay_done(message_id, False, "「%s」一直没回应，这句话可能没做"
                                     % self._peer_name(target), unknown=True)
        self._refresh_agents()
        self.shares.scan(now)
        self._service.retry_parked()


class MeshAgent(Agent):
    """网络里另一台 Runtime 上的帮手：把事交给那台、由那台的这个帮手做，结果回到这台的卡上。"""

    def __init__(self, mesh: Mesh, peer: str, peer_name: str, item: Dict[str, Any], name: str):
        super().__init__(AgentSpec(
            name=name, type="mesh", aliases=[],
            description="「%s」上的 %s：%s" % (peer_name, item["name"], item.get("description") or ""),
            speaks=True, timeout_seconds=0))
        self.mesh, self.peer, self.peer_name, self.remote_name = mesh, peer, peer_name, item["name"]

    def _worker(self, card: Optional[str]) -> Optional[Dict[str, Any]]:
        if not card:
            return None
        found = self.mesh._replicas.find(self.peer, "task", lambda data: isinstance(data.get("delegated_from"), dict)
                                         and data["delegated_from"].get("runtime") == self.mesh.runtime_id
                                         and data["delegated_from"].get("card") == card)
        return found[-1] if found else None

    def run(self, job: Job) -> Outcome:
        problem = self.mesh._reachable(self.peer)
        if problem:
            raise AgentError(problem)
        message_id = uuid.uuid4().hex
        command = self.mesh.command(self.peer, "message", {
            "conversation": job.conversation, "text": job.text, "agent": self.remote_name,
            "hop": job.hop + 1, "message_id": message_id, "effort": job.effort,
            "delegated_from": {"runtime": self.mesh.runtime_id, "card": job.card or "", "name": self.mesh.name}})
        control = job.control
        if control is not None:
            def stop() -> bool:
                worker = self._worker(job.card)
                if worker is not None:
                    self.mesh.command(self.peer, "cancel", {"card": worker["id"]})
                return True

            def steer(text: str) -> bool:
                worker = self._worker(job.card)
                if worker is None or self.mesh._reachable(self.peer):
                    return False
                self.mesh.command(self.peer, "message", {
                    "conversation": job.conversation, "text": text, "card": worker["id"], "pin": True,
                    "agent": self.remote_name, "message_id": uuid.uuid4().hex,
                    "delegated_from": {"runtime": self.mesh.runtime_id, "card": job.card or "",
                                       "name": self.mesh.name}})
                return True

            control.can_stop(stop)
            control.can_steer(steer)
        limit = job.timeout or self.spec.timeout_seconds
        began = time.monotonic()
        shown: List[str] = []
        while True:
            if control is not None and control.cancelled:
                raise AgentError("取消了")
            accepted = self.mesh.outcome(command, self.peer, "accepted")
            if accepted is not None and not accepted.get("ok"):
                raise AgentError("「%s」没接这件事：%s" % (self.peer_name, accepted.get("error") or "没有说明"))
            done = self.mesh.outcome(command, self.peer, "done")
            if done is not None:
                if done.get("ok") and (done.get("reply") or "").strip():
                    return Outcome(done["reply"], None, {"reply": done["reply"], "brief": done.get("brief"),
                                                          "mood": done.get("mood")})
                raise AgentError("「%s」那边没做成：%s" % (self.peer_name, done.get("error") or "没有说明"))
            worker = self._worker(job.card)
            if worker is not None and control is not None:
                lines = [line for line in worker.get("progress", []) if isinstance(line, str)]
                for line in lines:
                    if line not in shown:
                        control.progress(line)
                        control.touch()
                shown = lines
            if limit and time.monotonic() - began > limit:
                raise AgentError("「%s」超过 %d 秒还没做完" % (self.peer_name, limit))
            if self.mesh._reachable(self.peer) and accepted is None:
                raise AgentError(self.mesh._reachable(self.peer) or "连不上")
            self.mesh.wait(5)
