"""Result clean-up found in the SiBCN acceptance run (2026-10-12)."""

from __future__ import annotations

from litloop import search

_ABSTRACT = (
    "Aerogels with low thermal conductivity, high porosity and large specific surface area were widely used "
    "in the field of thermal insulation materials. Polymer-derived ceramic aerogels prepared by the precursor "
    "route would show severe shrinkage during the pyrolysis process. Herein, inspired by the tubular porous "
    "structure of bamboo culm walls, coaxial porous fibers consisting of uniformly dispersed parallel "
    "through-holes were prepared by the coaxial electrostatic spinning technique."
)


def _rec(doi, title, abstract=_ABSTRACT, authors=("Xuzhi Wang", "Haiming Li"), **kw):
    return {"doi": doi, "title": title, "abstract": abstract, "authors": list(authors), **kw}


def test_preprint_folds_into_published_version_despite_new_title():
    published = _rec("10.1016/j.cej.2024.157621",
                     "Coaxial porous SiBCN/SiCN ceramic fiber aerogels with reduced shrinkage", cited_by_count=31)
    preprint = _rec("10.2139/ssrn.4911027",
                    "Bio-Inspired Parallel Through-Holes Ceramic Fiber Aerogel: Fabrication, Anti-Shrinkage",
                    oa_url="http://dx.doi.org/10.2139/ssrn.4911027")
    stats = {}
    out = search._merge_preprints([published, preprint], stats)
    assert out == [published]
    assert published["preprint_doi"] == "10.2139/ssrn.4911027"
    assert published["preprint_url"].endswith("ssrn.4911027") and stats["merged_preprints"] == 1


def test_unpublished_preprint_and_unrelated_paper_stay():
    published = _rec("10.1016/j.x.1", "Something else", abstract="entirely different text " * 10,
                     authors=("Hongli Liu",))
    preprint = _rec("10.21203/rs.3.rs-10491947/v1", "Hollow SiBCN fibers", authors=("Hongli Liu",))
    assert search._merge_preprints([published, preprint]) == [published, preprint]


def test_journal_abbreviation_titles_are_dropped():
    junk = {"doi": "10.1002/aoc.1", "title": "Appl. Organomet. Chem.", "journal": "Applied Organometallic Chemistry"}
    same_as_journal = {"title": "Ceramics International", "journal": "Ceramics International"}
    real = {"title": "Thermal stability of SiCN ceramics", "journal": "Appl. Organomet. Chem."}
    assert search._drop_junk_titles([junk, same_as_journal, real]) == [real]


def test_hyphenated_concept_missing_ranks_below_full_matches():
    q = "polymer-derived ceramics thermal conductivity"
    polymers = {"title": "Thermal Conductivity of Nanoparticles Filled Polymers",
                "abstract": "Polymers ... fillers derived from ceramic materials raise thermal conductivity."}
    pdc = {"title": "SiOC foams", "abstract": "Polymer derived ceramics show low thermal conductivity."}
    sibcn = {"title": "Si–B–C–N", "abstract": "Polymer–derived Si–B–C–N with low thermal conductivity."}
    no_abstract = {"title": "Thermal conductivity of polymer-derived ceramics", "abstract": ""}
    ranked = search._match_tier([polymers, pdc, sibcn, no_abstract], q)
    assert ranked == [pdc, sibcn, no_abstract, polymers]  # nothing dropped, the miss goes last
    assert search._match_tier([polymers], "thermal conductivity ceramics") == [polymers]


def test_chemical_systems_and_terms_with_digits_are_not_concepts():
    assert search._query_compounds("Al-Si-Mg alloy infiltration SiC") == set()
    assert search._query_compounds("Si-B-C-N ceramics") == set()
    assert search._query_compounds("3D-SiC network Al-12%Si") == set()
    assert search._query_compounds("polymer-derived SiOC lithium-ion") == {"polymerderived", "lithiumion"}
    al_mg_si = {"title": "SiC honeycomb/Al-Mg-Si composite", "abstract": "Al-Mg-Si alloy infiltrated SiC preforms."}
    assert search._match_tier([al_mg_si], "Al-Si-Mg alloy infiltration SiC")[0]["_tier"] == 0


def test_venue_without_doi_is_flagged():
    rows = [{"doi": None, "journal": "Journal of International Crisis and Risk Communication Research"},
            {"doi": "10.1/x", "journal": "J. Eur. Ceram. Soc."}]
    search._note_unverified_venues(rows)
    assert rows[0]["venue_unverified"] is True and "venue_unverified" not in rows[1]


def test_a_record_matching_another_wording_is_not_ranked_down():
    full = {"title": "Polymer-derived ceramic aerogels", "abstract": "Polymer-derived ceramic aerogels " * 20}
    assert search._match_tier([full], "PDC aerogel thermal")[0]["_tier"] == 2
    full.pop("_tier")
    assert search._match_tier([full], "PDC aerogel thermal", ["polymer-derived ceramic aerogel thermal"])[0]["_tier"] == 0


def test_quick_search_runs_each_wording_and_takes_results_in_turns(monkeypatch):
    runs = {"PDC aerogel": [_rec("10.1/a", "A"), _rec("10.1/b", "B")],
            "polymer-derived ceramic aerogel": [_rec("10.1/c", "C"), _rec("10.1/a", "A again")]}
    used = [{"id": "x", "term": "PDC", "with": ["polymer-derived ceramic"], "query": "PDC aerogel"}]
    monkeypatch.setattr(search, "_glossary_variants", lambda q: (["polymer-derived ceramic aerogel"], used))
    monkeypatch.setattr(search, "_collect_one", lambda q, *a: [dict(r) for r in runs[q]])
    expansions: list = []
    got = search._collect("PDC aerogel", 10, 10, None, None, None, None, None, expansions)
    assert [r["doi"] for r in got] == ["10.1/a", "10.1/c", "10.1/b"] and expansions == used
    monkeypatch.setattr(search, "_glossary_variants", lambda q: ([], []))
    assert [r["doi"] for r in search._collect("PDC aerogel", 10, 10, None, None, None, None, None)] == ["10.1/a", "10.1/b"]
