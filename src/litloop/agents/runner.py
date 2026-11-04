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


def run_task(goal: str, tools: list[dict[str, Any]], *, functions: dict[str, Tool] | None = None,
             config: dict[str, Any] | None = None, system: str = "", max_steps: int = 8,
             max_tokens: int = 4096, timeout: float = 180,
             on_event: EventHandler | None = None) -> dict[str, Any]:
    """Run until the model answers without calling a tool (or ``max_steps``)."""
    protocol = model_config(config)["protocol"]
    functions = functions or {}
    transcript: list[dict[str, Any]] = [{"role": "user", "text": goal}]
    produced: list[dict[str, Any]] = []

    def emit(kind: str, **data: Any) -> None:
        if on_event:
            on_event(kind, data)

    for step in range(1, max_steps + 1):
        reply = chat(_messages_for(protocol, transcript), tools, config=config, system=system,
                     max_tokens=max_tokens, timeout=timeout)
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
