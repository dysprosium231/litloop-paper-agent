"""The glossary: entries the user keeps, suggestions from abstracts, and how a search uses them."""

from __future__ import annotations

import pytest

from litloop import deep_search, glossary


@pytest.fixture(autouse=True)
def _own_glossary(tmp_path, monkeypatch):
    monkeypatch.setattr(glossary, "path", lambda: tmp_path / "glossary.json")


def test_entries_are_added_confirmed_and_deleted():
    e = glossary.add(["PDC", "polymer-derived ceramic"])
    assert e["status"] == "active"
    s = glossary.add(["EMW", "electromagnetic wave"], source="agent")
    assert s["status"] == "suggested"  # the agent's entries wait for the user
    with pytest.raises(ValueError):
        glossary.add(["pdc", "Polymer-Derived Ceramics"])  # the same entry, written differently
    with pytest.raises(ValueError):
        glossary.add(["PDC"])
    assert glossary.update(s["id"], confirm=True)["status"] == "active"
    glossary.delete(e["id"])
    assert [x["terms"][0] for x in glossary.entries("active")] == ["EMW"]
    with pytest.raises(KeyError):
        glossary.delete(e["id"])


def test_normalize_merges_case_dashes_plurals_and_british_spelling():
    assert glossary.normalize("Metal-matrix composites") == glossary.normalize("metal matrix composite")
    assert glossary.normalize("aluminium matrix composites") == "aluminum matrix composite"
    assert glossary.normalize("Polymer‐derived ceramics") == "polymer derived ceramic"
    assert glossary.normalize("glass") == "glass"


def test_a_query_gets_a_variant_per_other_term_unless_it_is_the_term_alone():
    use = [{"id": "a", "terms": ["PDC", "polymer-derived ceramic", "preceramic polymer"]}]
    variants, used = glossary.query_variants("PDC aerogel microwave absorption", use)
    assert variants == ["polymer-derived ceramic aerogel microwave absorption",
                        "preceramic polymer aerogel microwave absorption"]
    assert used[0]["term"] == "PDC" and not used[0].get("alone")
    variants, used = glossary.query_variants("PDC", use)
    assert variants == [] and used[0]["alone"]  # nothing else to keep it on topic
    variants, _ = glossary.query_variants("pdc drill bit", use)
    assert variants == []  # an abbreviation is matched with its case


def test_a_term_is_found_however_normalize_would_write_it():
    use = [{"id": "a", "terms": ["PDC", "polymer-derived ceramic"]},
           {"id": "b", "terms": ["CVI", "chemical vapor infiltration"]},
           {"id": "c", "terms": ["EIS", "impedance spectroscopy"]}]
    assert glossary.query_variants("PDCs aerogel", use)[0] == ["polymer-derived ceramic aerogel"]
    assert glossary.query_variants("polymer derived ceramics aerogel", use)[0] == ["PDC aerogel"]
    # the other spelling comes first, then the glossary's terms
    assert glossary.query_variants("chemical vapour infiltration SiC", use)[0] == ["chemical vapor infiltration SiC", "CVI SiC"]
    assert glossary.query_variants("impedance spectroscopies of LLZO", use)[0] == ["EIS of LLZO"]
    assert glossary.query_variants("PDCX aerogel", use)[0] == []


def test_a_query_is_also_searched_in_its_other_spelling():
    assert glossary.other_spelling("Fibre reinforced aluminium foams") == (
        "Fiber reinforced aluminum foams", [("Fibre", "fiber"), ("aluminium", "aluminum")])
    assert glossary.other_spelling("aluminum foam")[0] == "aluminium foam"
    assert glossary.other_spelling("PDC SiC aerogel") == ("", [])  # abbreviations stay; aerogel has no other
    variants, used = glossary.query_variants("aluminium", [])
    assert variants == ["aluminum"] and used[0]["builtin"] == "spelling"  # safe even alone: same word


def test_core_term_words_get_the_other_terms_in_quotes():
    use = [{"id": "a", "terms": ["PDC", "polymer-derived ceramic"]}]
    assert glossary.alternatives("PDC", use) == (["polymer-derived ceramic"], [])
    q = deep_search._topic_search("PDC aerogel", {"pdc": ["polymer-derived ceramic"]})
    assert q == '(PDC OR "polymer-derived ceramic") AND aerogel'


def test_elements_are_built_in_and_their_symbols_matched_with_care():
    use = glossary.in_use()
    variants, used = glossary.query_variants("Al SiC composite", use)
    assert variants == ["aluminum SiC composite"] and used[0]["builtin"] == "element"
    assert glossary.query_variants("al alloy foam", use)[0] == []  # a symbol is matched with its case
    # everyday words and single letters never trigger: "In situ", "lead to", "type I"
    assert glossary.query_variants("In situ TEM of lead halide perovskite type I", use)[0] == []
    assert glossary.alternatives("La", use) == (["lanthanum"], ["element"])  # the name is still added
    assert glossary.alternatives("indium", use) == ([], [])  # but never the loose symbol
    assert glossary.alternatives("aluminum", use) == (["Al"], ["element"])
    _, alts, _ = deep_search._glossary_expansions(["x"], ["SiC aluminum"])
    assert deep_search._topic_search("SiC aluminum", alts) == "SiC AND (aluminum OR aluminium OR Al)"


def test_built_in_entries_can_be_switched_off():
    glossary.set_builtin("element:Al", False)
    assert "element:Al" not in {e["id"] for e in glossary.in_use()}
    assert glossary.query_variants("Al SiC composite", glossary.in_use())[0] == []
    from litloop import spelling
    glossary.set_builtin("spelling:aluminum", False)
    assert spelling.variants("aluminum") == ["aluminum"] and spelling.to_us("aluminium") == "aluminium"
    found = glossary.builtin("alumin")
    assert [e["id"] for e in found["elements"]] == ["element:Al"] and found["elements"][0]["disabled"]
    assert found["spelling"][0]["id"] == "spelling:aluminum" and found["spelling"][0]["disabled"]
    glossary.set_builtin("spelling:aluminum", True)
    assert "aluminium" in spelling.variants("aluminum")
    for bad in ("element:Xx", "spelling:ceramic", "other:x"):
        with pytest.raises(KeyError):
            glossary.set_builtin(bad, False)


def test_expansions_need_another_concept_and_report_what_they_did():
    glossary.add(["PDC", "polymer-derived ceramic"])
    variants, alts, used = deep_search._glossary_expansions(["PDC aerogel"], ["PDC aerogel"])
    assert variants == {"PDC aerogel": ["polymer-derived ceramic aerogel"]}
    assert alts == {"pdc": ["polymer-derived ceramic"]}
    assert {u.get("core_term", False) for u in used} == {True, False}
    _, alts, _ = deep_search._glossary_expansions(["x"], ["PDC"])
    assert alts == {}  # one core-term word: nothing else keeps the expansion on topic


ABSTRACTS = [
    "Polymer-derived ceramics (PDCs) are promising for electromagnetic wave (EMW) absorption.",
    "Here polymer derived ceramics (PDCs) with a high reflection loss (RL) were made.",
    "We prepared polymer-derived ceramic (PDC) aerogels by freeze drying; scanning electron microscopy (SEM).",
    "Ceramic matrix composites (CMC) were infiltrated.",
    "Carboxymethyl cellulose (CMC) binders were used, and ceramic matrix composites (CMCs) compared.",
    "A carboxymethyl cellulose (CMC) gel; reflection loss (RL) of -50 dB.",
]


def test_discover_merges_wordings_and_skips_common_methods():
    found = glossary.discover(ABSTRACTS)
    assert set(found["PDC"]) == {glossary._meaning_key(glossary.normalize("polymer-derived ceramics"))}
    assert next(iter(found["PDC"].values()))["papers"] == 3  # PDCs, PDC; dash or none
    assert len(found["CMC"]) == 2  # two meanings
    assert "SEM" not in found  # every abstract has it


def test_suggestions_are_made_once_and_a_deleted_one_is_not_made_again():
    new = glossary.suggest_from(ABSTRACTS)
    pairs = {(e["terms"][0], e["terms"][1]) for e in new}
    assert ("PDC", "polymer-derived ceramics") in pairs
    assert ("RL", "reflection loss") in pairs
    assert ("EMW", "electromagnetic wave") not in pairs  # in one abstract only
    cmc = [e for e in new if e["terms"][0] == "CMC"]
    assert len(cmc) == 2 and all("其他全称" in e["note"] for e in cmc)
    assert all(e["status"] == "suggested" and e["source"] == "auto" for e in new)
    assert glossary.suggest_from(ABSTRACTS) == []  # already suggested
    glossary.delete(cmc[0]["id"])
    assert glossary.suggest_from(ABSTRACTS) == []  # the user said no: not again
    assert len([e for e in glossary.entries("suggested") if e["terms"][0] == "CMC"]) == 1


def test_a_variant_of_a_query_counts_as_matching_it():
    state = {"session": "g1", "queries": ["PDC aerogel"], "query_variants": {"PDC aerogel": ["polymer-derived ceramic aerogel"]},
             "seeds": [], "seed_refs": [], "orig_seeds": [],
             "pool": {"a": {"title": "Polymer-derived ceramic aerogel for insulation", "abstract": "", "refs": []},
                      "b": {"title": "Silica aerogel", "abstract": "", "refs": []}}}
    deep_search._score(state)
    assert state["pool"]["a"]["query_hits"] == 1 and state["pool"]["b"]["query_hits"] == 0
