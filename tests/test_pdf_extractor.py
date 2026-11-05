"""PDF text and caption extraction, using real (tiny) PDFs made with pymupdf."""

from __future__ import annotations

import pymupdf
import pytest

from litloop.extractors import pdf_extractor


def _pdf_bytes(*pages: str) -> bytes:
    doc = pymupdf.open()
    for text in pages:
        page = doc.new_page()
        page.insert_text((72, 72), text, fontsize=11)
    data = doc.tobytes()
    doc.close()
    return data


@pytest.fixture
def pdf_file(tmp_path):
    path = tmp_path / "paper.pdf"
    path.write_bytes(_pdf_bytes("Polymer derived ceramic aerogels", "Second page text"))
    return path


def test_text_comes_out_of_a_file(pdf_file):
    text = pdf_extractor.extract_text(pdf_file)
    assert "Polymer derived ceramic aerogels" in text
    assert "Second page text" in text


def test_a_missing_file_is_an_empty_string(tmp_path):
    assert pdf_extractor.extract_text(tmp_path / "nope.pdf") == ""


def test_a_file_that_is_not_a_pdf_is_an_empty_string(tmp_path):
    path = tmp_path / "broken.pdf"
    path.write_bytes(b"this is not a pdf")
    assert pdf_extractor.extract_text(path) == ""


def test_bytes_are_read_the_same_way():
    text = pdf_extractor.extract_from_bytes(_pdf_bytes("From bytes"))
    assert "From bytes" in text
    assert pdf_extractor.extract_from_bytes(b"nonsense") == ""


def test_figure_captions_are_found_and_short_ones_dropped():
    text = (
        "Intro paragraph.\n"
        "Figure 1: Overview of the process\n"
        "Fig. 2: Results for SiC\n"
        "Fig 5: x\n"
        "Body text mentioning Figure 4 in passing.\n"
    )
    captions = pdf_extractor.extract_figures_from_text(text)
    assert captions == ["Figure 1: Overview of the process", "Fig. 2: Results for SiC"]
    assert pdf_extractor.extract_figures_from_text("") == []


def test_figures_can_be_read_straight_from_a_pdf(tmp_path):
    path = tmp_path / "figs.pdf"
    path.write_bytes(_pdf_bytes("Figure 1: A caption long enough to keep"))
    assert pdf_extractor.extract_figures(path) == ["Figure 1: A caption long enough to keep"]
    assert pdf_extractor.extract_figures(tmp_path / "missing.pdf") == []


def test_clean_text_undoes_hyphenation_and_rejoins_wrapped_lines():
    raw = "poly-\nmer chains are long enough\n\nNew paragraph here."
    cleaned = pdf_extractor._clean_text(raw)
    assert "polymer chains are long enough" in cleaned
    assert "long enough New" not in cleaned  # paragraph break survives
    assert "\n\nNew paragraph" in cleaned


def test_clean_text_keeps_list_items_on_their_own_lines():
    raw = "1. first item that is quite long\n2. second item that is quite long"
    cleaned = pdf_extractor._clean_text(raw)
    assert "1. first item that is quite long\n2. second item" in cleaned


def test_clean_text_collapses_spaces_and_blank_runs():
    assert pdf_extractor._clean_text("a   b\t\tc") == "a b c"
    assert pdf_extractor._clean_text("a\n\n\n\nb") == "a\n\nb"
