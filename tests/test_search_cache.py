"""7-day result caching for quick.search_papers_v2 with safe bypass rules."""

from __future__ import annotations

import os
import time
from pathlib import Path

from litloop import cache, config
from litloop.search import quick, scansci_find, watches
from litloop.terms import glossary


def _patch_quick(monkeypatch, *, results=None, error=None, expansion=None):
    calls: list[dict] = []

    def fake_collect(query, pool, limit, year_from, year_to, sort, author, author_id, expansions, errors):
        calls.append({"query": query, "sort": sort, "limit": limit})
        if expansions is not None and expansion is not None:
            expansions.append(expansion)
        if error:
            errors.append(error)
        return list(results or [])

    monkeypatch.setattr(quick, "_collect", fake_collect)
    monkeypatch.setattr(quick, "_finalize", lambda res, limit, **kw: (kw["stats"].update({"kept": len(res)}) if kw.get("stats") is not None else None) or res[:limit])
    monkeypatch.setattr(scansci_find, "find_cli_available", lambda: False)
    return calls


def test_quick_search_hits_cache_and_restores_side_effects(monkeypatch):
    calls = _patch_quick(
        monkeypatch,
        results=[{"doi": "10.1/a", "title": "SiC/Al interface", "year": 2024}],
        expansion={"term": "SiC", "with": ["silicon carbide"]},
    )

    exp1: list[dict] = []
    stats1: dict = {}
    out1 = quick.search_papers_v2("  SiC/Al   composite ", limit=10, expansions=exp1, stats=stats1)
    assert len(calls) == 1 and len(out1) == 1
    assert exp1 == [{"term": "SiC", "with": ["silicon carbide"]}]
    assert stats1.get("kept") == 1 and "cached" not in stats1

    # Case/whitespace normalization matches the same cache entry.
    exp2: list[dict] = []
    stats2: dict = {}
    out2 = quick.search_papers_v2("sic/al composite", limit=10, expansions=exp2, stats=stats2)
    assert len(calls) == 1
    assert out2 == out1
    assert exp2 == exp1
    assert stats2.get("kept") == 1 and stats2.get("cached") is True


def test_sort_date_and_use_cache_false_bypass_cache(monkeypatch):
    calls = _patch_quick(monkeypatch, results=[{"doi": "10.1/a", "title": "A"}])

    quick.search_papers_v2("aerogel", sort="date")
    quick.search_papers_v2("aerogel", sort="date")
    assert len(calls) == 2

    quick.search_papers_v2("aerogel", use_cache=False)
    quick.search_papers_v2("aerogel", use_cache=False)
    assert len(calls) == 4


def test_expired_ttl_and_zero_ttl_do_not_reuse_cache(monkeypatch):
    calls = _patch_quick(monkeypatch, results=[{"doi": "10.1/a", "title": "A"}])
    quick.search_papers_v2("ceramic", limit=5)
    assert len(calls) == 1

    cfg = config.load_config()
    files = list(Path(cfg["cache_dir"]).glob("search_*.json"))
    assert len(files) == 1
    old = time.time() - (169 * 3600)
    os.utime(files[0], (old, old))

    quick.search_papers_v2("ceramic", limit=5)
    assert len(calls) == 2

    config.update_config("search_cache_ttl_hours", 0)
    quick.search_papers_v2("ceramic", limit=5)
    assert len(calls) == 3


def test_empty_or_errored_results_are_not_cached(monkeypatch):
    calls_empty = _patch_quick(monkeypatch, results=[])
    quick.search_papers_v2("empty query")
    quick.search_papers_v2("empty query")
    assert len(calls_empty) == 2

    calls_err = _patch_quick(monkeypatch, results=[{"doi": "10.1/a", "title": "A"}], error="OpenAlex 429")
    quick.search_papers_v2("throttled query")
    quick.search_papers_v2("throttled query")
    assert len(calls_err) == 2


def test_config_key_or_glossary_change_invalidates_cache(monkeypatch, tmp_path):
    gp = tmp_path / "glossary.json"
    monkeypatch.setattr(glossary, "path", lambda: gp)
    calls = _patch_quick(monkeypatch, results=[{"doi": "10.1/a", "title": "A"}])

    quick.search_papers_v2("pdc")
    assert len(calls) == 1

    config.update_config("openalex_api_key", "oa_test_key")
    quick.search_papers_v2("pdc")
    assert len(calls) == 2

    gp.write_text('{"schema": 1, "entries": []}', encoding="utf-8")
    quick.search_papers_v2("pdc")
    assert len(calls) == 3

    cfg = config.load_config()
    assert cache.cache_clear(None, cfg) >= 1
    quick.search_papers_v2("pdc")
    assert len(calls) == 4


def test_watches_check_calls_quick_search_with_use_cache_false(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    seen: list[dict] = []

    def fake_search(query, **kwargs):
        seen.append({"query": query, **kwargs})
        return [{"doi": "10.1/new", "title": "New"}]

    monkeypatch.setattr(quick, "search_papers_v2", fake_search)
    w = watches.add("q", "ceramic 2026")
    out = watches.check(w["id"])
    assert len(out["new"]) == 1
    assert seen[0]["sort"] == "date" and seen[0]["use_cache"] is False
