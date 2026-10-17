"""Papers the fast and grey lanes miss go on to the school's route when there is one."""

from pathlib import Path

import pytest

from litloop import pipeline
from litloop.pipeline import QueueEntry


@pytest.fixture
def lanes(monkeypatch):
    asked = {}
    monkeypatch.setattr(pipeline, "_enrich_oa_urls", lambda *a, **k: None)
    monkeypatch.setattr(pipeline, "_transient_retry", lambda *a, **k: None)
    monkeypatch.setattr(pipeline, "_fetch_si_for_results", lambda *a, **k: None)
    monkeypatch.setattr(pipeline, "grey_allowed", lambda cfg: True)
    monkeypatch.setattr(pipeline, "_run_fast_lane", lambda entries, *a, **k: [
        {"success": e.identifier == "10.3390/oa.1", "doi": e.identifier, "source": "oa"} for e in entries])

    import litloop.sources as sources
    monkeypatch.setattr(sources, "batch_download", lambda ids, *a, **k: asked.setdefault("grey", list(ids)) and {
        "results": [{"success": False, "doi": d, "identifier": d, "error": "not in library"} for d in ids]})
    monkeypatch.setattr(pipeline, "_normalize_engine_results", lambda raw: raw["results"])

    class Fetcher:
        def __init__(self, cfg):
            pass

        def fetch_with_result(self, doi):
            asked.setdefault("inst", []).append(doi)

            class R:
                def to_dict(self):
                    return {"success": True, "doi": doi, "source": "WebVPN"}
            return R()

        def close(self):
            pass
    import litloop.institutional.fetcher as fetcher_mod
    import litloop.institutional.config_adapter as adapter_mod
    monkeypatch.setattr(fetcher_mod, "PaperFetcher", Fetcher)
    monkeypatch.setattr(adapter_mod.ConfigAdapter, "load", classmethod(lambda cls: type("C", (), {"_config": {}})()))
    return asked


ENTRIES = [QueueEntry(identifier="10.1016/sub.1", channel="elsevier"),   # fast lane misses (off the school's IPs)
           QueueEntry(identifier="10.1111/sub.2"),                       # auto -> grey lane misses
           QueueEntry(identifier="10.3390/oa.1", channel="oa")]           # fast lane gets it


def test_misses_reach_the_school_route(lanes, tmp_path: Path):
    results = pipeline.run_lanes(ENTRIES, tmp_path, config={"vpnsci_enabled": True})
    assert sorted(lanes["inst"]) == ["10.1016/sub.1", "10.1111/sub.2"]  # not the OA one it already has
    last = {r["doi"]: r for r in results}
    assert last["10.1016/sub.1"]["source"] == "WebVPN" and last["10.1111/sub.2"]["success"]


def test_without_a_school_route_nothing_more_is_tried(lanes, tmp_path: Path):
    pipeline.run_lanes(ENTRIES, tmp_path, config={})
    assert "inst" not in lanes
