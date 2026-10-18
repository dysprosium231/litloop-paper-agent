"""Logged into the publisher as the school and still no full text: no school routes."""

from __future__ import annotations

import litloop.sources as src
from litloop import _publisher_strategies_core as core
from litloop.error_text import reason


def _setup(monkeypatch, tmp_path, called):
    monkeypatch.setattr(src, "_build_free_sources", lambda doi, config: [(lambda *a, **k: None, "Crossref")])
    monkeypatch.setattr(src, "_run_tiers_parallel", lambda *a, **k: None)
    monkeypatch.setattr(src, "_run_institutional_sequence", lambda *a, **k: called.append(1))
    monkeypatch.setattr(src, "_build_institutional_sources", lambda *a, **k: [(None, "WebVPN")])
    import litloop.identifiers as ids
    monkeypatch.setattr(ids, "validate_doi", lambda doi: (True, ""))  # no network
    import litloop.session_heal as sh
    monkeypatch.setattr(sh, "ensure_institutional_sessions", lambda *a, **k: {})


def test_school_routes_skipped(monkeypatch, tmp_path):
    called = []
    _setup(monkeypatch, tmp_path, called)
    base = src.load_config()
    monkeypatch.setattr(src, "load_config", lambda: {**base, "school_routes_after_publisher_login": False})
    core.note_no_access("10.1007/978-981-96-2944-2_32")
    r = src.download("10.1007/978-981-96-2944-2_32", str(tmp_path), _institutional=True)
    assert not called
    assert r.get("error_type") == "not_entitled"
    assert "学校没有订阅" in (reason(r.get("reason")) or "")


def test_setting_keeps_school_routes(monkeypatch, tmp_path):
    called = []
    _setup(monkeypatch, tmp_path, called)
    base = src.load_config()
    monkeypatch.setattr(src, "load_config", lambda: {**base, "school_routes_after_publisher_login": True})
    core.note_no_access("10.1007/x-2")
    src.download("10.1007/x-2", str(tmp_path), _institutional=True)
    assert called


def test_the_judgment_holds_for_a_retry_and_expires(monkeypatch, tmp_path):
    called = []
    _setup(monkeypatch, tmp_path, called)
    base = src.load_config()
    monkeypatch.setattr(src, "load_config", lambda: {**base, "school_routes_after_publisher_login": False})
    doi = "10.1007/retry-1"
    core.note_no_access(doi)
    src.download(doi, str(tmp_path), _institutional=True)
    src.download(doi, str(tmp_path), _institutional=True)  # a retry: still no school routes
    assert not called
    core.LOGIN_NO_ACCESS[doi] -= core.NO_ACCESS_HOURS * 3600 + 1  # long ago
    src.download(doi, str(tmp_path), _institutional=True)
    assert called
    core.clear_no_access(doi)


def test_a_download_clears_the_judgment(monkeypatch, tmp_path):
    from pathlib import Path

    def writer(doi, output_path, config):
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(output_path).write_bytes(b"%PDF-1.4\n" + b"0" * 20000 + b"\n%%EOF")
        return {"success": True, "file": str(output_path), "source": "Fake"}

    import litloop.identifiers as ids
    monkeypatch.setattr(ids, "validate_doi", lambda doi: (True, ""))
    import litloop.citation as cit
    monkeypatch.setattr(cit, "fetch_metadata", lambda *a, **k: {})  # no network
    import litloop.pdf_utils as pu
    monkeypatch.setattr(pu, "is_suspicious_pdf", lambda *a, **k: False)
    monkeypatch.setattr(src, "_build_free_sources", lambda doi, config: [(writer, "Crossref")])
    core.note_no_access("10.9999/later-ok")
    assert src.download("10.9999/later-ok", str(tmp_path), _institutional=False).get("success")
    assert not core.no_access("10.9999/later-ok")


class _Page:
    def __init__(self, text):
        self.text = text

    def evaluate(self, js):
        return self.text


def test_only_a_page_that_sells_the_paper_counts():
    assert core._page_sells_paper(_Page("Chapter 32 ... Buy chapter EUR 29.95 ... Buy eBook"))
    assert core._page_sells_paper(_Page("You do not have access to this content"))
    # Entitled pages keep the institution link; a missing PDF link alone is no proof.
    assert not core._page_sells_paper(_Page("Access through your institution  Download PDF"))
    assert not core._page_sells_paper(_Page(""))
