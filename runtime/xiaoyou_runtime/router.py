"""路由：这一句话先交给哪个代理。

判断的先后顺序固定：
  1. 调用方指定了代理（接口里的 agent）——照办；
  2. 主人在话里点了名（“@codex ……”“让 Codex 看看……”）——交给被点名的；
  3. 路由器有意见——听它的；
  4. 都没有——交给默认代理。

路由器是可以换的那一块。现在有两种：mention 什么都不额外判断（只靠前两条和默认
代理），command 运行一条命令来判断——以后接本地的小模型或者分类器就走这里。
交给默认代理之后，她仍然可以在回复里把活转交给别的代理，那是小幽自己的判断，
不归这里管（见 xiaoyou.py）。
"""

import json
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional, Tuple

from .agents import Agent, AgentError, run_command
from .config import Config

# 放在句首表示“让谁去做”的说法。
ASK_WORDS = ("让", "叫", "请", "用", "问问", "问", "找", "交给")
MENTION_BREAK = " \t\r\n,，:：、"


@dataclass(frozen=True)
class Route:
    agent: str
    # 交给代理的话（“@名字”这种纯粹的点名会被去掉）
    text: str
    # asked（调用方指定）、mention（话里点名）、router（路由器判断）、default
    reason: str
    # 点名时紧跟着说的努力程度；没说就是 None
    effort: Optional[str] = None


def _labels(agents: List[Agent]) -> List[Tuple[str, str]]:
    """所有名字和叫法，长的在前：两个叫法一个是另一个的前缀时先认长的。"""
    labels = []
    for agent in agents:
        for label in [agent.name] + list(agent.spec.aliases):
            labels.append((label.lower(), agent.name))
    labels.sort(key=lambda pair: -len(pair[0]))
    return labels


def _ascii_word(char: str) -> bool:
    return char.isascii() and (char.isalnum() or char in "_-")


def _starts_with_label(text: str, label: str) -> bool:
    if not text.lower().startswith(label):
        return False
    rest = text[len(label):]
    # 英文名后面还连着字母就不算（codexes 不是 codex）；中文叫法后面接什么都行。
    return not (rest and _ascii_word(rest[0]) and _ascii_word(label[-1]))


def _named(text: str, agents: List[Agent]) -> Optional[Tuple[str, str, int]]:
    """话里有没有在开头点名。有就返回（代理名，交给它的话，这段话里名字之后从哪开始）。"""
    body = text.strip()
    labels = _labels(agents)
    if body.startswith(("@", "＠")):
        rest = body[1:]
        for label, name in labels:
            if _starts_with_label(rest, label):
                said = rest[len(label):].lstrip(MENTION_BREAK)
                # 只点了名、没说事：把原话交过去，让代理自己问。
                return (name, said, 0) if said else (name, body, len(body))
        return None
    for word in sorted(ASK_WORDS, key=len, reverse=True):
        if body.startswith(word):
            rest = body[len(word):].lstrip()
            for label, name in labels:
                if _starts_with_label(rest, label):
                    return name, body, len(body) - len(rest) + len(label)
            return None
    return None


def mention(text: str, agents: List[Agent]) -> Optional[Tuple[str, str]]:
    """话里有没有在开头点名。有就返回（代理名，交给它的话）。"""
    named = _named(text, agents)
    return None if named is None else named[:2]


# 点名之后紧跟着说的档位：“@codex 高档 看看这个”“让 codex 用高档看看这个”。
EFFORT_WORDS = {
    "最低档": "minimal", "低档": "low", "中档": "medium", "高档": "high", "特高档": "xhigh",
    "最高档": "max", "minimal": "minimal", "low": "low", "medium": "medium", "high": "high",
    "xhigh": "xhigh", "max": "max",
}


def take_effort(text: str, start: int) -> Tuple[Optional[str], str]:
    """text 里从 start 开始（名字之后）有没有紧跟着一个档位词。

    有就返回（档，去掉这个词之后的话）。只认紧跟在名字后面的：前面带“用”字，或者后面
    有空格、标点、或者话到此为止——“高档餐厅”不算。
    """
    head, tail = text[:start], text[start:]
    body = tail.lstrip(MENTION_BREAK)
    used = body.startswith("用")
    lead = body[1:].lstrip() if used else body
    for word in sorted(EFFORT_WORDS, key=len, reverse=True):
        if not lead.lower().startswith(word):
            continue
        after = lead[len(word):]
        if after and _ascii_word(after[0]) and word.isascii():
            continue  # highlight 不是 high
        if not used and after and after[0] not in MENTION_BREAK:
            continue
        if used and after.startswith("的"):
            continue  # “用高档的……”说的是别的东西
        after = after.lstrip(MENTION_BREAK)
        for filler in ("来", "去"):
            if used and after.startswith(filler):
                after = after[1:].lstrip()
                break
        if not after:
            return None, text  # 只说了档位、没说事：原话交过去
        gap = " " if head and after and _ascii_word(head[-1]) and _ascii_word(after[0]) else ""
        return EFFORT_WORDS[word], head + gap + after
    return None, text


class Router:
    """没有额外意见的路由器。"""

    name = "mention"

    def pick(self, text: str, agents: List[Agent], default: str) -> Optional[str]:
        return None

    def check(self) -> Optional[str]:
        return None


class CommandRouter(Router):
    """运行一条命令来判断。

    标准输入是一个 JSON 对象：{"text", "default", "agents": [{"name", "type", "description"}]}；
    标准输出的第一行是选中的代理名。输出为空、不认识的名字、出错或超时都退回默认代理：
    路由器坏了不应该让主人说不了话。
    """

    name = "command"

    def __init__(self, command: List[str], cwd: Path, timeout: int,
                 log: Optional[Callable[[str], None]] = None, run=subprocess.run):
        self._command = command
        self._cwd = cwd
        self._timeout = timeout
        self._log = log or (lambda message: None)
        self._run = run

    def pick(self, text: str, agents: List[Agent], default: str) -> Optional[str]:
        request = json.dumps({
            "text": text,
            "default": default,
            "agents": [
                {"name": agent.name, "type": agent.type, "description": agent.description}
                for agent in agents
            ],
        }, ensure_ascii=False)
        try:
            done = run_command(self._command, request, self._cwd, self._timeout, "路由器",
                               run=self._run)
        except AgentError as error:
            self._log("路由器没给出判断，交给默认代理：%s" % error)
            return None
        lines = done.stdout.strip().splitlines()
        choice = lines[0].strip() if lines else ""
        if done.returncode != 0:
            self._log("路由器退出码 %d，交给默认代理" % done.returncode)
            return None
        if not choice:
            return None
        for agent in agents:
            if agent.name.lower() == choice.lower():
                return agent.name
        self._log("路由器选了不认识的代理 %s，交给默认代理" % re.sub(r"\s+", " ", choice)[:40])
        return None

    def check(self) -> Optional[str]:
        if shutil.which(self._command[0]) is None and not Path(self._command[0]).is_file():
            if not (self._cwd / self._command[0]).is_file():
                return "找不到路由器命令 %s" % self._command[0]
        return None


def create(config: Config, log: Optional[Callable[[str], None]] = None) -> Router:
    if config.router_type == "command":
        return CommandRouter(config.router_command, config.base_dir,
                             config.router_timeout_seconds, log)
    return Router()


def decide(text: str, asked: Optional[str], agents: List[Agent], default: str,
           router: Router) -> Route:
    """按文件开头写的顺序决定这句话先交给谁。agents 是这一轮可用的代理。"""
    names = [agent.name for agent in agents]
    if asked is not None and asked in names:
        return Route(asked, text, "asked")
    named = _named(text, agents)
    if named is not None:
        effort, said = take_effort(named[1], named[2])
        return Route(named[0], said, "mention", effort)
    choice = router.pick(text, agents, default)
    if choice is not None and choice in names:
        return Route(choice, text, "router")
    return Route(default if default in names else names[0], text, "default")
