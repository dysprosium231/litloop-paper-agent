"""Saved searches: a watch reports only papers it has not seen, and survives a restart."""

from __future__ import annotations

import json

import pytest

from litloop import config, db
from litloop.search import quick, watches


@pytest.fixture(autouse=True)
def tmp_home(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
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
    assert (tmp_path / "litloop.db").exists()
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


def test_check_due_only_runs_watches_older_than_the_configured_days(monkeypatch):
    from datetime import datetime, timedelta
    old = watches.add("旧课题", "old topic", [], [])
    fresh = watches.add("新课题", "new topic", [], [])
    old_ts = (datetime.now() - timedelta(days=30)).isoformat(timespec="seconds")
    fresh_ts = (datetime.now() - timedelta(days=1)).isoformat(timespec="seconds")
    db.update_watch_check(old["id"], seen_keys=[], checked=old_ts, last_new=0)
    db.update_watch_check(fresh["id"], seen_keys=[], checked=fresh_ts, last_new=0)
    seen: list[str] = []

    def fake_check(wid, config=None):
        seen.append(wid)
        return {"id": wid, "checked": "", "found": 2, "new": [{"doi": "10.1/x"}]}

    monkeypatch.setattr(watches, "check", fake_check)
    out = watches.check_due({"watch_check_days": 7})
    assert seen == [old["id"]]
    assert out["new_total"] == 1 and out["checked"][0]["question"] == "旧课题"


def test_legacy_watches_json_is_imported_idempotently_and_kept(tmp_path):
    legacy = tmp_path / "watches.json"
    legacy.write_text(
        json.dumps(
            {
                "schema": 1,
                "watches": [
                    {
                        "id": "legacy01",
                        "question": "旧追踪",
                        "query": "legacy aerogel",
                        "core_terms": ["aerogel"],
                        "session": "s1",
                        "created": "2026-10-01 10:00",
                        "checked": "2026-10-02 10:00",
                        "last_new": 2,
                        "seen": ["doi:10.1/a", "doi:10.1/b"],
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    first = watches.list_watches()
    assert len(first) == 1
    assert first[0]["id"] == "legacy01"
    assert first[0]["seen_count"] == 2
    assert legacy.exists()  # original file is never deleted

    # Calling again is idempotent and preserves existing DB state
    assert db.import_legacy_watches(data_dir=tmp_path) == 0
    second = watches.get("legacy01")
    assert second is not None
    assert second["seen"] == ["doi:10.1/a", "doi:10.1/b"]


def test_merge_into_session_scores_and_dedupes(monkeypatch):
    from litloop.agents import scorer
    from litloop.search import screening

    s = screening.save(
        "SiC/Al interface",
        [{"doi": "10.1/old", "title": "Existing paper", "relevance": "高", "reason": "seed"}],
        criteria=["focus on SiC/Al"],
        query="SiC Al",
        mode="quick",
    )
    sid = s["session"]
    w = watches.add("SiC/Al interface", "SiC Al", seen=[{"doi": "10.1/old"}], session=sid)

    scored_calls: list[list[dict]] = []

    def fake_score(question, criteria, papers, **kwargs):
        scored_calls.append(papers)
        return {
            "ratings": [{"n": 1, "relevance": "高", "reason": "直接研究界面反应"}],
            "usage": {},
        }

    monkeypatch.setattr(scorer, "score_papers", fake_score)
    cfg = {
        "builtin_agent_provider": "openai",
        "builtin_agent_model": "deepseek-chat",
        "builtin_agent_api_key": "sk-test",
    }

    res = watches.merge_into_session(
        w["id"],
        [
            {"doi": "10.1/old", "title": "Existing paper"},
            {"doi": "10.1/new1", "title": "New interface paper", "year": 2026, "journal": "Acta Mater"},
        ],
        score=True,
        config=cfg,
    )
    assert res["session"] == sid
    assert res["added"] == 1
    assert res["total"] == 2
    assert res["scored"] is True
    assert len(scored_calls) == 1 and len(scored_calls[0]) == 1

    # 未配置模型时回退为「中」直接并入
    w2 = watches.add("Fallback topic", "fallback", session=sid)
    res_fallback = watches.merge_into_session(
        w2["id"],
        [{"doi": "10.1/new2", "title": "Another paper"}],
        score=True,
        config={},
    )
    assert res_fallback["added"] == 1
    assert res_fallback["scored"] is False
    assert "未配置内置 Agent" in res_fallback["note"]

    # 全是已存在文献时返回 added=0
    res_dup = watches.merge_into_session(w["id"], [{"doi": "10.1/old", "title": "Existing paper"}], config={})
    assert res_dup["added"] == 0
