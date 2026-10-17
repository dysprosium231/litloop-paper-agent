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
                "source", "file", "error",
                "zotero": {"status": "pushing" | "added" | "reused" | "no_pdf" | "failed",
                           "key", "error"}}],
     "zotero": {"state": "running" | "done" | "failed", "collection", "key",
                "started", "finished", "error"}}

Papers are marked done/failed one by one as the engine reports them
(``progress_reporter.advance``); source, file and error are filled in when
the batch returns. One job runs at a time: the engine's progress report and
cancel switch are process-wide.

Downloaded papers are pushed to Zotero (zotero.push_via_zotero_mcp) by hand
from the job page, or right after the download when ``zotero_auto_push`` is
on, into ``<zotero_collection_root>/<job title>`` unless another collection
is chosen. A push runs in its own thread, one at a time, and can overlap a
download.
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
_PUSH_LOCK = threading.Lock()
_PUSHING: dict[str, Any] = {"job": None}


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
    z = d.get("zotero")
    if z and z.get("state") == "running" and d.get("job") != _PUSHING["job"]:
        z["state"] = "interrupted"
        for it in d.get("items", []):
            if (it.get("zotero") or {}).get("status") == "pushing":
                it["zotero"]["status"] = "failed"
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


LOCAL = "本机已有"  # the source of a paper found in the save folder, not downloaded again
_ARXIV_DOI = "10.48550/arxiv."


def same_paper(doi: str) -> set[str]:
    """The keys one paper can go by: an arXiv id and its arXiv DOI are the same paper."""
    d = canonical_doi(doi or "")
    if d.startswith(_ARXIV_DOI):
        return {d, d[len(_ARXIV_DOI):]}
    if d and not d.startswith("10."):  # a bare arXiv id
        return {d, _ARXIV_DOI + d}
    return {d} if d else set()


def local_files(dois: list[str] | set[str] | None = None) -> dict[str, str]:
    """DOI -> PDF already in the save folder, from its DOI index (.doi_index.json).

    Only files that are still there count; age does not matter (a PDF on disk
    does not expire, whatever cache_ttl_hours says about re-checking sources).
    """
    from .config import load_config
    folder = Path(str(load_config().get("output_dir") or "")).expanduser()
    try:
        idx = json.loads((folder / ".doi_index.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    found = {}
    for key, entry in idx.items():
        f = entry.get("file") if isinstance(entry, dict) else entry
        if f and Path(f).is_file():
            for k in same_paper(key):
                found[k] = str(f)
    if dois is None:
        return found
    return {canonical_doi(d): found[k] for d in dois for k in same_paper(d) if k in found}


def downloaded_dois() -> set[str]:
    """DOIs some job has already got, or whose PDF is already in the save folder:
    they are not offered again."""
    got = {k for d in local_files() for k in same_paper(d)}
    for p in jobs_dir().glob("*.json"):
        try:
            got |= {i["doi"] for i in store.read_json(p).get("items", []) if i.get("status") == "done"}
        except (OSError, ValueError):
            continue
    return got


def start(papers: list[dict[str, Any]], title: str, session: str | None = None,
          zotero_target: str | None = None) -> dict[str, Any]:
    """Create a job for papers ({doi, title, year, journal}) and start it in the background.

    zotero_target: the collection a later push goes to by default (the attachment
    check sets it to the collection being repaired).
    """
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
    # Already in the save folder: done at once, the engine is not asked again.
    for it, f in ((it, local_files([it["doi"]]).get(it["doi"])) for it in items):
        if f:
            it.update(status="done", source=LOCAL, file=f)
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
                "output_dir": str(load_config().get("output_dir") or ""), "error": None, "items": items,
                "zotero_target": (zotero_target or "").strip().strip("/") or None}
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
    dois = [i["doi"] for i in store.read_json(path)["items"] if i["status"] != "done"]  # not the ones already here
    wanted = set(dois)

    def on_item(ok: bool, current: str) -> None:
        # A lane that fails hands the paper to the next one (grey, institutional,
        # a cooled-down retry), so a failure here is not final: the engine's
        # results at the end say what failed. Until then the paper is retrying.
        doi = canonical_doi(current or "")
        if doi in wanted:
            if ok:
                _mark(job, doi, status="done", retrying=False, stage=None)
            else:
                _mark(job, doi, retrying=True)

    def on_stage(current: str, text: str) -> None:
        doi = canonical_doi(current or "")
        if doi in wanted:
            _mark(job, doi, stage=text)

    def on_result(current: str, r: dict) -> None:
        # Final for this paper: show it now, as the end of the job would.
        doi = canonical_doi(current or "")
        if doi in wanted:
            ok = bool(r.get("success"))
            _mark(job, doi, status="done" if ok else "failed", retrying=False, stage=None,
                  source=r.get("source"), file=r.get("file") or None, error=None if ok else r.get("error"),
                  note=r.get("note"))

    from . import progress_reporter
    from .sources import _SKIPPED
    from .sources.scihub import note_years
    _SKIPPED.clear()  # skips asked for in an earlier job do not carry over
    # The publication year decides whether Sci-Hub is asked (scihub_skip_from_year).
    note_years({i["doi"]: i.get("year") for i in store.read_json(path)["items"] if i.get("year")})
    progress_reporter.add_listener(on_item)
    progress_reporter.add_stage_listener(on_stage)
    progress_reporter.add_result_listener(on_result)
    state, error, results = "done", None, []
    try:
        if dois:
            from .server import _batch_download_impl
            out = json.loads(_batch_download_impl(dois, batch_id=f"app-{job}"))
            results = out.get("results", [])
    except Exception as exc:  # the job file says what went wrong; the app shows it
        state, error = "failed", f"{type(exc).__name__}: {exc}"
    finally:
        progress_reporter.remove_listener(on_item)
        progress_reporter.remove_stage_listener(on_stage)
        progress_reporter.remove_result_listener(on_result)
        try:
            with store.update(path, indent=1) as data:
                by_doi = {canonical_doi(r.get("doi") or r.get("identifier") or ""): r for r in results}
                for it in data["items"]:
                    r = by_doi.get(it["doi"])
                    if r:
                        it.update(status="done" if r.get("success") else "failed", retrying=False, stage=None, source=r.get("source"),
                                  file=r.get("file") or None, error=None if r.get("success") else r.get("error"),
                                  note=r.get("note") or it.get("note"))
                    elif it["status"] == "waiting" and state == "done":
                        it.update(status="failed", error="cancelled" if _CURRENT["cancelled"] else "no result")
                data["state"] = "cancelled" if _CURRENT["cancelled"] and state == "done" else state
                data["error"] = error
                data["finished"] = time.strftime("%Y-%m-%d %H:%M:%S")
        finally:
            _CURRENT.update(job=None, cancelled=False)
            _RUN_LOCK.release()
    from .config import load_config
    if load_config().get("zotero_auto_push"):
        try:
            push_zotero(job)
        except (Busy, ValueError):
            pass  # another push runs, or nothing was downloaded: the user can push later


def default_collection(title: str) -> str:
    """<zotero_collection_root>/<title>, shortened; '/' would start a sub-collection."""
    from .config import load_config
    root = str(load_config().get("zotero_collection_root") or "LitLoop").strip().strip("/")
    name = " ".join(title.replace("/", "-").split())
    name = name if len(name) <= 40 else name[:39] + "…"
    return f"{root}/{name}" if root else name


def pushing() -> str | None:
    return _PUSHING["job"]


def find_local_pdf(doi: str) -> Path | None:
    """A PDF of this DOI on this computer: one a job downloaded, else a file in the
    download folder whose name carries the DOI (the default file-name format)."""
    from .config import load_config
    from .identifiers import safe_filename
    doi = canonical_doi(doi or "")
    if not doi:
        return None
    for d in _jobs_full():
        for it in d["items"]:
            if it["doi"] == doi and it.get("file") and Path(it["file"]).is_file():
                return Path(it["file"])
    out = Path(str(load_config().get("output_dir") or ""))
    tag = safe_filename(doi)
    if tag and out.is_dir():
        for p in out.glob("*.pdf"):
            if tag in p.name.lower():
                return p
    return None


def _jobs_full(limit: int = 200) -> list[dict[str, Any]]:
    out = []
    for j in list_jobs(limit):
        try:
            out.append(get(j["job"]))
        except (OSError, ValueError):
            continue
    return out


def zotero_pending() -> list[dict[str, Any]]:
    """Downloaded papers not in Zotero yet (never pushed, PDF not attached, or failed), by job."""
    out = []
    for d in _jobs_full():
        papers = [{k: i.get(k) for k in ("doi", "title", "year", "journal")} | {"zotero": (i.get("zotero") or {}).get("status")}
                  for i in d["items"] if i["status"] == "done" and i.get("file")
                  and (i.get("zotero") or {}).get("status") not in ("added", "reused")]
        if papers:
            out.append({"job": d["job"], "title": d["title"], "created": d["created"], "papers": papers,
                        "collection": (d.get("zotero") or {}).get("collection") or d.get("zotero_target")
                        or default_collection(d["title"])})
    return out


def zotero_history() -> list[dict[str, Any]]:
    """Jobs that were pushed, newest first, with counts per outcome."""
    out = []
    for d in _jobs_full():
        z = d.get("zotero")
        if not z:
            continue
        counts = {k: sum(1 for i in d["items"] if (i.get("zotero") or {}).get("status") == k)
                  for k in ("added", "reused", "no_pdf", "failed")}
        out.append({"job": d["job"], "title": d["title"], **{k: z.get(k) for k in ("state", "collection", "started", "finished", "error")}, **counts})
    return out


def push_zotero(job: str, collection: str | None = None, tags: list[str] | None = None) -> dict[str, Any]:
    """Push the job's downloaded papers to Zotero in the background (those not pushed yet)."""
    data = get(job)
    todo = [i for i in data["items"] if i["status"] == "done" and i.get("file")
            and (i.get("zotero") or {}).get("status") not in ("added", "reused")]
    if not todo:
        raise ValueError("nothing to push: no downloaded paper waits for Zotero")
    collection = (collection or "").strip().strip("/") or data.get("zotero_target") or default_collection(data["title"])
    if not _PUSH_LOCK.acquire(blocking=False):
        raise Busy("another Zotero push is still running")
    try:
        with store.update(job_path(job), indent=1) as d:
            d["zotero"] = {"state": "running", "collection": collection, "key": None,
                           "started": time.strftime("%Y-%m-%d %H:%M:%S"), "finished": None, "error": None}
        _PUSHING["job"] = job
        threading.Thread(target=_push, args=(job, collection, list(tags or [])),
                         name=f"zotero-{job}", daemon=True).start()
        return get(job)
    except BaseException:
        _PUSHING["job"] = None
        _PUSH_LOCK.release()
        raise


def _set_zotero(job: str, **fields: Any) -> None:
    with store.update(job_path(job), indent=1) as d:
        d["zotero"] = {**(d.get("zotero") or {}), **fields}


def _push(job: str, collection: str, tags: list[str]) -> None:
    from .config import load_config
    from .zotero import ensure_collection, push_via_zotero_mcp
    cfg = load_config()
    try:
        found = ensure_collection(collection, cfg)
        if not found.get("success"):
            _set_zotero(job, state="failed", error=found.get("error") or "cannot create the collection")
            return
        _set_zotero(job, key=found["key"])
        for it in get(job)["items"]:
            if it["status"] != "done" or not it.get("file") or (it.get("zotero") or {}).get("status") in ("added", "reused"):
                continue
            _mark(job, it["doi"], zotero={"status": "pushing", "key": None, "error": None})
            try:
                r = push_via_zotero_mcp(it["doi"], Path(it["file"]), cfg, collections=[found["key"]], tags=tags)
            except Exception as exc:
                r = {"success": False, "error": f"{type(exc).__name__}: {exc}"}
            if not r.get("success"):
                z = {"status": "failed", "key": None, "error": r.get("error")}
            elif not r.get("pdf_attached"):
                z = {"status": "no_pdf", "key": r.get("zotero_key"), "error": r.get("attach_message")}
            else:
                z = {"status": "reused" if r.get("reused") else "added", "key": r.get("zotero_key"), "error": None}
            _mark(job, it["doi"], zotero=z)
        _set_zotero(job, state="done", finished=time.strftime("%Y-%m-%d %H:%M:%S"))
    except Exception as exc:
        _set_zotero(job, state="failed", error=f"{type(exc).__name__}: {exc}",
                    finished=time.strftime("%Y-%m-%d %H:%M:%S"))
    finally:
        _PUSHING["job"] = None
        _PUSH_LOCK.release()
