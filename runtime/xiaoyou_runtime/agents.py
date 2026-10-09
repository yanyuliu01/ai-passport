"""代理：小幽可以把话或者活交给谁。

小幽自己不在这里。这里的每个代理都只是“给它一段话，它给回一段结果”，并且各自记着
自己的会话。有的代理能直接以小幽的身份回话（Runtime 会把人设和回复格式一起给它），
有的只干活、结果由小幽转述。

  claude_code   非交互模式驱动本机的 Claude Code 命令行
  codex         非交互模式驱动本机的 Codex 命令行（codex exec）
  command       任意命令：这一段话从标准输入送进去，标准输出就是结果
  remote        另一台电脑上的小幽 Runtime
  echo          原样复述，不调用任何模型，用来测试链路

接新的代理只需要再加一个实现并在 create 里登记。
"""

import json
import os
import shutil
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from .config import AgentSpec

MAX_OUTPUT_CHARS = 200000


class AgentError(Exception):
    """这个代理这一次没有给出结果；消息可以直接给用户看。"""


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


@dataclass(frozen=True)
class Outcome:
    # 完整的原始结果
    text: str
    # 下一次接着聊要用的会话编号
    session_id: Optional[str] = None
    # 代理按要求的结构给出的字段（reply、brief、mood、handoff）；没有就是 None
    fields: Optional[Dict[str, Any]] = None


def clip(text: str, limit: int) -> str:
    """截到 limit 个字符以内，截断时以省略号结尾。"""
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    return text[: max(1, limit - 1)].rstrip() + "…"


def fields_from_text(text: str) -> Optional[Dict[str, Any]]:
    """代理把结构化回复当成普通文字写出来时，把它认出来。认不出返回 None。"""
    body = text.strip()
    if body.startswith("```"):
        # 去掉 ```json … ``` 这层包装
        lines = body.splitlines()
        if len(lines) >= 2 and lines[-1].strip() == "```":
            body = "\n".join(lines[1:-1]).strip()
    if not (body.startswith("{") and body.endswith("}")):
        return None
    try:
        value = json.loads(body)
    except json.JSONDecodeError:
        return None
    if isinstance(value, dict) and isinstance(value.get("reply"), str):
        return value
    return None


def run_command(command: List[str], text: Optional[str], cwd: Optional[Path], timeout: int,
                what: str, env: Optional[Dict[str, str]] = None, run=subprocess.run):
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
    try:
        return run(
            command, **extra, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            encoding="utf-8", errors="replace", timeout=timeout,
        )
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

    def check(self) -> Optional[str]:
        """启动时的检查：有问题返回说明，没问题返回 None。不调用模型。"""
        return None

    def _command_problem(self) -> Optional[str]:
        if shutil.which(self.spec.command[0]) is None and not Path(self.spec.command[0]).is_file():
            return "找不到命令 %s" % self.spec.command[0]
        return None


class EchoAgent(Agent):
    """原样复述，用来在没有任何模型的情况下验证手机、Runtime、设备之间的链路。"""

    def run(self, job: Job) -> Outcome:
        count = 1
        if job.session_id and job.session_id.startswith("echo-"):
            try:
                count = int(job.session_id.split("-")[-1]) + 1
            except ValueError:
                count = 1
        reply = "（回声）" + job.text
        fields = {"reply": reply, "mood": "happy"} if job.system is not None else None
        return Outcome(reply, "echo-%d" % count, fields)


class ClaudeCodeAgent(Agent):
    """用非交互模式驱动 Claude Code 命令行。

    每一次启动一次命令：这一段话从标准输入送进去，要求输出 JSON；有上一次的会话编号
    就带上 --resume 接着聊。对话记录由 Claude Code 自己保存。
    """

    def __init__(self, spec: AgentSpec, run=subprocess.run):
        super().__init__(spec)
        self._run = run

    def command(self, job: Job) -> List[str]:
        spec = self.spec
        command = list(spec.command)
        command += ["-p", "--output-format", "json"]
        if job.system:
            command += ["--append-system-prompt", job.system]
        if job.schema:
            command += ["--json-schema", json.dumps(job.schema, ensure_ascii=False)]
        command += ["--permission-mode", spec.permission_mode]
        if spec.allowed_tools:
            command += ["--allowedTools", ",".join(spec.allowed_tools)]
        if spec.model:
            command += ["--model", spec.model]
        if job.session_id:
            command += ["--resume", job.session_id]
        command += spec.extra_args
        return command

    def run(self, job: Job) -> Outcome:
        env = None
        if self.spec.config_dir is not None:
            # 让这台机器上的 Claude Code 用一套单独的登录和设置，
            # 不受（也不影响）使用者平时那套 ~/.claude 配置。
            env = dict(os.environ, CLAUDE_CONFIG_DIR=str(self.spec.config_dir))
        done = run_command(
            self.command(job), job.text, self.spec.workdir, self.spec.timeout_seconds,
            "Claude Code", env=env, run=self._run,
        )
        return self.parse(done.returncode, done.stdout, done.stderr)

    def parse(self, returncode: int, stdout: str, stderr: str) -> Outcome:
        payload: Any = None
        try:
            payload = json.loads(stdout) if stdout.strip() else None
        except json.JSONDecodeError:
            payload = None
        if not isinstance(payload, dict):
            detail = clip(stderr or stdout or "没有输出", 200)
            raise AgentError("Claude Code 没有返回可用的结果（退出码 %d）：%s" % (returncode, detail))
        session_id = payload.get("session_id") if isinstance(payload.get("session_id"), str) else None
        result = payload.get("result") if isinstance(payload.get("result"), str) else ""
        if returncode != 0 or payload.get("is_error") is True:
            raise AgentError("Claude Code 报告失败：%s" % clip(result or stderr or "没有说明", 200))
        structured = payload.get("structured_output")
        if isinstance(structured, dict) and isinstance(structured.get("reply"), str):
            return Outcome(structured["reply"], session_id, structured)
        if not result.strip():
            raise AgentError("Claude Code 返回了空的回复")
        # 没拿到结构化输出：整段文字就是结果。
        return Outcome(result, session_id, fields_from_text(result))

    def check(self) -> Optional[str]:
        return self._command_problem()


class CodexAgent(Agent):
    """用非交互模式驱动 Codex 命令行：codex exec，接着聊时用 codex exec resume。

    最后一条回复让 Codex 写进一个临时文件（-o），会话编号从它的事件输出（--json）里取。
    """

    def __init__(self, spec: AgentSpec, run=subprocess.run):
        super().__init__(spec)
        self._run = run

    def command(self, job: Job, last_message: Path) -> List[str]:
        spec = self.spec
        command = list(spec.command)
        command += ["exec", "--json", "--skip-git-repo-check", "-o", str(last_message)]
        if spec.sandbox:
            command += ["--sandbox", spec.sandbox]
        if spec.model:
            command += ["--model", spec.model]
        command += spec.extra_args
        if job.session_id:
            command += ["resume", job.session_id]
        command += ["-"]  # 这一段话从标准输入读
        return command

    def run(self, job: Job) -> Outcome:
        text = job.text if job.system is None else "%s\n\n%s" % (job.system, job.text)
        env = None
        if self.spec.config_dir is not None:
            # 让这台机器上的 Codex 用一套单独的登录和设置（CODEX_HOME），
            # 不受（也不影响）使用者平时那套 ~/.codex 配置。
            env = dict(os.environ, CODEX_HOME=str(self.spec.config_dir))
        with tempfile.TemporaryDirectory(prefix="xiaoyou-codex-") as folder:
            last_message = Path(folder) / "last.txt"
            done = run_command(
                self.command(job, last_message), text, self.spec.workdir,
                self.spec.timeout_seconds, "Codex", env=env, run=self._run,
            )
            try:
                final = last_message.read_text(encoding="utf-8").strip()
            except OSError:
                final = ""
        return self.parse(done.returncode, done.stdout, done.stderr, final, job.system is not None)

    def parse(self, returncode: int, stdout: str, stderr: str, final: str,
              speaking: bool = False) -> Outcome:
        session_id = None
        said = ""
        problem = ""
        for line in stdout.splitlines():
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(event, dict):
                continue
            kind = event.get("type")
            if kind == "thread.started" and isinstance(event.get("thread_id"), str):
                session_id = event["thread_id"]
            elif kind == "item.completed" and isinstance(event.get("item"), dict):
                item = event["item"]
                if item.get("type") == "agent_message" and isinstance(item.get("text"), str):
                    said = item["text"]
            elif kind == "turn.failed":
                error = event.get("error")
                problem = error.get("message", "") if isinstance(error, dict) else str(error or "")
            elif kind == "error" and isinstance(event.get("message"), str):
                problem = problem or event["message"]
        final = (final or said).strip()
        if returncode != 0 or not final:
            detail = problem or (stderr.strip().splitlines() or ["没有输出"])[-1]
            raise AgentError("Codex 没有给出结果（退出码 %d）：%s" % (returncode, clip(detail, 200)))
        return Outcome(final[:MAX_OUTPUT_CHARS], session_id,
                       fields_from_text(final) if speaking else None)

    def check(self) -> Optional[str]:
        problem = self._command_problem()
        if problem is None and self.spec.config_dir is not None and not self.spec.config_dir.is_dir():
            # Codex 自己不会建这个目录，目录不在它直接报错退出。
            return "找不到 Codex 的配置目录 %s：先建好并在里面登录（mkdir -p 这个目录，再 CODEX_HOME=这个目录 codex login）" % self.spec.config_dir
        return problem


class CommandAgent(Agent):
    """任意命令。这一段话从标准输入送进去；参数里写了 {prompt} 时改为替换进参数。

    没有会话：每一次都是从头开始，需要的上下文由 Runtime 写在这一段话里。
    """

    def __init__(self, spec: AgentSpec, run=subprocess.run):
        super().__init__(spec)
        self._run = run

    def run(self, job: Job) -> Outcome:
        text = job.text if job.system is None else "%s\n\n%s" % (job.system, job.text)
        command = self.spec.command
        if any("{prompt}" in part for part in command):
            command = [part.replace("{prompt}", text) for part in command]
            stdin = None
        else:
            stdin = text
        done = run_command(command, stdin, self.spec.workdir, self.spec.timeout_seconds,
                           self.name, run=self._run)
        output = done.stdout.strip()
        if done.returncode != 0 or not output:
            detail = (done.stderr.strip().splitlines() or ["没有输出"])[-1]
            raise AgentError("%s 没有给出结果（退出码 %d）：%s"
                             % (self.name, done.returncode, clip(detail, 200)))
        output = output[:MAX_OUTPUT_CHARS]
        return Outcome(output, None, fields_from_text(output) if job.system is not None else None)

    def check(self) -> Optional[str]:
        return self._command_problem()


class RemoteAgent(Agent):
    """另一台电脑上的小幽 Runtime。那边有自己的人设、代理和会话，回来的已经是小幽的话。"""

    POLL_SECONDS = 50

    def __init__(self, spec: AgentSpec, opener=urllib.request.urlopen):
        super().__init__(spec)
        self._open = opener

    def _call(self, method: str, path: str, body: Optional[Dict[str, Any]], timeout: float) -> Dict[str, Any]:
        data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(self.spec.url + path, data=data, method=method)
        request.add_header("Authorization", "Bearer %s" % self.spec.token)
        if data is not None:
            request.add_header("Content-Type", "application/json; charset=utf-8")
        try:
            with self._open(request, timeout=timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            detail = ""
            try:
                detail = json.loads(error.read().decode("utf-8")).get("error", "")
            except (ValueError, AttributeError, UnicodeDecodeError):
                pass
            if error.code == 401:
                raise AgentError("%s 不认这个令牌（401）：检查配置里这个代理的 token" % self.name)
            raise AgentError("%s 返回 %d%s" % (self.name, error.code, "：" + detail if detail else ""))
        except (urllib.error.URLError, OSError, ValueError) as error:
            raise AgentError("连不上 %s（%s）：%s" % (self.name, self.spec.url, error))
        if not isinstance(payload, dict):
            raise AgentError("%s 返回的内容看不懂" % self.name)
        return payload

    def run(self, job: Job) -> Outcome:
        deadline = time.monotonic() + self.spec.timeout_seconds
        message = self._call("POST", "/v1/messages", {
            "text": job.text, "conversation": job.conversation,
            "client_id": uuid.uuid4().hex, "hop": job.hop + 1,
        }, 20)
        while True:
            status = message.get("status")
            if status == "done":
                reply = message.get("reply") if isinstance(message.get("reply"), str) else ""
                if not reply.strip():
                    raise AgentError("%s 返回了空的回复" % self.name)
                return Outcome(reply, None, {
                    "reply": reply, "brief": message.get("brief"), "mood": message.get("mood"),
                })
            if status == "failed":
                raise AgentError("%s 那边没成功：%s" % (self.name, message.get("error") or "没有说明"))
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not isinstance(message.get("id"), str):
                raise AgentError("%s 超过 %d 秒还没结束" % (self.name, self.spec.timeout_seconds))
            wait = max(1, min(self.POLL_SECONDS, int(remaining)))
            message = self._call("GET", "/v1/messages/%s?wait=%d" % (message["id"], wait), None, wait + 15)

    def check(self) -> Optional[str]:
        return None


def create(spec: AgentSpec) -> Agent:
    kinds = {
        "claude_code": ClaudeCodeAgent,
        "codex": CodexAgent,
        "command": CommandAgent,
        "remote": RemoteAgent,
        "echo": EchoAgent,
    }
    return kinds[spec.type](spec)
