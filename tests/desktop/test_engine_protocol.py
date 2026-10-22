"""Framing, envelope validation and the shared bridge fixtures."""
import json
from pathlib import Path

import pytest

from jobtracker.desktop import commands, protocol

FIXTURES = Path(__file__).resolve().parents[1] / "contracts" / "desktop_bridge"


def test_line_reader_handles_partial_multiple_and_blank_frames():
    reader = protocol.LineReader()
    assert reader.feed(b'{"a":') == []
    assert reader.feed(b'1}\n\n{"b":2}\n{"c"') == [b'{"a":1}', b'{"b":2}']
    assert reader.feed(b':3}\n') == [b'{"c":3}']


def test_oversized_frame_is_reported_once_and_does_not_desynchronize():
    reader = protocol.LineReader(limit=16)
    out = reader.feed(b"x" * 40)
    assert len(out) == 1 and isinstance(out[0], protocol.FrameError)
    assert reader.feed(b"y" * 10) == []
    assert reader.feed(b'tail\n{"ok":1}\n') == [b'{"ok":1}']


def test_encode_escapes_newlines_and_bounds_size():
    raw = protocol.encode({"text": "two\nlines", "unicode": "ë"})
    assert raw.count(b"\n") == 1 and raw.endswith(b"\n")
    assert json.loads(raw)["text"] == "two\nlines"
    with pytest.raises(protocol.FrameError, match="paginate"):
        protocol.encode({"big": "x" * protocol.MAX_FRAME_BYTES})
    with pytest.raises(ValueError):
        protocol.encode({"nan": float("nan")})


@pytest.mark.parametrize("message, problem", [
    ({"kind": "response"}, "expected a command"),
    ({"kind": "command", "protocol_version": 2}, "not supported"),
    ({"kind": "command", "protocol_version": 1, "request_id": "1"}, "UUID"),
    ({"kind": "command", "protocol_version": 1, "request_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"}, "command must"),
    ({"kind": "command", "protocol_version": 1, "request_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", "command": "x", "params": []}, "params"),
])
def test_command_envelope_is_validated(message, problem):
    with pytest.raises(protocol.FrameError, match=problem):
        protocol.validate_command(message)


def test_decode_rejects_non_objects_and_non_json_constants():
    for raw in (b"[]", b"nope", b'{"x": NaN}'):
        with pytest.raises(protocol.FrameError):
            protocol.decode(raw)


def test_allowlist_has_no_generic_or_unsafe_commands():
    names = set(commands.REGISTRY)
    assert not any(word in name for name in names for word in ("sql", "eval", "exec", "getattr", "shell"))
    kinds = {cmd.kind for cmd in commands.REGISTRY.values()}
    assert kinds == {commands.READ, commands.MUTATION, commands.ACTION}
    # Credentials and file writes are never receipt-backed mutations.
    assert commands.REGISTRY["sync.configure"].kind == commands.ACTION
    assert commands.REGISTRY["backup.export"].kind == commands.ACTION
    assert commands.REGISTRY["backup.import"].kind == commands.MUTATION


def test_shared_fixtures_cover_every_command_name():
    fixture = json.loads((FIXTURES / "commands.json").read_text())
    assert fixture["protocol_version"] == protocol.PROTOCOL_VERSION
    assert sorted(fixture["commands"]) == sorted(commands.REGISTRY)
    for name, spec in fixture["commands"].items():
        assert spec["kind"] == commands.REGISTRY[name].kind, name
        assert spec["topics"] == list(commands.REGISTRY[name].topics), name
    assert set(fixture["outcomes"]) == set(protocol.OUTCOMES)


def _keys(value, prefix=""):
    if isinstance(value, dict):
        out = set()
        for key, item in value.items():
            out.add(prefix + key)
            out |= _keys(item, prefix + key + ".")
        return out
    if isinstance(value, list) and value:
        return _keys(value[0], prefix + "[].")
    return set()


def test_shared_samples_match_live_engine_shapes():
    from tests.support.bridge_fixture import SAMPLES, live_samples

    stored = json.loads(SAMPLES.read_text())
    live = live_samples()
    assert set(stored) == set(live)
    for name in stored:
        # Same synthetic data, so the field sets must agree exactly.
        assert _keys(live[name]) == _keys(stored[name]), f"{name}: run python -m tests.support.bridge_fixture"
