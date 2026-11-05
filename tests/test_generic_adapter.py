"""The fallback HTML adapter: what it finds on an unknown publisher's page."""

from __future__ import annotations

from bs4 import BeautifulSoup

from litloop.extractors.publisher_adapters import generic


def _page(body: str) -> str:
    return f"<html><head><title>Fallback Title</title></head><body>{body}</body></html>"


def test_an_empty_page_returns_empty_fields_instead_of_raising():
    out = generic.extract("")
    assert out == {"title": "", "authors": [], "abstract": "", "full_text": "",
                   "figures": [], "references": []}


def test_the_title_selector_beats_the_title_tag():
    html = _page('<h1 class="article-title">Real Title</h1><p>body</p>')
    assert generic._extract_title(BeautifulSoup(html, "lxml")) == "Real Title"


def test_the_title_tag_is_the_last_resort():
    soup = BeautifulSoup(_page("<p>nothing</p>"), "lxml")
    assert generic._extract_title(soup) == "Fallback Title"


def test_the_abstract_comes_from_the_markup_first():
    html = _page('<div class="abstract">  We made   aerogels. </div>')
    assert generic._extract_abstract(BeautifulSoup(html, "lxml")) == "We made aerogels."


def test_the_meta_description_is_the_abstract_fallback():
    html = ('<html><head><meta name="description" content="A short summary."></head>'
            "<body><p>x</p></body></html>")
    assert generic._extract_abstract(BeautifulSoup(html, "lxml")) == "A short summary."


def test_a_short_article_body_is_rejected_and_the_largest_block_wins():
    long_text = "word " * 300
    html = _page(f"<article>too short</article><div>{long_text}</div>")
    soup = BeautifulSoup(html, "lxml")
    out = generic._extract_body(soup)
    assert out.startswith("word word") and len(out) > 500


def test_scripts_and_navigation_are_dropped_before_extraction():
    html = _page("<nav>menu menu</nav><article>" + "real text " * 60 + "</article>"
                 "<script>var x = 'junk';</script>")
    out = generic.extract(html)
    assert "junk" not in out["full_text"] and "menu" not in out["full_text"]
    assert "real text" in out["full_text"]


def test_figure_captions_come_from_figcaption():
    html = _page('<figure><img src="a.png"><figcaption>Figure 1: SEM image</figcaption></figure>')
    assert generic.extract(html)["figures"] == ["Figure 1: SEM image"]


def test_figure_class_captions_are_the_fallback_and_short_ones_are_skipped():
    html = _page('<p class="figure-caption">short</p>'
                 '<p class="figure-caption">Figure 2: a long enough caption</p>')
    assert generic.extract(html)["figures"] == ["Figure 2: a long enough caption"]


def test_references_are_read_from_list_items():
    html = _page('<section id="references"><ul>'
                 "<li>Smith J. Title of a paper. Journal. 2020;1:1-10.</li>"
                 "<li>short</li></ul></section>")
    assert generic.extract(html)["references"] == ["Smith J. Title of a paper. Journal. 2020;1:1-10."]


def test_references_fall_back_to_labelled_paragraphs():
    html = _page('<div class="references"><p class="ref-item">'
                 "Doe A. Another reference long enough to keep. 2019.</p></div>")
    assert generic.extract(html)["references"] == [
        "Doe A. Another reference long enough to keep. 2019."]


def test_authors_come_from_the_citation_meta_tags():
    html = ('<html><head>'
            '<meta name="citation_author" content="Li Wei">'
            '<meta name="citation_author" content="Zhang Min">'
            "</head><body><p>x</p></body></html>")
    assert generic.extract(html)["authors"] == ["Li Wei", "Zhang Min"]


def test_authors_fall_back_to_the_visible_byline():
    html = _page('<span class="author-name">Li Wei</span><span class="author-name">Zhang Min</span>')
    assert generic.extract(html)["authors"] == ["Li Wei", "Zhang Min"]


def test_text_is_collapsed_to_single_spaces():
    assert generic._clean_text("  a\n\n b\t c  ") == "a b c"
