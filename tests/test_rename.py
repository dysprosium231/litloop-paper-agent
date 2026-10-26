"""Generated PDF names: readable, unique per DOI, never overwriting another file."""

from __future__ import annotations

from litloop.download.rename import generate_filename, rename_pdf


def _meta(title, family="Jiang", year=2024, doi=None):
    m = {"title": [title], "author": [{"family": family}], "year": year}
    if doi:
        m["DOI"] = doi
    return m


def test_name_carries_title_words_and_full_doi():
    name = generate_filename(_meta("Lightweight, thermally insulating SiBCN/Al2O3 ceramic aerogel"),
                             doi="10.1016/j.cej.2024.157656")
    assert name == "Jiang2024_Lightweight_Thermally_Insulating_SiBCN_10.1016_j.cej.2024.157656"


def test_papers_that_used_to_collide_now_differ():
    a = generate_filename(_meta("Lightweight and thermally insulating polymer-derived SiBCN/SiCnw aerogel",
                                doi="10.1016/j.cej.2024.148878"))
    b = generate_filename(_meta("Lightweight, thermally insulating SiBCN/Al2O3 ceramic aerogel",
                                doi="10.1016/j.cej.2024.157656"))
    assert a != b
    assert a.endswith("_10.1016_j.cej.2024.148878")


def test_without_doi_the_readable_part_stands_alone():
    assert generate_filename(_meta("Ceramics", family="Müller")) == "Muller2024_Ceramics"


def test_existing_file_is_never_overwritten(tmp_path):
    meta = _meta("Lightweight aerogel", doi="10.1/x")
    existing = tmp_path / f"{generate_filename(meta)}.pdf"
    existing.write_bytes(b"%PDF first copy " + b"x" * 100)
    new = tmp_path / "tmp.pdf"
    new.write_bytes(b"%PDF other copy " + b"y" * 100)  # same size, different content

    out = rename_pdf(new, meta, doi="10.1/x")

    assert out.name.endswith("_2.pdf")
    assert existing.read_bytes().startswith(b"%PDF first copy")
    assert out.read_bytes().startswith(b"%PDF other copy")


def test_identical_content_reuses_existing_file(tmp_path):
    meta = _meta("Lightweight aerogel", doi="10.1/x")
    existing = tmp_path / f"{generate_filename(meta)}.pdf"
    existing.write_bytes(b"%PDF same")
    new = tmp_path / "tmp.pdf"
    new.write_bytes(b"%PDF same")

    assert rename_pdf(new, meta, doi="10.1/x") == existing
    assert not new.exists()
