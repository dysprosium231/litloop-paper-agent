"""A downloaded PDF must be the paper its DOI names (download.pdf.paper_check)."""

from pathlib import Path

import pymupdf
import pytest

from litloop import sources
from litloop.download import pdf

DOI = "10.1016/j.ceramint.2021.06.118"
TITLE = "Dye removal by polymer derived ceramic nanobeads"
FILLER = " ".join(["Results and discussion of the measured samples."] * 20)


def _pdf(path: Path, first_page: str, pages: int = 3) -> Path:
    doc = pymupdf.open()
    for i in range(pages):
        page = doc.new_page()
        page.insert_textbox(pymupdf.Rect(40, 40, 560, 800), first_page if i == 0 else FILLER, fontsize=9)
    doc.save(path)
    return path


@pytest.fixture
def titled(monkeypatch):
    monkeypatch.setitem(pdf._TITLE_CACHE, DOI, TITLE)


def test_a_printed_doi_or_the_title_makes_a_match(tmp_path, titled):
    assert pdf.paper_check(_pdf(tmp_path / "a.pdf", f"Ceramics International https://doi.org/{DOI} {FILLER}"), DOI) == "match"
    assert pdf.paper_check(_pdf(tmp_path / "b.pdf", f"{TITLE}. A. Icin, B. Author. {FILLER}"), DOI) == "match"


def test_another_paper_is_wrong(tmp_path, titled):
    essay = "NOMES DE LUGAR: CONFIM. Massimo Cacciari. Revista de Letras. JSTOR is a not-for-profit service. " + FILLER
    assert pdf.paper_check(_pdf(tmp_path / "c.pdf", essay), DOI) == "wrong"


def test_what_cannot_be_judged_is_accepted(tmp_path, titled):
    assert pdf.paper_check(_pdf(tmp_path / "d.pdf", "", pages=1), DOI) == "unknown"  # no text (a scan)
    assert pdf.paper_check(_pdf(tmp_path / "e.pdf", FILLER), "2409.17291") == "unknown"  # arXiv, not a DOI
    pdf._TITLE_CACHE.pop(DOI)
    assert pdf.paper_check(_pdf(tmp_path / "f.pdf", FILLER), DOI) == "unknown"  # no title to compare


def test_a_source_that_delivers_another_paper_fails_and_the_race_moves_on(tmp_path, titled, monkeypatch):
    essay = "NOMES DE LUGAR: CONFIM. Massimo Cacciari. " + FILLER
    # A test PDF is tiny; the preview/cover-page rule (under 50 KB) would reject it first.
    monkeypatch.setattr(pdf, "is_suspicious_pdf", lambda path, doi="": False)

    def scihub(doi, output_path, config):
        _pdf(output_path, essay)
        return {"success": True, "file": str(output_path)}

    out = sources._try_source(scihub, DOI, tmp_path / "x.pdf", {}, "Sci-Hub")
    assert out["success"] is False and out["error_type"] == "wrong_paper"
