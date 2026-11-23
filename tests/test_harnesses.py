"""Tests for external agent harnesses discovery, sync, stream parsing, and chat resumption."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from litloop import config, db, store
from litloop.desktop import agent_runner, harnesses, server as app_server
from litloop.search import screening

TOKEN = "t0ken-harness"


@pytest.fixture
def isolated_env(tmp_path, monkeypatch):
    home_dir = tmp_path / "home"
    data_dir = tmp_path / "data"
    home_dir.mkdir(parents=True, exist_ok=True)
    data_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(config, "DATA_DIR", data_dir)
    monkeypatch.setattr(config, "CONFIG_FILE", data_dir / "config.json")
    monkeypatch.setattr(screening._config, "DATA_DIR", data_dir)
    monkeypatch.setattr(harnesses, "_home", lambda: home_dir)
    monkeypatch.setattr(harnesses, "_system_install_dirs", lambda kind: [])
    monkeypatch.setattr(harnesses.shutil, "which", lambda cmd: None)
    return {"home": home_dir, "data": data_dir}


def test_detect_and_build_invocation_all_harnesses(isolated_env, monkeypatch):
    home = isolated_env["home"]
    data = isolated_env["data"]

    # Initially nothing external is installed -> auto falls back to claude
    initial = {h["id"]: h for h in harnesses.detect_harnesses()}
    assert initial["claude"]["detected"] is False
    assert initial["claude"]["selected"] is True
    assert initial["builtin"]["detected"] is True

    # 1. Install fake WorkBuddy desktop package (exe + mjs)
    wb_dir = home / "AppData" / "Local" / "Programs" / "WorkBuddyAI"
    wb_exe = wb_dir / "WorkBuddyAI.exe"
    wb_mjs = wb_dir / "resources" / "app.asar.unpacked" / "cli" / "dist" / "codebuddy-lite-wb.mjs"
    wb_exe.parent.mkdir(parents=True, exist_ok=True)
    wb_mjs.parent.mkdir(parents=True, exist_ok=True)
    wb_exe.write_text("", encoding="utf-8")
    wb_mjs.write_text("", encoding="utf-8")

    # Auto mode should now pick workbuddy as first detected
    detected = {h["id"]: h for h in harnesses.detect_harnesses()}
    assert detected["workbuddy"]["detected"] is True
    assert detected["workbuddy"]["selected"] is True
    assert harnesses.resolve_harness() == "workbuddy"

    cmd, env, hid = harnesses.build_invocation(harness_id="workbuddy", resume_session="wb-sess-1")
    assert hid == "workbuddy"
    assert cmd[0] == str(wb_exe) and cmd[1] == str(wb_mjs)
    assert "--mcp-config" in cmd
    assert cmd[-2:] == ["--resume", "wb-sess-1"]
    assert env == {"ELECTRON_RUN_AS_NODE": "1"}
    mcp_json = data / "agent_runs" / "litloop-mcp.json"
    assert mcp_json.is_file()
    assert "litloop" in json.loads(mcp_json.read_text(encoding="utf-8"))["mcpServers"]

    # 2. Install fake Claude Code binary
    claude_bin = home / ".local" / "bin" / "claude.exe"
    claude_bin.parent.mkdir(parents=True, exist_ok=True)
    claude_bin.write_text("", encoding="utf-8")
    assert harnesses.resolve_harness() == "claude"
    cmd_c, env_c, hid_c = harnesses.build_invocation(harness_id="claude", resume_session="cl-123")
    assert hid_c == "claude" and cmd_c[0] == str(claude_bin)
    assert "--mcp-config" in cmd_c and cmd_c[-2:] == ["--resume", "cl-123"]
    assert env_c == {}

    # 3. Install fake DeepSeek Harness (dsh.cmd) + desktop llm block
    dsh_bin = home / "AppData" / "Local" / "Programs" / "DSH" / "resources" / "runtime" / "cli" / "bin" / "dsh.cmd"
    dsh_bin.parent.mkdir(parents=True, exist_ok=True)
    dsh_bin.write_text("", encoding="utf-8")
    desktop_patch = home / ".dsh" / "profiles" / "desktop" / "cordis.patch.yml"
    desktop_patch.parent.mkdir(parents=True, exist_ok=True)
    desktop_patch.write_text(
        "- id: llm-deepseek\n"
        '  name: "@deepseek-ai/dsh-llm-deepseek-api-key"\n'
        "  config:\n"
        "    baseURL: https://api.deepseek.com/anthropic\n",
        encoding="utf-8",
    )
    cmd_d, env_d, hid_d = harnesses.build_invocation(harness_id="dsh", resume_session="dsh-sess-9")
    assert hid_d == "dsh" and cmd_d[0] == str(dsh_bin)
    assert "--profile" in cmd_d and "headless" in cmd_d and "--json" in cmd_d
    assert "--session-id" in cmd_d and "dsh-sess-9" in cmd_d and cmd_d[-1] == "-"
    patch_file = data / "agent_runs" / "litloop-dsh.patch.yml"
    assert patch_file.is_file()
    patch_text = patch_file.read_text(encoding="utf-8")
    assert "llm-deepseek" in patch_text and "mcp-litloop" in patch_text

    # 4. Install fake Codex CLI
    codex_bin = home / "AppData" / "Roaming" / "npm" / "codex.cmd"
    codex_bin.parent.mkdir(parents=True, exist_ok=True)
    codex_bin.write_text("", encoding="utf-8")
    cmd_x1, _, hid_x1 = harnesses.build_invocation(harness_id="codex")
    assert hid_x1 == "codex" and cmd_x1 == [str(codex_bin), "exec", "--json", "--full-auto", "-"]
    cmd_x2, _, _ = harnesses.build_invocation(harness_id="codex", resume_session="th-42")
    assert cmd_x2 == [str(codex_bin), "exec", "resume", "--json", "--full-auto", "th-42", "-"]

    # 5. Builtin & Custom resolution
    cfg_builtin = {**config.load_config(), "agent_backend": "builtin", "builtin_agent_model": "deepseek-chat"}
    assert harnesses.resolve_harness(cfg_builtin) == "builtin"
    b_info = next(h for h in harnesses.detect_harnesses(cfg_builtin) if h["id"] == "builtin")
    assert b_info["selected"] is True and "deepseek-chat" in b_info["detail"]

    cfg_custom = {**config.load_config(), "agent_harness": "custom", "agent_command": "my-agent", "agent_args": "--flag"}
    assert harnesses.resolve_harness(cfg_custom) == "custom"
    cmd_cu, _, hid_cu = harnesses.build_invocation(cfg_custom)
    assert hid_cu == "custom" and cmd_cu == ["my-agent", "--flag"]


def test_sync_harness_idempotent_and_copies_skill(isolated_env):
    home = isolated_env["home"]

    # 1. Claude sync
    (home / ".claude.json").write_text(json.dumps({"otherKey": 1, "mcpServers": {"keep": {"command": "x"}}}), encoding="utf-8")
    res_claude = harnesses.sync_harness("claude")
    assert res_claude["ok"] is True
    assert res_claude["harness"]["mcp_configured"] is True
    assert res_claude["harness"]["skill_installed"] is True
    claude_cfg = json.loads((home / ".claude.json").read_text(encoding="utf-8"))
    assert claude_cfg["otherKey"] == 1
    assert "keep" in claude_cfg["mcpServers"]
    assert claude_cfg["mcpServers"]["litloop"]["args"] == ["-m", "litloop", "run"]
    assert (home / ".claude" / "skills" / "litloop" / "SKILL.md").is_file()

    # 2. WorkBuddy sync
    res_wb = harnesses.sync_harness("workbuddy")
    assert res_wb["ok"] is True
    assert res_wb["harness"]["mcp_configured"] is True
    assert res_wb["harness"]["skill_installed"] is True
    wb_cfg = json.loads((home / ".workbuddy-ai" / "mcp.json").read_text(encoding="utf-8"))
    assert wb_cfg["mcpServers"]["litloop"]["command"] == sys.executable
    assert (home / ".workbuddy-ai" / "skills" / "litloop" / "SKILL.md").is_file()

    # 3. DSH sync (copies llm-* from desktop to headless and writes mcp-litloop idempotently)
    desktop_patch = home / ".dsh" / "profiles" / "desktop" / "cordis.patch.yml"
    headless_patch = home / ".dsh" / "profiles" / "headless" / "cordis.patch.yml"
    desktop_patch.parent.mkdir(parents=True, exist_ok=True)
    headless_patch.parent.mkdir(parents=True, exist_ok=True)
    desktop_patch.write_text(
        "- id: ui-chat\n  config:\n    enabled: true\n"
        "- id: llm-deepseek\n  name: \"@deepseek-ai/dsh-llm-deepseek-api-key\"\n  config:\n    baseURL: https://api.deepseek.com/anthropic\n",
        encoding="utf-8",
    )
    headless_patch.write_text("- id: credentials\n  disabled: false\n", encoding="utf-8")

    res_dsh = harnesses.sync_harness("dsh")
    assert res_dsh["ok"] is True
    assert res_dsh["harness"]["mcp_configured"] is True
    assert res_dsh["harness"]["skill_installed"] is True
    # Run a second time to verify idempotency
    harnesses.sync_harness("dsh")
    d_text = desktop_patch.read_text(encoding="utf-8")
    h_text = headless_patch.read_text(encoding="utf-8")
    assert d_text.count("mcp-litloop") == 1
    assert h_text.count("mcp-litloop") == 1
    assert "llm-deepseek" in h_text
    assert (home / ".dsh" / "skills" / "litloop" / "SKILL.md").is_file()

    # 4. Codex sync (idempotent TOML section)
    codex_toml = home / ".codex" / "config.toml"
    codex_toml.parent.mkdir(parents=True, exist_ok=True)
    codex_toml.write_text('[model]\nname = "o3"\n', encoding="utf-8")
    res_codex = harnesses.sync_harness("codex")
    assert res_codex["ok"] is True
    assert res_codex["harness"]["mcp_configured"] is True
    assert res_codex["harness"]["skill_installed"] is True
    harnesses.sync_harness("codex")
    toml_text = codex_toml.read_text(encoding="utf-8")
    assert toml_text.count("[mcp_servers.litloop]") == 1
    assert 'name = "o3"' in toml_text
    assert (home / ".codex" / "skills" / "litloop" / "SKILL.md").is_file()

    with pytest.raises(ValueError):
        harnesses.sync_harness("unknown")


def test_agent_runner_read_multi_harness_streams():
    # 1. Claude / WorkBuddy stream-json
    d_claude = {"doing": "正在启动 Agent", "events": [], "session": None, "agent_session": None, "result": None, "error": None}
    agent_runner._read(d_claude, json.dumps({"type": "system", "session_id": "claude-sid-1"}))
    agent_runner._read(d_claude, json.dumps({
        "type": "assistant",
        "message": {"content": [{"type": "tool_use", "id": "u1", "name": "mcp__litloop__litloop_deep_search", "input": {"action": "start"}}]},
    }))
    agent_runner._read(d_claude, json.dumps({
        "type": "user",
        "message": {"content": [{"type": "tool_result", "tool_use_id": "u1", "content": '{"session": "20261123-claude"}'}]},
    }))
    agent_runner._read(d_claude, json.dumps({"type": "result", "result": "Claude 检索完成", "is_error": False}))
    assert d_claude["agent_session"] == "claude-sid-1"
    assert d_claude["session"] == "20261123-claude"
    assert d_claude["result"] == "Claude 检索完成"

    # 2. DeepSeek Harness (dsh --profile headless --json)
    d_dsh = {"doing": "正在启动 Agent", "events": [], "session": None, "agent_session": None, "result": None, "error": None}
    agent_runner._read(d_dsh, json.dumps({"type": "session", "sessionId": "dsh-sid-42"}))
    assert d_dsh["agent_session"] == "dsh-sid-42"
    agent_runner._read(d_dsh, json.dumps({"type": "status", "phase": "turn_start"}))
    assert d_dsh["doing"] == "Agent 正在思考"
    agent_runner._read(d_dsh, json.dumps({
        "type": "tool_call",
        "callId": "c1",
        "toolName": "mcp__litloop__litloop_deep_search",
        "input": {"action": "next"},
    }))
    assert d_dsh["doing"] == "深度检索：正在生成下一批"
    agent_runner._read(d_dsh, json.dumps({
        "type": "tool_result",
        "callId": "c1",
        "output": {"session": "20261123-dsh", "batch": 2},
    }))
    assert d_dsh["session"] == "20261123-dsh"
    agent_runner._read(d_dsh, json.dumps({
        "type": "final",
        "sessionId": "dsh-sid-42",
        "text": "DSH 已完成第二批评分",
        "reason": {"kind": "complete"},
    }))
    assert d_dsh["result"] == "DSH 已完成第二批评分" and d_dsh["error"] is None

    # DSH error final event
    d_dsh_err = {"doing": "正在启动 Agent", "events": [], "session": None, "agent_session": None, "result": None, "error": None}
    agent_runner._read(d_dsh_err, json.dumps({
        "type": "final",
        "sessionId": "dsh-sid-err",
        "reason": {"kind": "error", "error": {"message": "API key invalid"}},
    }))
    assert d_dsh_err["agent_session"] == "dsh-sid-err"
    assert d_dsh_err["error"] == "API key invalid" and d_dsh_err["result"] is None

    # 3. Codex CLI (codex exec --json)
    d_codex = {"doing": "正在启动 Agent", "events": [], "session": None, "agent_session": None, "result": None, "error": None}
    agent_runner._read(d_codex, json.dumps({"type": "thread.started", "thread_id": "thread-99"}))
    assert d_codex["agent_session"] == "thread-99"
    agent_runner._read(d_codex, json.dumps({
        "type": "item.started",
        "item": {"type": "mcp_tool_call", "tool": "mcp__litloop__litloop_deep_search", "arguments": {"action": "rate"}},
    }))
    assert d_codex["doing"] == "深度检索：正在保存本批评分"
    agent_runner._read(d_codex, json.dumps({
        "type": "item.completed",
        "item": {"type": "mcp_tool_call", "tool": "mcp__litloop__litloop_deep_search", "result": '{"session": "20261123-codex"}'},
    }))
    assert d_codex["session"] == "20261123-codex"
    agent_runner._read(d_codex, json.dumps({
        "type": "item.completed",
        "item": {"type": "agent_message", "text": "Codex 筛选汇报"},
    }))
    assert d_codex["result"] == "Codex 筛选汇报"


def test_session_origin_and_resume_same_chat(isolated_env, monkeypatch):
    # Create a deep search session with 1 user-adjusted rating
    sid = screening.save(
        "钙钛矿太阳能电池界面钝化",
        [
            {"doi": "10.1/a", "title": "A", "relevance": "低", "reason": "r1"},
            {"doi": "10.1/b", "title": "B", "relevance": "高", "reason": "r2"},
        ],
        mode="deep",
    )["session"]
    db.save_pool_state({"session": sid, "pool": {}})
    db.update_item_rating(sid, "10.1/a", "A", "高")

    # Before any app run exists, origin is "external"
    ext_origin = agent_runner.session_origin(sid)
    assert ext_origin["source"] == "external"
    assert ext_origin["can_resume_same_chat"] is False
    assert ext_origin["changed_ratings"] == 1
    assert "手动调整了 1 篇评分" in ext_origin["continue_prompt"]
    assert sid in ext_origin["continue_prompt"]

    # Record a previous app run from Claude Code with agent_session
    agent_runner.runs_dir().mkdir(parents=True, exist_ok=True)
    store.write_json(
        agent_runner.runs_dir() / "20261123-100000.json",
        {
            "run": "20261123-100000",
            "state": "done",
            "started": "2026-11-23T10:00:00",
            "finished": "2026-11-23T10:02:00",
            "question": "钙钛矿太阳能电池界面钝化",
            "mode": "deep",
            "harness": "claude",
            "harness_label": "Claude Code",
            "session": sid,
            "agent_session": "chat-uuid-777",
            "resumed_chat": False,
            "result": "第一批完成",
            "error": None,
        },
    )

    app_origin = agent_runner.session_origin(sid)
    assert app_origin["source"] == "app"
    assert app_origin["harness"] == "claude"
    assert app_origin["agent_session"] == "chat-uuid-777"
    assert app_origin["can_resume_same_chat"] is True

    # Verify resume() passes resume_session="chat-uuid-777" and uses short continuing prompt
    captured = {}
    script = isolated_env["data"] / "fake_resume_agent.py"
    script.write_text(
        "import json, sys\n"
        "prompt = sys.stdin.read()\n"
        "print(json.dumps({'type': 'result', 'result': 'RESUMED:' + str('复核完上一批文献评分' in prompt), 'is_error': False}), flush=True)\n",
        encoding="utf-8",
    )

    def fake_build_invocation(cfg=None, *, harness_id=None, resume_session=None):
        captured["harness_id"] = harness_id
        captured["resume_session"] = resume_session
        return [sys.executable, str(script)], {}, harness_id or "claude"

    monkeypatch.setattr(harnesses, "build_invocation", fake_build_invocation)
    run_info = agent_runner.resume(sid, batches=1)
    for _ in range(200):
        d = agent_runner.get(run_info["run"])
        if d and d["state"] != "running":
            break
        time.sleep(0.05)

    assert captured["harness_id"] == "claude"
    assert captured["resume_session"] == "chat-uuid-777"
    assert d["resumed_chat"] is True
    assert d["result"] == "RESUMED:True"

    # Verify force_new=True starts a fresh conversation without resume_session
    run_info2 = agent_runner.resume(sid, batches=1, force_new=True)
    for _ in range(200):
        d2 = agent_runner.get(run_info2["run"])
        if d2 and d2["state"] != "running":
            break
        time.sleep(0.05)

    assert captured["resume_session"] is None
    assert d2["resumed_chat"] is False


def test_server_harnesses_and_session_origin_endpoints(isolated_env):
    home = isolated_env["home"]
    claude_bin = home / ".local" / "bin" / "claude.exe"
    claude_bin.parent.mkdir(parents=True, exist_ok=True)
    claude_bin.write_text("", encoding="utf-8")

    client = TestClient(app_server.create_app(TOKEN))
    client.headers["X-LitLoop-Token"] = TOKEN

    # GET /api/harnesses
    r = client.get("/api/harnesses")
    assert r.status_code == 200
    items = {h["id"]: h for h in r.json()}
    assert items["claude"]["detected"] is True

    # POST /api/harnesses/{id}/sync
    r_sync = client.post("/api/harnesses/claude/sync")
    assert r_sync.status_code == 200
    assert r_sync.json()["ok"] is True
    assert r_sync.json()["harness"]["mcp_configured"] is True
    assert client.post("/api/harnesses/invalid/sync").status_code == 400

    # POST /api/harnesses/{id}/select
    r_sel = client.post("/api/harnesses/dsh/select")
    assert r_sel.status_code == 200
    sel_map = {h["id"]: h for h in r_sel.json()}
    assert sel_map["dsh"]["selected"] is True
    assert config.load_config()["agent_harness"] == "dsh"
    assert client.post("/api/harnesses/invalid/select").status_code == 400

    # POST /api/settings/test/agent
    r_test = client.post("/api/settings/test/agent")
    assert r_test.status_code == 200
    names = [x["name"] for x in r_test.json()["results"]]
    assert "Claude Code" in names and "DeepSeek Harness" in names

    # GET /api/sessions/{id} includes origin
    sid = screening.save("q", [{"doi": "10.1/x", "title": "X", "relevance": "高", "reason": "r"}], mode="deep")["session"]
    r_sess = client.get(f"/api/sessions/{sid}")
    assert r_sess.status_code == 200
    body = r_sess.json()
    assert "origin" in body
    assert body["origin"]["source"] == "external"
    assert sid in body["origin"]["continue_prompt"]
