"""One call shape over the Anthropic Messages API and the OpenAI-compatible one.

Both give back the same dict: ``{"text", "tool_calls": [{"name", "arguments"}],
"stop", "usage"}``. Nothing here knows about tasks, prompts or the app; the
runner that drives a task is built on top of ``chat()``.
"""

from __future__ import annotations

import json
import time
from typing import Any
from urllib.parse import urlparse

import requests

from ..network import proxy_dict, select_proxy_for_url, user_agent

PROVIDERS: dict[str, dict[str, str]] = {
    "anthropic": {"label": "Anthropic", "base_url": "https://api.anthropic.com", "protocol": "anthropic"},
    "openai": {
        "label": "OpenAI 兼容",
        "base_url": "https://api.openai.com/v1",
        "protocol": "openai",
    },
}

ANTHROPIC_VERSION = "2023-06-01"
_LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}


def model_config(config: dict[str, Any] | None = None) -> dict[str, str]:
    """The configured model, with the provider's defaults filled in."""
    config = config or {}
    provider = (config.get("builtin_agent_provider") or "openai").strip().lower()
    if provider not in PROVIDERS:
        provider = "openai"
    base_url = (config.get("builtin_agent_base_url") or "").strip() or PROVIDERS[provider]["base_url"]
    return {
        "provider": provider,
        "protocol": PROVIDERS[provider]["protocol"],
        "base_url": base_url.rstrip("/"),
        "api_key": (config.get("builtin_agent_api_key") or "").strip(),
        "model": (config.get("builtin_agent_model") or "").strip(),
    }


def _endpoint(cfg: dict[str, str]) -> str:
    if cfg["protocol"] == "anthropic":
        return f"{cfg['base_url']}/v1/messages"
    return f"{cfg['base_url']}/chat/completions"


def _headers(cfg: dict[str, str]) -> dict[str, str]:
    if cfg["protocol"] == "anthropic":
        return {"x-api-key": cfg["api_key"], "anthropic-version": ANTHROPIC_VERSION,
                "content-type": "application/json"}
    return {"Authorization": f"Bearer {cfg['api_key']}", "content-type": "application/json"}


def _payload(cfg: dict[str, str], messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None,
             max_tokens: int, system: str) -> dict[str, Any]:
    body: dict[str, Any] = {"model": cfg["model"], "max_tokens": max_tokens}
    if cfg["protocol"] == "anthropic":
        body["messages"] = [m for m in messages if m.get("role") != "system"]
        if system:
            body["system"] = system
        if tools:
            body["tools"] = [{"name": t["name"], "description": t.get("description", ""),
                              "input_schema": t.get("parameters") or {"type": "object", "properties": {}}}
                             for t in tools]
        return body
    body["messages"] = ([{"role": "system", "content": system}] if system else []) + messages
    if tools:
        body["tools"] = [{"type": "function",
                          "function": {"name": t["name"], "description": t.get("description", ""),
                                       "parameters": t.get("parameters") or {"type": "object", "properties": {}}}}
                         for t in tools]
    return body


def _parse(cfg: dict[str, str], data: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {"text": "", "tool_calls": [], "stop": "", "usage": data.get("usage") or {}}
    if cfg["protocol"] == "anthropic":
        for block in data.get("content") or []:
            if block.get("type") == "text":
                out["text"] += block.get("text", "")
            elif block.get("type") == "tool_use":
                out["tool_calls"].append({"name": block.get("name", ""),
                                          "arguments": block.get("input") or {}})
        out["stop"] = data.get("stop_reason") or ""
        return out
    for choice in data.get("choices") or []:
        message = choice.get("message") or {}
        out["text"] += message.get("content") or ""
        for call in message.get("tool_calls") or []:
            fn = call.get("function") or {}
            raw = fn.get("arguments") or "{}"
            try:
                args = json.loads(raw) if isinstance(raw, str) else dict(raw)
            except ValueError:
                args = {}
            out["tool_calls"].append({"name": fn.get("name", ""), "arguments": args})
        out["stop"] = choice.get("finish_reason") or ""
    return out


def _post(url: str, headers: dict[str, str], body: dict[str, Any], config: dict[str, Any],
          timeout: float) -> dict[str, Any]:
    host = (urlparse(url).hostname or "").lower()
    proxies = {} if host in _LOCAL_HOSTS else proxy_dict(select_proxy_for_url(url, config))
    resp = requests.post(url, headers={**headers, "User-Agent": user_agent()}, json=body,
                         timeout=timeout, proxies=proxies)
    if resp.status_code >= 400:
        detail = (resp.text or "").strip().replace("\n", " ")[:300]
        raise RuntimeError(f"HTTP {resp.status_code}: {detail or resp.reason}")
    return resp.json()


def chat(messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None, *,
         config: dict[str, Any] | None = None, system: str = "", max_tokens: int = 2048,
         timeout: float = 120) -> dict[str, Any]:
    """One round trip. ``tools`` are ``{"name", "description", "parameters"}``."""
    cfg = model_config(config)
    if not cfg["model"]:
        raise ValueError("还没有填模型名")
    if not cfg["api_key"] and cfg["protocol"] == "anthropic":
        raise ValueError("还没有填 API Key")
    if not system:  # a system message passed in the list works too
        system = "\n".join(m.get("content") or "" for m in messages
                           if m.get("role") == "system").strip()
    body = _payload(cfg, messages, tools, max_tokens, system)
    data = _post(_endpoint(cfg), _headers(cfg), body, config or {}, timeout)
    return _parse(cfg, data)


_PING_TOOL = {
    "name": "ping",
    "description": "Answer with the given number. Call this instead of replying in text.",
    "parameters": {"type": "object", "properties": {"n": {"type": "integer"}}, "required": ["n"]},
}


def test_model(config: dict[str, Any] | None = None, timeout: float = 30) -> dict[str, Any]:
    """Ask the model to call a tool; that is the one thing a task runner needs."""
    cfg = model_config(config)
    if not cfg["model"]:
        return {"ok": False, "detail": "还没有填模型名"}
    if not cfg["api_key"] and cfg["protocol"] == "anthropic":
        return {"ok": False, "detail": "还没有填 API Key"}
    started = time.monotonic()
    try:
        reply = chat([{"role": "user", "content": "调用 ping，n 用 1。"}], [_PING_TOOL],
                     config=config, max_tokens=256, timeout=timeout)
    except Exception as exc:
        return {"ok": False, "detail": str(exc)[:300]}
    took = time.monotonic() - started
    name = f"{cfg['provider']} · {cfg['model']}"
    calls = [c for c in reply["tool_calls"] if c["name"] == "ping"]
    if not calls:
        text = (reply["text"] or "").strip().replace("\n", " ")[:120]
        return {"ok": False, "detail": f"{name} 没有发起工具调用（回了：{text or '空'}），不能用来跑任务"}
    return {"ok": True, "detail": f"{name} 支持工具调用（{took:.1f} 秒）"}
