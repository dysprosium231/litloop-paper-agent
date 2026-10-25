"""Tests for title-to-DOI resolver in litloop.resolver."""

from __future__ import annotations

from typing import Any
import requests

from litloop.citations.paper_list import PaperEntry
from litloop.resolver import batch_resolve, resolve_title_to_doi


class DummyResponse:
    def __init__(self, data: dict[str, Any], status_code: int = 200):
        self._data = data
        self.status_code = status_code

    def json(self) -> dict[str, Any]:
        return self._data


class DummySession:
    def __init__(self, response_or_exc: Any):
        self.response_or_exc = response_or_exc
        self.calls: list[dict[str, Any]] = []

    def get(self, url: str, params: dict[str, Any] | None = None, timeout: Any = None) -> Any:
        self.calls.append({"url": url, "params": params, "timeout": timeout})
        if isinstance(self.response_or_exc, Exception):
            raise self.response_or_exc
        return self.response_or_exc


def test_resolve_title_short_or_empty(monkeypatch):
    called = False

    def fake_get_session(config):
        nonlocal called
        called = True
        return DummySession(DummyResponse({}))

    monkeypatch.setattr("litloop.network._get_session", fake_get_session)

    assert resolve_title_to_doi("", {}) is None
    assert resolve_title_to_doi("   ", {}) is None
    assert resolve_title_to_doi("Short", {}) is None
    assert not called, "Short titles should exit before issuing network requests"


def test_resolve_title_exact_match(monkeypatch):
    canned = {
        "results": [
            {
                "title": "Attention Is All You Need",
                "doi": "https://doi.org/10.48550/arXiv.1706.03762",
            }
        ]
    }
    dummy_session = DummySession(DummyResponse(canned))
    monkeypatch.setattr("litloop.network._get_session", lambda cfg: dummy_session)

    doi = resolve_title_to_doi("Attention Is All You Need", {})
    assert doi == "10.48550/arXiv.1706.03762"
    assert len(dummy_session.calls) == 1
    assert dummy_session.calls[0]["params"]["search"] == "Attention Is All You Need"


def test_resolve_title_near_match(monkeypatch):
    canned = {
        "results": [
            {
                "title": "Deep Residual Learning for Image Recognition",
                "doi": "https://doi.org/10.1109/CVPR.2016.90",
            }
        ]
    }
    dummy_session = DummySession(DummyResponse(canned))
    monkeypatch.setattr("litloop.network._get_session", lambda cfg: dummy_session)

    # Near match: minor punctuation or trailing text, SequenceMatcher ratio >= 0.75
    query = "Deep Residual Learning for Image Recognition:"
    doi = resolve_title_to_doi(query, {})
    assert doi == "10.1109/CVPR.2016.90"


def test_resolve_title_no_match_low_similarity(monkeypatch):
    canned = {
        "results": [
            {
                "title": "Quantum Computing in the NISQ Era and Beyond",
                "doi": "https://doi.org/10.22331/q-2018-08-06-79",
            }
        ]
    }
    dummy_session = DummySession(DummyResponse(canned))
    monkeypatch.setattr("litloop.network._get_session", lambda cfg: dummy_session)

    query = "Deep Residual Learning for Image Recognition"
    doi = resolve_title_to_doi(query, {})
    assert doi is None


def test_resolve_title_no_results(monkeypatch):
    dummy_session = DummySession(DummyResponse({"results": []}))
    monkeypatch.setattr("litloop.network._get_session", lambda cfg: dummy_session)

    assert resolve_title_to_doi("A Very Obscure Paper Title", {}) is None


def test_resolve_title_normalization(monkeypatch):
    canned = {
        "results": [
            {
                "title": "attention is all you need",
                "doi": "https://doi.org/10.48550/arXiv.1706.03762",
            }
        ]
    }
    dummy_session = DummySession(DummyResponse(canned))
    monkeypatch.setattr("litloop.network._get_session", lambda cfg: dummy_session)

    # Test extra spaces and uppercase
    query = "   ATTENTION IS ALL YOU NEED   \n"
    doi = resolve_title_to_doi(query, {})
    assert doi == "10.48550/arXiv.1706.03762"


def test_resolve_title_missing_doi_in_work(monkeypatch):
    canned = {
        "results": [
            {
                "title": "A Great Paper Without DOI",
                "doi": "",
            }
        ]
    }
    dummy_session = DummySession(DummyResponse(canned))
    monkeypatch.setattr("litloop.network._get_session", lambda cfg: dummy_session)

    assert resolve_title_to_doi("A Great Paper Without DOI", {}) is None


def test_resolve_title_http_failure_and_exception(monkeypatch):
    dummy_session = DummySession(DummyResponse({}, status_code=500))
    monkeypatch.setattr("litloop.network._get_session", lambda cfg: dummy_session)

    assert resolve_title_to_doi("A Valid Title That Fails HTTP", {}) is None

    error_session = DummySession(requests.ConnectionError("Connection failed"))
    monkeypatch.setattr("litloop.network._get_session", lambda cfg: error_session)
    assert resolve_title_to_doi("A Valid Title That Raises ConnectionError", {}) is None


def test_batch_resolve(monkeypatch):
    def fake_resolve(title: str, config: dict[str, Any]) -> str | None:
        if "ResNet" in title:
            return "10.1109/CVPR.2016.90"
        return None

    monkeypatch.setattr("litloop.resolver.resolve_title_to_doi", fake_resolve)

    entries = [
        PaperEntry(title="Transformer paper", doi="10.48550/arXiv.1706.03762"),
        PaperEntry(title="", doi=""),
        PaperEntry(title="ResNet paper", doi=""),
        PaperEntry(title="Unknown paper title", doi=""),
    ]

    result = batch_resolve(entries, {}, delay=0)
    stats = result["stats"]
    assert stats["total"] == 4
    assert stats["already_has_doi"] == 1
    assert stats["resolved_by_title"] == 1
    assert stats["unresolvable"] == 2

    # Check updated entry
    assert entries[0].doi == "10.48550/arXiv.1706.03762"
    assert entries[2].doi == "10.1109/CVPR.2016.90"
    assert entries[3].doi == ""
