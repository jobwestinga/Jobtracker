"""Verify every local web dependency is present in the offline shell inventory."""
from __future__ import annotations

import argparse
import re
from pathlib import Path

try:
    from .source_index import ROOT, js_links
except ImportError:
    from source_index import ROOT, js_links


def check(root: Path) -> list[str]:
    web = root / "web"
    source = (web / "sw.js").read_text()
    inventory_match = re.search(r"const SHELL\s*=\s*\[([\s\S]*?)\];", source)
    if not inventory_match:
        return ["sw.js: explicit SHELL inventory missing"]
    inventory = re.findall(r'["\']([^"\']+)["\']', inventory_match.group(1))
    errors = []
    if len(inventory) != len(set(inventory)):
        errors.append("sw.js: duplicate shell entry")
    shell = {(item + "index.html" if item.endswith("/") else item).lstrip("/") for item in inventory}
    needed = {"index.html", "manifest.webmanifest", "icon.png"}
    for name in shell:
        if not (web / name).is_file():
            errors.append(f"sw.js: missing cached asset {name}")
    for path in sorted(web.rglob("*")):
        if path.suffix not in {".js", ".css", ".html"} or path.name == "sw.js":
            continue
        text = path.read_text()
        dependencies = []
        if path.suffix == ".js":
            dependencies += js_links(text)
        elif path.suffix == ".css":
            text = re.sub(r"/\*[\s\S]*?\*/", "", text)
            dependencies += re.findall(r'@import\s+(?:url\()?\s*["\']([^"\']+)', text)
            dependencies += re.findall(r'url\(\s*["\']?([^\)"\']+)', text)
        else:
            dependencies += re.findall(r'(?:src|href)=["\']([^"\']+)', text)
        for dependency in dependencies:
            dependency = dependency.split("?", 1)[0].split("#", 1)[0]
            if not dependency or re.match(r"(?:[a-z]+:|//)", dependency):
                continue
            target = (web / dependency.lstrip("/") if dependency.startswith("/") else path.parent / dependency).resolve()
            if target.is_dir():
                target /= "index.html"
            if not target.is_relative_to(web.resolve()):
                errors.append(f"{path.relative_to(root)}: dependency escapes web: {dependency}")
                continue
            name = target.relative_to(web.resolve()).as_posix()
            if not target.is_file():
                errors.append(f"{path.relative_to(root)}: missing dependency {dependency}")
            needed.add(name)
    for name in sorted(needed - shell):
        errors.append(f"sw.js: uncached dependency {name}")
    return errors


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args()
    errors = check(args.root)
    if errors:
        print("\n".join(errors))
        return 1
    print("Web dependencies and offline shell inventory agree.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
