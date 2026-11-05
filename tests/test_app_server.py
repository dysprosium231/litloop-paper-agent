"""The desktop app's local backend (litloop.desktop.server)."""

import pytest
from fastapi.testclient import TestClient

from litloop import store
from litloop.desktop import server as app_server
from litloop.search import screening

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


def test_a_rejection_reason_is_kept_until_the_paper_is_taken_back(client, session):
    r = client.post(f"/api/sessions/{session}/decision",
                    json={"doi": "10.1/a", "decision": "rejected", "reason": "主题不相关"}).json()
    assert r["item"]["reject_reason"] == "主题不相关"
    assert screening.prisma(session)["reasons"] == [{"reason": "主题不相关", "count": 1}]
    # un-picking takes the reason with it
    r = client.post(f"/api/sessions/{session}/decision", json={"doi": "10.1/a", "decision": None}).json()
    assert "reject_reason" not in r["item"]
    # ... and so does a pick, or a rejection without a reason
    client.post(f"/api/sessions/{session}/decision",
                json={"doi": "10.1/a", "decision": "rejected", "reason": "重复文献"})
    r = client.post(f"/api/sessions/{session}/decision", json={"doi": "10.1/a", "decision": "selected"}).json()
    assert "reject_reason" not in r["item"]


def test_progress_is_none_when_nothing_runs(client, session):
    assert client.get(f"/api/sessions/{session}/progress").json() is None


@pytest.fixture
def config_home(tmp_path, monkeypatch):
    from litloop import config
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "config.json")
    for env in config.ENV_KEYS.values():
        monkeypatch.delenv(env, raising=False)
    return tmp_path


def test_settings_show_secrets_only_by_their_ends(client, config_home):
    from litloop import config
    config.update_config("openalex_api_key", "abcd1234567890wxyz")
    fields = {f["key"]: f for g in client.get("/api/settings").json() for f in g["fields"]}
    assert fields["openalex_api_key"]["value"] == "abcd…wxyz" and fields["openalex_api_key"]["set"]
    assert fields["openalex_api_key"]["source"] == "file"
    assert "abcd1234567890wxyz" not in client.get("/api/settings").text
    assert fields["email"]["value"] == ""  # the shipped placeholder is not shown as an address


def test_settings_update_only_known_keys_with_valid_values(client, config_home):
    from litloop import config
    r = client.post("/api/settings", json={"key": "scihub_enabled", "value": True})
    assert r.status_code == 200 and config.load_config()["scihub_enabled"] is True
    assert client.post("/api/settings", json={"key": "download_strategy", "value": "oa_first"}).status_code == 200
    assert client.post("/api/settings", json={"key": "download_strategy", "value": "nope"}).status_code == 400
    assert client.post("/api/settings", json={"key": "email", "value": "not-an-email"}).status_code == 400
    assert client.post("/api/settings", json={"key": "browser_executable", "value": "x"}).status_code == 400


def test_download_folder_test(client, config_home, tmp_path):
    from litloop import config
    config.update_config("output_dir", str(tmp_path / "papers"))
    got = client.post("/api/settings/test/downloads").json()["results"][0]
    assert got["ok"] is True
    assert client.post("/api/settings/test/nope").status_code == 404


def test_component_install_goes_through_uv(client, monkeypatch):
    from litloop import optional
    seen = {}

    def fake_install(key, *, index_url=""):
        seen.update(key=key, index_url=index_url)
        return {"ok": True, "log": "installed"}

    monkeypatch.setattr(optional, "install", fake_install)
    got = client.post("/api/components/layout/install").json()
    assert got == {"ok": True, "log": "installed"}
    assert seen["key"] == "layout"
    assert client.post("/api/components/nope/install").status_code == 404


def test_openalex_live_count_is_kept_two_minutes(client, monkeypatch):
    from litloop import quota
    calls = []
    monkeypatch.setattr(app_server, "_OPENALEX", {"at": 0.0, "data": None})
    monkeypatch.setattr(quota, "openalex_live", lambda: calls.append(1) or {"credits_remaining": len(calls)})
    assert client.get("/api/quota/openalex").json() == {"credits_remaining": 1}
    assert client.get("/api/quota/openalex").json() == {"credits_remaining": 1}  # from the cache
    assert client.get("/api/quota/openalex?fresh=true").json() == {"credits_remaining": 2}


def test_glossary_add_confirm_edit_delete(client):
    from litloop.terms import glossary
    added = client.post("/api/glossary", json={"terms": ["PDC", "polymer-derived ceramic"]}).json()
    assert added["status"] == "active"
    assert client.post("/api/glossary", json={"terms": ["PDC"]}).status_code == 400
    assert client.post("/api/glossary", json={"terms": ["pdc", "Polymer derived ceramics"]}).status_code == 400
    s = glossary.suggest_from(["silicon oxycarbide (SiOC) foam", "dense silicon oxycarbide (SiOC)"])[0]
    got = client.get("/api/glossary").json()
    assert [e["id"] for e in got["active"]] == [added["id"]] and [e["id"] for e in got["suggested"]] == [s["id"]]
    assert client.post(f"/api/glossary/{s['id']}", json={"confirm": True}).json()["status"] == "active"
    edited = client.post(f"/api/glossary/{added['id']}", json={"terms": ["PDC", "PDCs", "polymer-derived ceramic"], "note": "n"}).json()
    assert edited["note"] == "n"
    assert client.post(f"/api/glossary/{added['id']}", json={"terms": ["PDC"]}).status_code == 400
    assert client.post(f"/api/glossary/{added['id']}/delete").json() == {"deleted": added["id"]}
    assert client.post(f"/api/glossary/{added['id']}/delete").status_code == 404
    assert client.post("/api/glossary/nope", json={"confirm": True}).status_code == 404


def test_glossary_builtin_lookup_and_switch(client):
    got = client.get("/api/glossary/builtin", params={"q": "alumin"}).json()
    assert [e["id"] for e in got["elements"]] == ["element:Al"] and got["spelling"][0]["id"] == "spelling:aluminum"
    assert len(client.get("/api/glossary/builtin").json()["elements"]) == 118
    r = client.post("/api/glossary/builtin", json={"id": "element:Al", "enabled": False}).json()
    assert r == {"id": "element:Al", "disabled": True}
    assert client.get("/api/glossary/builtin", params={"q": "Al"}).json()["elements"][0]["disabled"]
    assert client.post("/api/glossary/builtin", json={"id": "element:Xx", "enabled": False}).status_code == 404


def test_round_notice_is_none_without_a_deep_search(client):
    assert client.get("/api/screening/notice").json() is None


def test_round_notice_names_the_newest_round(client, tmp_path):
    store.write_json(store.progress_path("s-old"), {
        "session": "s-old", "event": "next", "state": "idle", "started": 100.0, "updated": 200.0})
    store.write_json(store.progress_path("s-new"), {
        "session": "s-new", "event": "next", "state": "running", "started": 300.0, "updated": 400.0})
    store.write_json(store.summary_path("s-new"), {
        "session": "s-new", "question": "稀土药物载体", "batches": [{}, {}, {}]})

    n = client.get("/api/screening/notice").json()
    assert n["session"] == "s-new" and n["state"] == "running"
    assert n["question"] == "稀土药物载体" and n["batches"] == 3 and n["started"] == 300.0


def test_round_notice_survives_a_missing_summary(client):
    store.write_json(store.progress_path("s1"), {
        "session": "s1", "event": "start", "state": "idle", "started": 1.0, "updated": 2.0})
    n = client.get("/api/screening/notice").json()
    assert n["session"] == "s1" and n["question"] == "" and n["batches"] == 0


def test_a_watch_is_saved_from_a_session_and_checked(client, session, monkeypatch):
    from litloop.search import quick, watches
    monkeypatch.setattr(watches, "path", lambda: screening._config.DATA_DIR / "watches.json")
    screening.save("q", [{"doi": "10.1/a", "title": "A", "relevance": "低"},
                            {"doi": "10.1/b", "title": "B", "relevance": "高"}],
                   query="aerogel 2024..2026", session=session)
    got = client.post("/api/watches", json={"session": session}).json()
    assert got["query"] == "aerogel 2024..2026" and got["seen_count"] == 2
    monkeypatch.setattr(quick, "search_papers_v2",
                        lambda *a, **k: [{"doi": "10.1/a", "title": "A"}, {"doi": "10.1/z", "title": "Z"}])
    checked = client.post(f"/api/watches/{got['id']}/check").json()
    assert [p["doi"] for p in checked["new"]] == ["10.1/z"]
    assert client.delete(f"/api/watches/{got['id']}").json() == {"removed": True}
    assert client.post("/api/watches/nope/check").status_code == 404
