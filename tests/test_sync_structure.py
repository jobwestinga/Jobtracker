"""Compatibility dispatch and shared state across extracted sync owners."""

from datetime import datetime
from types import SimpleNamespace
import uuid

from jobtracker.sync import engine as engine_module, mirror, state
from jobtracker.sync.engine import SyncEngine, SyncResult


def test_row_application_retains_engine_column_override(database, monkeypatch):
    subject = database.add_subject("Before", "#123456", "")
    engine = SyncEngine(database, SimpleNamespace())
    queried = []

    def column_override(table):
        queried.append(table)
        return ["uid", "name"]

    monkeypatch.setattr(engine, "_columns", column_override)
    with database.atomic():
        engine._upsert("subjects", subject.uid, {"name": "After", "color": "#abcdef"})
    changed = database.get_subject(subject.id)
    assert queried == ["tasks"]
    assert changed.name == "After"
    assert changed.color == "#123456"


def test_engine_and_mirror_share_one_column_cache(database):
    engine = SyncEngine(database, SimpleNamespace())
    statements = []
    database.connection.set_trace_callback(statements.append)
    try:
        first = engine._columns("tasks")
        assert mirror.columns(database, "tasks", engine._column_cache) is first
        assert engine._columns("tasks") is first
        assert engine._column_cache["tasks"] is first
        pragmas = [sql for sql in statements if sql.upper().startswith("PRAGMA TABLE_INFO")]
        assert len(pragmas) == 1
    finally:
        database.connection.set_trace_callback(None)


def test_pull_dispatches_through_engine_row_and_order_overrides(database):
    old = database.add_subject("Remove", "#123456", "")
    added_uid = str(uuid.uuid4())
    response = {
        "changes": [
            {"op": "delete", "table": "subjects", "uid": old.uid, "seq": 1},
            {"op": "upsert", "table": "subjects", "uid": added_uid, "seq": 2,
             "row": {"uid": added_uid, "name": "Added", "color": "#abcdef"}},
        ],
        "seq": 2, "head": 2, "more": False,
    }
    calls, keys = [], []

    class ObservedEngine(SyncEngine):
        def _apply_order(self, change):
            keys.append(change["uid"])
            return super()._apply_order(change)

        def _upsert(self, api_table, uid, wire_row, codec=None, heartbeat=None):
            calls.append(("upsert", uid))
            return super()._upsert(api_table, uid, wire_row, codec, heartbeat)

        def _delete(self, api_table, uid, codec=None):
            calls.append(("delete", uid))
            return super()._delete(api_table, uid, codec)

    engine = ObservedEngine(database, SimpleNamespace(pull=lambda cursor: response))
    result = SyncResult()
    assert engine.pull(result)
    assert set(keys) == {old.uid, added_uid}
    assert calls == [("upsert", added_uid), ("delete", old.uid)]
    assert database.id_for_uid("tasks", old.uid) is None
    assert database.get_subject(database.id_for_uid("tasks", added_uid)).name == "Added"
    assert (result.applied, result.deleted) == (1, 1)
    assert state.last_seq(database.connection) == 2


def test_integrity_report_retains_engine_hash_and_clock_overrides(database, monkeypatch):
    database.add_subject("Counted", "#123456", "")
    requested, hashed = [], []
    report = {"tables": {"subjects": {"count": 1, "hash": "override-digest"}}}

    def report_for(deep=False):
        requested.append(deep)
        return report

    engine = SyncEngine(database, SimpleNamespace(integrity=report_for))

    def custom_hash(table):
        hashed.append(table)
        return "override-digest"

    monkeypatch.setattr(engine, "_uid_hash", custom_hash)
    monkeypatch.setattr(engine, "_check_clock", lambda data, result: result.messages.append("clock override"))
    result = SyncResult()
    assert engine.verify(result, deep=True)
    assert requested == [True]
    assert hashed == ["tasks"]
    assert result.messages == ["clock override"]


def test_engine_clock_injection_survives_integrity_extraction(monkeypatch):
    class Clock:
        @staticmethod
        def now():
            return datetime(2026, 10, 11, 12, 10)

    monkeypatch.setattr(engine_module, "datetime", Clock)
    result = SyncResult()
    SyncEngine._check_clock({"server_time": "2026-10-11T12:00:00"}, result)
    assert result.messages == [
        "server clock is 10 min from this machine's; sessions will be recorded at the wrong time",
    ]
