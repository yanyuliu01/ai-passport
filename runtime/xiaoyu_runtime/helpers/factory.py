"""Create the configured agent of each type (agents.py holds the implementations)."""

from ..config import AgentSpec
from .agents import Agent, CodexAgent, CommandAgent, DshAgent, EchoAgent, PiAgent, RemoteAgent
from .claude_gateway import create_claude_agent


def create(spec: AgentSpec) -> Agent:
    kinds = {
        "claude_code": create_claude_agent,
        "codex": CodexAgent,
        "dsh": DshAgent,
        "pi": PiAgent,
        "command": CommandAgent,
        "remote": RemoteAgent,
        "echo": EchoAgent,
    }
    return kinds[spec.type](spec)
