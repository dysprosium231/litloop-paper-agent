"""A search that finds nothing says why: rate limits, failures, and the keyless path."""

from __future__ import annotations

from typing import Any

import litloop.search.quick as quick
from litloop.cli.main import _explain_empty_search


class _Resp:
    def __init__(self, status: int, body: dict[str, Any] | None = None) -> None:
        self.status_code = status
        self._body = body or {}
        self.reason = "Too Many Requests" if status == 429 else ""

    def json(self) -> dict[str, Any]:
        return self._body


class _Session:
    def __init__(self, resp: _Resp) -> None:
        self.resp = resp
        self.calls: list[dict[str, Any]] = []
        self.headers: dict[str, str] = {}

    def get(self, url: str, params: dict[str, Any] | None = None, **kw: Any) -> _Resp:
        self.calls.append({"url": url, "params": dict(params or {})})
        return self.resp


def test_a_rate_limited_openalex_is_remembered(monkeypatch):
    session = _Session(_Resp(429, {"error": "Rate limit exceeded"}))
    monkeypatch.setattr(quick, "_plain_session", lambda: session)
    monkeypatch.setattr(quick, "load_config", lambda: {})

    errors: list[str] = []
    assert quick._search_openalex("ceramic aerogel", 3, None, None, None, errors) == []
    assert any("429" in e for e in errors)


def test_keyword_search_asks_for_title_and_abstract(monkeypatch):
    """`search=` answers 429 without a key; the filter form does not."""
    session = _Session(_Resp(200, {"results": []}))
    monkeypatch.setattr(quick, "_plain_session", lambda: session)
    monkeypatch.setattr(quick, "load_config", lambda: {})

    quick._search_openalex("ceramic aerogel", 3, None, None, None, [])

    params = session.calls[0]["params"]
    assert params["filter"] == "title_and_abstract.search:ceramic aerogel"
    assert "search" not in params


def test_stats_carry_the_reason_a_search_was_empty(monkeypatch):
    def fake_collect_one(query, pool, yf, yt, sort, errors):
        errors.append("OpenAlex 429: Rate limit exceeded")
        return []

    monkeypatch.setattr(quick, "_collect_one", fake_collect_one)
    stats: dict[str, Any] = {}
    assert quick.search_papers_v2("ceramic aerogel", limit=3, stats=stats) == []
    assert stats["errors"] == ["OpenAlex 429: Rate limit exceeded"]


def test_the_cli_says_what_to_do_on_a_rate_limit(capsys):
    _explain_empty_search(["OpenAlex 429: Rate limit exceeded"])
    err = capsys.readouterr().err
    assert "429" in err
    assert "openalex.org/settings/api" in err


def test_the_cli_says_something_even_without_errors(capsys):
    _explain_empty_search([])
    assert "没有搜到结果" in capsys.readouterr().err
