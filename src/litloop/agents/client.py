"""One call shape over the Anthropic Messages API and the OpenAI-compatible one.

Both give back the same dict: ``{"text", "tool_calls": [{"name", "arguments"}],
"stop", "usage"}``. Nothing here knows about tasks, prompts or the app; the
runner that drives a task is built on top of ``chat()``.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
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


def model_config(config: dict[str, Any] | None = None, *, task: str = "default") -> dict[str, str]:
    """The configured model, with the provider's defaults filled in."""
    if config is None:
        from ..config import load_config
        config = load_config()
    provider = (config.get("builtin_agent_provider") or "openai").strip().lower()
    if provider not in PROVIDERS:
        provider = "openai"
    base_url = (config.get("builtin_agent_base_url") or "").strip() or PROVIDERS[provider]["base_url"]
    model = (config.get("builtin_agent_model") or "").strip()
    if task == "plan":
        model = (config.get("builtin_agent_plan_model") or "").strip() or model
    elif task == "rate":
        model = (config.get("builtin_agent_rate_model") or "").strip() or model
    return {
        "provider": provider,
        "protocol": PROVIDERS[provider]["protocol"],
        "base_url": base_url.rstrip("/"),
        "api_key": (config.get("builtin_agent_api_key") or "").strip(),
        "model": model,
    }


def _endpoint(cfg: dict[str, str]) -> str:
    base = cfg["base_url"].rstrip("/")
    if cfg["protocol"] == "anthropic":
        if base.endswith("/v1/messages") or base.endswith("/messages"):
            return base
        if base.endswith("/v1"):
            return f"{base}/messages"
        return f"{base}/v1/messages"
    if base.endswith("/chat/completions"):
        return base
    return f"{base}/chat/completions"


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
                out["tool_calls"].append({"id": block.get("id", ""), "name": block.get("name", ""),
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
            out["tool_calls"].append({"id": call.get("id", ""), "name": fn.get("name", ""),
                                      "arguments": args})
        out["stop"] = choice.get("finish_reason") or ""
    return out


# Hosts where Python's direct socket timed out (e.g. when a TUN proxy sets a
# ``process_name: python.exe -> direct`` rule for campus downloads, which also
# forces python.exe's foreign LLM calls off the proxy) while system curl works.
_CURL_HOSTS: set[str] = set()


def _curl_bin() -> str | None:
    if os.name == "nt":
        sys_curl = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "curl.exe"
        if sys_curl.is_file():
            return str(sys_curl)
    return shutil.which("curl.exe" if os.name == "nt" else "curl") or shutil.which("curl")


def _post_curl(url: str, headers: dict[str, str], body: dict[str, Any], proxy_url: str,
               timeout: float) -> dict[str, Any]:
    curl = _curl_bin()
    if not curl:
        raise RuntimeError("curl is not available")
    with tempfile.TemporaryDirectory() as tmp:
        body_path = Path(tmp) / "body.json"
        body_path.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
        cmd = [curl, "-sS", "-m", str(max(10, int(timeout))), "-w", "\n%{http_code}"]
        if os.name == "nt":
            cmd.append("--ssl-no-revoke")
        if proxy_url:
            cmd += ["--proxy", proxy_url]
        cfg_lines = [f'url = "{url}"', f'data-binary = "@{body_path.as_posix()}"']
        for k, v in headers.items():
            escaped = str(v).replace("\\", "\\\\").replace('"', '\\"')
            cfg_lines.append(f'header = "{k}: {escaped}"')
        cmd += ["--config", "-"]
        proc = subprocess.run(
            cmd,
            input="\n".join(cfg_lines),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=max(15.0, float(timeout) + 5.0),
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    if proc.returncode != 0:
        raise RuntimeError(f"curl failed (rc={proc.returncode}): {(proc.stderr or proc.stdout).strip()[:240]}")
    out = proc.stdout or ""
    raw_body, _, code_str = out.rpartition("\n")
    try:
        status_code = int(code_str.strip())
    except ValueError:
        status_code = 200
        raw_body = out
    if status_code >= 400:
        detail = raw_body.strip().replace("\n", " ")[:300]
        raise RuntimeError(f"HTTP {status_code}: {detail}")
    return json.loads(raw_body)


def _post(url: str, headers: dict[str, str], body: dict[str, Any], config: dict[str, Any],
          timeout: float) -> dict[str, Any]:
    host = (urlparse(url).hostname or "").lower()
    proxy_url = "" if host in _LOCAL_HOSTS else select_proxy_for_url(url, config)
    proxies = {} if host in _LOCAL_HOSTS else proxy_dict(proxy_url)
    req_headers = {**headers, "User-Agent": user_agent()}
    if host and host in _CURL_HOSTS and _curl_bin():
        return _post_curl(url, req_headers, body, proxy_url, timeout)
    conn_timeout = min(6.0, float(timeout)) if timeout else 6.0
    try:
        resp = requests.post(url, headers=req_headers, json=body,
                             timeout=(conn_timeout, timeout), proxies=proxies)
    except (requests.Timeout, requests.ConnectionError):
        if host and host not in _LOCAL_HOSTS and _curl_bin():
            data = _post_curl(url, req_headers, body, proxy_url, timeout)
            _CURL_HOSTS.add(host)
            return data
        raise
    if resp.status_code >= 400:
        detail = (resp.text or "").strip().replace("\n", " ")[:300]
        raise RuntimeError(f"HTTP {resp.status_code}: {detail or resp.reason}")
    return resp.json()


def chat(messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None, *,
         config: dict[str, Any] | None = None, system: str = "", max_tokens: int = 2048,
         timeout: float = 120, task: str = "default") -> dict[str, Any]:
    """One round trip. ``tools`` are ``{"name", "description", "parameters"}``."""
    cfg = model_config(config, task=task)
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
