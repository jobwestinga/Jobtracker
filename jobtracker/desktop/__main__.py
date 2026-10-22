"""Engine entry point: ``python -m jobtracker.desktop --mode MODE [--db PATH]``.

Protocol frames use a private duplicate of the original stdout descriptor;
descriptor 1 is then pointed at stderr, so a stray ``print`` from any imported
module becomes a diagnostic instead of corrupting the protocol stream.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import sys
import threading


def _protocol_writer():
    protocol_fd = os.dup(1)
    os.dup2(2, 1)
    stream = os.fdopen(protocol_fd, "wb", buffering=0)

    def write(frame: bytes) -> None:
        view = memoryview(frame)
        while view:
            written = stream.write(view)
            view = view[written:]

    return write


def _fatal(write, reason: str, message: str, code: int) -> int:
    frame = {"protocol_version": 1, "kind": "fatal", "reason": reason, "message": message}
    write((json.dumps(frame) + "\n").encode("utf-8"))
    return code


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="jobtracker-engine", description=__doc__)
    parser.add_argument("--mode", required=True, choices=("production", "development", "isolated"))
    parser.add_argument("--db", help="Store path; required for and only valid in isolated mode")
    args = parser.parse_args(argv)
    write = _protocol_writer()

    from .bootstrap import apply_environment, resolve_store
    from .ownership import StoreInUse, StoreLock

    try:
        selection = resolve_store(args.mode, args.db)
    except ValueError as exc:
        return _fatal(write, "invalid_store", str(exc), 2)
    lock = StoreLock(selection.db_path, role="native-engine")
    try:
        lock.acquire()
    except StoreInUse:
        return _fatal(write, "store_in_use", "JobTracker is already open with this data.", 3)
    try:
        apply_environment(selection)
        from ..core.logging_config import setup_logging

        setup_logging()
        from .host import EngineHost

        host = EngineHost.for_selected_store(selection, write=write)
    except Exception as exc:  # noqa: BLE001 - report startup failure to the UI
        lock.release()
        return _fatal(write, "startup_failed", f"The engine could not open its data: {exc}", 4)

    # A terminate request behaves like losing the parent. Enqueue from another
    # thread so the handler never re-enters the inbox lock it interrupted.
    signal.signal(signal.SIGTERM, lambda *_: threading.Thread(target=host.request_stop, daemon=True).start())
    code = 1
    try:
        code = host.run(sys.stdin.buffer)
    finally:
        try:
            host.service.db.connection.close()
        finally:
            lock.release()
    return code


if __name__ == "__main__":
    exit_code = main()
    # The stdin reader may still be blocked when the parent sent SIGTERM;
    # leave without interpreter teardown racing that daemon thread.
    logging.shutdown()
    sys.stderr.flush()
    os._exit(exit_code)
