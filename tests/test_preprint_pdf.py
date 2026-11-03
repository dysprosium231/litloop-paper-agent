"""A preprint of the paper is not the published PDF.

2026-10-26: for 10.1038/s41598-024-67653-y the open-access route served the
Research Square preprint (21 pages, "Page 1/21", no published DOI on page 1).
Title matching cannot tell the two versions apart, so the downloader reads the
preprint server's own marks, prefers a version of record, and marks the file
when only the preprint can be had (TODO「预印本被存成正式版」).
"""

from __future__ import annotations

import os
from pathlib import Path

import litloop.sources as sources
from litloop.download import pdf
from litloop.sources import _run_tiers_parallel

RS_TEXT = (
    "Page 1/21\n"
    "Improving thermal conductivity of AlSiC composites\n"
    "by post-oxidization of reaction-bonded silicon carbide preforms\n"
    "Xinping Lin, Qiang Xu, Tianyou Deng, Bingquan Yang, Liang Chen\n"
    "Article\nKeywords:\n"
    "Posted Date: January 2nd, 2024\n"
    "DOI: https://doi.org/10.21203/rs.3.rs-3810170/v1\n"
    "License:\nThis work is licensed under a Creative Commons Attribution 4.0 "
    "International License\nAdditional Declarations: No competing interests reported.\n"
)
VOR_TEXT = (
    "www.nature.com/scientificreports\n"
    "Improving thermal conductivity of AlSiC composites by post-oxidization of "
    "reaction-bonded silicon carbide preforms\n"
    "Xinping Lin, Qiang Xu, Tianyou Deng, Bingquan Yang & Liang Chen\n"
    "Scientific Reports | (2024) 14:15392\n"
    "https://doi.org/10.1038/s41598-024-67653-y\n"
)


def _pdf(path: Path, page1: str, pages: int = 3, pad: int = 120_000) -> Path:
    """A real multi-page PDF, padded past the size heuristic, with `page1` on top."""
    import fitz

    doc = fitz.open()
    doc.new_page().insert_text((72, 72), page1)
    for i in range(1, pages):
        doc.new_page().insert_text((72, 72), f"page {i + 1}")
    blob = os.urandom(pad)
    xref = doc.get_new_xref()
    doc.update_object(xref, f"<< /Type /EmbeddedFile /Length {len(blob)} >>")
    doc.update_stream(xref, blob, compress=False)
    path.write_bytes(doc.tobytes())
    return path


class TestPreprintMark:
    def test_research_square_page(self, tmp_path: Path):
        mark = pdf.preprint_mark(_pdf(tmp_path / "rs.pdf", RS_TEXT), "10.1038/s41598-024-67653-y")
        assert mark == "Research Square"

    def test_version_of_record_is_not_a_preprint(self, tmp_path: Path):
        """A published PDF that cites an arXiv preprint on page 1 still prints
        its own DOI — that settles it."""
        text = VOR_TEXT + "See the preprint arXiv:2401.12345 for the first report.\n"
        assert pdf.preprint_mark(_pdf(tmp_path / "vor.pdf", text), "10.1038/s41598-024-67653-y") == ""

    def test_arxiv_sidebar(self, tmp_path: Path):
        text = "arXiv:2401.12345v1 [cond-mat.mtrl-sci] 1 Jan 2024\nThermal properties of SiC\n"
        assert pdf.preprint_mark(_pdf(tmp_path / "ax.pdf", text), "10.48550/arXiv.2401.12345") == ""

    def test_scanned_page_has_no_mark(self, tmp_path: Path):
        assert pdf.preprint_mark(_pdf(tmp_path / "scan.pdf", "", pages=1, pad=20_000)) == ""


def _lane(page1: str, delay: float, label: str):
    def src(doi, out_path, config, label_=label):
        import time as _t
        _t.sleep(delay)
        _pdf(Path(out_path), page1)
        return {"success": True, "file": str(out_path), "source": label_}
    return src


def _race(tiers, tmp_path: Path, config: dict | None = None):
    failures: list = []
    return _run_tiers_parallel(
        tiers, "10.1038/s41598-024-67653-y", tmp_path, tmp_path / "out.pdf",
        config or {"race_mode": "full"}, False, 10, failures=failures)


class TestRace:
    def test_published_version_wins_over_preprint(self, tmp_path: Path):
        """The preprint arrives first but must not end the race."""
        preprint = _lane(RS_TEXT, 0.02, "OpenAlexContent")
        published = _lane(VOR_TEXT, 0.3, "Publisher")
        result = _race([([(preprint, "OpenAlexContent"), (published, "Publisher")], "T", 3)], tmp_path)

        assert result is not None and result.get("success")
        assert result.get("source") == "Publisher"
        assert not result.get("preprint")
        assert Path(result["file"]) == tmp_path / "out.pdf"

    def test_preprint_is_used_when_nothing_else_answers(self, tmp_path: Path):
        preprint = _lane(RS_TEXT, 0.02, "OpenAlexContent")
        failing = _lane(VOR_TEXT, 0.05, "Publisher")

        def fails(doi, out_path, config):
            return {"success": False, "error": "no access", "source": "Publisher"}

        result = _race([([(preprint, "OpenAlexContent"), (fails, "Publisher")], "T", 3)], tmp_path)

        assert result is not None and result.get("success")
        assert result.get("preprint") == "Research Square"
        assert Path(result["file"]) == tmp_path / "out.pdf"
        assert Path(result["file"]).exists()


class TestDownload:
    def test_falls_back_to_the_preprint_after_all_phases(self, tmp_path: Path, monkeypatch):
        """Phase 1 finds only the preprint, phase 2 finds nothing: the preprint
        is the answer, named so nobody takes it for the published PDF."""
        import time
        from litloop.citations import formats

        monkeypatch.setattr(time, "sleep", lambda s: None)
        monkeypatch.setattr(sources, "_build_free_sources", lambda doi, config: [("fake", "Fake")])
        monkeypatch.setattr(formats, "fetch_metadata", lambda doi, config: {
            "title": ["Improving thermal conductivity of AlSiC composites"],
            "author": [{"family": "Lin"}], "year": "2024"})
        path = _pdf(tmp_path / "raw.pdf", RS_TEXT)
        result = {"success": True, "identifier": "10.1038/s41598-024-67653-y",
                  "doi": "10.1038/s41598-024-67653-y", "file": str(path),
                  "source": "OpenAlexContent", "preprint": "Research Square"}
        monkeypatch.setattr(sources, "_run_tiers_parallel", lambda *a, **k: result)

        out = sources.download("10.1038/s41598-024-67653-y", tmp_path, _institutional=False)

        assert out["success"], out
        assert out["preprint"] == "Research Square"
        assert out["file"].endswith("_preprint.pdf"), out["file"]
        assert Path(out["file"]).exists()


class TestRename:
    def test_file_name_says_preprint(self, tmp_path: Path, monkeypatch):
        from litloop.citations import formats

        monkeypatch.setattr(formats, "fetch_metadata", lambda doi, config: {
            "title": ["Improving thermal conductivity of AlSiC composites"],
            "author": [{"family": "Lin"}], "year": "2024"})
        path = _pdf(tmp_path / "10.1038_s41598-024-67653-y_OpenAlexContent.pdf", RS_TEXT)
        result = {"success": True, "file": str(path), "doi": "10.1038/s41598-024-67653-y",
                  "preprint": "Research Square"}
        sources._auto_rename(result, "10.1038/s41598-024-67653-y", {"rename_format": "author_year_title_doi"})

        assert result["file"].endswith("_preprint.pdf"), result["file"]
        assert Path(result["file"]).exists()
