"""A lane that finishes after its paper's race window must not be fetched twice."""

from __future__ import annotations

from pathlib import Path

import fitz

from litloop.download.pipeline import _adopt_late_files
from litloop.identifiers import safe_filename

DOI = "10.1016/j.ceramint.2024.08.002"


def _pdf(path: Path, first_page: str, pages: int = 3) -> Path:
    """A parseable multi-page PDF, padded past the preview-size threshold."""
    doc = fitz.open()
    for i in range(pages):
        page = doc.new_page()
        page.insert_text((72, 72), first_page if i == 0 else f"page {i + 1}")
    blob = bytes(range(256)) * 500  # ~128 KB of filler, not compressible text
    xref = doc.get_new_xref()
    doc.update_object(xref, f"<< /Type /EmbeddedFile /Length {len(blob)} >>")
    doc.update_stream(xref, blob, compress=False)
    path.write_bytes(doc.tobytes())
    return path


def _no_metadata(monkeypatch):
    monkeypatch.setattr("litloop.citations.formats.fetch_metadata", lambda *a, **k: {})


def test_a_file_that_lands_after_failure_is_adopted(tmp_path, monkeypatch):
    _no_metadata(monkeypatch)
    landed = _pdf(tmp_path / f"{safe_filename(DOI)}_Publisher.pdf", f"https://doi.org/{DOI}")
    results = [{"doi": DOI, "success": False, "error": "timeout", "reason": "racing"}]
    assert _adopt_late_files(results, tmp_path, {}) == 1
    assert results[0]["success"] is True
    assert Path(results[0]["file"]) == landed
    assert "error" not in results[0] and "reason" not in results[0]


def test_a_file_the_check_rejects_is_left_alone(tmp_path, monkeypatch):
    _no_metadata(monkeypatch)
    monkeypatch.setattr("litloop.download.pdf.paper_check", lambda *a, **k: "wrong")
    _pdf(tmp_path / f"{safe_filename(DOI)}_Sci-Hub.pdf", "a different paper entirely")
    results = [{"doi": DOI, "success": False, "error": "timeout"}]
    assert _adopt_late_files(results, tmp_path, {}) == 0
    assert results[0]["success"] is False


def test_a_success_keeps_its_own_file(tmp_path, monkeypatch):
    _no_metadata(monkeypatch)
    mine = _pdf(tmp_path / "mine.pdf", f"https://doi.org/{DOI}")
    other = _pdf(tmp_path / f"{safe_filename(DOI)}_Sci-Hub.pdf", f"https://doi.org/{DOI}")
    results = [{"doi": DOI, "success": True, "file": str(mine)}]
    assert _adopt_late_files(results, tmp_path, {}) == 0
    assert Path(results[0]["file"]) == mine and other.exists()


def test_only_failed_rows_with_a_matching_name_are_touched(tmp_path, monkeypatch):
    _no_metadata(monkeypatch)
    _pdf(tmp_path / f"{safe_filename(DOI)}_Publisher.pdf", f"https://doi.org/{DOI}")
    results = [{"doi": "10.1/other", "success": False, "error": "timeout"}]
    assert _adopt_late_files(results, tmp_path, {}) == 0
    assert results[0]["success"] is False


def test_run_lanes_counts_a_late_file_as_success(tmp_path, monkeypatch):
    """The lane scheduler adopts the file before the cooling retry sees a failure."""
    from litloop.download import pipeline as p

    late = _pdf(tmp_path / f"{safe_filename(DOI)}_Publisher.pdf", f"https://doi.org/{DOI}")
    _no_metadata(monkeypatch)
    monkeypatch.setattr(p, "_enrich_oa_urls", lambda *a, **k: None)
    monkeypatch.setattr(p, "_run_fast_lane", lambda *a, **k: [
        {"doi": DOI, "success": False, "error": "timeout"}])
    monkeypatch.setattr(p, "_fetch_si_for_results", lambda *a, **k: None)
    still_failed: list[list[str]] = []
    monkeypatch.setattr(p, "_transient_retry", lambda results, *a, **k: still_failed.append(
        [r["doi"] for r in results if not r.get("success")]))

    rows = p.run_lanes([p.QueueEntry(identifier=DOI, channel="oa")], tmp_path,
                       {"cache_dir": str(tmp_path / "c"), "output_dir": str(tmp_path)},
                       allow_grey=False, allow_institution=False)
    assert rows[0]["success"] is True and Path(rows[0]["file"]) == late
    assert still_failed == [[]]  # nothing left for the retry to fetch again

