"""Read old installation inputs without changing durable identity or state."""
import os
from typing import Mapping


def environment(values: Mapping[str, str]) -> dict:
    result = dict(values)
    for key, value in values.items():
        if key.startswith('XIAOYOU_'):
            result.setdefault('XIAOYU_' + key[len('XIAOYOU_'):], value)
    return result


def env_get(key: str, default=None):
    if key in os.environ:
        return os.environ[key]
    return os.environ.get(key.replace('XIAOYU_', 'XIAOYOU_', 1), default)
