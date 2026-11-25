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
