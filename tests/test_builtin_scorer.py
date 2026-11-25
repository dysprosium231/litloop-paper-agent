"""Tests for built-in agent presets, usage/cost estimation, concurrent scorer, and search pipelines."""

from __future__ import annotations

import json
import threading
from typing import Any

from litloop import config, db
from litloop.agents import (
    PROVIDER_PRESETS,
    collect_user_feedback,
    estimate_cost,
    get_preset,
    normalize_usage,
    run_builtin_resume,
    run_builtin_search,
    run_task,
    score_papers,
)
from litloop.agents import runner, scorer, tools


def test_config_defaults_use_builtin_backend():
    assert config.DEFAULT_CONFIG["agent_backend"] == "builtin"
    assert config.DEFAULT_CONFIG["builtin_agent_preset"] == ""


def test_provider_presets_and_lookup():
    assert len(PROVIDER_PRESETS) == 9
    expected_ids = {
        "deepseek",
        "qwen",
        "kimi",
        "glm",
        "siliconflow",
        "openai",
        "anthropic",
        "openrouter",
        "ollama",
    }
    assert {p["id"] for p in PROVIDER_PRESETS} == expected_ids
    for p in PROVIDER_PRESETS:
        for key in ("id", "label", "protocol", "base_url", "model", "plan_model", "rate_model", "needs_key", "note", "link"):
            assert key in p
    assert get_preset("DeepSeek")["protocol"] == "openai"
    assert get_preset("anthropic")["protocol"] == "anthropic"
    assert get_preset("ollama")["needs_key"] is False
    assert get_preset("not-a-preset") is None


def test_normalize_usage_and_estimate_cost():
    assert normalize_usage(None) == {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
    assert normalize_usage({"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150}) == {
        "input_tokens": 100,
        "output_tokens": 50,
        "total_tokens": 150,
    }
    assert normalize_usage({"input_tokens": 200, "output_tokens": 80}) == {
        "input_tokens": 200,
        "output_tokens": 80,
        "total_tokens": 280,
    }

    cny = estimate_cost({"deepseek-chat": {"input_tokens": 10000, "output_tokens": 1000, "total_tokens": 11000}})
    assert cny["currency"] == "CNY"
    assert cny["estimated"] == 0.028
    assert cny["cost_text"] == "约 ¥0.03"

    usd = estimate_cost({"gpt-4.1-mini": {"input_tokens": 20000, "output_tokens": 1000}})
    assert usd["currency"] == "USD"
    assert usd["estimated"] == 0.0096
    assert usd["cost_text"] == "约 $0.0096"

    mixed = estimate_cost({
        "deepseek-chat": {"input_tokens": 1000, "output_tokens": 100},
        "gpt-4.1": {"input_tokens": 1000, "output_tokens": 100},
    })
    assert mixed["currency"] is None and mixed["estimated"] is None and mixed["cost_text"] is None

    unknown = estimate_cost({"custom-local-llm": {"input_tokens": 500, "output_tokens": 100}})
    assert unknown["currency"] is None and unknown["estimated"] is None and unknown["cost_text"] is None


def test_tools_brief_keeps_abstract_and_journal_rank():
    long_abs = "A" * 1000
    brief = tools._brief({
        "doi": "10.1/a",
        "title": "Paper A",
        "year": 2024,
        "journal": "Acta Mater.",
        "journal_rank": "CAS 1区",
        "abstract": long_abs,
        "abstract_missing": False,
    })
    assert brief["journal_rank"] == "CAS 1区"
    assert brief["abstract_missing"] is False
    assert len(brief["abstract"]) == 800


def test_collect_user_feedback(monkeypatch):
    monkeypatch.setattr(
        db,
        "load_screening",
        lambda session: {
            "items": [
                {"title": "T1", "relevance": "高", "user_relevance": "高", "reason": "r1"},
                {"title": "T2", "relevance": "高", "user_relevance": "不相关", "reason": "r2", "reject_reason": "体系不对"},
                {"title": "T3", "relevance": "中", "user_relevance": "", "reason": "r3", "reject_reason": "缺实验数据"},
                {"title": "T4", "relevance": "低", "user_relevance": "高", "reason": "r4"},
            ]
        },
    )
    fb = collect_user_feedback("sess-1", limit=2)
    assert len(fb) == 2
    assert [x["title"] for x in fb] == ["T3", "T4"]
    assert fb[0]["reject_reason"] == "缺实验数据"
    assert fb[1]["agent_relevance"] == "低" and fb[1]["user_relevance"] == "高"


def test_score_papers_concurrent_retry_and_cancel(monkeypatch):
    cfg = {"builtin_agent_provider": "openai", "builtin_agent_model": "deepseek-chat"}
    prompts: list[str] = []
    lock = threading.Lock()
    first_chunk_calls = 0

    def fake_chat(messages, tools=None, *, config=None, system="", max_tokens=1600, timeout=120, task="rate"):
        nonlocal first_chunk_calls
        assert task == "rate"
        content = messages[0]["content"]
        with lock:
            prompts.append(content)
        # Chunk 1 has [1] and [2]; fail [2] on the first call to verify single retry for missing only.
        if "[1] 标题：" in content and "[2] 标题：" in content:
            with lock:
                first_chunk_calls += 1
            return {
                "text": json.dumps({"ratings": [{"n": 1, "relevance": "高", "reason": "命中核心"}]}),
                "usage": {"prompt_tokens": 100, "completion_tokens": 20},
            }
        if "[2] 标题：" in content and "[1] 标题：" not in content:
            return {
                "text": json.dumps({"ratings": [{"n": 2, "relevance": "中", "reason": "补评命中"}]}),
                "usage": {"prompt_tokens": 50, "completion_tokens": 10},
            }
        # Chunk 2 has [3]
        return {
            "text": '```json\n{"ratings": [{"n": 3, "relevance": "不相关", "reason": "无关"}]}\n```',
            "usage": {"prompt_tokens": 80, "completion_tokens": 15},
        }

    monkeypatch.setattr(scorer, "chat", fake_chat)

    papers = [
        {"title": "P1", "year": 2023, "journal": "J1", "journal_rank": "Q1", "abstract": "X" * 1500},
        {"title": "P2", "year": 2022, "journal": "J2", "abstract": ""},
        {"title": "P3", "year": 2021, "journal": "J3", "abstract": "short abs"},
    ]
    progress_events: list[tuple[int, int]] = []
    res = score_papers(
        "SiC/Al 界面反应",
        ["研究界面微结构"],
        papers,
        config=cfg,
        few_shots=[{"title": "Old", "agent_relevance": "高", "user_relevance": "不相关", "reject_reason": "非渗流"}],
        steering_notes=["排除纯模拟研究"],
        chunk_size=2,
        max_workers=2,
        on_chunk_done=lambda chunk, done, total, cost: progress_events.append((done, total)),
    )
    assert res["cancelled"] is False
    assert res["missing"] == []
    assert [r["n"] for r in res["ratings"]] == [1, 2, 3]
    assert res["ratings"][1]["relevance"] == "中"
    assert res["usage"]["input_tokens"] == 230
    assert res["usage"]["output_tokens"] == 45
    assert progress_events[-1][0] == 3 and progress_events[-1][1] == 3
    assert any("（无摘要）" in p and "排除纯模拟研究" in p and "非渗流" in p for p in prompts)
    assert any(("X" * 1200) in p and ("X" * 1201) not in p for p in prompts)

    # Stop event set before start cancels immediately
    ev = threading.Event()
    ev.set()
    stopped = score_papers("q", [], papers, config=cfg, stop_event=ev)
    assert stopped["cancelled"] is True
    assert stopped["ratings"] == []


def test_plan_search_and_run_task_include_usage(monkeypatch):
    cfg = {
        "builtin_agent_provider": "openai",
        "builtin_agent_model": "deepseek-chat",
        "builtin_agent_plan_model": "deepseek-reasoner",
        "builtin_agent_rate_model": "deepseek-chat",
    }
    monkeypatch.setattr(
        runner,
        "chat",
        lambda *a, **k: {
            "text": '{"queries": ["SiC Al"], "core_terms": ["SiC"], "criteria": ["界面"]}',
            "tool_calls": [],
            "usage": {"prompt_tokens": 300, "completion_tokens": 100},
        },
    )
    plan = runner.plan_search({"question": "SiC/Al"}, config=cfg)
    assert plan["usage"]["input_tokens"] == 300
    assert plan["usage"]["by_model"]["deepseek-reasoner"]["output_tokens"] == 100

    step_idx = 0

    def fake_task_chat(messages, tools, *, config=None, system="", max_tokens=4096, timeout=180, task="default"):
        nonlocal step_idx
        step_idx += 1
        if step_idx == 1:
            return {
                "text": "",
                "tool_calls": [{"id": "c1", "name": "search", "arguments": {"query": "SiC"}}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 20},
            }
        return {
            "text": "完成",
            "tool_calls": [],
            "usage": {"prompt_tokens": 200, "completion_tokens": 50},
        }

    monkeypatch.setattr(runner, "chat", fake_task_chat)
    out = run_task(
        "goal",
        [{"name": "search", "description": "s", "parameters": {"type": "object", "properties": {}}}],
        functions={"search": lambda **kw: {"items": []}},
        config=cfg,
        task_for_step=runner.default_task_for_step,
    )
    assert out["usage"]["input_tokens"] == 300
    assert out["usage"]["output_tokens"] == 70
    assert set(out["usage"]["by_model"].keys()) == {"deepseek-reasoner", "deepseek-chat"}


def test_run_builtin_search_quick_and_deep_and_resume(monkeypatch):
    cfg = {"builtin_agent_provider": "openai", "builtin_agent_model": "deepseek-chat"}
    saved_calls: list[dict[str, Any]] = []
    events: list[tuple[str, dict[str, Any]]] = []

    monkeypatch.setattr(
        runner.quick,
        "search_papers_v2",
        lambda q, **kw: [
            {"doi": "10.1/a", "title": "Paper A", "year": 2024, "journal": "J1", "abstract": "abs A"},
            {"doi": "10.1/b", "title": "Paper B", "year": 2023, "journal": "J2", "abstract": "abs B"},
        ],
    )
    monkeypatch.setattr(
        scorer,
        "chat",
        lambda messages, *a, **k: {
            "text": json.dumps({
                "ratings": [
                    {"n": 1, "relevance": "高", "reason": "fit"},
                    {"n": 2, "relevance": "低", "reason": "weak"},
                ]
            }),
            "usage": {"prompt_tokens": 120, "completion_tokens": 30},
        },
    )

    def fake_save(question, items, **kw):
        saved_calls.append({"question": question, "items": items, **kw})
        return {"session": "20261124-quick-1", "added": len(items), "total": len(items), "by_relevance": {"高": 1, "低": 1}}

    monkeypatch.setattr(runner.screening, "save", fake_save)

    quick_res = run_builtin_search(
        {"question": "SiC/Al", "mode": "quick", "planned_queries": ["SiC Al"], "criteria": ["界面"]},
        config=cfg,
        on_event=lambda k, d: events.append((k, d)),
    )
    assert quick_res["session"] == "20261124-quick-1"
    assert quick_res["cancelled"] is False
    assert quick_res["usage"]["input_tokens"] == 120
    assert len(saved_calls) == 1 and saved_calls[0]["items"][0]["abstract"] == "abs A"
    assert any(k == "progress" and "100" not in d.get("text", "") and "正在评分 2/2 篇" in d.get("text", "") for k, d in events)

    # Deep search with auto-seed + 2 batches
    deep_calls: list[str] = []
    monkeypatch.setattr(
        runner.deep,
        "start",
        lambda q, queries, seeds, core_terms, **kw: (
            deep_calls.append(f"start:{len(seeds)}")
            or {
                "session": "20261124-deep-1",
                "batch": [{"n": 1, "doi": "10.1/a", "title": "Paper A", "abstract": "abs"}],
                "stop_suggested": False,
            }
        ),
    )
    monkeypatch.setattr(
        runner.deep,
        "rate",
        lambda sid, ratings: (
            deep_calls.append(f"rate:{len(ratings)}")
            or {"session": sid, "saved": len(ratings), "total": 1, "by_relevance": {"高": 1}}
        ),
    )
    monkeypatch.setattr(
        runner.deep,
        "next_batch",
        lambda sid: (
            deep_calls.append("next")
            or {
                "session": sid,
                "batch": [{"n": 2, "doi": "10.1/b", "title": "Paper B", "abstract": "abs"}],
                "stop_suggested": True,
            }
        ),
    )

    deep_res = run_builtin_search(
        {
            "question": "SiC/Al",
            "mode": "deep",
            "planned_queries": ["SiC Al"],
            "planned_core_terms": ["SiC"],
            "batches": 3,
        },
        config=cfg,
    )
    assert deep_res["session"] == "20261124-deep-1"
    assert deep_calls == ["start:2", "rate:1", "next", "rate:1"]

    # Resume with unrated papers + feedback
    monkeypatch.setattr(
        runner.deep,
        "status",
        lambda sid: {
            "session": sid,
            "screened": 2,
            "stop_suggested": False,
            "unrated": [{"n": 3, "doi": "10.1/c", "title": "Paper C", "abstract": "abs C"}],
        },
    )
    monkeypatch.setattr(runner.screening, "load", lambda sid: {"question": "SiC/Al", "criteria": ["界面"]})
    monkeypatch.setattr(db, "load_screening", lambda sid: {"items": [{"title": "X", "relevance": "高", "user_relevance": "不相关"}]})
    resume_calls: list[str] = []
    monkeypatch.setattr(
        runner.deep,
        "rate",
        lambda sid, ratings: resume_calls.append(f"rate:{[r['n'] for r in ratings]}") or {"total": 3, "by_relevance": {"高": 2}},
    )
    monkeypatch.setattr(
        runner.deep,
        "next_batch",
        lambda sid: resume_calls.append("next") or {"session": sid, "batch": [], "stop_suggested": True},
    )
    monkeypatch.setattr(
        scorer,
        "chat",
        lambda *a, **k: {
            "text": '{"ratings": [{"n": 3, "relevance": "高", "reason": "ok"}]}',
            "usage": {"prompt_tokens": 90, "completion_tokens": 15},
        },
    )

    resumed = run_builtin_resume("20261124-deep-1", config=cfg)
    assert resumed["session"] == "20261124-deep-1"
    assert resume_calls == ["rate:[3]", "next"]
    assert resumed["usage"]["input_tokens"] == 90


def test_client_retries_429_and_5xx_and_fails_fast_on_401(monkeypatch):
    import pytest
    from litloop.agents import client

    sleeps: list[float] = []
    monkeypatch.setattr(client, "_sleep", lambda sec: sleeps.append(sec))

    class _FakeResp:
        def __init__(self, status_code: int, payload: Any, headers: dict[str, str] | None = None, text: str = ""):
            self.status_code = status_code
            self._payload = payload
            self.headers = headers or {}
            self.text = text or (json.dumps(payload) if isinstance(payload, dict) else "")
            self.reason = f"HTTP {status_code}"
            self.encoding = "utf-8"

        def json(self):
            return self._payload

        def iter_lines(self, decode_unicode: bool = True):
            return iter(self._payload)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    calls = {"n": 0}

    def fake_post(url, headers=None, json=None, timeout=None, proxies=None, stream=False):
        calls["n"] += 1
        if calls["n"] == 1:
            return _FakeResp(429, {}, headers={"Retry-After": "2"}, text="rate limited")
        if calls["n"] == 2:
            return _FakeResp(503, {}, text="bad gateway")
        if stream:
            return _FakeResp(
                200,
                ['data: {"choices":[{"delta":{"content":"ok"}}]}', "data: [DONE]"],
                headers={"content-type": "text/event-stream"},
            )
        return _FakeResp(
            200,
            {"choices": [{"message": {"content": "hello"}, "finish_reason": "stop"}], "usage": {}},
        )

    monkeypatch.setattr(client.requests, "post", fake_post)
    cfg = {"builtin_agent_provider": "openai", "builtin_agent_model": "deepseek-chat", "builtin_agent_api_key": "k"}

    out = client.chat([{"role": "user", "content": "hi"}], config=cfg)
    assert out["text"] == "hello"
    assert calls["n"] == 3
    assert sleeps == [2.0, 2.0]

    # chat_stream also retries 429 before first delta
    calls["n"] = 0
    sleeps.clear()
    deltas: list[str] = []
    s_out = client.chat_stream([{"role": "user", "content": "hi"}], config=cfg, on_delta=deltas.append)
    assert s_out["text"] == "ok" and deltas == ["ok"] and calls["n"] == 3

    # 401 fails immediately without retrying
    calls["n"] = 0
    sleeps.clear()
    monkeypatch.setattr(client.requests, "post", lambda *a, **k: (calls.__setitem__("n", calls["n"] + 1) or _FakeResp(401, {}, text="bad key")))
    with pytest.raises(RuntimeError, match="HTTP 401"):
        client.chat([{"role": "user", "content": "hi"}], config=cfg)
    assert calls["n"] == 1 and sleeps == []
