"""Searches run by the app's own agent (backend B) instead of an agent command."""

from __future__ import annotations

import json
import threading
import time

import pytest

from litloop import config
from litloop.desktop import agent_runner


def _idle(timeout: float = 5.0) -> None:
    """The state flips before the worker clears itself, so wait for both."""
    deadline = time.monotonic() + timeout
    while agent_runner.running() is not None and time.monotonic() < deadline:
        time.sleep(0.02)


def _wait(run: str, timeout: float = 5.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        d = agent_runner.get(run)
        if d and d.get("state") != "running":
            return d
        time.sleep(0.02)
    raise TimeoutError(d)


@pytest.fixture
def builtin(monkeypatch):
    cfg = config.load_config()
    monkeypatch.setitem(cfg, "agent_backend", "builtin")
    monkeypatch.setitem(cfg, "builtin_agent_model", "deepseek-chat")
    monkeypatch.setattr(config, "load_config", lambda: cfg)
    monkeypatch.setattr(agent_runner._config, "load_config", lambda: cfg)
    return cfg


def test_a_missing_model_name_is_reported_before_anything_runs(builtin, monkeypatch):
    builtin["builtin_agent_model"] = ""
    monkeypatch.setattr("litloop.agents.run_builtin_search", lambda *a, **k: pytest.fail("no request expected"))
    monkeypatch.setattr("litloop.agents.run_task", lambda *a, **k: pytest.fail("no request expected"))
    d = agent_runner.start({"question": "x"})
    assert d["state"] == "failed" and "模型名" in d["error"]


def test_the_session_and_the_answer_come_back(builtin, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    usage = {"input_tokens": 100, "output_tokens": 40, "total_tokens": 140, "cost_text": "约 ¥0.0003"}

    def fake_search(form, *, config=None, stop_event=None, on_event=None):
        assert form["question"] == "x"
        on_event("progress", {"text": "正在规划检索词"})
        on_event("tool", {"name": "deep_search_start", "arguments": {}, "step": 1})
        on_event("result", {"name": "deep_search_start", "result": '{"session": "20261104-1-x"}', "step": 1})
        on_event("text", {"text": "找到 12 篇。", "step": 2})
        return {"text": "找到 12 篇。", "session": "20261104-1-x", "usage": usage}

    monkeypatch.setattr("litloop.agents.run_builtin_search", fake_search)
    run = agent_runner.start({"question": "x"})["run"]
    d = _wait(run)
    _idle()
    assert d["state"] == "done" and d["session"] == "20261104-1-x"
    assert d["result"] == "找到 12 篇。"
    assert d["usage"] == usage
    events = [e["text"] for e in d["events"]]
    assert "正在规划检索词" in events and "深度检索：正在构建候选池" in events
    assert agent_runner.list_runs()[0]["usage"] == usage
    assert agent_runner.session_origin("20261104-1-x")["last_run"]["usage"] == usage


def test_builtin_search_receives_form_config_and_stop_event(builtin, monkeypatch):
    seen = {}

    def fake_search(form, *, config=None, stop_event=None, on_event=None):
        seen["form"] = form
        seen["config"] = config
        seen["stop_event"] = stop_event
        return {"text": "完成", "session": "s1", "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2}}

    monkeypatch.setattr("litloop.agents.run_builtin_search", fake_search)
    _wait(agent_runner.start({"question": "x", "mode": "deep", "batches": 3})["run"])
    _idle()
    assert seen["form"]["mode"] == "deep" and seen["form"]["batches"] == 3
    assert seen["config"]["builtin_agent_model"] == "deepseek-chat"
    assert isinstance(seen["stop_event"], threading.Event)


def test_the_builtin_prompt_does_not_point_at_a_skill_it_cannot_read():
    assert agent_runner.build_prompt({"question": "x"}).startswith("用 litloop 技能")
    without = agent_runner.build_prompt({"question": "x", "mode": "deep"}, skill=False)
    assert "技能" not in without and without.startswith("做深度检索")


def test_a_failing_model_fails_the_run_with_its_message(builtin, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("HTTP 401: bad key")

    monkeypatch.setattr("litloop.agents.run_builtin_search", boom)
    d = _wait(agent_runner.start({"question": "x"})["run"])
    assert d["state"] == "failed" and "HTTP 401" in d["error"]


def test_cancel_stops_a_builtin_run(builtin, monkeypatch):
    started = threading.Event()
    release = threading.Event()

    def slow_search(form, *, config=None, stop_event=None, on_event=None):
        started.set()
        while not release.is_set():
            time.sleep(0.02)
            on_event("progress", {"text": "正在评分 1/10"})
        return {"text": "查完了", "cancelled": True}

    monkeypatch.setattr("litloop.agents.run_builtin_search", slow_search)
    run = agent_runner.start({"question": "x"})["run"]
    assert started.wait(2.0)
    assert agent_runner.cancel(run) is True
    d = _wait(run)
    release.set()
    assert d["state"] == "cancelled"
    # The state flips before the worker clears itself from the running table,
    # so give that a moment instead of reading it once.
    deadline = time.monotonic() + 5.0
    while agent_runner.running() is not None and time.monotonic() < deadline:
        time.sleep(0.02)
    assert agent_runner.running() is None


def test_cancelled_flag_from_builtin_search_marks_run_cancelled(builtin, monkeypatch):
    monkeypatch.setattr(
        "litloop.agents.run_builtin_search",
        lambda *a, **k: {"text": "已取消", "cancelled": True, "usage": {"input_tokens": 5, "output_tokens": 2, "total_tokens": 7}},
    )
    d = _wait(agent_runner.start({"question": "x"})["run"])
    _idle()
    assert d["state"] == "cancelled"
    assert d["usage"]["total_tokens"] == 7


def test_a_run_that_finishes_while_being_read_is_not_called_interrupted(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(agent_runner, "runs_dir", lambda: tmp_path)
    (tmp_path / "20261107-201500.json").write_text(
        json.dumps({"state": "running"}), encoding="utf-8"
    )
    seen: list[int] = []

    def read_json(p):
        seen.append(1)
        if len(seen) == 1:
            return {"state": "running"}  # the run had not written its end yet
        return {"state": "failed", "error": "x"}

    monkeypatch.setattr(agent_runner.store, "read_json", read_json)
    assert agent_runner.get("20261107-201500")["state"] == "failed"
    assert len(seen) == 2


def test_a_new_run_can_start_the_moment_the_last_one_finishes(builtin, monkeypatch):
    monkeypatch.setattr("litloop.agents.run_builtin_search", lambda *a, **k: {"text": "好了", "session": "s1"})
    first = agent_runner.start({"question": "x"})["run"]
    assert _wait(first)["state"] == "done"
    # No sleep: the finished run must be out of the running table already.
    second = agent_runner.start({"question": "y"})["run"]
    assert _wait(second)["state"] == "done"


def test_resume_runs_builtin_agent_and_blocks_concurrent_runs(builtin, tmp_path, monkeypatch):
    from litloop import db
    from litloop.search import screening

    monkeypatch.setattr(screening._config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    sid = screening.save("SiC/Al", [{"doi": "10.1/a", "title": "A", "relevance": "高", "reason": "r"}], mode="deep")["session"]
    db.save_pool_state({"session": sid, "pool": {}})

    seen = {}
    started = threading.Event()
    release = threading.Event()

    def fake_resume(session, *, batches=0, steering_notes=None, config=None, stop_event=None, on_event=None):
        seen.update(session=session, batches=batches)
        started.set()
        release.wait(2.0)
        return {"text": "续跑完成", "session": session, "usage": {"input_tokens": 50, "output_tokens": 20, "total_tokens": 70}}

    monkeypatch.setattr("litloop.agents.run_builtin_resume", fake_resume)
    run = agent_runner.resume(sid, batches=2)["run"]
    assert started.wait(2.0)
    with pytest.raises(RuntimeError, match="已有检索在进行"):
        agent_runner.resume(sid)
    release.set()
    d = _wait(run)
    _idle()
    assert d["state"] == "done" and d["resumed_from"] == sid and d["session"] == sid
    assert d["usage"]["total_tokens"] == 70
    assert seen["session"] == sid and seen["batches"] == 2


def test_per_task_model_selection_and_step_switching(monkeypatch):
    from litloop.agents import client, runner

    cfg = {
        "builtin_agent_provider": "openai",
        "builtin_agent_model": "gpt-main",
        "builtin_agent_plan_model": "gpt-strong",
        "builtin_agent_rate_model": "gpt-fast",
    }
    assert client.model_config(cfg)["model"] == "gpt-main"
    assert client.model_config(cfg, task="plan")["model"] == "gpt-strong"
    assert client.model_config(cfg, task="rate")["model"] == "gpt-fast"
    assert client.model_config({"builtin_agent_model": "only-main"}, task="rate")["model"] == "only-main"

    seen_tasks: list[str] = []

    def fake_chat(messages, tools, *, config=None, system="", max_tokens=4096, timeout=180, task="default"):
        seen_tasks.append(task)
        if len(seen_tasks) == 1:
            return {"text": "", "tool_calls": [{"id": "c1", "name": "search", "arguments": {"query": "x"}}]}
        return {"text": "done", "tool_calls": []}

    monkeypatch.setattr(runner, "chat", fake_chat)
    out = runner.run_task(
        "goal",
        [{"name": "search", "description": "s", "parameters": {"type": "object", "properties": {}}}],
        functions={"search": lambda **kw: {"items": []}},
        config=cfg,
        task_for_step=runner.default_task_for_step,
    )
    assert out["text"] == "done"
    assert seen_tasks == ["plan", "rate"]


def test_plan_search_endpoint_and_build_prompt(monkeypatch):
    from fastapi.testclient import TestClient
    from litloop.agents import runner
    from litloop.desktop import server as app_server

    monkeypatch.setattr(
        config,
        "load_config",
        lambda: {"builtin_agent_provider": "openai", "builtin_agent_model": "m-main", "builtin_agent_plan_model": "m-plan"},
    )
    monkeypatch.setattr(
        runner,
        "chat",
        lambda *a, **k: {
            "text": '```json\n{"queries": ["SiC aluminum infiltration", "SiC Al interface"], "core_terms": ["SiC", "aluminum|aluminium"], "criteria": ["研究界面反应"]}\n```',
            "tool_calls": [],
        },
    )

    c = TestClient(app_server.create_app("t0k"))
    c.headers["X-LitLoop-Token"] = "t0k"

    bad = c.post("/api/searches/plan", json={"question": "   "})
    assert bad.status_code == 400

    res = c.post("/api/searches/plan", json={"question": "SiC/Al", "mode": "deep", "batches": 3})
    assert res.status_code == 200
    body = res.json()
    assert body["model"] == "m-plan"
    assert body["queries"] == ["SiC aluminum infiltration", "SiC Al interface"]
    assert body["core_terms"] == ["SiC", "aluminum|aluminium"]
    assert "积分" in body["estimated_credits"]

    prompt = agent_runner.build_prompt({
        "question": "SiC/Al",
        "mode": "deep",
        "planned_queries": body["queries"],
        "planned_core_terms": body["core_terms"],
    })
    assert "已确认检索词" in prompt and "SiC aluminum infiltration" in prompt and "已确认核心词" in prompt


def test_endpoint_normalization_and_curl_fallback(monkeypatch):
    import requests
    from litloop.agents import client

    assert client._endpoint({"protocol": "openai", "base_url": "https://api.b.ai/v1/chat/completions"}) == "https://api.b.ai/v1/chat/completions"
    assert client._endpoint({"protocol": "openai", "base_url": "https://api.b.ai/v1"}) == "https://api.b.ai/v1/chat/completions"
    assert client._endpoint({"protocol": "anthropic", "base_url": "https://api.anthropic.com/v1/messages"}) == "https://api.anthropic.com/v1/messages"
    assert client._endpoint({"protocol": "anthropic", "base_url": "https://api.anthropic.com/v1"}) == "https://api.anthropic.com/v1/messages"

    client._CURL_HOSTS.clear()
    calls = {"requests": 0, "curl": 0}

    def fail_post(*a, **kw):
        calls["requests"] += 1
        raise requests.ReadTimeout("handshake timed out")

    def fake_curl(url, headers, body, proxy_url, timeout):
        calls["curl"] += 1
        return {"choices": [{"finish_reason": "stop", "message": {"content": "ok"}}]}

    monkeypatch.setattr(client.requests, "post", fail_post)
    monkeypatch.setattr(client, "_curl_bin", lambda: "curl")
    monkeypatch.setattr(client, "_post_curl", fake_curl)

    res1 = client._post("https://api.b.ai/v1/chat/completions", {}, {}, {}, 10)
    assert res1["choices"][0]["message"]["content"] == "ok"
    assert calls == {"requests": 1, "curl": 1}
    assert "api.b.ai" in client._CURL_HOSTS

    # Subsequent calls to the same host go straight to _post_curl without waiting on requests.post
    res2 = client._post("https://api.b.ai/v1/chat/completions", {}, {}, {}, 10)
    assert res2["choices"][0]["message"]["content"] == "ok"
    assert calls == {"requests": 1, "curl": 2}
    client._CURL_HOSTS.clear()


def test_resume_merges_active_steering_notes_from_db(builtin, tmp_path, monkeypatch):
    from litloop import db
    from litloop.search import screening

    monkeypatch.setattr(screening._config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    sid = screening.save("SiC/Al", [{"doi": "10.1/a", "title": "A", "relevance": "高", "reason": "r"}], mode="deep")["session"]
    db.save_pool_state({"session": sid, "pool": {}})
    n1 = db.add_steering_note(sid, "偏重实验验证论文")
    n2 = db.add_steering_note(sid, "排除纯分子动力学模拟")
    db.remove_steering_note(n2["id"])

    seen = {}

    def fake_resume(session, *, batches=0, steering_notes=None, config=None, stop_event=None, on_event=None):
        seen["steering_notes"] = steering_notes
        return {"text": "续跑完成", "session": session}

    monkeypatch.setattr("litloop.agents.run_builtin_resume", fake_resume)
    run = agent_runner.resume(sid, batches=1, steering_notes=["偏重实验验证论文", "关注界面相容性"])["run"]
    d = _wait(run)
    _idle()
    assert d["state"] == "done"
    assert seen["steering_notes"] == ["偏重实验验证论文", "关注界面相容性"]
    assert d["steering_notes"] == ["偏重实验验证论文", "关注界面相容性"]
    assert n1["note"] in d["steering_notes"]


def test_start_from_draft_migrates_chat_and_steering_to_new_session(builtin, tmp_path, monkeypatch):
    from litloop import db

    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    db.add_agent_message("__draft__", "user", "帮我搜 SiC/Al")
    db.add_steering_note("__draft__", "排除纯仿真")

    def fake_search(form, *, config=None, on_event=None, stop_event=None):
        if on_event:
            on_event("result", {"name": "deep_search_start", "result": '{"session": "20261125-migrated"}'})
        return {"text": "完成", "session": "20261125-migrated", "usage": None}

    monkeypatch.setattr("litloop.agents.run_builtin_search", fake_search)
    d = _wait(agent_runner.start({"question": "SiC/Al", "mode": "deep", "from_draft": True})["run"])
    _idle()
    assert d["state"] == "done" and d["session"] == "20261125-migrated"
    assert db.list_agent_messages("__draft__") == []
    assert db.list_steering_notes("__draft__") == []
    assert [m["content"] for m in db.list_agent_messages("20261125-migrated")] == ["帮我搜 SiC/Al"]
    assert [n["note"] for n in db.list_steering_notes("20261125-migrated")] == ["排除纯仿真"]


def test_quick_search_passes_journal_filters_and_interleaves_all_queries(monkeypatch):
    from litloop.agents import runner
    from litloop.search import quick, screening

    search_calls: list[dict] = []

    def fake_search_v2(query="", limit=10, year_from=None, year_to=None, sort=None, **kw):
        search_calls.append({"query": query, "limit": limit, "kw": kw})
        if query == "q1":
            return [
                {"doi": "10.1/a", "title": "A"},
                {"doi": "10.1/b", "title": "B"},
                {"doi": "10.1/c", "title": "C"},
                {"doi": "10.1/d", "title": "D"},
                {"doi": "10.1/e", "title": "E"},
            ]
        return [
            {"doi": "10.1/x", "title": "X"},
            {"doi": "10.1/b", "title": "B"},  # duplicate across queries
            {"doi": "10.1/y", "title": "Y"},
        ]

    scored_papers: list[dict] = []

    def fake_score(question, criteria, papers, *, config=None, steering_notes=None, stop_event=None, on_chunk_done=None):
        scored_papers.extend(papers)
        ratings = [{"n": p["n"], "relevance": "高", "reason": "ok"} for p in papers]
        on_chunk_done(ratings, len(papers), len(papers), {})
        return {"ratings": ratings, "usage": {"by_model": {}, "total_tokens": 0}, "cancelled": False}

    monkeypatch.setattr(quick, "search_papers_v2", fake_search_v2)
    monkeypatch.setattr(runner, "score_papers", fake_score)
    monkeypatch.setattr(
        screening,
        "save",
        lambda q, items, **kw: {"session": "s-quick", "total": len(items), "by_relevance": {"高": len(items)}},
    )

    out = runner.run_builtin_search(
        {
            "question": "SiC/Al",
            "mode": "quick",
            "planned_queries": ["q1", "q2"],
            "limit": 5,
            "max_cas_zone": 2,
            "max_jcr_quartile": 1,
            "exclude_warning": True,
        }
    )

    assert out["session"] == "s-quick"
    # Must execute both queries instead of breaking after q1 reaches limit=5.
    assert [c["query"] for c in search_calls] == ["q1", "q2"]
    for c in search_calls:
        assert c["limit"] == 10  # max(10, min(5, ceil(5*1.5/2)+4))
        assert c["kw"]["max_cas_zone"] == 2
        assert c["kw"]["max_jcr_quartile"] == 1
        assert c["kw"]["exclude_warning"] is True
    # Interleaved & deduplicated: q1[0]=a, q2[0]=x, q1[1]=b, q2[1]=b(dup), q1[2]=c, q2[2]=y -> first 5
    assert [p["doi"] for p in scored_papers] == ["10.1/a", "10.1/x", "10.1/b", "10.1/c", "10.1/y"]


def test_deep_search_appends_journal_criteria_and_merges_zotero_collection_seeds(monkeypatch):
    from litloop.agents import runner
    from litloop.search import deep
    from litloop.zotero import client as zotero_client

    seen: dict = {}

    monkeypatch.setattr(
        zotero_client,
        "collection_dois",
        lambda col, cfg: {
            "success": True,
            "key": "K1",
            "path": col,
            "dois": ["10.1/hand", "10.1/z1", "10.1/z2"],
            "without_doi": 0,
        },
    )

    def fake_deep_start(question, queries, seeds, core_terms, *, criteria=None, **kw):
        seen["seeds"] = list(seeds)
        seen["criteria"] = list(criteria or [])
        return {
            "session": "s-deep",
            "batch": [{"n": 1, "doi": "10.1/p1", "title": "P1"}],
            "stop_suggested": True,
            "screened": 1,
        }

    def fake_score(question, criteria, papers, *, config=None, steering_notes=None, stop_event=None, on_chunk_done=None):
        seen["score_criteria"] = list(criteria)
        ratings = [{"n": 1, "relevance": "高", "reason": "ok"}]
        on_chunk_done(ratings, 1, 1, {})
        return {"ratings": ratings, "usage": {"by_model": {}, "total_tokens": 0}, "cancelled": False}

    monkeypatch.setattr(deep, "start", fake_deep_start)
    monkeypatch.setattr(deep, "rate", lambda sid, r: {"total": 1, "by_relevance": {"高": 1}})
    monkeypatch.setattr(runner, "score_papers", fake_score)

    out = runner.run_builtin_search(
        {
            "question": "SiC/Al",
            "mode": "deep",
            "planned_queries": ["q1"],
            "planned_core_terms": ["SiC"],
            "criteria": ["研究界面反应", "排除预警期刊"],
            "seeds": ["10.1/hand"],
            "seed_collection": "LitLoop/Seeds",
            "max_cas_zone": 2,
            "max_jcr_quartile": 2,
            "exclude_warning": True,
        },
        config={"builtin_agent_model": "m1"},
    )

    assert out["session"] == "s-deep"
    assert seen["seeds"] == ["10.1/hand", "10.1/z1", "10.1/z2"]
    assert seen["criteria"] == [
        "研究界面反应",
        "排除预警期刊",
        "优先中科院 1–2 区期刊",
        "优先 JCR Q1–Q2 期刊",
    ]
    assert seen["score_criteria"] == seen["criteria"]


def test_deep_search_zotero_failure_falls_back_to_llm_seed_selection(monkeypatch):
    from litloop.agents import runner
    from litloop.search import deep, quick
    from litloop.zotero import client as zotero_client

    events: list[tuple[str, dict]] = []
    search_calls: list[dict] = []
    seen_chat: dict = {}
    seen_deep: dict = {}

    monkeypatch.setattr(
        zotero_client,
        "collection_dois",
        lambda col, cfg: {"success": False, "error": "zotero-mcp not found"},
    )

    def fake_search_v2(query="", limit=10, year_from=None, year_to=None, **kw):
        search_calls.append({"query": query, "limit": limit, "kw": kw})
        idx = len(search_calls)
        return [
            {"doi": f"10.1/{idx}a", "title": f"Paper {idx}A", "year": 2024, "journal": "J1", "abstract": "abs"},
            {"doi": "", "title": f"No DOI {idx}"},
            {"doi": f"10.1/{idx}b", "title": f"Paper {idx}B", "year": 2023, "journal": "J2"},
        ]

    def fake_chat(messages, tools=None, *, config=None, system="", max_tokens=256, timeout=60, task="default"):
        seen_chat["task"] = task
        seen_chat["prompt"] = messages[0]["content"]
        return {
            "text": '```json\n{"selected": [2, 4, 99]}\n```',
            "tool_calls": [],
            "usage": {"input_tokens": 120, "output_tokens": 15, "total_tokens": 135},
        }

    monkeypatch.setattr(quick, "search_papers_v2", fake_search_v2)
    monkeypatch.setattr(runner, "chat", fake_chat)
    monkeypatch.setattr(
        deep,
        "start",
        lambda q, queries, seeds, core_terms, **kw: seen_deep.update(seeds=seeds)
        or {"session": "s-auto", "batch": [], "stop_suggested": True, "screened": 0},
    )

    out = runner.run_builtin_search(
        {
            "question": "SiC/Al",
            "mode": "deep",
            "planned_queries": ["q1", "q2", "q3", "q4"],
            "planned_core_terms": ["SiC"],
            "seed_collection": "Missing/Col",
            "max_cas_zone": 1,
            "exclude_warning": True,
        },
        config={"builtin_agent_provider": "openai", "builtin_agent_model": "gpt-main", "builtin_agent_rate_model": "gpt-rate"},
        on_event=lambda k, d: events.append((k, d)),
    )

    assert out["session"] == "s-auto"
    assert any("Missing/Col" in d.get("text", "") and "继续后续流程" in d.get("text", "") for k, d in events if k == "progress")
    # Only first 3 queries are used for seed discovery, with journal filters forwarded
    assert [c["query"] for c in search_calls] == ["q1", "q2", "q3"]
    assert all(c["kw"]["max_cas_zone"] == 1 and c["kw"]["exclude_warning"] is True for c in search_calls)
    assert seen_chat["task"] == "rate"
    # Interleaved candidates with DOI: 1a(1), 2a(2), 3a(3), 1b(4), 2b(5), 3b(6) -> picked [2, 4]
    assert seen_deep["seeds"] == ["10.1/2a", "10.1/1b"]
    assert out["usage"]["total_tokens"] == 135
    assert "gpt-rate" in out["usage"]["by_model"]


def test_select_seeds_falls_back_to_top3_on_error_or_bad_json(monkeypatch):
    from litloop.agents import runner

    candidates = [
        {"doi": "", "title": "Skip"},
        {"doi": "10.1/1", "title": "T1"},
        {"doi": "10.1/2", "title": "T2"},
        {"doi": "10.1/3", "title": "T3"},
        {"doi": "10.1/4", "title": "T4"},
    ]
    cfg = {"builtin_agent_provider": "openai", "builtin_agent_model": "gpt-main"}

    monkeypatch.setattr(runner, "chat", lambda *a, **k: {"text": "not json at all", "usage": {"input_tokens": 10, "output_tokens": 5}})
    seeds1, usage1 = runner._select_seeds("q", [], candidates, config=cfg)
    assert seeds1 == ["10.1/1", "10.1/2", "10.1/3"]
    assert usage1["total_tokens"] == 15

    def boom(*a, **k):
        raise RuntimeError("network error")

    monkeypatch.setattr(runner, "chat", boom)
    seeds2, usage2 = runner._select_seeds("q", [], candidates, config=cfg)
    assert seeds2 == ["10.1/1", "10.1/2", "10.1/3"]
    assert usage2["total_tokens"] == 0


def test_plan_search_strips_thinking_tags_before_parsing_json(monkeypatch):
    from litloop.agents import runner

    cfg = {"builtin_agent_provider": "openai", "builtin_agent_model": "r1-plan"}
    monkeypatch.setattr(
        runner,
        "chat",
        lambda *a, **k: {
            "text": '<think>先分析一下检索词\n{"ignored": true}</think>\n```json\n{"queries": ["SiC Al wetting", "pressureless infiltration SiC"], "core_terms": ["SiC", "Al|aluminum"], "criteria": ["研究润湿性"]}\n```',
            "tool_calls": [],
            "usage": {"input_tokens": 30, "output_tokens": 20},
        },
    )

    plan = runner.plan_search({"question": "SiC/Al wetting", "mode": "deep"}, config=cfg)
    assert plan["queries"] == ["SiC Al wetting", "pressureless infiltration SiC"]
    assert plan["core_terms"] == ["SiC", "Al|aluminum"]
    assert plan["criteria"] == ["研究润湿性"]
