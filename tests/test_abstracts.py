from litloop import abstracts as a


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


def test_fill_order_free_first_then_scopus_then_crossref(monkeypatch):
    calls = []
    monkeypatch.setattr(a, "_semantic_scholar",
                        lambda dois, timeout: calls.append(("s2", tuple(dois))) or {"10.1111/b": "S2 text"})

    class FakeScopus:
        def __init__(self, key):
            pass

        def get(self, doi, timeout):
            calls.append(("scopus", doi))
            return "Scopus text. © 2020 Elsevier Ltd." if doi == "10.1016/a" else ""

    monkeypatch.setattr(a, "_Scopus", FakeScopus)
    monkeypatch.setattr(a, "_crossref", lambda doi, timeout: calls.append(("crossref", doi)) or
                        ("<jats:p>CR text</jats:p>" if doi == "10.1002/c" else ""))
    rows = [{"doi": "10.1016/A", "abstract": ""}, {"doi": "10.1111/b"}, {"doi": "10.1002/c"},
            {"doi": "10.1007/d"}, {"doi": "10.9/e", "abstract": "<p>Already here</p>", "source": "openalex"}]
    a.fill_abstracts(rows, {"elsevier_api_key": "k"})
    assert [r.get("abstract_source") for r in rows] == ["scopus", "semantic_scholar", "crossref", None, "openalex"]
    assert rows[0]["abstract"] == "Scopus text." and rows[4]["abstract"] == "Already here"
    assert rows[3]["abstract_missing"] is True
    assert ("s2", ("10.1111/b", "10.1002/c", "10.1007/d")) in calls  # Elsevier skipped S2
    assert ("scopus", "10.1111/b") not in calls  # S2 already had it


def test_no_elsevier_key_uses_free_sources_only(monkeypatch):
    monkeypatch.setattr(a, "_semantic_scholar", lambda dois, timeout: {})
    monkeypatch.setattr(a, "_Scopus", lambda key: (_ for _ in ()).throw(AssertionError("scopus used")))
    monkeypatch.setattr(a, "_crossref", lambda doi, timeout: "")
    rows = [{"doi": "10.1016/a"}]
    a.fill_abstracts(rows, {})
    assert rows[0]["abstract_missing"] is True


def test_trim_authors():
    rows = [{"authors": ["A", "B", "C", "D", "E"]}, {"authors": ["A"]}]
    a.trim_authors(rows)
    assert rows[0] == {"authors": ["A", "B", "C"], "author_count": 5} and rows[1] == {"authors": ["A"]}


def test_dedupe_titles_merges_preprint_and_published():
    from litloop.search import _dedupe_titles
    rows = [{"title": "High-Entropy Fluorite Oxides for barrier coatings", "doi": "", "cited_by_count": 3},
            {"title": "High-entropy fluorite oxides for barrier coatings.", "doi": "10.1/x",
             "journal": "J", "cited_by_count": 650},
            {"title": "Short", "doi": "10.1/s"}, {"title": "Short", "doi": "10.1/t"}]
    out = _dedupe_titles(rows)
    assert len(out) == 3 and out[0]["doi"] == "10.1/x" and out[0]["cited_by_count"] == 650

def test_semantic_scholar_batches_are_chunked_and_odd_replies_ignored(monkeypatch):
    import litloop.abstracts as a
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
