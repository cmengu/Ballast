"""Tests for ballast/core/server.py and ballast/core/sync.py.

Server tests use FastAPI TestClient — no live server needed.
SpecPoller tests mock httpx.get — no network needed.
"""
from unittest.mock import MagicMock, patch

import httpx
import pytest
from fastapi.testclient import TestClient

from ballast.core.server import _current_spec, app
from ballast.core.spec import SpecModel, lock
from ballast.core.sync import SpecPoller

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

client = TestClient(app)


def _make_spec(intent: str = "do something") -> SpecModel:
    return lock(SpecModel(intent=intent, success_criteria=["it is done"]))


# ---------------------------------------------------------------------------
# Fixture — reset server state between tests
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def clear_server_state():
    _current_spec.clear()
    yield
    _current_spec.clear()


# ---------------------------------------------------------------------------
# Server endpoint tests (4 tests)
# ---------------------------------------------------------------------------

def test_get_unknown_job_returns_empty_dict():
    r = client.get("/spec/unknown-job/current")
    assert r.status_code == 200
    assert r.json() == {}


def test_post_stores_spec_and_returns_version():
    spec = _make_spec()
    r = client.post("/spec/job-001/update", json=spec.model_dump())
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["version_hash"] == spec.version_hash


def test_get_returns_stored_spec():
    spec = _make_spec()
    client.post("/spec/job-001/update", json=spec.model_dump())
    r = client.get("/spec/job-001/current")
    assert r.status_code == 200
    data = r.json()
    assert data["version_hash"] == spec.version_hash
    assert data["intent"] == "do something"


def test_post_overwrites_existing_spec():
    spec_v1 = _make_spec("do x")
    spec_v2 = _make_spec("do y")
    client.post("/spec/job-001/update", json=spec_v1.model_dump())
    client.post("/spec/job-001/update", json=spec_v2.model_dump())
    r = client.get("/spec/job-001/current")
    assert r.json()["version_hash"] == spec_v2.version_hash


# ---------------------------------------------------------------------------
# SpecPoller isolation tests (5 tests, httpx.get mocked)
# ---------------------------------------------------------------------------

def test_poller_returns_none_before_set_initial():
    poller = SpecPoller("http://localhost:8765", "job-001")
    assert poller.poll() is None


def test_poller_returns_none_when_version_unchanged():
    spec = _make_spec()
    poller = SpecPoller("http://localhost:8765", "job-001")
    poller.set_initial(spec)
    mock_r = MagicMock()
    mock_r.status_code = 200
    mock_r.json.return_value = spec.model_dump()
    with patch("ballast.core.sync.httpx.get", return_value=mock_r):
        assert poller.poll() is None


def test_poller_returns_new_spec_when_version_changed():
    spec_v1 = _make_spec("do x")
    spec_v2 = _make_spec("do y")
    poller = SpecPoller("http://localhost:8765", "job-001")
    poller.set_initial(spec_v1)
    mock_r = MagicMock()
    mock_r.status_code = 200
    mock_r.json.return_value = spec_v2.model_dump()
    with patch("ballast.core.sync.httpx.get", return_value=mock_r):
        result = poller.poll()
    assert result is not None
    assert result.version_hash == spec_v2.version_hash
    assert result.intent == "do y"


def test_poller_returns_none_on_network_error():
    spec = _make_spec()
    poller = SpecPoller("http://localhost:8765", "job-001")
    poller.set_initial(spec)
    with patch(
        "ballast.core.sync.httpx.get",
        side_effect=httpx.ConnectError("unreachable"),
    ):
        assert poller.poll() is None


def test_poller_returns_none_on_non_200_status():
    spec = _make_spec()
    poller = SpecPoller("http://localhost:8765", "job-001")
    poller.set_initial(spec)
    mock_r = MagicMock()
    mock_r.status_code = 500
    with patch("ballast.core.sync.httpx.get", return_value=mock_r):
        assert poller.poll() is None
