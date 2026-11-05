"""Internal asynchronous worker for the Runtime's browser tool."""

import json
from pathlib import Path
from ..helpers.base import Agent, AgentError, Outcome
from . import bilibili
from .browser import logged_in, open_session


class BrowserToolWorker(Agent):
    def run(self, job):
        if not job.scratch or not job.artifact_dir:
            raise AgentError("浏览工具没有可用的任务目录或本地限额记录")
        if not logged_in():
            return Outcome("请先在小鱼桌面端的独立浏览器登录小鱼自己的 B 站账号。", None)
        def open_checked(url):
            if job.control is not None and job.control.cancelled:
                raise AgentError("B站任务已停止")
            return open_session(url)
        request = None
        try:
            # Older queued tasks may have owner notes appended after the JSON.
            # Decode the leading object so their chosen scope is not lost.
            candidate, _ = json.JSONDecoder().raw_decode(job.text.lstrip())
            if isinstance(candidate, dict) and candidate.get("_browser_request") == 1:
                request = candidate
        except (TypeError, ValueError):
            pass  # Existing persisted browser tasks contain plain text.
        target, count, read_video = job.text, 8, True
        if request is not None:
            target, count, read_video = request.get("url"), request.get("max_items"), request.get("read_video")
            if not isinstance(target, str) or len(target) > 2048:
                raise AgentError("浏览工具参数无效")
        text = bilibili.process(target, Path(job.scratch) / "entertainment" / "bilibili",
                                job.artifact_dir, open_checked, max_items=count, read_video=read_video)
        if job.control is not None and job.control.cancelled:
            raise AgentError("B站任务已停止")
        return Outcome(text, None)
