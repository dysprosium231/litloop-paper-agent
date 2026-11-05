"""Searches run by the app's own agent (backend B) instead of an agent command."""

from __future__ import annotations

import threading
import time

import pytest

from litloop import config
from litloop.desktop import agent_runner


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
