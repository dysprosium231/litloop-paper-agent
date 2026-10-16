"""The desktop app's local backend (litloop.app_server)."""

import pytest
from fastapi.testclient import TestClient

from litloop import app_server, screening, store

TOKEN = "t0ken"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(screening._config, "DATA_DIR", tmp_path)
    c = TestClient(app_server.create_app(TOKEN))
    c.headers["X-LitLoop-Token"] = TOKEN
    return c


@pytest.fixture
def session(client):
    return screening.save("q", [{"doi": "10.1/a", "title": "A", "relevance": "低", "reason": "r"},
                                {"doi": "10.1/b", "title": "B", "relevance": "高", "reason": "r"}])["session"]


def test_every_request_needs_the_token(client):
    assert TestClient(app_server.create_app(TOKEN)).get("/api/health").status_code == 401
    assert client.get("/api/health").json()["schema"] == store.SCHEMA


def test_sessions_and_one_session(client, session):
    assert [s["session"] for s in client.get("/api/sessions").json()] == [session]
    got = client.get(f"/api/sessions/{session}").json()
    assert len(got["record"]["items"]) == 2 and got["summary"] is None
    assert client.get("/api/sessions/no-such").status_code == 404
    assert client.get("/api/sessions/..%2Fconfig").status_code in (400, 404)


def test_the_users_rating_is_saved_and_can_be_cleared(client, session):
    r = client.post(f"/api/sessions/{session}/rating", json={"doi": "10.1/a", "relevance": "高"}).json()
    assert r["item"]["user_relevance"] == "高" and r["edited"] == 1
    item = next(it for it in screening.load(session)["items"] if it["doi"] == "10.1/a")
    assert screening._rating(item) == "高"
    # back to the agent's own rating: nothing is overridden any more
    r = client.post(f"/api/sessions/{session}/rating", json={"doi": "10.1/a", "relevance": "低"}).json()
    assert "user_relevance" not in r["item"] and r["edited"] == 0
    assert client.post(f"/api/sessions/{session}/rating", json={"doi": "10.1/a", "relevance": "x"}).status_code == 400


def test_decision(client, session):
    r = client.post(f"/api/sessions/{session}/decision", json={"doi": "10.1/b", "decision": "selected"}).json()
    assert r["item"]["decision"] == "selected"
    assert client.post(f"/api/sessions/{session}/decision", json={"doi": "10.1/b", "decision": "maybe"}).status_code == 400


def test_progress_is_none_when_nothing_runs(client, session):
    assert client.get(f"/api/sessions/{session}/progress").json() is None
