"""Tests for download jobs and desktop app download endpoints."""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any

import pytest
import requests
from fastapi.testclient import TestClient

from litloop import app_server, config, download_jobs, progress_reporter, screening, server, store, zotero

TOKEN = "t0ken"


class FakeBatch:
    def __init__(self, tmp_path: Path):
        self.tmp_path = tmp_path
        self.block_event: threading.Event | None = None
        self.entered_event: threading.Event | None = None
        self.exception_to_raise: Exception | None = None

    def __call__(self, dois: list[str], batch_id: str | None = None) -> str:
        if self.entered_event:
            self.entered_event.set()
        if self.block_event:
            self.block_event.wait()
        if self.exception_to_raise:
            raise self.exception_to_raise

        results = []
        for doi in dois:
            if "fail" in doi.lower():
                progress_reporter.advance(False, current=doi)
                results.append({
                    "doi": doi,
                    "success": False,
                    "source": None,
                    "file": None,
                    "error": "not found",
                })
            else:
                progress_reporter.advance(True, current=doi)
                fake_pdf = self.tmp_path / "papers" / f"{doi.replace('/', '_')}.pdf"
                fake_pdf.parent.mkdir(parents=True, exist_ok=True)
                if not fake_pdf.exists():
                    fake_pdf.write_bytes(b"%PDF-1.4 test")
                results.append({
                    "doi": doi,
                    "success": True,
                    "source": "unpaywall",
                    "file": str(fake_pdf),
                    "error": None,
                })
        return json.dumps({"results": results})


@pytest.fixture
def fake_batch(tmp_path, monkeypatch):
    fake = FakeBatch(tmp_path)
    monkeypatch.setattr(server, "_batch_download_impl", fake)
    return fake


@pytest.fixture(autouse=True)
def setup_dirs_and_cleanup(tmp_path, monkeypatch, fake_batch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "config.json")
    monkeypatch.setattr(screening._config, "DATA_DIR", tmp_path)
    monkeypatch.setitem(config.DEFAULT_CONFIG, "output_dir", str(tmp_path / "papers"))

    if download_jobs._RUN_LOCK.locked():
        try:
            download_jobs._RUN_LOCK.release()
        except RuntimeError:
            pass
    if download_jobs._PUSH_LOCK.locked():
        try:
            download_jobs._PUSH_LOCK.release()
        except RuntimeError:
            pass
    download_jobs._CURRENT.update(job=None, cancelled=False)
    download_jobs._PUSHING["job"] = None
    progress_reporter._LISTENERS.clear()

    yield

    for t in threading.enumerate():
        if t.name.startswith("download-") or t.name.startswith("zotero-"):
            t.join(timeout=2.0)
    if download_jobs._RUN_LOCK.locked():
        try:
            download_jobs._RUN_LOCK.release()
        except RuntimeError:
            pass
    if download_jobs._PUSH_LOCK.locked():
        try:
            download_jobs._PUSH_LOCK.release()
        except RuntimeError:
            pass
    download_jobs._CURRENT.update(job=None, cancelled=False)
    download_jobs._PUSHING["job"] = None
    progress_reporter._LISTENERS.clear()


@pytest.fixture
def client():
    c = TestClient(app_server.create_app(TOKEN))
    c.headers["X-LitLoop-Token"] = TOKEN
    return c


def wait_job(job_id: str, timeout: float = 3.0) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        for t in threading.enumerate():
            if t.name == f"download-{job_id}":
                t.join(timeout=0.05)
                break
        data = download_jobs.get(job_id)
        if data.get("state") != "running":
            for t in threading.enumerate():
                if t.name == f"download-{job_id}":
                    t.join(timeout=0.5)
            return data
        time.sleep(0.02)
    raise TimeoutError(f"Job {job_id} did not finish within {timeout}s")


def wait_push(job_id: str, timeout: float = 3.0) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        for t in threading.enumerate():
            if t.name == f"zotero-{job_id}":
                t.join(timeout=0.05)
                break
        data = download_jobs.get(job_id)
        z = data.get("zotero") or {}
        if z.get("state") in ("done", "failed"):
            for t in threading.enumerate():
                if t.name == f"zotero-{job_id}":
                    t.join(timeout=0.5)
            return data
        time.sleep(0.02)
    raise TimeoutError(f"Zotero push for job {job_id} did not finish within {timeout}s")


def test_start_runs_in_thread_and_finishes(fake_batch, tmp_path):
    """(1) start() runs in a thread; after it finishes items are done/failed with source/file/error filled and state done."""
    papers = [
        {"doi": "10.1000/ok-paper", "title": "Good Paper", "year": 2020, "journal": "Nature"},
        {"doi": "10.1000/fail-paper", "title": "Failed Paper", "year": 2021, "journal": "Science"},
    ]
    job_info = download_jobs.start(papers, title="Test Job", session="session-1")
    job_id = job_info["job"]
    assert job_info["state"] == "running"
    assert job_info["title"] == "Test Job"
    assert job_info["session"] == "session-1"

    data = wait_job(job_id)
    assert data["state"] == "done"
    assert data["finished"] is not None
    assert data["error"] is None

    items = {it["doi"]: it for it in data["items"]}
    ok_item = items["10.1000/ok-paper"]
    assert ok_item["status"] == "done"
    assert ok_item["source"] == "unpaywall"
    assert ok_item["file"] is not None
    assert Path(ok_item["file"]).is_file()
    assert ok_item["error"] is None

    fail_item = items["10.1000/fail-paper"]
    assert fail_item["status"] == "failed"
    assert fail_item["source"] is None
    assert fail_item["file"] is None
    assert fail_item["error"] == "not found"


def test_busy_raises_and_api_returns_409(client, fake_batch):
    """(2) a second start() while one is running raises download_jobs.Busy and POST /api/downloads returns 409."""
    fake_batch.block_event = threading.Event()
    fake_batch.entered_event = threading.Event()

    papers = [{"doi": "10.1000/first", "title": "First"}]
    job_info = download_jobs.start(papers, title="First Job")
    job_id = job_info["job"]

    assert fake_batch.entered_event.wait(timeout=2.0)
    assert download_jobs.running() == job_id

    # Python direct call raises Busy
    with pytest.raises(download_jobs.Busy, match="another download is still running"):
        download_jobs.start([{"doi": "10.1000/second"}], title="Second Job")

    # API call returns 409
    resp = client.post("/api/downloads", json={
        "papers": [{"doi": "10.1000/third", "title": "Third"}],
        "title": "Third Job",
    })
    assert resp.status_code == 409
    assert "已有下载在进行" in resp.json()["detail"]

    # Unblock and verify completion
    fake_batch.block_event.set()
    wait_job(job_id)
    assert download_jobs.running() is None


def test_interrupted_job_reads_back(client, tmp_path):
    """(3) a job file left with state running that this process is not running reads back as 'interrupted'."""
    job_id = "20261017-090000"
    data = {
        "schema": store.SCHEMA,
        "job": job_id,
        "title": "Old Crashed Job",
        "session": None,
        "state": "running",
        "created": "2026-10-17 09:00:00",
        "finished": None,
        "output_dir": "",
        "error": None,
        "items": [{"doi": "10.1000/1", "title": "P1", "year": None, "journal": None,
                   "status": "waiting", "source": None, "file": None, "error": None}],
    }
    store.write_json(download_jobs.job_path(job_id), data)

    # get() reads back as interrupted
    got = download_jobs.get(job_id)
    assert got["state"] == "interrupted"

    # list_jobs() settles it too
    listed = download_jobs.list_jobs()
    assert len(listed) == 1
    assert listed[0]["job"] == job_id
    assert listed[0]["state"] == "interrupted"

    # API reads it as interrupted
    resp = client.get(f"/api/downloads/{job_id}")
    assert resp.status_code == 200
    assert resp.json()["state"] == "interrupted"

    list_resp = client.get("/api/downloads")
    assert list_resp.status_code == 200
    assert list_resp.json()["jobs"][0]["state"] == "interrupted"


def test_papers_deduped_missing_doi_dropped_and_empty_raises(client):
    """(4) papers without DOI are dropped, duplicates merged, all-empty raises ValueError (400 via API)."""
    papers = [
        {"doi": "10.1000/same", "title": "Same 1"},
        {"doi": "https://doi.org/10.1000/same", "title": "Same 2"},
        {"doi": "", "title": "Empty DOI"},
        {"doi": None, "title": "None DOI"},
        {"title": "Missing DOI field"},
        {"doi": "10.1000/distinct", "title": "Distinct"},
    ]
    job_info = download_jobs.start(papers, title="Dedup Test")
    job_id = job_info["job"]
    assert [it["doi"] for it in job_info["items"]] == ["10.1000/same", "10.1000/distinct"]
    wait_job(job_id)

    # all-empty raises ValueError in Python
    with pytest.raises(ValueError, match="no paper with a DOI to download"):
        download_jobs.start([{"doi": ""}, {"title": "Missing"}], title="All Empty")

    with pytest.raises(ValueError, match="no paper with a DOI to download"):
        download_jobs.start([], title="Empty list")

    # all-empty returns 400 via API
    resp_empty = client.post("/api/downloads", json={"papers": [], "title": "Empty"})
    assert resp_empty.status_code == 400
    assert "no paper with a DOI to download" in resp_empty.json()["detail"]

    resp_no_doi = client.post("/api/downloads", json={
        "papers": [{"doi": ""}, {"doi": "  "}],
        "title": "Blank DOIs",
    })
    assert resp_no_doi.status_code == 400
    assert "no paper with a DOI to download" in resp_no_doi.json()["detail"]


def test_downloads_pending_filters_selected_and_downloaded(client):
    """(5) GET /api/downloads/pending lists screening items with decision 'selected' minus DOIs a finished job already downloaded."""
    session_id = screening.save("Drug carriers review", [
        {"doi": "10.1000/sel1", "title": "Paper Sel 1", "relevance": "高"},
        {"doi": "10.1000/sel2", "title": "Paper Sel 2", "relevance": "高"},
        {"doi": "10.1000/rej", "title": "Paper Rejected", "relevance": "低"},
    ])["session"]

    screening.decide(session_id, selected=["10.1000/sel1", "10.1000/sel2"], rejected=["10.1000/rej"])

    # Initially both selected papers are pending
    resp = client.get("/api/downloads/pending")
    assert resp.status_code == 200
    pending = resp.json()
    assert len(pending) == 1
    assert pending[0]["session"] == session_id
    assert {p["doi"] for p in pending[0]["papers"]} == {"10.1000/sel1", "10.1000/sel2"}

    # A finished job downloaded 10.1000/sel1
    finished_job_data = {
        "schema": store.SCHEMA,
        "job": "20261017-100000",
        "title": "Finished Job",
        "session": session_id,
        "state": "done",
        "created": "2026-10-17 10:00:00",
        "finished": "2026-10-17 10:01:00",
        "output_dir": "",
        "error": None,
        "items": [
            {"doi": "10.1000/sel1", "title": "Paper Sel 1", "year": None, "journal": None,
             "status": "done", "source": "unpaywall", "file": "p1.pdf", "error": None},
        ],
    }
    store.write_json(download_jobs.job_path("20261017-100000"), finished_job_data)

    # Now only sel2 is pending
    resp2 = client.get("/api/downloads/pending")
    assert resp2.status_code == 200
    pending2 = resp2.json()
    assert len(pending2) == 1
    assert [p["doi"] for p in pending2[0]["papers"]] == ["10.1000/sel2"]

    # When all selected papers are downloaded, the session is omitted
    finished_job_data["items"].append({
        "doi": "10.1000/sel2", "title": "Paper Sel 2", "year": None, "journal": None,
        "status": "done", "source": "unpaywall", "file": "p2.pdf", "error": None,
    })
    store.write_json(download_jobs.job_path("20261017-100000"), finished_job_data)

    resp3 = client.get("/api/downloads/pending")
    assert resp3.status_code == 200
    assert resp3.json() == []


def test_api_open_unrecorded_and_recorded(client, monkeypatch, tmp_path):
    """(6) POST /api/open returns 403 for a path no job recorded and 200 for a recorded existing .pdf file."""
    opened_paths: list[Any] = []
    monkeypatch.setattr(app_server, "_open_with_system", lambda p: opened_paths.append(p))

    # Existing file on disk, but not recorded in any job -> 403
    unrecorded = tmp_path / "papers" / "unrecorded.pdf"
    unrecorded.parent.mkdir(parents=True, exist_ok=True)
    unrecorded.write_bytes(b"%PDF-1.4 unrecorded")

    resp_unrec = client.post("/api/open", json={"path": str(unrecorded)})
    assert resp_unrec.status_code == 403
    assert "only downloaded PDFs and their folders can be opened" in resp_unrec.json()["detail"]
    assert not opened_paths

    # Non-existent file -> 403
    resp_nonexistent = client.post("/api/open", json={"path": str(tmp_path / "nonexistent.pdf")})
    assert resp_nonexistent.status_code == 403

    # Non-pdf file -> 403
    text_file = tmp_path / "papers" / "notes.txt"
    text_file.write_text("notes")
    resp_txt = client.post("/api/open", json={"path": str(text_file)})
    assert resp_txt.status_code == 403

    # Now create a job recording a pdf file and output_dir
    recorded_pdf = tmp_path / "papers" / "recorded.pdf"
    recorded_pdf.write_bytes(b"%PDF-1.4 recorded")
    output_dir = tmp_path / "papers"

    job_data = {
        "schema": store.SCHEMA,
        "job": "20261017-100500",
        "title": "Open Test Job",
        "session": None,
        "state": "done",
        "created": "2026-10-17 10:05:00",
        "finished": "2026-10-17 10:06:00",
        "output_dir": str(output_dir),
        "error": None,
        "items": [
            {"doi": "10.1000/rec", "title": "Recorded", "year": None, "journal": None,
             "status": "done", "source": "unpaywall", "file": str(recorded_pdf), "error": None},
        ],
    }
    store.write_json(download_jobs.job_path("20261017-100500"), job_data)

    # Recorded existing .pdf -> 200
    resp_ok = client.post("/api/open", json={"path": str(recorded_pdf)})
    assert resp_ok.status_code == 200
    assert resp_ok.json() == {"opened": str(recorded_pdf.resolve())}
    assert opened_paths == [recorded_pdf.resolve()]

    # Opening recorded folder -> 200
    resp_dir = client.post("/api/open", json={"path": str(output_dir)})
    assert resp_dir.status_code == 200
    assert resp_dir.json() == {"opened": str(output_dir.resolve())}


def test_cancel_returns_false_when_nothing_runs(client):
    """(7) cancel() returns False when nothing runs."""
    assert download_jobs.cancel() is False

    resp = client.post("/api/downloads/cancel")
    assert resp.status_code == 200
    assert resp.json() == {"cancelled": False}


def test_cancel_running_job(client, fake_batch):
    """cancel() stops a running job and marks state as cancelled."""
    fake_batch.block_event = threading.Event()
    fake_batch.entered_event = threading.Event()

    job_info = download_jobs.start([{"doi": "10.1000/cancel-target"}], title="Job to Cancel")
    job_id = job_info["job"]

    assert fake_batch.entered_event.wait(timeout=2.0)
    assert download_jobs.running() == job_id

    resp = client.post("/api/downloads/cancel")
    assert resp.status_code == 200
    assert resp.json() == {"cancelled": True}

    fake_batch.block_event.set()
    data = wait_job(job_id)
    assert data["state"] == "cancelled"


def test_api_downloads_endpoints(client):
    """Test /api/downloads list and single job endpoints."""
    # Bad job id format returns 400
    assert client.get("/api/downloads/bad%20id!").status_code == 400
    assert client.get("/api/downloads/no-such-job").status_code == 404

    # List when empty
    resp = client.get("/api/downloads")
    assert resp.status_code == 200
    assert resp.json() == {"jobs": [], "running": None, "live": None, "pushing": None}


def test_job_failure_on_engine_exception(fake_batch):
    """When _batch_download_impl raises, job state becomes failed."""
    fake_batch.exception_to_raise = RuntimeError("Engine crash")

    job_info = download_jobs.start([{"doi": "10.1000/crash"}], title="Crash Job")
    data = wait_job(job_info["job"])
    assert data["state"] == "failed"
    assert "RuntimeError: Engine crash" in (data.get("error") or "")


def test_default_collection(tmp_path, monkeypatch):
    """(1) default_collection(title) gives "<root>/<title>", replaces "/", collapses whitespace,
    shortens >40 chars with "…", and uses "LitLoop" when empty/unset."""
    # Unset config uses "LitLoop"
    assert download_jobs.default_collection("Simple Title") == "LitLoop/Simple Title"

    # Empty config string uses "LitLoop"
    cfg_file = tmp_path / "config.json"
    cfg_file.write_text(json.dumps({"zotero_collection_root": ""}), encoding="utf-8")
    assert download_jobs.default_collection("Simple Title") == "LitLoop/Simple Title"

    # Custom root with whitespace / slashes stripped
    cfg_file.write_text(json.dumps({"zotero_collection_root": " /MyLibrary/Sub/ "}), encoding="utf-8")
    assert download_jobs.default_collection("Simple Title") == "MyLibrary/Sub/Simple Title"

    # Replaces "/" in title with "-"
    assert download_jobs.default_collection("COVID-19/SARS-CoV-2/Review") == "MyLibrary/Sub/COVID-19-SARS-CoV-2-Review"

    # Collapses whitespace in title
    assert download_jobs.default_collection("  Paper   with \t\n  lots   of   spaces  ") == "MyLibrary/Sub/Paper with lots of spaces"

    # Exact 40 chars is not shortened
    title40 = "A" * 40
    assert download_jobs.default_collection(title40) == f"MyLibrary/Sub/{title40}"

    # Over 40 chars shortened to 39 chars + "…"
    title45 = "B" * 45
    expected = "B" * 39 + "…"
    assert len(expected) == 40
    assert download_jobs.default_collection(title45) == f"MyLibrary/Sub/{expected}"


def test_push_zotero_items_status_and_skips_on_second_push(monkeypatch, tmp_path):
    """(2) push_zotero pushes only done items with file, records added/reused/no_pdf/failed,
    sets state done with collection and key, and skips added/reused on second push."""
    papers_dir = tmp_path / "papers"
    papers_dir.mkdir(parents=True, exist_ok=True)
    f1 = papers_dir / "p1.pdf"
    f2 = papers_dir / "p2.pdf"
    f3 = papers_dir / "p3.pdf"
    f4 = papers_dir / "p4.pdf"
    for f in (f1, f2, f3, f4):
        f.write_bytes(b"%PDF-1.4")

    job_id = "20261017-120000"
    job_data = {
        "schema": store.SCHEMA,
        "job": job_id,
        "title": "Zotero Test Job",
        "session": None,
        "state": "done",
        "created": "2026-10-17 12:00:00",
        "finished": "2026-10-17 12:05:00",
        "output_dir": str(papers_dir),
        "error": None,
        "items": [
            {"doi": "10.1000/1", "title": "Added", "year": None, "journal": None,
             "status": "done", "source": "unpaywall", "file": str(f1), "error": None},
            {"doi": "10.1000/2", "title": "Reused", "year": None, "journal": None,
             "status": "done", "source": "unpaywall", "file": str(f2), "error": None},
            {"doi": "10.1000/3", "title": "No PDF", "year": None, "journal": None,
             "status": "done", "source": "unpaywall", "file": str(f3), "error": None},
            {"doi": "10.1000/4", "title": "Failed", "year": None, "journal": None,
             "status": "done", "source": "unpaywall", "file": str(f4), "error": None},
            {"doi": "10.1000/5", "title": "Download Failed", "year": None, "journal": None,
             "status": "failed", "source": None, "file": None, "error": "not found"},
            {"doi": "10.1000/6", "title": "No File", "year": None, "journal": None,
             "status": "done", "source": "unpaywall", "file": None, "error": None},
        ],
    }
    store.write_json(download_jobs.job_path(job_id), job_data)

    monkeypatch.setattr(zotero, "ensure_collection",
                        lambda col, cfg: {"success": True, "key": "COL_KEY_1", "path": col})

    pushed: list[dict[str, Any]] = []

    def fake_push(identifier, pdf_path, cfg, collections=None, tags=None):
        pushed.append({"doi": identifier, "file": pdf_path, "collections": collections, "tags": tags})
        if identifier == "10.1000/1":
            return {"success": True, "zotero_key": "ZK1", "pdf_attached": True, "reused": False}
        elif identifier == "10.1000/2":
            return {"success": True, "zotero_key": "ZK2", "pdf_attached": True, "reused": True}
        elif identifier == "10.1000/3":
            return {"success": True, "zotero_key": "ZK3", "pdf_attached": False, "attach_message": "too large"}
        elif identifier == "10.1000/4":
            return {"success": False, "error": "bridge error"}
        raise AssertionError(f"Unexpected doi pushed: {identifier}")

    monkeypatch.setattr(zotero, "push_via_zotero_mcp", fake_push)

    # First push
    res = download_jobs.push_zotero(job_id, collection="Custom/Coll", tags=["tag1", "tag2"])
    assert res["zotero"]["state"] == "running"

    data = wait_push(job_id)
    assert data["zotero"]["state"] == "done"
    assert data["zotero"]["collection"] == "Custom/Coll"
    assert data["zotero"]["key"] == "COL_KEY_1"
    assert data["zotero"]["finished"] is not None

    items_by_doi = {i["doi"]: i for i in data["items"]}
    assert items_by_doi["10.1000/1"]["zotero"] == {"status": "added", "key": "ZK1", "error": None}
    assert items_by_doi["10.1000/2"]["zotero"] == {"status": "reused", "key": "ZK2", "error": None}
    assert items_by_doi["10.1000/3"]["zotero"] == {"status": "no_pdf", "key": "ZK3", "error": "too large"}
    assert items_by_doi["10.1000/4"]["zotero"] == {"status": "failed", "key": None, "error": "bridge error"}
    assert "zotero" not in items_by_doi["10.1000/5"]
    assert "zotero" not in items_by_doi["10.1000/6"]

    assert len(pushed) == 4
    for p in pushed:
        assert p["collections"] == ["COL_KEY_1"]
        assert p["tags"] == ["tag1", "tag2"]

    # Second push: 10.1000/1 and 10.1000/2 are already added/reused -> skipped!
    pushed.clear()
    download_jobs.push_zotero(job_id)
    data2 = wait_push(job_id)
    assert [p["doi"] for p in pushed] == ["10.1000/3", "10.1000/4"]


def test_push_zotero_nothing_waits_raises_and_api_400(client, tmp_path):
    """(3a) push_zotero raises ValueError when nothing waits; API returns 400."""
    job_id = "20261017-121000"
    job_data = {
        "schema": store.SCHEMA,
        "job": job_id,
        "title": "Nothing Waits Job",
        "session": None,
        "state": "done",
        "created": "2026-10-17 12:10:00",
        "finished": "2026-10-17 12:11:00",
        "output_dir": "",
        "error": None,
        "items": [
            {"doi": "10.1000/fail", "title": "F", "year": None, "journal": None,
             "status": "failed", "source": None, "file": None, "error": "fail"},
            {"doi": "10.1000/nofile", "title": "NF", "year": None, "journal": None,
             "status": "done", "source": "oa", "file": None, "error": None},
        ],
    }
    store.write_json(download_jobs.job_path(job_id), job_data)

    with pytest.raises(ValueError, match="nothing to push"):
        download_jobs.push_zotero(job_id)

    resp = client.post(f"/api/downloads/{job_id}/zotero", json={})
    assert resp.status_code == 400
    assert "nothing to push" in resp.json()["detail"]


def test_push_zotero_busy_raises_and_api_409(client, monkeypatch, tmp_path):
    """(3b) push_zotero raises Busy when a push is already running; API returns 409."""
    f = tmp_path / "paper.pdf"
    f.write_bytes(b"%PDF-1.4")

    job_id = "20261017-122000"
    job_data = {
        "schema": store.SCHEMA,
        "job": job_id,
        "title": "Busy Push Job",
        "session": None,
        "state": "done",
        "created": "2026-10-17 12:20:00",
        "finished": "2026-10-17 12:21:00",
        "output_dir": "",
        "error": None,
        "items": [
            {"doi": "10.1000/busy", "title": "Busy Paper", "year": None, "journal": None,
             "status": "done", "source": "unpaywall", "file": str(f), "error": None},
        ],
    }
    store.write_json(download_jobs.job_path(job_id), job_data)

    block_event = threading.Event()
    entered_event = threading.Event()

    def fake_ensure(col, cfg):
        entered_event.set()
        block_event.wait(timeout=5.0)
        return {"success": True, "key": "KEY_BUSY", "path": col}

    monkeypatch.setattr(zotero, "ensure_collection", fake_ensure)
    monkeypatch.setattr(zotero, "push_via_zotero_mcp",
                        lambda *a, **k: {"success": True, "zotero_key": "K", "pdf_attached": True})

    download_jobs.push_zotero(job_id)
    assert entered_event.wait(timeout=2.0)
    assert download_jobs.pushing() == job_id

    # Python direct call raises Busy
    with pytest.raises(download_jobs.Busy, match="another Zotero push is still running"):
        download_jobs.push_zotero(job_id)

    # API returns 409
    resp = client.post(f"/api/downloads/{job_id}/zotero", json={})
    assert resp.status_code == 409
    assert "已有推送在进行" in resp.json()["detail"]

    block_event.set()
    wait_push(job_id)
    assert download_jobs.pushing() is None


def test_push_zotero_ensure_collection_failed(monkeypatch, tmp_path):
    """(4) ensure_collection failing sets zotero state failed with its error."""
    f = tmp_path / "paper.pdf"
    f.write_bytes(b"%PDF-1.4")

    job_id = "20261017-123000"
    job_data = {
        "schema": store.SCHEMA,
        "job": job_id,
        "title": "Ensure Fail Job",
        "session": None,
        "state": "done",
        "created": "2026-10-17 12:30:00",
        "finished": "2026-10-17 12:31:00",
        "output_dir": "",
        "error": None,
        "items": [
            {"doi": "10.1000/efail", "title": "P", "year": None, "journal": None,
             "status": "done", "source": "oa", "file": str(f), "error": None},
        ],
    }
    store.write_json(download_jobs.job_path(job_id), job_data)

    monkeypatch.setattr(zotero, "ensure_collection",
                        lambda col, cfg: {"success": False, "error": "Zotero collection forbidden"})

    download_jobs.push_zotero(job_id)
    data = wait_push(job_id)
    assert data["zotero"]["state"] == "failed"
    assert data["zotero"]["error"] == "Zotero collection forbidden"
    assert download_jobs.pushing() is None


def test_interrupted_zotero_job_reads_back(client, tmp_path):
    """(5) a job file whose zotero.state is running while no push runs reads back
    as interrupted and its 'pushing' items as failed."""
    job_id = "20261017-124000"
    job_data = {
        "schema": store.SCHEMA,
        "job": job_id,
        "title": "Interrupted Zotero Job",
        "session": None,
        "state": "done",
        "created": "2026-10-17 12:40:00",
        "finished": "2026-10-17 12:41:00",
        "output_dir": "",
        "error": None,
        "items": [
            {"doi": "10.1000/already-done", "title": "P1", "year": None, "journal": None,
             "status": "done", "source": "unpaywall", "file": "p1.pdf", "error": None,
             "zotero": {"status": "added", "key": "K1", "error": None}},
            {"doi": "10.1000/was-pushing", "title": "P2", "year": None, "journal": None,
             "status": "done", "source": "unpaywall", "file": "p2.pdf", "error": None,
             "zotero": {"status": "pushing", "key": None, "error": None}},
        ],
        "zotero": {
            "state": "running",
            "collection": "LitLoop/Test",
            "key": "COL1",
            "started": "2026-10-17 12:42:00",
            "finished": None,
            "error": None,
        },
    }
    store.write_json(download_jobs.job_path(job_id), job_data)

    got = download_jobs.get(job_id)
    assert got["zotero"]["state"] == "interrupted"
    items = {i["doi"]: i for i in got["items"]}
    assert items["10.1000/already-done"]["zotero"]["status"] == "added"
    assert items["10.1000/was-pushing"]["zotero"]["status"] == "failed"

    # API also sees it as interrupted
    resp = client.get(f"/api/downloads/{job_id}")
    assert resp.status_code == 200
    api_data = resp.json()
    assert api_data["zotero"]["state"] == "interrupted"
    api_items = {i["doi"]: i for i in api_data["items"]}
    assert api_items["10.1000/was-pushing"]["zotero"]["status"] == "failed"


def test_auto_push_triggers_on_finished_download_job(fake_batch, monkeypatch, tmp_path):
    """(6) with config zotero_auto_push true a finished download job triggers a push automatically."""
    monkeypatch.setitem(config.DEFAULT_CONFIG, "zotero_auto_push", True)

    monkeypatch.setattr(zotero, "ensure_collection",
                        lambda col, cfg: {"success": True, "key": "AUTO_COL_KEY", "path": col})

    pushed_dois: list[str] = []

    def fake_push(doi, path, cfg, collections=None, tags=None):
        pushed_dois.append(doi)
        return {"success": True, "zotero_key": f"KEY_{doi}", "pdf_attached": True, "reused": False}

    monkeypatch.setattr(zotero, "push_via_zotero_mcp", fake_push)

    papers = [
        {"doi": "10.1000/auto-ok", "title": "Auto OK"},
        {"doi": "10.1000/fail-paper", "title": "Auto Fail"},
    ]
    job_info = download_jobs.start(papers, title="Auto Push Job")
    job_id = job_info["job"]

    wait_job(job_id)
    data = wait_push(job_id)

    assert data["state"] == "done"
    assert data["zotero"]["state"] == "done"
    assert data["zotero"]["key"] == "AUTO_COL_KEY"
    assert pushed_dois == ["10.1000/auto-ok"]

    items = {i["doi"]: i for i in data["items"]}
    assert items["10.1000/auto-ok"]["zotero"]["status"] == "added"
    assert "zotero" not in items["10.1000/fail-paper"]


def test_api_download_job_includes_zotero_default(client, tmp_path):
    """(7a) GET /api/downloads/{job} includes zotero_default."""
    job_id = "20261017-125000"
    job_data = {
        "schema": store.SCHEMA,
        "job": job_id,
        "title": "Machine Learning in Medicine",
        "session": None,
        "state": "done",
        "created": "2026-10-17 12:50:00",
        "finished": "2026-10-17 12:51:00",
        "output_dir": "",
        "error": None,
        "items": [],
    }
    store.write_json(download_jobs.job_path(job_id), job_data)

    resp = client.get(f"/api/downloads/{job_id}")
    assert resp.status_code == 200
    assert resp.json()["zotero_default"] == "LitLoop/Machine Learning in Medicine"


def test_api_zotero_collections_endpoint(client, monkeypatch):
    """(7b) GET /api/zotero/collections returns list from zotero_collections, and 503 on failure."""
    # Success
    cols = [{"key": "C1", "name": "LitLoop", "parent": "", "path": "LitLoop"}]
    monkeypatch.setattr(zotero, "zotero_collections", lambda cfg: {"success": True, "collections": cols})

    resp = client.get("/api/zotero/collections")
    assert resp.status_code == 200
    assert resp.json() == cols

    # Failure with error message
    monkeypatch.setattr(zotero, "zotero_collections",
                        lambda cfg: {"success": False, "error": "zotero-mcp not installed"})
    resp_fail = client.get("/api/zotero/collections")
    assert resp_fail.status_code == 503
    assert resp_fail.json()["detail"] == "zotero-mcp not installed"

    # Failure without error message
    monkeypatch.setattr(zotero, "zotero_collections", lambda cfg: {"success": False})
    resp_fail2 = client.get("/api/zotero/collections")
    assert resp_fail2.status_code == 503
    assert resp_fail2.json()["detail"] == "Zotero is not reachable"


def test_zotero_pending():
    """zotero_pending lists, per job, papers with status done and a file whose
    zotero status is not added/reused (includes never pushed, no_pdf, failed),
    with the job's pushed collection or default_collection(title); jobs with
    nothing pending are left out."""
    # Job 1: Has various items, no zotero block -> uses default_collection(title)
    job1_id = "20261017-100000"
    job1_data = {
        "schema": store.SCHEMA,
        "job": job1_id,
        "title": "Job Pending Default",
        "session": None,
        "state": "done",
        "created": "2026-10-17 10:00:00",
        "finished": "2026-10-17 10:05:00",
        "output_dir": "",
        "error": None,
        "items": [
            # never pushed (no zotero key) -> pending
            {"doi": "10.1000/never-pushed", "title": "Paper 1", "year": 2021, "journal": "Nature",
             "status": "done", "source": "unpaywall", "file": "p1.pdf", "error": None},
            # failed push -> pending
            {"doi": "10.1000/failed-push", "title": "Paper 2", "year": 2022, "journal": "Science",
             "status": "done", "source": "unpaywall", "file": "p2.pdf", "error": None,
             "zotero": {"status": "failed", "key": None, "error": "bridge error"}},
            # no_pdf push -> pending
            {"doi": "10.1000/no-pdf-push", "title": "Paper 3", "year": 2023, "journal": "Cell",
             "status": "done", "source": "unpaywall", "file": "p3.pdf", "error": None,
             "zotero": {"status": "no_pdf", "key": "ZK3", "error": "too large"}},
            # added -> NOT pending
            {"doi": "10.1000/added-push", "title": "Paper 4", "year": 2024, "journal": "Lancet",
             "status": "done", "source": "unpaywall", "file": "p4.pdf", "error": None,
             "zotero": {"status": "added", "key": "ZK4", "error": None}},
            # reused -> NOT pending
            {"doi": "10.1000/reused-push", "title": "Paper 5", "year": 2025, "journal": "JAMA",
             "status": "done", "source": "unpaywall", "file": "p5.pdf", "error": None,
             "zotero": {"status": "reused", "key": "ZK5", "error": None}},
            # download failed -> NOT pending (status != done)
            {"doi": "10.1000/download-failed", "title": "Paper 6", "year": 2026, "journal": "NEJM",
             "status": "failed", "source": None, "file": None, "error": "not found"},
            # done but no file -> NOT pending
            {"doi": "10.1000/no-file", "title": "Paper 7", "year": 2027, "journal": "BMJ",
             "status": "done", "source": "oa", "file": None, "error": None},
        ],
    }
    store.write_json(download_jobs.job_path(job1_id), job1_data)

    # Job 2: Has custom collection in zotero block -> uses pushed collection
    job2_id = "20261017-090000"
    job2_data = {
        "schema": store.SCHEMA,
        "job": job2_id,
        "title": "Job Pending Pushed Coll",
        "session": None,
        "state": "done",
        "created": "2026-10-17 09:00:00",
        "finished": "2026-10-17 09:05:00",
        "output_dir": "",
        "error": None,
        "zotero": {
            "state": "done",
            "collection": "Custom/PushedColl",
            "key": "COL_KEY",
            "started": "2026-10-17 09:05:00",
            "finished": "2026-10-17 09:06:00",
            "error": None,
        },
        "items": [
            {"doi": "10.1000/j2-pending", "title": "J2 Paper", "year": 2020, "journal": "PNAS",
             "status": "done", "source": "unpaywall", "file": "j2.pdf", "error": None,
             "zotero": {"status": "failed", "key": None, "error": "timeout"}},
        ],
    }
    store.write_json(download_jobs.job_path(job2_id), job2_data)

    # Job 3: All downloaded papers already added/reused -> left out
    job3_id = "20261017-080000"
    job3_data = {
        "schema": store.SCHEMA,
        "job": job3_id,
        "title": "All Added Job",
        "session": None,
        "state": "done",
        "created": "2026-10-17 08:00:00",
        "finished": "2026-10-17 08:05:00",
        "output_dir": "",
        "error": None,
        "items": [
            {"doi": "10.1000/j3-added", "title": "J3 Paper", "year": 2019, "journal": "PLOS",
             "status": "done", "source": "unpaywall", "file": "j3.pdf", "error": None,
             "zotero": {"status": "added", "key": "K3", "error": None}},
        ],
    }
    store.write_json(download_jobs.job_path(job3_id), job3_data)

    # Job 4: No items done with file -> left out
    job4_id = "20261017-070000"
    job4_data = {
        "schema": store.SCHEMA,
        "job": job4_id,
        "title": "All Failed Job",
        "session": None,
        "state": "done",
        "created": "2026-10-17 07:00:00",
        "finished": "2026-10-17 07:05:00",
        "output_dir": "",
        "error": None,
        "items": [
            {"doi": "10.1000/j4-fail", "title": "J4 Paper", "year": 2018, "journal": None,
             "status": "failed", "source": None, "file": None, "error": "not found"},
        ],
    }
    store.write_json(download_jobs.job_path(job4_id), job4_data)

    pending = download_jobs.zotero_pending()
    assert len(pending) == 2

    # Job 1 is first (newest by id)
    assert pending[0]["job"] == job1_id
    assert pending[0]["title"] == "Job Pending Default"
    assert pending[0]["created"] == "2026-10-17 10:00:00"
    assert pending[0]["collection"] == "LitLoop/Job Pending Default"
    assert pending[0]["papers"] == [
        {"doi": "10.1000/never-pushed", "title": "Paper 1", "year": 2021, "journal": "Nature", "zotero": None},
        {"doi": "10.1000/failed-push", "title": "Paper 2", "year": 2022, "journal": "Science", "zotero": "failed"},
        {"doi": "10.1000/no-pdf-push", "title": "Paper 3", "year": 2023, "journal": "Cell", "zotero": "no_pdf"},
    ]

    # Job 2 is second
    assert pending[1]["job"] == job2_id
    assert pending[1]["title"] == "Job Pending Pushed Coll"
    assert pending[1]["created"] == "2026-10-17 09:00:00"
    assert pending[1]["collection"] == "Custom/PushedColl"
    assert pending[1]["papers"] == [
        {"doi": "10.1000/j2-pending", "title": "J2 Paper", "year": 2020, "journal": "PNAS", "zotero": "failed"},
    ]


def test_zotero_history():
    """zotero_history lists only jobs that have a 'zotero' block,
    with state/collection and counts added/reused/no_pdf/failed."""
    # Job A: Done push with mixed outcomes
    jobA_id = "20261017-150000"
    jobA_data = {
        "schema": store.SCHEMA,
        "job": jobA_id,
        "title": "Pushed Job A",
        "session": None,
        "state": "done",
        "created": "2026-10-17 15:00:00",
        "finished": "2026-10-17 15:05:00",
        "output_dir": "",
        "error": None,
        "zotero": {
            "state": "done",
            "collection": "LitLoop/Pushed Job A",
            "key": "KEY_A",
            "started": "2026-10-17 15:05:00",
            "finished": "2026-10-17 15:06:00",
            "error": None,
        },
        "items": [
            {"doi": "10.1000/a1", "title": "A1", "year": None, "journal": None,
             "status": "done", "source": "unpaywall", "file": "a1.pdf", "error": None,
             "zotero": {"status": "added", "key": "K1", "error": None}},
            {"doi": "10.1000/a2", "title": "A2", "year": None, "journal": None,
             "status": "done", "source": "unpaywall", "file": "a2.pdf", "error": None,
             "zotero": {"status": "added", "key": "K2", "error": None}},
            {"doi": "10.1000/a3", "title": "A3", "year": None, "journal": None,
             "status": "done", "source": "unpaywall", "file": "a3.pdf", "error": None,
             "zotero": {"status": "reused", "key": "K3", "error": None}},
            {"doi": "10.1000/a4", "title": "A4", "year": None, "journal": None,
             "status": "done", "source": "unpaywall", "file": "a4.pdf", "error": None,
             "zotero": {"status": "no_pdf", "key": "K4", "error": "too big"}},
            {"doi": "10.1000/a5", "title": "A5", "year": None, "journal": None,
             "status": "done", "source": "unpaywall", "file": "a5.pdf", "error": None,
             "zotero": {"status": "failed", "key": None, "error": "timeout"}},
            {"doi": "10.1000/a6", "title": "A6", "year": None, "journal": None,
             "status": "failed", "source": None, "file": None, "error": "not found"},
        ],
    }
    store.write_json(download_jobs.job_path(jobA_id), jobA_data)

    # Job B: Failed push
    jobB_id = "20261017-140000"
    jobB_data = {
        "schema": store.SCHEMA,
        "job": jobB_id,
        "title": "Pushed Job B",
        "session": None,
        "state": "done",
        "created": "2026-10-17 14:00:00",
        "finished": "2026-10-17 14:05:00",
        "output_dir": "",
        "error": None,
        "zotero": {
            "state": "failed",
            "collection": "Custom/CollB",
            "key": None,
            "started": "2026-10-17 14:05:00",
            "finished": "2026-10-17 14:05:10",
            "error": "Zotero collection forbidden",
        },
        "items": [
            {"doi": "10.1000/b1", "title": "B1", "year": None, "journal": None,
             "status": "done", "source": "unpaywall", "file": "b1.pdf", "error": None,
             "zotero": {"status": "failed", "key": None, "error": "forbidden"}},
        ],
    }
    store.write_json(download_jobs.job_path(jobB_id), jobB_data)

    # Job C: No zotero block -> must be excluded from history
    jobC_id = "20261017-130000"
    jobC_data = {
        "schema": store.SCHEMA,
        "job": jobC_id,
        "title": "Unpushed Job C",
        "session": None,
        "state": "done",
        "created": "2026-10-17 13:00:00",
        "finished": "2026-10-17 13:05:00",
        "output_dir": "",
        "error": None,
        "items": [
            {"doi": "10.1000/c1", "title": "C1", "year": None, "journal": None,
             "status": "done", "source": "unpaywall", "file": "c1.pdf", "error": None},
        ],
    }
    store.write_json(download_jobs.job_path(jobC_id), jobC_data)

    history = download_jobs.zotero_history()
    assert len(history) == 2

    # Newest first
    assert history[0] == {
        "job": jobA_id,
        "title": "Pushed Job A",
        "state": "done",
        "collection": "LitLoop/Pushed Job A",
        "started": "2026-10-17 15:05:00",
        "finished": "2026-10-17 15:06:00",
        "error": None,
        "added": 2,
        "reused": 1,
        "no_pdf": 1,
        "failed": 1,
    }

    assert history[1] == {
        "job": jobB_id,
        "title": "Pushed Job B",
        "state": "failed",
        "collection": "Custom/CollB",
        "started": "2026-10-17 14:05:00",
        "finished": "2026-10-17 14:05:10",
        "error": "Zotero collection forbidden",
        "added": 0,
        "reused": 0,
        "no_pdf": 0,
        "failed": 1,
    }


def test_api_zotero_overview(client, monkeypatch):
    """GET /api/zotero returns running, mcp, pending, history, pushing."""
    # Write a job so pending and history have data
    job_id = "20261017-160000"
    job_data = {
        "schema": store.SCHEMA,
        "job": job_id,
        "title": "API Zotero Test Job",
        "session": None,
        "state": "done",
        "created": "2026-10-17 16:00:00",
        "finished": "2026-10-17 16:05:00",
        "output_dir": "",
        "error": None,
        "zotero": {
            "state": "done",
            "collection": "LitLoop/API Zotero Test Job",
            "key": "K_API",
            "started": "2026-10-17 16:05:00",
            "finished": "2026-10-17 16:06:00",
            "error": None,
        },
        "items": [
            {"doi": "10.1000/pending-item", "title": "Pending Item", "year": 2024, "journal": "Nature",
             "status": "done", "source": "unpaywall", "file": "p.pdf", "error": None,
             "zotero": {"status": "failed", "key": None, "error": "network"}},
            {"doi": "10.1000/added-item", "title": "Added Item", "year": 2024, "journal": "Nature",
             "status": "done", "source": "unpaywall", "file": "a.pdf", "error": None,
             "zotero": {"status": "added", "key": "K1", "error": None}},
        ],
    }
    store.write_json(download_jobs.job_path(job_id), job_data)

    # Case 1: Zotero running (200), MCP discovered, pushing in progress
    class FakeResponse:
        status_code = 200

    ping_calls = []

    def fake_get(url, **kwargs):
        ping_calls.append((url, kwargs))
        return FakeResponse()

    monkeypatch.setattr(requests, "get", fake_get)
    monkeypatch.setattr(zotero, "discover_zotero_mcp", lambda cfg: {"python": "C:/python/python.exe"})
    download_jobs._PUSHING["job"] = "20261017-160000"

    resp1 = client.get("/api/zotero")
    assert resp1.status_code == 200
    data1 = resp1.json()
    assert data1["running"] is True
    assert data1["mcp"] == "C:/python/python.exe"
    assert data1["pushing"] == "20261017-160000"
    assert data1["pending"] == download_jobs.zotero_pending()
    assert len(data1["pending"]) == 1
    assert data1["pending"][0]["papers"][0]["doi"] == "10.1000/pending-item"
    assert data1["history"] == download_jobs.zotero_history()
    assert len(data1["history"]) == 1
    assert data1["history"][0]["job"] == job_id

    assert len(ping_calls) == 1
    assert ping_calls[0][0] == "http://127.0.0.1:23119/connector/ping"
    assert ping_calls[0][1].get("timeout") == 2
    assert ping_calls[0][1].get("proxies") == {"http": None, "https": None}

    # Case 2: ping raises RequestException, MCP not found (None), no push in progress
    def fake_get_fail(url, **kwargs):
        raise requests.RequestException("connection refused")

    monkeypatch.setattr(requests, "get", fake_get_fail)
    monkeypatch.setattr(zotero, "discover_zotero_mcp", lambda cfg: None)
    download_jobs._PUSHING["job"] = None

    resp2 = client.get("/api/zotero")
    assert resp2.status_code == 200
    data2 = resp2.json()
    assert data2["running"] is False
    assert data2["mcp"] is None
    assert data2["pushing"] is None
    assert data2["pending"] == download_jobs.zotero_pending()
    assert data2["history"] == download_jobs.zotero_history()


