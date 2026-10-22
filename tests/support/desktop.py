"""In-process and subprocess harnesses for the native engine host."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import uuid
from pathlib import Path

from jobtracker.desktop import commands, protocol, receipts
from jobtracker.desktop.host import EngineHost, build_service

ROOT = Path(__file__).resolve().parents[2]


def request_id() -> str:
    return str(uuid.uuid4())


class InProcessEngine:
    """An EngineHost on an isolated database whose frames are collected."""

    def __init__(self, database, tmp_path, *, sync_factory=None, recover=True, clock=None):
        receipts.install(database.connection)
        self.frames: list[dict] = []
        service, sync_on = build_service(database, recover=recover)
        kwargs = {"clock": clock} if clock else {}
        self.host = EngineHost(
            service, sync_on=sync_on, mode="isolated", backups_dir=tmp_path / "backups",
            write=lambda raw: self.frames.append(json.loads(raw)), sync_factory=sync_factory, **kwargs,
        )

    @property
    def service(self):
        return self.host.service

    def call(self, name: str, params: dict | None = None, *, rid: str | None = None) -> dict:
        self.frames.clear()
        self.host.execute(rid or request_id(), commands.REGISTRY[name], params or {})
        return next(frame for frame in self.frames if frame["kind"] == protocol.KIND_RESPONSE)

    def ok(self, name: str, params: dict | None = None, **kwargs) -> dict:
        response = self.call(name, params, **kwargs)
        assert response["outcome"] in (protocol.OK, protocol.APPLIED), response
        return response["result"]


def command_frame(name: str, params: dict | None = None, rid: str | None = None) -> bytes:
    return (json.dumps({
        "protocol_version": protocol.PROTOCOL_VERSION, "kind": "command",
        "request_id": rid or request_id(), "command": name, "params": params or {},
    }) + "\n").encode()


class EngineProcess:
    """``python -m jobtracker.desktop`` over real pipes, for lifecycle tests."""

    def __init__(self, db_path: Path, lock_dir: Path, python: str = sys.executable):
        env = dict(os.environ)
        env.pop("JOBTRACKER_DB_PATH", None)
        env["JOBTRACKER_LOCK_DIR"] = str(lock_dir)
        self.process = subprocess.Popen(
            [python, "-m", "jobtracker.desktop", "--mode", "isolated", "--db", str(db_path)],
            cwd=ROOT, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        self.first = self.read()

    def read(self) -> dict:
        line = self.process.stdout.readline()
        assert line, self.process.stderr.read().decode(errors="replace")
        return json.loads(line)

    def send(self, name: str, params: dict | None = None, rid: str | None = None) -> dict:
        rid = rid or request_id()
        self.process.stdin.write(command_frame(name, params, rid))
        self.process.stdin.flush()
        while True:
            frame = self.read()
            if frame.get("kind") == "response" and frame.get("request_id") == rid:
                return frame

    def close(self, timeout: float = 20) -> int:
        if self.process.stdin and not self.process.stdin.closed:
            self.process.stdin.close()
        code = self.process.wait(timeout=timeout)
        self.process.stdout.close()
        self.process.stderr.close()
        return code

    def kill(self) -> None:
        self.process.kill()
        self.process.wait(timeout=10)
        self.process.stdout.close()
        self.process.stderr.close()
        self.process.stdin.close()
