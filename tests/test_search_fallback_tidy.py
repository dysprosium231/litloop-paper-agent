"""Without the discovery engine, results are still ranked, cleaned and deduplicated."""

from __future__ import annotations

import litloop.search as s

RAW = [
    {"title": "Unrelated work on bird migration", "abstract": "Birds fly south. " * 30, "doi": "10.1/a"},
    {"title": "Pressureless infiltration of SiC/Al composites", "abstract": "We study pressureless infiltration of SiC preforms by Al alloys. " * 10, "doi": "10.1/b"},
    {"title": "Pressureless Infiltration of SiC/Al Composites", "abstract": "", "doi": "10.1/b2"},
    {"title": "Acta Materialia", "journal": "Acta Materialia", "abstract": "", "doi": "10.1/c"},
]


def _run(monkeypatch, *, engine_raises: bool):
    import litloop.discovery as d
    if engine_raises:
        monkeypatch.setattr(d, "find_cli_available", lambda: True)
        monkeypatch.setattr(d, "search", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("engine down")))
    else:
        monkeypatch.setattr(d, "find_cli_available", lambda: False)
    monkeypatch.setattr(s, "search_papers", lambda *a, **k: [dict(r) for r in RAW])
    return s._collect("pressureless infiltration SiC Al", 10, 10, None, None, None, None, None)


def test_no_engine_results_are_tidied(monkeypatch):
    out = _run(monkeypatch, engine_raises=False)
    titles = [r["title"] for r in out]
    assert titles[0].lower().startswith("pressureless infiltration")
    assert "Acta Materialia" not in titles
    assert sum(t.lower().startswith("pressureless infiltration") for t in titles) == 1
    assert all("_tier" not in r for r in out)


def test_engine_failure_results_are_tidied(monkeypatch):
    out = _run(monkeypatch, engine_raises=True)
    assert out[0]["title"].lower().startswith("pressureless infiltration")
    assert "Acta Materialia" not in [r["title"] for r in out]
