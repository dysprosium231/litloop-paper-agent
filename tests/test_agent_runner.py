"""Searches started from the app: the form becomes a message, the agent's output becomes a status."""

import json
import sys
import time

import pytest

from litloop import agent_runner, config


def test_the_form_becomes_the_message():
    text = agent_runner.build_prompt({
        "question": "SiC 增强铝基复合材料的界面", "criteria": "研究 SiC/Al\n关注界面反应",
        "mode": "deep", "batches": 3, "year_from": 2015, "max_cas_zone": 2, "exclude_warning": True,
        "seeds": "10.1/a, 10.1/b", "seed_collection": "SiC/Al"})
    assert "深度检索" in text and "SiC 增强铝基复合材料的界面" in text
    assert "- 研究 SiC/Al" in text and "- 关注界面反应" in text
    assert "2015–至今" in text and "max_cas_zone=2" in text and "exclude_warning=true" in text
    assert "10.1/a、10.1/b" in text and "seed_collection" in text and "最多 3 批" in text
    assert "不要提问" in text


def test_no_criteria_lets_the_agent_write_them():
    text = agent_runner.build_prompt({"question": "x"})
    assert "快速查找" in text and "由你按问题拟定" in text and "约 20 篇" in text


def test_a_question_is_needed():
    with pytest.raises(ValueError):
        agent_runner.build_prompt({"question": "  "})


FAKE = r'''
import json, sys
prompt = sys.stdin.read()
def out(m): print(json.dumps(m, ensure_ascii=False), flush=True)
out({"type": "system", "session_id": "abc"})
out({"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "t1", "name": "mcp__litloop__litloop_deep_search", "input": {"action": "start"}}]}})
out({"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "t1", "content": [{"type": "text", "text": "{\"session\": \"20261017-x\"}"}]}]}})
out({"type": "result", "result": "已保存 20261017-x，" + str(len(prompt) > 0), "is_error": False, "total_cost_usd": 0.1})
'''


@pytest.fixture
def agent(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "config.json")
    script = tmp_path / "fake_agent.py"
    script.write_text(FAKE, encoding="utf-8")
    monkeypatch.setitem(config.DEFAULT_CONFIG, "agent_command", sys.executable)
    monkeypatch.setitem(config.DEFAULT_CONFIG, "agent_args", f'"{script}"')
    return tmp_path


def _wait(run):
    for _ in range(300):
        d = agent_runner.get(run)
        if d["state"] != "running":
            return d
        time.sleep(0.05)
    raise AssertionError("run did not finish")


def test_a_run_reports_the_session_it_saved(agent):
    d = _wait(agent_runner.start({"question": "x"})["run"])
    assert d["state"] == "done" and d["session"] == "20261017-x" and d["agent_session"] == "abc"
    assert d["result"] == "已保存 20261017-x，True"
    assert [e["text"] for e in d["events"]] == ["深度检索：正在构建候选池"]
    assert (agent / "agent_runs" / f"{d['run']}.log").read_text(encoding="utf-8").count("\n") == 4
    assert agent_runner.list_runs()[0]["run"] == d["run"]


def test_an_agent_that_cannot_start_says_so(agent, monkeypatch):
    monkeypatch.setitem(config.DEFAULT_CONFIG, "agent_command", str(agent / "no-such-agent.exe"))
    d = agent_runner.start({"question": "x"})
    assert d["state"] == "failed" and "Agent 命令" in d["error"]


def test_an_agent_that_is_not_logged_in_says_what_to_do():
    data = {"events": [], "result": None, "error": None}
    agent_runner._read(data, json.dumps({"type": "result", "is_error": True,
                                         "result": "Failed to authenticate: OAuth session expired"}))
    assert data["error"].startswith("无法完成检索：Agent 未登录") and data["result"] is None


def test_a_session_named_by_another_tool_is_ignored():
    data = {"events": []}
    agent_runner._read(data, json.dumps({"type": "assistant", "message": {"content": [
        {"type": "tool_use", "id": "w", "name": "WebFetch", "input": {}}]}}))
    agent_runner._read(data, json.dumps({"type": "user", "message": {"content": [
        {"type": "tool_result", "tool_use_id": "w", "content": '{"session": "not-ours"}'}]}}))
    assert data.get("session") is None


def test_a_silent_agent_is_stopped(agent, monkeypatch):
    (agent / "fake_agent.py").write_text("import sys, time" + chr(10) + "sys.stdin.read()" + chr(10) + "time.sleep(60)" + chr(10), encoding="utf-8")
    monkeypatch.setitem(config.DEFAULT_CONFIG, "agent_idle_minutes", 0.02)
    t = time.time()
    d = _wait(agent_runner.start({"question": "x"})["run"])
    assert time.time() - t < 15
    assert d["state"] == "failed" and "没有输出" in d["error"]
    assert not agent_runner._CANCELLED


def test_session_extracted_without_tool_use_id_when_last_tool_is_deep_search():
    data = {"events": []}
    agent_runner._read(data, json.dumps({"type": "assistant", "message": {"content": [
        {"type": "tool_use", "name": "mcp__litloop__litloop_deep_search", "input": {"action": "start"}}]}}))
    agent_runner._read(data, json.dumps({"type": "user", "message": {"content": [
        {"type": "tool_result", "content": '{"session": "20261018-deep"}'}]}}))
    assert data.get("session") == "20261018-deep"

    # 对不上记录的 id 时也退回最近一次 tool_use
    data2 = {"events": []}
    agent_runner._read(data2, json.dumps({"type": "assistant", "message": {"content": [
        {"type": "tool_use", "id": "t1", "name": "litloop_deep_search", "input": {"action": "start"}}]}}))
    agent_runner._read(data2, json.dumps({"type": "user", "message": {"content": [
        {"type": "tool_result", "tool_use_id": "unmatched_id", "content": '{"session": "20261018-deep2"}'}]}}))
    assert data2.get("session") == "20261018-deep2"


def test_session_ignored_without_tool_use_id_when_last_tool_is_other():
    data = {"events": []}
    agent_runner._read(data, json.dumps({"type": "assistant", "message": {"content": [
        {"type": "tool_use", "name": "WebFetch", "input": {}}]}}))
    agent_runner._read(data, json.dumps({"type": "user", "message": {"content": [
        {"type": "tool_result", "content": '{"session": "not-ours"}'}]}}))
    assert data.get("session") is None

    # 对不上记录的 id 且最近一次不是会话工具
    data2 = {"events": []}
    agent_runner._read(data2, json.dumps({"type": "assistant", "message": {"content": [
        {"type": "tool_use", "id": "s1", "name": "litloop_search", "input": {}}]}}))
    agent_runner._read(data2, json.dumps({"type": "user", "message": {"content": [
        {"type": "tool_result", "tool_use_id": "mismatched", "content": '{"session": "not-ours"}'}]}}))
    assert data2.get("session") is None


def test_agent_idle_minutes_zero_does_not_stop_agent(agent, monkeypatch):
    script = (
        "import json, sys, time\n"
        "prompt = sys.stdin.read()\n"
        "time.sleep(0.3)\n"
        "print(json.dumps({'type': 'result', 'result': 'ok', 'is_error': False}), flush=True)\n"
    )
    (agent / "fake_agent.py").write_text(script, encoding="utf-8")
    monkeypatch.setitem(config.DEFAULT_CONFIG, "agent_idle_minutes", 0)
    watch_called = []
    orig_watch = agent_runner._watch_idle
    monkeypatch.setattr(agent_runner, "_watch_idle", lambda *a, **k: (watch_called.append(True), orig_watch(*a, **k))[1])
    d = _wait(agent_runner.start({"question": "x"})["run"])
    assert d["state"] == "done"
    assert d["result"] == "ok"
    assert d.get("error") is None
    assert not watch_called


def test_idle_minutes_parsing(monkeypatch):
    monkeypatch.setitem(config.DEFAULT_CONFIG, "agent_idle_minutes", 0)
    assert agent_runner._idle_minutes() == 0.0
    monkeypatch.setitem(config.DEFAULT_CONFIG, "agent_idle_minutes", "0")
    assert agent_runner._idle_minutes() == 0.0
    monkeypatch.setitem(config.DEFAULT_CONFIG, "agent_idle_minutes", -1)
    assert agent_runner._idle_minutes() == 15.0
    monkeypatch.setitem(config.DEFAULT_CONFIG, "agent_idle_minutes", "-10")
    assert agent_runner._idle_minutes() == 15.0
    monkeypatch.setitem(config.DEFAULT_CONFIG, "agent_idle_minutes", "not-a-number")
    assert agent_runner._idle_minutes() == 15.0
    monkeypatch.setitem(config.DEFAULT_CONFIG, "agent_idle_minutes", False)
    assert agent_runner._idle_minutes() == 15.0
    monkeypatch.setitem(config.DEFAULT_CONFIG, "agent_idle_minutes", 20)
    assert agent_runner._idle_minutes() == 20.0
