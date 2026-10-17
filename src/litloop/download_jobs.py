"""Download jobs started from the desktop app.

A job is a list of papers downloaded by the normal batch engine
(``server._batch_download_impl``: lanes, racing, institutional cascade) in a
background thread of the app backend. Its state lives in one file,
``<litloop home>/downloads/<job>.json``, written through ``store`` (locked,
replaced in one step) so the app can poll it while the job runs::

    {"schema": 1, "job": "20261017-101500", "title": "...", "session": "...",
     "state": "running" | "done" | "cancelled" | "failed" | "interrupted",
     "created": "...", "finished": "...", "output_dir": "...", "error": "...",
     "items": [{"doi", "title", "year", "journal",
                "status": "waiting" | "done" | "failed",
                "source", "file", "error"}]}

Papers are marked done/failed one by one as the engine reports them
(``progress_reporter.advance``); source, file and error are filled in when
the batch returns. One job runs at a time: the engine's progress report and
cancel switch are process-wide.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any

from . import store
from .identifiers import canonical_doi

_RUN_LOCK = threading.Lock()
_CURRENT: dict[str, Any] = {"job": None, "cancelled": False}


class Busy(RuntimeError):
    """Another download job is still running."""


def jobs_dir() -> Path:
    from .config import DATA_DIR
    d = Path(DATA_DIR) / "downloads"
    d.mkdir(parents=True, exist_ok=True)
    return d


def job_path(job: str) -> Path:
    return jobs_dir() / f"{job}.json"


def _settled(d: dict[str, Any]) -> dict[str, Any]:
    """A job still marked running that this process is not running was cut off (app closed)."""
    if d.get("state") == "running" and d.get("job") != _CURRENT["job"]:
        d["state"] = "interrupted"
    return d


def list_jobs(limit: int = 50) -> list[dict[str, Any]]:
    """Newest first, without the item lists (counts instead)."""
    out = []
    for p in sorted(jobs_dir().glob("*.json"), reverse=True)[:limit]:
        try:
            d = _settled(store.read_json(p))
        except (OSError, ValueError):
            continue
        items = d.get("items", [])
        out.append({k: d.get(k) for k in ("job", "title", "session", "state", "created", "finished")}
                   | {"total": len(items), "done": sum(1 for i in items if i["status"] == "done"),
                      "failed": sum(1 for i in items if i["status"] == "failed")})
    return out


def get(job: str) -> dict[str, Any]:
    return _settled(store.read_json(job_path(job)))


def running() -> str | None:
    return _CURRENT["job"]


def downloaded_dois() -> set[str]:
    """DOIs some job has already got, so they are not offered again."""
    got = set()
    for p in jobs_dir().glob("*.json"):
        try:
            got |= {i["doi"] for i in store.read_json(p).get("items", []) if i.get("status") == "done"}
        except (OSError, ValueError):
            continue
    return got


def start(papers: list[dict[str, Any]], title: str, session: str | None = None) -> dict[str, Any]:
    """Create a job for papers ({doi, title, year, journal}) and start it in the background."""
    items, seen = [], set()
    for p in papers:
        doi = canonical_doi(p.get("doi") or "")
        if not doi or doi in seen:
            continue
        seen.add(doi)
        items.append({"doi": doi, "title": p.get("title") or doi, "year": p.get("year"),
                      "journal": p.get("journal"), "status": "waiting", "source": None, "file": None, "error": None})
    if not items:
        raise ValueError("no paper with a DOI to download")
    if not _RUN_LOCK.acquire(blocking=False):
        raise Busy("another download is still running")
    try:
        from .config import load_config
        job = time.strftime("%Y%m%d-%H%M%S")
        while job_path(job).exists():
            time.sleep(1)
            job = time.strftime("%Y%m%d-%H%M%S")
        data = {"schema": store.SCHEMA, "job": job, "title": title, "session": session, "state": "running",
                "created": time.strftime("%Y-%m-%d %H:%M:%S"), "finished": None,
                "output_dir": str(load_config().get("output_dir") or ""), "error": None, "items": items}
        store.write_json(job_path(job), data, indent=1)
        _CURRENT.update(job=job, cancelled=False)
        threading.Thread(target=_run, args=(job,), name=f"download-{job}", daemon=True).start()
        return data
    except BaseException:
        _CURRENT.update(job=None)
        _RUN_LOCK.release()
        raise


def cancel() -> bool:
    """Ask the running job to stop: pending papers are skipped, browser windows closed."""
    if not _CURRENT["job"]:
        return False
    from .sources import cancel_downloads
    _CURRENT["cancelled"] = True
    cancel_downloads()
    return True


def _mark(job: str, doi: str, **fields: Any) -> None:
    with store.update(job_path(job), indent=1) as data:
        for it in data["items"]:
            if it["doi"] == doi:
                it.update(fields)


def _run(job: str) -> None:
    path = job_path(job)
    dois = [i["doi"] for i in store.read_json(path)["items"]]
    wanted = set(dois)

    def on_item(ok: bool, current: str) -> None:
        doi = canonical_doi(current or "")
        if doi in wanted:
            _mark(job, doi, status="done" if ok else "failed")

    from . import progress_reporter
    progress_reporter.add_listener(on_item)
    state, error, results = "done", None, []
    try:
        from .server import _batch_download_impl
        out = json.loads(_batch_download_impl(dois, batch_id=f"app-{job}"))
        results = out.get("results", [])
    except Exception as exc:  # the job file says what went wrong; the app shows it
        state, error = "failed", f"{type(exc).__name__}: {exc}"
    finally:
        progress_reporter.remove_listener(on_item)
        try:
            with store.update(path, indent=1) as data:
                by_doi = {canonical_doi(r.get("doi") or r.get("identifier") or ""): r for r in results}
                for it in data["items"]:
                    r = by_doi.get(it["doi"])
                    if r:
                        it.update(status="done" if r.get("success") else "failed", source=r.get("source"),
                                  file=r.get("file") or None, error=None if r.get("success") else r.get("error"))
                    elif it["status"] == "waiting" and state == "done":
                        it.update(status="failed", error="cancelled" if _CURRENT["cancelled"] else "no result")
                data["state"] = "cancelled" if _CURRENT["cancelled"] and state == "done" else state
                data["error"] = error
                data["finished"] = time.strftime("%Y-%m-%d %H:%M:%S")
        finally:
            _CURRENT.update(job=None, cancelled=False)
            _RUN_LOCK.release()
