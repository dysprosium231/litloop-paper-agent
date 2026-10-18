"""Tests for PDF format validation, preview detection, and DOI ownership checks."""

from __future__ import annotations

import os
from pathlib import Path
import fitz
import pytest

from litloop.pdf_utils import (
    is_pdf_file,
    is_suspicious_pdf,
    success,
    suspicious_pdf,
)
from litloop.sources import _pdf_mentions_doi


def _build_pdf(
    tmp_path: Path,
    name: str,
    pages: int = 1,
    text: str = "Test",
    pad_size: int = 0,
) -> Path:
    """Generate a test PDF file with PyMuPDF."""
    p = tmp_path / name
    doc = fitz.open()
    for i in range(pages):
        page = doc.new_page()
        page.insert_text((72, 72), f"{text} (page {i + 1})")
    if pad_size and len(doc.tobytes()) < pad_size:
        blob = os.urandom(pad_size)
        xref = doc.get_new_xref()
        doc.update_object(xref, f"<< /Type /EmbeddedFile /Length {len(blob)} >>")
        doc.update_stream(xref, blob, compress=False)
    p.write_bytes(doc.tobytes())
    return p


def test_is_pdf_file_valid_raw_bytes(tmp_path: Path):
    p = tmp_path / "valid.pdf"
    content = b"%PDF-1.5\n" + b"x" * 1500 + b"\n%%EOF\n"
    p.write_bytes(content)
    assert is_pdf_file(p) is True


def test_is_pdf_file_rejects_undersized(tmp_path: Path):
    p = tmp_path / "tiny.pdf"
    content = b"%PDF-1.5\n" + b"x" * 50 + b"\n%%EOF\n"
    p.write_bytes(content)
    assert p.stat().st_size < 1000
    assert is_pdf_file(p) is False


def test_is_pdf_file_rejects_missing_header_or_eof(tmp_path: Path):
    # Missing %PDF- header
    p1 = tmp_path / "no_header.pdf"
    p1.write_bytes(b"INVALID" + b"x" * 1500 + b"\n%%EOF\n")
    assert is_pdf_file(p1) is False

    # Missing %%EOF trailer
    p2 = tmp_path / "no_eof.pdf"
    p2.write_bytes(b"%PDF-1.5\n" + b"x" * 1500 + b"END_OF_FILE")
    assert is_pdf_file(p2) is False


def test_is_pdf_file_nonexistent(tmp_path: Path):
    p = tmp_path / "does_not_exist.pdf"
    assert is_pdf_file(p) is False


def test_is_suspicious_pdf_undersized(tmp_path: Path):
    # Even a valid 1-page PDF is suspicious if smaller than 50 KB
    pdf_path = _build_pdf(tmp_path, "small_cover.pdf", pages=1)
    assert pdf_path.stat().st_size < 50_000
    assert is_suspicious_pdf(pdf_path) is True


def test_is_suspicious_pdf_multipage_accepted(tmp_path: Path):
    # Multi-page PDF (> 50KB) is accepted as genuine full text
    pdf_path = _build_pdf(tmp_path, "full_paper.pdf", pages=3, pad_size=60_000)
    assert pdf_path.stat().st_size >= 50_000
    assert is_suspicious_pdf(pdf_path) is False


def test_is_suspicious_pdf_single_page_rejected_by_default(tmp_path: Path):
    # Large (>50KB) single-page PDF (like an Elsevier preview) is flagged as suspicious
    pdf_path = _build_pdf(tmp_path, "preview.pdf", pages=1, pad_size=60_000)
    assert pdf_path.stat().st_size >= 50_000
    assert is_suspicious_pdf(pdf_path) is True


def test_is_suspicious_pdf_single_page_exempted_for_1page_article(tmp_path: Path, monkeypatch):
    # When Crossref says the article itself is single-page, it should be accepted
    pdf_path = _build_pdf(tmp_path, "single_page_article.pdf", pages=1, pad_size=60_000)
    doi = "10.1038/s41586-026-0001-1"

    monkeypatch.setattr("litloop.pdf_utils._article_is_single_page", lambda d: d == doi)

    # For matching single-page DOI, not suspicious
    assert is_suspicious_pdf(pdf_path, doi=doi) is False
    # For another DOI, still treated as suspicious preview
    assert is_suspicious_pdf(pdf_path, doi="10.1038/other") is True


def test_is_suspicious_pdf_unreadable(tmp_path: Path):
    # Corrupt PDF that throws in fitz
    p = tmp_path / "corrupt.pdf"
    p.write_bytes(b"%PDF-1.4\n" + b"\xff" * 60_000 + b"\n%%EOF\n")
    assert is_suspicious_pdf(p) is True


def test_is_suspicious_pdf_regex_fallback(tmp_path: Path, monkeypatch):
    # Test regex counting when fitz is not imported / unavailable
    import builtins

    orig_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "fitz":
            raise ImportError("No fitz")
        return orig_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    multi = _build_pdf(tmp_path, "regex_multi.pdf", pages=2, pad_size=60_000)
    single = _build_pdf(tmp_path, "regex_single.pdf", pages=1, pad_size=60_000)

    monkeypatch.setattr("litloop.pdf_utils._article_is_single_page", lambda d: False)
    assert is_suspicious_pdf(multi) is False
    assert is_suspicious_pdf(single) is True


def test_pdf_mentions_doi_ownership(tmp_path: Path):
    target_doi = "10.1038/s41586-020-2649-2"
    pdf_with_doi = _build_pdf(
        tmp_path,
        "with_doi.pdf",
        pages=2,
        text=f"Nature Article DOI: {target_doi} Published 2020",
    )
    pdf_without_doi = _build_pdf(
        tmp_path,
        "without_doi.pdf",
        pages=2,
        text="A completely different paper about quantum computing.",
    )

    assert _pdf_mentions_doi(pdf_with_doi, target_doi) is True
    assert _pdf_mentions_doi(pdf_without_doi, target_doi) is False
    # Case insensitivity and whitespace tolerance in DOI
    assert _pdf_mentions_doi(pdf_with_doi, target_doi.upper()) is True


def test_pdf_result_helpers(tmp_path: Path):
    p = tmp_path / "sample.pdf"
    p.write_bytes(b"x" * 2048)

    ok_res = success("10.1234/test", p, "publisher")
    assert ok_res["success"] is True
    assert ok_res["identifier"] == "10.1234/test"
    assert ok_res["source"] == "publisher"
    assert ok_res["size_kb"] == 2.0

    susp_res = suspicious_pdf("10.1234/test", p, "scihub")
    assert susp_res["success"] is False
    assert susp_res["error_type"] == "suspicious_pdf"
    assert "cover page or preview" in susp_res["reason"]
