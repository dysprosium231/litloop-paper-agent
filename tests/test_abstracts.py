from litloop.search import abstracts as a


def test_clean_jats_formula_entities_and_boilerplate():
    raw = ("<jats:title>Abstract</jats:title>\n <jats:p>\n A\n <jats:sub>2</jats:sub>\n B\n"
           " <jats:sub>2</jats:sub>\n O\n <jats:sub>7</jats:sub>\n pyrochlores were sintered in 2 steps"
           " at 1600 &#x00B0;C.</jats:p>")
    assert a.clean_abstract(raw) == "A2B2O7 pyrochlores were sintered in 2 steps at 1600 °C."
    assert a.clean_abstract("Graphical abstract (Hf<sub>0.2</sub>Zr<sub>0.2</sub>)C is hard. "
                            "© 2020 Elsevier Ltd. All rights reserved.") == "(Hf0.2Zr0.2)C is hard."
    assert a.clean_abstract("Abstract: Al2O3 for 3 h. Copyright © 2021 The American Ceramic Society") \
        == "Al2O3 for 3 h."


def test_very_long_abstract_keeps_head_and_conclusion():
    text = "start " + "x" * 6000 + " conclusion."
    out = a.clean_abstract(text)
    assert len(out) < 4000 and out.startswith("start") and out.endswith("conclusion.")


def test_fill_asks_all_sources_and_keeps_the_first_answer(monkeypatch):
    import time
    calls = []

    def s2(dois, timeout):
        calls.append(("s2", tuple(dois)))
        time.sleep(0.3)  # slower than the others
        return {"10.1111/b": "S2 text", "10.1002/c": "S2 c"}

    class FakeScopus:
        def __init__(self, key):
            pass

        def get(self, doi, timeout):
            calls.append(("scopus", doi))
            return {"10.1016/a": "Scopus text. © 2020 Elsevier Ltd.", "10.1111/b": "Scopus b"}.get(doi, "")

    monkeypatch.setattr(a, "_semantic_scholar", s2)
    monkeypatch.setattr(a, "_Scopus", FakeScopus)
    monkeypatch.setattr(a, "_crossref", lambda doi, timeout, email="": calls.append(("crossref", doi)) or
                        ("<jats:p>CR text</jats:p>" if doi == "10.1002/c" else ""))
    rows = [{"doi": "10.1016/A", "abstract": ""}, {"doi": "10.1111/b"}, {"doi": "10.1002/c"},
            {"doi": "10.1007/d"}, {"doi": "10.9/e", "abstract": "<p>Already here</p>", "source": "openalex"}]
    a.fill_abstracts(rows, {"elsevier_api_key": "k"})
    # Scopus and Crossref answered before the slow Semantic Scholar call
    assert [r.get("abstract_source") for r in rows] == ["scopus", "scopus", "crossref", None, "openalex"]
    assert rows[0]["abstract"] == "Scopus text." and rows[2]["abstract"] == "CR text"
    assert rows[4]["abstract"] == "Already here" and rows[3]["abstract_missing"] is True
    assert ("s2", ("10.1016/a", "10.1111/b", "10.1002/c", "10.1007/d")) in calls


def test_fill_does_not_wait_for_a_slow_source_once_everything_is_found(monkeypatch):
    import time
    monkeypatch.setattr(a, "_semantic_scholar", lambda dois, timeout: time.sleep(3) or {})
    monkeypatch.setattr(a, "_Scopus", lambda key: (_ for _ in ()).throw(AssertionError("no key given")))
    monkeypatch.setattr(a, "_crossref", lambda doi, timeout, email="": "found")
    rows = [{"doi": "10.1/x"}, {"doi": "10.1/y"}]
    t0 = time.monotonic()
    a.fill_abstracts(rows, {})
    assert time.monotonic() - t0 < 1.5
    assert [r["abstract_source"] for r in rows] == ["crossref", "crossref"]


def test_no_elsevier_key_uses_free_sources_only(monkeypatch):
    monkeypatch.setattr(a, "_semantic_scholar", lambda dois, timeout: {})
    monkeypatch.setattr(a, "_Scopus", lambda key: (_ for _ in ()).throw(AssertionError("scopus used")))
    monkeypatch.setattr(a, "_crossref", lambda doi, timeout, email="": "")
    rows = [{"doi": "10.1016/a"}]
    a.fill_abstracts(rows, {})
    assert rows[0]["abstract_missing"] is True


def test_trim_authors():
    rows = [{"authors": ["A", "B", "C", "D", "E"]}, {"authors": ["A"]}]
    a.trim_authors(rows)
    assert rows[0] == {"authors": ["A", "B", "C"], "author_count": 5} and rows[1] == {"authors": ["A"]}


def test_dedupe_titles_merges_preprint_and_published():
    from litloop.search.quick import _dedupe_titles
    rows = [{"title": "High-Entropy Fluorite Oxides for barrier coatings", "doi": "", "cited_by_count": 3},
            {"title": "High-entropy fluorite oxides for barrier coatings.", "doi": "10.1/x",
             "journal": "J", "cited_by_count": 650},
            {"title": "Short", "doi": "10.1/s"}, {"title": "Short", "doi": "10.1/t"}]
    out = _dedupe_titles(rows)
    assert len(out) == 3 and out[0]["doi"] == "10.1/x" and out[0]["cited_by_count"] == 650

def test_semantic_scholar_batches_are_chunked_and_odd_replies_ignored(monkeypatch):
    import litloop.search.abstracts as a
    sizes = []

    class R:
        status_code = 200

        def __init__(self, rows):
            self.rows = rows

        def json(self):
            return self.rows

    class S:
        def post(self, url, params=None, json=None, headers=None, timeout=None):
            ids = json["ids"]
            sizes.append(len(ids))
            if len(sizes) == 1:
                return R([{"abstract": "text"} if i == 0 else None for i in range(len(ids))])
            return R({"message": "odd"})

    monkeypatch.setattr(a, "_session", lambda: S())
    dois = [f"10.1/{i}" for i in range(1200)]
    got = a._semantic_scholar(dois, 5)
    assert sizes == [500, 500]          # second reply was not a list: stop asking
    assert got == {"10.1/0": "text"}


def test_scopus_retries_when_throttled_and_stops_only_when_the_quota_is_spent(monkeypatch):
    replies = []

    class R:
        def __init__(self, code, spent=False, text=""):
            self.status_code, self.text = code, text
            self.headers = {"X-ELS-Status": "QUOTA_EXCEEDED - Quota Exceeded"} if spent else {}

        def json(self):
            return {"abstracts-retrieval-response": {"coredata": {"dc:description": self.text}}}

    class S:
        def get(self, *a, **k):
            return replies.pop(0)

    monkeypatch.setattr(a, "_session", lambda: S())
    monkeypatch.setattr(a.time, "sleep", lambda s: None)
    scopus = a._Scopus("k")
    replies[:] = [R(429), R(200, text="found")]          # throttled once, then fine
    assert scopus.get("10.1016/x", 5) == "found" and not scopus.exhausted
    replies[:] = [R(429, spent=True)]                    # weekly quota spent (Elsevier's signal)
    assert scopus.get("10.1016/y", 5) is None and scopus.exhausted   # could not ask: not a definite "none"


def test_fill_marks_a_paper_decided_only_when_every_source_answered(monkeypatch):
    class FakeScopus:
        def __init__(self, key):
            pass

        def get(self, doi, timeout):
            return {"10.1016/none": "", "10.1016/timeout": None}.get(doi, "")

    monkeypatch.setattr(a, "_semantic_scholar", lambda dois, timeout: {})
    monkeypatch.setattr(a, "_Scopus", FakeScopus)
    monkeypatch.setattr(a, "_crossref", lambda doi, timeout, email="": "")
    rows = [{"doi": "10.1016/none"}, {"doi": "10.1016/timeout"}]
    a.fill_abstracts(rows, {"elsevier_api_key": "k"})
    assert [r["abstract_decided"] for r in rows] == [True, False]


def test_springer_is_asked_only_for_springer_nature_dois(monkeypatch):
    asked = []
    monkeypatch.setattr(a, "_semantic_scholar", lambda dois, timeout: {})
    monkeypatch.setattr(a, "_crossref", lambda doi, timeout, email="": "")
    monkeypatch.setattr(a, "_springer", lambda doi, timeout, key, limit: asked.append(doi) or "Springer text")
    rows = [{"doi": "10.1007/s1"}, {"doi": "10.1038/n2"}, {"doi": "10.1016/e3"}, {"doi": "10.1021/acs4"}]
    a.fill_abstracts(rows, {"springer_meta_api_key": "k"})
    assert sorted(asked) == ["10.1007/s1", "10.1038/n2"]
    assert [r.get("abstract_source") for r in rows] == ["springer", "springer", None, None]


def test_springer_stops_at_the_daily_limit(monkeypatch):
    monkeypatch.setattr(a.quota, "summary", lambda: {"springer": {"requests_today": 500}})
    monkeypatch.setattr(a, "_session", lambda: (_ for _ in ()).throw(AssertionError("no request expected")))
    assert a._springer("10.1007/x", 5, "k", 500) is None


def test_fill_reports_progress_as_papers_are_settled(monkeypatch):
    from litloop.search import abstracts
    monkeypatch.setattr(abstracts, "_semantic_scholar", lambda dois, timeout: {"10.1/a": "Found by S2."})
    monkeypatch.setattr(abstracts, "_crossref", lambda doi, timeout, email="": "")
    seen = []
    rows = [{"doi": "10.1/a", "title": "A"}, {"doi": "10.1/b", "title": "B"}]
    abstracts.fill_abstracts(rows, {}, budget_s=5, progress=lambda done, total: seen.append((done, total)))
    assert seen[0] == (0, 2) and seen[-1] == (2, 2)   # a found, b: Crossref said there is none
