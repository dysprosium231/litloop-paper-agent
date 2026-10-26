"""Papers already in the save folder: not offered again, not downloaded again; lane failures are not final."""

import json
import time

import pytest

from litloop import config, server
from litloop.desktop import download_jobs
from litloop.download import progress as progress_reporter


@pytest.fixture
def folder(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "config.json")
    out = tmp_path / "papers"
    out.mkdir()
    monkeypatch.setitem(config.DEFAULT_CONFIG, "output_dir", str(out))
    (out / "Old2024.pdf").write_bytes(b"%PDF-1.4")
    (out / ".doi_index.json").write_text(json.dumps({
        "10.1/here": {"file": str(out / "Old2024.pdf"), "source": "Elsevier(Browser)", "ts": 1.0},
        "10.1/gone": {"file": str(out / "Deleted.pdf"), "source": "x", "ts": 1.0},
    }), encoding="utf-8")
    return out


def _wait(job):
    for _ in range(200):
        d = download_jobs.get(job)
        if d["state"] != "running":
            return d
        time.sleep(0.02)
    raise AssertionError("job did not finish")


def test_local_files_needs_the_file_to_exist(folder):
    assert download_jobs.local_files() == {"10.1/here": str(folder / "Old2024.pdf")}
    assert "10.1/here" in download_jobs.downloaded_dois()


def test_a_paper_already_here_is_done_without_the_engine(folder, monkeypatch):
    asked = []

    def engine(dois, **kw):
        asked.append(list(dois))
        return json.dumps({"results": [{"doi": d, "success": True, "source": "oa", "file": "x.pdf"} for d in dois]})
    monkeypatch.setattr(server, "_batch_download_impl", engine)
    d = _wait(download_jobs.start([{"doi": "10.1/here"}, {"doi": "10.1/new"}], "t")["job"])
    assert asked == [["10.1/new"]]
    here = next(i for i in d["items"] if i["doi"] == "10.1/here")
    assert here["status"] == "done" and here["source"] == download_jobs.LOCAL


def test_only_papers_already_here_runs_nothing(folder, monkeypatch):
    monkeypatch.setattr(server, "_batch_download_impl", lambda dois, **kw: pytest.fail("engine asked"))
    d = _wait(download_jobs.start([{"doi": "10.1/here"}], "t")["job"])
    assert d["state"] == "done" and d["items"][0]["status"] == "done"


def test_a_lane_failure_is_not_final(folder, monkeypatch):
    seen = {}

    def engine(dois, **kw):
        progress_reporter.advance(False, current="10.1/new")  # the fast lane missed it ...
        seen["mid"] = download_jobs.get(job_id[0])["items"][0]
        return json.dumps({"results": [{"doi": "10.1/new", "success": True, "source": "grey", "file": "y.pdf"}]})
    job_id = []
    monkeypatch.setattr(server, "_batch_download_impl", engine)
    monkeypatch.setattr(download_jobs.threading, "Thread", _deferred(job_id))
    job_id.append(download_jobs.start([{"doi": "10.1/new"}], "t")["job"])
    _deferred.run()
    assert seen["mid"]["status"] == "waiting" and seen["mid"]["retrying"] is True
    d = download_jobs.get(job_id[0])
    assert d["items"][0]["status"] == "done" and d["items"][0]["retrying"] is False


class _deferred:
    """A Thread stand-in that runs the target later, once the job id is known."""
    pending = []

    def __init__(self, job_id):
        self.job_id = job_id

    def __call__(self, target, args=(), **kw):
        _deferred.pending.append((target, args))
        return self

    def start(self):
        pass

    @classmethod
    def run(cls):
        while cls.pending:
            target, args = cls.pending.pop(0)
            target(*args)


def test_a_paper_shows_where_it_is_and_clears_when_done(folder, monkeypatch):
    seen = {}

    def engine(dois, **kw):
        progress_reporter.item_stage("10.1/new", "已在 Sci-Hub 找到，正在下载")
        seen["mid"] = download_jobs.get(job_id[0])["items"][0]
        return json.dumps({"results": [{"doi": "10.1/new", "success": True, "source": "Sci-Hub", "file": "y.pdf"}]})
    job_id = []
    monkeypatch.setattr(server, "_batch_download_impl", engine)
    monkeypatch.setattr(download_jobs.threading, "Thread", _deferred(job_id))
    job_id.append(download_jobs.start([{"doi": "10.1/new"}], "t")["job"])
    _deferred.run()
    assert seen["mid"]["stage"] == "已在 Sci-Hub 找到，正在下载"
    assert download_jobs.get(job_id[0])["items"][0]["stage"] is None


def test_a_final_result_shows_at_once(folder, monkeypatch):
    seen = {}

    def engine(dois, **kw):
        progress_reporter.advance(False, current="10.1/new")
        progress_reporter.item_result("10.1/new", {"success": False, "error": "all sources failed"})
        seen["mid"] = dict(download_jobs.get(job_id[0])["items"][0])
        return json.dumps({"results": [{"doi": "10.1/new", "success": False, "error": "all sources failed"}]})
    job_id = []
    monkeypatch.setattr(server, "_batch_download_impl", engine)
    monkeypatch.setattr(download_jobs.threading, "Thread", _deferred(job_id))
    job_id.append(download_jobs.start([{"doi": "10.1/new"}], "t")["job"])
    _deferred.run()
    assert seen["mid"]["status"] == "failed" and seen["mid"]["retrying"] is False and seen["mid"]["stage"] is None
