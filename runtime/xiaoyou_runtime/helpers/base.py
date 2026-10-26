"""Shared helper types: jobs, outcomes, controls, watchdogs and the Agent base class.

Concrete agents (agents.py) and their protocol adapters (codex_app, dsh_app, dsh_stream,
decision, claude_gateway) import these from here, so no adapter imports agents.py back.
"""

import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple


from ..config import AgentSpec
from ..errors import AgentError

MAX_OUTPUT_CHARS = 200000
# 看不到事件流的跑法（任意命令、codex exec、只说话的那一次）没法判断卡没卡住，只能按
# 总时长：配置里写了“不限”时用这个。
UNWATCHED_SECONDS = 3600
# 没动静超过时限、去问主人“还等不等”之后的几种结果。
WAIT, STOP, SILENT, MOVED = "wait", "stop", "silent", "moved"
RESUME_HINT = "对这件事说“接着做”可以从停下的地方继续"



# 启动 Runtime 的那个 Claude / Codex 会话塞进环境的变量，只对那个会话有效，不往代理传。
PARENT_SESSION_ENV = (
    "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL",
    "CODEX_SANDBOX", "CODEX_SANDBOX_NETWORK_DISABLED", "CODEX_PERMISSION_PROFILE",
    "CODEX_CI", "CODEX_SESSION_ID", "CODEX_THREAD_ID", "CODEX_TOOL_CALL_ID",
)


class Cancelled(AgentError):
    """这一次是被叫停的（取消，或者主人改了要求要重做），不是代理自己出的错。"""


class Control:
    """后台任务手里的遥控器：叫停正在跑的这一次，或者中途追加一句话。

    每跑一次用一个新的。代理把自己起的进程登记进来，叫停时整个进程组一起结束；
    不起进程的代理不用管它，叫停之后它的结果会被丢掉。
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._process: Optional[subprocess.Popen] = None
        self._steer: Optional[Callable[[str], bool]] = None
        self._stop: Optional[Callable[[], bool]] = None
        self.cancelled = False
        # 下面几样由开任务的一方填好，代理用得上就用：
        # 做了一步操作时报一行进展
        self.progress: Callable[[str], None] = lambda line: None
        # Public execution events only; never reasoning or hidden provider context.
        self.activity: Callable[[str, str, Optional[str]], None] = lambda phase, text, key=None: None
        # 一拿到会话编号就报：这样中途被停掉也能接着用
        self.session: Callable[[str], None] = lambda session_id: None
        self.native_turn: Callable[[str, str], None] = lambda turn_id, phase: None
        # 权限询问工具要的环境变量（小门的地址和这件事的钥匙）；None 表示这一次没有人可问
        self.gate: Optional[Dict[str, str]] = None
        # 放临时文件（比如给 Claude Code 的 MCP 配置）的目录
        self.scratch: Optional[Path] = None
        # 代理自己能收到“可以吗”的询问时（Codex 的 app-server），用它问主人：
        # (谁要用什么, 内容原文) → 可以不可以。None 表示这一次没有人可问
        self.ask: Optional[Callable[[str, str], bool]] = None
        # 这件事是不是正停着等主人点头。等主人的时间不算在任何时限里
        self.paused: Callable[[], bool] = lambda: False
        # 没动静超过时限时用它问主人还等不等：(没动静了多少秒, 最后一步, 不用再问了吗)
        # → WAIT、STOP、SILENT（没人答）、MOVED（问的时候它自己又动了）。None 表示没有人可问
        self.stalled: Optional[Callable[[float, str, Callable[[], bool]], str]] = None
        # 代理一知道这一轮实际用的是哪个模型就报（配置里没写模型时，只有它自己知道）
        self.model: Callable[[str], None] = lambda name: None
        # 代理看到订阅额度的消息时原样报上来：(哪一种, 内容)。哪一种见 usage.py
        self.limits: Callable[[str, Any], None] = lambda kind, payload: None
        # 这一轮花掉的 token：in（新读的）、out（写的）、cached（从缓存读的）
        self.tokens: Dict[str, int] = {}
        # 代理每读到一行事件就 touch 一下；看门狗（Watchdog）靠这几样判断卡没卡住
        self._seen = time.monotonic()
        self._moves = 0
        self._busy: Set[str] = set()  # 已经开始、还没结束的操作
        self._step = ""  # 最后开始的那一步，原样
        self.patience = 1  # 主人每说一次“接着等”翻一倍

    def spend(self, tokens: Optional[Dict[str, int]]) -> None:
        """这一轮又花了这么多 token。"""
        with self._lock:
            for key, value in (tokens or {}).items():
                self.tokens[key] = self.tokens.get(key, 0) + value

    def touch(self, began: Optional[str] = None, ended: Optional[str] = None,
              step: Optional[str] = None) -> None:
        """有动静了。began / ended 是开始、结束的那一步操作的编号，step 是它的进展行。"""
        with self._lock:
            self._seen = time.monotonic()
            self._moves += 1
            if began:
                self._busy.add(began)
            if ended:
                self._busy.discard(ended)
            if step:
                self._step = step

    def quiet(self) -> Tuple[float, bool, int, str]:
        """（没动静了多少秒, 是不是有操作还没跑完, 到现在一共动了几次, 最后一步）。"""
        with self._lock:
            return time.monotonic() - self._seen, bool(self._busy), self._moves, self._step

    def attach(self, process: Optional[subprocess.Popen]) -> None:
        with self._lock:
            self._process = process
            late = self.cancelled and process is not None
        if late:
            kill_tree(process)

    def can_steer(self, steer: Optional[Callable[[str], bool]]) -> None:
        """代理支持中途追加时登记一个函数：收下了返回 True。"""
        with self._lock:
            self._steer = steer

    def steer(self, text: str) -> bool:
        with self._lock:
            steer = None if self.cancelled else self._steer
        try:
            return bool(steer(text)) if steer is not None else False
        except Exception:
            return False

    def can_stop(self, stop: Optional[Callable[[], bool]]) -> None:
        """代理有比直接结束进程更好的停法时登记一个函数：它接手了返回 True。"""
        with self._lock:
            self._stop = stop

    def cancel(self) -> None:
        with self._lock:
            self.cancelled = True
            process = self._process
            stop = self._stop
        if stop is not None:
            try:
                if stop():
                    return  # 代理自己会收尾，到时限还没停它会自己结束进程
            except Exception:
                pass
        if process is not None:
            kill_tree(process)


def _descendants(pid: int) -> List[int]:
    """这个进程起的所有进程（子、孙……）的编号。查不到就是空的。"""
    try:
        listing = subprocess.run(["ps", "-eo", "pid=,ppid="], stdout=subprocess.PIPE,
                                 stderr=subprocess.DEVNULL, text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    children: Dict[int, List[int]] = {}
    for line in listing.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
            children.setdefault(int(parts[1]), []).append(int(parts[0]))
    found: List[int] = []
    queue = [pid]
    while queue:
        for child in children.get(queue.pop(), []):
            if child not in found:
                found.append(child)
                queue.append(child)
    return found


def kill_tree(process: subprocess.Popen) -> None:
    """结束一个进程和它起的所有进程。

    只结束进程组不够：Claude Code 跑命令时会给命令另开一个会话，那些进程不在它的
    进程组里。所以先按父子关系把整棵树找出来，再一个一个结束。
    """
    if process.poll() is not None:
        return
    if os.name == "nt":
        try:
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(process.pid)],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError:
            pass
        return
    others = _descendants(process.pid)
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except (OSError, ProcessLookupError):
        try:
            process.kill()
        except OSError:
            pass
    for pid in others:
        try:
            os.kill(pid, signal.SIGKILL)
        except (OSError, ProcessLookupError):
            pass


def span(seconds: float) -> str:
    seconds = int(seconds)
    return "%d 秒" % seconds if seconds < 120 else "%d 分钟" % (seconds // 60)


class Watchdog:
    """看着后台的一轮有没有卡住。

    不按总时长一刀切（total 为 0 时根本不看总时长）：看的是多久没动静。平时连续 idle
    秒没有一行事件、或者有操作在跑时连续 idle_command 秒没有，就去问主人还等不等；主人
    说接着等就把耐心翻倍，说停、或者没人答，才结束进程，原因留在 reason 里。停着等主人
    点头的时间不算。
    """

    TICK = 1.0

    def __init__(self, control: Control, what: str, kill: Callable[[], None], total: int = 0,
                 idle: int = 0, idle_command: int = 0):
        self._control = control
        self._what = what
        self._kill = kill
        self._total = total
        self._idle = idle
        self._idle_command = idle_command
        self._lock = threading.Lock()
        self._done = threading.Event()
        self.reason: Optional[str] = None

    def start(self) -> "Watchdog":
        self._control.touch()
        if self._total or self._idle or self._idle_command:
            threading.Thread(target=self._watch, daemon=True, name="xiaoyou-watchdog").start()
        return self

    def stop(self) -> None:
        with self._lock:
            self._done.set()

    def _expire(self, reason: str) -> None:
        with self._lock:
            if self._done.is_set():
                return  # 这一轮已经自己结束了
            self._done.set()
            self.reason = reason
        self._kill()

    def _watch(self) -> None:
        control = self._control
        last = time.monotonic()
        spent = 0.0  # 不算等主人的时间
        while not self._done.wait(self.TICK):
            now = time.monotonic()
            if control.paused():
                control.touch()  # 主人答完之后从头计
                last = now
                continue
            spent += now - last
            last = now
            if self._total and spent > self._total:
                self._expire("%s 超过 %d 秒还没结束，已经停止" % (self._what, self._total))
                return
            idle, busy, moves, step = control.quiet()
            limit = (self._idle_command if busy else self._idle) * control.patience
            if not limit or idle < limit:
                continue
            verdict = STOP
            if control.stalled is not None:
                try:
                    verdict = control.stalled(
                        idle, step, lambda: self._done.is_set() or control.quiet()[2] != moves)
                except Exception:
                    verdict = SILENT
                last = time.monotonic()
                if verdict == MOVED:
                    continue  # 问的时候它自己又动了
                if verdict == WAIT:
                    control.patience *= 2
                    control.touch()
                    continue
                how = "问了主人没人答，已经停掉" if verdict == SILENT else "主人说停掉"
                self._expire("%s 有 %s没动静，%s。%s" % (self._what, span(idle), how, RESUME_HINT))
            else:
                self._expire("%s 已经 %s没有动静，已经停止。%s" % (
                    self._what, span(idle), RESUME_HINT))
            return


@dataclass(frozen=True)
class Job:
    """交给代理的一件事。"""

    text: str
    # 这个代理上一次在这个对话里的会话编号；没有就是新开
    session_id: Optional[str] = None
    # 对话名：代理自己不用，转给另一台 Runtime 时用它对上那边的对话
    conversation: str = "default"
    # 让代理以小幽的身份回话时：人设和回复格式。None 表示只是干活
    system: Optional[str] = None
    # 要求的回复结构（JSON Schema）；代理不支持时会忽略
    schema: Optional[Dict[str, Any]] = None
    # 这句话已经被别的 Runtime 转过几次手
    hop: int = 0
    # 只要它说话，不让它动手：不给任何工具。小幽这条线上的调用用这个，为的是几秒内答完
    plain: bool = False
    # 这一次最长多久；None 用代理自己配置的
    timeout: Optional[int] = None
    # 后台任务的遥控器；None 表示这一次不能中途叫停
    control: Optional[Control] = None
    # 这一次用哪一档努力程度、哪个模型；None 用这个代理平时的。代理不认的会被忽略
    effort: Optional[str] = None
    model: Optional[str] = None
    # 想边生成边看时：回复每多一块，就把到目前为止的原始全文交给它。代理做不到流式时不调用
    on_text: Optional[Callable[[str], None]] = None
    # 小幽手边的工具（voice_tools.Toolbox）：有它时她直接写给主人的文字就是说的话，要做事就调工具，
    # 不再写 JSON。只交给 talks_with_tools() 为真的代理；None 表示这一次不给工具
    tools: Any = None
    decision_only: bool = False
    decision_messages: Any = None
    # 主人插话了（threading.Event 被设上）：边说边出的这一次马上停下，已经说出的部分照样交回。
    # 真的因此提前停下的代理把它的 took_effect 设为 True（见 Cut）。
    interrupt: Any = None
    # 在哪个目录里跑；None 用代理配置的 workdir。接着电脑上的会话时要用那个会话的目录，
    # Claude Code 只在原项目目录下找得到要 --resume 的会话
    cwd: Optional[str] = None
    # 这是哪件事（卡的编号）；交给网络里别的 Runtime 时，那台替这件事开的卡记着它
    card: Optional[str] = None
    images: tuple = ()  # trusted local immutable snapshots, supplied by Runtime only
    title: str = ""  # Runtime-owned display name, never an owner prompt


def workdir(spec: "AgentSpec", job: Job) -> Optional[Path]:
    return Path(job.cwd) if job.cwd else spec.workdir


class Cut(threading.Event):
    """主人插话的信号。took_effect：说话的代理确实因此提前停下，交回的只是说了一半的话；
    回复已经说完之后才插话的不算。"""

    def __init__(self) -> None:
        super().__init__()
        self.took_effect = False


def stopped_early(interrupt: Any) -> None:
    """说话的代理因为插话不再往下说时调用。"""
    if isinstance(interrupt, Cut):
        interrupt.took_effect = True


@dataclass(frozen=True)
class Outcome:
    # 完整的原始结果
    text: str
    # 下一次接着聊要用的会话编号
    session_id: Optional[str] = None
    # 代理按要求的结构给出的字段（reply、brief、mood、action……）；没有就是 None
    fields: Optional[Dict[str, Any]] = None


def clip(text: str, limit: int) -> str:
    """截到 limit 个字符以内，截断时以省略号结尾。"""
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    return text[: max(1, limit - 1)].rstrip() + "…"


def _count(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else 0


def claude_tokens(usage: Any) -> Dict[str, int]:
    """Claude Code 结果里的 usage → in / out / cached。"""
    if not isinstance(usage, dict):
        return {}
    return {
        "in": _count(usage.get("input_tokens")) + _count(usage.get("cache_creation_input_tokens")),
        "out": _count(usage.get("output_tokens")),
        "cached": _count(usage.get("cache_read_input_tokens")),
    }


def codex_tokens(usage: Any) -> Dict[str, int]:
    """Codex 的一份 token 用量（app-server 的驼峰写法，或 exec 事件里的下划线写法）。"""
    if not isinstance(usage, dict):
        return {}
    cached = _count(usage.get("cachedInputTokens", usage.get("cached_input_tokens")))
    total = _count(usage.get("inputTokens", usage.get("input_tokens")))
    return {
        "in": max(0, total - cached),  # 它报的输入里含从缓存读的
        "out": _count(usage.get("outputTokens", usage.get("output_tokens"))),
        "cached": cached,
    }


def fields_from_text(text: str) -> Optional[Dict[str, Any]]:
    """代理把结构化回复当成普通文字写出来时，把它认出来。认不出返回 None。"""
    body = text.strip()
    if body.startswith("```"):
        # 去掉 ```json … ``` 这层包装
        lines = body.splitlines()
        if len(lines) >= 2 and lines[-1].strip() == "```":
            body = "\n".join(lines[1:-1]).strip()
    if body.startswith("{") and body.endswith("}"):
        try:
            value = json.loads(body)
        except json.JSONDecodeError:
            value = None
        if isinstance(value, dict) and isinstance(value.get("reply"), str):
            return value
    # 模型常在 JSON 前后多说一句（“下面是给主人的结论：{…}”），或者把它包在一段 ``` 里：
    # 从每个 { 起试着读一个完整的对象，认第一个带 reply 的。都不是就算认不出。
    decoder = json.JSONDecoder()
    at, tries = text.find("{"), 0
    while at >= 0 and tries < 64:  # 帮手的长结果里可能满是代码花括号：试有限次就停
        tries += 1
        try:
            value, _ = decoder.raw_decode(text, at)
        except json.JSONDecodeError:
            value = None
        if isinstance(value, dict) and isinstance(value.get("reply"), str):
            return value
        at = text.find("{", at + 1)
    return None


def _run_controlled(command: List[str], control: Control, timeout: int, **extra: Any):
    """和 subprocess.run 一样，但进程登记在遥控器上，可以中途叫停。"""
    text = extra.pop("input", None)
    if text is not None:
        extra["stdin"] = subprocess.PIPE
    if os.name != "nt":
        # 独立的进程组：叫停时连它起的子进程一起结束。
        extra["start_new_session"] = True
    process = subprocess.Popen(command, **extra)
    control.attach(process)
    try:
        out, err = process.communicate(text, timeout=timeout)
    except subprocess.TimeoutExpired:
        kill_tree(process)
        process.communicate()
        raise
    finally:
        control.attach(None)
    return subprocess.CompletedProcess(command, process.returncode, out, err)


def stream_command(command: List[str], text: str, cwd: Optional[Path], timeout: int, what: str,
                   env: Optional[Dict[str, str]], control: Control,
                   on_line: Callable[[str], None], idle: int = 0,
                   idle_command: int = 0) -> "subprocess.CompletedProcess[str]":
    """运行一条命令，标准输出来一行处理一行。失败的说法和 run_command 一样。

    timeout 是总时长，0 是不限；idle / idle_command 是多久没有一行输出算卡住（见 Watchdog）。
    """
    command = list(command)
    command[0] = shutil.which(command[0]) or command[0]
    extra: Dict[str, Any] = {}
    if env is not None:
        extra["env"] = env
    if cwd is not None:
        cwd.mkdir(parents=True, exist_ok=True)
        extra["cwd"] = str(cwd)
    if os.name != "nt":
        extra["start_new_session"] = True
    if control.cancelled:
        raise Cancelled("%s 被叫停了" % what)
    try:
        process = subprocess.Popen(
            command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace", **extra)
    except FileNotFoundError:
        raise AgentError("找不到命令 %s。请先安装并登录 %s，或在配置里改这个代理的 command"
                         % (command[0], what))
    except OSError as error:
        raise AgentError("启动 %s 失败：%s" % (what, error))
    control.attach(process)
    watch = Watchdog(control, what, lambda: kill_tree(process), timeout, idle, idle_command).start()
    errors: List[str] = []
    reader = threading.Thread(target=lambda: errors.append(process.stderr.read()), daemon=True)
    reader.start()
    try:
        try:
            process.stdin.write(text)
            process.stdin.close()
        except OSError:
            pass  # 它没读完就退出了；原因在退出码和标准错误里
        for line in process.stdout:
            control.touch()
            on_line(line)
        process.wait()
    finally:
        watch.stop()
        kill_tree(process)
        control.attach(None)
        reader.join(timeout=5)
        for pipe in (process.stdin, process.stdout, process.stderr):
            try:
                pipe.close()
            except OSError:
                pass
    if control.cancelled:
        raise Cancelled("%s 被叫停了" % what)
    if watch.reason is not None:
        raise AgentError(watch.reason)
    return subprocess.CompletedProcess(command, process.returncode, "", "".join(errors))


def run_command(command: List[str], text: Optional[str], cwd: Optional[Path], timeout: int,
                what: str, env: Optional[Dict[str, str]] = None, run=subprocess.run,
                control: Optional[Control] = None):
    """运行一条命令并把常见的失败换成说得清楚的 AgentError。"""
    command = list(command)
    # 按 PATH 找到完整路径再启动：Windows 上这样才能找到 claude.exe / claude.cmd。
    command[0] = shutil.which(command[0]) or command[0]
    extra: Dict[str, Any] = {}
    if env is not None:
        extra["env"] = env
    if cwd is not None:
        cwd.mkdir(parents=True, exist_ok=True)
        extra["cwd"] = str(cwd)
    if text is None:
        extra["stdin"] = subprocess.DEVNULL
    else:
        extra["input"] = text
    extra.update(stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                 encoding="utf-8", errors="replace")
    try:
        if control is not None and run is subprocess.run:
            if control.cancelled:
                raise Cancelled("%s 被叫停了" % what)
            done = _run_controlled(command, control, timeout, **extra)
            if control.cancelled:
                raise Cancelled("%s 被叫停了" % what)
            return done
        return run(command, **extra, timeout=timeout)
    except FileNotFoundError:
        raise AgentError("找不到命令 %s。请先安装并登录 %s，或在配置里改这个代理的 command"
                         % (command[0], what))
    except subprocess.TimeoutExpired:
        raise AgentError("%s 超过 %d 秒还没结束，已经停止" % (what, timeout))
    except OSError as error:
        raise AgentError("启动 %s 失败：%s" % (what, error))


class Agent:
    def __init__(self, spec: AgentSpec):
        self.spec = spec
        self.name = spec.name
        self.type = spec.type
        self.description = spec.description
        self.speaks = spec.speaks

    def run(self, job: Job) -> Outcome:
        raise NotImplementedError

    def tier(self, job: Job) -> Tuple[Optional[str], Optional[str]]:
        """这一次用的（模型，努力程度）；None 是不传，由工具自己定。

        点的档不在这个代理能选的里面时当作没点。只说话的那一次（小幽接话）求的是快，
        不带平时那一档，除非点了。
        """
        spec = self.spec
        if job.effort in spec.efforts:
            effort = job.effort
        else:
            effort = None if job.plain else spec.effort
        return job.model or spec.model, effort

    def check(self) -> Optional[str]:
        """启动时的检查：有问题返回说明，没问题返回 None。不调用模型。"""
        return None

    def talks_with_tools(self) -> bool:
        """以小幽的身份说话时能不能用工具调用（Job.tools）：说的话就是文字，做事走工具。
        不能的代理照旧写一个 JSON（reply、action……）。"""
        return False

    @staticmethod
    def _parent_session() -> List[str]:
        """Runtime 自己是从哪个 Claude / Codex 会话里启动时，环境里带着的那个会话的变量。"""
        return [key for key in PARENT_SESSION_ENV if key in os.environ]

    @staticmethod
    def _inherited_env() -> Dict[str, str]:
        """交给代理的环境：Runtime 自己的，去掉启动它的那个会话留下的变量。
        Claude 会话的临时登录交给 cc 会被拒（403 Request not allowed）；
        Codex 会话的沙箱标记（CODEX_SANDBOX_NETWORK_DISABLED 等）会让 codex 断网。
        配置里明确写了的照样用。"""
        return {key: value for key, value in os.environ.items() if key not in PARENT_SESSION_ENV}

    def _file_env(self) -> Dict[str, str]:
        """env_files 里的变量：每次现读，换了密钥不用重启。"""
        values = {}
        for key, path in self.spec.env_files.items():
            try:
                value = path.read_text(encoding="utf-8").strip()
            except OSError as error:
                raise AgentError("读不到 %s 用的文件 %s：%s" % (key, path, error.strerror or error))
            if not value:
                raise AgentError("%s 用的文件 %s 是空的" % (key, path))
            values[key] = value
        return values

    def _command_problem(self) -> Optional[str]:
        if shutil.which(self.spec.command[0]) is None and not Path(self.spec.command[0]).is_file():
            return "找不到命令 %s" % self.spec.command[0]
        return None
