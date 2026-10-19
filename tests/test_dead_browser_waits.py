"""Waiting helpers stop at once when the browser is gone, and a stalled host is
not asked again (2026-10-19 retest: a skip took 101 s; SAGE lost 52 s a paper)."""

from __future__ import annotations

import time

import pytest

from litloop import _publisher_strategies_core as core
from litloop import browser_pdf


class _GonePage:
    url = ""

    def _gone(self, *a, **k):
        raise RuntimeError("the browser driver is gone")

    wait_for_load_state = title = evaluate = content = _gone


@pytest.mark.parametrize("call", [
    lambda p: browser_pdf.wait_page_settled(p, max_wait_s=30),
    lambda p: browser_pdf.wait_url_stable(p, max_wait_s=30),
    lambda p: browser_pdf.find_page_pdf_link(p, max_wait_s=30),
])
def test_a_killed_browser_ends_the_wait_at_once(call):
    t = time.time()
    call(_GonePage())
    assert time.time() - t < 2


def test_other_errors_still_wait(monkeypatch):
    class Loading(_GonePage):
        def _busy(self, *a, **k):
            raise RuntimeError("Execution context was destroyed, most likely because of a navigation")
        evaluate = _busy
    monkeypatch.setattr(browser_pdf.time, "sleep", lambda s: None)
    assert browser_pdf.find_page_pdf_link(Loading(), max_wait_s=0.3) is None  # kept polling until the budget


def test_sage_skips_the_direct_pdf_tries():
    assert core._direct_pdf_urls("10.1177/02670836241301975", "SAGE") == []


def test_a_host_that_timed_out_is_not_asked_again(monkeypatch, tmp_path):
    import requests
    calls = []

    def stall(self, url, **kw):
        calls.append(url)
        raise requests.ReadTimeout("read timed out")

    monkeypatch.setattr(requests.Session, "get", stall)
    monkeypatch.setattr(core, "_STALLED_HOSTS", {})
    out = tmp_path / "x.pdf"
    assert core._try_http_download("https://slow.example.org/a.pdf", out, {}) is False
    assert core._try_http_download("https://slow.example.org/b.pdf", out, {}) is False
    assert calls == ["https://slow.example.org/a.pdf"]  # the second one was skipped
    core._STALLED_HOSTS["slow.example.org"] -= core._STALLED_HOST_SECONDS + 1
    core._try_http_download("https://slow.example.org/c.pdf", out, {})
    assert len(calls) == 2  # and asked again once the time is up
