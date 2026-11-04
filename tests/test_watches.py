"""Saved searches: a watch reports only papers it has not seen, and survives a restart."""

from __future__ import annotations

import json

import pytest

from litloop.search import quick, watches


@pytest.fixture(autouse=True)
def tmp_home(tmp_path, monkeypatch):
    monkeypatch.setattr(watches, "path", lambda: tmp_path / "watches.json")
    return tmp_path


PAPERS = [
    {"doi": "10.1/OLD", "title": "Old one", "year": 2020},
    {"doi": "10.1/new", "title": "New one", "year": 2026},
]


def test_a_check_reports_only_papers_it_has_not_seen(monkeypatch):
    calls: list[dict] = []

    def fake_search(query, **kwargs):
        calls.append({"query": query, **kwargs})
        return PAPERS

    monkeypatch.setattr(quick, "search_papers_v2", fake_search)
    w = watches.add("陶瓷气凝胶", "ceramic aerogel 2024..2026", ["aerogel"], [{"doi": "10.1/old"}])
    first = watches.check(w["id"])
    assert [p["doi"] for p in first["new"]] == ["10.1/new"] and first["found"] == 2
    assert calls[0]["query"] == "ceramic aerogel 2024..2026" and calls[0]["sort"] == "date"
    second = watches.check(w["id"])
    assert second["new"] == []                                   # the same paper is not new twice


def test_the_watch_survives_a_restart(tmp_path, monkeypatch):
    monkeypatch.setattr(quick, "search_papers_v2", lambda *a, **k: [])
    watches.add("q", "some query")
    data = json.loads((tmp_path / "watches.json").read_text(encoding="utf-8"))
    assert data["schema"] == watches.store.SCHEMA and data["watches"][0]["query"] == "some query"
    assert watches.list_watches()[0]["query"] == "some query"


def test_listing_hides_the_long_seen_list():
    watches.add("q", "x", seen=[{"doi": f"10.1/{i}"} for i in range(5)])
    entry = watches.list_watches()[0]
    assert "seen" not in entry and entry["seen_count"] == 5


def test_an_empty_query_is_rejected():
    with pytest.raises(ValueError, match="检索式是空的"):
        watches.add("q", "   ")


def test_remove_and_unknown_ids():
    w = watches.add("q", "x")
    assert watches.remove(w["id"]) is True and watches.remove(w["id"]) is False
    with pytest.raises(ValueError, match="没有这个追踪"):
        watches.check("nope")


def test_papers_without_a_doi_are_tracked_by_title():
    assert watches.key({"title": "Same  Title", "doi": ""}) == "title:same title"
    assert watches.key({"doi": "10.1/ABC"}) == "doi:10.1/abc"
