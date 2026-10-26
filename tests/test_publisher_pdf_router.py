"""PDF routes built from a DOI and the article page (publisher_pdf_router, used by the lane fetcher)."""

from __future__ import annotations

import pytest

from litloop.publishers import pdf_router as r
from litloop.publishers.profiles import PUBLISHER_PROFILES as P


@pytest.mark.parametrize("url, pii", [
    ("https://www.sciencedirect.com/science/article/pii/S0272884224012345", "S0272884224012345"),
    ("https://linkinghub.elsevier.com/retrieve/pii/S0272884224012345", "S0272884224012345"),
    ("https://x.test/a?pii=S1359645424000001&b=1", "S1359645424000001"),
    ("https://pdf.sciencedirectassets.com/1-s2.0-S0272884224012345-main.pdf", "S0272884224012345"),
    ("https://example.org/article/1", ""),
])
def test_elsevier_pii(url, pii):
    assert r.extract_elsevier_pii(url) == pii


def test_ieee_article_number():
    assert r.extract_ieee_article_number("https://ieeexplore.ieee.org/document/9876543/") == "9876543"
    assert r.extract_ieee_article_number("https://ieeexplore.ieee.org/stamp/stamp.jsp?tp=&arnumber=123") == "123"
    assert r.extract_ieee_article_number("https://example.org/") == ""


def test_html_discovery_takes_meta_links_viewer_and_query_targets():
    page = """<html><head><meta name="citation_pdf_url" content="/content/a.pdf"></head><body>
      <a href="/view?file=%2Fstore%2Fb.pdf">Read</a>
      <a href="/doi/abs/10.1/x">Abstract</a>
      <a href="/dl/123" aria-label="Download PDF">get</a>
      <script>PDFViewerApplicationOptions.set('defaultUrl', '/viewer/c.pdf');</script>
    </body></html>"""
    got = r.discover_pdf_candidates_from_html(page, "https://pub.test/doi/10.1/x")
    assert got[0] == "https://pub.test/content/a.pdf"
    assert "https://pub.test/store/b.pdf" in got
    assert "https://pub.test/dl/123" in got
    assert "https://pub.test/viewer/c.pdf" in got
    assert "https://pub.test/doi/abs/10.1/x" not in got
    assert len(got) == len(set(got))


def test_elsevier_candidates_start_with_pdfft_and_drop_other_articles():
    src = "https://www.sciencedirect.com/science/article/pii/S0272884224012345"
    got = r.build_pdf_candidates(P["elsevier"], "10.1016/j.x.2024.1", source_url=src, discovered_urls=[
        "https://www.sciencedirect.com/science/article/pii/S0272884224099999/pdfft",  # a related article
        "https://www.sciencedirect.com/science/article/pii/S0272884224012345/pdfft?download=true",
    ])
    assert got[0] == src + "/pdfft"
    assert not any("S0272884224099999" in u for u in got)
    assert src + "/pdfft?download=true" in got


def test_ieee_candidates_from_document_url():
    got = r.build_pdf_candidates(P["ieee"], "10.1109/x", source_url="https://ieeexplore.ieee.org/document/42")
    assert got[:2] == ["https://ieeexplore.ieee.org/stampPDF/getPDF.jsp?tp=&isnumber=&arnumber=42",
                       "https://ieeexplore.ieee.org/stamp/stamp.jsp?tp=&arnumber=42"]


def test_aps_abstract_to_pdf_and_accepted_manuscripts_skipped():
    doi = "10.1103/PhysRevB.1.1"
    src = f"https://journals.aps.org/prb/abstract/{doi}"
    got = r.build_pdf_candidates(P["aps"], doi, source_url=src,
                                 discovered_urls=[f"https://journals.aps.org/prb/accepted/{doi}/pdf"])
    assert got[0] == f"https://journals.aps.org/prb/pdf/{doi}"
    assert not any("/accepted" in u for u in got)


def test_plos_mdpi_copernicus_routes_from_the_doi_alone():
    plos = r.build_pdf_candidates(P["plos"], "10.1371/journal.pone.0123456")
    assert plos[0] == "https://journals.plos.org/plosone/article/file?id=10.1371/journal.pone.0123456&type=printable"
    mdpi = r.build_pdf_candidates(P["mdpi"], "10.3390/s24010123")
    assert mdpi[0] == "https://www.mdpi.com/1424-8220/24/1/123/pdf"
    cop = r.build_pdf_candidates(P["copernicus"], "10.5194/acp-24-1234-2024")
    assert cop[0] == "https://acp.copernicus.org/articles/24/1234/2024/acp-24-1234-2024.pdf"


def test_oxford_article_pdf_path():
    got = r.build_pdf_candidates(P["oxfordacademic"], "10.1093/mnras/stad123",
                                 source_url="https://academic.oup.com/mnras/article/520/1/1/7000000")
    assert got[0] == "https://academic.oup.com/mnras/article-pdf/520/1/1/7000000/stad123.pdf"


def test_supplements_and_non_pdf_links_are_not_candidates():
    got = r.build_pdf_candidates(P["wiley"], "10.1002/x", discovered_urls=[
        "https://onlinelibrary.wiley.com/action/downloadSupplement?doi=10.1002/x&file=suppl_file.pdf",
        "https://onlinelibrary.wiley.com/doi/full/10.1002/x",
        "",
    ])
    assert got == list(P["wiley"].pdf_urls("10.1002/x"))  # only the profile's own routes


def test_optica_profile_is_in_the_one_registry():
    from litloop.publishers.profiles import infer_publisher_profile
    assert infer_publisher_profile("10.1364/ol.47.013297").name == "Optica"
