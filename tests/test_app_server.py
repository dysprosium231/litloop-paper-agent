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


def test_webview_private_network_preflight_is_allowed():
    """WebView2 158+ preflights tauri.localhost -> 127.0.0.1 as a private-network request (2026-11-23)."""
    c = TestClient(app_server.create_app(TOKEN))
    for method, path in (("GET", "/api/sessions"), ("DELETE", "/api/watches/1")):
        r = c.options(path, headers={
            "Origin": "http://tauri.localhost", "Access-Control-Request-Method": method,
            "Access-Control-Request-Headers": "x-litloop-token", "Access-Control-Request-Private-Network": "true"})
        assert r.status_code == 200
        assert r.headers.get("access-control-allow-private-network") == "true"


def test_the_data_dir_is_told_to_the_app(client):
    """Settings shows where papers and sessions live, so an install does not look empty."""
    from litloop.config import DATA_DIR
    body = client.get("/api/data-dir").json()
    assert body["path"] == str(DATA_DIR)
    assert isinstance(body["exists"], bool)


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
    from litloop import db

    store.write_json(store.progress_path("s-old"), {
        "session": "s-old", "event": "next", "state": "idle", "started": 100.0, "updated": 200.0})
    store.write_json(store.progress_path("s-new"), {
        "session": "s-new", "event": "next", "state": "running", "started": 300.0, "updated": 400.0})
    db.save_summary("s-new", {
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

def test_diagnostics_export_writes_a_zip(client, tmp_path, monkeypatch):
    from litloop import diagnostics

    target = tmp_path / "bundle.zip"
    monkeypatch.setattr(diagnostics, "write_bundle", lambda *a, **k: target)
    target.write_bytes(b"PK")
    got = client.post("/api/diagnostics").json()
    assert got == {"path": str(target), "size": 2}


def test_session_reports_resumable_and_progress_translates_errors(client, session, monkeypatch):
    from litloop import db
    from litloop.search import deep as deep_search

    assert client.get(f"/api/sessions/{session}").json()["resumable"] is False
    db.save_pool_state({"session": session, "pool": {}})
    assert client.get(f"/api/sessions/{session}").json()["resumable"] is True

    store.write_json(store.progress_path(session), {
        "session": session, "event": "start", "state": "failed",
        "stopped_phase": "expand", "error": "OpenAlex daily budget used up (1000 credits)",
        "started": 1.0, "updated": 2.0, "phase": None, "phases": [],
    })
    prog = client.get(f"/api/sessions/{session}/progress").json()
    assert prog["error_text"].startswith("无法检索：OpenAlex 今日积分已用完")

    store.write_json(store.progress_path(session), {
        "session": session, "event": "next", "state": "running", "pid": 999999,
        "started": 1.0, "updated": 9999999999.0, "phase": {"name": "expand", "done": 1, "total": 5, "started": 1.0}, "phases": [],
    })
    monkeypatch.setattr(store, "pid_alive", lambda pid: False)
    assert client.get(f"/api/sessions/{session}/progress").json()["state"] == "interrupted"

    import threading
    called = threading.Event()
    monkeypatch.setattr(deep_search, "next_batch", lambda *a, **k: called.set() or {})
    assert client.post(f"/api/sessions/{session}/next").json() == {"started": True}
    assert called.wait(2.0)


def test_resume_endpoint_checks_session_and_forwards_to_agent_runner(client, session, monkeypatch):
    from litloop.desktop import agent_runner

    assert client.post("/api/sessions/no-such/resume", json={}).status_code == 404
    assert client.post(f"/api/sessions/{session}/resume", json={}).status_code == 400  # quick session: no pool

    monkeypatch.setattr(agent_runner, "resume", lambda s, batches=0: {"run": "r1", "session": s, "state": "running", "batches": batches})
    got = client.post(f"/api/sessions/{session}/resume", json={"batches": 2})
    assert got.status_code == 200 and got.json()["batches"] == 2

    def busy(*a, **k):
        raise RuntimeError("已有检索在进行")

    monkeypatch.setattr(agent_runner, "resume", busy)
    assert client.post(f"/api/sessions/{session}/resume", json={}).status_code == 409


def test_batch_decisions_and_prisma_reasons(client, session):
    r = client.post(f"/api/sessions/{session}/batch", json={
        "ops": [
            {"doi": "10.1/b", "decision": "selected"},
            {"doi": "10.1/a", "decision": "rejected"},
        ],
    }).json()
    assert r["updated"] == 2 and r["skipped"] == 0
    items = {it["doi"]: it for it in screening.load(session)["items"]}
    assert items["10.1/b"]["decision"] == "selected"
    assert items["10.1/a"]["decision"] == "rejected"
    assert "reject_reason" not in items["10.1/a"]
    # No user reject_reason falls back to the agent's reason in prisma().
    assert screening.prisma(session)["reasons"] == [{"reason": "r", "count": 1}]

    # Overwriting with an explicit reject_reason, then clearing decisions.
    client.post(f"/api/sessions/{session}/batch", json={
        "ops": [{"doi": "10.1/a", "decision": "rejected", "reject_reason": "主题不相关"}],
    })
    assert screening.prisma(session)["reasons"] == [{"reason": "主题不相关", "count": 1}]

    client.post(f"/api/sessions/{session}/batch", json={
        "ops": [
            {"doi": "10.1/a", "decision": None},
            {"doi": "10.1/b", "decision": None},
        ],
    })
    items = {it["doi"]: it for it in screening.load(session)["items"]}
    assert items["10.1/a"]["decision"] is None and "reject_reason" not in items["10.1/a"]
    assert items["10.1/b"]["decision"] is None


def test_batch_ratings_clear_and_undo_mixed_state(client, session):
    client.post(f"/api/sessions/{session}/decision", json={"doi": "10.1/a", "decision": "rejected", "reason": "重复文献"})
    client.post(f"/api/sessions/{session}/rating", json={"doi": "10.1/a", "relevance": "中"})
    client.post(f"/api/sessions/{session}/decision", json={"doi": "10.1/b", "decision": "selected"})

    res = client.post(f"/api/sessions/{session}/batch", json={
        "ops": [
            {"doi": "10.1/a", "clear_user_relevance": True, "decision": "selected"},
            {"doi": "10.1/b", "relevance": "中", "decision": "rejected", "reject_reason": "数据不可靠"},
        ],
    }).json()
    assert res["updated"] == 2 and res["edited"] == 1
    items = {it["doi"]: it for it in screening.load(session)["items"]}
    assert "user_relevance" not in items["10.1/a"] and items["10.1/a"]["decision"] == "selected"
    assert items["10.1/b"]["user_relevance"] == "中" and items["10.1/b"]["reject_reason"] == "数据不可靠"

    # Undo using returned snapshots restores every field cleanly.
    undo_ops = [
        {
            "doi": p["doi"],
            "title": p["title"],
            **({"relevance": p["user_relevance"]} if p.get("user_relevance") else {"clear_user_relevance": True}),
            "decision": p["decision"],
            "reject_reason": p["reject_reason"],
        }
        for p in res["previous"]
    ]
    undo_res = client.post(f"/api/sessions/{session}/batch", json={"ops": undo_ops}).json()
    assert undo_res["updated"] == 2 and undo_res["edited"] == 1
    restored = {it["doi"]: it for it in screening.load(session)["items"]}
    assert restored["10.1/a"]["user_relevance"] == "中"
    assert restored["10.1/a"]["decision"] == "rejected" and restored["10.1/a"]["reject_reason"] == "重复文献"
    assert "user_relevance" not in restored["10.1/b"] and restored["10.1/b"]["decision"] == "selected"
    assert "reject_reason" not in restored["10.1/b"]


def test_batch_handles_unrated_batch_papers_and_safe_undo(client, session):
    from litloop import db

    db.save_pool_state({
        "session": session,
        "question": "q",
        "pool": {},
        "seeds": [],
        "served": [],
        "rounds": [],
        "last_batch": [{"n": 1, "doi": "10.1/c", "title": "C", "year": 2024, "journal": "J"}],
    })

    # Decision-only batch skips unrated batch papers cleanly.
    r1 = client.post(f"/api/sessions/{session}/batch", json={
        "ops": [
            {"doi": "10.1/a", "decision": "selected"},
            {"doi": "10.1/c", "decision": "selected"},
        ],
    }).json()
    assert r1["updated"] == 1 and r1["skipped"] == 1

    # Rating batch creates the unrated paper with existed=False in previous.
    r2 = client.post(f"/api/sessions/{session}/batch", json={
        "ops": [{"doi": "10.1/c", "relevance": "高", "decision": "selected"}],
    }).json()
    assert r2["updated"] == 1 and r2["previous"][0]["existed"] is False
    items = {it["doi"]: it for it in screening.load(session)["items"]}
    assert items["10.1/c"]["user_relevance"] == "高" and items["10.1/c"]["decision"] == "selected"

    # Undo via delete_if_unrated deletes a user-only item...
    client.post(f"/api/sessions/{session}/batch", json={
        "ops": [{"doi": "10.1/c", "delete_if_unrated": True}],
    })
    assert all(it.get("doi") != "10.1/c" for it in screening.load(session)["items"])

    # ...but if the agent has since added a reason, delete_if_unrated keeps the item and only clears user_relevance.
    client.post(f"/api/sessions/{session}/batch", json={
        "ops": [{"doi": "10.1/c", "relevance": "高"}],
    })
    screening.save(
        "",
        [{"doi": "10.1/c", "title": "C", "relevance": "高", "reason": "Agent 补评理由"}],
        session=session,
        mode="deep",
    )
    client.post(f"/api/sessions/{session}/batch", json={
        "ops": [{"doi": "10.1/c", "delete_if_unrated": True}],
    })
    c_item = next(it for it in screening.load(session)["items"] if it["doi"] == "10.1/c")
    assert c_item["reason"] == "Agent 补评理由" and "user_relevance" not in c_item


def test_batch_validation_errors(client, session):
    assert client.post(f"/api/sessions/{session}/batch", json={"ops": []}).status_code == 400
    assert client.post(f"/api/sessions/{session}/batch", json={"ops": [{"doi": "10.1/a", "relevance": "bad"}]}).status_code == 400
    assert client.post(f"/api/sessions/{session}/batch", json={"ops": [{"doi": "10.1/a", "decision": "maybe"}]}).status_code == 400


def test_builtin_agent_presets_and_apply(client, config_home):
    from litloop import config

    config.update_config("builtin_agent_api_key", "sk-keep-me")
    r_list = client.get("/api/builtin-agent/presets")
    assert r_list.status_code == 200
    presets = r_list.json()
    assert any(p["id"] == "deepseek" for p in presets)

    r_bad = client.post("/api/builtin-agent/preset", json={"preset": "no-such-provider"})
    assert r_bad.status_code == 400

    r_apply = client.post("/api/builtin-agent/preset", json={"preset": "deepseek"})
    assert r_apply.status_code == 200
    fields = {f["key"]: f for f in r_apply.json()}
    assert fields["builtin_agent_provider"]["value"] == "openai"
    assert fields["builtin_agent_model"]["value"]
    assert fields["builtin_agent_api_key"]["set"] is True
    cfg = config.load_config()
    assert cfg["builtin_agent_preset"] == "deepseek"
    assert cfg["builtin_agent_api_key"] == "sk-keep-me"
    assert cfg["agent_backend"] == "builtin"


def test_agent_chat_read_clear_and_steering_routes(client, session):
    from litloop import db

    assert client.get("/api/agent/chat/no-such-session").status_code == 404
    assert client.get("/api/agent/chat/.bad").status_code == 400

    draft = client.get("/api/agent/chat/__draft__")
    assert draft.status_code == 200
    assert draft.json() == {"session_id": "__draft__", "messages": [], "steering_notes": []}

    assert client.post(f"/api/agent/steering/{session}", json={"note": "   "}).status_code == 400
    note_res = client.post(f"/api/agent/steering/{session}", json={"note": "优先实验验证类论文"})
    assert note_res.status_code == 200
    note_id = note_res.json()["id"]

    db.add_agent_message(session, "user", "帮我总结高相关文献")
    chat_res = client.get(f"/api/agent/chat/{session}")
    assert chat_res.status_code == 200
    body = chat_res.json()
    assert len(body["messages"]) == 1
    assert body["messages"][0]["content"] == "帮我总结高相关文献"
    assert [n["note"] for n in body["steering_notes"]] == ["优先实验验证类论文"]

    del_res = client.delete(f"/api/agent/steering/{note_id}")
    assert del_res.status_code == 200 and del_res.json() == {"removed": True}
    assert client.delete(f"/api/agent/steering/{note_id}").status_code == 404

    clear_res = client.post(f"/api/agent/chat/{session}/clear")
    assert clear_res.status_code == 200 and clear_res.json()["cleared"] == 1
    assert client.get(f"/api/agent/chat/{session}").json()["messages"] == []


def test_agent_chat_stream_and_action_confirm_cancel(client, session, monkeypatch):
    import json
    from litloop import db
    from litloop.agents import chat_agent

    assert client.post(f"/api/agent/chat/{session}/stream", json={"message": "  "}).status_code == 400

    seen = {}

    def fake_stream(sid, text, *, view=None, config=None):
        seen["sid"] = sid
        seen["text"] = text
        seen["view"] = view
        yield {"type": "user_message", "message": {"id": 1, "role": "user", "content": text}}
        yield {"type": "delta", "text": "收到"}
        yield {"type": "done", "message": {"id": 2, "role": "assistant", "content": "收到"}}

    monkeypatch.setattr(chat_agent, "stream_chat_turn", fake_stream)
    res = client.post(
        f"/api/agent/chat/{session}/stream",
        json={"message": "分析当前文献", "view": {"paper": {"doi": "10.1/b"}}},
    )
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("text/event-stream")
    assert res.headers.get("cache-control") == "no-cache"
    assert res.headers.get("x-accel-buffering") == "no"
    events = [
        json.loads(line[len("data: "):])
        for line in res.text.splitlines()
        if line.startswith("data: ")
    ]
    assert [e["type"] for e in events] == ["user_message", "delta", "done"]
    assert seen == {"sid": session, "text": "分析当前文献", "view": {"paper": {"doi": "10.1/b"}}}

    msg = db.add_agent_message(
        session,
        "assistant",
        "确认操作",
        actions=[{"id": "act-1", "kind": "add_glossary", "title": "添加术语", "summary": "s", "params": {"terms": ["SiC"]}, "status": "pending", "result": None}],
    )

    def fake_execute(sid, message_id, action_id, *, edited_params=None):
        if action_id == "missing":
            raise LookupError("未找到操作")
        if action_id == "done":
            raise ValueError("该操作已处理")
        return {"ok": True, "action": {"id": action_id, "status": "confirmed"}, "result": edited_params}

    monkeypatch.setattr(chat_agent, "execute_action", fake_execute)
    ok_res = client.post(
        "/api/agent/actions/confirm",
        json={"session_id": session, "message_id": msg["id"], "action_id": "act-1", "params": {"terms": ["SiC", "碳化硅"]}},
    )
    assert ok_res.status_code == 200 and ok_res.json()["ok"] is True
    assert client.post(
        "/api/agent/actions/confirm",
        json={"session_id": session, "message_id": msg["id"], "action_id": "missing"},
    ).status_code == 404
    assert client.post(
        "/api/agent/actions/confirm",
        json={"session_id": session, "message_id": msg["id"], "action_id": "done"},
    ).status_code == 400

    cancel_res = client.post(
        "/api/agent/actions/cancel",
        json={"session_id": session, "message_id": msg["id"], "action_id": "act-1"},
    )
    assert cancel_res.status_code == 200
    assert cancel_res.json()["action"]["status"] == "cancelled"
    assert client.post(
        "/api/agent/actions/cancel",
        json={"session_id": session, "message_id": msg["id"], "action_id": "no-such-act"},
    ).status_code == 404


def test_translation_uses_builtin_model_when_configured(client, monkeypatch):
    from litloop import config as _config, db
    from litloop.agents import client as aclient
    from litloop.desktop import translate

    sid = screening.save("q", [{
        "doi": "10.1/abs",
        "title": "Abstract Paper",
        "abstract": "We report a novel catalyst.",
        "relevance": "高",
        "reason": "r",
    }])["session"]

    cfg_ready = {
        "translation_provider": "agent",
        "builtin_agent_provider": "openai",
        "builtin_agent_model": "deepseek-chat",
        "builtin_agent_api_key": "sk-test",
    }
    monkeypatch.setattr(_config, "load_config", lambda: cfg_ready)
    monkeypatch.setattr(translate, "load_config", lambda: cfg_ready)
    res_ready = client.get("/api/translation")
    assert res_ready.status_code == 200
    assert res_ready.json()["provider"] == "agent"
    assert res_ready.json()["ready"] is True

    monkeypatch.setattr(
        aclient,
        "chat",
        lambda *a, **k: {"text": "<think>内部思考</think>\n我们报道了一种新型催化剂。", "tool_calls": [], "stop": "stop", "usage": {}},
    )
    res_tr = client.post(f"/api/sessions/{sid}/translate", json={"doi": "10.1/abs", "title": "Abstract Paper"})
    assert res_tr.status_code == 200
    assert res_tr.json() == {"abstract_zh": "我们报道了一种新型催化剂。"}
    saved = db.load_screening(sid)["items"][0]
    assert saved["abstract_zh"] == "我们报道了一种新型催化剂。"

    # 未配置内置模型时返回 502 并附友好引导
    cfg_empty = {"translation_provider": "agent"}
    monkeypatch.setattr(_config, "load_config", lambda: cfg_empty)
    monkeypatch.setattr(translate, "load_config", lambda: cfg_empty)
    assert client.get("/api/translation").json()["ready"] is False
    sid2 = screening.save("q2", [{
        "doi": "10.1/abs2",
        "title": "Abstract Paper 2",
        "abstract": "Another abstract.",
        "relevance": "高",
        "reason": "r",
    }])["session"]
    res_fail = client.post(f"/api/sessions/{sid2}/translate", json={"doi": "10.1/abs2", "title": "Abstract Paper 2"})
    assert res_fail.status_code == 502
    assert "内置 Agent" in res_fail.json()["detail"]


def test_agent_chat_stream_requires_stream_suffix(client, session, monkeypatch):
    from litloop.agents import chat_agent

    monkeypatch.setattr(
        chat_agent,
        "stream_chat_turn",
        lambda sid, text, *, view=None, config=None: iter([
            {"type": "delta", "text": "ok"},
            {"type": "done", "message": {"id": 1, "role": "assistant", "content": "ok"}},
        ]),
    )
    ok_res = client.post(f"/api/agent/chat/{session}/stream", json={"message": "你好"})
    assert ok_res.status_code == 200
    assert ok_res.headers["content-type"].startswith("text/event-stream")

    bad_res = client.post(f"/api/agent/chat/{session}", json={"message": "你好"})
    assert bad_res.status_code in (404, 405)


def test_recommend_site_route_campus_and_speedup_rules():
    from litloop.institutional.proxy_check import recommend_site_route

    campus_site = {"name": "ScienceDirect", "host": "www.sciencedirect.com", "campus_sensitive": True}
    oa_site = {"name": "arXiv", "host": "arxiv.org", "campus_sensitive": False}

    # 校园网下直连可用时，即使代理更快也不建议切代理，以免丢失学校 IP 权限
    r1 = recommend_site_route(
        campus_site,
        {"ok": True, "status": 200, "ms": 4200, "error": ""},
        {"ok": True, "status": 200, "ms": 300, "error": ""},
        is_campus=True,
    )
    assert r1["route"] == "direct"

    # 校园网下直连失败而代理可达时，建议走代理
    r2 = recommend_site_route(
        campus_site,
        {"ok": False, "status": 0, "ms": 6000, "error": "SSLError"},
        {"ok": True, "status": 200, "ms": 325, "error": ""},
        is_campus=True,
    )
    assert r2["route"] == "proxy"

    # 非权限敏感 OA 站，直连 >=2000ms 且比代理慢 2 倍以上时建议走代理
    r3 = recommend_site_route(
        oa_site,
        {"ok": True, "status": 200, "ms": 5000, "error": ""},
        {"ok": True, "status": 200, "ms": 750, "error": ""},
        is_campus=True,
    )
    assert r3["route"] == "proxy"

    # 直连本身较快（<2000ms）保持直连
    r4 = recommend_site_route(
        oa_site,
        {"ok": True, "status": 200, "ms": 800, "error": ""},
        {"ok": True, "status": 200, "ms": 300, "error": ""},
        is_campus=False,
    )
    assert r4["route"] == "direct"

    # 未检测到代理时
    r5 = recommend_site_route(oa_site, {"ok": False, "status": 0, "ms": 3000, "error": "Timeout"}, None, is_campus=False)
    assert r5["route"] == "unavailable"


def test_network_route_benchmark_and_apply_endpoints(client, monkeypatch):
    from litloop import config as _config
    from litloop.institutional import proxy_check

    monkeypatch.setattr(proxy_check, "detect_local_proxy", lambda configured="": "http://127.0.0.1:10808")

    def fake_probe(url, *, proxy_url=None, timeout=6.0):
        if "journals.sagepub.com" in url:
            return {"ok": False, "status": 0, "ms": 1200, "error": "SSLError"} if not proxy_url else {"ok": True, "status": 200, "ms": 320, "error": ""}
        if "arxiv.org" in url:
            return {"ok": True, "status": 200, "ms": 4800, "error": ""} if not proxy_url else {"ok": True, "status": 200, "ms": 700, "error": ""}
        return {"ok": True, "status": 200, "ms": 400, "error": ""}

    monkeypatch.setattr(proxy_check, "_probe_one_url", fake_probe)

    res = client.post("/api/network/route-benchmark", json={"is_campus": True})
    assert res.status_code == 200
    body = res.json()
    assert body["proxy_url"] == "http://127.0.0.1:10808"
    assert body["is_campus"] is True
    assert set(body["recommended_hosts"]) == {"journals.sagepub.com", "arxiv.org"}

    apply_res = client.post(
        "/api/network/host-proxy",
        json={"proxy_url": "http://127.0.0.1:10808", "hosts": ["journals.sagepub.com", "arxiv.org", "JOURNALS.SAGEPUB.COM"]},
    )
    assert apply_res.status_code == 200
    applied = apply_res.json()
    assert applied["ok"] is True
    assert applied["proxy_url"] == "http://127.0.0.1:10808"
    assert applied["hosts"] == ["journals.sagepub.com", "arxiv.org"]

    cfg = _config.load_config()
    assert cfg["host_proxy_url"] == "http://127.0.0.1:10808"
    assert cfg["host_proxy_hosts"] == ["journals.sagepub.com", "arxiv.org"]


def test_watch_merge_and_systematic_review_endpoints(client, session, monkeypatch, tmp_path):
    import json
    from pathlib import Path
    from litloop import config as _config
    from litloop.search import watches

    monkeypatch.setattr(_config, "load_config", lambda: {"output_dir": str(tmp_path), "builtin_agent_api_key": ""})
    w = watches.add("TiO2 photocatalysis", "TiO2 photocatalysis", session=session)

    merge_res = client.post(
        f"/api/watches/{w['id']}/merge",
        json={"papers": [{"doi": "10.1/watch-new", "title": "Watch Hit", "year": 2026}], "score": True},
    )
    assert merge_res.status_code == 200
    assert merge_res.json()["session"] == session
    assert merge_res.json()["added"] == 1

    repro_res = client.post(f"/api/sessions/{session}/reproducibility/export")
    assert repro_res.status_code == 200
    assert Path(repro_res.json()["file"]).is_file()

    blind_res = client.post(f"/api/sessions/{session}/blind-review/export")
    assert blind_res.status_code == 200
    assert Path(blind_res.json()["file"]).is_file()
    assert blind_res.json()["count"] >= 1

    bad_cmp = client.post(f"/api/sessions/{session}/dual-review/compare", json={"raw_json": "{bad"})
    assert bad_cmp.status_code == 400

    good_cmp = client.post(
        f"/api/sessions/{session}/dual-review/compare",
        json={"raw_json": json.dumps({"items": [{"doi": "10.1/watch-new", "decision": "selected"}]})},
    )
    assert good_cmp.status_code == 200
    assert "kappa" in good_cmp.json()


def test_app_update_endpoint_parses_release_and_errors(client, monkeypatch):
    import requests
    from litloop.desktop import server as desktop_server

    desktop_server._APP_UPDATE.update(at=0.0, data=None)

    class _Resp:
        status_code = 200
        def json(self):
            return {"tag_name": "v9.9.9", "html_url": "https://example.org/rel", "body": "notes"}

    monkeypatch.setattr(requests, "get", lambda *a, **k: _Resp())
    res = client.get("/api/app-update?fresh=true")
    assert res.status_code == 200
    assert res.json()["available"] is True
    assert res.json()["latest"] == "9.9.9"

    def _timeout(*a, **k):
        raise requests.Timeout("slow")

    monkeypatch.setattr(requests, "get", _timeout)
    res_to = client.get("/api/app-update?fresh=true")
    assert res_to.status_code == 200
    assert "超时" in res_to.json()["error"]


def test_client_log_and_http_middleware_write_to_persisted_log(client, tmp_path):
    from litloop import log

    log_path = log.enable_file_logging(tmp_path / "logs")
    assert log_path is not None
    try:
        res = client.post(
            "/api/client-log",
            json={
                "level": "error",
                "source": "window.onerror",
                "message": "TypeError: boom",
                "stack": "at App.tsx:10",
                "context": "http://localhost:1420/",
            },
        )
        assert res.status_code == 200
        assert res.json() == {"logged": True}

        bad = client.get("/api/sessions/nonexistent-id-12345")
        assert bad.status_code == 404

        lines = log.tail_file(50)
        joined = "\n".join(lines)
        assert "[UI:window.onerror] TypeError: boom" in joined
        assert "[HTTP] GET /api/sessions/nonexistent-id-12345 -> 404" in joined
    finally:
        log.disable_file_logging()


def test_screening_test_handles_agent_and_builtin(client, config_home, monkeypatch):
    from litloop import config
    from litloop.desktop import translate

    config.update_config("translation_provider", "agent")
    res_agent = client.post("/api/settings/test/screening")
    assert res_agent.status_code == 200
    assert res_agent.json()["results"][0]["ok"] is None

    config.update_config("translation_provider", "builtin")
    res_unready = client.post("/api/settings/test/screening")
    assert res_unready.status_code == 200
    assert res_unready.json()["results"][0]["ok"] is None

    monkeypatch.setattr(translate, "is_ready", lambda cfg=None: True)
    monkeypatch.setattr(translate, "translate_text", lambda text, cfg=None: "示例中文摘要。")
    res_ok = client.post("/api/settings/test/screening")
    assert res_ok.status_code == 200
    assert res_ok.json()["results"][0]["ok"] is True
    assert "示例中文摘要" in res_ok.json()["results"][0]["detail"]


def test_unhandled_error_returns_json_500_with_cors(client, monkeypatch):
    from litloop.desktop import settings as app_settings

    def _boom(group):
        raise RuntimeError("boom")

    monkeypatch.setattr(app_settings, "run_test", _boom)
    res = client.post("/api/settings/test/screening", headers={"Origin": "http://tauri.localhost"})
    assert res.status_code == 500
    assert "RuntimeError" in res.json()["detail"]
    assert res.headers.get("access-control-allow-origin") == "http://tauri.localhost"
