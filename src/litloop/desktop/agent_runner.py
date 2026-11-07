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

from .. import config as _config
from .. import store

# What the agent tells the app, by tool: shown under the run as it works.
_TOOL_TEXT = {
    "litloop_search": "正在检索",
    "litloop_deep_search": "正在深度检索",
    "litloop_screening": "正在保存评分",
    "litloop_find": "正在查找文献",
    "litloop_config": "正在读取设置",
    "litloop_diagnostics": "正在检测设置",
}
_SESSION_RE = re.compile(r'"session"\s*:\s*"([^"]+)"')
_LOCK = threading.Lock()
_DONE: set[str] = set()  # builtin runs whose final state is written
_PROCS: dict[str, subprocess.Popen] = {}
_CANCELLED: set[str] = set()
_SESSION_TOOLS = frozenset({"litloop_screening", "litloop_deep_search"})
# Backend B runs in a thread of this process instead of a child process.
_BUILTIN: dict[str, threading.Thread] = {}
_STOP: dict[str, threading.Event] = {}
_BUILTIN_TOOLS = {
    "search": "正在检索",
    "deep_search_start": "深度检索：正在构建候选池",
    "deep_search_next": "深度检索：正在生成下一批",
    "deep_search_status": "深度检索：正在查看进度",
    "screening_save": "正在保存评分",
}


class _Cancelled(Exception):
    pass


def runs_dir() -> Path:
    return _config.DATA_DIR / "agent_runs"


def _path(run: str) -> Path:
    if not re.fullmatch(r"[0-9-]+", run):
        raise ValueError("无法打开检索：编号不正确")
    return runs_dir() / f"{run}.json"


def _lines(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"[\n;；]+", text or "") if s.strip()]


def build_prompt(form: dict[str, Any], skill: bool = True) -> str:
    """The message the user would have typed, from the app's form.

    ``skill=False`` is for the built-in agent: it is told what to do by the system
    prompt in ``litloop.agents.system`` instead of by a skill it cannot read.
    """
    question = str(form.get("question") or "").strip()
    if not question:
        raise ValueError("填写检索问题")
    deep = form.get("mode") == "deep"
    parts = [f"{'用 litloop 技能' if skill else ''}{'做深度检索' if deep else '快速查找文献'}：{question}"]
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


def _backend() -> str:
    """Which backend runs a search started here: the agent command or a model API."""
    return "builtin" if str(_config.load_config().get("agent_backend") or "") == "builtin" else "command"


def _write(run: str, data: dict[str, Any]) -> None:
    store.write_json(_path(run), {k: v for k, v in data.items() if not k.startswith("_")}, indent=1)


def get(run: str) -> dict[str, Any] | None:
    p = _path(run)
    if not p.exists():
        return None
    d = store.read_json(p)
    if d.get("state") == "running" and not _alive(run):
        # The reader may have caught the file just before the run wrote its
        # final state; if the run is gone now, look once more before calling it
        # interrupted.
        d = store.read_json(p)
        if d.get("state") == "running":
            d["state"] = "interrupted"  # the app was closed while it ran
    return d


def _alive(run: str) -> bool:
    with _LOCK:
        return run in _PROCS or run in _BUILTIN or run in _DONE


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
        for r, p in _PROCS.items():
            if p.poll() is None:
                return r
        return next((r for r, t in _BUILTIN.items() if t.is_alive() and r not in _DONE), None)


def start(form: dict[str, Any]) -> dict[str, Any]:
    builtin = _backend() == "builtin"
    prompt = build_prompt(form, skill=not builtin)
    if running():
        raise RuntimeError("已有检索在进行，完成或取消后再开始新的检索")
    run = datetime.now().strftime("%Y%m%d-%H%M%S")
    runs_dir().mkdir(parents=True, exist_ok=True)
    data = {"run": run, "state": "running", "started": datetime.now().isoformat(timespec="seconds"),
            "question": str(form.get("question")).strip(), "mode": form.get("mode") or "quick",
            "form": form, "prompt": prompt, "doing": "正在启动 Agent", "events": [],
            "session": None, "agent_session": None, "result": None, "error": None}
    _write(run, data)
    if builtin:
        if not str(_config.load_config().get("builtin_agent_model") or "").strip():
            data.update(state="failed", doing=None,
                        error="无法开始检索：还没有填内置 Agent 的模型名。在「设置 → 内置 Agent」里填写")
            _write(run, data)
            return data
        thread = threading.Thread(target=_run_builtin, args=(run, data, prompt), daemon=True)
        with _LOCK:
            _BUILTIN[run] = thread
        thread.start()
        return data
    cmd = _command()
    try:
        proc = subprocess.Popen(
            cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            cwd=str(Path.home()), env={**os.environ, "PYTHONUTF8": "1"},
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except OSError as e:
        data.update(state="failed", doing=None, error=f"无法启动 Agent（{cmd[0]}）。在「设置 → Agent」中检查 Agent 命令")
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
            data["_last_tool"] = name
            if c.get("id"):
                data.setdefault("_tools", {})[c["id"]] = name
            action = (c.get("input") or {}).get("action")
            text = _TOOL_TEXT.get(name, f"正在调用 {name}")
            if name == "litloop_deep_search" and action:
                text = {"start": "深度检索：正在构建候选池", "rate": "深度检索：正在保存本批评分",
                        "next": "深度检索：正在生成下一批", "status": "深度检索：正在查看进度"}.get(action, text)
            data["doing"] = text
            _event(data, text)
        elif c.get("type") == "tool_result":
            body = c.get("content")
            body = body if isinstance(body, str) else " ".join(
                x.get("text", "") for x in body or [] if isinstance(x, dict))
            tool_id = c.get("tool_use_id")
            tool = (data.get("_tools") or {}).get(tool_id) if tool_id else None
            if not tool:
                tool = data.get("_last_tool")
            session = _session_from(tool, body or "")
            if session:
                data["session"] = session
        elif c.get("type") == "text" and kind == "assistant" and c.get("text", "").strip():
            data["doing"] = "Agent 正在评分"
    if kind == "result":
        data["result"] = str(msg.get("result") or "").strip() or None
        if msg.get("is_error"):
            data["error"] = data["result"] or "无法完成检索：Agent 出错"
            if re.search(r"authenticat|log ?in|OAuth", data["error"], re.I):
                cmd = _config.load_config().get("agent_command") or "claude"
                data["error"] = f"无法完成检索：Agent 未登录。在终端运行 {cmd} 登录后重试"
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


def _idle_minutes() -> float:
    raw = _config.load_config().get("agent_idle_minutes", 15)
    if isinstance(raw, bool):
        return 15.0
    try:
        val = float(raw)
    except (TypeError, ValueError):
        return 15.0
    if val < 0:
        return 15.0
    return val


def _watch_idle(run: str, proc: subprocess.Popen, data: dict[str, Any], seen: list[float], limit_minutes: float | None = None) -> None:
    """Stop an agent that has printed nothing for agent_idle_minutes (it hung,
    or waits on a prompt nobody sees); the run would otherwise stay "running"."""
    minutes = _idle_minutes() if limit_minutes is None else limit_minutes
    if minutes <= 0:
        return
    limit = minutes * 60
    while proc.poll() is None:
        time.sleep(min(5.0, max(0.2, limit / 4)))
        if time.time() - seen[0] > limit and proc.poll() is None:
            data["error"] = f"检索已停止：Agent 超过 {limit / 60:.0f} 分钟没有输出"
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
    idle = _idle_minutes()
    if idle > 0:
        threading.Thread(target=_watch_idle, args=(run, proc, data, seen, idle), daemon=True).start()
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
                data["error"] = f"无法完成检索：Agent 异常退出（退出码 {code}）。详情已写入日志"
    except Exception as e:
        data.update(state="failed", error=str(e))
    finally:
        data["doing"] = None
        data.pop("_tools", None)
        data.pop("_last_tool", None)
        data["finished"] = datetime.now().isoformat(timespec="seconds")
        _write(run, data)
        with _LOCK:
            _PROCS.pop(run, None)
            _CANCELLED.discard(run)


def _session_of(result: Any) -> str | None:
    """The screening session a built-in tool reported (deep search or screening save)."""
    if isinstance(result, dict):
        obj: Any = result
    else:
        try:
            obj = json.loads(str(result or ""))
        except ValueError:
            return None
    if isinstance(obj, dict):
        for key in ("session", "id"):
            value = obj.get(key)
            if isinstance(value, str) and value:
                return value
    return None


def _run_builtin(run: str, data: dict[str, Any], prompt: str) -> None:
    """Backend B: this process asks the model and runs the tools itself."""
    stop = threading.Event()
    with _LOCK:
        _STOP[run] = stop
    last = [0.0]

    def on_event(kind: str, ev: dict[str, Any]) -> None:
        if stop.is_set():
            raise _Cancelled()
        if kind == "tool":
            name = str(ev.get("name") or "")
            data["doing"] = _BUILTIN_TOOLS.get(name, f"正在调用 {name}")
            _event(data, data["doing"])
        elif kind == "result":
            session = _session_of(ev.get("result"))
            if session:
                data["session"] = session
        elif kind == "error":
            _event(data, f"{ev.get('name')} 出错：{str(ev.get('detail') or '')[:200]}")
        elif kind == "text":
            data["doing"] = "正在整理结论"
        if time.time() - last[0] > 1.0:  # the app polls this file
            _write(run, data)
            last[0] = time.time()

    try:
        from ..agents import FUNCTIONS, TOOLS, run_task
        from ..agents.system import system_prompt

        form = data.get("form") or {}
        deep = form.get("mode") == "deep"
        batches = int(form.get("batches") or 0) or 5
        # One batch costs a rate + a next call; building the pool and the final
        # answer need a few more (2026-11-06: the old flat 8 stopped deep runs).
        steps = min(4 + 2 * batches, 24) if deep else 8
        out = run_task(prompt, TOOLS, functions=FUNCTIONS, system=system_prompt(),
                       max_steps=steps, on_event=on_event)
        data["result"] = str(out.get("text") or "").strip() or None
        data["state"] = "cancelled" if stop.is_set() else "done"
    except _Cancelled:
        data["state"] = "cancelled"
    except Exception as e:
        data.update(state="failed", error=f"无法完成检索：{type(e).__name__}: {e}")
    finally:
        data["doing"] = None
        data["finished"] = datetime.now().isoformat(timespec="seconds")
        # Mark it finished before the last write: whoever reads the finished
        # state may start the next run at once, while this thread is still
        # alive for a moment and must not count as running.
        with _LOCK:
            _DONE.add(run)
        _write(run, data)
        with _LOCK:
            _BUILTIN.pop(run, None)
            _STOP.pop(run, None)
            _CANCELLED.discard(run)
            _DONE.discard(run)


def cancel(run: str) -> bool:
    with _LOCK:
        proc = _PROCS.get(run)
        thread = _BUILTIN.get(run)
        stop = _STOP.get(run)
    if thread is not None and thread.is_alive():
        _CANCELLED.add(run)
        if stop is not None:
            stop.set()  # the loop checks it before the next step
        return True
    if not proc or proc.poll() is not None:
        return False
    _CANCELLED.add(run)
    _kill(proc)
    return True
