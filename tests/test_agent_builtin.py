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
    monkeypatch.setattr("litloop.agents.run_task", lambda *a, **k: pytest.fail("no request expected"))
    d = agent_runner.start({"question": "x"})
    assert d["state"] == "failed" and "模型名" in d["error"]


def test_the_session_and_the_answer_come_back(builtin, monkeypatch):
    def fake_run(goal, tools, *, functions, on_event, **kwargs):
        on_event("tool", {"name": "deep_search_start", "arguments": {}, "step": 1})
        on_event("result", {"name": "deep_search_start", "result": '{"session": "20261104-1-x"}', "step": 1})
        on_event("text", {"text": "找到 12 篇。", "step": 2})
        return {"text": "找到 12 篇。", "steps": 2, "tool_calls": []}

    monkeypatch.setattr("litloop.agents.run_task", fake_run)
    run = agent_runner.start({"question": "x"})["run"]
    d = _wait(run)
    assert d["state"] == "done" and d["session"] == "20261104-1-x"
    assert d["result"] == "找到 12 篇。"
    assert [e["text"] for e in d["events"]][0] == "深度检索：正在构建候选池"


def test_the_model_gets_the_rules_and_a_step_budget_that_fits_the_batches(builtin, monkeypatch):
    seen = {}

    def fake_run(goal, tools, *, functions, on_event, **kwargs):
        seen.update(kwargs)
        seen["goal"] = goal
        return {"text": "完成", "steps": 1, "tool_calls": []}

    monkeypatch.setattr("litloop.agents.run_task", fake_run)
    _wait(agent_runner.start({"question": "x", "mode": "deep", "batches": 3})["run"])
    _idle()
    assert "相关度" in seen["system"] and "高 / 中 / 低 / 不相关" in seen["system"]
    assert seen["max_steps"] == 10  # 4 + 2 per batch
    seen.clear()
    _wait(agent_runner.start({"question": "y"})["run"])
    _idle()
    assert seen["max_steps"] == 8  # a quick search does not need more


def test_the_builtin_prompt_does_not_point_at_a_skill_it_cannot_read():
    assert agent_runner.build_prompt({"question": "x"}).startswith("用 litloop 技能")
    without = agent_runner.build_prompt({"question": "x", "mode": "deep"}, skill=False)
    assert "技能" not in without and without.startswith("做深度检索")


def test_a_failing_model_fails_the_run_with_its_message(builtin, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("HTTP 401: bad key")

    monkeypatch.setattr("litloop.agents.run_task", boom)
    d = _wait(agent_runner.start({"question": "x"})["run"])
    assert d["state"] == "failed" and "HTTP 401" in d["error"]


def test_cancel_stops_a_builtin_run(builtin, monkeypatch):
    started = threading.Event()
    release = threading.Event()

    def slow_run(goal, tools, *, functions, on_event, **kwargs):
        started.set()
        while not release.is_set():
            time.sleep(0.02)
            on_event("tool", {"name": "search", "arguments": {}, "step": 1})
        return {"text": "查完了", "steps": 1, "tool_calls": []}

    monkeypatch.setattr("litloop.agents.run_task", slow_run)
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
    monkeypatch.setattr("litloop.agents.run_task", lambda *a, **k: {"text": "好了", "steps": 1, "tool_calls": []})
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

    def fake_run(goal, tools, *, functions, on_event, **kwargs):
        seen.update(goal=goal, system=kwargs.get("system", ""))
        started.set()
        release.wait(2.0)
        return {"text": "续跑完成", "steps": 2, "tool_calls": []}

    monkeypatch.setattr("litloop.agents.run_task", fake_run)
    run = agent_runner.resume(sid)["run"]
    assert started.wait(2.0)
    with pytest.raises(RuntimeError, match="已有检索在进行"):
        agent_runner.resume(sid)
    release.set()
    d = _wait(run)
    _idle()
    assert d["state"] == "done" and d["resumed_from"] == sid and d["session"] == sid
    assert "deep_search_status" in seen["goal"] and "deep_search_rate" in seen["system"]


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
