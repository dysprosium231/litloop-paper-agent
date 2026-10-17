"""Short lists: Sci-Hub for every paper first, then a full single download for each miss."""

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
