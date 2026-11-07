"""The and-style pass: short phrases that find papers the relevance pass buries."""

from litloop.search import deep

CORE = ["lanthanide|cerium|erbium|ytterbium", "drug|delivery|antioxidant"]


def test_a_long_query_also_runs_as_two_short_phrases():
    terms = deep._exact_terms(["cerium oxide nanoparticles drug delivery"], CORE)
    assert "cerium oxide nanoparticles" in terms
    assert "cerium nanoparticle drug" in terms


def test_repeats_are_dropped_and_the_list_is_capped():
    terms = deep._exact_terms(["drug delivery of nano particles"] * 30, CORE)
    assert len(terms) <= 16
    assert len(set(terms)) == len(terms)


def test_an_empty_query_adds_nothing():
    assert deep._exact_terms(["", "  "], CORE) == []


def test_exact_candidates_asks_the_work_to_have_every_word(monkeypatch):
    calls = []

    def fake_pages(flt, max_records, split_over=False):
        calls.append((flt, max_records))
        return [{"doi": "10.1/x", "title": "Cerium dioxide nanoparticles"}]

    monkeypatch.setattr(deep, "_pages", fake_pages)
    got = deep._exact_candidates(["cerium nanoparticle drug"], 2015, 2020)
    assert got["cerium nanoparticle drug"][0]["doi"] == "10.1/x"
    flt, cap = calls[0]
    assert "title_and_abstract.search:cerium nanoparticle drug" in flt
    assert "from_publication_date:2015-01-01" in flt
    assert cap == 60


def test_a_failing_exact_query_does_not_stop_the_others(monkeypatch):
    def boom(flt, max_records, split_over=False):
        raise RuntimeError("no network")

    monkeypatch.setattr(deep, "_pages", boom)
    assert deep._exact_candidates(["cerium nanoparticle drug"], None, None) == {
        "cerium nanoparticle drug": []
    }
