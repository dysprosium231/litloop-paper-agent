"""Searches started from the desktop app: the form becomes a message to the agent.

The app does what the user would do in a chat: it sends the question, the
criteria, the year and journal filters, the seeds and the scale to the agent
(``agent_command``, Claude Code's ``claude -p`` by default), which runs the
usual LitLoop workflow with the litloop tools and saves a screening session.
One run at a time. Each run is ``agent_runs/<id>.json`` (status, what the agent
is doing, the screening session it saved) plus the agent's raw output in
``agent_runs/<id>.log``.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from . import config as _config
from . import store

# What the agent tells the app, by tool: shown under the run as it works.
_TOOL_TEXT = {
    "litloop_search": "检索中",
    "litloop_deep_search": "深度检索中",
    "litloop_screening": "保存筛选结果",
    "litloop_find": "查找文献",
    "litloop_config": "读取设置",
    "litloop_diagnostics": "检查设置",
}
_SESSION_RE = re.compile(r'"session"\s*:\s*"([^"]+)"')
_LOCK = threading.Lock()
_PROCS: dict[str, subprocess.Popen] = {}
_CANCELLED: set[str] = set()
_SESSION_TOOLS = frozenset({"litloop_screening", "litloop_deep_search"})


def runs_dir() -> Path:
    return _config.DATA_DIR / "agent_runs"


def _path(run: str) -> Path:
    if not re.fullmatch(r"[0-9-]+", run):
        raise ValueError(f"bad run id: {run}")
    return runs_dir() / f"{run}.json"


def _lines(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"[\n;；]+", text or "") if s.strip()]


def build_prompt(form: dict[str, Any]) -> str:
    """The message the user would have typed, from the app's form."""
    question = str(form.get("question") or "").strip()
    if not question:
        raise ValueError("请填写检索问题")
    deep = form.get("mode") == "deep"
    parts = [f"用 litloop 技能{'做深度检索' if deep else '快速查找文献'}：{question}"]
    criteria = _lines(str(form.get("criteria") or ""))
    if criteria:
        parts.append("纳入标准（按这些判断，不要改）：\n" + "\n".join(f"- {c}" for c in criteria))
    else:
        parts.append("纳入标准由你按问题拟定 2–5 条，保存时写进 criteria。")
    y0, y1 = form.get("year_from"), form.get("year_to")
    if y0 or y1:
        parts.append(f"年份：{y0 or '不限'}–{y1 or '至今'}。")
    rank = []
    if form.get("max_cas_zone"):
        rank.append(f"中科院分区 {form['max_cas_zone']} 区及以上（max_cas_zone={form['max_cas_zone']}）")
    if form.get("max_jcr_quartile"):
        rank.append(f"JCR Q{form['max_jcr_quartile']} 及以上（max_jcr_quartile={form['max_jcr_quartile']}）")
    if form.get("exclude_warning"):
        rank.append("排除预警期刊（exclude_warning=true）")
    if rank:
        parts.append("期刊要求：" + "；".join(rank) + "。")
    seeds = _lines(str(form.get("seeds") or "").replace(",", "\n").replace("，", "\n"))
    if seeds:
        parts.append("种子文献：" + "、".join(seeds))
    if form.get("seed_collection"):
        parts.append(f"Zotero 分类「{form['seed_collection']}」里的文献也作种子（seed_collection）。")
    if deep:
        batches = int(form.get("batches") or 0)
        parts.append(
            f"每批逐篇评分后取下一批，{'最多 ' + str(batches) + ' 批，' if batches else ''}"
            "返回 stop_suggested=true 时停止。")
    else:
        parts.append(f"返回约 {int(form.get('limit') or 20)} 篇，逐篇评分后保存。")
    parts.append(
        "这是从 LitLoop 桌面 app 发起的任务，用户不在对话里：不要提问、不要等确认，"
        "需要用户决定的事写进最后的汇报；不要下载、不要推送 Zotero。"
        "结束时用两三句中文汇报筛选会话名、篇数和各相关度数量。")
    return "\n\n".join(parts)


def _command() -> list[str]:
    cfg = _config.load_config()
    cmd = str(cfg.get("agent_command") or "claude").strip()
    exe = shutil.which(cmd) or cmd
    # Windows paths keep their backslashes; quotes only group words.
    args = [a.strip('"') for a in shlex.split(str(cfg.get("agent_args") or ""), posix=os.name != "nt")]
    return [exe, *args]


def _write(run: str, data: dict[str, Any]) -> None:
    store.write_json(_path(run), {k: v for k, v in data.items() if not k.startswith("_")}, indent=1)


def get(run: str) -> dict[str, Any] | None:
    p = _path(run)
    if not p.exists():
        return None
    d = store.read_json(p)
    if d.get("state") == "running" and run not in _PROCS:
        d["state"] = "interrupted"  # the app was closed while it ran
    return d


def list_runs(limit: int = 20) -> list[dict[str, Any]]:
    out = []
    for p in sorted(runs_dir().glob("*.json"), reverse=True)[:limit]:
        d = get(p.stem)
        if d:
            d.pop("events", None)
            out.append(d)
    return out


def running() -> str | None:
    with _LOCK:
        return next((r for r, p in _PROCS.items() if p.poll() is None), None)


def start(form: dict[str, Any]) -> dict[str, Any]:
    prompt = build_prompt(form)
    if running():
        raise RuntimeError("已有一个检索在进行，等它结束或先取消")
    run = datetime.now().strftime("%Y%m%d-%H%M%S")
    runs_dir().mkdir(parents=True, exist_ok=True)
    data = {"run": run, "state": "running", "started": datetime.now().isoformat(timespec="seconds"),
            "question": str(form.get("question")).strip(), "mode": form.get("mode") or "quick",
            "form": form, "prompt": prompt, "doing": "正在启动 Agent", "events": [],
            "session": None, "agent_session": None, "result": None, "error": None}
    _write(run, data)
    cmd = _command()
    try:
        proc = subprocess.Popen(
            cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            cwd=str(Path.home()), env={**os.environ, "PYTHONUTF8": "1"},
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except OSError as e:
        data.update(state="failed", doing=None, error=f"无法启动 Agent（{cmd[0]}）：{e}。在设置里检查 Agent 命令")
        _write(run, data)
        return data
    with _LOCK:
        _PROCS[run] = proc
    threading.Thread(target=_follow, args=(run, proc, prompt, data), daemon=True).start()
    return data


def _event(data: dict[str, Any], text: str) -> None:
    data["events"].append({"t": datetime.now().strftime("%H:%M:%S"), "text": text})
    data["events"] = data["events"][-200:]


def _read(data: dict[str, Any], line: str) -> None:
    """One line of ``--output-format stream-json``; other output is kept in the log only."""
    try:
        msg = json.loads(line)
    except ValueError:
        return
    kind = msg.get("type")
    if kind == "system" and msg.get("session_id"):
        data["agent_session"] = msg["session_id"]
    for c in ((msg.get("message") or {}).get("content") or []) if kind in ("assistant", "user") else []:
        if not isinstance(c, dict):
            continue
        if c.get("type") == "tool_use":
            name = str(c.get("name") or "").split("__")[-1]
            if c.get("id"):
                data.setdefault("_tools", {})[c["id"]] = name
            action = (c.get("input") or {}).get("action")
            text = _TOOL_TEXT.get(name, f"调用 {name}")
            if name == "litloop_deep_search" and action:
                text = {"start": "深度检索：开始", "rate": "深度检索：保存这一批的评分",
                        "next": "深度检索：取下一批", "status": "深度检索：查看进度"}.get(action, text)
            data["doing"] = text
            _event(data, text)
        elif c.get("type") == "tool_result":
            body = c.get("content")
            body = body if isinstance(body, str) else " ".join(
                x.get("text", "") for x in body or [] if isinstance(x, dict))
            session = _session_from(data.get("_tools", {}).get(c.get("tool_use_id")), body or "")
            if session:
                data["session"] = session
        elif c.get("type") == "text" and kind == "assistant" and c.get("text", "").strip():
            data["doing"] = "Agent 在判断文献"
    if kind == "result":
        data["result"] = str(msg.get("result") or "").strip() or None
        if msg.get("is_error"):
            data["error"] = data["result"] or "Agent 报错"
            if re.search(r"authenticat|log ?in|OAuth", data["error"], re.I):
                cmd = _config.load_config().get("agent_command") or "claude"
                data["error"] = f"Agent 没有登录：在终端运行 {cmd}，登录后再试（{data['error']}）"
            data["result"] = None
        data["cost_usd"] = msg.get("total_cost_usd")


def _session_from(tool: str | None, body: str) -> str | None:
    """The screening session a LitLoop tool reported: its JSON "session" field.

    Only litloop_screening and litloop_deep_search name the session the app
    opens; a session-like string in another tool's output (a web page, a
    file the agent read) is not it (code review, 2026-10-18)."""
    if tool not in _SESSION_TOOLS:
        return None
    try:
        obj = json.loads(body)
    except ValueError:
        obj = None
    if isinstance(obj, dict) and isinstance(obj.get("session"), str) and obj["session"]:
        return obj["session"]
    m = _SESSION_RE.search(body)  # a result wrapped in text around the JSON
    return m.group(1) if m else None


def _watch_idle(run: str, proc: subprocess.Popen, data: dict[str, Any], seen: list[float]) -> None:
    """Stop an agent that has printed nothing for agent_idle_minutes (it hung,
    or waits on a prompt nobody sees); the run would otherwise stay "running"."""
    limit = float(_config.load_config().get("agent_idle_minutes", 15) or 15) * 60
    while proc.poll() is None:
        time.sleep(min(5.0, max(0.2, limit / 4)))
        if time.time() - seen[0] > limit and proc.poll() is None:
            data["error"] = f"Agent 超过 {limit / 60:.0f} 分钟没有任何输出，已停止"
            _kill(proc)
            return


def _kill(proc: subprocess.Popen) -> None:
    if os.name == "nt":  # the agent's own tool servers go with it
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    else:
        proc.terminate()


def _follow(run: str, proc: subprocess.Popen, prompt: str, data: dict[str, Any]) -> None:
    log = _path(run).with_suffix(".log")
    last = 0.0
    seen = [time.time()]  # when the agent last printed anything
    threading.Thread(target=_watch_idle, args=(run, proc, data, seen), daemon=True).start()
    try:
        assert proc.stdin and proc.stdout
        proc.stdin.write(prompt.encode("utf-8"))
        proc.stdin.close()
        with log.open("ab") as fh:
            for raw in proc.stdout:
                seen[0] = time.time()
                fh.write(raw)
                _read(data, raw.decode("utf-8", errors="replace").strip())
                if time.time() - last > 1.0:
                    _write(run, data)
                    last = time.time()
        code = proc.wait()
        if run in _CANCELLED:
            data["state"] = "cancelled"
        else:
            ok = code == 0 and not data.get("error")
            data["state"] = "done" if ok else "failed"
            if not ok and not data.get("error"):
                data["error"] = f"Agent 退出码 {code}，详见日志"
    except Exception as e:
        data.update(state="failed", error=str(e))
    finally:
        data["doing"] = None
        data.pop("_tools", None)
        data["finished"] = datetime.now().isoformat(timespec="seconds")
        _write(run, data)
        with _LOCK:
            _PROCS.pop(run, None)
            _CANCELLED.discard(run)


def cancel(run: str) -> bool:
    with _LOCK:
        proc = _PROCS.get(run)
    if not proc or proc.poll() is not None:
        return False
    _CANCELLED.add(run)
    _kill(proc)
    return True
