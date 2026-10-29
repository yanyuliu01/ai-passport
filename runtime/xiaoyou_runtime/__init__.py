"""Compatibility import path; new code and commands use xiaoyu_runtime."""
import importlib
import importlib.abc
import importlib.util
import sys

from xiaoyu_runtime import __version__


class _LegacyLoader(importlib.abc.Loader):
    def __init__(self, target):
        self.target = target

    def create_module(self, spec):
        module = importlib.import_module(self.target)
        self.attributes = {key: getattr(module, key, None) for key in ("__name__", "__package__", "__spec__", "__loader__")}
        return module

    def exec_module(self, module):
        for key, value in self.attributes.items():
            setattr(module, key, value)

    def get_code(self, fullname):
        return compile("import runpy; runpy.run_module(%r, run_name='__main__')" % self.target, fullname, "exec")


class _LegacyFinder(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname in ('xiaoyou_runtime.__main__', 'xiaoyou_runtime.shared_history'):
            return None
        if not fullname.startswith('xiaoyou_runtime.'):
            return None
        canonical = fullname.replace('xiaoyou_runtime', 'xiaoyu_runtime', 1)
        if canonical == 'xiaoyu_runtime.loop.xiaoyou':
            canonical = 'xiaoyu_runtime.loop.xiaoyu'
        found = importlib.util.find_spec(canonical)
        return importlib.util.spec_from_loader(fullname, _LegacyLoader(canonical),
                                              is_package=found.submodule_search_locations is not None) if found else None


sys.meta_path.insert(0, _LegacyFinder())
