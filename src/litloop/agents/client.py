"""One call shape over the Anthropic Messages API and the OpenAI-compatible one.

Both give back the same dict: ``{"text", "tool_calls": [{"name", "arguments"}],
"stop", "usage"}``. Nothing here knows about tasks, prompts or the app; the
runner that drives a task is built on top of ``chat()``.
"""

from __future__ import annotations

import itertools
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, Callable, Iterable
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

PROVIDER_PRESETS: list[dict[str, Any]] = [
    {
        "id": "deepseek",
        "label": "DeepSeek",
        "protocol": "openai",
        "base_url": "https://api.deepseek.com/v1",
        "model": "deepseek-chat",
        "plan_model": "deepseek-reasoner",
        "rate_model": "deepseek-chat",
        "needs_key": True,
        "note": "性价比高，国内直连稳定；规划用 reasoner、评分用 chat",
        "link": "https://platform.deepseek.com/api_keys",
    },
    {
        "id": "qwen",
        "label": "通义千问",
        "protocol": "openai",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "model": "qwen-plus",
        "plan_model": "qwen-max",
        "rate_model": "qwen-turbo",
        "needs_key": True,
        "note": "阿里云百炼 OpenAI 兼容接口，国内直连快",
        "link": "https://bailian.console.aliyun.com/",
    },
    {
        "id": "kimi",
        "label": "Kimi (Moonshot)",
        "protocol": "openai",
        "base_url": "https://api.moonshot.cn/v1",
        "model": "moonshot-v1-32k",
        "plan_model": "moonshot-v1-32k",
        "rate_model": "moonshot-v1-8k",
        "needs_key": True,
        "note": "月之暗面长文本模型，适合带大量摘要的筛选",
        "link": "https://platform.moonshot.cn/console/api-keys",
    },
    {
        "id": "glm",
        "label": "智谱 GLM",
        "protocol": "openai",
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "model": "glm-4-plus",
        "plan_model": "glm-4-plus",
        "rate_model": "glm-4-flash",
        "needs_key": True,
        "note": "glm-4-flash 免费/极低价，适合批量评分",
        "link": "https://open.bigmodel.cn/usercenter/apikeys",
    },
    {
        "id": "siliconflow",
        "label": "硅基流动",
        "protocol": "openai",
        "base_url": "https://api.siliconflow.cn/v1",
        "model": "deepseek-ai/DeepSeek-V3",
        "plan_model": "deepseek-ai/DeepSeek-R1",
        "rate_model": "deepseek-ai/DeepSeek-V3",
        "needs_key": True,
        "note": "国内聚合平台，支持 DeepSeek-V3/R1 等开源模型",
        "link": "https://cloud.siliconflow.cn/account/ak",
    },
    {
        "id": "openai",
        "label": "OpenAI",
        "protocol": "openai",
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-4.1",
        "plan_model": "gpt-4.1",
        "rate_model": "gpt-4.1-mini",
        "needs_key": True,
        "note": "官方接口，需代理或海外网络",
        "link": "https://platform.openai.com/api-keys",
    },
    {
        "id": "anthropic",
        "label": "Anthropic (Claude)",
        "protocol": "anthropic",
        "base_url": "https://api.anthropic.com",
        "model": "claude-sonnet-4-5",
        "plan_model": "claude-sonnet-4-5",
        "rate_model": "claude-haiku-4-5",
        "needs_key": True,
        "note": "Anthropic 原生 Messages API，需代理或海外网络",
        "link": "https://console.anthropic.com/settings/keys",
    },
    {
        "id": "openrouter",
        "label": "OpenRouter",
        "protocol": "openai",
        "base_url": "https://openrouter.ai/api/v1",
        "model": "deepseek/deepseek-chat-v3-0324",
        "plan_model": "anthropic/claude-sonnet-4",
        "rate_model": "openai/gpt-4.1-mini",
        "needs_key": True,
        "note": "一个 Key 切换多家大模型",
        "link": "https://openrouter.ai/keys",
    },
    {
        "id": "ollama",
        "label": "本地 Ollama",
        "protocol": "openai",
        "base_url": "http://127.0.0.1:11434/v1",
        "model": "qwen2.5:14b",
        "plan_model": "qwen2.5:14b",
        "rate_model": "qwen2.5:7b",
        "max_workers": 1,
        "needs_key": False,
        "note": "本机运行，无需 API Key，数据不出本地",
        "link": "",
    },
]

_PRESET_BY_ID: dict[str, dict[str, Any]] = {p["id"]: p for p in PROVIDER_PRESETS}

# Reference pricing per 1M tokens: (currency, input_price_per_1m, output_price_per_1m).
_MODEL_PRICING: dict[str, tuple[str, float, float]] = {
    "deepseek-chat": ("CNY", 2.0, 8.0),
    "deepseek-reasoner": ("CNY", 4.0, 16.0),
    "qwen-plus": ("CNY", 0.8, 2.0),
    "qwen-turbo": ("CNY", 0.3, 0.6),
    "qwen-max": ("CNY", 2.4, 9.6),
    "glm-4-flash": ("CNY", 0.0, 0.0),
    "glm-4-plus": ("CNY", 5.0, 5.0),
    "gpt-4.1": ("USD", 2.0, 8.0),
    "gpt-4.1-mini": ("USD", 0.4, 1.6),
    "claude-sonnet-4-5": ("USD", 3.0, 15.0),
    "claude-haiku-4-5": ("USD", 0.8, 4.0),
}


def get_preset(preset_id: str) -> dict[str, Any] | None:
    """Return a copy of the preset dict for ``preset_id``, or ``None`` if unknown."""
    key = (preset_id or "").strip().lower()
    preset = _PRESET_BY_ID.get(key)
    return dict(preset) if preset is not None else None


def normalize_usage(usage: dict[str, Any] | None) -> dict[str, int]:
    """Normalize OpenAI / Anthropic token usage dicts into a common shape."""
    if not isinstance(usage, dict):
        return {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}

    def _int(val: Any) -> int:
        try:
            return max(0, int(val or 0))
        except (TypeError, ValueError):
            return 0

    inp = _int(usage.get("input_tokens") if "input_tokens" in usage else usage.get("prompt_tokens"))
    out = _int(usage.get("output_tokens") if "output_tokens" in usage else usage.get("completion_tokens"))
    tot = _int(usage.get("total_tokens"))
    if tot == 0 and (inp or out):
        tot = inp + out
    return {"input_tokens": inp, "output_tokens": out, "total_tokens": tot}


def estimate_cost(usage_by_model: dict[str, dict[str, int]]) -> dict[str, Any]:
    """Aggregate token usage across models and estimate reference cost when all models are known."""
    total_in = 0
    total_out = 0
    total_all = 0
    by_model: dict[str, dict[str, int]] = {}
    currencies: set[str] = set()
    unknown = False
    cost_sum = 0.0

    for raw_model, raw_usage in (usage_by_model or {}).items():
        model = str(raw_model or "").strip()
        if not model:
            continue
        norm = normalize_usage(raw_usage)
        by_model[model] = norm
        total_in += norm["input_tokens"]
        total_out += norm["output_tokens"]
        total_all += norm["total_tokens"]

        pricing = _MODEL_PRICING.get(model)
        if pricing is None:
            unknown = True
        else:
            cur, p_in, p_out = pricing
            currencies.add(cur)
            cost_sum += (norm["input_tokens"] * p_in + norm["output_tokens"] * p_out) / 1_000_000.0

    if not by_model or unknown or len(currencies) != 1:
        currency = None
        estimated = None
        cost_text = None
    else:
        currency = next(iter(currencies))
        estimated = round(cost_sum, 4)
        symbol = "¥" if currency == "CNY" else "$"
        if 0 < estimated < 0.01:
            cost_text = f"约 {symbol}{estimated:.4f}"
        else:
            cost_text = f"约 {symbol}{estimated:.2f}"

    return {
        "input_tokens": total_in,
        "output_tokens": total_out,
        "total_tokens": total_all,
        "by_model": by_model,
        "currency": currency,
        "estimated": estimated,
        "cost_text": cost_text,
    }

ANTHROPIC_VERSION = "2023-06-01"
REASONING_EFFORTS = ("off", "low", "medium", "high")
_ANTHROPIC_THINKING_BUDGET: dict[str, int] = {"low": 1024, "medium": 4096, "high": 8192}
_LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}
_THINK_CLOSED_RE = re.compile(r"<think\b[^>]*>.*?</think>", re.IGNORECASE | re.DOTALL)
_THINK_UNCLOSED_RE = re.compile(r"<think\b[^>]*>.*\Z", re.IGNORECASE | re.DOTALL)


def strip_thinking(text: str) -> str:
    """Strip ``<think>...</think>`` blocks (including unclosed trailing blocks) and trim whitespace."""
    s = str(text or "")
    s = _THINK_CLOSED_RE.sub("", s)
    s = _THINK_UNCLOSED_RE.sub("", s)
    return s.strip()


def recommended_concurrency(config: dict[str, Any] | None = None, *, task: str = "rate") -> int:
    """Recommend worker concurrency based on whether the configured endpoint is local."""
    cfg = model_config(config, task=task)
    host = (urlparse(cfg.get("base_url") or "").hostname or "").strip().lower()
    if host in _LOCAL_HOSTS:
        return 1
    return 3


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
    raw_effort = str(config.get("builtin_agent_reasoning_effort") or "off").strip().lower()
    effort = raw_effort if raw_effort in REASONING_EFFORTS else "off"
    if task == "rate":
        effort = "off"
    return {
        "provider": provider,
        "protocol": PROVIDERS[provider]["protocol"],
        "base_url": base_url.rstrip("/"),
        "api_key": (config.get("builtin_agent_api_key") or "").strip(),
        "model": model,
        "reasoning_effort": effort,
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
    effort = (cfg.get("reasoning_effort") or "off").strip().lower()
    if cfg["protocol"] == "anthropic":
        body["messages"] = [m for m in messages if m.get("role") != "system"]
        if system:
            body["system"] = system
        if tools:
            body["tools"] = [{"name": t["name"], "description": t.get("description", ""),
                              "input_schema": t.get("parameters") or {"type": "object", "properties": {}}}
                             for t in tools]
        if effort in _ANTHROPIC_THINKING_BUDGET:
            budget = _ANTHROPIC_THINKING_BUDGET[effort]
            body["thinking"] = {"type": "enabled", "budget_tokens": budget}
            body["max_tokens"] = max(max_tokens, budget + 2048)
        return body
    body["messages"] = ([{"role": "system", "content": system}] if system else []) + messages
    if tools:
        body["tools"] = [{"type": "function",
                          "function": {"name": t["name"], "description": t.get("description", ""),
                                       "parameters": t.get("parameters") or {"type": "object", "properties": {}}}}
                         for t in tools]
    if effort in ("low", "medium", "high"):
        is_deepseek = "deepseek.com" in cfg["base_url"].lower() or cfg["model"].lower().startswith("deepseek-")
        if is_deepseek:
            body["thinking"] = {"type": "enabled"}
        else:
            body["reasoning_effort"] = effort
        body["max_tokens"] = max(max_tokens, 4096)
    return body


def _parse(cfg: dict[str, str], data: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {"text": "", "tool_calls": [], "stop": "", "usage": data.get("usage") or {}}
    if cfg["protocol"] == "anthropic":
        anth_reasoning: list[str] = []
        for block in data.get("content") or []:
            if block.get("type") == "text":
                out["text"] += block.get("text", "")
            elif block.get("type") == "thinking":
                t = block.get("thinking")
                if t:
                    anth_reasoning.append(str(t))
            elif block.get("type") == "tool_use":
                out["tool_calls"].append({"id": block.get("id", ""), "name": block.get("name", ""),
                                          "arguments": block.get("input") or {}})
        if anth_reasoning:
            out["reasoning"] = "".join(anth_reasoning)
        out["stop"] = data.get("stop_reason") or ""
        return out
    reasoning_parts: list[str] = []
    for choice in data.get("choices") or []:
        message = choice.get("message") or {}
        reasoning = message.get("reasoning_content")
        if reasoning:
            reasoning_parts.append(str(reasoning))
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
    if reasoning_parts:
        out["reasoning"] = "".join(reasoning_parts)
    return out


# Hosts where Python's direct socket timed out (e.g. when a TUN proxy sets a
# ``process_name: python.exe -> direct`` rule for campus downloads, which also
# forces python.exe's foreign LLM calls off the proxy) while system curl works.
_CURL_HOSTS: set[str] = set()
_RETRY_STATUS_CODES = frozenset({429, 500, 502, 503, 504})
_MAX_RETRIES = 3
_MAX_RETRY_DELAY = 15.0
_sleep = time.sleep


class _RetryableHTTPError(RuntimeError):
    def __init__(self, status_code: int, detail: str, retry_after: str | None = None) -> None:
        super().__init__(f"HTTP {status_code}: {detail}")
        self.status_code = status_code
        self.retry_after = retry_after


def _retry_delay(attempt: int, retry_after: str | None = None) -> float:
    if retry_after:
        try:
            val = float(str(retry_after).strip())
            return min(_MAX_RETRY_DELAY, max(0.1, val))
        except ValueError:
            pass
    return min(_MAX_RETRY_DELAY, 1.0 * (2 ** attempt))


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
        if status_code in _RETRY_STATUS_CODES:
            raise _RetryableHTTPError(status_code, detail)
        raise RuntimeError(f"HTTP {status_code}: {detail}")
    return json.loads(raw_body)


def _post_once(url: str, req_headers: dict[str, str], body: dict[str, Any], proxy_url: str,
               proxies: dict[str, str], host: str, timeout: float) -> dict[str, Any]:
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
        detail = (resp.text or "").strip().replace("\n", " ")[:300] or str(resp.reason or "")
        if resp.status_code in _RETRY_STATUS_CODES:
            raise _RetryableHTTPError(resp.status_code, detail, resp.headers.get("Retry-After"))
        raise RuntimeError(f"HTTP {resp.status_code}: {detail}")
    return resp.json()


def _post(url: str, headers: dict[str, str], body: dict[str, Any], config: dict[str, Any],
          timeout: float) -> dict[str, Any]:
    host = (urlparse(url).hostname or "").lower()
    proxy_url = "" if host in _LOCAL_HOSTS else select_proxy_for_url(url, config)
    proxies = {} if host in _LOCAL_HOSTS else proxy_dict(proxy_url)
    req_headers = {**headers, "User-Agent": user_agent()}
    for attempt in range(_MAX_RETRIES + 1):
        try:
            return _post_once(url, req_headers, body, proxy_url, proxies, host, timeout)
        except _RetryableHTTPError as exc:
            if attempt >= _MAX_RETRIES:
                raise RuntimeError(str(exc)) from exc
            _sleep(_retry_delay(attempt, exc.retry_after))
    raise RuntimeError("unreachable")


def chat(messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None, *,
         config: dict[str, Any] | None = None, system: str = "", max_tokens: int = 2048,
         timeout: float = 120, task: str = "default", reasoning_effort: str | None = None) -> dict[str, Any]:
    """One round trip. ``tools`` are ``{"name", "description", "parameters"}``."""
    cfg = model_config(config, task=task)
    if reasoning_effort and reasoning_effort.strip().lower() in REASONING_EFFORTS:
        cfg["reasoning_effort"] = reasoning_effort.strip().lower()
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


def _parse_openai_sse(
    lines: Iterable[str],
    on_delta: Callable[[str], None] | None = None,
    on_reasoning: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    text_parts: list[str] = []
    reasoning_parts: list[str] = []
    tool_buf: dict[int, dict[str, str]] = {}
    stop = ""
    usage: dict[str, Any] = {}

    for raw_line in lines:
        line = (raw_line or "").strip()
        if not line or not line.startswith("data:"):
            continue
        payload_str = line[5:].strip()
        if payload_str == "[DONE]":
            break
        try:
            chunk = json.loads(payload_str)
        except ValueError:
            continue
        if not isinstance(chunk, dict):
            continue
        if chunk.get("error") and not chunk.get("choices"):
            raise RuntimeError(str(chunk["error"]))
        if isinstance(chunk.get("usage"), dict):
            usage = chunk["usage"]
        for choice in chunk.get("choices") or []:
            if choice.get("finish_reason"):
                stop = str(choice["finish_reason"])
            delta = choice.get("delta") or {}
            reasoning = delta.get("reasoning_content")
            if reasoning:
                r_str = str(reasoning)
                reasoning_parts.append(r_str)
                if on_reasoning is not None:
                    on_reasoning(r_str)
            content = delta.get("content")
            if content:
                text_parts.append(content)
                if on_delta is not None:
                    on_delta(content)
            for tc in delta.get("tool_calls") or []:
                idx = int(tc.get("index") or 0)
                entry = tool_buf.setdefault(idx, {"id": "", "name": "", "arguments": ""})
                if tc.get("id"):
                    entry["id"] = str(tc["id"])
                fn = tc.get("function") or {}
                if fn.get("name"):
                    entry["name"] += str(fn["name"])
                if fn.get("arguments") is not None:
                    entry["arguments"] += str(fn["arguments"])

    tool_calls: list[dict[str, Any]] = []
    for idx in sorted(tool_buf):
        entry = tool_buf[idx]
        raw_args = entry["arguments"] or "{}"
        try:
            args = json.loads(raw_args)
        except ValueError:
            args = {}
        if not isinstance(args, dict):
            args = {}
        tool_calls.append({"id": entry["id"], "name": entry["name"], "arguments": args})

    out: dict[str, Any] = {"text": "".join(text_parts), "tool_calls": tool_calls, "stop": stop, "usage": usage}
    if reasoning_parts:
        out["reasoning"] = "".join(reasoning_parts)
    return out


def _parse_anthropic_sse(
    lines: Iterable[str],
    on_delta: Callable[[str], None] | None = None,
    on_reasoning: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    current_event = ""
    text_parts: list[str] = []
    reasoning_parts: list[str] = []
    blocks: dict[int, dict[str, Any]] = {}
    tool_calls: list[dict[str, Any]] = []
    stop = ""
    usage: dict[str, Any] = {}

    def _finish_block(blk: dict[str, Any]) -> None:
        if blk.get("type") != "tool_use":
            return
        raw_in = blk.get("input_json") or "{}"
        try:
            args = json.loads(raw_in)
        except ValueError:
            args = {}
        if not isinstance(args, dict):
            args = {}
        tool_calls.append({"id": blk.get("id", ""), "name": blk.get("name", ""), "arguments": args})

    for raw_line in lines:
        line = (raw_line or "").strip()
        if not line:
            current_event = ""
            continue
        if line.startswith("event:"):
            current_event = line[6:].strip()
            continue
        if not line.startswith("data:"):
            continue
        payload_str = line[5:].strip()
        try:
            ev = json.loads(payload_str)
        except ValueError:
            continue
        if not isinstance(ev, dict):
            continue
        etype = ev.get("type") or current_event
        if etype == "error":
            raise RuntimeError(str(ev.get("error") or ev))
        if etype == "message_start":
            msg = ev.get("message") or {}
            if isinstance(msg.get("usage"), dict):
                usage.update(msg["usage"])
        elif etype == "content_block_start":
            idx = int(ev.get("index") or 0)
            cb = ev.get("content_block") or {}
            btype = cb.get("type")
            blocks[idx] = {
                "type": btype,
                "id": str(cb.get("id") or ""),
                "name": str(cb.get("name") or ""),
                "input_json": "",
            }
            if btype == "text" and cb.get("text"):
                txt = str(cb["text"])
                text_parts.append(txt)
                if on_delta is not None:
                    on_delta(txt)
            elif btype == "thinking" and cb.get("thinking"):
                r_txt = str(cb["thinking"])
                reasoning_parts.append(r_txt)
                if on_reasoning is not None:
                    on_reasoning(r_txt)
        elif etype == "content_block_delta":
            idx = int(ev.get("index") or 0)
            delta = ev.get("delta") or {}
            dtype = delta.get("type")
            if dtype == "text_delta":
                txt = delta.get("text") or ""
                if txt:
                    text_parts.append(txt)
                    if on_delta is not None:
                        on_delta(txt)
            elif dtype == "thinking_delta":
                r_txt = delta.get("thinking") or ""
                if r_txt:
                    reasoning_parts.append(r_txt)
                    if on_reasoning is not None:
                        on_reasoning(r_txt)
            elif dtype == "input_json_delta":
                blk = blocks.get(idx)
                if blk is not None:
                    blk["input_json"] += delta.get("partial_json") or ""
        elif etype == "content_block_stop":
            idx = int(ev.get("index") or 0)
            blk = blocks.pop(idx, None)
            if blk is not None:
                _finish_block(blk)
        elif etype == "message_delta":
            delta = ev.get("delta") or {}
            if delta.get("stop_reason"):
                stop = str(delta["stop_reason"])
            u = ev.get("usage")
            if isinstance(u, dict):
                usage.update(u)

    for idx in sorted(blocks):
        _finish_block(blocks[idx])

    out: dict[str, Any] = {"text": "".join(text_parts), "tool_calls": tool_calls, "stop": stop, "usage": usage}
    if reasoning_parts:
        out["reasoning"] = "".join(reasoning_parts)
    return out


def _parse_sse_lines(
    cfg: dict[str, str],
    lines: Iterable[str],
    on_delta: Callable[[str], None] | None,
    on_reasoning: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    if cfg["protocol"] == "anthropic":
        return _parse_anthropic_sse(lines, on_delta, on_reasoning=on_reasoning)
    return _parse_openai_sse(lines, on_delta, on_reasoning=on_reasoning)


def _curl_http_status(header_path: Path) -> int:
    if not header_path.is_file():
        return 200
    status = 200
    for line in header_path.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.upper().startswith("HTTP/"):
            parts = line.split()
            if len(parts) >= 2 and parts[1].isdigit():
                status = int(parts[1])
    return status


def _post_stream_curl(
    cfg: dict[str, str],
    url: str,
    headers: dict[str, str],
    body: dict[str, Any],
    proxy_url: str,
    timeout: float,
    on_delta: Callable[[str], None] | None,
    on_reasoning: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    curl = _curl_bin()
    if not curl:
        raise RuntimeError("curl is not available")
    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        body_path = tmp_dir / "body.json"
        header_path = tmp_dir / "headers.txt"
        body_path.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
        cmd = [
            curl,
            "-sSN",
            "--no-buffer",
            "-m",
            str(max(10, int(timeout))),
            "-D",
            str(header_path),
        ]
        if os.name == "nt":
            cmd.append("--ssl-no-revoke")
        if proxy_url:
            cmd += ["--proxy", proxy_url]
        cfg_lines = [f'url = "{url}"', f'data-binary = "@{body_path.as_posix()}"']
        for k, v in headers.items():
            escaped = str(v).replace("\\", "\\\\").replace('"', '\\"')
            cfg_lines.append(f'header = "{k}: {escaped}"')
        cmd += ["--config", "-"]
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        assert proc.stdin is not None and proc.stdout is not None and proc.stderr is not None
        proc.stdin.write("\n".join(cfg_lines))
        proc.stdin.close()

        first_non_empty = ""
        for line in proc.stdout:
            if line.strip():
                first_non_empty = line
                break

        if first_non_empty.lstrip().startswith("{"):
            rest = proc.stdout.read()
            raw_body = first_non_empty + rest
            stderr_out = proc.stderr.read()
            rc = proc.wait()
            status_code = _curl_http_status(header_path)
            if rc != 0:
                raise RuntimeError(f"curl failed (rc={rc}): {(stderr_out or raw_body).strip()[:240]}")
            if status_code >= 400:
                detail = raw_body.strip().replace("\n", " ")[:300]
                if status_code in _RETRY_STATUS_CODES:
                    raise _RetryableHTTPError(status_code, detail)
                raise RuntimeError(f"HTTP {status_code}: {detail}")
            data = json.loads(raw_body)
            if isinstance(data, dict) and data.get("error") and not data.get("choices") and not data.get("content"):
                raise RuntimeError(str(data["error"]))
            res = _parse(cfg, data)
            if res.get("reasoning") and on_reasoning is not None:
                on_reasoning(res["reasoning"])
            if res["text"] and on_delta is not None:
                on_delta(res["text"])
            return res

        lines = itertools.chain([first_non_empty] if first_non_empty else [], proc.stdout)
        res = _parse_sse_lines(cfg, lines, on_delta, on_reasoning=on_reasoning)
        stderr_out = proc.stderr.read()
        rc = proc.wait()
        status_code = _curl_http_status(header_path)
        if status_code >= 400:
            detail = stderr_out.strip()[:300]
            if status_code in _RETRY_STATUS_CODES:
                raise _RetryableHTTPError(status_code, detail)
            raise RuntimeError(f"HTTP {status_code}: {detail}")
        if rc != 0:
            raise RuntimeError(f"curl failed (rc={rc}): {stderr_out.strip()[:240]}")
        return res


def _post_stream_once(
    cfg: dict[str, str],
    url: str,
    req_headers: dict[str, str],
    body: dict[str, Any],
    proxy_url: str,
    proxies: dict[str, str],
    host: str,
    timeout: float,
    on_delta: Callable[[str], None] | None,
    on_reasoning: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    if host and host in _CURL_HOSTS and _curl_bin():
        return _post_stream_curl(cfg, url, req_headers, body, proxy_url, timeout, on_delta, on_reasoning=on_reasoning)
    conn_timeout = min(6.0, float(timeout)) if timeout else 6.0
    try:
        resp = requests.post(
            url,
            headers=req_headers,
            json=body,
            timeout=(conn_timeout, timeout),
            proxies=proxies,
            stream=True,
        )
    except (requests.Timeout, requests.ConnectionError):
        if host and host not in _LOCAL_HOSTS and _curl_bin():
            res = _post_stream_curl(cfg, url, req_headers, body, proxy_url, timeout, on_delta, on_reasoning=on_reasoning)
            _CURL_HOSTS.add(host)
            return res
        raise
    with resp:
        if resp.status_code >= 400:
            detail = (resp.text or "").strip().replace("\n", " ")[:300] or str(resp.reason or "")
            if resp.status_code in _RETRY_STATUS_CODES:
                raise _RetryableHTTPError(resp.status_code, detail, resp.headers.get("Retry-After"))
            raise RuntimeError(f"HTTP {resp.status_code}: {detail}")
        ctype = (resp.headers.get("content-type") or "").lower()
        if "application/json" in ctype:
            data = resp.json()
            res = _parse(cfg, data)
            if res.get("reasoning") and on_reasoning is not None:
                on_reasoning(res["reasoning"])
            if res["text"] and on_delta is not None:
                on_delta(res["text"])
            return res
        resp.encoding = resp.encoding or "utf-8"
        return _parse_sse_lines(cfg, resp.iter_lines(decode_unicode=True), on_delta, on_reasoning=on_reasoning)


def _post_stream(
    cfg: dict[str, str],
    url: str,
    headers: dict[str, str],
    body: dict[str, Any],
    config: dict[str, Any],
    timeout: float,
    on_delta: Callable[[str], None] | None,
    on_reasoning: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    host = (urlparse(url).hostname or "").lower()
    proxy_url = "" if host in _LOCAL_HOSTS else select_proxy_for_url(url, config)
    proxies = {} if host in _LOCAL_HOSTS else proxy_dict(proxy_url)
    req_headers = {**headers, "User-Agent": user_agent()}
    for attempt in range(_MAX_RETRIES + 1):
        try:
            return _post_stream_once(
                cfg, url, req_headers, body, proxy_url, proxies, host, timeout, on_delta, on_reasoning=on_reasoning
            )
        except _RetryableHTTPError as exc:
            if attempt >= _MAX_RETRIES:
                raise RuntimeError(str(exc)) from exc
            _sleep(_retry_delay(attempt, exc.retry_after))
    raise RuntimeError("unreachable")


def chat_stream(
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
    *,
    config: dict[str, Any] | None = None,
    system: str = "",
    max_tokens: int = 2048,
    timeout: float = 120,
    task: str = "default",
    reasoning_effort: str | None = None,
    on_delta: Callable[[str], None] | None = None,
    on_reasoning: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Streaming round trip over OpenAI or Anthropic SSE, returning the same shape as ``chat()``."""
    cfg = model_config(config, task=task)
    if reasoning_effort and reasoning_effort.strip().lower() in REASONING_EFFORTS:
        cfg["reasoning_effort"] = reasoning_effort.strip().lower()
    if not cfg["model"]:
        raise ValueError("还没有填模型名")
    if not cfg["api_key"] and cfg["protocol"] == "anthropic":
        raise ValueError("还没有填 API Key")
    if not system:
        system = "\n".join(
            m.get("content") or "" for m in messages if m.get("role") == "system"
        ).strip()
    body = _payload(cfg, messages, tools, max_tokens, system)
    body["stream"] = True
    if cfg["protocol"] == "openai":
        body["stream_options"] = {"include_usage": True}
    return _post_stream(cfg, _endpoint(cfg), _headers(cfg), body, config or {}, timeout, on_delta, on_reasoning=on_reasoning)


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
