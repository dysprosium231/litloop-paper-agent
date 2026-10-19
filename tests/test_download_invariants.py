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
    import litloop.pdf_utils as pu
    monkeypatch.setattr(pu, "is_suspicious_pdf", lambda *a, **k: False)  # the fake PDF has one page
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


def test_one_papers_browser_error_is_not_anothers_failure(monkeypatch, tmp_path):
    """Two papers at once: A's lane hits a paywall, B's lane just finds nothing.
    B's final reason must not be A's paywall (the download thread used to read
    a process-wide "last error")."""
    a_failed = threading.Event()

    def lane(doi, output_path, config):
        if doi.endswith(".a"):
            core._set_error("paywall", "login_required")
            a_failed.set()
        else:
            a_failed.wait(5)  # B finishes after A's lane reported
        return None

    monkeypatch.setattr(src, "_build_free_sources", lambda doi, config: [(lane, "ElsevierBrowser")])
    out = {}

    def run(doi):
        out[doi] = src.download(doi, str(tmp_path), _institutional=False)

    threads = [threading.Thread(target=run, args=(d,)) for d in ("10.9999/leak2.a", "10.9999/leak2.b")]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)
    a, b = out["10.9999/leak2.a"], out["10.9999/leak2.b"]
    assert a.get("error_type") == "paywall", a
    assert b.get("error_type") != "paywall" and "paywall" not in str(b.get("reason") or b.get("error")), b
    assert [s.get("error_type") for s in a["attempts"]] == ["paywall"]


def test_a_browser_opened_in_a_pool_thread_belongs_to_the_paper(monkeypatch):
    """Sci-Hub's mirror race opens its windows in a pool worker, which has none of
    the orchestrator's thread-locals; the window must still be the paper's."""
    from concurrent.futures import ThreadPoolExecutor
    from types import SimpleNamespace
    import litloop.browser_backend as bb
    import litloop.browser_engine as be
    from litloop.sources import scihub
    killed = []
    monkeypatch.setattr(be, "_tree_kill", lambda proc: killed.append(proc))
    monkeypatch.setattr(bb, "_LAUNCHED", [])

    def launch_in_worker():
        bb._remember(SimpleNamespace(_impl_obj=SimpleNamespace(
            _connection=SimpleNamespace(_transport=SimpleNamespace(_proc="sci")))))

    raw = ThreadPoolExecutor(max_workers=1)
    src._SOURCE_TLS.doi = "10.9999/pool.a"  # the source thread that submits
    try:
        scihub._submit(raw, launch_in_worker).result()
    finally:
        src._SOURCE_TLS.doi = None
    raw.submit(lambda: None).result()  # the worker is reused for another task
    assert [o for _, _, o in bb._LAUNCHED] == ["10.9999/pool.a"]
    assert bb.close_browsers_since(0.0, owner="10.9999/pool.a") == 1 and killed == ["sci"]
    raw.shutdown()


def test_settled_keeps_only_recent_papers():
    s = src._RecentSet(limit=3)
    for k in "abcd":
        s.add(k)
    s.add("b")  # seen again: now the newest
    s.add("e")
    assert "a" not in s and "c" not in s and {"b", "d", "e"} == {k for k in "abcde" if k in s}


def test_a_skip_is_used_up_so_a_retry_runs(monkeypatch, tmp_path):
    monkeypatch.setattr(src, "_build_free_sources", lambda doi, config: [(_writer(), "Crossref")])
    doi = "10.9999/invariant.retry"
    src.skip_download(doi)  # skipped while queued
    assert "skipped" in str(src.download(doi, str(tmp_path), _institutional=False).get("reason"))
    assert src.download(doi, str(tmp_path), _institutional=False).get("success")


def test_dead_browsers_leave_the_registry(monkeypatch):
    from types import SimpleNamespace
    import litloop.browser_backend as bb
    monkeypatch.setattr(bb, "_LAUNCHED", [])

    def fake(returncode):
        return SimpleNamespace(_impl_obj=SimpleNamespace(_connection=SimpleNamespace(
            _transport=SimpleNamespace(_proc=SimpleNamespace(returncode=returncode)))))

    bb._remember(fake(0))  # its driver has exited
    bb._remember(fake(None))
    assert len(bb._LAUNCHED) == 1


def test_late_result_through_download_is_dropped(monkeypatch, tmp_path):
    """The full path: download() returns on the fast source; the slow one finishes
    afterwards and its duplicate file is removed."""
    slow_started, slow_done = threading.Event(), threading.Event()
    slow_write, fast_write = _writer(delay=1.0), _writer()

    def slow(doi, output_path, config):
        slow_started.set()
        try:
            return slow_write(doi, output_path, config)
        finally:
            slow_done.set()

    def fast(doi, output_path, config):
        slow_started.wait(5)  # both are racing before the fast one wins
        return fast_write(doi, output_path, config)

    monkeypatch.setattr(src, "_build_free_sources", lambda doi, config: [(fast, "Crossref"), (slow, "Unpaywall")])
    r = src.download("10.9999/invariant.late2", str(tmp_path), _institutional=False)
    assert r.get("success"), r
    assert slow_done.wait(10)
    time.sleep(0.3)  # _try_source removes the late copy right after the source returns
    assert len(list(tmp_path.rglob("*.pdf"))) == 1, list(tmp_path.rglob("*.pdf"))


def test_a_download_started_while_a_cancel_winds_down_runs(monkeypatch, tmp_path):
    """Cancel is job-wide (every paper in flight then belongs to the cancelled job);
    a paper started after the cancel, while the cancelled one is still winding
    down, must not be cancelled with it."""
    def by_doi(doi, config):
        return [(_writer(delay=1.5) if doi.endswith(".old") else _writer(), "Crossref")]

    monkeypatch.setattr(src, "_build_free_sources", by_doi)
    out = {}
    old = threading.Thread(target=lambda: out.update(old=src.download("10.9999/cancel.old", str(tmp_path / "a"),
                                                                       _institutional=False)))
    old.start()
    time.sleep(0.3)
    src.cancel_downloads()
    new = src.download("10.9999/cancel.new", str(tmp_path / "b"), _institutional=False)
    old.join(10)
    assert new.get("success"), new
    assert not out["old"].get("success") and out["old"].get("error_type") == "cancelled", out["old"]


def test_a_late_lane_of_the_last_try_does_not_write_into_the_retry(monkeypatch, tmp_path):
    """DeepSeek review 2026-10-19: a slow source of the first try ended during the
    retry and wrote its steps and paywall error into the retry's attempt."""
    from litloop.sources import attempt
    doi = "10.9999/invariant.retry-late"
    first = attempt.begin(doi, 0)
    started, release = threading.Event(), threading.Event()

    def late_lane(d, output_path, config):
        started.set()
        release.wait(5)
        core._set_error("paywall", "login_required")
        return None

    t = threading.Thread(target=lambda: src._try_source(late_lane, doi, tmp_path / "late.pdf", {}, "ElsevierBrowser"))
    t.start()
    started.wait(5)
    attempt.end(doi)              # the first try returned
    retry = attempt.begin(doi, 0)  # the user retries
    release.set()
    t.join(10)
    try:
        assert retry.steps == [] and retry.last_error == ("", ""), retry
        assert first.steps and first.last_error[0] == "paywall"
    finally:
        attempt.end(doi)
