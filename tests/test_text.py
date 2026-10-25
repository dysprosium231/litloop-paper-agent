"""Shared text normalisation (litloop.terms.text)."""

from litloop.terms import text


def test_every_dash_counts_the_same():
    assert text.ascii_dashes("10.1016/j.x–2024−a") == "10.1016/j.x-2024-a"
    assert text.plain("Polymer‐derived Aluminium") == "polymer derived aluminum"
    assert text.squash("Si–B/C N") == "sibcn"


def test_singular_and_stem():
    assert [text.singular(w) for w in ("ceramics", "properties", "glass", "virus", "analysis", "gas")] == [
        "ceramic", "property", "glass", "virus", "analysis", "gas"]
    assert text.stem("infiltration") == text.stem("infiltrated") == "infiltr"


def test_elements_and_chemical_systems():
    assert len(text.elements()) == 118 and ("Al", "aluminum") in text.elements()
    assert text.is_chemical_system("Al-Si-Mg") and text.is_chemical_system("Si–B–C–N")
    assert not text.is_chemical_system("Ab-Cd") and not text.is_chemical_system("SiC")


def test_paper_key():
    assert text.paper_key({"doi": "https://doi.org/10.1/ABC", "title": "x"}) == "10.1/abc"
    assert text.paper_key({"title": "A Title: With, Punctuation"}) == "title:atitlewithpunctuation"


def test_ranking_stop_list_is_pinned():
    # Changing it changes the deep search's ranking: re-run scripts/eval_synergy.py first.
    assert text.RANKING_STOPWORDS == frozenset(
        "the and of in for with by on to a an is are was were from as at this that these be or its "
        "their which we our into than also".split())
    assert {"review", "study", "effect", "via"}.isdisjoint(text.RANKING_STOPWORDS)
