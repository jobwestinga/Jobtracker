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
JS_DYNAMIC = re.compile(r"\bimport\s*\(\s*[\"']([^\"']+)[\"']\s*\)")


def js_code_mask(source: str) -> str:
    """Preserve offsets while blanking comments and literal contents.

    This supports the repository's explicit ES import/export forms. It is a
    lexical aid, not a JavaScript parser. Template bodies are treated as literals;
    dependency imports should use explicit quoted specifiers, not interpolation.
    """
    chars = list(source)
    index = 0
    while index < len(source):
        if source.startswith("//", index):
            end = source.find("\n", index)
            end = len(source) if end == -1 else end
            for position in range(index, end):
                chars[position] = " "
            index = end
        elif source.startswith("/*", index):
            end = source.find("*/", index + 2)
            end = len(source) if end == -1 else end + 2
            for position in range(index, end):
                if chars[position] not in "\r\n":
                    chars[position] = " "
            index = end
        elif source[index] in "\"'`":
            quote = source[index]
            index += 1
            while index < len(source):
                if source[index] == quote:
                    index += 1
                    break
                if source[index] == "\\":
                    chars[index] = " "
                    index += 1
                    if index < len(source):
                        chars[index] = " "
                        index += 1
                else:
                    if chars[index] not in "\r\n":
                        chars[index] = " "
                    index += 1
        else:
            index += 1
    return "".join(chars)


def js_links(source: str) -> list[str]:
    mask = js_code_mask(source)
    return [source[match.start(1):match.end(1)] for pattern in (JS_LINK, JS_DYNAMIC) for match in pattern.finditer(mask)]


def top_level(mask: str, position: int) -> bool:
    # Braces in comments/strings are already blanked. Balanced module code is
    # verified by Node/browser checks; here this limits supported declarations.
    before = mask[:position]
    return before.count("{") == before.count("}")


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
    mask = js_code_mask(source)
    exports = {match.group(1) for match in JS_DECL.finditer(mask) if top_level(mask, match.start())}
    if any(top_level(mask, match.start()) for match in re.finditer(r"\bexport\s+default\b", mask)):
        exports.add("default")
    bindings = {match.group(1) for match in re.finditer(r"\b(?:function|class|const|let|var)\s+([A-Za-z_$][\w$]*)", mask) if top_level(mask, match.start())}
    for match in re.finditer(r"\bimport\s+([^;]*?)\s+from\s*[\"']([^\"']+)[\"']", mask):
        dependency = source[match.start(2):match.end(2)]
        available = js_exports((path.parent / dependency).resolve(), seen.copy())
        clause = match.group(1).strip()
        named = re.search(r"\{([^}]+)\}", clause)
        if named:
            for name in named.group(1).split(","):
                words = name.strip().split()
                if words and words[0] in available:
                    bindings.add(words[-1])
        namespace = re.search(r"\*\s+as\s+(\w+)", clause)
        if namespace:
            bindings.add(namespace.group(1))
        default = re.match(r"(\w+)\s*(?:,|$)", clause)
        if default and "default" in available:
            bindings.add(default.group(1))
    for match in JS_NAMES.finditer(mask):
        names = match.group(1)
        dependency = source[match.start(2):match.end(2)] if match.group(2) else None
        available = js_exports((path.parent / dependency).resolve(), seen.copy()) if dependency else bindings
        for part in names.split(","):
            words = part.strip().split()
            if words and words[0] in available:
                exports.add(words[-1])
    for match in JS_STAR.finditer(mask):
        dependency = source[match.start(1):match.end(1)]
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
