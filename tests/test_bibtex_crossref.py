"""BibTeX from Crossref metadata, without touching the network."""

from __future__ import annotations

import pytest

from litloop.citations import bibtex_crossref


ARTICLE = {
    "type": "journal-article",
    "title": ["Polymer derived ceramic aerogels"],
    "author": [
        {"family": "Schawaller", "given": "Doreen"},
        {"family": "Clauß", "given": "Bernhard"},
        {"family": "Wolff", "given": ""},
    ],
    "container-title": ["Journal of the European Ceramic Society"],
    "published-print": {"date-parts": [[2021, 5]]},
    "volume": "41",
    "issue": "6",
    "page": "3401-3410",
    "publisher": "Elsevier BV",
    "DOI": "10.1016/j.jeurceramsoc.2020.12.031",
}


def test_an_article_becomes_a_complete_bibtex_entry():
    text = bibtex_crossref._crossref_to_bibtex(ARTICLE, "10.1016/j.jeurceramsoc.2020.12.031")
    assert text is not None
    lines = text.splitlines()
    assert lines[0].startswith("@article{schawa2021poly")
    assert "  title = {Polymer derived ceramic aerogels}," in lines
    assert "  author = {Schawaller, Doreen and Clauß, Bernhard and Wolff}," in lines
    assert "  journal = {Journal of the European Ceramic Society}," in lines
    assert "  year = {2021}," in lines
    assert "  volume = {41}," in lines
    assert "  number = {6}," in lines
    assert "  pages = {3401--3410}," in lines
    assert "  doi = {10.1016/j.jeurceramsoc.2020.12.031}," in lines
    assert "  url = {https://doi.org/10.1016/j.jeurceramsoc.2020.12.031}," in lines
    assert lines[-1] == "}"


@pytest.mark.parametrize(
    ("crossref_type", "expected"),
    [
        ("journal-article", "article"),
        ("proceedings-article", "inproceedings"),
        ("book-chapter", "incollection"),
        ("monograph", "book"),
        ("dissertation", "phdthesis"),
        ("report", "techreport"),
        ("posted-content", "misc"),
        ("something-new", "article"),
    ],
)
def test_crossref_types_map_to_bibtex_entry_types(crossref_type, expected):
    assert bibtex_crossref._detect_entry_type(crossref_type) == expected


def test_markup_and_special_characters_survive_escaping():
    entry = {"title": ["A <i>p</i> & 50% study of x_y"], "type": "journal-article"}
    text = bibtex_crossref._crossref_to_bibtex(entry, "10.1/abc")
    assert "title = {A p \\& 50\\% study of x\\_y}," in text
    assert "<i>" not in text


def test_an_entry_without_a_date_still_gets_id_and_title():
    text = bibtex_crossref._crossref_to_bibtex({"title": ["Untitled draft"]}, "10.1/no-date")
    assert "@article{" in text
    assert "  doi = {10.1/no-date}," in text
    # No year field at all, and the cite key says so.
    assert "  year = " not in text
    assert "xxxx" in text.splitlines()[0]


def test_the_cite_key_falls_back_to_the_doi_when_nothing_else_is_known():
    assert bibtex_crossref._make_cite_key({}, "10.1016/j.ceramint.2021.06.118") == (
        "10_1016_j_ceramint_2021_06_118"
    )


def test_fetching_uses_crossref_and_returns_none_when_it_fails(monkeypatch):
    seen: list[str] = []

    def ok(url, config, headers=None):
        seen.append(url)
        return {"message": ARTICLE}

    monkeypatch.setattr(bibtex_crossref, "fetch_json", ok)
    assert "@article{" in bibtex_crossref.fetch_bibtex("10.1016/j.jeurceramsoc.2020.12.031", {})
    assert seen == ["https://api.crossref.org/works/10.1016%2Fj.jeurceramsoc.2020.12.031"]

    monkeypatch.setattr(bibtex_crossref, "fetch_json", lambda *a, **k: None)
    assert bibtex_crossref.fetch_bibtex("10.1/x", {}) is None

    def boom(*a, **k):
        raise RuntimeError("网络断了")

    monkeypatch.setattr(bibtex_crossref, "fetch_json", boom)
    assert bibtex_crossref.fetch_bibtex("10.1/x", {}) is None
