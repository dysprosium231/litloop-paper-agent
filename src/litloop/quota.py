"""Usage and limits of the rate-limited APIs LitLoop calls.

Every request to OpenAlex, Scopus, Semantic Scholar or Crossref goes through
``slot(provider)`` (a per-provider cap on parallel requests, so bursts do not
trip per-second throttles) and ``record(provider, response)``, which keeps:

* requests made today, and how many were throttled (HTTP 429);
* what the provider last said about its budget (remaining, limit, reset),
  read from the X-RateLimit-* headers when it sends them.

The numbers live in ~/.litloop/api_usage.json, shared by the MCP server, the
command line and scripts. ``summary()`` is what diagnostics, deep search and
(later) the app show; ``warnings()`` names the providers close to their limit.
The caps only coordinate threads within one process; separate processes
still see each other's usage through the file.
"""

from __future__ import annotations

import json
import threading
import time
from contextlib import contextmanager
from typing import Any

from . import config as _config

# Official limits (checked 2026-10-16; full list with sources in docs/API_LIMITS.md).
# PER_SECOND stays a little below each published per-second figure. CONCURRENCY
# is official only for Crossref (1 public / 3 polite); the providers below that
# publish no concurrency limit get LitLoop's own conservative choice.
# * OpenAlex: 30 requests/s with a key, but 5/s for searches with more than five
#   boolean operators; budget 10,000 credits/day with a free key (list 1 credit,
#   keyword search 10), reset at midnight UTC.
#   https://help.openalex.org/how-to-use-the-api/rate-limits-and-authentication
# * Scopus Abstract Retrieval: 9 requests/s, 10,000 per week.
#   https://dev.elsevier.com/api_key_settings.html
# * Crossref: public pool 5 single-record requests/s, 1 at a time; polite pool
#   (with a contact email) 10/s, 3 at a time.
#   https://crossref.org/blog/announcing-changes-to-rest-api-rate-limits/
# * Semantic Scholar: keyless requests share one pool and are often throttled;
#   a key gives 1 request/s. https://www.semanticscholar.org/product/api
# * Springer Nature Meta API, free plan: 500 requests/day, 100/min.
#   https://dev.springernature.com/subscription/
# Lanes: "openalex" assumes a key (30/s); without one OpenAlex allows 10/s and
# 1/s for boolean-heavy searches, and only $0.10 (1,000 credits) a day.
CONCURRENCY = {"openalex": 4, "openalex_boolean": 2, "openalex_nokey": 2, "openalex_boolean_nokey": 1,
               "scopus": 5, "semantic_scholar": 1, "crossref": 1, "crossref_polite": 3, "springer": 2}
PER_SECOND = {"openalex": 25.0, "openalex_boolean": 4.0, "openalex_nokey": 8.0, "openalex_boolean_nokey": 0.9,
              "scopus": 8.0, "semantic_scholar": 1.0, "crossref": 4.0, "crossref_polite": 8.0, "springer": 1.5}
WARN_FRACTION = 0.1  # warn when less than this share of the budget is left

_lock = threading.Lock()
_slots = {name: threading.Semaphore(n) for name, n in CONCURRENCY.items()}
_next_free = {name: 0.0 for name in PER_SECOND}
_spent: dict[str, int] = {}  # budget units used by this process (OpenAlex credits, others requests)


def _path():
    return _config.DATA_DIR / "api_usage.json"


def _load() -> dict[str, Any]:
    try:
        return json.loads(_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save(data: dict[str, Any]) -> None:
    try:
        _path().parent.mkdir(parents=True, exist_ok=True)
        tmp = _path().with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(_path())
    except OSError:
        pass  # usage tracking must never break a search


@contextmanager
def slot(provider: str):
    """Hold one of ``provider``'s parallel-request slots, spaced to its per-second limit."""
    sem = _slots.get(provider)
    if sem is None:
        yield
        return
    with sem:
        rate = PER_SECOND.get(provider)
        if rate:
            with _lock:
                now = time.monotonic()
                start = max(now, _next_free[provider])
                _next_free[provider] = start + 1.0 / rate
            if start > now:
                time.sleep(start - now)
        yield


def spent(provider: str) -> int:
    """Budget units ``provider`` has used in this process (OpenAlex: credits)."""
    return _spent.get(provider, 0)


def record(provider: str, response: Any = None, *, throttled: bool | None = None) -> None:
    """Count one request to ``provider`` and keep the budget headers it returned."""
    today = time.strftime("%Y-%m-%d")
    headers = getattr(response, "headers", None) or {}
    status = getattr(response, "status_code", None)
    with _lock:
        data = _load()
        p = data.setdefault(provider, {})
        if p.get("day") != today:
            p.update(day=today, requests=0, throttled=0, credits=0)
        p["requests"] = p.get("requests", 0) + 1
        cost = _int(headers.get("X-RateLimit-Credits-Used"))
        cost = 1 if cost is None else cost
        if status is not None and status < 400:
            p["credits"] = p.get("credits", 0) + cost
            _spent[provider] = _spent.get(provider, 0) + cost
        if throttled or status == 429:
            p["throttled"] = p.get("throttled", 0) + 1
            p["last_throttled"] = time.strftime("%Y-%m-%d %H:%M:%S")
        remaining = headers.get("X-RateLimit-Remaining")
        if "QUOTA_EXCEEDED" in str(headers.get("X-ELS-Status") or ""):
            p["remaining"] = 0  # Elsevier drops the Remaining header once the quota is spent
        if remaining is not None:
            p["remaining"] = _int(remaining)
            p["limit"] = _int(headers.get("X-RateLimit-Limit"))
            reset = _int(headers.get("X-RateLimit-Reset"))
            # OpenAlex sends seconds until reset, Scopus an epoch time.
            if reset is not None:
                p["resets_at"] = time.strftime("%Y-%m-%d %H:%M", time.localtime(
                    reset if reset > 10**9 else time.time() + reset))
            p["checked_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        _save(data)


def _int(value: Any) -> int | None:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def summary() -> dict[str, Any]:
    """Per provider: requests today, throttled today, remaining / limit, reset time."""
    today = time.strftime("%Y-%m-%d")
    out = {}
    for name, p in _load().items():
        out[name] = {
            "requests_today": p.get("requests", 0) if p.get("day") == today else 0,
            "credits_today": p.get("credits", 0) if p.get("day") == today else 0,
            "throttled_today": p.get("throttled", 0) if p.get("day") == today else 0,
            "remaining": p.get("remaining"), "limit": p.get("limit"),
            "resets_at": p.get("resets_at"), "checked_at": p.get("checked_at"),
            "concurrency": CONCURRENCY.get(name),
        }
    return out


def warnings() -> list[str]:
    """Plain-language notes for providers that are nearly out of budget or being throttled."""
    notes = []
    for name, s in summary().items():
        if s["remaining"] is not None and s["limit"] and s["remaining"] < s["limit"] * WARN_FRACTION:
            notes.append(f"{name}: only {s['remaining']} of {s['limit']} requests left"
                         + (f", resets at {s['resets_at']}" if s["resets_at"] else ""))
        if s["throttled_today"] >= 5:
            notes.append(f"{name}: throttled {s['throttled_today']} times today; requests are slowed down")
    return notes


def openalex_live(timeout: float = 20.0) -> dict[str, Any] | None:
    """OpenAlex's own account of today's budget, from its /rate-limit endpoint
    (no credits charged): used, remaining, limit, reset time, price per call type."""
    import requests
    key = str(_config.load_config().get("openalex_api_key") or "").strip()
    try:
        r = requests.get("https://api.openalex.org/rate-limit", params={"api_key": key} if key else None,
                         timeout=timeout, proxies={"http": None, "https": None})
        if r.status_code != 200:
            return {"error": f"HTTP {r.status_code}"}
        info = r.json().get("rate_limit") or {}
    except Exception as exc:  # diagnostics only
        return {"error": str(exc)}
    return {"credits_used": info.get("credits_used"), "credits_remaining": info.get("credits_remaining"),
            "credits_limit": info.get("credits_limit"), "resets_at": info.get("resets_at"),
            "credit_costs": info.get("credit_costs"), "has_key": bool(key)}
