"""Rules the download engine must keep while its state moves into DownloadAttempt.

1. A skip stops the paper soon, even while the session check is running.
2. Once a paper is settled, a late result is dropped (its file removed).
3. Cancelling one job does not cancel downloads started after it.
4. One paper's browser error does not leak into another paper's sources.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

import litloop.identifiers as ids
import litloop.sources as src
from litloop import _publisher_strategies_core as core

PDF = b"%PDF-1.4\n" + b"0" * 20000 + b"\n%%EOF"


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    monkeypatch.setattr(ids, "validate_doi", lambda doi: (True, ""))
    import litloop.citation as cit
    monkeypatch.setattr(cit, "fetch_metadata", lambda *a, **k: {})  # no network
    import litloop.session_heal as sh
    monkeypatch.setattr(sh, "ensure_institutional_sessions", lambda *a, **k: {})
    monkeypatch.setattr(src, "_build_institutional_sources", lambda *a, **k: [])
    yield
    src._SKIPPED.clear()


def _writer(delay: float = 0.0):
    def fn(doi, output_path, config):
        time.sleep(delay)
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(output_path).write_bytes(PDF)
        return {"success": True, "file": str(output_path), "source": "Fake"}
    return fn


def _nothing(doi, output_path, config):
    return None


def test_skip_stops_during_the_session_check(monkeypatch, tmp_path):
    import litloop.session_heal as sh
    reached = []

    def slow_heal(*a, **k):  # a session check that waits ~3 s (e.g. for a login)
        time.sleep(3)
        return {}

    monkeypatch.setattr(sh, "ensure_institutional_sessions", slow_heal)
    monkeypatch.setattr(src, "_build_free_sources", lambda doi, config: [(_nothing, "Crossref")])
    monkeypatch.setattr(src, "_build_institutional_sources", lambda *a, **k: [(_nothing, "WebVPN")])
    monkeypatch.setattr(src, "_run_institutional_sequence", lambda *a, **k: reached.append(1))
    doi = "10.9999/invariant.skip"
    threading.Timer(0.5, src.skip_download, args=(doi,)).start()
    t = time.time()
    r = src.download(doi, str(tmp_path), _institutional=True)
    assert time.time() - t < 5
    assert not reached, "a skipped paper went on to the school routes"
    assert "skipped" in str(r.get("reason") or r.get("error") or ""), r
    assert not r.get("success")


def test_late_result_after_settle_is_dropped(tmp_path):
    doi = "10.9999/invariant.late"
    out = tmp_path / "late.pdf"
    with src._INFLIGHT_LOCK:
        src._SETTLED.add(doi)
    try:
        r = src._try_source(_writer(), doi, out, {}, "Fake")
    finally:
        src._SETTLED.discard(doi)
    assert r is None
    assert not out.exists(), "a settled paper kept a late duplicate file"


def test_cancel_does_not_touch_later_downloads(monkeypatch, tmp_path):
    monkeypatch.setattr(src, "_build_free_sources", lambda doi, config: [(_writer(), "Crossref")])
    src.cancel_downloads()
    r = src.download("10.9999/invariant.after-cancel", str(tmp_path), _institutional=False)
    assert r.get("success"), r


def test_browser_error_does_not_leak_between_papers(tmp_path):
    seen = []

    def lane(doi, output_path, config):
        seen.append(core.get_last_error()[0])
        return None

    t = threading.Thread(target=lambda: core._set_error("paywall", "login_required"))
    t.start()
    t.join()
    worker = threading.Thread(target=lambda: src._try_source(lane, "10.9999/invariant.leak",
                                                             tmp_path / "x.pdf", {}, "ElsevierBrowser"))
    worker.start()
    worker.join()
    assert seen == [""], f"a fresh source saw another paper's error: {seen}"


def test_a_paper_closes_only_its_own_browsers(monkeypatch):
    from types import SimpleNamespace
    import litloop.browser_backend as bb
    import litloop.browser_engine as be
    killed = []
    monkeypatch.setattr(be, "_tree_kill", lambda proc: killed.append(proc))
    monkeypatch.setattr(bb, "_LAUNCHED", [])
    owner = {"v": "10.9999/a"}
    monkeypatch.setattr(bb, "OWNER", lambda: owner["v"])

    def fake(proc):
        return SimpleNamespace(_impl_obj=SimpleNamespace(
            _connection=SimpleNamespace(_transport=SimpleNamespace(_proc=proc))))

    bb._remember(fake("a"))
    owner["v"] = "10.9999/b"
    bb._remember(fake("b"))
    assert bb.close_browsers_since(0.0, owner="10.9999/a") == 1
    assert killed == ["a"]


def test_steps_are_recorded(monkeypatch, tmp_path):
    from litloop.sources import attempt
    seen = {}
    real_end = attempt.end

    def end(key):
        att = real_end(key)
        seen["steps"] = list(att.steps) if att else None
        return att

    monkeypatch.setattr(attempt, "end", end)
    monkeypatch.setattr(src, "_build_free_sources", lambda doi, config: [(_writer(), "Crossref")])
    r = src.download("10.9999/invariant.steps", str(tmp_path), _institutional=False)
    assert r.get("success")
    assert seen["steps"] and seen["steps"][0]["source"] == "Crossref"
    assert {"route", "seconds", "ok"} <= set(seen["steps"][0])
