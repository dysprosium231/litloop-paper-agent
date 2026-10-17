"""When a download waits for the user (a login, a human check), the app is told what to do."""

import pytest

from litloop import progress_reporter, sources


def test_waiting_for_the_user_raises_a_notice_and_a_stage(monkeypatch):
    notices, stages = [], []
    monkeypatch.setattr(progress_reporter, "set_attention", lambda key, msg, **kw: notices.append((key, msg)))
    progress_reporter.add_stage_listener(lambda doi, text: stages.append((doi, text)))
    try:
        sources._SOURCE_TLS.doi = "10.1/x"
        sources.note_user_waiting("login")
        sources.note_user_waiting("verify")
    finally:
        sources._SOURCE_TLS.doi = None
        progress_reporter._STAGE_LISTENERS.clear()
    assert notices[0] == ("wait:10.1/x", "请在弹出的浏览器窗口中登录学校账号（10.1/x）")
    assert "人机验证" in notices[1][1]
    assert stages[0] == ("10.1/x", "等待你在浏览器中操作")


def test_no_paper_no_notice(monkeypatch):
    monkeypatch.setattr(progress_reporter, "set_attention", lambda *a, **k: (_ for _ in ()).throw(AssertionError))
    sources._SOURCE_TLS.doi = None
    sources.note_user_waiting("login")  # outside a source thread: nothing to say


def test_a_paper_already_downloaded_stops_the_wait(monkeypatch):
    monkeypatch.setattr(progress_reporter, "set_attention", lambda *a, **k: (_ for _ in ()).throw(AssertionError))
    monkeypatch.setattr(sources, "_SETTLED", {"10.1/done"})
    sources._SOURCE_TLS.doi = "10.1/done"
    try:
        with pytest.raises(sources.SourceWaived):
            sources.note_user_waiting("login")
    finally:
        sources._SOURCE_TLS.doi = None


def test_a_stopped_source_is_not_scored_as_a_failure(tmp_path, monkeypatch):
    from litloop.sources import scoring
    scored = []
    monkeypatch.setattr(scoring, "record_result", lambda *a, **k: scored.append(a))

    def waiting_source(doi, out, cfg):
        raise sources.SourceWaived("done")
    assert sources._try_source(waiting_source, "10.1/y", tmp_path / "y.pdf", {}, "CARSI") is None
    assert scored == []


def test_skipping_a_paper_stops_its_download_and_only_that_one(tmp_path, monkeypatch):
    import threading

    started, release = threading.Event(), threading.Event()
    out = {}

    def impl(identifier, output_dir=None, **kw):
        started.set()
        release.wait(5)
        return {"success": False, "doi": identifier, "error": "all sources failed"}
    monkeypatch.setattr(sources, "_download_impl", impl)
    monkeypatch.setattr(progress_reporter, "clear_attention", lambda key: None)
    t = threading.Thread(target=lambda: out.setdefault("r", sources.download("10.1/skip-me", str(tmp_path))))
    t.start()
    assert started.wait(5)
    assert sources.skip_download("10.1/skip-me") is True
    assert sources.skip_download("10.1/not-running") is False
    release.set()
    t.join(5)
    assert out["r"]["error_type"] == "skipped"
    assert "10.1/skip-me" not in sources._SKIPPED            # a later retry starts afresh


def test_a_skipped_paper_reads_as_skipped():
    from litloop import error_text
    assert error_text.reason("skipped by user") == "已跳过"
