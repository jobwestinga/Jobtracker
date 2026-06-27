"""
Shared pytest fixtures.

CRITICAL: this module redirects the database away from real user data BEFORE any
``jobtracker`` module is imported. Importing ``jobtracker.core.database`` creates
a module-level ``Database()`` singleton; without this redirect that singleton
would open (and run a startup cleanup DELETE against) the developer's real
``data/jobtracker.db``. Setting JOBTRACKER_DB_PATH here points the singleton at a
throwaway temp file instead.

Individual tests still get their own isolated ``Database`` via the ``database``
fixture, so they never share state with each other or with the singleton.
"""

import os
import pathlib
import tempfile

# Must happen before importing anything from jobtracker.
_TEST_DB_DIR = tempfile.mkdtemp(prefix="jobtracker-tests-")
os.environ["JOBTRACKER_DB_PATH"] = str(pathlib.Path(_TEST_DB_DIR) / "singleton.db")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

from jobtracker.core.database import Database  # noqa: E402
from jobtracker.services.tracker_service import TrackerService  # noqa: E402


@pytest.fixture
def database(tmp_path):
    """A fresh, isolated SQLite database on a temp path (auto-closed)."""
    db = Database(tmp_path / "jobtracker_test.db")
    try:
        yield db
    finally:
        db.connection.close()


@pytest.fixture
def service(database):
    """A TrackerService bound to the isolated test database."""
    return TrackerService(database)


@pytest.fixture
def subject(service):
    """A single saved subject to attach sessions to."""
    return service.add_subject("Physics", "#3B82F6", "")
