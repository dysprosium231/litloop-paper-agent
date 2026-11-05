"""Reading a .bib file someone hands us: entries, DOIs, and the fields around them."""

from litloop.citations import bib_import

BIB = r"""
@article{Smith2024,
  title = {A {SiC} aerogel with nested {braces}},
  author = {Smith, J. and Doe, A.},
  year = 2024,
  doi = {10.1016/j.ceramint.2024.01.001},
  journal = "Ceramics International"
}

@inproceedings{NoDoi2020,
  title = "Something without an identifier",
  year = {2020}
}
"""


def test_entries_keep_their_fields_and_nested_braces():
    entries = bib_import.parse_bib_text(BIB)
    assert [e["cite_key"] for e in entries] == ["Smith2024"]  # the one without a DOI is dropped
    first = entries[0]
    assert first["type"] == "article"
    assert first["title"] == "A {SiC} aerogel with nested {braces}"
    assert first["doi"] == "10.1016/j.ceramint.2024.01.001"
    assert first["author"] == "Smith, J. and Doe, A."
    assert first["year"] == "2024"
    assert first["journal"] == "Ceramics International"  # a value in quotes reads the same


def test_require_doi_false_keeps_everything():
    entries = bib_import.parse_bib_text(BIB, require_doi=False)
    assert [e["cite_key"] for e in entries] == ["Smith2024", "NoDoi2020"]
    assert "doi" not in entries[1]


def test_bare_values_are_read_too():
    text = "@misc{k, doi = 10.1/x, year = 1999}\n"
    entry = bib_import.parse_bib_text(text)[0]
    assert entry["doi"] == "10.1/x" and entry["year"] == "1999"


def test_a_file_is_read_as_utf8_and_falls_back_to_latin1(tmp_path):
    utf8 = tmp_path / "a.bib"
    utf8.write_text("@article{k, doi={10.1/a}, title={Sic 气凝胶}}", encoding="utf-8")
    assert bib_import.parse_bib_file(utf8)[0]["title"] == "Sic 气凝胶"

    latin = tmp_path / "b.bib"
    latin.write_bytes("@article{k, doi={10.1/b}, title={Sic 气凝胶}}".encode("gbk"))
    assert bib_import.parse_bib_file(latin)[0]["doi"] == "10.1/b"  # decodes as latin-1, still parses


def test_a_missing_file_is_an_empty_list(tmp_path):
    assert bib_import.parse_bib_file(tmp_path / "nope.bib") == []
    assert bib_import.parse_bib_text("") == []
