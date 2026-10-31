"""代理：小鱼可以把话或者活交给谁。

小鱼自己不在这里。这里的每个代理都只是“给它一段话，它给回一段结果”，并且各自记着
自己的会话。有的代理能直接以小鱼的身份回话（Runtime 会把人设和回复格式一起给它），
有的只干活、结果由小鱼转述。

  claude_code   非交互模式驱动本机的 Claude Code 命令行
  codex         非交互模式驱动本机的 Codex 命令行（codex exec）
  dsh           驱动本机的 DeepSeek Harness 命令行（dsh）
  command       任意命令：这一段话从标准输入送进去，标准输出就是结果
  remote        另一台电脑上的小鱼 Runtime
  echo          原样复述，不调用任何模型，用来测试链路

接新的代理只需要再加一个实现并在 factory.create 里登记。
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
import urllib.parse
import uuid
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from ..loop import project_brief
from ..world.approvals import progress_line
from ..config import AgentSpec
from .deadline import Deadline
from .base import (  # noqa: F401  re-exported: callers use agents.<name>
    Agent, AgentError, Cancelled, claude_tokens, clip, codex_tokens, Control, Cut, fields_from_text,
    Job, kill_tree, MAX_OUTPUT_CHARS, MOVED, Outcome, PARENT_SESSION_ENV, RESUME_HINT, run_command,
    SILENT, span, STOP, stopped_early, stream_command, UNWATCHED_SECONDS, WAIT, Watchdog, workdir,
    _count, _descendants, _run_controlled,
)


def login_problem(text: str) -> bool:
    """Claude Code 说的是没登录、登录失效或密钥不能用：这时该告诉主人去登录，而不是转述英文报错。"""
    lowered = (text or "").lower()
    return any(mark in lowered for mark in (
        "not logged in", "please run /login", "failed to authenticate", "invalid api key",
        "oauth token has expired", "authentication_error"))


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

    每一次启动一次命令：这一段话从标准输入送进去；有上一次的会话编号就带上 --resume
    接着聊。对话记录由 Claude Code 自己保存。

    两种跑法。只说话（小鱼接主人一句话、转述结果）：要一整个 JSON，不给工具。做事
    （后台任务）：要事件流，这样能看到它每一步在干什么、一开始就拿到会话编号；要问
    “可以吗”的操作通过权限询问工具（permission_mcp.py）交给主人点头。
    """

    PROMPT_TOOL = "mcp__xiaoyu__approve"
    native_resume = True

    def __init__(self, spec: AgentSpec, run=subprocess.run):
        super().__init__(spec)
        self._run = run

    def command(self, job: Job, stream: bool = False,
                mcp_config: Optional[Path] = None, plugin_dir: Optional[Path] = None,
                prompt_tool: bool = True) -> List[str]:
        spec = self.spec
        command = list(spec.command)
        if stream:
            command += ["-p", "--output-format", "stream-json", "--verbose"]
        else:
            command += ["-p", "--output-format", "json"]
        if job.system:
            command += ["--append-system-prompt", job.system]
        if job.schema:
            command += ["--json-schema", json.dumps(job.schema, ensure_ascii=False)]
        if job.plain:
            # 只说话：内置工具一个不给，别的（比如登记过的 MCP）一律不批。
            command += ["--tools", "", "--permission-mode", "dontAsk"]
        else:
            command += ["--permission-mode", spec.permission_mode]
            if mcp_config is not None:
                # 不加 --strict-mcp-config：使用者自己登记的 MCP 照常可用。
                if prompt_tool:
                    command += ["--permission-prompt-tool", self.PROMPT_TOOL]
                command += ["--mcp-config", str(mcp_config)]
            if plugin_dir is not None:
                # 这个帮手的 skill，拼成一个临时插件（helper_extras.claude_plugin）。
                command += ["--plugin-dir", str(plugin_dir)]
            for folder in spec.add_dirs:
                command += ["--add-dir", str(folder)]
            if spec.allowed_tools:
                command += ["--allowedTools", ",".join(spec.allowed_tools)]
        model, effort = self.tier(job)
        if model:
            command += ["--model", model]
        if effort:
            command += ["--effort", effort]
        if job.session_id:
            command += ["--resume", job.session_id]
        command += spec.extra_args
        return command

    def _env(self) -> Optional[Dict[str, str]]:
        if (self.spec.config_dir is None and not self.spec.env and not self.spec.env_files
                and not self._parent_session()):
            return None
        env = self._inherited_env()
        # 配置里写的环境变量：换接口地址、密钥、模型之类。
        env.update(self.spec.env)
        env.update(self._file_env())
        if self.spec.config_dir is not None:
            # 让这台机器上的 Claude Code 用一套单独的登录和设置，
            # 不受（也不影响）使用者平时那套 ~/.claude 配置。
            env["CLAUDE_CONFIG_DIR"] = str(self.spec.config_dir)
            # 指的正是默认的 ~/.claude（续接主人自己的原会话）时不写这个变量：macOS 上登录存在
            # 钥匙串里，条目名跟着这个变量变，写了就找不到主人平时 claude 登录的那份。
            if Path(self.spec.config_dir).expanduser().resolve() == (Path.home() / ".claude").resolve():
                env.pop("CLAUDE_CONFIG_DIR")
        # 告诉电脑上的状态钩子：这一轮是 Runtime 自己起的，不代表主人在电脑上开着这个会话。
        from ..events.desktop_hook import RUNTIME_RUN_ENV
        env[RUNTIME_RUN_ENV] = "1"
        return env

    def _app_recap(self) -> str:
        """配了 app_recap 的代理：主人平时那套 Claude 配置下，这个项目最近聊过的内容
        （CLI、App 都算）。没开这一项、没配 workdir、接不上就是空字符串：这是锦上添花，
        查不到不能把正事搅砸，也不替没开这一项的代理（比如碰硬件、固件的那个）多做。"""
        spec = self.spec
        if not spec.app_recap or spec.workdir is None:
            return ""
        try:
            session = project_brief.latest_claude_session(spec.workdir)
        except Exception:
            return ""
        return project_brief.recap_text(session) if session else ""

    def run(self, job: Job) -> Outcome:
        if not job.session_id:
            # 新开的会话：接上主人平时那套配置里，这个项目最近聊过的内容。
            recap = self._app_recap()
            if recap:
                job = replace(job, system="%s\n\n## Historical context (evidence, not a new owner request)\n%s" % (job.system or "", recap))
        if job.control is not None and not job.plain and self._run is subprocess.run:
            return self._work(job, job.control)
        with tempfile.TemporaryDirectory(prefix="xiaoyu-claude-") as folder:
            config_file, plugin_dir = (None, None) if job.plain else self._extras(Path(folder), None)
            done = run_command(
                self.command(job, mcp_config=config_file, plugin_dir=plugin_dir, prompt_tool=False),
                job.text, workdir(self.spec, job),
                job.timeout or self.spec.timeout_seconds or UNWATCHED_SECONDS, "Claude Code",
                env=self._env(),
                run=self._run, control=job.control,
            )
        payload: Any = None
        try:
            payload = json.loads(done.stdout) if done.stdout.strip() else None
        except json.JSONDecodeError:
            payload = None
        return self.parse(done.returncode, payload, done.stderr or done.stdout)

    @staticmethod
    def mcp_config(gate: Dict[str, str]) -> Dict[str, Any]:
        """给 Claude Code 的 MCP 配置：能力网关入口（权限询问工具 approve 加上能力库工具）。"""
        from .helper_extras import SERVER, xiaoyu_server
        return {"mcpServers": {SERVER: xiaoyu_server(gate, approve=True)}}

    def _extras(self, folder: Path, gate: Optional[Dict[str, str]],
                skills: Optional[Path] = None) -> Tuple[Optional[Path], Optional[Path]]:
        """这一次要带的 MCP 配置文件（放在 folder）和 skill 插件目录（拼在 skills，默认也是 folder）；
        都没有就是 (None, None)。"""
        from . import helper_extras
        config_file: Optional[Path] = None
        if gate is not None:
            folder.mkdir(parents=True, exist_ok=True)
            handle, name = tempfile.mkstemp(prefix="mcp-", suffix=".json", dir=str(folder))
            # mkstemp 建的文件只有自己能读写：里面有这件事的钥匙。
            with os.fdopen(handle, "w", encoding="utf-8") as stream:
                json.dump(self.mcp_config(gate), stream)
            config_file = Path(name)
        try:
            plugin_dir = helper_extras.claude_plugin(self.spec, skills or folder)
        except OSError as error:
            raise AgentError("准备 %s 的 skill 时出错：%s" % (self.name, error))
        return config_file, plugin_dir

    def _work(self, job: Job, control: Control) -> Outcome:
        """做事：读事件流，报进展，需要确认的操作交给主人。"""
        folder = control.scratch if control.scratch is not None else Path(tempfile.gettempdir())
        folder.mkdir(parents=True, exist_ok=True)
        # 钥匙文件直接放在 state 里（mkstemp 只给自己读写）；skill 插件另拼一个临时目录。
        scratch = Path(tempfile.mkdtemp(prefix="claude-skills-", dir=str(folder))) if self.spec.skills else None
        config_file, plugin_dir = self._extras(folder, control.gate, scratch)
        final: Dict[str, Any] = {}

        def on_line(line: str) -> None:
            line = line.strip()
            if not line.startswith("{"):
                return
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                return
            if not isinstance(event, dict):
                return
            kind = event.get("type")
            if kind == "system" and event.get("subtype") == "init":
                if isinstance(event.get("session_id"), str):
                    control.session(event["session_id"])
                if isinstance(event.get("model"), str) and event["model"]:
                    control.model(event["model"])
            elif kind == "rate_limit_event":
                # 订阅额度的状态：每次有变化它都会说一次，两个窗口各用了多少都在里面。
                control.limits("claude_event", event.get("rate_limit_info"))
            elif kind == "assistant":
                message = event.get("message")
                content = message.get("content") if isinstance(message, dict) else None
                for index, block in enumerate(content if isinstance(content, list) else []):
                    if (isinstance(block, dict) and block.get("type") == "tool_use"
                            and isinstance(block.get("name"), str)
                            # 这是它交结构化回复用的，不是一步操作。
                            and block["name"] != "StructuredOutput"):
                        step = progress_line(block["name"], block.get("input"))
                        # 从这里到它的结果回来，事件流是安静的：按“有操作在跑”的时限等。
                        control.touch(began=block.get("id") if isinstance(
                            block.get("id"), str) else None, step=step)
                        control.progress(step)
                        control.activity("tool_start", step, block.get("id"))
                    elif isinstance(block, dict) and block.get("type") == "text" and isinstance(block.get("text"), str):
                        control.activity("message", block["text"], "%s-%s" % (message.get("id"), index)
                                         if message.get("id") else None)
            elif kind == "user":
                message = event.get("message")
                content = message.get("content") if isinstance(message, dict) else None
                for block in content if isinstance(content, list) else []:
                    if (isinstance(block, dict) and block.get("type") == "tool_result"
                            and isinstance(block.get("tool_use_id"), str)):
                        control.touch(ended=block["tool_use_id"])
                        from ..events.desktop_content import message_text
                        output = message_text(block.get("content"))
                        status = "失败" if block.get("is_error") else "完成"
                        control.activity("tool_result", "%s · %s\n%s" % (
                            status, block["tool_use_id"], output), block["tool_use_id"])
            elif kind == "result":
                final.update(event)
                control.spend(claude_tokens(event.get("usage")))

        try:
            done = stream_command(
                self.command(job, stream=True, mcp_config=config_file, plugin_dir=plugin_dir,
                             prompt_tool=control.gate is not None), job.text,
                workdir(self.spec, job), job.timeout or self.spec.timeout_seconds, "Claude Code",
                self._env(), control, on_line, self.spec.idle_seconds,
                self.spec.idle_command_seconds,
            )
        finally:
            if config_file is not None:
                try:
                    config_file.unlink()
                except OSError:
                    pass
            if scratch is not None:
                shutil.rmtree(scratch, ignore_errors=True)
        return self.parse(done.returncode, final or None, done.stderr)

    def parse(self, returncode: int, payload: Any, detail: str) -> Outcome:
        """payload 是 Claude Code 最后给出的那个结果对象；没有就是 None。"""
        if not isinstance(payload, dict):
            raise AgentError("Claude Code 没有返回可用的结果（退出码 %d）：%s" % (
                returncode, clip(detail or "没有输出", 200)))
        session_id = payload.get("session_id") if isinstance(payload.get("session_id"), str) else None
        result = payload.get("result") if isinstance(payload.get("result"), str) else ""
        if returncode != 0 or payload.get("is_error") is True:
            said = result or detail or ""
            if login_problem(said):
                raise AgentError("电脑上的 Claude 没登录或登录失效：在电脑终端运行 claude auth login 后再试")
            raise AgentError("Claude Code 报告失败：%s" % clip(said or "没有说明", 200))
        structured = payload.get("structured_output")
        if isinstance(structured, dict) and isinstance(structured.get("reply"), str):
            return Outcome(structured["reply"], session_id, structured)
        if not result.strip():
            raise AgentError("Claude Code 返回了空的回复")
        # 没拿到结构化输出：整段文字就是结果。
        return Outcome(result, session_id, fields_from_text(result))

    def _effort_problem(self) -> Optional[str]:
        """这个版本的 Claude Code 认不认 --effort：看它的帮助。看不出来就当它认。"""
        try:
            done = run_command(list(self.spec.command) + ["--help"], None, None, 15,
                               "Claude Code")
        except AgentError:
            return None
        text = done.stdout or ""
        if "--model" in text and "--effort" not in text:
            return ("这个版本的 Claude Code 不认 --effort：升级它，或者把这个代理的 efforts 写成 []"
                    "（不传努力程度）")
        return None

    def check(self) -> Optional[str]:
        from .helper_extras import missing_skill
        problem = self._command_problem() or missing_skill(self.spec)
        if problem is None:
            try:
                self._file_env()
            except AgentError as error:
                return str(error)
            for key, path in self.spec.env_files.items():
                if os.name == "posix" and path.stat().st_mode & 0o077:
                    return "%s 用的文件 %s 别人也能读：chmod 600 %s" % (key, path, path)
        if problem is None and self.spec.effort and self._run is subprocess.run:
            problem = self._effort_problem()
        if problem is None and self.spec.env.get("ANTHROPIC_BASE_URL"):
            keys = ("ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_API_KEY")
            if not any(self.spec.env.get(key) or key in self.spec.env_files
                       or (os.environ.get(key) and key not in PARENT_SESSION_ENV)
                       for key in keys):
                return ("env 里设了 ANTHROPIC_BASE_URL，但没有 ANTHROPIC_AUTH_TOKEN："
                        "在 env_files 里指向放密钥的文件，或者填进 env")
        return problem


class CodexAgent(Agent):
    """驱动 Codex 命令行。两种模式，配置里的 mode 选：

    app_server（默认）：`codex app-server`，见 codex_app.py。需要确认的操作会来问主人，
    做的过程中可以追加一句话。只用于后台的事；别处（没有遥控器的调用）仍然走 exec。

    exec：`codex exec`，接着聊时用 `codex exec resume`。最后一条回复让 Codex 写进一个
    临时文件（-o），会话编号从它的事件输出（--json）里取。不会来问：沙箱不让做的就是
    做不了。
    """

    native_resume = True

    def __init__(self, spec: AgentSpec, run=subprocess.run):
        super().__init__(spec)
        self._run = run

    def _env(self) -> Optional[Dict[str, str]]:
        from .helper_models import prepare_codex
        prepare_codex(self.spec)
        if (self.spec.config_dir is None and not self.spec.env and not self.spec.env_files
                and not self._parent_session()):
            return None
        env = self._inherited_env()
        # 配置里写的环境变量（代理、接口地址之类）和 env_files 里的，和 Claude Code 一样带上。
        env.update(self.spec.env)
        env.update(self._file_env())
        if self.spec.config_dir is not None:
            # 让这台机器上的 Codex 用一套单独的登录和设置（CODEX_HOME），
            # 不受（也不影响）使用者平时那套 ~/.codex 配置。
            env["CODEX_HOME"] = str(self.spec.config_dir)
        return env

    def command(self, job: Job, last_message: Path) -> List[str]:
        from . import helper_extras
        spec = self.spec
        # 能力网关入口（有这一轮的钥匙时）：-c 是 codex 的全局参数，写在子命令前面。
        gate = job.control.gate if job.control is not None and not job.plain else None
        from .helper_models import codex_overrides as model_overrides
        command = list(spec.command) + model_overrides(spec) + helper_extras.codex_overrides(gate)
        command += ["exec", "--json", "--skip-git-repo-check", "-o", str(last_message)]
        if spec.sandbox:
            command += ["--sandbox", spec.sandbox]
        model, effort = self.tier(job)
        if model:
            command += ["--model", model]
        if effort:
            command += ["-c", 'model_reasoning_effort="%s"' % effort]
        if job.system:
            command += ["-c", "developer_instructions=" + json.dumps(job.system, ensure_ascii=False)]
        command += spec.extra_args
        if job.session_id:
            command += ["resume", job.session_id]
        for image in job.images:
            command += ["--image", str(image)]
        command += ["-"]  # 这一段话从标准输入读
        return command

    def run(self, job: Job) -> Outcome:
        if (self.spec.codex_mode == "app_server" and job.control is not None
                and self._run is subprocess.run):
            from . import codex_app  # 用到时才加载
            return codex_app.run(self.spec, job, job.control, self._env(), self.tier(job))
        text = job.text
        env = self._env()
        with tempfile.TemporaryDirectory(prefix="xiaoyu-codex-") as folder:
            last_message = Path(folder) / "last.txt"
            done = run_command(
                self.command(job, last_message), text, workdir(self.spec, job),
                job.timeout or self.spec.timeout_seconds or UNWATCHED_SECONDS, "Codex", env=env,
                run=self._run,
                control=job.control,
            )
            try:
                final = last_message.read_text(encoding="utf-8").strip()
            except OSError:
                final = ""
        if job.control is not None:
            job.control.spend(self.spent(done.stdout))
        return self.parse(done.returncode, done.stdout, done.stderr, final, job.system is not None)

    @staticmethod
    def spent(stdout: str) -> Dict[str, int]:
        """exec 的事件输出里每一轮结束时报的 token 用量，加起来。"""
        total: Dict[str, int] = {}
        for line in stdout.splitlines():
            line = line.strip()
            if not line.startswith("{") or "turn.completed" not in line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(event, dict) and event.get("type") == "turn.completed":
                for key, value in codex_tokens(event.get("usage")).items():
                    total[key] = total.get(key, 0) + value
        return total

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
        if problem is None and self.spec.codex_mode == "app_server":
            from . import codex_app
            problem = codex_app.check(self.spec, self._env())
        return problem


class DshAgent(Agent):
    """驱动 DeepSeek Harness（dsh）命令行。两条路：

    有遥控器时（后台的事）：`dsh --profile acp`，走 Agent Client Protocol，见
    dsh_app.py。沙箱（spec.sandbox）内的操作它自己做；出了沙箱才可能来问主人。

    没有遥控器时（小鱼接话那一次，求的是快）：`dsh --profile headless --json`，
    一次问答，不跟它聊起来也不会来问——沙箱不让做的就是做不了。模型和努力程度
    通过临时的 --patch 文件传（headless 没有 --model 这种参数）。
    """

    PROVIDER = "deepseek-official"
    native_resume = True

    def __init__(self, spec: AgentSpec, run=subprocess.run, opener=urllib.request.urlopen):
        super().__init__(spec)
        self._run = run
        self._open = opener

    def talks_with_tools(self) -> bool:
        # 说话那一次直接问 DeepSeek 的 Messages 接口（dsh_stream），它支持工具调用。
        return bool(self.spec.stream) and (
            "DEEPSEEK_API_KEY" in self.spec.env_files or bool(self.spec.env.get("DEEPSEEK_API_KEY"))
            or bool(os.environ.get("DEEPSEEK_API_KEY")))

    def _env(self) -> Optional[Dict[str, str]]:
        env = self._inherited_env()
        env.update(self.spec.env)
        for key, path in self.spec.env_files.items():
            try:
                value = path.read_text(encoding="utf-8").strip()
            except OSError as error:
                raise AgentError("读不到 %s 用的文件 %s：%s" % (key, path, error.strerror or error))
            if not value:
                raise AgentError("%s 用的文件 %s 是空的" % (key, path))
            env[key] = value
        if self.spec.config_dir is not None:
            # 让这台机器上的 dsh 用一套单独的 profile 和会话记录，
            # 不受（也不影响）使用者平时那套 ~/.dsh 配置。
            env["DSH_HOME"] = str(self.spec.config_dir)
        env["DSH_PERMISSION_MODE"] = self.spec.sandbox or "workspace-write"
        return env

    def run(self, job: Job) -> Outcome:
        if job.plain and job.model is None and self.spec.chat_model:
            job = replace(job, model=self.spec.chat_model)
        if job.decision_only:
            from ..loop.decision import choose
            return choose(self.spec, job, self._env(), self.tier(job)[0], self._open)
        if job.control is not None and self._run is subprocess.run:
            from . import dsh_app  # 用到时才加载
            return dsh_app.run(self.spec, job, job.control, self._env(), self.tier(job))
        if job.plain and job.on_text is not None and self.spec.stream:
            # 只说话、又想边生成边看：dsh 不逐字给，直接问 DeepSeek。不成就走老路。
            budget = Deadline(job.timeout or self.spec.timeout_seconds or 60)
            from . import dsh_stream
            try:
                env = self._env()
                return dsh_stream.run(self.spec, replace(job, timeout=budget.remaining()), env, self.tier(job)[0],
                                      job.on_text, self._open)
            except AgentError as error:
                if job.tools is not None:
                    # 带工具的这一次退回 dsh 老路就没有工具了（要做的事会丢）：交给调用方改走 JSON。
                    raise
                budget.remaining()
                sys.stderr.write("流式回复没成，改用 dsh：%s\n" % error)
                job.on_text("")
                job = replace(job, timeout=budget.remaining())
                if job.session_id and job.session_id.startswith(dsh_stream.SESSION_PREFIX):
                    job = replace(job, session_id=None)
                return self._headless(job, budget=budget)
        return self._headless(job)

    # 只要它说话、不让它动手时（job.plain）关掉的工具插件：bash、文件读写和搜索、
    # 后台任务、技能。关了这些，它就只能直接答，不会像背着活儿那样先去翻仓库。
    NO_TOOLS_PLUGINS = ("tool-bash", "tool-pwsh", "tool-jobs", "tool-fs", "tool-fs-search",
                       "tool-skill")

    def _patch_file(self, folder: Path, model: Optional[str], plain: bool) -> Optional[Path]:
        # 只说话这条线求的是几秒内答完：关掉思考，不然 DeepSeek 默认的 reasoningEffort
        # 是 high，光思考就能花上十几二十秒，跟这条线的用途对不上。
        from .helper_extras import dsh_skill_patch
        from .helper_models import dsh_patch, URL_ENV, PROVIDER
        skills = [] if plain else dsh_skill_patch(self.spec)
        skills += dsh_patch(self.spec)
        if not model and not plain and not skills:
            return None
        folder.mkdir(parents=True, exist_ok=True)
        handle, name = tempfile.mkstemp(prefix="dsh-patch-", suffix=".yml", dir=str(folder))
        lines: List[str] = []
        effective_model = model or self.spec.model
        if effective_model:
            lines += ["- id: agent-default-model",
                     "  name: '@deepseek-ai/dsh-agent-default-model'",
                     "  config:", "    provider: %s" % (PROVIDER if URL_ENV in self.spec.env else self.PROVIDER),
                     "    model: %s" % json.dumps(effective_model)]
            if plain and (URL_ENV not in self.spec.env or "off" in self.spec.efforts):
                lines.append('    reasoningEffort: "off"')
        if plain:
            for plugin in self.NO_TOOLS_PLUGINS:
                lines += ["- id: %s" % plugin, "  disabled: true"]
        lines += skills
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write("\n".join(lines) + "\n")
        return Path(name)

    @staticmethod
    def _stale_session(stdout: str) -> bool:
        """接不上这个会话编号（比如上一次是别的壳留下的）。"""
        for line in stdout.splitlines():
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if (isinstance(event, dict) and event.get("type") == "error"
                    and "does not exist" in str(event.get("message") or "")):
                return True
        return False

    def _headless(self, job: Job, budget=None) -> Outcome:
        timeout = job.timeout or self.spec.timeout_seconds or UNWATCHED_SECONDS
        # Background controls can ask the owner to keep waiting. Only speech
        # retries are bounded here; their caller supplied the remaining budget.
        budget = (budget or Deadline(timeout)) if job.plain else None
        model, _ = self.tier(job)
        text = job.text if job.system is None else "%s\n\n%s" % (job.system, job.text)
        if job.schema:
            text += ("\n\n请只输出一个 JSON 对象，不要代码围栏或额外说明。按以下 JSON Schema "
                    "填写字段；reply 是对主人说的话。\n" + json.dumps(job.schema, ensure_ascii=False))
        with tempfile.TemporaryDirectory(prefix="xiaoyu-dsh-") as folder:
            patch = self._patch_file(Path(folder), model, job.plain)

            def build(session_id: Optional[str]) -> List[str]:
                # --patch 是 dsh 自己的参数，要写在 --profile 后面；--json 和任务文字是
                # headless 这个 profile 自己的参数，写在 --patch 后面才认得出来。
                command = list(self.spec.command) + ["--profile", "headless"]
                if patch is not None:
                    command += ["--patch", str(patch)]
                command.append("--json")
                if session_id:
                    command += ["--session-id", session_id]
                command.append("-")
                return command

            env = self._env()
            done = run_command(build(job.session_id), text, workdir(self.spec, job),
                               budget.remaining() if budget is not None else timeout, "dsh",
                               env=env, run=self._run, control=job.control)
            if job.session_id and self._stale_session(done.stdout):
                if job.require_session:
                    raise AgentError("原 dsh 会话不存在，尚未续接；不会新开代替")
                # 当作新对话开始，不要因为接不上旧对话就让这一轮直接失败。
                done = run_command(build(None), text, workdir(self.spec, job),
                                   budget.remaining() if budget is not None else timeout, "dsh",
                                   env=env, run=self._run, control=job.control)
        return self.parse(done.returncode, done.stdout, done.stderr, job.system is not None)

    def parse(self, returncode: int, stdout: str, stderr: str, speaking: bool = False) -> Outcome:
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
            if kind == "session" and isinstance(event.get("sessionId"), str):
                session_id = event["sessionId"]
            elif kind == "text" and isinstance(event.get("text"), str):
                said += event["text"]
            elif kind == "final" and isinstance(event.get("text"), str):
                said = event["text"]
            elif kind == "status" and event.get("phase") == "turn_end":
                reason = event.get("reason")
                if isinstance(reason, dict) and reason.get("kind") == "error":
                    error = reason.get("error")
                    problem = (error.get("message") if isinstance(error, dict)
                              else str(error or "")) or problem
        said = said.strip()
        if returncode != 0 or not said:
            detail = problem or (stderr.strip().splitlines() or ["没有输出"])[-1]
            raise AgentError("dsh 没有给出结果（退出码 %d）：%s" % (returncode, clip(detail, 200)))
        return Outcome(said[:MAX_OUTPUT_CHARS], session_id,
                       fields_from_text(said) if speaking else None)

    def check(self) -> Optional[str]:
        from .helper_extras import missing_skill
        problem = self._command_problem() or missing_skill(self.spec)
        if problem is None:
            try:
                self._env()
            except AgentError as error:
                return str(error)
            for key, path in self.spec.env_files.items():
                if os.name == "posix" and path.stat().st_mode & 0o077:
                    return "%s 用的文件 %s 别人也能读：chmod 600 %s" % (key, path, path)
        if problem is None and self._run is subprocess.run:
            from . import dsh_app
            problem = dsh_app.check(self.spec, self._env())
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
        done = run_command(command, stdin, self.spec.workdir,
                           job.timeout or self.spec.timeout_seconds or UNWATCHED_SECONDS,
                           self.name, run=self._run,
                           control=job.control)
        output = done.stdout.strip()
        if done.returncode != 0 or not output:
            detail = (done.stderr.strip().splitlines() or ["没有输出"])[-1]
            raise AgentError("%s 没有给出结果（退出码 %d）：%s"
                             % (self.name, done.returncode, clip(detail, 200)))
        output = output[:MAX_OUTPUT_CHARS]
        return Outcome(output, None, fields_from_text(output) if job.system is not None else None)

    def check(self) -> Optional[str]:
        return self._command_problem()


class PiAgent(Agent):
    """驱动 pi 命令行（pi-coding-agent）。

    一次一条：`pi -p`，这一段话从标准输入送进去，最后的回复写到标准输出。会话编号由这里
    生成，用 --session-id 打开或新建，下次带同一个编号接着聊。用 Key 的模型从 env_files 里
    读（服务商自己的环境变量，比如 OPENAI_API_KEY）；订阅登录的在 pi 里 /login 过就行。

    pi 没有沙箱和审批：能用哪些工具，用 extra_args 里的 --tools 限定。
    """

    native_resume = True

    def __init__(self, spec: AgentSpec, run=subprocess.run):
        super().__init__(spec)
        self._run = run

    def _env(self) -> Optional[Dict[str, str]]:
        from .helper_models import prepare_pi
        prepare_pi(self.spec)
        if not self.spec.env and not self.spec.env_files and not self._parent_session():
            return None
        env = self._inherited_env()
        env.update(self.spec.env)
        env.update(self._file_env())
        return env

    def command(self, job: Job, session_id: str) -> List[str]:
        spec = self.spec
        command = list(spec.command) + ["-p", "--session" if job.require_session else "--session-id", session_id]
        model, effort = self.tier(job)
        if model:
            command += ["--model", model]
        if effort:
            command += ["--thinking", effort]
        if job.system:
            command += ["--append-system-prompt", job.system]
        if job.plain:
            # 只说话：一个工具也不给，也不带 skill。
            command += ["--no-tools", "--no-skills"]
        else:
            for skill in spec.skills:
                command += ["--skill", str(skill)]
        return command + spec.extra_args

    def _resume_file(self, job, env):
        # --session-id creates a missing session. Resolve an existing exact
        # header and pass --session <path> instead; never rely on prefix search.
        if not job.session_id or not job.cwd:
            raise AgentError("原 pi 会话缺少编号或目录，尚未续接")
        if any(arg.split('=', 1)[0] in ('--session', '--session-id', '--session-dir', '--no-session', '--continue', '-c', '--resume', '-r', '--fork') for arg in self.spec.extra_args):
            raise AgentError("pi 的额外参数覆盖会话选择，尚未续接")
        env = env or os.environ
        root = Path(env.get('PI_CODING_AGENT_SESSION_DIR') or
                    str(Path(env.get('PI_CODING_AGENT_DIR') or Path.home()/'.pi'/'agent')/'sessions')).expanduser()
        found = []
        for index, path in enumerate(root.rglob('*.jsonl')):
            if index >= 10000:
                raise AgentError("pi 来源目录过大，无法核实原会话，尚未续接")
            try:
                with path.open(encoding='utf-8') as stream:
                    header = json.loads(stream.readline(8192))
                if (header.get('type') == 'session' and header.get('id') == job.session_id
                        and Path(header.get('cwd') or '').resolve() == Path(job.cwd).resolve()):
                    found.append(path.resolve())
            except (OSError, ValueError, AttributeError):
                continue
        if len(found) != 1:
            raise AgentError("无法唯一核实原 pi 会话，尚未续接；不会新开代替")
        return str(found[0])

    def run(self, job: Job) -> Outcome:
        session_id = job.session_id or str(uuid.uuid4())
        env = self._env()
        selection = self._resume_file(job, env) if job.require_session else session_id
        text = job.text
        if job.schema:
            text += ("\n\n请只输出一个 JSON 对象，不要代码围栏或额外说明。按以下 JSON Schema "
                    "填写字段；reply 是对主人说的话。\n" + json.dumps(job.schema, ensure_ascii=False))
        done = run_command(self.command(job, selection), text, workdir(self.spec, job),
                           job.timeout or self.spec.timeout_seconds or UNWATCHED_SECONDS, "pi",
                           env=env, run=self._run, control=job.control)
        output = done.stdout.strip()
        if done.returncode != 0 or not output:
            detail = (done.stderr.strip().splitlines() or ["没有输出"])[-1]
            raise AgentError("pi 没有给出结果（退出码 %d）：%s" % (done.returncode, clip(detail, 200)))
        output = output[:MAX_OUTPUT_CHARS]
        return Outcome(output, session_id, fields_from_text(output) if job.system is not None else None)

    def check(self) -> Optional[str]:
        from .helper_extras import missing_skill
        problem = self._command_problem() or missing_skill(self.spec)
        if problem is None:
            try:
                self._file_env()
            except AgentError as error:
                return str(error)
        return problem


class RemoteAgent(Agent):
    """另一台电脑上的小鱼 Runtime。那边有自己的人设、代理和会话，回来的已经是小鱼的话。"""

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
        # 0 是不限：那一台自己看着它的帮手卡没卡住，这边只管等到它说结束。
        limit = job.timeout or self.spec.timeout_seconds
        began = time.monotonic()
        message = self._call("POST", "/v1/messages", {
            "text": job.text, "conversation": job.conversation,
            "client_id": uuid.uuid4().hex, "hop": job.hop + 1,
        }, 20)
        unified = message.get("sync_requests_v1") is True
        source = message.get("message_snapshot") or {}
        after, epoch = 0, ""
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
            remaining = limit - (time.monotonic() - began) if limit else self.POLL_SECONDS
            if remaining <= 0 or not isinstance(message.get("id"), str):
                raise AgentError("%s 超过 %d 秒还没结束" % (self.name, limit))
            wait = max(1, min(self.POLL_SECONDS, int(remaining)))
            if unified:
                key = message["id"]
                query = urllib.parse.urlencode({"conversation": job.conversation, "after": after,
                                                "epoch": epoch, "wait": wait, "requests": key})
                page = self._call("GET", "/v1/sync?" + query, None, wait + 15)
                if source and any(page.get(field) != source.get(field) for field in ("runtime_id", "subject_id")):
                    raise AgentError("%s 的回复来源已改变，原请求未重新提交" % self.name)
                current = next((item for item in page.get("requests", []) if isinstance(item, dict)
                                and item.get("id") == key and item.get("conversation") == job.conversation), None)
                if current is None:
                    raise AgentError("%s 的已受理请求状态不可读，原请求未重新提交" % self.name)
                message, after, epoch = current, page["seq"], page["epoch"]
            else:
                message = self._call("GET", "/v1/messages/%s?wait=%d" % (message["id"], wait), None, wait + 15)

    def check(self) -> Optional[str]:
        return None
