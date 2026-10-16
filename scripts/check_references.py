"""Validate feature references and reproducible source-derived documentation."""
from __future__ import annotations

import argparse
import ast
import json
import re
from pathlib import Path
from urllib.parse import unquote

try:
    from .source_index import ROOT, inside, python_symbols, js_exports, operation_handlers, api_routes, desktop_operations
except ImportError:
    from source_index import ROOT, inside, python_symbols, js_exports, operation_handlers, api_routes, desktop_operations


def validate_ref(root: Path, reference: dict) -> None:
    path = inside(root, reference["path"])
    symbol = reference.get("symbol")
    if not symbol:
        return
    if path.suffix == ".py":
        symbols = python_symbols(path)
    elif path.suffix in {".js", ".mjs"}:
        symbols = js_exports(path)
    else:
        raise ValueError(f"unsupported symbol reference: {reference}")
    if symbol not in symbols:
        raise ValueError(f"missing symbol: {reference['path']}::{symbol}")


def heading_ids(text: str) -> set[str]:
    text = re.sub(r"```[\s\S]*?```|~~~[\s\S]*?~~~", "", text)
    result, counts = set(), {}
    for heading in re.findall(r"^#{1,6}\s+(.+?)\s*#*\s*$", text, re.M):
        heading = re.sub(r"[`*]", "", heading).lower()
        slug = re.sub(r"[^\w\- ]", "", heading).replace(" ", "-")
        duplicate = counts.get(slug, 0)
        counts[slug] = duplicate + 1
        result.add(slug + (f"-{duplicate}" if duplicate else ""))
    return result


def document_target(root: Path, source: Path, target: str) -> None:
    target = unquote(target.strip("<>"))
    file, _, anchor = target.partition("#")
    path = (source.parent / file).resolve() if file else source.resolve()
    if not path.is_relative_to(root.resolve()) or not path.exists():
        raise ValueError(f"{source.relative_to(root)}: broken local link {target}")
    if anchor and path.suffix == ".md" and anchor not in heading_ids(path.read_text()):
        raise ValueError(f"{source.relative_to(root)}: missing heading {target}")


def markdown_errors(root: Path) -> list[str]:
    errors = []
    paths = [*sorted((root / "docs").rglob("*.md")), *[root / name for name in ("README.md", "AGENTS.md", "CLAUDE.md")]]
    for path in paths:
        if not path.is_file():
            errors.append(f"missing navigation document: {path.relative_to(root)}")
            continue
        text = re.sub(r"```[\s\S]*?```|~~~[\s\S]*?~~~", "", path.read_text())
        targets = re.findall(r"\[[^\]\n]+\]\(\s*(<[^>]+>|[^\s)]+)(?:\s+[\"'][^\n]*?[\"'])?\s*\)", text)
        definitions = {" ".join(label.lower().split()): target for label, target in
                       re.findall(r"^\s{0,3}\[([^\]]+)\]:\s*(<[^>]+>|[^\s]+)", text, re.M)}
        targets += list(definitions.values())
        for match in re.finditer(r"\[([^\]\n]+)\](?:\[([^\]\n]*)\])?", text):
            if text[match.end():match.end()+1] in {"(", ":"}:
                continue
            label = " ".join((match.group(2) or match.group(1)).lower().split())
            if label in definitions:
                targets.append(definitions[label])
            elif match.group(2) is not None:
                errors.append(f"{path.relative_to(root)}: undefined reference link {label}")
        for target in targets:
            if re.match(r"(?:[a-z][a-z0-9+.-]*:|//)", target):
                continue
            try:
                document_target(root, path, target)
            except ValueError as exc:
                errors.append(str(exc))
    return errors


def phone_operations(root: Path) -> dict[str, str]:
    source = (root / "web/data/projection.js").read_text()
    projectors = re.search(r"const PROJECTORS\s*=\s*\{([\s\S]*?)^\};", source, re.M)
    unprojected = re.search(r"export const UNPROJECTED_OPERATIONS\s*=\s*Object.freeze\(\{([\s\S]*?)^\}\);", source, re.M)
    if not projectors or not unprojected:
        raise ValueError("phone operation inventories missing or unsupported shape")
    projected = re.findall(r"^  (\w+):", projectors.group(1), re.M)
    reasons = dict(re.findall(r'^  (\w+): "([^"\n]+)",?$', unprojected.group(1), re.M))
    weight = (root / "web/features/weight/projection.js").read_text()
    weights = re.search(r"WEIGHT_PROJECTED_OPERATIONS\s*=\s*Object.freeze\(\[([^]]*)\]\)", weight)
    if not weights:
        raise ValueError("weight operation inventory missing")
    weight_names = re.findall(r'"(\w+)"', weights.group(1))
    names = [*projected, *reasons, *weight_names]
    if len(names) != len(set(names)):
        raise ValueError("phone operation inventories overlap")
    return {**dict.fromkeys(projected, "Tracking display projection"),
            **dict.fromkeys(weight_names, "Weight display projection"),
            **{name: "Server authority: " + reason for name, reason in reasons.items()}}


def validate_metadata(root: Path, manifest: dict, registry: dict, handlers: dict, routes: dict) -> list[str]:
    errors = []
    if manifest.get("schema_version") != 1 or registry.get("schema_version") != 1:
        errors.append("unsupported feature/contract schema version")
    features = manifest.get("features", [])
    contract_ids = [contract["id"] for contract in registry.get("contracts", [])]
    feature_ids = [feature["id"] for feature in features]
    for kind, ids in (("feature", feature_ids), ("contract", contract_ids)):
        if len(ids) != len(set(ids)):
            errors.append(f"duplicate {kind} ID")
        if any(not re.fullmatch(r"[a-z][a-z0-9.-]+", ident) for ident in ids):
            errors.append(f"invalid {kind} ID")
    for contract in registry.get("contracts", []):
        try:
            document_target(root, root / "index.md", contract["document"])
        except ValueError as exc:
            errors.append(str(exc))
    for feature in features:
        name = feature["id"]
        for key in ("title", "entrypoints", "rule_owner", "persistence", "operations", "tables", "contracts", "routes", "tests", "related"):
            if key not in feature:
                errors.append(f"{name}: missing {key}")
        references = [*feature.get("entrypoints", []), feature.get("rule_owner", {}), *feature.get("persistence", []), *feature.get("supporting", [])]
        for reference in references:
            try:
                validate_ref(root, reference)
            except (KeyError, ValueError, OSError, SyntaxError) as exc:
                errors.append(f"{name}: {exc}")
        if feature.get("guide"):
            try:
                document_target(root, root / "index.md", feature["guide"])
            except ValueError as exc:
                errors.append(f"{name}: {exc}")
        for test in feature.get("tests", []):
            try:
                inside(root, test)
            except ValueError as exc:
                errors.append(f"{name}: {exc}")
        for field, allowed in (("related", feature_ids), ("contracts", contract_ids), ("operations", handlers), ("routes", routes)):
            for value in feature.get(field, []):
                if value not in allowed:
                    errors.append(f"{name}: unknown {field} reference {value}")
    return errors


def link(path: str, label=None) -> str:
    return f"[{label or path}](../../{path})"


def delegation_rows(root: Path, path: str, class_name: str) -> list[str]:
    tree = ast.parse((root / path).read_text())
    cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == class_name)
    # Binding names are extracted from the actual constructor, not another map.
    bindings = {}
    for node in ast.walk(cls):
        if (isinstance(node, ast.Assign) and isinstance(node.value, ast.Call)
                and isinstance(node.value.func, ast.Name)):
            for target in node.targets:
                if isinstance(target, ast.Attribute) and isinstance(target.value, ast.Name) and target.value.id == "self":
                    bindings[target.attr] = node.value.func.id
    rows = []
    for method in cls.body:
        if not isinstance(method, ast.FunctionDef):
            continue
        owners = set()
        for node in ast.walk(method):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and isinstance(node.func.value, ast.Attribute)
                    and isinstance(node.func.value.value, ast.Name) and node.func.value.value.id == "self"
                    and node.func.value.attr in bindings):
                owners.add(bindings[node.func.value.attr] + "." + node.func.attr)
        rows.append(f"| `{class_name}.{method.name}` | {', '.join('`' + owner + '`' for owner in sorted(owners)) or 'Facade / direct adapter'} |")
    return rows


def generated(root: Path, manifest: dict, handlers: dict, routes: dict, phone: dict) -> dict[str, str]:
    header = "<!-- Generated by scripts/check_references.py --write; edit source/manifest instead. -->\n\n"
    feature_rows = [header + "# Feature index\n\nUse `python3 scripts/dev.py context` to list/search features, then `context FEATURE_ID` for a focused task packet.\n\n| Feature | UI / entry points | Authoritative rule | Persistence / supporting files | Tests |\n| --- | --- | --- | --- | --- |"]
    for feature in manifest["features"]:
        owner = feature["rule_owner"]
        label = f"`{feature['id']}` — {feature['title']}"
        if feature.get("guide"):
            label += " · " + link(feature["guide"], "Guide")
        feature_rows.append(f"| {label} | "
                            + ", ".join(link(ref['path'], ref.get('symbol', ref['path'])) for ref in feature['entrypoints'])
                            + f" | {link(owner['path'], owner.get('symbol', owner['path']))} | "
                            + ", ".join(link(ref['path'], ref.get('symbol', ref['path'])) for ref in [*feature['persistence'], *feature.get('supporting', [])])
                            + " | " + ", ".join(link(path) for path in feature['tests']) + " |")
    desktop = desktop_operations(root)
    operation_rows = [header + "# Operations and wire map\n\nGenerated from explicit server registration, desktop queue calls and phone projection inventories. Display projection does not authorize a write.\n\n| Operation | Server handler | Desktop queue method | Phone behavior |\n| --- | --- | --- | --- |"]
    for name, handler in sorted(handlers.items()):
        domain, symbol = handler.split(".", 1)
        operation_rows.append(f"| `{name}` | {link('server/operations/' + domain + '.py', symbol)} | "
                              + ", ".join('`' + method + '`' for method in sorted(desktop.get(name, []))) + " | " + phone[name] + " |")
    tree = ast.parse((root / "jobtracker/core/sync_policy.py").read_text())
    api_names = next(ast.literal_eval(node.value) for node in tree.body if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id == "API_NAMES")
    operation_rows.append("\n## Legacy table to wire name\n\nUIDs travel; local integer IDs do not. Source: " + link("jobtracker/core/sync_policy.py") + ".\n\n| SQLite table | Wire name |\n| --- | --- |")
    operation_rows += [f"| `{table}` | `{wire}` |" for table, wire in api_names.items()]
    operation_rows.append("\n## Routes\n\n| Endpoint | Adapter |\n| --- | --- |")
    for endpoint, owner in sorted(routes.items()):
        path, symbol = owner.split("::")
        operation_rows.append(f"| `{endpoint}` | {link(path, symbol)} |")
    interface_rows = [header + "# Facade delegation interfaces\n\nImport paths and signatures remain stable. State and transaction ownership are documented in "
                      + "[boundaries](../architecture/boundaries.md#facades-and-state-owners). This index covers declared Python methods and explicit component calls; it is not a complete runtime call graph.\n"]
    for path, name in (("jobtracker/core/database.py", "Database"), ("jobtracker/services/tracker_service.py", "TrackerService"), ("jobtracker/ui/app.py", "MainWindow")):
        interface_rows.append(f"\n## {name}\n\nSource: {link(path)}.\n\n| Compatibility method | Extracted delegate |\n| --- | --- |")
        interface_rows += delegation_rows(root, path, name)
    return {"feature-index.md": "\n".join(feature_rows) + "\n", "operations.md": "\n".join(operation_rows) + "\n", "interfaces.md": "\n".join(interface_rows) + "\n"}


def update_generated(root: Path, documents: dict[str, str], *, write=False) -> list[str]:
    errors = []
    for name, content in documents.items():
        path = root / "docs/generated" / name
        if write:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
        elif not path.is_file() or path.read_text() != content:
            errors.append(f"generated reference drift: {path.relative_to(root)}; run check --write")
    return errors


def check(root: Path, *, write=False) -> list[str]:
    try:
        manifest = json.loads((root / "docs/architecture/features.json").read_text())
        registry = json.loads((root / "docs/architecture/contracts.json").read_text())
        handlers, routes, phone = operation_handlers(root), api_routes(root), phone_operations(root)
        errors = validate_metadata(root, manifest, registry, handlers, routes)
        for name, handler in handlers.items():
            domain, symbol = handler.split(".", 1)
            try:
                validate_ref(root, {"path": f"server/operations/{domain}.py", "symbol": symbol})
            except ValueError as exc:
                errors.append(str(exc))
        if set(phone) != set(handlers):
            errors.append(f"phone inventory differs from server: missing={sorted(set(handlers)-set(phone))}; unknown={sorted(set(phone)-set(handlers))}")
        unknown = set(desktop_operations(root)) - set(handlers)
        if unknown:
            errors.append(f"desktop queues unknown operations: {sorted(unknown)}")
        schema = "\n".join((root / path).read_text() for path in (
            "jobtracker/core/persistence/schema.py", "jobtracker/sync/state.py", "server/feed.py"))
        tables = set(re.findall(r"CREATE TABLE(?: IF NOT EXISTS)?\s+(\w+)", schema, re.I))
        for feature in manifest["features"]:
            for table in feature["tables"]:
                if table not in tables:
                    errors.append(f"{feature['id']}: unknown schema table {table}")
        if errors:
            return errors
        errors += update_generated(root, generated(root, manifest, handlers, routes, phone), write=write)
        return errors + markdown_errors(root)
    except (KeyError, ValueError, OSError, SyntaxError) as exc:
        return [str(exc)]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    errors = check(args.root, write=args.write)
    if errors:
        print("\n".join(errors))
        return 1
    print("Feature paths, symbols, contracts, operations and generated references pass.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
