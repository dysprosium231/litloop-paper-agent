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

    def cites_refs(refs, terms, years, max_records=1000, credits0=None):
        calls["cites_refs"].append(sorted(refs))
        return ([REF_TARGET, NOISE] if "R1" in refs else ([LATE] if "R7" in refs else [])), set(refs)

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


def test_stop_knee_rule(net):
    b1 = [f"b1_{i}" for i in range(30)]
    b2 = [f"b2_{i}" for i in range(30)]
    b3 = [f"b3_{i}" for i in range(30)]
    state = {"cap": 200, "served": [b1, [], b2, b3]}
    rated = {}
    for i, k in enumerate(b1):
        rated[k] = "高" if i < 12 else "低"
    for i, k in enumerate(b2):
        rated[k] = "高" if i < 10 else "低"
    for i, k in enumerate(b3):
        rated[k] = "高" if i < 2 else "中"

    res = deep_search._stop(state, rated)
    assert res["stop_suggested"] is True
    assert "1/6" in res["stop_reason"]

    for i, k in enumerate(b3):
        rated[k] = "高" if i < 4 else "中"

    res2 = deep_search._stop(state, rated)
    assert res2["stop_suggested"] is False


def test_start_needs_queries_and_seeds(net):
    with pytest.raises(ValueError):
        deep_search.start("q", [], ["10.1/seed"], ["x"])
    with pytest.raises(ValueError):
        deep_search.start("q", ["x"], [], ["x"])


def test_rated_paper_without_doi_is_not_served_again(net, monkeypatch):
    no_doi = dict(rec("10.1/x", "Infiltration of SiC foams by aluminium", abstract="pressureless infiltration SiC aluminum"),
                  doi=None, openalex="WNODOI")
    monkeypatch.setattr(deep_search, "_cites_refs_on_topic", lambda refs, terms, years, max_records=1000, credits0=None: ([no_doi], set(refs)))
    out = deep_search.start("q", ["pressureless infiltration SiC aluminum"], ["10.1/seed"], ["aluminum"], batch_size=10)
    first = [b for b in out["batch"] if b["doi"] is None]
    assert first
    screening.save("q", [{"doi": b["doi"], "title": b["title"], "relevance": "低", "reason": "r"} for b in out["batch"]],
                   session=out["session"])
    nxt = deep_search.next_batch(out["session"])
    assert nxt["screened"] == len(out["batch"])
    assert not [b for b in nxt["batch"] if b["doi"] is None]


def _state(pool, seeds, orig, queries=("pressureless infiltration SiC aluminum",)):
    return {"pool": pool, "seeds": list(seeds), "orig_seeds": list(orig), "queries": list(queries),
            "seed_refs": sorted({r for s in seeds for r in pool[s]["refs"]})}


def test_references_only_snowballed_seeds_cite_count_less():
    pool = {k: rec(k, t, refs=r) for k, t, r in [
        ("10.1/o", "orig seed", ["A", "B"]), ("10.1/s", "snowballed seed", ["C", "D"]),
        ("10.1/on", "on topic", ["A", "B"]), ("10.1/drift", "neighbouring topic", ["C", "D"])]}
    st = _state(pool, ["10.1/o", "10.1/s"], ["10.1/o"])
    deep_search._score(st)
    assert pool["10.1/on"]["coupling"] == pool["10.1/drift"]["coupling"] == 2
    assert pool["10.1/on"]["anchored"] == 2 and pool["10.1/drift"]["anchored"] < 1


def test_model_learns_from_negative_ratings():
    on = "pressureless infiltration of SiC preforms by molten aluminium alloys wetting magnesium"
    off = "supergravity infiltration of copper into tungsten skeletons under centrifugal force"
    pool = {}
    for i in range(8):
        pool[f"10.1/on{i}"] = rec(f"10.1/on{i}", f"paper {i}", abstract=on)
        pool[f"10.1/off{i}"] = rec(f"10.1/off{i}", f"paper {i}", abstract=off)
    pool["10.1/new_on"] = rec("10.1/new_on", "x", abstract=on)
    pool["10.1/new_off"] = rec("10.1/new_off", "y", abstract=off)
    st = _state(pool, ["10.1/on0"], ["10.1/on0"])
    deep_search._score(st)
    few = {"10.1/on1": "高"}
    assert deep_search._rerank(st, few) == "formula"
    rated = {f"10.1/on{i}": "高" for i in range(1, 6)} | {f"10.1/off{i}": "低" for i in range(6)}
    assert deep_search._rerank(st, rated) == "model"
    assert pool["10.1/new_on"]["rank"] > pool["10.1/new_off"]["rank"]


def test_missing_abstracts_of_top_candidates_are_fetched_once(monkeypatch):
    import litloop.abstracts as ab
    calls = []

    def fill(items, cfg=None, **kw):
        calls.append(sorted(it["doi"] for it in items))
        for it in items:
            it["abstract"] = "fetched" if it["doi"] == "10.1/a" else ""
            it["abstract_decided"] = it["doi"] != "10.1/d"     # d: the source timed out

    monkeypatch.setattr(ab, "fill_abstracts", fill)
    pool = {"10.1/a": rec("10.1/a", "A"), "10.1/b": rec("10.1/b", "B"), "10.1/c": rec("10.1/c", "C", abstract="has"),
            "10.1/d": rec("10.1/d", "D")}
    st = _state(pool, [], [])
    assert deep_search._prefill_abstracts(st, {}) == 1
    assert calls[0] == ["10.1/a", "10.1/b", "10.1/d"]
    deep_search._prefill_abstracts(st, {})
    assert calls[1] == ["10.1/d"]          # b had a definite "none"; d was not answered, so it is asked again


def test_prefill_abstracts_picks_among_records_lacking_abstract(monkeypatch):
    import litloop.abstracts as ab
    monkeypatch.setattr(deep_search, "PREFILL", 2)
    calls = []

    def fill(items, cfg=None, **kw):
        calls.append([it["doi"] for it in items])

    monkeypatch.setattr(ab, "fill_abstracts", fill)
    pool = {
        "10.1/top1": dict(rec("10.1/top1", "Top 1", abstract="has"), rank=5.0),
        "10.1/top2": dict(rec("10.1/top2", "Top 2", abstract="has"), rank=4.0),
        "10.1/top3": dict(rec("10.1/top3", "Top 3", abstract="has"), rank=3.0),
        "10.1/low1": dict(rec("10.1/low1", "Low 1", abstract=""), rank=2.0),
        "10.1/low2": dict(rec("10.1/low2", "Low 2", abstract=""), rank=1.0),
    }
    st = _state(pool, [], [])
    deep_search._prefill_abstracts(st, {})
    assert len(calls) == 1
    assert calls[0] == ["10.1/low1", "10.1/low2"]


def test_topic_search_expands_alternatives_and_handles_punctuation():
    assert deep_search._topic_search("ceramic aerogel") == "ceramic aerogel"
    assert deep_search._topic_search("Al|aluminum|aluminium SiC") == "(Al OR aluminum OR aluminium) AND SiC"
    assert deep_search._topic_search("ceramic, aerogel") == "ceramic aerogel"
    assert deep_search._topic_search("a||b") == "(a OR b)"
    # British spellings are added on their own
    assert deep_search._topic_search("aluminum SiC") == "(aluminum OR aluminium) AND SiC"
    assert deep_search._topic_search("Al|aluminum fiber") == "(Al OR aluminum OR aluminium) AND (fiber OR fibre)"



def test_citation_query_that_hits_the_cap_is_split(monkeypatch):
    seen = []

    def pages(flt, max_records, split_over=False):
        ids = flt.split("cites:")[1].split(",")[0].split("|")
        seen.append(len(ids))
        if split_over and 2 * len(ids) > max_records:
            return None                               # the first page shows the count is over the cap
        return [rec(f"10.1/{i}_{n}", "t") for i in ids for n in range(2)]

    monkeypatch.setattr(deep_search, "_pages", pages)
    out, asked = deep_search._cites_refs_on_topic(["A", "B", "C", "D"], ["x"], [], max_records=3)
    assert len({r["doi"] for r in out}) == 8          # nothing lost to the cap
    assert seen == [4, 2, 2, 1, 1, 1, 1]              # split down to single references
    assert asked == {"A", "B", "C", "D"}


def test_a_single_reference_over_the_cap_keeps_only_one_page(monkeypatch):
    caps = []

    def pages(flt, max_records, split_over=False):
        caps.append((max_records, split_over))
        return None if split_over else [rec("10.1/x", "t")]

    monkeypatch.setattr(deep_search, "_pages", pages)
    deep_search._cites_refs_on_topic(["A", "B"], ["x"], [])
    assert caps[-1] == (deep_search.SINGLE_REF_CAP, False)


def test_expansion_stops_at_the_round_budget_and_keeps_the_rest_for_later(monkeypatch):
    from litloop import quota
    spent = [0]
    monkeypatch.setattr(quota, "spent", lambda provider: spent[0])
    monkeypatch.setattr(quota, "summary", lambda: {"openalex": {"remaining": 5000}})

    def pages(flt, max_records, split_over=False):
        spent[0] += deep_search.ROUND_CREDITS  # the first wave uses up the round
        return [rec("10.1/" + flt.split("cites:")[1].split(",")[0].split("|")[0], "t")]

    monkeypatch.setattr(deep_search, "_pages", pages)
    refs = [f"W{i}" for i in range(50 * deep_search.WORKERS * 2 + 50)]  # one wave more than fits
    out, asked = deep_search._cites_refs_on_topic(refs, ["x"], [])
    assert len(asked) == 50 * deep_search.WORKERS * 2
    assert asked == set(refs[:len(asked)])            # the most important references were asked


def test_expansion_waits_when_the_daily_budget_is_nearly_spent(monkeypatch):
    from litloop import quota
    monkeypatch.setattr(quota, "spent", lambda provider: 0)
    monkeypatch.setattr(quota, "summary", lambda: {"openalex": {"remaining": deep_search.RESERVE_CREDITS - 1}})
    monkeypatch.setattr(deep_search, "_pages", lambda *a, **k: [rec("10.1/x", "t")])
    assert deep_search._cites_refs_on_topic(["A"], ["x"], []) == ([], set())


def test_angewandte_german_edition_is_the_same_paper():
    pool = {}
    deep_search._add(pool, rec("10.1002/anie.202116983", "Nanoagents"), "seed")
    deep_search._add(pool, rec("10.1002/ange.202116983", "Nanoagents"), "cites_seed_references")
    assert list(pool) == ["10.1002/anie.202116983"]
    assert pool["10.1002/anie.202116983"]["via"] == ["seed", "cites_seed_references"]


def test_openalex_retries_when_throttled_and_explains_a_spent_budget(monkeypatch):
    import litloop.search as s
    replies = []

    class R:
        def __init__(self, code, remaining="500"):
            self.status_code = code
            self.headers = {"X-RateLimit-Remaining": remaining, "X-RateLimit-Limit": "10000", "X-RateLimit-Reset": "7200"}

        def json(self):
            return {"results": []}

    class S:
        def get(self, *a, **k):
            return replies.pop(0)

    monkeypatch.setattr(s, "_plain_session", lambda: S())
    monkeypatch.setattr(deep_search.time, "sleep", lambda sec: None)
    replies[:] = [R(429), R(429), R(200)]
    assert deep_search._get({"filter": "x"}) == {"results": []}
    replies[:] = [R(429, remaining="0")]
    with pytest.raises(RuntimeError, match="daily budget used up"):
        deep_search._get({"filter": "x"})


def test_keyword_searches_are_counted_against_the_openalex_budget(monkeypatch):
    import requests
    from litloop import quota, search

    class R:
        status_code = 200
        headers = {"X-RateLimit-Credits-Used": "10", "X-RateLimit-Remaining": "4000", "X-RateLimit-Limit": "10000"}

    monkeypatch.setattr(requests.Session, "request", lambda self, method, url, *a, **k: R())
    before = quota.spent("openalex")
    search._plain_session().get("https://api.openalex.org/works", params={"search": "x"})
    search._plain_session().get("https://api.crossref.org/works")  # other APIs are not OpenAlex's business
    assert quota.spent("openalex") - before == 10
    assert quota.summary()["openalex"]["remaining"] == 4000
