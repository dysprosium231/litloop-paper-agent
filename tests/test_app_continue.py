"""继续检索 in the app: the user rates a batch the agent has not seen yet, the app serves the next one."""

import time

import pytest
from fastapi.testclient import TestClient

from litloop import store
from litloop.desktop import server as app_server
from litloop.search import deep as deep_search, screening

TOKEN = "t0ken"
PAPER = {"doi": "10.1/new", "title": "New", "year": 2024, "journal": "J", "abstract": "A."}


@pytest.fixture
def session(tmp_path, monkeypatch):
    monkeypatch.setattr(screening._config, "DATA_DIR", tmp_path)
    sid = screening.save("q", [{"doi": "10.1/old", "title": "Old", "relevance": "高", "reason": "agent"}], mode="deep")["session"]
    store.write_json(deep_search._pool_path(sid), {
        "session": sid, "pool": {"10.1/old": {}, "10.1/new": {}}, "served": [["10.1/old"], ["10.1/new"]],
        "last_batch": [{"key": "10.1/new", **PAPER}], "seeds": [], "batch_size": 20, "cap": 200, "rounds": []})
    monkeypatch.setattr(deep_search, "_ranked", lambda state: [])
    monkeypatch.setattr(deep_search, "_coverage", lambda state, rated, order: [])
    monkeypatch.setattr(deep_search, "_stop", lambda state, rated: {"stop_suggested": False, "stop_reason": ""})
    return sid


@pytest.fixture
def client():
    c = TestClient(app_server.create_app(TOKEN))
    c.headers["X-LitLoop-Token"] = TOKEN
    return c


def _item(sid, doi):
    return next((it for it in screening.load(sid)["items"] if it["doi"] == doi), None)


def test_user_rates_a_paper_the_agent_has_not_rated(client, session):
    r = client.post(f"/api/sessions/{session}/rating", json={"doi": "10.1/new", "relevance": "中"}).json()
    assert r["item"]["relevance"] == "中" and r["item"]["user_relevance"] == "中" and r["item"]["reason"] == ""
    assert _item(session, "10.1/new")["title"] == "New"  # filled in from the batch
    # changing it, then clearing it, removes the paper again: nobody has rated it
    client.post(f"/api/sessions/{session}/rating", json={"doi": "10.1/new", "relevance": "高"})
    assert _item(session, "10.1/new")["user_relevance"] == "高"
    client.post(f"/api/sessions/{session}/rating", json={"doi": "10.1/new", "relevance": None})
    assert _item(session, "10.1/new") is None


def test_unknown_paper_is_404(client, session):
    assert client.post(f"/api/sessions/{session}/rating", json={"doi": "10.1/zzz", "relevance": "高"}).status_code == 404


def test_status_lists_the_batch_papers_the_agent_still_has_to_rate(client, session):
    client.post(f"/api/sessions/{session}/rating", json={"doi": "10.1/new", "relevance": "低"})
    out = deep_search.status(session)
    assert [u["doi"] for u in out["unrated"]] == ["10.1/new"] and out["unrated"][0]["n"] == 1
    # the agent rates it: its rating and reason arrive, the user's stays
    deep_search.rate(session, [{"n": 1, "relevance": "高", "reason": "agent says"}])
    it = _item(session, "10.1/new")
    assert it["relevance"] == "高" and it["reason"] == "agent says" and screening._rating(it) == "低"
    assert "unrated" not in deep_search.status(session)


def test_continue_starts_a_round_and_refuses_while_one_runs(client, session, monkeypatch):
    calls = []
    monkeypatch.setattr(deep_search, "next_batch", lambda sid, force=False: calls.append((sid, force)))
    assert client.post(f"/api/sessions/{session}/next").json() == {"started": True}
    for _ in range(50):
        if calls:
            break
        time.sleep(0.02)
    assert calls == [(session, True)]
    store.write_json(store.progress_path(session), {"state": "running", "updated": time.time()})
    assert client.post(f"/api/sessions/{session}/next").status_code == 409


def test_continue_needs_a_deep_search(client, session):
    quick = screening.save("q2", [{"doi": "10.1/q", "title": "Q", "relevance": "高", "reason": "r"}])["session"]
    assert client.post(f"/api/sessions/{quick}/next").status_code == 400
