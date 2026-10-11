"""Check layer direction and eager import cycles without importing the app."""
from __future__ import annotations

import argparse
import ast
from pathlib import Path

try:
    from .source_index import ROOT, JS_LINK
except ImportError:
    from source_index import ROOT, JS_LINK


def module_name(path: Path, root: Path) -> str:
    parts = list(path.relative_to(root).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def imports(path: Path, root: Path, modules: dict, *, eager=False) -> set[str]:
    module = module_name(path, root)
    package = module if path.name == "__init__.py" else module.rpartition(".")[0]
    found = set()

    def visit(nodes):
        for node in nodes:
            if isinstance(node, ast.If) and (ast.unparse(node.test) in {"TYPE_CHECKING", "typing.TYPE_CHECKING"}):
                visit(node.orelse)
                continue
            if eager and isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                continue
            if isinstance(node, ast.Import):
                found.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                base = node.module or ""
                if node.level:
                    parts = package.split(".")
                    base = ".".join(parts[:len(parts) - node.level + 1] + ([base] if base else []))
                candidates = [base + "." + alias.name for alias in node.names]
                found.update(candidate for candidate in candidates if candidate in modules)
                if base and (not candidates or any(candidate not in modules for candidate in candidates)):
                    found.add(base)
            for child in ast.iter_child_nodes(node):
                if hasattr(child, "body") and isinstance(child.body, list):
                    visit(child.body)
                elif isinstance(child, (ast.Import, ast.ImportFrom)):
                    visit([child])
            # A ClassDef/FunctionDef's body is a direct list, not a child node.
            if hasattr(node, "body") and isinstance(node.body, list):
                visit(node.body)
            if hasattr(node, "orelse") and isinstance(node.orelse, list):
                visit(node.orelse)
    visit(ast.parse(path.read_text()).body)
    return found


def cycles(graph: dict[str, set[str]]) -> list[str]:
    visited, stack, active, result = set(), [], set(), []

    def walk(node):
        if node in active:
            result.append(" -> ".join(stack[stack.index(node):] + [node]))
            return
        if node in visited:
            return
        active.add(node)
        stack.append(node)
        for target in sorted(graph.get(node, ())):
            walk(target)
        stack.pop()
        active.remove(node)
        visited.add(node)
    for node in sorted(graph):
        walk(node)
    return result


def check(root: Path) -> list[str]:
    paths = [*sorted((root / "jobtracker").rglob("*.py")), *sorted((root / "server").rglob("*.py"))]
    modules = {module_name(path, root): path for path in paths}
    graph, errors = {}, []
    for name, path in modules.items():
        dependencies = imports(path, root, modules)
        forbidden = []
        if name.startswith("jobtracker.core"):
            forbidden = ["PySide6", "fastapi", "server", "jobtracker.ui", "jobtracker.services", "jobtracker.sync"]
        elif name.startswith("jobtracker.services"):
            forbidden = ["PySide6", "fastapi", "server", "jobtracker.ui", "jobtracker.sync"]
        elif name.startswith("jobtracker.sync") and name != "jobtracker.sync.qt_worker":
            forbidden = ["PySide6", "fastapi", "server", "jobtracker.ui"]
        elif name.startswith("server"):
            forbidden = ["PySide6", "jobtracker.ui"]
        for dependency in sorted(dependencies):
            if any(dependency == prefix or dependency.startswith(prefix + ".") for prefix in forbidden):
                errors.append(f"{path.relative_to(root)}: forbidden import {dependency}")
        graph[name] = imports(path, root, modules, eager=True) & modules.keys()
    errors += ["Python eager import cycle: " + cycle for cycle in cycles(graph)]
    web = root / "web"
    js_graph = {}
    for path in sorted(web.rglob("*.js")):
        if path.name == "sw.js":
            continue
        name = path.relative_to(root).as_posix()
        edges = set()
        for dependency in JS_LINK.findall(path.read_text()):
            if not dependency.startswith("."):
                errors.append(f"{name}: use explicit relative ES imports: {dependency}")
                continue
            target = (path.parent / dependency).resolve()
            if not target.is_relative_to(web.resolve()) or not target.is_file():
                errors.append(f"{name}: missing/escaping import {dependency}")
                continue
            target_name = target.relative_to(root.resolve()).as_posix()
            if name.startswith("web/features/") and target_name == "web/app.js":
                errors.append(f"{name}: feature imports composition shell")
            edges.add(target_name)
        js_graph[name] = edges
    errors += ["JavaScript import cycle: " + cycle for cycle in cycles(js_graph)]
    for path in (root / "tests").rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("tests.test_"):
                errors.append(f"{path.relative_to(root)}: import shared helpers from tests.support")
    return sorted(set(errors))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args()
    errors = check(args.root)
    if errors:
        print("\n".join(errors))
        return 1
    print("Layer direction, eager imports and test support boundaries pass.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
