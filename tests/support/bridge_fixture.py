"""Regenerate the shared native bridge fixtures after changing the allowlist
or a result shape. Swift decodes the samples; Python checks they still match
live engine output.

    JOBTRACKER_DB_PATH=/tmp/x.db python -m tests.support.bridge_fixture
"""
from __future__ import annotations

import json
import tempfile
from datetime import datetime
from pathlib import Path

from jobtracker.desktop import commands, protocol

FIXTURES = Path(__file__).resolve().parents[1] / "contracts" / "desktop_bridge"
FIXTURE = FIXTURES / "commands.json"
SAMPLES = FIXTURES / "samples.json"

# (command, params builder) run against the synthetic store, in order.
SAMPLE_READS = ("engine.info", "settings.get", "subjects.list", "timer.get", "goals.list", "templates.list",
                "graphs.breakdown", "graphs.agenda", "weight.view", "sync.status")


def render() -> str:
    data = {
        "protocol_version": protocol.PROTOCOL_VERSION,
        "outcomes": list(protocol.OUTCOMES),
        "commands": {item["name"]: {"kind": item["kind"], "topics": item["topics"]} for item in commands.describe()},
    }
    return json.dumps(data, indent=2) + "\n"


def live_samples() -> dict:
    """Responses from a real engine host over the synthetic store."""
    from jobtracker.core.database import Database
    from tests.support import synthetic_store
    from tests.support.desktop import InProcessEngine

    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "store.db"
        synthetic_store.build(path, now=datetime.now().replace(microsecond=0), foreign_timer=True)
        database = Database(path)
        try:
            engine = InProcessEngine(database, Path(directory), recover=False)
            samples = {name: engine.call(name) for name in SAMPLE_READS}
            subject_uid = samples["subjects.list"]["result"]["subjects"][1]["uid"]
            samples["sessions.for_subject"] = engine.call("sessions.for_subject", {"subject_uid": subject_uid, "limit": 3})
            day = samples["graphs.agenda"]["result"]["days"][-4]
            samples["sessions.for_day"] = engine.call("sessions.for_day", {"day": day})
            samples["session.missing"] = engine.call("session.shift", {"session_uid": "00000000-0000-4000-8000-000000000000", "seconds": 900})
            samples["hello"] = {**engine.host.hello(), "protocol_version": protocol.PROTOCOL_VERSION, "engine_instance": engine.host.instance_id}
            for frame in (samples["hello"], samples["engine.info"]["result"]):
                frame["db_path"] = "/synthetic/store.db"
                frame["pid"] = 0
            return samples
        finally:
            database.connection.close()


if __name__ == "__main__":
    FIXTURE.write_text(render())
    SAMPLES.write_text(json.dumps(live_samples(), indent=1, sort_keys=True, default=str) + "\n")
    print(FIXTURE, SAMPLES)
