"""Tests for download jobs and desktop app download endpoints."""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from litloop import app_server, config, download_jobs, progress_reporter, screening, server, store

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
    download_jobs._CURRENT.update(job=None, cancelled=False)
    progress_reporter._LISTENERS.clear()

    yield

    for t in threading.enumerate():
        if t.name.startswith("download-"):
            t.join(timeout=2.0)
    if download_jobs._RUN_LOCK.locked():
        try:
            download_jobs._RUN_LOCK.release()
        except RuntimeError:
            pass
    download_jobs._CURRENT.update(job=None, cancelled=False)
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
    assert resp.json() == {"jobs": [], "running": None, "live": None}


def test_job_failure_on_engine_exception(fake_batch):
    """When _batch_download_impl raises, job state becomes failed."""
    fake_batch.exception_to_raise = RuntimeError("Engine crash")

    job_info = download_jobs.start([{"doi": "10.1000/crash"}], title="Crash Job")
    data = wait_job(job_info["job"])
    assert data["state"] == "failed"
    assert "RuntimeError: Engine crash" in (data.get("error") or "")
