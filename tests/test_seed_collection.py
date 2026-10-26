"""A Zotero collection as deep-search seeds (litloop_deep_search seed_collection)."""

import json

from litloop import server
from litloop.search import deep as deep_search
from litloop.zotero import client as zotero


def _capture_start(monkeypatch):
    seen = {}

    def fake_start(question, queries, seeds, core_terms, **kw):
        seen["seeds"] = seeds
        return {"session": "s1", "batch": []}

    monkeypatch.setattr(deep_search, "start", fake_start)
    return seen


def test_collection_dois_join_the_seeds(monkeypatch):
    seen = _capture_start(monkeypatch)
    monkeypatch.setattr(zotero, "collection_dois", lambda c, cfg: {
        "success": True, "key": "ABCD1234", "path": "A/SiC", "dois": ["10.1/a", "10.1/b"], "without_doi": 1})
    out = json.loads(server.litloop_deep_search(action="start", question="q", queries=["x"],
                                                seeds=["10.1/b", "10.1/c"], seed_collection="A/SiC"))
    assert seen["seeds"] == ["10.1/b", "10.1/c", "10.1/a"]  # merged, no duplicates, given seeds first
    assert out["seed_collection"] == {"path": "A/SiC", "seeds": 2, "without_doi": 1}


def test_unknown_collection_is_an_error_and_nothing_starts(monkeypatch):
    seen = _capture_start(monkeypatch)
    monkeypatch.setattr(zotero, "collection_dois", lambda c, cfg: {"success": False, "error": "no Zotero collection 'X'"})
    out = json.loads(server.litloop_deep_search(action="start", question="q", queries=["x"], seed_collection="X"))
    assert "seed_collection" in out["error"] and "seeds" not in seen


def test_without_collection_nothing_changes(monkeypatch):
    seen = _capture_start(monkeypatch)
    out = json.loads(server.litloop_deep_search(action="start", question="q", queries=["x"], seeds=["10.1/a"]))
    assert seen["seeds"] == ["10.1/a"] and "seed_collection" not in out


def test_collection_dois_resolves_path_key_and_unique_name(monkeypatch):
    cols = {"success": True, "collections": [
        {"key": "K1", "name": "SiC/Al", "path": "1 陶瓷/SiC/Al"},
        {"key": "K2", "name": "综述", "path": "1 陶瓷/综述"},
        {"key": "K3", "name": "综述", "path": "2 其他/综述"},
    ]}
    calls = []

    def fake_bridge(found, req, timeout):
        calls.append(req)
        if req["op"] == "collections":
            return cols
        return {"success": True, "items": [{"doi": "10.1/a"}, {"doi": ""}, {"doi": "10.1/a"}]}

    monkeypatch.setattr(zotero, "discover_zotero_mcp", lambda cfg: {"python": "x"})
    monkeypatch.setattr(zotero, "_run_bridge", fake_bridge)
    by_path = zotero.collection_dois("1 陶瓷/SiC/Al", {})
    assert by_path["key"] == "K1" and by_path["dois"] == ["10.1/a"] and by_path["without_doi"] == 1
    assert zotero.collection_dois("K2", {})["path"] == "1 陶瓷/综述"
    assert zotero.collection_dois("SiC/Al", {})["key"] == "K1"           # unique name
    assert zotero.collection_dois("综述", {})["success"] is False       # ambiguous name
