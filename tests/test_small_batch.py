"""Short lists: open-access links, then Sci-Hub for every paper, then a full single download for each miss."""

import json
from pathlib import Path

import pytest

from litloop import pipeline, server
from litloop.pipeline import QueueEntry


@pytest.fixture
def calls(monkeypatch):
    seen = []
    import litloop.sources as sources

    def download(ident, out, **kw):
        seen.append((ident, kw.get("strategy"), kw.get("_institutional"), kw.get("use_vpnsci")))
        if kw.get("strategy") == "scihub_only":
            return {"success": ident == "10.1/in-scihub", "doi": ident, "source": "Sci-Hub"}
        return {"success": ident != "10.1/nowhere", "doi": ident, "source": "WebVPN"}
    monkeypatch.setattr(sources, "download", download)
    monkeypatch.setattr(pipeline, "grey_allowed", lambda cfg: True)
    monkeypatch.setattr(pipeline, "_enrich_oa_urls", lambda entries, cfg: None)  # no network
    return seen


ENTRIES = [QueueEntry(identifier=d) for d in ("10.1/in-scihub", "10.1/subscription", "10.1/nowhere")]


def test_scihub_round_then_single_downloads_for_the_misses(calls, tmp_path: Path):
    results = pipeline.run_scihub_then_single(ENTRIES, tmp_path, config={"vpnsci_enabled": True})
    first = [c for c in calls if c[1] == "scihub_only"]
    second = [c for c in calls if c[1] is None]
    assert sorted(c[0] for c in first) == ["10.1/in-scihub", "10.1/nowhere", "10.1/subscription"]
    assert all(c[2] is False for c in first)                       # round 1: Sci-Hub only
    assert sorted(c[0] for c in second) == ["10.1/nowhere", "10.1/subscription"]
    assert all(c[2] is True and c[3] is True for c in second)     # round 2: school routes, WebVPN on
    by = {r["doi"]: r for r in results}
    assert [r["doi"] for r in results] == ["10.1/in-scihub", "10.1/subscription", "10.1/nowhere"]  # input order
    assert by["10.1/in-scihub"]["source"] == "Sci-Hub" and by["10.1/subscription"]["success"]
    assert not by["10.1/nowhere"]["success"]


def test_gray_sources_off_means_no_scihub_round(calls, monkeypatch, tmp_path: Path):
    pipeline.run_scihub_then_single(ENTRIES, tmp_path, config={}, allow_grey=False)
    assert all(c[1] is None for c in calls) and len(calls) == 3


def test_batch_tool_routes_short_and_long_lists(monkeypatch, tmp_path: Path):
    used = []
    monkeypatch.setattr(pipeline, "run_scihub_then_single", lambda e, *a, **k: used.append(("short", len(e))) or [])
    monkeypatch.setattr(pipeline, "run_lanes", lambda e, *a, **k: used.append(("lanes", len(e))) or [])
    monkeypatch.setattr(server, "load_config", lambda: {"batch_small_max": 2, "output_dir": str(tmp_path)})
    server._batch_download_impl(["10.1000/a"], output_dir=str(tmp_path))
    server._batch_download_impl(["10.1000/a", "10.1000/b", "10.1000/c"], output_dir=str(tmp_path))
    assert used == [("short", 1), ("lanes", 3)]


def test_open_access_papers_are_fetched_before_scihub(calls, monkeypatch, tmp_path: Path):
    fast = []

    def lane(entries, out, cfg, **kw):
        fast.extend(e.identifier for e in entries)
        pdf = out / "oa.pdf"
        pdf.write_bytes(b"%PDF-1.4")
        return [{"success": True, "doi": e.identifier, "file": str(pdf), "source": "oa_url"} for e in entries]
    monkeypatch.setattr(pipeline, "_run_fast_lane", lane)
    entries = [QueueEntry(identifier="10.3390/oa"), QueueEntry(identifier="10.1/in-scihub")]
    results = pipeline.run_scihub_then_single(entries, tmp_path, config={"rename_format": "off"})
    assert fast == ["10.3390/oa"]                                   # MDPI: an open-access publisher
    assert [c[0] for c in calls] == ["10.1/in-scihub"]              # Sci-Hub never asked for it
    assert {r["doi"]: r["source"] for r in results} == {"10.3390/oa": "oa_url", "10.1/in-scihub": "Sci-Hub"}
    index = json.loads((tmp_path / ".doi_index.json").read_text(encoding="utf-8"))
    assert "10.3390/oa" in index                                    # the app sees the file


def test_cancel_stops_the_papers_not_yet_started(calls, monkeypatch, tmp_path: Path):
    import litloop.sources as sources
    real = sources.download

    def download(ident, out, **kw):
        r = real(ident, out, **kw)
        if ident == "10.1/subscription" and kw.get("strategy") is None:
            sources.cancel_downloads()  # the user cancels while this paper downloads
        return r
    monkeypatch.setattr(sources, "download", download)
    entries = [QueueEntry(identifier=d) for d in ("10.1/subscription", "10.1/nowhere")]
    results = pipeline.run_scihub_then_single(entries, tmp_path, config={}, allow_grey=False)
    assert [c[0] for c in calls] == ["10.1/subscription"]           # the next paper never started
    assert {r["doi"]: r.get("error_type") for r in results}["10.1/nowhere"] == "cancelled"
