"""Small static indexes used by repository checks. No app/database imports.

Python uses the AST. JavaScript checks intentionally cover explicit ES module
imports/exports in this repository, not arbitrary JavaScript or a call graph.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
JS_LINK = re.compile(r"(?:\bimport\s+(?:[^;]*?\s+from\s+)?|\bexport\s+[^;]*?\s+from\s+)[\"']([^\"']+)[\"']")
JS_DECL = re.compile(r"\bexport\s+(?:async\s+)?(?:function|class|const|let|var)\s+([A-Za-z_$][\w$]*)")
JS_NAMES = re.compile(r"\bexport\s*\{([^}]+)\}(?:\s*from\s*[\"']([^\"']+)[\"'])?")
JS_STAR = re.compile(r"\bexport\s*\*\s*from\s*[\"']([^\"']+)[\"']")


def inside(root: Path, relative: str) -> Path:
    path = (root / relative).resolve()
    if Path(relative).is_absolute() or not path.is_relative_to(root.resolve()):
        raise ValueError(f"path escapes repository: {relative}")
    if not path.is_file():
        raise ValueError(f"missing file: {relative}")
    return path


def python_symbols(path: Path) -> set[str]:
    symbols = set()

    def visit(nodes, prefix=""):
        for node in nodes:
            if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                name = prefix + node.name
                symbols.add(name)
                visit(node.body, name + ".")
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                symbols.update(prefix + (alias.asname or alias.name.split(".")[0]) for alias in node.names)
    visit(ast.parse(path.read_text()).body)
    return symbols


def js_exports(path: Path, seen=None) -> set[str]:
    seen = set() if seen is None else seen
    if path in seen:
        return set()
    seen.add(path)
    source = path.read_text()
    exports = set(JS_DECL.findall(source))
    for names, dependency in JS_NAMES.findall(source):
        available = js_exports((path.parent / dependency).resolve(), seen.copy()) if dependency else None
        for part in names.split(","):
            words = part.strip().split()
            if words and (available is None or words[0] in available):
                exports.add(words[-1])
    for dependency in JS_STAR.findall(source):
        exports.update(js_exports((path.parent / dependency).resolve(), seen.copy()))
    return exports


def operation_handlers(root: Path) -> dict[str, str]:
    tree = ast.parse((root / "server/operations/__init__.py").read_text())
    result = {}
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Call)
                and isinstance(node.func.func, ast.Name) and node.func.func.id == "register"
                and node.func.args and isinstance(node.func.args[0], ast.Constant)):
            name = node.func.args[0].value
            if name in result:
                raise ValueError(f"duplicate registered operation: {name}")
            result[name] = ast.unparse(node.args[0])
    return result


def api_routes(root: Path) -> dict[str, str]:
    result = {}
    for path in [root / "server/app.py", *sorted((root / "server/routes").glob("*.py"))]:
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for decorator in node.decorator_list:
                    if (isinstance(decorator, ast.Call) and isinstance(decorator.func, ast.Attribute)
                            and decorator.func.attr in {"get", "post", "put", "delete", "patch"}
                            and decorator.args and isinstance(decorator.args[0], ast.Constant)):
                        route = decorator.func.attr.upper() + " " + decorator.args[0].value
                        if route in result:
                            raise ValueError(f"duplicate route: {route}")
                        result[route] = f"{path.relative_to(root)}::{node.name}"
    return result


def desktop_operations(root: Path) -> dict[str, list[str]]:
    tree = ast.parse((root / "jobtracker/sync/service.py").read_text())
    result = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            for call in ast.walk(node):
                if (isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)
                        and call.func.attr == "_queue" and call.args
                        and isinstance(call.args[0], ast.Constant) and isinstance(call.args[0].value, str)):
                    result.setdefault(call.args[0].value, []).append(node.name)
    return result
