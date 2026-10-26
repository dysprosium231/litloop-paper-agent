"""Download jobs started from the desktop app.

A job is a list of papers downloaded by the normal batch engine
(``server._batch_download_impl``: lanes, racing, institutional cascade) in a
background thread of the app backend. Its state lives in one file,
``<litloop home>/downloads/<job>.json``, written through ``store`` (locked,
replaced in one step) so the app can poll it while the job runs::

    {"schema": 1, "job": "20261017-101500", "title": "...", "session": "...",
     "state": "running" | "queued" | "done" | "cancelled" | "failed" | "interrupted",
     "created": "...", "finished": "...", "output_dir": "...", "error": "...",
     "items": [{"doi", "title", "year", "journal", "publisher",
                "status": "waiting" | "queued" | "done" | "failed",
                "source", "file", "error",
                "zotero": {"status": "pushing" | "added" | "reused" | "no_pdf" | "failed",
                           "key", "error"}}],
     "zotero": {"state": "running" | "done" | "failed", "collection", "key",
                "started", "finished", "error"}}

Papers are marked done/failed one by one as the engine reports them
(``progress_reporter.advance``); source, file and error are filled in when
the batch returns. One job runs at a time: the engine's progress report and
cancel switch are process-wide.

A long list (more than ``batch_small_max`` papers) runs only the rounds that
need nobody, open access and Sci-Hub; the rest is ``queued`` and waits in one
queue per publisher (``queues``), which the user starts when they can log in
or pass a check (``start_queue``): its papers then go one by one through the
publisher's site and the school's routes, so one publisher's logins and checks
come together. The job is ``queued`` while any paper waits in a queue.

Downloaded papers are pushed to Zotero (zotero.push_via_zotero_mcp) by hand
from the job page, or right after the download when ``zotero_auto_push`` is
on, into ``<zotero_collection_root>/<job title>`` unless another collection
is chosen. A push runs in its own thread, one at a time, and can overlap a
download.
"""

from __future__ import annotations

import json
import re
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


OTHER_PUBLISHER = "其他出版商"
_CORPORATE = re.compile(r"[\s,]+(?:\([A-Z]{2,}\)|B\.?V\.?|Ltd\.?|Limited|Inc\.?|LLC|GmbH|plc|AG)$", re.I)


def publisher_name(raw: str | None) -> str:
    """OpenAlex's host organization without its company form ("Elsevier BV" -> "Elsevier")."""
    name, before = (raw or "").strip(), None
    while name != before:
        before, name = name, _CORPORATE.sub("", name).strip()
    return name


def queues(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The papers waiting in a queue, one queue per publisher, the largest first."""
    count: dict[str, int] = {}
    for it in items:
        if it.get("status") == "queued":
            name = it.get("publisher") or OTHER_PUBLISHER
            count[name] = count.get(name, 0) + 1
    return [{"publisher": k, "count": n}
            for k, n in sorted(count.items(), key=lambda kv: (kv[0] == OTHER_PUBLISHER, -kv[1], kv[0]))]


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
                      "failed": sum(1 for i in items if i["status"] == "failed"),
                      "queued": sum(1 for i in items if i["status"] == "queued")})
    return out


def get(job: str) -> dict[str, Any]:
    d = _settled(store.read_json(job_path(job)))
    d["queues"] = queues(d.get("items", []))
    return d


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


def queued_dois() -> set[str]:
    """DOIs waiting in a long list's publisher queue: not offered again either."""
    out: set[str] = set()
    for p in jobs_dir().glob("*.json"):
        try:
            out |= {i["doi"] for i in store.read_json(p).get("items", []) if i.get("status") == "queued"}
        except (OSError, ValueError):
            continue
    return out


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
        raise ValueError("无法下载：没有带 DOI 的文献")
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
        todo = sum(1 for it in items if it["status"] != "done")
        rounds = "free" if todo > int(load_config().get("batch_small_max", 30) or 0) else "all"
        threading.Thread(target=_run, args=(job,), kwargs={"rounds": rounds}, name=f"download-{job}", daemon=True).start()
        return data
    except BaseException:
        _CURRENT.update(job=None)
        _RUN_LOCK.release()
        raise


def start_queue(job: str, publisher: str) -> dict[str, Any]:
    """Download the papers of one publisher's queue (see the module doc)."""
    path = job_path(job)
    dois = [it["doi"] for it in get(job)["items"]
            if it["status"] == "queued" and (it.get("publisher") or OTHER_PUBLISHER) == publisher]
    if not dois:
        raise ValueError("无法开始：这个队列里没有待下载的文献")
    if not _RUN_LOCK.acquire(blocking=False):
        raise Busy("another download is still running")
    try:
        with store.update(path, indent=1) as data:
            for it in data["items"]:
                if it["doi"] in dois:
                    it.update(status="waiting", error=None)
            data.update(state="running", finished=None, error=None)
        _CURRENT.update(job=job, cancelled=False)
        threading.Thread(target=_run, args=(job,), kwargs={"only": dois, "rounds": "single"},
                         name=f"download-{job}", daemon=True).start()
        return get(job)
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


def _fill_meta(path: Path, wait_s: float = 10.0, publishers: bool = False) -> None:
    """Papers without a publication year (a pasted list has none) get it from
    OpenAlex, so the Sci-Hub year rule (scihub_skip_from_year) applies to them
    too; the years also go into the job file. With ``publishers`` (a long
    list) every paper also gets its publisher, which names its queue. The job waits at most
    ``wait_s``; years that come later still reach the papers not yet started."""
    missing = [i["doi"] for i in store.read_json(path)["items"]
               if i.get("doi") and (not i.get("year") or publishers and "publisher" not in i)]
    if not missing:
        return

    def run() -> None:
        from .search.deep import _get
        from .sources.scihub import note_years
        for k in range(0, len(missing), 50):
            chunk = missing[k:k + 50]
            try:
                data = _get({"filter": "doi:" + "|".join(chunk), "select": "doi,publication_year,primary_location",
                             "per_page": 50}, tries=1)
            except Exception:  # no network, no budget: the papers just stay without a year
                return
            works = {canonical_doi(w.get("doi")): w for w in data.get("results", []) if w.get("doi")}
            years = {d: int(w["publication_year"]) for d, w in works.items() if w.get("publication_year")}
            if years:
                note_years(years)
            if not works:
                continue
            with store.update(path, indent=1) as data_file:
                for it in data_file["items"]:
                    w = works.get(it.get("doi"))
                    if w is None:
                        continue
                    if not it.get("year") and it["doi"] in years:
                        it["year"] = years[it["doi"]]
                    if publishers and "publisher" not in it:
                        source = (w.get("primary_location") or {}).get("source") or {}
                        it["publisher"] = publisher_name(source.get("host_organization_name")) or None

    t = threading.Thread(target=run, name="job-years", daemon=True)
    t.start()
    t.join(wait_s)


def _run(job: str, only: list[str] | None = None, rounds: str = "all") -> None:
    """Download the job's papers not yet done (``only``: these), with the
    engine's ``rounds`` (``server._batch_download_impl``)."""
    path = job_path(job)
    dois = only or [i["doi"] for i in store.read_json(path)["items"] if i["status"] != "done"]  # not the ones already here
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
            from . import error_text
            _mark(job, doi, status="done" if ok else "failed", retrying=False, stage=None,
                  source=r.get("source"), file=r.get("file") or None, error=error_text.pick_failure(r),
                  note=r.get("note"), attempts=error_text.steps_for_app(r.get("attempts")))

    from .download import progress as progress_reporter
    from .sources import _SKIPPED
    from .sources.scihub import note_years
    _SKIPPED.clear()  # skips asked for in an earlier job do not carry over
    # The publication year decides whether Sci-Hub is asked (scihub_skip_from_year).
    note_years({i["doi"]: i.get("year") for i in store.read_json(path)["items"] if i.get("year")})
    _fill_meta(path, publishers=rounds == "free")
    progress_reporter.add_listener(on_item)
    progress_reporter.add_stage_listener(on_stage)
    progress_reporter.add_result_listener(on_result)
    state, error, results = "done", None, []
    try:
        if dois:
            from .server import _batch_download_impl
            out = json.loads(_batch_download_impl(dois, batch_id=f"app-{job}", keep_attempts=True,  # the app shows them
                                                  rounds=rounds))
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
                    if r and r.get("queued"):
                        it.update(status="queued", retrying=False, stage=None, error=None)
                    elif r:
                        from . import error_text
                        it.update(status="done" if r.get("success") else "failed", retrying=False, stage=None, source=r.get("source"),
                                  file=r.get("file") or None,
                                  error=error_text.pick_failure(r) or (None if r.get("success") else it.get("error")),
                                  note=r.get("note") or it.get("note"),
                                  attempts=error_text.steps_for_app(r.get("attempts")) or it.get("attempts"))
                    elif it["status"] == "waiting" and state == "done":
                        if rounds == "single" and _CURRENT["cancelled"]:
                            it.update(status="queued", retrying=False, stage=None)  # back in its queue
                        else:
                            it.update(status="failed", error="cancelled" if _CURRENT["cancelled"] else "no result")
                data["state"] = ("cancelled" if _CURRENT["cancelled"] and state == "done"
                                 else "queued" if state == "done" and any(i["status"] == "queued" for i in data["items"])
                                 else state)
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
        raise ValueError("无法推送：没有待推送的文献")
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
    from .zotero.client import ensure_collection, push_via_zotero_mcp
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
