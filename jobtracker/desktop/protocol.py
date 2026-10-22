"""Versioned newline-delimited JSON frames between the native app and engine.

One JSON object per line. Protocol output uses the engine's private stdout
descriptor; diagnostics go to stderr. This is a local process boundary, not a
server wire format: ``/ops`` and the outbox JSON are unchanged.

Message kinds
    hello     engine → client once at start: version, instance, store, commands
    command   client → engine: request_id, command, params
    response  engine → client: request_id, outcome, result/error, topics, revision
    event     engine → client: committed topic changes, timer/sync status
    fatal     engine → client before exiting on a startup failure
"""

from __future__ import annotations

import json
import math
import re
import uuid

PROTOCOL_VERSION = 1
MAX_FRAME_BYTES = 1 << 20
MAX_OUTSTANDING = 64

KIND_HELLO = "hello"
KIND_COMMAND = "command"
KIND_RESPONSE = "response"
KIND_EVENT = "event"
KIND_FATAL = "fatal"

# Outcomes keep domain meaning separate from transport and storage failures.
APPLIED = "applied"          # a database mutation committed with its receipt
REPLAYED = "replayed"        # the same request already committed; saved result
OK = "ok"                    # a read or non-database action finished
MISSING = "missing"          # the target no longer exists
REFUSED = "refused"          # the existing service declined (domain rule)
INVALID = "invalid"          # malformed input or unknown command
CONFLICT = "conflict"        # request_id reused for different parameters/store
STORAGE_ERROR = "storage_error"
UNAVAILABLE = "unavailable"  # engine is stopping or incompatible
OUTCOMES = (APPLIED, REPLAYED, OK, MISSING, REFUSED, INVALID, CONFLICT, STORAGE_ERROR, UNAVAILABLE)

_REQUEST_ID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")


class FrameError(ValueError):
    """A frame that cannot be parsed or violates the envelope."""


def new_instance_id() -> str:
    return str(uuid.uuid4())


def encode(message: dict) -> bytes:
    """Serialize one frame. JSON escaping guarantees no embedded newline."""
    data = json.dumps(message, separators=(",", ":"), ensure_ascii=False, allow_nan=False, default=str)
    raw = data.encode("utf-8") + b"\n"
    if len(raw) > MAX_FRAME_BYTES:
        raise FrameError("response exceeds the frame limit; paginate this read")
    return raw


def decode(line: bytes) -> dict:
    if len(line) > MAX_FRAME_BYTES:
        raise FrameError("frame exceeds the size limit")
    try:
        message = json.loads(line.decode("utf-8"), parse_constant=_reject_constant)
    except (UnicodeDecodeError, ValueError) as exc:
        raise FrameError(f"frame is not valid JSON: {exc}") from None
    if not isinstance(message, dict):
        raise FrameError("frame must be a JSON object")
    return message


def _reject_constant(name):
    raise ValueError(f"{name} is not valid JSON")


def validate_command(message: dict) -> tuple[str, str, dict]:
    """Return ``(request_id, command, params)`` or raise ``FrameError``."""
    if message.get("kind") != KIND_COMMAND:
        raise FrameError("expected a command frame")
    if message.get("protocol_version") != PROTOCOL_VERSION:
        raise FrameError(f"protocol version {message.get('protocol_version')!r} is not supported")
    request_id = message.get("request_id")
    if not isinstance(request_id, str) or not _REQUEST_ID.match(request_id):
        raise FrameError("request_id must be a UUID string")
    command = message.get("command")
    if not isinstance(command, str) or not command:
        raise FrameError("command must be a non-empty string")
    params = message.get("params", {})
    if params is None:
        params = {}
    if not isinstance(params, dict):
        raise FrameError("params must be an object")
    _reject_non_finite(params)
    return request_id.lower(), command, params


def _reject_non_finite(value) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise FrameError("numbers must be finite")
    if isinstance(value, dict):
        for item in value.values():
            _reject_non_finite(item)
    elif isinstance(value, list):
        for item in value:
            _reject_non_finite(item)


class LineReader:
    """Split a byte stream into bounded lines, tolerating partial chunks.

    An oversized line is discarded up to its newline and reported once, so a
    single bad frame cannot desynchronize every frame that follows it.
    """

    def __init__(self, limit: int = MAX_FRAME_BYTES):
        self._limit = limit
        self._buffer = bytearray()
        self._discarding = False

    def feed(self, chunk: bytes) -> list[bytes | FrameError]:
        out: list[bytes | FrameError] = []
        self._buffer.extend(chunk)
        while True:
            index = self._buffer.find(b"\n")
            if index < 0:
                if len(self._buffer) > self._limit:
                    if not self._discarding:
                        out.append(FrameError("frame exceeds the size limit"))
                    self._discarding = True
                    self._buffer.clear()
                return out
            line = bytes(self._buffer[:index])
            del self._buffer[: index + 1]
            if self._discarding:
                self._discarding = False
                continue
            if len(line) > self._limit:
                out.append(FrameError("frame exceeds the size limit"))
                continue
            if line.strip():
                out.append(line)
