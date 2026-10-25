"""A batch's second pass: the school routes, paper by paper, through the single-download
engine (2026-10-20: replaces the per-publisher PublisherBatchDownloader)."""

from __future__ import annotations

from litloop import sources


def test_phase_two_runs_only_the_school_routes_for_each_failed_paper(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(sources, "download", lambda doi, out, **kw: calls.append((doi, kw)) or {"success": True, "doi": doi})
    monkeypatch.setattr(sources, "_save_progress", lambda *a: None)
    results = {"10.1/ok": {"success": True}, "10.1/a": {"success": False}, "10.1/b": {"success": False}}
    sources._batch_institutional_phase(["10.1/ok", "10.1/a", "10.1/b"], tmp_path, {}, "b1", results,
                                       sources._CANCEL_GEN[0])
    assert [d for d, _ in calls] == ["10.1/a", "10.1/b"]
    assert all(kw["_school_only"] and kw["use_vpnsci"] and kw["_institutional"] for _, kw in calls)
    assert results["10.1/a"]["success"] and results["10.1/b"]["success"]


def test_a_pdf_already_on_disk_is_not_downloaded_again(tmp_path, monkeypatch):
    (tmp_path / "10.1_a.pdf").write_bytes(b"%PDF-" + b"x" * 6000)
    monkeypatch.setattr(sources, "download", lambda *a, **k: (_ for _ in ()).throw(AssertionError("downloaded")))
    monkeypatch.setattr(sources, "_save_progress", lambda *a: None)
    results = {}
    sources._batch_institutional_phase(["10.1/a"], tmp_path, {}, "b1", results)
    assert results["10.1/a"]["source"] == "local_cache"


def test_a_cancelled_batch_stops_phase_two(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(sources, "download", lambda doi, out, **kw: calls.append(doi) or {"success": False})
    monkeypatch.setattr(sources, "_save_progress", lambda *a: None)
    old_gen = sources._CANCEL_GEN[0] - 1  # the batch started before a cancel
    sources._batch_institutional_phase(["10.1/a"], tmp_path, {}, "b1", {}, old_gen)
    assert calls == []


def test_school_only_skips_the_free_sources(tmp_path, monkeypatch):
    built = []
    monkeypatch.setattr(sources, "load_config", lambda: {"cache_dir": str(tmp_path / "c"), "output_dir": str(tmp_path),
                                                         "download_strategy": "legal_only"})
    monkeypatch.setattr(sources, "_build_free_sources", lambda *a: built.append(1) or [])
    monkeypatch.setattr("litloop.identifiers.validate_doi", lambda d: (True, ""))
    monkeypatch.setattr("litloop.citations.formats.fetch_metadata", lambda *a, **k: {})
    monkeypatch.setattr(sources, "_build_institutional_sources", lambda *a, **k: [])
    monkeypatch.setattr("litloop.session_heal.ensure_institutional_sessions", lambda *a, **k: {})
    sources._download_impl("10.1/x", tmp_path, _school_only=True)
    assert built == []
