"""Local HTTP backend for the LitLoop desktop app.

The Tauri shell starts this as a child process:

    python -m litloop.app_server --token <random>

It listens on 127.0.0.1 only, on a free port it picks itself, and prints one
line to stdout before serving, so the shell knows where to connect:

    {"litloop_app_server": 1, "port": 51234}

Every request must carry the token (header ``X-LitLoop-Token``), so another
web page in the user's browser cannot call it. Data comes from the files
described in docs/APP_DATA.md, read and written through ``store``.
"""

from __future__ import annotations

import argparse
import json
import re
import secrets
import socket
import sys
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from . import __version__, screening, store

# Where the app's pages are served from: the Vite dev server, and Tauri's
# own origins (Windows uses http://tauri.localhost, macOS/Linux tauri://localhost).
ORIGINS = ["http://localhost:1420", "http://tauri.localhost", "https://tauri.localhost", "tauri://localhost"]
_SESSION_ID = re.compile(r"^[\w一-鿿.-]{1,200}$")


class Rating(BaseModel):
    doi: str | None = None
    title: str | None = None
    relevance: str | None = None  # None clears the user's rating: the agent's counts again


class Setting(BaseModel):
    key: str
    value: Any = None


class Paper(BaseModel):
    doi: str
    title: str | None = None
    year: int | None = None
    journal: str | None = None


class NewDownload(BaseModel):
    papers: list[Paper]
    title: str
    session: str | None = None
    zotero_target: str | None = None


class ZoteroCheck(BaseModel):
    key: str


class ZoteroRepair(BaseModel):
    item_key: str
    doi: str | None = None
    trash: list[str] = []
    attach: bool = True  # False: only move the empty attachments to the Trash


class ZoteroPush(BaseModel):
    collection: str | None = None  # None: <zotero_collection_root>/<job title>
    tags: list[str] = []


class OpenPath(BaseModel):
    path: str


class Translate(BaseModel):
    doi: str | None = None
    title: str | None = None


class Decision(BaseModel):
    doi: str | None = None
    title: str | None = None
    decision: str | None = None  # "selected", "rejected" or None


_OPENALEX: dict[str, Any] = {"at": 0.0, "data": None}  # see /api/quota/openalex
_WRITE_MODE: dict[str, Any] = {}  # zotero-mcp's write mode, cached until the user authorizes


def _explain(d: dict[str, Any] | None, action: str = "下载", full: bool = False) -> None:
    """Add ``error_text`` (docs/UI_COPY.md section 7) next to a raw ``error``; the raw text stays for the tooltip."""
    from . import error_text
    if d and d.get("error"):
        d["error_text"] = error_text.message(d["error"], action) if full else error_text.reason(d["error"])


def _fail(status: int, raw: str | None, action: str, fallback: str) -> HTTPException:
    from . import error_text
    return HTTPException(status, error_text.message(raw or fallback, action))


def _record_path(session: str):
    if not _SESSION_ID.match(session) or session.startswith("."):
        raise HTTPException(400, "bad session id")
    p = screening.screening_dir() / f"{session}.json"
    if not p.exists():
        raise HTTPException(404, f"no session {session}")
    return p


def _ensure_item(session: str, doi: str | None, title: str | None, relevance: str | None) -> None:
    """A paper of the latest batch the agent has not rated yet: the user's rating creates its item
    (no reason: the agent's rating, when it comes, fills that in and the user's stays)."""
    if relevance is None:
        return
    record = store.read_json(_record_path(session))
    try:
        _find(record["items"], doi, title)
        return
    except HTTPException:
        pass
    from . import deep_search
    try:
        paper = deep_search.batch_paper(session, doi, title)
    except FileNotFoundError:
        paper = None
    if not paper:
        raise HTTPException(404, "no such paper in this session")
    screening.save("", [{**paper, "relevance": relevance, "reason": ""}], session=session, mode="deep")


def _find(items: list[dict[str, Any]], doi: str | None, title: str | None) -> dict[str, Any]:
    key = screening._key({"doi": doi, "title": title})
    for it in items:
        if screening._key(it) == key:
            return it
    raise HTTPException(404, "no such paper in this session")


def create_app(token: str) -> FastAPI:
    app = FastAPI(title="LitLoop app backend", version=__version__, docs_url=None, redoc_url=None)
    app.add_middleware(CORSMiddleware, allow_origins=ORIGINS, allow_methods=["GET", "POST"],
                       allow_headers=["X-LitLoop-Token", "Content-Type"])

    @app.middleware("http")
    async def require_token(request: Request, call_next):
        if request.method != "OPTIONS" and not secrets.compare_digest(
                request.headers.get("X-LitLoop-Token", ""), token):
            return JSONResponse({"detail": "missing or wrong token"}, status_code=401)
        return await call_next(request)

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        return {"version": __version__, "schema": store.SCHEMA}

    @app.get("/api/sessions")
    def sessions(limit: int = 50) -> list[dict[str, Any]]:
        out = screening.list_sessions(limit)
        for s in out:
            s["deep"] = store.summary_path(s["session"]).exists()
        return out

    @app.get("/api/sessions/{session}")
    def session(session: str) -> dict[str, Any]:
        record = store.read_json(_record_path(session))
        try:
            summary = store.read_json(store.summary_path(session))
            # The file keeps the agent's English notes; the app shows today's, in Chinese.
            from . import quota
            summary["api_warnings"] = quota.warnings("zh")
        except FileNotFoundError:
            summary = None
        return {"record": record, "summary": summary}

    @app.get("/api/sessions/{session}/progress")
    def progress(session: str) -> dict[str, Any] | None:
        _record_path(session)
        return store.read_progress(session)

    @app.post("/api/sessions/{session}/rating")
    def rate(session: str, body: Rating) -> dict[str, Any]:
        if body.relevance is not None and body.relevance not in screening.RELEVANCE:
            raise HTTPException(400, f"relevance must be one of {screening.RELEVANCE} or null")
        _ensure_item(session, body.doi, body.title, body.relevance)
        with store.update(_record_path(session), indent=2) as data:
            item = _find(data["items"], body.doi, body.title)
            if not item.get("reason"):  # rated by the user only: theirs is the only rating
                if body.relevance is None:
                    data["items"].remove(item)
                    return {"item": None, "edited": sum(1 for it in data["items"] if it.get("user_relevance"))}
                item["relevance"] = item["user_relevance"] = body.relevance
            elif body.relevance is None or body.relevance == item["relevance"]:
                item.pop("user_relevance", None)
            else:
                item["user_relevance"] = body.relevance
            edited = sum(1 for it in data["items"] if it.get("user_relevance"))
        return {"item": item, "edited": edited}

    @app.post("/api/sessions/{session}/next")
    def next_round(session: str) -> dict[str, Any]:
        """The app's continue button: serve the next batch here, without the agent (it rates the rest when it is back)."""
        import threading
        import time

        from . import deep_search
        _record_path(session)
        if not deep_search._pool_path(session).exists():
            raise HTTPException(400, "无法继续检索：这不是深度检索会话。")
        p = store.read_progress(session)
        if p and p.get("state") == "running" and time.time() - float(p.get("updated") or 0) < 300:
            raise HTTPException(409, "正在生成下一批，完成后再继续。")

        def run() -> None:
            try:
                deep_search.next_batch(session, force=True)
            except Exception:  # the progress file says it failed; the app shows that
                pass
        threading.Thread(target=run, name=f"next-{session}", daemon=True).start()
        return {"started": True}

    @app.get("/api/updates")
    def updates_available() -> list[dict[str, Any]]:
        """Components with a newer version (from the weekly check; a due check starts in the background)."""
        from . import updates
        return updates.available()

    @app.get("/api/translation")
    def translation() -> dict[str, Any]:
        """What the detail pane needs: who translates, which ratings without asking, whether it can."""
        from . import translate
        from .config import load_config
        config = load_config()
        name = translate.provider(config)
        return {"provider": name, "auto": screening.translate_wanted(config),
                "ready": name != "agent" and bool(str(config.get(f"{name}_api_key") or "").strip())}

    @app.post("/api/sessions/{session}/translate")
    def translate_abstract(session: str, body: Translate) -> dict[str, Any]:
        """Translate one paper's abstract with DeepSeek / DeepL and keep it in the record."""
        from . import translate
        item = _find(store.read_json(_record_path(session))["items"], body.doi, body.title)
        if item.get("abstract_zh"):
            return {"abstract_zh": item["abstract_zh"]}
        if not item.get("abstract"):
            raise HTTPException(400, "无法翻译：此文献没有摘要")
        try:
            text = translate.translate_text(item["abstract"])
        except translate.TranslationError as exc:
            raise HTTPException(502, f"无法翻译：{exc}。") from exc
        with store.update(_record_path(session), indent=2) as data:  # the network call ran outside the lock
            _find(data["items"], body.doi, body.title)["abstract_zh"] = text[:screening.ABSTRACT_CHARS]
        return {"abstract_zh": text}

    @app.post("/api/sessions/{session}/decision")
    def decide(session: str, body: Decision) -> dict[str, Any]:
        if body.decision not in (None, "selected", "rejected"):
            raise HTTPException(400, "decision must be selected, rejected or null")
        with store.update(_record_path(session), indent=2) as data:
            item = _find(data["items"], body.doi, body.title)
            item["decision"] = body.decision
        return {"item": item}

    @app.get("/api/quota")
    def quota_summary() -> dict[str, Any]:
        from . import quota
        return quota.summary()

    @app.get("/api/quota/openalex")
    def quota_openalex(fresh: bool = False) -> dict[str, Any] | None:
        """OpenAlex's own count (costs no credits, but takes seconds): kept two minutes."""
        import time

        from . import quota
        if fresh or time.time() - _OPENALEX["at"] > 120:
            _OPENALEX.update(at=time.time(), data=quota.openalex_live())
        return _OPENALEX["data"]

    @app.get("/api/settings")
    def settings_list() -> list[dict[str, Any]]:
        from . import app_settings
        return app_settings.settings()

    @app.post("/api/settings")
    def settings_update(body: Setting) -> dict[str, Any]:
        from . import app_settings
        try:
            return app_settings.update(body.key, body.value)
        except KeyError as exc:
            raise HTTPException(400, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.post("/api/settings/test/{group}")
    def settings_test(group: str) -> dict[str, Any]:
        from . import app_settings
        try:
            return app_settings.run_test(group)
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc

    # Downloads: jobs started from the app (download_jobs), one at a time.
    @app.get("/api/downloads")
    def downloads() -> dict[str, Any]:
        from . import download_jobs, progress_reporter
        from .config import load_config
        live = progress_reporter.read_state() if download_jobs.running() else None
        return {"jobs": download_jobs.list_jobs(), "running": download_jobs.running(), "live": live,
                "pushing": download_jobs.pushing(), "output_dir": str(load_config().get("output_dir") or "")}

    @app.get("/api/downloads/pending")
    def downloads_pending() -> list[dict[str, Any]]:
        """Papers picked 选中下载 in screening that no job has downloaded yet, by session."""
        from . import download_jobs
        from .identifiers import canonical_doi
        got = download_jobs.downloaded_dois()
        out = []
        for s in screening.list_sessions(200):
            if not s["selected"]:
                continue
            try:
                items = store.read_json(_record_path(s["session"]))["items"]
            except (OSError, ValueError, HTTPException):
                continue
            papers = [{k: it.get(k) for k in ("doi", "title", "year", "journal")} for it in items
                      if it.get("decision") == "selected" and it.get("doi") and canonical_doi(it["doi"]) not in got]
            if papers:
                out.append({"session": s["session"], "question": s["question"], "papers": papers})
        return out

    @app.get("/api/downloads/{job}")
    def download_job(job: str) -> dict[str, Any]:
        from . import download_jobs
        if not re.fullmatch(r"[\w-]{1,40}", job):
            raise HTTPException(400, "bad job id")
        try:
            d = download_jobs.get(job)
        except FileNotFoundError as exc:
            raise HTTPException(404, f"no job {job}") from exc
        d["zotero_default"] = download_jobs.default_collection(d["title"])
        _explain(d, full=True)
        _explain(d.get("zotero"), "推送", full=True)
        for it in d.get("items") or []:
            _explain(it)
            _explain(it.get("zotero"), "推送")
        return d

    @app.post("/api/downloads/{job}/zotero")
    def download_push(job: str, body: ZoteroPush) -> dict[str, Any]:
        from . import download_jobs
        if not re.fullmatch(r"[\w-]{1,40}", job):
            raise HTTPException(400, "bad job id")
        try:
            return download_jobs.push_zotero(job, body.collection, body.tags)
        except FileNotFoundError as exc:
            raise HTTPException(404, f"no job {job}") from exc
        except download_jobs.Busy as exc:
            raise HTTPException(409, "已有推送任务在进行，完成后再开始新的推送") from exc
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.get("/api/zotero")
    def zotero_overview() -> dict[str, Any]:
        """Is Zotero up, which zotero-mcp is used, what waits to be pushed, what was pushed."""
        import requests

        from . import download_jobs
        from .config import load_config
        from .zotero import discover_zotero_mcp
        try:
            running = requests.get("http://127.0.0.1:23119/connector/ping", timeout=2,
                                   proxies={"http": None, "https": None}).status_code == 200
        except requests.RequestException:
            running = False
        found = discover_zotero_mcp(load_config())
        history = download_jobs.zotero_history()
        for h in history:
            _explain(h, "推送", full=True)
        return {"running": running, "mcp": found["python"] if found else None,
                "pending": download_jobs.zotero_pending(), "history": history,
                "pushing": download_jobs.pushing()}

    @app.get("/api/zotero/write-mode")
    def zotero_write_mode() -> dict[str, Any]:
        from .config import load_config
        from .zotero import write_mode
        # Asking costs a zotero-mcp start (2-3 s) and the mode only changes when the
        # user authorizes, so the first good answer is kept until then.
        if "mode" not in _WRITE_MODE:
            r = write_mode(load_config())
            if not r.get("success"):
                return {"mode": None, "local_supported": None}
            _WRITE_MODE.update(mode=r.get("mode"), local_supported=r.get("local_supported"))
        return dict(_WRITE_MODE)

    @app.post("/api/zotero/authorize")
    def zotero_authorize() -> dict[str, Any]:
        """Zotero asks the user (a dialog in the Zotero app); the answer decides the mode."""
        from .config import load_config
        from .zotero import authorize_local_writes
        r = authorize_local_writes(load_config())
        _WRITE_MODE.clear()
        from . import error_text
        ok = bool(r.get("success"))
        return {"mode": r.get("mode"), "ok": ok,
                "message": r.get("message") if ok else error_text.message(r.get("error") or r.get("message"), "授权")}

    @app.post("/api/zotero/check")
    def zotero_check(body: ZoteroCheck) -> dict[str, Any]:
        """The PDF state of every item in a collection, and for problem items a local PDF if one exists."""
        from . import download_jobs
        from .config import load_config
        from .zotero import check_collection
        if not re.fullmatch(r"[A-Z0-9]{8}", body.key):
            raise HTTPException(400, "bad collection key")
        r = check_collection(body.key, load_config())
        if not r.get("success"):
            raise _fail(503, r.get("error"), "检查附件", "Zotero is not reachable")
        for it in r["items"]:
            pdf = download_jobs.find_local_pdf(it["doi"]) if it["state"] != "ok" and it["doi"] else None
            it["local_pdf"] = str(pdf) if pdf else None
        return {"items": r["items"]}

    @app.post("/api/zotero/repair")
    def zotero_repair(body: ZoteroRepair) -> dict[str, Any]:
        """Repair one item: trash its empty attachments, attach the local PDF found for its DOI."""
        from . import download_jobs
        from .config import load_config
        from .zotero import repair_attachment
        if not re.fullmatch(r"[A-Z0-9]{8}", body.item_key) or any(not re.fullmatch(r"[A-Z0-9]{8}", k) for k in body.trash):
            raise HTTPException(400, "bad item key")
        pdf = download_jobs.find_local_pdf(body.doi or "") if body.attach else None  # never a path from the client
        if body.attach and not pdf:
            raise _fail(404, "no local PDF", "重新附加", "")
        r = repair_attachment(body.item_key, pdf, body.trash, load_config())
        if not r.get("success"):
            raise _fail(502, r.get("error"), "重新附加", "repair did not complete")
        return r

    @app.get("/api/zotero/collections")
    def zotero_collections() -> list[dict[str, Any]]:
        """The library's collections as '/'-paths (read through zotero-mcp)."""
        from .config import load_config
        from .zotero import zotero_collections as read
        r = read(load_config())
        if not r.get("success"):
            raise _fail(503, r.get("error"), "读取分类", "Zotero is not reachable")
        return r["collections"]

    @app.post("/api/downloads")
    def download_start(body: NewDownload) -> dict[str, Any]:
        from . import download_jobs
        try:
            return download_jobs.start([p.model_dump() for p in body.papers], body.title, body.session,
                                       body.zotero_target)
        except download_jobs.Busy as exc:
            raise HTTPException(409, "已有下载任务在进行，完成后再开始新的下载") from exc
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.post("/api/downloads/cancel")
    def download_cancel() -> dict[str, Any]:
        from . import download_jobs
        return {"cancelled": download_jobs.cancel()}

    @app.post("/api/open")
    def open_path(body: OpenPath) -> dict[str, Any]:
        """Open a downloaded PDF or a job's folder with the system's default program.

        Only paths a download job recorded (its files and output folder) and
        the configured save folder are opened, never an arbitrary path:
        opening runs whatever handles it.
        """
        from pathlib import Path

        from . import download_jobs
        from .config import load_config
        target = Path(body.path).resolve()
        allowed = set()
        if load_config().get("output_dir"):
            allowed.add(Path(load_config()["output_dir"]).expanduser().resolve())
        for j in download_jobs.list_jobs(200):
            d = download_jobs.get(j["job"])
            if d.get("output_dir"):
                allowed.add(Path(d["output_dir"]).resolve())
            allowed |= {Path(i["file"]).resolve() for i in d["items"] if i.get("file")}
        ok_file = target in allowed and target.suffix.lower() == ".pdf" and target.is_file()
        ok_dir = target.is_dir() and (target in allowed or any(target == a.parent for a in allowed if a.suffix))
        if not (ok_file or ok_dir):
            raise HTTPException(403, "only downloaded PDFs and their folders can be opened")
        _open_with_system(target)
        return {"opened": str(target)}

    return app


def _open_with_system(path) -> None:
    import os
    import subprocess
    if os.name == "nt":
        os.startfile(str(path))  # noqa: S606 - a vetted PDF or folder
    else:
        subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", str(path)])


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="LitLoop desktop app backend (127.0.0.1 only)")
    ap.add_argument("--token", required=True, help="random token every request must carry")
    ap.add_argument("--port", type=int, default=0, help="0 = pick a free port")
    args = ap.parse_args(argv)
    import os

    import uvicorn
    # The app shows download progress itself; no floating progress bar next to it.
    os.environ.setdefault("LITLOOP_PROGRESS_BAR", "0")
    port = args.port or _free_port()
    print(json.dumps({"litloop_app_server": 1, "port": port}), flush=True)
    uvicorn.run(create_app(args.token), host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":
    main(sys.argv[1:])
