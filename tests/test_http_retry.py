"""Retry helper: 429/5xx and network errors back off, everything else comes straight back."""

import pytest
import requests

from litloop import http_utils


class Resp:
    def __init__(self, status):
        self.status_code = status


def test_a_good_response_is_returned_on_the_first_try(monkeypatch):
    calls = []
    monkeypatch.setattr(http_utils.requests, "request", lambda m, u, **kw: calls.append((m, u, kw)) or Resp(200))
    slept = []
    monkeypatch.setattr(http_utils.time, "sleep", slept.append)
    r = http_utils.request_with_retry("GET", "https://example.test/a")
    assert r.status_code == 200 and len(calls) == 1 and slept == []
    assert calls[0][2]["timeout"] == 30  # a default the caller can override


def test_server_errors_are_retried_with_growing_waits(monkeypatch):
    monkeypatch.setattr(http_utils.requests, "request", lambda m, u, **kw: Resp(503))
    slept = []
    monkeypatch.setattr(http_utils.time, "sleep", slept.append)
    r = http_utils.request_with_retry("GET", "https://example.test/a", max_retries=2, retry_backoff=2.0)
    assert r.status_code == 503
    assert slept == [1.0, 2.0]  # backoff ** attempt, starting at 1 second


def test_a_404_is_not_retried(monkeypatch):
    calls = []
    monkeypatch.setattr(http_utils.requests, "request", lambda m, u, **kw: calls.append(1) or Resp(404))
    assert http_utils.request_with_retry("GET", "https://example.test/a").status_code == 404
    assert len(calls) == 1


def test_a_network_error_is_raised_after_the_last_attempt(monkeypatch):
    attempts = []

    def boom(m, u, **kw):
        attempts.append(1)
        raise requests.ConnectionError("down")

    monkeypatch.setattr(http_utils.requests, "request", boom)
    monkeypatch.setattr(http_utils.time, "sleep", lambda s: None)
    with pytest.raises(requests.ConnectionError):
        http_utils.request_with_retry("GET", "https://example.test/a", max_retries=1)
    assert len(attempts) == 2


def test_a_network_error_can_be_survived(monkeypatch):
    outcomes = [requests.ConnectionError("blip"), Resp(200)]

    def flaky(m, u, **kw):
        out = outcomes.pop(0)
        if isinstance(out, Exception):
            raise out
        return out

    monkeypatch.setattr(http_utils.requests, "request", flaky)
    monkeypatch.setattr(http_utils.time, "sleep", lambda s: None)
    assert http_utils.request_with_retry("GET", "https://example.test/a").status_code == 200
