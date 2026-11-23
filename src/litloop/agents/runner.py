"""Drive one task: ask the model, run the tools it calls, ask again.

The loop is protocol-agnostic; only the message shapes differ, so the runner
keeps a neutral transcript and converts it per turn. Tool functions are plain
callables that return JSON-serializable values; the schemas handed to the model
are the same ``{"name", "description", "parameters"}`` dicts ``chat()`` takes.
"""

from __future__ import annotations

import json
from typing import Any, Callable

from .client import chat, model_config

Tool = Callable[..., Any]
EventHandler = Callable[[str, dict[str, Any]], None]


class ToolError(Exception):
    """A tool the model asked for does not exist or could not do its job."""


def _json(value: Any) -> str:
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return str(value)


def _call(tools: dict[str, Tool], name: str, arguments: dict[str, Any]) -> str:
    fn = tools.get(name)
    if fn is None:
        raise ToolError(f"没有这个工具：{name}")
    try:
        return _json(fn(**arguments))
    except TypeError as exc:  # wrong or missing arguments
        raise ToolError(f"{name} 的参数不对：{exc}") from exc


def _messages_for(protocol: str, transcript: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Neutral turns -> the shape this protocol expects."""
    out: list[dict[str, Any]] = []
    for turn in transcript:
        role = turn["role"]
        if role in ("user", "system"):
            out.append({"role": role, "content": turn.get("text", "")})
            continue
        calls = turn.get("calls") or []
        if protocol == "anthropic":
            blocks: list[dict[str, Any]] = []
            if turn.get("text"):
                blocks.append({"type": "text", "text": turn["text"]})
            for call in calls:
                blocks.append({"type": "tool_use", "id": call["id"], "name": call["name"],
                               "input": call["arguments"]})
            if blocks:
                out.append({"role": "assistant", "content": blocks})
            results = [{"type": "tool_result", "tool_use_id": call["id"], "content": call["result"]}
                       for call in calls]
            if results:
                out.append({"role": "user", "content": results})
            continue
        out.append({"role": "assistant", "content": turn.get("text") or None,
                    "tool_calls": [{"id": call["id"], "type": "function",
                                    "function": {"name": call["name"],
                                                 "arguments": _json(call["arguments"])}}
                                   for call in calls]})
        for call in calls:
            out.append({"role": "tool", "tool_call_id": call["id"], "content": call["result"]})
    return out


_FETCH_TOOLS = frozenset({"search", "deep_search_start", "deep_search_next", "deep_search_status"})


def default_task_for_step(step: int, produced: list[dict[str, Any]]) -> str:
    """Use the planning model initially, and switch to the rating model right after batches/hits arrive."""
    if step <= 1 or not produced:
        return "plan"
    if str(produced[-1].get("name") or "") in _FETCH_TOOLS:
        return "rate"
    return "plan"


def estimate_search_credits(mode: str, query_count: int, batches: int) -> tuple[str, str]:
    """Rough OpenAlex credit range and a short Chinese explanation (0 credits spent to estimate)."""
    q = max(1, int(query_count or 1))
    if mode == "deep":
        b = max(1, int(batches or 3))
        low = min(300, 80 + q * 20)
        high = max(low + 100, min(1500, 120 + q * 30 + b * 90))
        return f"约 {low}–{high} 积分", f"按 {q} 组检索词、约 {b} 批扩展与补摘要粗略估算（免费 Key 每日 10,000 积分）"
    low = q * 10
    high = q * 40
    return f"约 {low}–{high} 积分", f"按 {q} 组检索词的快速查找粗略估算（免费 Key 每日 10,000 积分）"


def _strip_code_fence(text: str) -> str:
    s = (text or "").strip()
    if s.startswith("```"):
        lines = s.splitlines()
        if len(lines) >= 2 and lines[-1].strip().startswith("```"):
            return "\n".join(lines[1:-1]).strip()
    return s


def plan_search(form: dict[str, Any], *, config: dict[str, Any] | None = None) -> dict[str, Any]:
    """Generate editable search queries, core terms, criteria, and credit estimate without calling search APIs."""
    question = str(form.get("question") or "").strip()
    if not question:
        raise ValueError("请先填写检索问题")
    cfg = model_config(config, task="plan")
    if not cfg["model"]:
        raise ValueError("无法预览检索方案：还没有填内置 Agent 的模型名。在「设置 → 内置 Agent」中填写")
    if not cfg["api_key"] and cfg["protocol"] == "anthropic":
        raise ValueError("无法预览检索方案：还没有填内置 Agent 的 API Key")

    mode = "deep" if form.get("mode") == "deep" else "quick"
    raw_criteria = str(form.get("criteria") or "").strip()
    system = (
        "你是学术文献检索规划助手。只输出一个合法 JSON 对象，不要输出任何额外说明或代码块以外的文字。"
        'JSON 字段固定为：{"queries": ["..."], "core_terms": ["..."], "criteria": ["..."]}。'
        "其中 queries 给 3–5 条空格分隔的纯英文关键词短语（每条 3–6 个词，如 \"pressureless infiltration SiC aluminum\"，不要写 AND/OR、括号或引号）；"
        "core_terms 给 2–4 条用于过滤候选池的英文核心概念词（每个概念一条，同义词用 | 连接，如 \"Al|aluminum|aluminium\"）；"
        "criteria 给 2–5 条简洁明确的中文纳入标准（如果用户已提供纳入标准，保留并微调即可）。"
    )
    user_parts = [f"检索问题：{question}", f"检索方式：{'深度检索' if mode == 'deep' else '快速查找'}"]
    if raw_criteria:
        user_parts.append(f"用户已填写的纳入标准：\n{raw_criteria}")
    if form.get("year_from") or form.get("year_to"):
        user_parts.append(f"年份范围：{form.get('year_from') or '不限'}–{form.get('year_to') or '至今'}")

    reply = chat([{"role": "user", "content": "\n".join(user_parts)}],
                 tools=None, config=config, system=system, max_tokens=1200, timeout=60, task="plan")
    raw_text = _strip_code_fence(str(reply.get("text") or ""))
    try:
        parsed = json.loads(raw_text)
    except ValueError:
        parsed = {}
    if not isinstance(parsed, dict):
        parsed = {}

    queries = [str(x).strip() for x in (parsed.get("queries") or []) if str(x).strip()][:6]
    if not queries:
        queries = [question]
    core_terms = [str(x).strip() for x in (parsed.get("core_terms") or []) if str(x).strip()][:6]
    criteria = [str(x).strip() for x in (parsed.get("criteria") or []) if str(x).strip()][:6]
    if not criteria and raw_criteria:
        criteria = [s.strip() for s in raw_criteria.splitlines() if s.strip()]

    est, note = estimate_search_credits(mode, len(queries), int(form.get("batches") or 0))
    return {
        "queries": queries,
        "core_terms": core_terms,
        "criteria": criteria,
        "estimated_credits": est,
        "credit_note": note,
        "model": cfg["model"],
    }


def run_task(goal: str, tools: list[dict[str, Any]], *, functions: dict[str, Tool] | None = None,
             config: dict[str, Any] | None = None, system: str = "", max_steps: int = 8,
             max_tokens: int = 4096, timeout: float = 180,
             on_event: EventHandler | None = None,
             task_for_step: Callable[[int, list[dict[str, Any]]], str] | None = None) -> dict[str, Any]:
    """Run until the model answers without calling a tool (or ``max_steps``)."""
    protocol = model_config(config)["protocol"]
    functions = functions or {}
    transcript: list[dict[str, Any]] = [{"role": "user", "text": goal}]
    produced: list[dict[str, Any]] = []

    def emit(kind: str, **data: Any) -> None:
        if on_event:
            on_event(kind, data)

    for step in range(1, max_steps + 1):
        step_task = task_for_step(step, produced) if task_for_step else "default"
        reply = chat(_messages_for(protocol, transcript), tools, config=config, system=system,
                     max_tokens=max_tokens, timeout=timeout, task=step_task)
        text = (reply.get("text") or "").strip()
        calls = reply.get("tool_calls") or []
        if text:
            emit("text", text=text, step=step)
        if not calls:
            transcript.append({"role": "assistant", "text": text})
            return {"text": text, "steps": step, "tool_calls": produced,
                    "messages": _messages_for(protocol, transcript)}
        for index, call in enumerate(calls):
            name = call.get("name") or ""
            # Some providers omit the id; a stable one keeps the next turn valid.
            call["id"] = call.get("id") or f"call_{step}_{index}"
            emit("tool", name=name, arguments=call.get("arguments") or {}, step=step)
            try:
                result = _call(functions, name, call.get("arguments") or {})
                call["result"] = result
            except ToolError as exc:
                call["result"] = f"出错：{exc}"
                emit("error", name=name, detail=str(exc), step=step)
            except Exception as exc:  # a tool blowing up must not end the task
                call["result"] = f"出错：{type(exc).__name__}: {exc}"
                emit("error", name=name, detail=str(exc), step=step)
            emit("result", name=name, result=call["result"], step=step)
            produced.append({"name": name, "arguments": call.get("arguments") or {},
                             "result": call["result"]})
        transcript.append({"role": "assistant", "text": text, "calls": calls})

    last = next((c["result"] for c in reversed(produced)), "")
    return {"text": f"到 {max_steps} 步还没有给出结论。最后一步工具返回：{last[:500]}",
            "steps": max_steps, "tool_calls": produced,
            "messages": _messages_for(protocol, transcript)}
