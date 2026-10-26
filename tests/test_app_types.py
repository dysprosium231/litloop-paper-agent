"""The app's TypeScript types list every field the backend writes (code review, 2026-10-18)."""

from __future__ import annotations

import json
import re
import threading
from pathlib import Path

from litloop import config, download_jobs, error_text, server, store
from litloop.search import deep as deep_search, screening
from litloop.zotero import client as zotero

API_TS = Path(__file__).resolve().parents[1] / "app" / "src" / "lib" / "api.ts"


def _ts_fields(interface: str) -> set[str]:
    text = API_TS.read_text(encoding="utf-8")
    m = re.search(r"export interface\s+" + re.escape(interface) + r"\b.*?\{\n(.*?)\n\}", text, re.S)
    assert m, f"interface {interface} not found in api.ts"
    body = m.group(1)
    while re.search(r"\{[^{}]*\}", body, re.S):
        body = re.sub(r"\{[^{}]*\}", "", body, flags=re.S)
    return set(re.findall(r"^\s*(?:readonly\s+)?(\w+)\s*\??\s*:", body, re.M))


def test_summary_type_covers_the_deep_search_summary(monkeypatch):
    written = {}
    monkeypatch.setattr(store, "write_json", lambda path, data, **k: written.update(data))
    state = {"session": "s", "question": "q", "pool": {}, "seeds": [], "served": [], "rounds": [{"time": "t", "event": "start"}]}
    deep_search._write_summary(state, {}, {"stop_suggested": False, "stop_reason": ""}, [], [], 0, [])
    missing = set(written) - _ts_fields("Summary")
    assert not missing, f"api.ts Summary lacks {sorted(missing)}"


def test_download_item_type_covers_the_download_job_items(tmp_path, monkeypatch):
    monkeypatch.setattr(download_jobs, "jobs_dir", lambda: tmp_path)
    monkeypatch.setattr(config, "load_config", lambda: {"output_dir": str(tmp_path), "zotero_auto_push": False})

    class _SyncThread:
        def __init__(self, target, args=(), kwargs=None, **_):
            self.target = target
            self.args = args
            self.kwargs = kwargs or {}

        def start(self):
            self.target(*self.args, **self.kwargs)

    monkeypatch.setattr(threading, "Thread", _SyncThread)

    mock_batch = {
        "results": [{
            "doi": "10.1000/1",
            "success": True,
            "source": "crossref",
            "file": str(tmp_path / "paper.pdf"),
            "note": "chapter",
            "attempts": [{"source": "cr", "route": "free", "seconds": 1.5, "ok": True, "why": None}],
        }]
    }
    monkeypatch.setattr(server, "_batch_download_impl", lambda dois, batch_id, keep_attempts=False, **kw: json.dumps(mock_batch))
    monkeypatch.setattr(zotero, "ensure_collection", lambda col, cfg: {"success": True, "key": "k"})
    monkeypatch.setattr(zotero, "push_via_zotero_mcp", lambda doi, path, cfg, **k: {"success": True, "zotero_key": "zk", "pdf_attached": True})

    data = download_jobs.start([{"doi": "10.1000/1", "title": "T", "year": 2024, "journal": "J"}], "title")
    download_jobs.push_zotero(data["job"])

    saved = store.read_json(download_jobs.job_path(data["job"]))
    item = saved["items"][0]
    assert "attempts" in item, "attempts must be present in download item"
    missing = set(item.keys()) - _ts_fields("DownloadItem")
    assert not missing, f"api.ts DownloadItem lacks {sorted(missing)}"


def test_download_step_type_covers_steps_for_app():
    steps = error_text.steps_for_app([
        {"source": "crossref", "route": "free", "seconds": 1.2, "ok": False, "reason": "paywall"}
    ])
    assert steps and len(steps) == 1
    missing = set(steps[0].keys()) - _ts_fields("DownloadStep")
    assert not missing, f"api.ts DownloadStep lacks {sorted(missing)}"


def test_session_record_type_covers_screening_record(tmp_path, monkeypatch):
    written = {}
    monkeypatch.setattr(screening, "screening_dir", lambda: tmp_path)
    monkeypatch.setattr(store, "write_json", lambda path, data, **k: written.update(data))
    monkeypatch.setattr(store, "write_text", lambda path, text, **k: None)
    screening.save("q", [{"doi": "10.1000/1", "title": "t", "relevance": "高", "reason": "r"}], criteria="c", query="q")
    missing = set(written) - _ts_fields("SessionRecord")
    assert not missing, f"api.ts SessionRecord lacks {sorted(missing)}"

