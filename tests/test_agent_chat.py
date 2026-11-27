"""Tests for SSE streaming client and conversational chat agent engine."""

from __future__ import annotations

import io
import json
from types import SimpleNamespace

import pytest
import requests

from litloop import config, db
from litloop.agents import chat_agent, client


@pytest.fixture(autouse=True)
def tmp_data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(client, "_CURL_HOSTS", set())
    return tmp_path


class _FakeResponse:
    def __init__(
        self,
        *,
        status_code: int = 200,
        headers: dict[str, str] | None = None,
        lines: list[str] | None = None,
        json_data: dict | None = None,
        text: str = "",
        reason: str = "OK",
    ) -> None:
        self.status_code = status_code
        self.headers = headers or {"content-type": "text/event-stream"}
        self._lines = lines or []
        self._json = json_data
        self.text = text
        self.reason = reason
        self.encoding = "utf-8"

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def iter_lines(self, decode_unicode: bool = True):
        for line in self._lines:
            yield line if decode_unicode else line.encode("utf-8")

    def json(self):
        return self._json


def test_chat_stream_openai_sse_text_and_tool_calls(monkeypatch):
    captured: dict = {}
    sse_lines = [
        'data: {"choices":[{"delta":{"content":"你好"}}]}',
        "",
        'data: {"choices":[{"delta":{"content":"，世界"}}]}',
        'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"call_1","function":{"name":"list_","arguments":"{\\"limit\\":"}}]}}]}',
        'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"name":"papers","arguments":" 5}"}}]},"finish_reason":"tool_calls"}]}',
        'data: {"choices":[],"usage":{"prompt_tokens":12,"completion_tokens":8,"total_tokens":20}}',
        "data: [DONE]",
    ]

    def fake_post(url, headers=None, json=None, timeout=None, proxies=None, stream=None):
        captured["url"] = url
        captured["body"] = json
        captured["stream"] = stream
        return _FakeResponse(lines=sse_lines)

    monkeypatch.setattr(requests, "post", fake_post)
    deltas: list[str] = []
    cfg = {
        "builtin_agent_provider": "openai",
        "builtin_agent_base_url": "https://api.example.com/v1",
        "builtin_agent_api_key": "sk-test",
        "builtin_agent_model": "gpt-4.1",
    }
    res = client.chat_stream(
        [{"role": "user", "content": "hi"}],
        config=cfg,
        on_delta=deltas.append,
    )

    assert captured["stream"] is True
    assert captured["body"]["stream"] is True
    assert captured["body"]["stream_options"] == {"include_usage": True}
    assert deltas == ["你好", "，世界"]
    assert res["text"] == "你好，世界"
    assert res["stop"] == "tool_calls"
    assert res["usage"] == {"prompt_tokens": 12, "completion_tokens": 8, "total_tokens": 20}
    assert res["tool_calls"] == [{"id": "call_1", "name": "list_papers", "arguments": {"limit": 5}}]


def test_chat_stream_anthropic_sse_text_and_tool_calls(monkeypatch):
    sse_lines = [
        "event: message_start",
        'data: {"type":"message_start","message":{"usage":{"input_tokens":30,"output_tokens":1}}}',
        "",
        "event: content_block_start",
        'data: {"type":"content_block_start","index":0,"content_block":{"type":"text","text":""}}',
        "event: content_block_delta",
        'data: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"正在查询"}}',
        "event: content_block_stop",
        'data: {"type":"content_block_stop","index":0}',
        "event: content_block_start",
        'data: {"type":"content_block_start","index":1,"content_block":{"type":"tool_use","id":"tu_1","name":"lookup_glossary"}}',
        "event: content_block_delta",
        'data: {"type":"content_block_delta","index":1,"delta":{"type":"input_json_delta","partial_json":"{\\"query\\":"}}',
        "event: content_block_delta",
        'data: {"type":"content_block_delta","index":1,"delta":{"type":"input_json_delta","partial_json":"\\"SiC\\"}"}}',
        "event: content_block_stop",
        'data: {"type":"content_block_stop","index":1}',
        "event: message_delta",
        'data: {"type":"message_delta","delta":{"stop_reason":"tool_use"},"usage":{"output_tokens":15}}',
        "event: message_stop",
        'data: {"type":"message_stop"}',
    ]

    monkeypatch.setattr(requests, "post", lambda *a, **kw: _FakeResponse(lines=sse_lines))
    deltas: list[str] = []
    cfg = {
        "builtin_agent_provider": "anthropic",
        "builtin_agent_base_url": "https://api.anthropic.com",
        "builtin_agent_api_key": "sk-ant-test",
        "builtin_agent_model": "claude-sonnet-4-5",
    }
    res = client.chat_stream(
        [{"role": "user", "content": "查术语"}],
        config=cfg,
        on_delta=deltas.append,
    )

    assert deltas == ["正在查询"]
    assert res["text"] == "正在查询"
    assert res["stop"] == "tool_use"
    assert res["usage"] == {"input_tokens": 30, "output_tokens": 15}
    assert res["tool_calls"] == [{"id": "tu_1", "name": "lookup_glossary", "arguments": {"query": "SiC"}}]


def test_chat_stream_json_fallback(monkeypatch):
    payload = {
        "choices": [
            {
                "message": {"content": "非流式降级回复", "tool_calls": []},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 5, "completion_tokens": 7, "total_tokens": 12},
    }
    monkeypatch.setattr(
        requests,
        "post",
        lambda *a, **kw: _FakeResponse(
            headers={"content-type": "application/json"},
            json_data=payload,
        ),
    )
    deltas: list[str] = []
    cfg = {
        "builtin_agent_provider": "openai",
        "builtin_agent_base_url": "https://api.example.com/v1",
        "builtin_agent_api_key": "sk-test",
        "builtin_agent_model": "gpt-4.1",
    }
    res = client.chat_stream([{"role": "user", "content": "hi"}], config=cfg, on_delta=deltas.append)
    assert deltas == ["非流式降级回复"]
    assert res["text"] == "非流式降级回复"
    assert res["stop"] == "stop"


def test_chat_stream_curl_fallback(monkeypatch):
    def timeout_post(*a, **kw):
        raise requests.Timeout("timed out")

    monkeypatch.setattr(requests, "post", timeout_post)
    monkeypatch.setattr(client, "_curl_bin", lambda: "curl")

    class _FakeProc:
        def __init__(self, cmd, **kwargs):
            # Write HTTP 200 header to -D file
            if "-D" in cmd:
                hpath = cmd[cmd.index("-D") + 1]
                with open(hpath, "w", encoding="utf-8") as fh:
                    fh.write("HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\n\r\n")
            self.stdin = io.StringIO()
            self.stdout = io.StringIO(
                'data: {"choices":[{"delta":{"content":"来自 curl"}}]}\n'
                'data: {"choices":[{"finish_reason":"stop"}]}\n'
                "data: [DONE]\n"
            )
            self.stderr = io.StringIO("")
            self.returncode = 0

        def wait(self):
            return 0

    monkeypatch.setattr(client.subprocess, "Popen", _FakeProc)
    deltas: list[str] = []
    cfg = {
        "builtin_agent_provider": "openai",
        "builtin_agent_base_url": "https://api.example.com/v1",
        "builtin_agent_api_key": "sk-test",
        "builtin_agent_model": "gpt-4.1",
    }
    res = client.chat_stream([{"role": "user", "content": "hi"}], config=cfg, on_delta=deltas.append)
    assert deltas == ["来自 curl"]
    assert res["text"] == "来自 curl"
    assert "api.example.com" in client._CURL_HOSTS


def test_stream_chat_turn_context_readonly_and_propose(monkeypatch):
    saved = db.save_screening(
        "SiC/Al 无压浸渗",
        [
            {
                "n": 1,
                "doi": "10.1000/p1",
                "title": "Pressureless infiltration of SiC/Al",
                "year": 2024,
                "journal": "Acta Mater.",
                "relevance": "中",
                "reason": "直接研究浸渗温度",
                "abstract": "Detailed abstract",
            }
        ],
        criteria=["包含制备工艺参数"],
        mode="deep",
    )
    sid = saved["session"]
    with db.transaction() as conn:
        conn.execute(
            "UPDATE decisions SET relevance = '中', user_relevance = '高', reason = '直接研究浸渗温度' WHERE session_id = ?",
            (sid,),
        )
    db.save_summary(sid, {"session": sid, "pool": 120, "screened": 1, "remaining": 119})
    db.add_steering_note(sid, "重点关注 800-900C 工艺")

    seen_systems: list[str] = []
    round_idx = 0

    def fake_chat_stream(messages, tools=None, *, config=None, system="", max_tokens=2048, timeout=120, task="default", on_delta=None):
        nonlocal round_idx
        round_idx += 1
        seen_systems.append(system)
        if round_idx == 1:
            return {
                "text": "",
                "tool_calls": [{"id": "c1", "name": "list_papers", "arguments": {"relevance": "高"}}],
                "stop": "tool_calls",
                "usage": {"input_tokens": 20, "output_tokens": 10},
            }
        if round_idx == 2:
            if on_delta:
                on_delta("已查看高相关文献，")
            return {
                "text": "已查看高相关文献，",
                "tool_calls": [
                    {
                        "id": "c2",
                        "name": "propose_start_search",
                        "arguments": {"question": "SiC/Al wetting", "mode": "deep", "batches": 2},
                    }
                ],
                "stop": "tool_calls",
                "usage": {"input_tokens": 30, "output_tokens": 15},
            }
        if on_delta:
            on_delta("请点击下方卡片确认发起检索。")
        return {
            "text": "请点击下方卡片确认发起检索。",
            "tool_calls": [],
            "stop": "stop",
            "usage": {"input_tokens": 40, "output_tokens": 10},
        }

    monkeypatch.setattr(chat_agent, "chat_stream", fake_chat_stream)
    cfg = {
        "builtin_agent_provider": "openai",
        "builtin_agent_base_url": "https://api.example.com/v1",
        "builtin_agent_api_key": "sk-test",
        "builtin_agent_model": "gpt-4.1",
    }
    long_abs = "A" * 2000
    events = list(
        chat_agent.stream_chat_turn(
            sid,
            "帮我看看当前高相关文献并准备下一轮检索",
            view={
                "current_paper": {
                    "title": "Focused Paper",
                    "year": 2025,
                    "doi": "10.1000/focus",
                    "relevance": "高",
                    "abstract": long_abs,
                }
            },
            config=cfg,
        )
    )

    # Context injection check
    assert "SiC/Al 无压浸渗" in seen_systems[0]
    assert "重点关注 800-900C 工艺" in seen_systems[0]
    assert "Focused Paper" in seen_systems[0]
    assert ("A" * 1500 + "…") in seen_systems[0]
    assert ("A" * 1501) not in seen_systems[0]

    types = [e["type"] for e in events]
    assert types[0] == "user_message"
    assert "tool" in types
    assert "action" in types
    assert "delta" in types
    assert types[-1] == "done"

    action_ev = next(e for e in events if e["type"] == "action")
    assert action_ev["action"]["kind"] == "start_search"
    assert action_ev["action"]["status"] == "pending"

    done_ev = events[-1]
    assert done_ev["message"]["content"] == "已查看高相关文献，请点击下方卡片确认发起检索。"
    assert len(done_ev["message"]["actions"]) == 1
    assert done_ev["message"]["usage"]["total_tokens"] == 125


def test_execute_action_all_five_kinds_and_status_transitions(monkeypatch):
    from litloop.desktop import agent_runner, download_jobs
    from litloop.terms import glossary

    calls: list[tuple[str, object]] = []
    monkeypatch.setattr(agent_runner, "start", lambda form: (calls.append(("start", form)), {"run": "run-1"})[1])
    monkeypatch.setattr(agent_runner, "resume", lambda sess, batches=0: (calls.append(("resume", (sess, batches))), {"run": "run-2"})[1])
    monkeypatch.setattr(download_jobs, "start", lambda papers, title, session=None: (calls.append(("download", (papers, title, session))), {"job": "job-1"})[1])
    monkeypatch.setattr(download_jobs, "push_zotero", lambda job, collection=None: (calls.append(("zotero", (job, collection))), {"ok": True})[1])
    monkeypatch.setattr(glossary, "add", lambda terms, note="": (calls.append(("glossary", (terms, note))), {"id": "g1", "terms": terms})[1])

    sid = "sess-exec-1"
    actions = [
        {"id": "a1", "kind": "start_search", "title": "发起", "summary": "", "params": {"question": "Q", "mode": "deep"}, "status": "pending", "result": None},
        {"id": "a2", "kind": "resume_search", "title": "续跑", "summary": "", "params": {"session": sid, "batches": 2, "steering_note": "排除纯模拟"}, "status": "pending", "result": None},
        {"id": "a3", "kind": "download", "title": "下载", "summary": "", "params": {"papers": [{"doi": "10.1/x", "title": "X"}], "title": "DL"}, "status": "pending", "result": None},
        {"id": "a4", "kind": "zotero_push", "title": "Zotero", "summary": "", "params": {"job": "job-1", "collection": "MyCol"}, "status": "pending", "result": None},
        {"id": "a5", "kind": "add_glossary", "title": "术语", "summary": "", "params": {"terms": ["SiC", "silicon carbide"], "note": "陶瓷"}, "status": "pending", "result": None},
    ]
    msg = db.add_agent_message(sid, "assistant", "请确认", actions=actions)

    r1 = chat_agent.execute_action(sid, msg["id"], "a1", edited_params={"batches": 4})
    assert r1["ok"] is True and r1["action"]["status"] == "confirmed"
    assert calls[0] == ("start", {"question": "Q", "mode": "deep", "batches": 4})

    # Re-running confirmed action raises ValueError
    with pytest.raises(ValueError):
        chat_agent.execute_action(sid, msg["id"], "a1")

    r2 = chat_agent.execute_action(sid, msg["id"], "a2")
    assert r2["ok"] is True and r2["action"]["status"] == "confirmed"
    assert [n["note"] for n in db.list_steering_notes(sid)] == ["排除纯模拟"]

    r3 = chat_agent.execute_action(sid, msg["id"], "a3")
    assert r3["ok"] is True and r3["action"]["status"] == "confirmed"

    r4 = chat_agent.execute_action(sid, msg["id"], "a4")
    assert r4["ok"] is True and r4["action"]["status"] == "confirmed"

    r5 = chat_agent.execute_action(sid, msg["id"], "a5")
    assert r5["ok"] is True and r5["action"]["status"] == "confirmed"

    # Failure transitions status to failed
    def fail_start(form):
        raise RuntimeError("已有检索在进行")

    monkeypatch.setattr(agent_runner, "start", fail_start)
    msg2 = db.add_agent_message(
        sid,
        "assistant",
        "再试一次",
        actions=[{"id": "a6", "kind": "start_search", "title": "发起", "summary": "", "params": {"question": "Q2"}, "status": "pending", "result": None}],
    )
    r6 = chat_agent.execute_action(sid, msg2["id"], "a6")
    assert r6["ok"] is False
    assert r6["action"]["status"] == "failed"
    assert "已有检索在进行" in r6["error"]


def test_list_papers_decision_aliases_and_history_budget_and_action_mapping(monkeypatch):
    from litloop.desktop import agent_runner
    from litloop.search import screening

    saved = screening.save(
        "SiC/Al",
        [
            {"doi": "10.1/sel", "title": "Selected Paper", "relevance": "高", "reason": "ok"},
            {"doi": "10.1/rej", "title": "Rejected Paper", "relevance": "低", "reason": "no"},
            {"doi": "10.1/und", "title": "Undecided Paper", "relevance": "中", "reason": "maybe"},
        ],
        mode="deep",
    )
    sid = saved["session"]
    db.update_item_decision(sid, "10.1/sel", "Selected Paper", "selected")
    db.update_item_decision(sid, "10.1/rej", "Rejected Paper", "rejected")

    inc = json.loads(chat_agent._exec_readonly_tool("list_papers", {"decision": "include"}, sid, None))
    sel = json.loads(chat_agent._exec_readonly_tool("list_papers", {"decision": "selected"}, sid, None))
    und = json.loads(chat_agent._exec_readonly_tool("list_papers", {"decision": "undecided"}, sid, None))
    assert [p["doi"] for p in inc["papers"]] == ["10.1/sel"]
    assert inc == sel
    assert [p["doi"] for p in und["papers"]] == ["10.1/und"]

    # History budget trimming & tool output truncation
    trimmed = chat_agent._trim_history(
        [{"role": "user", "text": "A" * 7000}, {"role": "assistant", "text": "B" * 7000}, {"role": "user", "text": "latest"}],
        max_chars=12000,
    )
    assert [m["text"] for m in trimmed] == ["B" * 7000, "latest"]
    long_out = chat_agent._truncate_tool_result("X" * 8050)
    assert long_out.endswith("\n...(已截断)") and len(long_out) < 8020

    # Draft start_search maps queries/core_terms and sets from_draft; cross-session resume writes note to target_session
    started_forms: list[dict[str, Any]] = []
    resumed_calls: list[tuple[str, int]] = []
    monkeypatch.setattr(agent_runner, "start", lambda form: started_forms.append(form) or {"run": "r-draft", "state": "running"})
    monkeypatch.setattr(agent_runner, "resume", lambda target, batches=0: resumed_calls.append((target, batches)) or {"run": "r-res", "state": "running"})

    draft_msg = db.add_agent_message(
        "__draft__",
        "assistant",
        "确认",
        actions=[
            {
                "id": "d1",
                "kind": "start_search",
                "title": "发起",
                "summary": "",
                "params": {"question": "Q", "queries": ["q1", "q2"], "core_terms": ["SiC"]},
                "status": "pending",
                "result": None,
            },
            {
                "id": "d2",
                "kind": "resume_search",
                "title": "续跑",
                "summary": "",
                "params": {"session": sid, "batches": 2, "steering_note": "跨会话纠偏"},
                "status": "pending",
                "result": None,
            },
        ],
    )
    res_start = chat_agent.execute_action("__draft__", draft_msg["id"], "d1")
    assert res_start["ok"] is True
    assert started_forms[0]["planned_queries"] == ["q1", "q2"]
    assert started_forms[0]["planned_core_terms"] == ["SiC"]
    assert started_forms[0]["from_draft"] is True

    res_resume = chat_agent.execute_action("__draft__", draft_msg["id"], "d2")
    assert res_resume["ok"] is True
    assert resumed_calls == [(sid, 2)]
    assert [n["note"] for n in db.list_steering_notes(sid)] == ["跨会话纠偏"]
    assert db.list_steering_notes("__draft__") == []


def test_stream_chat_turn_reasoning_sse_and_legacy_compat(monkeypatch):
    def fake_stream_with_reasoning(
        messages,
        tools=None,
        *,
        config=None,
        system="",
        max_tokens=2048,
        timeout=120,
        task="default",
        on_delta=None,
        on_reasoning=None,
    ):
        assert callable(on_reasoning)
        on_reasoning("先分析文献相关性，")
        on_reasoning("再给出结论。")
        if on_delta:
            on_delta("建议保留第 1 篇。")
        return {
            "text": "建议保留第 1 篇。",
            "tool_calls": [],
            "stop": "stop",
            "usage": {"input_tokens": 10, "output_tokens": 5},
        }

    monkeypatch.setattr(chat_agent, "chat_stream", fake_stream_with_reasoning)
    cfg = {
        "builtin_agent_provider": "openai",
        "builtin_agent_base_url": "https://api.example.com/v1",
        "builtin_agent_api_key": "sk-test",
        "builtin_agent_model": "deepseek-reasoner",
    }
    events = list(chat_agent.stream_chat_turn("sess-r1", "帮我分析", config=cfg))
    reasonings = [e["text"] for e in events if e["type"] == "reasoning"]
    deltas = [e["text"] for e in events if e["type"] == "delta"]
    assert reasonings == ["先分析文献相关性，", "再给出结论。"]
    assert deltas == ["建议保留第 1 篇。"]
    assert events[-1]["type"] == "done"
    assert events[-1]["message"]["content"] == "建议保留第 1 篇。"

    # Legacy fake chat_stream without on_reasoning parameter must still work
    def fake_stream_legacy(messages, tools=None, *, config=None, system="", max_tokens=2048, timeout=120, on_delta=None):
        if on_delta:
            on_delta("兼容旧签名。")
        return {
            "text": "兼容旧签名。",
            "tool_calls": [],
            "stop": "stop",
            "usage": {"input_tokens": 5, "output_tokens": 3},
        }

    monkeypatch.setattr(chat_agent, "chat_stream", fake_stream_legacy)
    legacy_events = list(chat_agent.stream_chat_turn("sess-r1", "再问一次", config=cfg))
    assert [e["text"] for e in legacy_events if e["type"] == "delta"] == ["兼容旧签名。"]
    assert legacy_events[-1]["type"] == "done"


def test_propose_batch_screen_card_and_execute_action():
    saved = db.save_screening(
        "SiC/Al 浸渗工艺",
        [
            {"n": 1, "doi": "10.1/p1", "title": "Paper One", "relevance": "中", "reason": "r1"},
            {"n": 2, "doi": "10.1/p2", "title": "Paper Two", "relevance": "中", "reason": "r2"},
        ],
        mode="deep",
    )
    sid = saved["session"]
    db.update_item_decision(sid, "10.1/p2", "Paper Two", "selected")

    card = chat_agent._build_action_card(
        "propose_batch_screen",
        {
            "summary": "调高工艺文献并排除综述",
            "items": [
                {"n": 1, "relevance": "高", "decision": "selected", "note": "含具体温度参数"},
                {"n": 2, "relevance": "低", "decision": "rejected", "reject_reason": "仅综述"},
                {"n": 999, "relevance": "高"},
            ],
        },
        sid,
    )
    assert card["kind"] == "batch_screen"
    assert card["title"] == "批量调整筛选结果"
    assert "共 2 篇" in card["summary"]
    assert "调高工艺文献并排除综述" in card["summary"]
    assert card["params"]["session"] == sid
    items = card["params"]["items"]
    assert [it["n"] for it in items] == [1, 2]
    assert items[0]["doi"] == "10.1/p1"
    assert items[0]["title"] == "Paper One"
    assert items[0]["cur_relevance"] == "中"
    assert items[0]["cur_decision"] is None
    assert items[1]["doi"] == "10.1/p2"
    assert items[1]["cur_decision"] == "selected"

    # Empty items fails cleanly
    msg_empty = db.add_agent_message(sid, "assistant", "批量调整", actions=[card])
    res_empty = chat_agent.execute_action(sid, msg_empty["id"], card["id"], edited_params={"items": []})
    assert res_empty["ok"] is False
    assert res_empty["action"]["status"] == "failed"
    assert "请至少保留 1 篇待调整的文献" in res_empty["error"]

    # Partial selection persists only checked items to db.load_screening()
    card2 = chat_agent._build_action_card(
        "propose_batch_screen",
        {"items": [{"n": 1, "relevance": "高", "decision": "selected"}, {"n": 2, "relevance": "低"}]},
        sid,
    )
    msg_ok = db.add_agent_message(sid, "assistant", "批量调整", actions=[card2])
    res_ok = chat_agent.execute_action(
        sid,
        msg_ok["id"],
        card2["id"],
        edited_params={"items": [card2["params"]["items"][0]]},
    )
    assert res_ok["ok"] is True
    assert res_ok["result"] == {"updated": 1, "count": 1, "message": "已更新 1 篇筛选记录"}
    assert res_ok["action"]["status"] == "confirmed"

    loaded = {it["n"]: it for it in db.load_screening(sid)["items"]}
    assert loaded[1].get("user_relevance") == "高"
    assert loaded[1].get("decision") == "selected"
    assert loaded[2].get("user_relevance") is None
    assert loaded[2].get("decision") == "selected"


def test_download_jobs_retry_failed_and_get_download_status(monkeypatch):
    from litloop import store
    from litloop.desktop import download_jobs

    job_done_only = {
        "schema": 1,
        "job": "20261126-100000",
        "title": "All Done",
        "session": "sess-dl-1",
        "state": "done",
        "created": "2026-11-26 10:00:00",
        "finished": "2026-11-26 10:01:00",
        "items": [
            {"doi": "10.1/ok", "title": "OK Paper", "status": "done", "file": "/tmp/ok.pdf"},
        ],
    }
    store.write_json(download_jobs.job_path("20261126-100000"), job_done_only)
    with pytest.raises(ValueError, match="没有下载失败的文献"):
        download_jobs.retry_failed("20261126-100000")

    job_mixed = {
        "schema": 1,
        "job": "20261126-110000",
        "title": "SiC 文献",
        "session": "sess-dl-1",
        "zotero_target": "LitLoop/SiC",
        "state": "queued",
        "created": "2026-11-26 11:00:00",
        "finished": None,
        "items": [
            {"doi": "10.1/ok", "title": "OK", "status": "done", "file": "/tmp/ok.pdf"},
            {
                "doi": "10.1/fail1",
                "title": "Fail One",
                "year": 2024,
                "journal": "Acta",
                "status": "failed",
                "error": "http_403",
                "error_text": "Forbidden",
                "error_hint": "需机构权限",
            },
            {"doi": "10.1/q1", "title": "Queued One", "publisher": "Elsevier", "status": "queued"},
        ],
        "zotero": {"state": "done", "collection": "LitLoop/SiC"},
    }
    store.write_json(download_jobs.job_path("20261126-110000"), job_mixed)

    captured: list[tuple[Any, ...]] = []
    monkeypatch.setattr(
        download_jobs,
        "start",
        lambda papers, title, session=None, zotero_target=None: (
            captured.append((papers, title, session, zotero_target)),
            {"job": "20261126-110500", "items": papers, "title": title, "session": session},
        )[1],
    )
    retried = download_jobs.retry_failed("20261126-110000")
    assert retried["job"] == "20261126-110500"
    assert captured == [
        (
            [{"doi": "10.1/fail1", "title": "Fail One", "year": 2024, "journal": "Acta"}],
            "SiC 文献（重试）",
            "sess-dl-1",
            "LitLoop/SiC",
        )
    ]

    # get_download_status with explicit job
    by_job = json.loads(
        chat_agent._exec_readonly_tool("get_download_status", {"job": "20261126-110000"}, "sess-dl-1", None)
    )
    assert by_job["counts"] == {"total": 3, "done": 1, "failed": 1, "queued": 1, "waiting": 0}
    assert by_job["queues"] == [{"publisher": "Elsevier", "count": 1}]
    assert by_job["failed_items"] == [
        {
            "doi": "10.1/fail1",
            "title": "Fail One",
            "error": "http_403",
            "error_text": "Forbidden",
            "error_hint": "需机构权限",
        }
    ]
    assert by_job["zotero"] == {"state": "done", "collection": "LitLoop/SiC"}

    # get_download_status by session vs fallback
    by_sess = json.loads(chat_agent._exec_readonly_tool("get_download_status", {}, "sess-dl-1", None))
    assert by_sess["session_matched"] is True
    assert {j["job"] for j in by_sess["jobs"]} == {"20261126-100000", "20261126-110000"}

    by_other = json.loads(chat_agent._exec_readonly_tool("get_download_status", {}, "sess-other", None))
    assert by_other["session_matched"] is False
    assert len(by_other["jobs"]) == 2


def test_get_zotero_status_and_graceful_degradation(monkeypatch):
    from litloop.desktop import download_jobs
    from litloop.zotero import client as zotero_client

    saved = db.save_screening("SiC/Al 界面润湿性", [{"n": 1, "doi": "10.1/a", "title": "A", "relevance": "高"}], mode="deep")
    sid = saved["session"]

    monkeypatch.setattr(zotero_client, "discover_zotero_mcp", lambda cfg: {"cmd": ["zotero-mcp"]})
    monkeypatch.setattr(download_jobs, "zotero_pending", lambda: [{"job": "j1", "papers": [{"doi": "10.1/a"}]}])
    monkeypatch.setattr(download_jobs, "zotero_history", lambda: [{"job": "j0", "state": "done"}])
    monkeypatch.setattr(
        zotero_client,
        "zotero_collections",
        lambda cfg: {"success": True, "collections": [{"path": "LitLoop/SiC", "name": "SiC"}]},
    )

    res = json.loads(chat_agent._exec_readonly_tool("get_zotero_status", {"include_collections": True}, sid, {}))
    assert res["mcp_found"] is True
    assert res["pending"] == [{"job": "j1", "papers": [{"doi": "10.1/a"}]}]
    assert res["history"] == [{"job": "j0", "state": "done"}]
    assert res["recommended_collection"].startswith("LitLoop/")
    assert res["collections"] == ["LitLoop/SiC"]

    # Graceful degradation when zotero_collections raises
    def fail_cols(cfg):
        raise RuntimeError("bridge timeout")

    monkeypatch.setattr(zotero_client, "zotero_collections", fail_cols)
    res_err = json.loads(chat_agent._exec_readonly_tool("get_zotero_status", {}, sid, {}))
    assert res_err["collections"] == []
    assert "bridge timeout" in res_err["collections_error"]


def test_propose_download_selection_dedup_and_retry_download_and_context(monkeypatch):
    from litloop import store
    from litloop.desktop import download_jobs

    saved = db.save_screening(
        "SiC 润湿性",
        [
            {"n": 1, "doi": "10.48550/arXiv.2401.00001", "title": "Already Downloaded arXiv", "relevance": "高"},
            {"n": 2, "doi": "10.1/new1", "title": "New High Paper", "relevance": "高", "year": 2024, "journal": "Acta"},
            {"n": 3, "doi": "", "title": "No DOI Paper", "relevance": "高"},
            {"n": 4, "doi": "10.1/med1", "title": "Medium Paper", "relevance": "中"},
        ],
        mode="deep",
    )
    sid = saved["session"]
    db.update_item_decision(sid, "10.1/new1", "New High Paper", "selected")

    # Mark bare arXiv id as already downloaded; same_paper should match 10.48550/arXiv.2401.00001
    monkeypatch.setattr(download_jobs, "downloaded_dois", lambda: {"2401.00001"})

    card_high = chat_agent._build_action_card(
        "propose_download",
        {"selection": "high", "skip_downloaded": True},
        sid,
    )
    assert card_high["kind"] == "download"
    assert card_high["params"]["session"] == sid
    assert card_high["params"]["skipped_downloaded"] == 1
    assert card_high["params"]["missing_doi"] == 1
    assert [p["doi"] for p in card_high["params"]["papers"]] == ["10.1/new1"]
    assert card_high["params"]["preview_titles"] == ["New High Paper"]
    assert "跳过已有 1 篇" in card_high["summary"]
    assert "缺 DOI 1 篇" in card_high["summary"]

    # paper_ns selection without skipping downloaded
    card_ns = chat_agent._build_action_card(
        "propose_download",
        {"paper_ns": [1, 4], "skip_downloaded": False, "title": "自选下载"},
        sid,
    )
    assert [p["doi"] for p in card_ns["params"]["papers"]] == ["10.48550/arXiv.2401.00001", "10.1/med1"]
    assert card_ns["params"]["skipped_downloaded"] == 0

    # retry_download card & execution (failed + publisher_queue)
    job_data = {
        "schema": 1,
        "job": "20261126-120000",
        "title": "批量下载",
        "session": sid,
        "state": "queued",
        "created": "2026-11-26 12:00:00",
        "items": [
            {"doi": "10.1/f1", "title": "F1", "status": "failed"},
            {"doi": "10.1/f2", "title": "F2", "status": "failed"},
            {"doi": "10.1/q1", "title": "Q1", "publisher": "Elsevier", "status": "queued"},
            {"doi": "10.1/d1", "title": "D1", "status": "done", "file": __file__},
        ],
    }
    store.write_json(download_jobs.job_path("20261126-120000"), job_data)

    ctx = chat_agent.build_context_block(sid)
    assert "20261126-120000" in ctx
    assert "待推送 Zotero：1 篇" in ctx

    card_retry = chat_agent._build_action_card("propose_retry_download", {"job": "20261126-120000"}, sid)
    assert card_retry["kind"] == "retry_download"
    assert card_retry["params"] == {"job": "20261126-120000", "mode": "failed", "count": 2}
    assert "失败文献重试（2 篇）" in card_retry["summary"]

    card_queue = chat_agent._build_action_card(
        "propose_retry_download",
        {"job": "20261126-120000", "mode": "publisher_queue", "publisher": "Elsevier"},
        sid,
    )
    assert card_queue["params"] == {
        "job": "20261126-120000",
        "mode": "publisher_queue",
        "count": 1,
        "publisher": "Elsevier",
    }

    monkeypatch.setattr(
        download_jobs,
        "retry_failed",
        lambda job: {"job": "20261126-120500", "items": [{"doi": "10.1/f1"}, {"doi": "10.1/f2"}]},
    )
    monkeypatch.setattr(
        download_jobs,
        "start_queue",
        lambda job, pub: {"job": job, "state": "running"},
    )
    msg = db.add_agent_message(sid, "assistant", "重试", actions=[card_retry, card_queue])
    r_fail = chat_agent.execute_action(sid, msg["id"], card_retry["id"])
    assert r_fail["ok"] is True
    assert r_fail["result"]["job"] == "20261126-120500"
    assert "20261126-120500" in r_fail["result"]["message"]

    r_q = chat_agent.execute_action(sid, msg["id"], card_queue["id"])
    assert r_q["ok"] is True
    assert "Elsevier" in r_q["result"]["message"]


def test_messages_for_openai_omits_empty_tool_calls():
    from litloop.agents import runner

    msgs = runner._messages_for(
        "openai",
        [
            {"role": "user", "text": "第一轮提问"},
            {"role": "assistant", "text": "纯文本回答"},
            {"role": "assistant", "text": "", "calls": [{"id": "c1", "name": "list_papers", "arguments": {}, "result": "ok"}]},
        ],
    )
    assert msgs[0] == {"role": "user", "content": "第一轮提问"}
    assert msgs[1] == {"role": "assistant", "content": "纯文本回答"}
    assert "tool_calls" not in msgs[1]
    assert len(msgs[2]["tool_calls"]) == 1
    assert msgs[3] == {"role": "tool", "tool_call_id": "c1", "content": "ok"}
