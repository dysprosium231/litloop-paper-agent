"""The built-in agent's task loop: model -> tool -> model, in both message shapes."""

from __future__ import annotations

from litloop.agents import runner


def _tools():
    return [{"name": "search", "description": "检索", "parameters": {"type": "object"}}]


def _scripted(monkeypatch, replies):
    seen: list[list[dict]] = []

    def fake_chat(messages, tools, **kwargs):
        seen.append(messages)
        return replies[len(seen) - 1]

    monkeypatch.setattr(runner, "chat", fake_chat)
    return seen


def test_the_loop_runs_a_tool_then_reports_the_answer(monkeypatch):
    replies = [
        {"text": "", "tool_calls": [{"id": "t1", "name": "search", "arguments": {"q": "SiC"}}],
         "stop": "tool_use"},
        {"text": "找到 3 篇。", "tool_calls": [], "stop": "end_turn"},
    ]
    seen = _scripted(monkeypatch, replies)
    events: list[str] = []
    out = runner.run_task("找 SiC", _tools(), functions={"search": lambda q: {"n": 3, "q": q}},
                          config={"builtin_agent_provider": "anthropic"},
                          on_event=lambda kind, data: events.append(kind))
    assert out["text"] == "找到 3 篇。" and out["steps"] == 2
    assert events == ["tool", "result", "text"]
    assistant, tool_result = seen[1][1], seen[1][2]
    assert assistant["content"][0]["type"] == "tool_use" and assistant["content"][0]["id"] == "t1"
    assert tool_result["content"][0] == {"type": "tool_result", "tool_use_id": "t1",
                                         "content": '{"n": 3, "q": "SiC"}'}


def test_the_loop_uses_openai_message_shapes_too(monkeypatch):
    replies = [
        {"text": "", "tool_calls": [{"id": "c1", "name": "search", "arguments": {"q": "PDC"}}]},
        {"text": "完成。", "tool_calls": []},
    ]
    seen = _scripted(monkeypatch, replies)
    runner.run_task("找 PDC", _tools(), functions={"search": lambda q: "ok"},
                    config={"builtin_agent_provider": "openai"})
    second = seen[1]
    assert second[1]["role"] == "assistant" and second[1]["tool_calls"][0]["function"]["name"] == "search"
    assert second[2] == {"role": "tool", "tool_call_id": "c1", "content": "ok"}


def test_an_unknown_tool_comes_back_as_an_error_not_a_crash(monkeypatch):
    replies = [{"text": "", "tool_calls": [{"id": "x", "name": "nope", "arguments": {}}]},
               {"text": "算了。", "tool_calls": []}]
    _scripted(monkeypatch, replies)
    out = runner.run_task("x", _tools(), functions={}, config={"builtin_agent_provider": "openai"})
    assert out["text"] == "算了。"
    assert out["tool_calls"][0]["result"].startswith("出错：没有这个工具")


def test_a_tool_that_blows_up_does_not_end_the_task(monkeypatch):
    def boom(**_):
        raise RuntimeError("磁盘满了")

    replies = [{"text": "", "tool_calls": [{"id": "1", "name": "search", "arguments": {}}]},
               {"text": "工具坏了，但任务还在。", "tool_calls": []}]
    _scripted(monkeypatch, replies)
    out = runner.run_task("x", _tools(), functions={"search": boom}, config={})
    assert out["text"] == "工具坏了，但任务还在。"
    assert "磁盘满了" in out["tool_calls"][0]["result"]


def test_the_loop_stops_at_the_step_limit(monkeypatch):
    replies = [{"text": "", "tool_calls": [{"id": "", "name": "search", "arguments": {}}]}] * 3
    _scripted(monkeypatch, replies)
    out = runner.run_task("x", _tools(), functions={"search": lambda: "…"}, config={}, max_steps=3)
    assert out["steps"] == 3 and "还没有给出结论" in out["text"]
    assert out["messages"][1]["tool_calls"][0]["id"] == "call_1_0"  # invented when the provider omits it
