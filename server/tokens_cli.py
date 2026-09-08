"""Issue and revoke the per-device API tokens.

    python -m server.tokens_cli list
    python -m server.tokens_cli add macbook
    python -m server.tokens_cli revoke macbook

The plaintext token is printed **once**, at issue time, and only its SHA-256 is
stored. If it is lost, issue a new one for that device rather than trying to
recover the old one.
"""

from __future__ import annotations

import argparse

from . import auth


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list")
    add = sub.add_parser("add")
    add.add_argument("name", help="device name, e.g. macbook or iphone")
    revoke = sub.add_parser("revoke")
    revoke.add_argument("name")
    args = parser.parse_args(argv)

    if args.command == "list":
        tokens = auth.load_tokens()
        if not tokens:
            print(f"No tokens yet ({auth.token_store_path()})")
        for entry in tokens:
            state = "revoked" if entry.get("revoked") else "active"
            print(f"{entry.get('name'):20} {state}")
        return 0

    if args.command == "add":
        token = auth.issue_token(args.name)
        print(f"Token for {args.name} (shown once, store it now):\n\n  {token}\n")
        print(f"Stored hashed in {auth.token_store_path()}")
        return 0

    if auth.revoke_token(args.name):
        print(f"Revoked {args.name}")
        return 0
    print(f"No token named {args.name}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
