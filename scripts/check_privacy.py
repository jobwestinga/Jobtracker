"""Reject private files and recognizable secrets before they reach Git.

No application imports, network calls, credential reads, or raw matching values
in reports. This is a conservative repository guard, not a complete secret scan.
"""
from __future__ import annotations

import argparse
import ipaddress
import json
from pathlib import Path
import re
import struct
import subprocess

try:
    from .source_index import ROOT
except ImportError:
    from source_index import ROOT


PRIVATE_DIRS = {".local", "private", "secrets", "backups", "exports", "MOD", ".codex", ".claude"}
PRIVATE_SUFFIXES = {".csv", ".tsv", ".xls", ".xlsx", ".db", ".sqlite", ".sqlite3", ".log", ".pem", ".key", ".crt"}
TEXT_SUFFIXES = {".md", ".py", ".js", ".mjs", ".cjs", ".html", ".css", ".json", ".yml", ".yaml", ".sh", ".ini", ".txt", ".example", ".template", ".webmanifest"}
PATTERNS = {
    "private key": re.compile(r"-----BEGIN (?:[A-Z]+ )?PRIVATE KEY-----"),
    "GitHub token": re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,})\b"),
    "API secret": re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{32,}\b"),
    "AWS access key": re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
    "literal bearer credential": re.compile(r"(?i)\bBearer\s+[A-Za-z0-9_-]{24,}"),
    "personal home path": re.compile(r"(?:/Users/|/home/)(?!youruser(?:/|\b)|username(?:/|\b)|example(?:/|\b))[A-Za-z][A-Za-z0-9_.-]+/"),
    "private tailnet hostname": re.compile(r"\b(?!yourserver\.)[a-z0-9-]+\.[a-z0-9-]+\.ts\.net\b", re.I),
}
IPV4 = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
PRIVATE_NETWORKS = tuple(ipaddress.ip_network(value) for value in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "100.64.0.0/10"))


def private_path(name: str) -> bool:
    path = Path(name)
    lower = path.name.lower()
    return (any(part in PRIVATE_DIRS for part in path.parts)
            or path.parts[0] == "data"
            or path.suffix.lower() in PRIVATE_SUFFIXES
            or bool(re.search(r"\.(?:db|sqlite|sqlite3)-", lower))
            or lower in {"sync_token", "tokens.json", "server.env", "jobtrackerweights.json"}
            or lower.startswith("jobtracker_backup")
            or (lower.startswith(".env") and not lower.endswith(".example")))


def inspect(name: str, content: bytes) -> list[str]:
    if private_path(name):
        return [f"{name}: private data/configuration file must stay untracked"]
    if Path(name).suffix.lower() == ".png" and content.startswith(b"\x89PNG\r\n\x1a\n"):
        offset = 8
        while offset + 12 <= len(content):
            length = struct.unpack(">I", content[offset:offset + 4])[0]
            kind = content[offset + 4:offset + 8]
            if kind in {b"eXIf", b"tEXt", b"iTXt", b"zTXt"}:
                return [f"{name}: remove embedded image metadata before publishing"]
            offset += length + 12
    if Path(name).suffix not in TEXT_SUFFIXES:
        return []
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        return []
    errors = []
    for number, line in enumerate(text.splitlines(), 1):
        for label, pattern in PATTERNS.items():
            if pattern.search(line):
                errors.append(f"{name}:{number}: possible {label}; matching value withheld")
        for match in IPV4.finditer(line):
            try:
                address = ipaddress.ip_address(match.group())
            except ValueError:
                continue
            # Public definitions of entire reserved ranges are useful in this
            # checker and networking documentation; host addresses are not.
            if any(line[match.start():].startswith(str(network)) for network in PRIVATE_NETWORKS):
                continue
            if any(address in network for network in PRIVATE_NETWORKS):
                errors.append(f"{name}:{number}: private network address; matching value withheld")
                break
    # Serialized measurement history is never a public asset. Synthetic test
    # fixtures live under tests/ and must be documented as such.
    if Path(name).suffix == ".json" and not name.startswith("tests/"):
        try:
            payload = json.loads(text)
        except ValueError:
            payload = None

        def has_reading(value):
            if isinstance(value, dict):
                return {"measured_at", "weight_kg"} <= value.keys() or any(has_reading(item) for item in value.values())
            return isinstance(value, list) and any(has_reading(item) for item in value)

        if has_reading(payload):
            errors.append(f"{name}: serialized weight history must stay untracked")
    return errors


def check(root: Path = ROOT, *, staged=False) -> list[str]:
    command = (["git", "diff", "--cached", "--name-only", "--diff-filter=ACMR", "-z"] if staged else
               ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"])
    paths = subprocess.check_output(command, cwd=root).decode().strip("\0").split("\0")
    errors = []
    for name in sorted(set(paths) - {""}):
        path = root / name
        if staged:
            content = subprocess.check_output(["git", "show", ":" + name], cwd=root)
        elif path.is_file():
            content = path.read_bytes()
        else:
            continue
        errors.extend(inspect(name, content))
    return errors


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--staged", action="store_true", help="Inspect exact staged blobs, including force-added ignored files.")
    args = parser.parse_args(argv)
    errors = check(staged=args.staged)
    print("\n".join(errors) if errors else "Repository privacy guard passes.")
    return int(bool(errors))


if __name__ == "__main__":
    raise SystemExit(main())
