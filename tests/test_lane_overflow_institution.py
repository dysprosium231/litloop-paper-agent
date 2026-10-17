"""Papers the fast and grey lanes miss go through the school's routes, as a single download does."""

from pathlib import Path

import pytest

from litloop import pipeline
from litloop.pipeline import QueueEntry


@pytest.fixture
def lanes(monkeypatch):
    asked = {"inst": [], "heal": []}
    monkeypatch.setattr(pipeline, "_enrich_oa_urls", lambda *a, **k: None)
    monkeypatch.setattr(pipeline, "_transient_retry", lambda *a, **k: None)
    monkeypatch.setattr(pipeline, "_fetch_si_for_results", lambda *a, **k: None)
    monkeypatch.setattr(pipeline, "grey_allowed", lambda cfg: True)
    monkeypatch.setattr(pipeline, "_run_fast_lane", lambda entries, *a, **k: [
        {"success": e.identifier == "10.3390/oa.1", "doi": e.identifier, "source": "oa"} for e in entries])

    import litloop.sources as sources
    monkeypatch.setattr(sources, "batch_download", lambda ids, *a, **k: {
        "results": [{"success": False, "doi": d, "identifier": d, "error": "not in library"} for d in ids]})
    monkeypatch.setattr(pipeline, "_normalize_engine_results", lambda raw: raw["results"])
    return asked, sources


ENTRIES = [QueueEntry(identifier="10.1016/sub.1", channel="elsevier"),   # fast lane misses
           QueueEntry(identifier="10.1111/sub.2"),                       # auto -> grey lane misses
           QueueEntry(identifier="10.3390/oa.1", channel="oa")]           # fast lane gets it


def test_misses_go_through_the_school_routes_one_by_one(lanes, monkeypatch, tmp_path: Path):
    asked, sources = lanes

    def school(doi, out, heal=True):
        asked["inst"].append(doi)
        asked["heal"].append(heal)
        return {"success": True, "doi": doi, "source": "WebVPN"}
    monkeypatch.setattr(sources, "institutional_download", school)
    results = pipeline.run_lanes(ENTRIES, tmp_path, config={})
    assert asked["inst"] == ["10.1016/sub.1", "10.1111/sub.2"]  # not the OA one it already has
    assert asked["heal"] == [True, False]  # sessions refreshed once
    last = {r["doi"]: r for r in results}
    assert last["10.1016/sub.1"]["source"] == "WebVPN" and last["10.1111/sub.2"]["success"]


def test_no_school_route_stops_after_the_first_answer(lanes, monkeypatch, tmp_path: Path):
    asked, sources = lanes
    monkeypatch.setattr(sources, "institutional_download", lambda doi, out, heal=True: asked["inst"].append(doi))
    pipeline.run_lanes(ENTRIES, tmp_path, config={})
    assert asked["inst"] == ["10.1016/sub.1"]  # None: nothing configured, the rest are not asked


def test_institution_off_means_off(lanes, monkeypatch, tmp_path: Path):
    asked, sources = lanes
    monkeypatch.setattr(sources, "institutional_download", lambda *a, **k: pytest.fail("asked"))
    pipeline.run_lanes(ENTRIES, tmp_path, config={}, allow_institution=False)
