"""Deep search: pool building, ranking, snowballing and stopping, without the network."""

from __future__ import annotations

import pytest

from litloop import deep_search, screening


def rec(doi, title, refs=(), abstract="", year=2020):
    return {"openalex": "W" + doi.split(".")[-1], "doi": doi, "title": title, "year": year, "journal": "J",
            "issn": [], "authors": ["A"], "cited_by_count": 1, "abstract": abstract, "refs": list(refs)}


SEED = rec("10.1/seed", "Pressureless infiltration of SiC by aluminum", refs=["R1", "R2", "R3"])
REF_TARGET = rec("10.1/coupled", "Al/SiC composites by infiltration", refs=["R1", "R2", "R3"],
                 abstract="aluminum infiltration into SiC preforms")
KEYWORD_ONLY = rec("10.1/keyword", "Pressureless infiltration of SiC preforms by aluminum alloys",
                   abstract="pressureless infiltration SiC aluminum")
NOISE = rec("10.1/noise", "Endoscopic resection", refs=["R9"])
LATE = rec("10.1/late", "Late paper found through the new seed", refs=["R7"],
           abstract="pressureless infiltration SiC aluminum")


@pytest.fixture
def net(tmp_path, monkeypatch):
    monkeypatch.setattr(screening._config, "DATA_DIR", tmp_path)
    calls = {"cites_refs": [], "citing": []}
    by_doi = {r["doi"]: r for r in (SEED, REF_TARGET, KEYWORD_ONLY, NOISE, LATE)}

    def by_filter(field, values, extra=None):
        if field == "doi":
            return [by_doi[v] for v in values if v in by_doi]
        return []  # seed references: no metadata needed for these tests

    def cites_refs(refs, terms, years, max_records=1000):
        calls["cites_refs"].append(sorted(refs))
        return [REF_TARGET, NOISE] if "R1" in refs else ([LATE] if "R7" in refs else [])

    def citing(ids, years, max_records=400):
        calls["citing"].append(sorted(ids))
        return []

    def keyword(queries, yf, yt):
        return {q: [{"doi": KEYWORD_ONLY["doi"], "title": KEYWORD_ONLY["title"],
                     "abstract": KEYWORD_ONLY["abstract"]}] for q in queries}

    monkeypatch.setattr(deep_search, "_by_filter", by_filter)
    monkeypatch.setattr(deep_search, "_cites_refs_on_topic", cites_refs)
    monkeypatch.setattr(deep_search, "_citing", citing)
    monkeypatch.setattr(deep_search, "_keyword_candidates", keyword)
    import litloop.abstracts as ab
    import litloop.search as s
    monkeypatch.setattr(ab, "fill_abstracts", lambda items, cfg=None, **kw: None)
    monkeypatch.setattr(s, "_add_journal_rank", lambda items: None)
    monkeypatch.setattr(s, "_flag_retractions", lambda items: None)
    return calls


def test_start_ranks_coupled_and_matching_papers_first_and_leaves_seeds_out(net):
    out = deep_search.start("SiC/Al 无压浸渗", ["pressureless infiltration SiC aluminum"], ["10.1/SEED"],
                            ["aluminum"], must_find=["10.1/coupled", "10.1/missing"], batch_size=10)
    dois = [b["doi"] for b in out["batch"]]
    assert dois[0] == "10.1/coupled"            # 3 shared references + keyword match
    assert "10.1/keyword" in dois and dois[-1] == "10.1/noise"
    assert "10.1/seed" not in dois
    assert out["batch"][0]["why"]["shared_refs_with_seeds"] == 3
    cov = {c["doi"]: c["status"] for c in out["coverage"]}
    assert cov == {"10.1/coupled": "in_pool", "10.1/missing": "not_found"}


def test_high_rated_papers_become_seeds_and_pull_in_their_neighbours(net):
    out = deep_search.start("q", ["pressureless infiltration SiC aluminum"], ["10.1/seed"], ["aluminum"],
                            batch_size=2)
    sid = out["session"]
    LATE_REFS_HOLDER = dict(KEYWORD_ONLY, refs=["R7"])
    state = deep_search._load(sid)
    state["pool"]["10.1/keyword"]["refs"] = LATE_REFS_HOLDER["refs"]
    deep_search._save(state)
    screening.save("q", [{"doi": "10.1/coupled", "title": "t", "relevance": "中", "reason": "r"},
                         {"doi": "10.1/keyword", "title": "t", "relevance": "高", "reason": "r"}], session=sid)
    nxt = deep_search.next_batch(sid)
    assert ["R7"] in net["cites_refs"]                     # the new seed's references were followed
    assert "10.1/late" in [b["doi"] for b in nxt["batch"]]
    assert "10.1/keyword" not in [b["doi"] for b in nxt["batch"]]


def test_unrated_papers_from_a_batch_come_back(net):
    out = deep_search.start("q", ["pressureless infiltration SiC aluminum"], ["10.1/seed"], ["aluminum"],
                            batch_size=1)
    first = out["batch"][0]["doi"]
    again = deep_search.next_batch(out["session"])
    assert again["batch"][0]["doi"] == first


def test_stop_when_a_batch_brings_almost_nothing(net):
    state = {"cap": 200, "served": [["a", "b", "c", "d"], ["e", "f", "g", "h"]]}
    rated = {k: "低" for k in "abcdefgh"}
    assert deep_search._stop(state, rated)["stop_suggested"] is True
    rated.update({"e": "高", "f": "中", "g": "高"})
    assert deep_search._stop(state, rated)["stop_suggested"] is False
    assert deep_search._stop({"cap": 5, "served": []}, {k: "低" for k in "abcde"})["stop_suggested"] is True


def test_start_needs_queries_and_seeds(net):
    with pytest.raises(ValueError):
        deep_search.start("q", [], ["10.1/seed"], ["x"])
    with pytest.raises(ValueError):
        deep_search.start("q", ["x"], [], ["x"])
