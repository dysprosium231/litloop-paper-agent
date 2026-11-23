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
from .. import db, store
from . import harnesses

# What the agent tells the app, by tool: shown under the run as it works.
_TOOL_TEXT = {
    "litloop_search": "正在检索",
    "litloop_deep_search": "正在深度检索",
    "litloop_screening": "正在保存评分",
    "litloop_find": "正在查找文献",
    "litloop_config": "正在读取设置",
    "litloop_diagnostics": "正在检测设置",
}
_DEEP_ACTION_TEXT = {
    "start": "深度检索：正在构建候选池",
    "rate": "深度检索：正在保存本批评分",
    "next": "深度检索：正在生成下一批",
    "status": "深度检索：正在查看进度",
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
    "deep_search_rate": "深度检索：正在保存本批评分",
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
    planned_q = [str(x).strip() for x in (form.get("planned_queries") or []) if str(x).strip()]
    if planned_q:
        parts.append("已确认检索词（直接按这些检索，不要自行改写）：\n" + "\n".join(f"- {q}" for q in planned_q))
    planned_c = [str(x).strip() for x in (form.get("planned_core_terms") or []) if str(x).strip()]
    if planned_c:
        parts.append("已确认核心词：" + "、".join(planned_c))
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
    cfg = _config.load_config()
    if str(cfg.get("agent_backend") or "") == "builtin" or str(cfg.get("agent_harness") or "") == "builtin":
        return "builtin"
    return "command"


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


def _new_run_id() -> str:
    base = datetime.now().strftime("%Y%m%d-%H%M%S")
    run = base
    idx = 2
    while _path(run).exists():
        run = f"{base}-{idx}"
        idx += 1
    return run


def _count_changed_ratings(record: dict[str, Any] | None) -> int:
    if not isinstance(record, dict):
        return 0
    return sum(
        1
        for it in (record.get("items") or [])
        if isinstance(it, dict) and it.get("user_relevance") and it.get("user_relevance") != it.get("relevance")
    )


def build_external_continue_prompt(session: str, *, changed_ratings: int = 0) -> str:
    """Concise prompt for users to paste into an external Claude Code / WorkBuddy / DSH chat."""
    note = f"（手动调整了 {int(changed_ratings)} 篇评分）" if changed_ratings else ""
    return (
        f"我已在 LitLoop 桌面端复核完会话 {session} 的上一批文献{note}。"
        f"请先调用 litloop_deep_search(action='status', session={session!r}) 同步最新进度，"
        "如有 unrated 先用 action='rate' 评完，再调用 action='next' 生成并逐篇评分下一批。"
    )


def session_origin(session: str) -> dict[str, Any]:
    """Identify whether a session was started in the app or in an external agent chat."""
    try:
        record = db.load_screening(session)
    except Exception:
        record = {"items": []}
    changed_ratings = _count_changed_ratings(record)
    continue_prompt = build_external_continue_prompt(session, changed_ratings=changed_ratings)

    matched_runs: list[dict[str, Any]] = []
    if runs_dir().is_dir():
        for p in sorted(runs_dir().glob("*.json"), reverse=True):
            try:
                d = get(p.stem)
            except ValueError:
                d = None
            if not isinstance(d, dict):
                continue
            if d.get("session") == session or d.get("resumed_from") == session:
                matched_runs.append(d)

    if matched_runs:
        last_run = matched_runs[0]
        agent_session = next(
            (str(r.get("agent_session")).strip() for r in matched_runs if str(r.get("agent_session") or "").strip()),
            None,
        )
        harness = str(
            last_run.get("harness")
            or ("builtin" if last_run.get("builtin") else "claude")
        ).strip()
        harness_label = harnesses.HARNESS_LABELS.get(harness, harness)
        can_resume = bool(agent_session and harness in harnesses.RESUMABLE_HARNESSES)
        return {
            "source": "app",
            "harness": harness,
            "harness_label": harness_label,
            "agent_session": agent_session,
            "can_resume_same_chat": can_resume,
            "last_run": {
                "run": last_run.get("run"),
                "state": last_run.get("state"),
                "started": last_run.get("started"),
                "finished": last_run.get("finished"),
                "harness": harness,
                "harness_label": harness_label,
                "agent_session": agent_session,
                "resumed_chat": bool(last_run.get("resumed_chat")),
            },
            "continue_prompt": continue_prompt,
            "changed_ratings": changed_ratings,
        }

    return {
        "source": "external",
        "harness": None,
        "harness_label": None,
        "agent_session": None,
        "can_resume_same_chat": False,
        "last_run": None,
        "continue_prompt": continue_prompt,
        "changed_ratings": changed_ratings,
    }


def build_resume_prompt(
    session: str,
    *,
    batches: int = 0,
    skill: bool = True,
    continuing_conversation: bool = False,
) -> str:
    """Build the prompt to resume an interrupted or paused deep-search session.

    Reads only the small screening record and optional summary; never reads the
    (potentially huge) candidate pool file (docs/APP_DATA.md).
    """
    from ..search import deep as deep_search, screening

    if not deep_search.has_pool(session):
        raise ValueError("无法继续检索：这不是深度检索会话")
    record = screening.load(session)
    limit_note = f"本次最多再筛 {int(batches)} 批，" if batches else ""

    if continuing_conversation:
        changed = _count_changed_ratings(record)
        changed_note = f"（手动调整了 {changed} 篇评分）" if changed else ""
        return (
            f"我已在 LitLoop 桌面端复核完上一批文献评分{changed_note}。请继续会话 {session}："
            f"先调用 litloop_deep_search(action='status', session={session!r}) 查看进度与未评文献，"
            f"如有 unrated 先用 action='rate' 评完，再调用 action='next' 生成并逐篇评分下一批，"
            f"{limit_note}返回 stop_suggested=true 时停止。"
            "不要提问、不要下载、不要推送 Zotero；结束时用两三句中文汇报筛选会话名、总篇数和各相关度数量。"
        )

    summary = db.load_summary(session)

    question = str(record.get("question") or "").strip()
    head = f"{'用 litloop 技能' if skill else ''}继续深度检索会话 {session}" + (f"：{question}" if question else "")
    parts = [head]
    criteria = [str(c).strip() for c in (record.get("criteria") or []) if str(c).strip()]
    if criteria:
        parts.append("纳入标准（按这些判断，不要改）：\n" + "\n".join(f"- {c}" for c in criteria))
    if isinstance(summary, dict):
        screened = int(summary.get("screened") or 0)
        pool_size = int(summary.get("pool") or 0)
        done_batches = len(summary.get("batches") or [])
        parts.append(f"已有进度：已生成 {done_batches} 批，已评分 {screened}/{pool_size} 篇。")
    else:
        parts.append("上次检索在生成首批结果前中断，候选池基础记录已保存。")
    if skill:
        parts.append(
            f"不要重新调用 litloop_deep_search(action='start')。请从这个会话接着做："
            f"先调用 litloop_deep_search(action='status', session={session!r}) 查看进度与 unrated；"
            f"如果 unrated 非空，先按编号用 action='rate' 评完并保存，再调用 action='next' 继续生成并筛选下一批，"
            f"{limit_note}返回 stop_suggested=true 时停止。"
        )
    else:
        parts.append(
            f"不要重新调用 deep_search_start。请从这个会话接着做："
            f"先调用 deep_search_status(session={session!r}) 查看进度与 unrated；"
            f"如果 unrated 非空，先按编号用 deep_search_rate 评完并保存，再调用 deep_search_next(session={session!r}) 继续生成并筛选下一批，"
            f"{limit_note}返回 stop_suggested=true 时停止。"
        )
    parts.append(
        "这是从 LitLoop 桌面 app 发起的任务，用户不在对话里：不要提问、不要等确认，"
        "需要用户决定的事写进最后的汇报；不要下载、不要推送 Zotero。"
        "结束时用两三句中文汇报筛选会话名、总篇数和各相关度数量。"
    )
    return "\n\n".join(parts)


def _launch(
    run: str,
    data: dict[str, Any],
    prompt: str,
    builtin: bool,
    *,
    harness_id: str | None = None,
    resume_agent_session: str | None = None,
) -> dict[str, Any]:
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
    cfg = _config.load_config()
    cmd, extra_env, resolved_harness = harnesses.build_invocation(
        cfg, harness_id=harness_id, resume_session=resume_agent_session
    )
    data["harness"] = resolved_harness
    data["harness_label"] = harnesses.HARNESS_LABELS.get(resolved_harness, resolved_harness)
    _write(run, data)
    try:
        proc = subprocess.Popen(
            cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            cwd=str(Path.home()), env={**os.environ, "PYTHONUTF8": "1", **extra_env},
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except OSError:
        data.update(state="failed", doing=None, error=f"无法启动 Agent（{cmd[0]}）。在「设置 → Agent」中检查 Agent 命令")
        _write(run, data)
        return data
    with _LOCK:
        _PROCS[run] = proc
    threading.Thread(target=_follow, args=(run, proc, prompt, data), daemon=True).start()
    return data


def start(form: dict[str, Any]) -> dict[str, Any]:
    cfg = _config.load_config()
    harness_id = harnesses.resolve_harness(cfg)
    builtin = harness_id == "builtin" or _backend() == "builtin"
    if builtin:
        harness_id = "builtin"
    prompt = build_prompt(form, skill=not builtin)
    if running():
        raise RuntimeError("已有检索在进行，完成或取消后再开始新的检索")
    run = _new_run_id()
    runs_dir().mkdir(parents=True, exist_ok=True)
    data = {
        "run": run,
        "state": "running",
        "started": datetime.now().isoformat(timespec="seconds"),
        "question": str(form.get("question")).strip(),
        "mode": form.get("mode") or "quick",
        "form": form,
        "prompt": prompt,
        "doing": "正在启动 Agent",
        "events": [],
        "harness": harness_id,
        "harness_label": harnesses.HARNESS_LABELS.get(harness_id, harness_id),
        "session": None,
        "agent_session": None,
        "resumed_chat": False,
        "result": None,
        "error": None,
    }
    return _launch(run, data, prompt, builtin, harness_id=harness_id)


def resume(session: str, *, batches: int = 0, force_new: bool = False) -> dict[str, Any]:
    cfg = _config.load_config()
    configured_harness = harnesses.resolve_harness(cfg)
    builtin = configured_harness == "builtin" or _backend() == "builtin"
    origin = session_origin(session)

    if not builtin and not force_new and origin.get("can_resume_same_chat"):
        harness_id = str(origin.get("harness") or configured_harness)
        resume_agent_session = str(origin.get("agent_session") or "").strip() or None
        continuing = bool(resume_agent_session)
    else:
        harness_id = "builtin" if builtin else configured_harness
        resume_agent_session = None
        continuing = False

    prompt = build_resume_prompt(
        session,
        batches=batches,
        skill=not builtin,
        continuing_conversation=continuing,
    )
    if running():
        raise RuntimeError("已有检索在进行，完成或取消后再开始新的检索")
    from ..search import screening
    record = screening.load(session)
    run = _new_run_id()
    runs_dir().mkdir(parents=True, exist_ok=True)
    question = str(record.get("question") or "").strip() or session
    data = {
        "run": run,
        "state": "running",
        "started": datetime.now().isoformat(timespec="seconds"),
        "question": question,
        "mode": "deep",
        "form": {"question": question, "mode": "deep", "batches": int(batches or 0), "session": session},
        "prompt": prompt,
        "doing": "正在继续深度检索",
        "events": [],
        "harness": harness_id,
        "harness_label": harnesses.HARNESS_LABELS.get(harness_id, harness_id),
        "session": session,
        "resumed_from": session,
        "agent_session": resume_agent_session,
        "resumed_chat": continuing,
        "result": None,
        "error": None,
    }
    return _launch(
        run,
        data,
        prompt,
        builtin,
        harness_id=harness_id,
        resume_agent_session=resume_agent_session,
    )


def _event(data: dict[str, Any], text: str) -> None:
    data["events"].append({"t": datetime.now().strftime("%H:%M:%S"), "text": text})
    data["events"] = data["events"][-200:]


def _strip_tool_prefix(raw_name: str) -> str:
    name = str(raw_name or "").strip()
    if "__" in name:
        name = name.split("__")[-1]
    for prefix in ("litloop:", "litloop."):
        if name.startswith(prefix):
            name = name[len(prefix):]
    return name


def _tool_doing_text(name: str, action: str | None = None) -> str:
    text = _TOOL_TEXT.get(name, f"正在调用 {name}")
    if name == "litloop_deep_search" and action:
        text = _DEEP_ACTION_TEXT.get(action, text)
    return text


def _parse_input_action(raw_input: Any) -> str | None:
    if isinstance(raw_input, str) and raw_input.strip():
        try:
            raw_input = json.loads(raw_input)
        except ValueError:
            return None
    if isinstance(raw_input, dict):
        val = raw_input.get("action")
        return str(val) if val else None
    return None


def _stringify_tool_body(body: Any) -> str:
    if isinstance(body, str):
        return body
    if isinstance(body, dict):
        return json.dumps(body, ensure_ascii=False)
    if isinstance(body, list):
        parts: list[str] = []
        for x in body:
            if isinstance(x, str):
                parts.append(x)
            elif isinstance(x, dict):
                if isinstance(x.get("text"), str):
                    parts.append(x["text"])
                else:
                    parts.append(json.dumps(x, ensure_ascii=False))
        return " ".join(parts)
    return ""


def _read(data: dict[str, Any], line: str) -> None:
    """One line of JSONL stream output from Claude/WorkBuddy, DSH, or Codex CLI."""
    try:
        msg = json.loads(line)
    except ValueError:
        return
    if not isinstance(msg, dict):
        return

    kind = msg.get("type")

    # Session ID capture across harnesses
    if kind == "system" and msg.get("session_id"):
        data["agent_session"] = msg["session_id"]
    elif msg.get("session_id") and not data.get("agent_session"):
        data["agent_session"] = msg["session_id"]

    if kind == "session" and msg.get("sessionId"):
        data["agent_session"] = msg["sessionId"]
    elif msg.get("sessionId") and not data.get("agent_session"):
        data["agent_session"] = msg["sessionId"]

    if kind == "thread.started" and msg.get("thread_id"):
        data["agent_session"] = msg["thread_id"]

    # DeepSeek Harness status event
    if kind == "status":
        if msg.get("phase") == "turn_start" and data.get("doing") == "正在启动 Agent":
            data["doing"] = "Agent 正在思考"
        return

    # DeepSeek Harness final event
    if kind == "final":
        if msg.get("sessionId"):
            data["agent_session"] = msg["sessionId"]
        reason = msg.get("reason") or {}
        if isinstance(reason, dict) and reason.get("kind") == "error":
            err_obj = reason.get("error")
            err_msg = (
                (err_obj.get("message") if isinstance(err_obj, dict) else str(err_obj or "")).strip()
                or str(msg.get("text") or "").strip()
                or "无法完成检索：DeepSeek Harness 出错"
            )
            data["error"] = err_msg
            data["result"] = None
        else:
            text_out = str(msg.get("text") or "").strip()
            if text_out:
                data["result"] = text_out
        return

    # Codex CLI item.started / item.completed events
    if kind in ("item.started", "item.completed") and isinstance(msg.get("item"), dict):
        item = msg["item"]
        item_type = item.get("type")
        if item_type in ("mcp_tool_call", "tool_call"):
            name = _strip_tool_prefix(str(item.get("tool") or item.get("name") or ""))
            if name:
                data["_last_tool"] = name
                action = _parse_input_action(item.get("arguments") or item.get("input"))
                if kind == "item.started":
                    text = _tool_doing_text(name, action)
                    data["doing"] = text
                    _event(data, text)
                else:
                    raw_out = (
                        item.get("result")
                        if item.get("result") is not None
                        else (item.get("output") if item.get("output") is not None else item.get("content"))
                    )
                    session = _session_from(name, _stringify_tool_body(raw_out))
                    if session:
                        data["session"] = session
        elif kind == "item.completed" and item_type in ("agent_message", "message") and item.get("text"):
            data["result"] = str(item["text"]).strip()
        return

    # Claude Code & WorkBuddy stream-json assistant/user messages
    if kind in ("assistant", "user"):
        for c in (msg.get("message") or {}).get("content") or []:
            if not isinstance(c, dict):
                continue
            if c.get("type") == "tool_use":
                name = _strip_tool_prefix(str(c.get("name") or ""))
                data["_last_tool"] = name
                if c.get("id"):
                    data.setdefault("_tools", {})[c["id"]] = name
                action = _parse_input_action(c.get("input"))
                text = _tool_doing_text(name, action)
                data["doing"] = text
                _event(data, text)
            elif c.get("type") == "tool_result":
                body = _stringify_tool_body(c.get("content"))
                tool_id = c.get("tool_use_id")
                tool = (data.get("_tools") or {}).get(tool_id) if tool_id else None
                if not tool:
                    tool = data.get("_last_tool")
                session = _session_from(tool, body)
                if session:
                    data["session"] = session
            elif c.get("type") == "text" and kind == "assistant" and c.get("text", "").strip():
                data["doing"] = "Agent 正在评分"
        return

    # Claude Code & WorkBuddy result message
    if kind == "result":
        data["result"] = str(msg.get("result") or "").strip() or None
        if msg.get("is_error"):
            data["error"] = data["result"] or "无法完成检索：Agent 出错"
            if re.search(r"authenticat|log ?in|OAuth", data["error"], re.I):
                cmd = _config.load_config().get("agent_command") or "claude"
                data["error"] = f"无法完成检索：Agent 未登录。在终端运行 {cmd} 登录后重试"
            data["result"] = None
        data["cost_usd"] = msg.get("total_cost_usd")
        return

    # DeepSeek Harness / generic flat tool call and tool result events
    is_tool_call = kind in ("tool_call", "tool_use", "tool_start") or (
        kind not in ("tool_result", "tool_end", "tool_output")
        and bool(msg.get("toolName") or msg.get("name"))
        and not any(k in msg for k in ("output", "result"))
    )
    if is_tool_call:
        raw_tool = str(msg.get("toolName") or msg.get("name") or "")
        name = _strip_tool_prefix(raw_tool)
        if name:
            data["_last_tool"] = name
            call_id = msg.get("callId") or msg.get("toolCallId") or msg.get("id")
            if call_id:
                data.setdefault("_tools", {})[call_id] = name
            action = _parse_input_action(msg.get("input") or msg.get("arguments") or msg.get("args"))
            text = _tool_doing_text(name, action)
            data["doing"] = text
            _event(data, text)

    is_tool_result = kind in ("tool_result", "tool_end", "tool_output") or any(
        k in msg for k in ("output", "result", "content")
    )
    if is_tool_result:
        raw_tool = str(msg.get("toolName") or msg.get("name") or "")
        tool = _strip_tool_prefix(raw_tool) if raw_tool else None
        if not tool:
            call_id = msg.get("callId") or msg.get("toolCallId") or msg.get("tool_use_id") or msg.get("id")
            if call_id:
                tool = (data.get("_tools") or {}).get(call_id)
        if not tool:
            tool = data.get("_last_tool")
        raw_body = (
            msg.get("output")
            if msg.get("output") is not None
            else (msg.get("result") if msg.get("result") is not None else msg.get("content"))
        )
        session = _session_from(tool, _stringify_tool_body(raw_body))
        if session:
            data["session"] = session
            return

    # Fallback for any JSON line reporting a LitLoop screening/deep_search session directly
    last_tool = data.get("_last_tool")
    if isinstance(msg.get("session"), str) and msg["session"]:
        if last_tool in _SESSION_TOOLS or any(k in msg for k in ("pool", "screened", "unrated", "items")):
            data["session"] = msg["session"]
    elif last_tool in _SESSION_TOOLS:
        m = _SESSION_RE.search(line)
        if m:
            data["session"] = m.group(1)


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
        from ..agents.runner import default_task_for_step
        from ..agents.system import system_prompt

        form = data.get("form") or {}
        deep = form.get("mode") == "deep"
        batches = int(form.get("batches") or 0) or 5
        # One batch costs a rate + a next call; building the pool and the final
        # answer need a few more (2026-11-06: the old flat 8 stopped deep runs).
        steps = min(4 + 2 * batches, 24) if deep else 8
        out = run_task(prompt, TOOLS, functions=FUNCTIONS, config=_config.load_config(),
                       system=system_prompt(), max_steps=steps, on_event=on_event,
                       task_for_step=default_task_for_step)
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
