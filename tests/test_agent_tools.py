"""The built-in agent's tools: thin wrappers with schemas the model can read."""

from __future__ import annotations

import pytest

from litloop.agents import tools


def test_search_trims_the_result_and_reports_filters(monkeypatch):
    calls = {}

    def fake(query, **kwargs):
        calls.update(query=query, **kwargs)
        kwargs["stats"].update({"retracted": 2})
        kwargs["expansions"].append({"term": "SEM", "ok": True})
        return [{"doi": "10.1/a", "title": "A", "year": 2020, "journal": "J", "cited_by_count": 5,
                 "abstract": "long text", "authors": ["X"], "is_oa": True}]

    monkeypatch.setattr(tools, "search_papers_v2", fake)
    out = tools.search("SiC", limit=99)
    assert calls["limit"] == 50  # capped, whatever the model asked for
    assert out["count"] == 1 and out["papers"] == [
        {"doi": "10.1/a", "title": "A", "year": 2020, "journal": "J", "cited_by_count": 5, "is_oa": True}]
    assert out["filtered"] == {"retracted": 2} and out["glossary"][0]["term"] == "SEM"


def test_no_filters_means_no_empty_noise(monkeypatch):
    monkeypatch.setattr(tools, "search_papers_v2", lambda q, **k: [])
    out = tools.search("x")
    assert out == {"query": "x", "count": 0, "papers": [], "filtered": None, "glossary": None}


def test_screening_save_checks_relevance_words(monkeypatch):
    saved = {}

    def fake_save(question, items, **kwargs):
        saved.update(question=question, items=items, **kwargs)
        return {"session": "s1", "added": 1, "total": 1, "by_relevance": {"高": 1}}

    monkeypatch.setattr(tools.screening, "save", fake_save)
    monkeypatch.setattr(tools.screening, "load", lambda session: {"question": "SiC/Al"})
    out = tools.screening_save("s1", [{"doi": "10.1/a", "relevance": "高", "reason": "对"},
                                     {"doi": "10.1/b", "relevance": "低", "title": "B", "year": 2020}])
    assert out == {"session": "s1", "added": 1, "total": 1, "by_relevance": {"高": 1}}
    assert saved["question"] == "SiC/Al" and saved["session"] == "s1"
    assert saved["items"][0] == {"doi": "10.1/a", "relevance": "高", "reason": "对"}
    assert saved["items"][1]["title"] == "B" and saved["items"][1]["year"] == 2020
    with pytest.raises(ValueError, match="评分不对"):
        tools.screening_save("s1", [{"doi": "10.1/a", "relevance": "interesting"}])


def test_a_quick_save_needs_a_question_but_can_create_the_session(monkeypatch):
    saved = {}

    def fake_save(question, items, **kwargs):
        saved.update(question=question, **kwargs)
        return {"session": "20261106-1-x", "added": 2, "total": 2, "by_relevance": {"高": 1, "低": 1}}

    monkeypatch.setattr(tools.screening, "save", fake_save)
    rows = [{"doi": "10.1/a", "relevance": "高", "title": "A"},
            {"doi": "10.1/b", "relevance": "低", "title": "B"}]
    with pytest.raises(ValueError, match="question"):
        tools.screening_save("", rows)
    out = tools.screening_save("", rows, question="SiC/Al 无压浸渗")
    assert out["session"] == "20261106-1-x"
    assert saved["question"] == "SiC/Al 无压浸渗" and saved["session"] is None  # a new session
    with pytest.raises(ValueError, match="没有评分"):
        tools.screening_save("s1", [], question="x")


def test_deep_search_tools_go_to_the_engine(monkeypatch):
    seen = {}
    monkeypatch.setattr(tools.deep, "start", lambda q, w, s, c, **k: seen.setdefault("start", (q, w, s, c, k)) or {})
    monkeypatch.setattr(tools.deep, "next_batch", lambda s, force=False: seen.setdefault("next", (s, force)) or {})
    monkeypatch.setattr(tools.deep, "status", lambda s: seen.setdefault("status", s) or {})
    tools.deep_search_start("Q", ["a"], ["10.1/s"], None, batch_size=15)
    tools.deep_search_next("s1", force=True)
    tools.deep_search_status("s1")
    assert seen["start"] == ("Q", ["a"], ["10.1/s"], [], {"criteria": None, "must_find": None, "batch_size": 15})
    assert seen["next"] == ("s1", True) and seen["status"] == "s1"


def test_every_schema_names_a_function_and_stays_short():
    names = [tool["name"] for tool in tools.TOOLS]
    assert set(names) == set(tools.FUNCTIONS)
    for tool in tools.TOOLS:
        assert len(tool["description"]) <= 200, tool["name"]
        assert tool["parameters"]["type"] == "object"
