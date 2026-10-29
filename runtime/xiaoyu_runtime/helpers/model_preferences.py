"""Local helper defaults, separate from credentials and native CLI settings."""
import json
import os
import re
import tempfile
from dataclasses import replace
from pathlib import Path

from .helper_models import URL_ENV


def editable(spec):
    return spec.type in ("codex", "claude_code", "pi", "dsh") and URL_ENV not in spec.env


def options(spec):
    values = list(spec.models)
    if spec.type == "codex":
        home = Path(spec.env.get("CODEX_HOME") or os.environ.get("CODEX_HOME") or Path.home() / ".codex")
        try:
            cache = home / "models_cache.json"
            if cache.stat().st_size <= 2 * 1024 * 1024:
                values += [row["slug"] for row in json.loads(cache.read_text())["models"]
                           if row.get("visibility") == "list" and isinstance(row.get("slug"), str)]
        except (OSError, ValueError, KeyError, TypeError):
            pass
    elif spec.type == "claude_code":
        values += ["sonnet", "haiku", "opus"]
    return list(dict.fromkeys(([spec.model] if spec.model else []) + values))


class Preferences:
    def __init__(self, state_dir):
        self.path = Path(state_dir) / "helper-models.json"
        try:
            self.values = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(self.values, dict):
                raise ValueError("helper-models.json must be an object")
        except FileNotFoundError:
            self.values = {}

    def apply(self, spec):
        model = self.values.get(spec.name)
        return replace(spec, model=model) if editable(spec) and valid(model) else spec

    def save(self, name, model):
        values = dict(self.values, **{name: model})
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix="helper-models-", dir=str(self.path.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(values, stream, ensure_ascii=False)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
            self.values = values
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)


def valid(model):
    return isinstance(model, str) and bool(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}", model))
