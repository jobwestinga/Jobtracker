"""Server execution of the same acknowledgement contracts consumed by Node."""
import json
from pathlib import Path

import pytest

from tests.support.api import api  # noqa: F401
from server import app as app_module

CASES = json.loads((Path(__file__).parent / "contracts/operation_outcomes.json").read_text())["cases"]


def assert_subset(actual, expected):
    if isinstance(expected, dict):
        for key, value in expected.items():
            assert key in actual
            assert_subset(actual[key], value)
    elif isinstance(expected, list):
        assert len(actual) == len(expected)
        for left, right in zip(actual, expected):
            assert_subset(left, right)
    else:
        assert actual == expected


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["id"])
def test_public_operation_outcome_contract(api, case):
    for setup in case["setup"]:
        response = api.post("/ops", json={"ops": [setup]})
        assert response.status_code == 200, response.text
    for request, expected in zip(case["requests"], case["responses"]):
        response = api.post("/ops", json=request)
        assert response.status_code == expected["status"], response.text
        assert_subset(response.json(), expected["body"])
    database = app_module._state["db"]
    if case["id"] == "lost-ack-replay":
        assert database.connection.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 1
    if case["id"] == "daily-weight-canonical-identity":
        assert database.connection.execute("SELECT COUNT(*) FROM weight_measurements").fetchone()[0] == 1
    if case["id"] == "ordered-partial-failure":
        assert database.connection.execute("SELECT COUNT(*) FROM weight_measurements").fetchone()[0] == 1
        assert database.connection.execute("SELECT COUNT(*) FROM applied_ops").fetchone()[0] == 1
