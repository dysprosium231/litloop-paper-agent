"""Tests for WebVPN host boundary check and pipeline/source timing logs.

Offline tests: no network calls, no real browser, isolated config/cache.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

import pytest

import litloop.browser_backend as bb
from litloop.pipeline import QueueEntry, run_scihub_then_single
import litloop.pipeline as pipeline
import litloop.sources as sources
from litloop.sources import _try_source, instsci


class _FakeBrowser:
    def __init__(self):
        self.closed = False
        self.context = _FakeContext()

    def new_context(self, *args, **kwargs):
        return self.context

    def close(self):
        self.closed = True


class _FakeContext:
    def __init__(self):
        self.page = _FakePage()
        self.added_cookies = []

    def new_page(self):
        return self.page

    def cookies(self):
        return []

    def add_cookies(self, cookies):
        self.added_cookies.extend(cookies)


class _FakePage:
    def __init__(self):
        self.url = ""
        self.handlers = {}
        self.timeouts = []
        self.gotos = []

    def on(self, event, handler):
        self.handlers[event] = handler

    def goto(self, url, **kwargs):
        self.gotos.append(url)

    def wait_for_timeout(self, ms):
        self.timeouts.append(ms)

    def title(self):
        return ""

    def evaluate(self, expr, *args):
        return None

    def content(self):
        return ""


# -----------------------------------------------------------------------------
# 1. instsci._try_instsci_browser host boundary check
# -----------------------------------------------------------------------------

def test_webvpn_browser_left_webvpn_gives_up(monkeypatch, tmp_path, caplog):
    """When landing on an external non-school auth host (e.g. Elsevier ShibAuth),
    log 'Left WebVPN' and return None without waiting for user login."""
    caplog.set_level(logging.INFO)

    fake_browser = _FakeBrowser()
    monkeypatch.setattr(bb, "is_available", lambda: True)
    monkeypatch.setattr(bb, "launch", lambda **kw: fake_browser)

    monkeypatch.setattr(instsci, "_get_webvpn_base", lambda cfg: "https://d.buaa.edu.cn")
    monkeypatch.setattr(instsci, "_resolve_doi_url", lambda doi: f"https://doi.org/{doi}")
    monkeypatch.setattr(instsci, "convert_url", lambda url, base, cfg=None: f"{base}/converted")
    monkeypatch.setattr(instsci, "instsci_cookie_path", lambda cfg: tmp_path / "instsci-cookies.json")

    # Settled page is outside the school domain (buaa.edu.cn)
    monkeypatch.setattr(
        instsci,
        "wait_page_settled",
        lambda page, **kw: ("登录", "https://auth.elsevier.com/ShibAuth/somepath"),
    )

    waited_calls = []
    monkeypatch.setattr(sources, "note_user_waiting", lambda what: waited_calls.append(what))

    result = instsci._try_instsci_browser("10.1016/j.test", tmp_path / "paper.pdf", {})

    assert result is None
    assert fake_browser.closed is True
    assert "Left WebVPN" in caplog.text
    # Must NOT wait for login
    assert waited_calls == []
    assert fake_browser.context.page.timeouts == []


def test_webvpn_browser_school_sso_does_not_give_up(monkeypatch, tmp_path, caplog):
    """A URL on sso.buaa.edu.cn matches the school domain and must not give up
    at the Left WebVPN check; it enters the login wait path."""
    caplog.set_level(logging.INFO)

    fake_browser = _FakeBrowser()
    monkeypatch.setattr(bb, "is_available", lambda: True)
    monkeypatch.setattr(bb, "launch", lambda **kw: fake_browser)

    monkeypatch.setattr(instsci, "_get_webvpn_base", lambda cfg: "https://d.buaa.edu.cn")
    monkeypatch.setattr(instsci, "_resolve_doi_url", lambda doi: f"https://doi.org/{doi}")
    monkeypatch.setattr(instsci, "convert_url", lambda url, base, cfg=None: f"{base}/converted")
    monkeypatch.setattr(instsci, "instsci_cookie_path", lambda cfg: tmp_path / "instsci-cookies.json")
    monkeypatch.setattr(instsci, "find_page_pdf_link", lambda page, **kw: None)
    monkeypatch.setattr(instsci, "_construct_publisher_pdf_url", lambda doi, url: None)
    monkeypatch.setattr(instsci.time, "sleep", lambda s: None)

    # First settle lands on sso.buaa.edu.cn; second settle after wait simulates login completion
    settled_sequence = [
        ("登录", "https://sso.buaa.edu.cn/cas/login"),
        ("Article Title", "https://d.buaa.edu.cn/article"),
    ]
    monkeypatch.setattr(
        instsci,
        "wait_page_settled",
        lambda page, **kw: settled_sequence.pop(0) if settled_sequence else ("Article Title", "https://d.buaa.edu.cn/article"),
    )

    waited_calls = []
    monkeypatch.setattr(sources, "note_user_waiting", lambda what: waited_calls.append(what))

    result = instsci._try_instsci_browser("10.1016/j.test", tmp_path / "paper.pdf", {})

    assert "Left WebVPN" not in caplog.text
    # Entered login waiting rather than giving up
    assert "login" in waited_calls
    assert fake_browser.closed is True


def test_webvpn_browser_gateway_host_does_not_give_up(monkeypatch, tmp_path, caplog):
    """A URL directly on the WebVPN gateway host (d.buaa.edu.cn) does not give up."""
    caplog.set_level(logging.INFO)

    fake_browser = _FakeBrowser()
    monkeypatch.setattr(bb, "is_available", lambda: True)
    monkeypatch.setattr(bb, "launch", lambda **kw: fake_browser)

    monkeypatch.setattr(instsci, "_get_webvpn_base", lambda cfg: "https://d.buaa.edu.cn")
    monkeypatch.setattr(instsci, "_resolve_doi_url", lambda doi: f"https://doi.org/{doi}")
    monkeypatch.setattr(instsci, "convert_url", lambda url, base, cfg=None: f"{base}/converted")
    monkeypatch.setattr(instsci, "instsci_cookie_path", lambda cfg: tmp_path / "instsci-cookies.json")
    monkeypatch.setattr(instsci, "find_page_pdf_link", lambda page, **kw: None)
    monkeypatch.setattr(instsci, "_construct_publisher_pdf_url", lambda doi, url: None)
    monkeypatch.setattr(instsci.time, "sleep", lambda s: None)

    monkeypatch.setattr(
        instsci,
        "wait_page_settled",
        lambda page, **kw: ("Article Title", "https://d.buaa.edu.cn/https/123/article"),
    )

    result = instsci._try_instsci_browser("10.1016/j.test", tmp_path / "paper.pdf", {})

    assert "Left WebVPN" not in caplog.text
    assert fake_browser.closed is True


# -----------------------------------------------------------------------------
# 2. pipeline.run_scihub_then_single timing logs
# -----------------------------------------------------------------------------

def test_pipeline_run_scihub_then_single_timing_logs(monkeypatch, tmp_path, caplog):
    """run_scihub_then_single logs timing milestones for OA enrichment, one-by-one round, and whole list."""
    caplog.set_level(logging.INFO)

    monkeypatch.setattr(pipeline, "_enrich_oa_urls", lambda entries, cfg: None)
    monkeypatch.setattr(
        sources,
        "download",
        lambda ident, out, **kw: {"success": True, "doi": ident, "file": str(tmp_path / "p.pdf"), "source": "test"},
    )

    entries = [QueueEntry(identifier="10.1000/182", channel="auto")]
    results = run_scihub_then_single(
        entries,
        output_dir=tmp_path / "out",
        config={"scihub_enabled": False},
        allow_grey=False,
    )

    assert len(results) == 1
    assert results[0]["doi"] == "10.1000/182"

    assert "[timing] open-access links looked up" in caplog.text
    assert "[timing] one-by-one round" in caplog.text
    assert "[timing] whole list" in caplog.text


# -----------------------------------------------------------------------------
# 3. sources._try_source timing logs on success and failure
# -----------------------------------------------------------------------------

def test_try_source_timing_logs_success_and_failure(tmp_path, caplog):
    """_try_source logs '[timing] <label>: <n>s, PDF' on success and 'no PDF' on failure."""
    caplog.set_level(logging.INFO)

    dummy_pdf = tmp_path / "test.pdf"

    # 1. Success case
    def _mock_success(doi, out_path, config):
        return {"success": True, "doi": doi, "file": "", "source": "MockSuccess"}

    res_ok = _try_source(_mock_success, "10.1000/182", dummy_pdf, {}, label="MockSuccess")
    assert res_ok is not None and res_ok.get("success") is True
    assert re.search(r"\[timing\] MockSuccess: \d+\.\d+s, PDF", caplog.text) is not None

    # 2. Failure case
    def _mock_failure(doi, out_path, config):
        return None

    caplog.clear()
    res_fail = _try_source(_mock_failure, "10.1000/182", tmp_path / "fail.pdf", {}, label="MockFailure")
    assert res_fail is None
    assert re.search(r"\[timing\] MockFailure: \d+\.\d+s, no PDF", caplog.text) is not None
