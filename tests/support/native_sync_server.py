"""Disposable loopback API for the Swift native integration checks.

Copies a synthetic seed with SQLite's online backup API. The directory must
be new; the helper never opens the production configuration or token store.
"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import socket
import sqlite3
import sys


def main() -> None:
    directory, seed = map(lambda arg: Path(arg).resolve(), sys.argv[1:])
    directory.mkdir(mode=0o700)
    target = directory / "server.db"
    with sqlite3.connect(seed.as_uri() + "?mode=ro", uri=True) as source:
        with sqlite3.connect(target) as destination:
            source.backup(destination)
    os.environ["JOBTRACKER_DB_PATH"] = str(target)
    os.environ["JOBTRACKER_TOKENS_PATH"] = str(directory / "tokens.json")

    import uvicorn
    from server import auth
    from server.app import app

    tokens = {name: auth.issue_token(name) for name in ("native-check", "phone-check")}
    server = uvicorn.Server(uvicorn.Config(app, log_level="error", access_log=False))

    async def run(listener: socket.socket) -> None:
        task = asyncio.create_task(server.serve(sockets=[listener]))
        while not server.started:
            if task.done():
                await task
                raise RuntimeError("Test API did not start")
            await asyncio.sleep(0.01)
        manifest = directory / "ready.json"
        manifest.write_text(json.dumps({
            "url": f"http://127.0.0.1:{listener.getsockname()[1]}", **tokens,
        }))
        manifest.chmod(0o600)
        await task

    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        asyncio.run(run(listener))


if __name__ == "__main__":
    main()
