"""R7: the Runtime package has no import cycles, counting imports inside functions too."""

import ast
import unittest
from pathlib import Path

RUNTIME = Path(__file__).resolve().parents[1]
PACKAGE = RUNTIME / "xiaoyu_runtime"


def modules():
    found = {}
    for path in PACKAGE.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        parts = list(path.relative_to(RUNTIME).with_suffix("").parts)
        is_package = parts[-1] == "__init__"
        found[".".join(parts[:-1] if is_package else parts)] = (path, is_package)
    return found


def graph():
    found = modules()
    edges = {name: set() for name in found}
    for name, (path, is_package) in found.items():
        here = name.split(".") if is_package else name.split(".")[:-1]
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ImportFrom):
                if node.level:
                    base = here[:len(here) - (node.level - 1)]
                    module = ".".join(base + ([node.module] if node.module else []))
                else:
                    module = node.module or ""
                targets = [module + "." + a.name if module + "." + a.name in found else module
                           for a in node.names]
            elif isinstance(node, ast.Import):
                targets = [a.name for a in node.names]
            else:
                continue
            for target in targets:
                while target and target not in found:
                    target = target.rpartition(".")[0]
                # A package's __init__ is not a dependency on its submodules.
                if target and target != name and target != "xiaoyu_runtime" \
                        and not name.startswith(target + "."):
                    edges[name].add(target)
    return edges


def cycles(edges):
    index, low, stack, on, found, counter = {}, {}, [], set(), [], [0]

    def visit(v):
        index[v] = low[v] = counter[0]
        counter[0] += 1
        stack.append(v)
        on.add(v)
        for w in sorted(edges[v]):
            if w not in index:
                visit(w)
                low[v] = min(low[v], low[w])
            elif w in on:
                low[v] = min(low[v], index[w])
        if low[v] == index[v]:
            component = []
            while True:
                w = stack.pop()
                on.discard(w)
                component.append(w)
                if w == v:
                    break
            if len(component) > 1:
                found.append(sorted(component))

    for v in sorted(edges):
        if v not in index:
            visit(v)
    return found


class ImportLayerTests(unittest.TestCase):
    def test_events_and_world_do_not_import_execution_or_delivery_layers(self):
        edges = graph()
        violations = sorted(
            (source, target)
            for source, targets in edges.items()
            if source.startswith(("xiaoyu_runtime.events.", "xiaoyu_runtime.world."))
            for target in targets
            if target.split(".")[1] in {"helpers", "loop", "api", "delivery"}
        )
        self.assertEqual([], violations)

    def test_no_import_cycles_including_deferred_imports(self):
        self.assertEqual([], cycles(graph()))

    def test_the_check_sees_a_cycle_through_a_function_level_import(self):
        self.assertEqual([["a", "b"]], cycles({"a": {"b"}, "b": {"a"}, "c": {"a"}}))


if __name__ == "__main__":
    unittest.main()
