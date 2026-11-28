"""Local HTTP backend for the LitLoop desktop app.

The Tauri shell starts this as a child process:

    python -m litloop.desktop.server --token <random>

It listens on 127.0.0.1 only, on a free port it picks itself, and prints one
line to stdout before serving, so the shell knows where to connect:

    {"litloop_app_server": 1, "port": 51234}

Every request must carry the token (header ``X-LitLoop-Token``), so another
web page in the user's browser cannot call it. Data comes from the files
described in docs/APP_DATA.md, read and written through ``store``.
"""

from __future__ import annotations

import argparse
import inspect
import json
import re
import secrets
import socket
import sys
import time
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

from .. import __version__, db, store
from ..search import screening

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


class RouteBenchmarkRequest(BaseModel):
    proxy_url: str = ""
    is_campus: bool | None = None
    timeout: float = 6.0


class HostProxyApplyRequest(BaseModel):
    proxy_url: str = ""
    hosts: list[str] = []


class BuiltinPreset(BaseModel):
    preset: str


class Paper(BaseModel):
    doi: str
    title: str | None = None
    year: int | None = None
    journal: str | None = None


class SkipPaper(BaseModel):
    doi: str


class StartQueue(BaseModel):
    publisher: str


class NewSearch(BaseModel):
    """The app's search form; agent_runner.build_prompt turns it into the message."""
    question: str
    criteria: str = ""
    mode: str = "quick"
    limit: int = 20
    batches: int = 0
    year_from: int | None = None
    year_to: int | None = None
    max_cas_zone: int | None = None
    max_jcr_quartile: int | None = None
    exclude_warning: bool = False
    seeds: str = ""
    seed_collection: str = ""
    planned_queries: list[str] = []
    planned_core_terms: list[str] = []


class ResumeSearch(BaseModel):
    batches: int = 0
    force_new: bool = False


class NewDownload(BaseModel):
    papers: list[Paper]
    title: str
    session: str | None = None
    zotero_target: str | None = None


class PastedList(BaseModel):
    text: str


class ReferenceLines(BaseModel):
    lines: list[str]


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


class Onboarding(BaseModel):
    done: bool = True


class GlossaryEntry(BaseModel):
    terms: list[str] | None = None
    note: str | None = None
    confirm: bool = False  # a suggestion becomes an entry in use


class BuiltinSwitch(BaseModel):
    id: str  # "element:<symbol>" or "spelling:<American word>"
    enabled: bool


class Translate(BaseModel):
    doi: str | None = None
    title: str | None = None


class Export(BaseModel):
    format: str = "bibtex"   # bibtex | ris | doi
    which: str = "selected"  # selected | high


class DualReviewCompareRequest(BaseModel):
    decisions: list[dict[str, Any]] = []
    raw_json: str = ""


class Watch(BaseModel):
    session: str
    query: str = ""


class WatchMergeRequest(BaseModel):
    papers: list[dict[str, Any]] = []
    score: bool = True


class Decision(BaseModel):
    doi: str | None = None
    title: str | None = None
    decision: str | None = None  # "selected", "rejected" or None
    reason: str | None = None  # why, when rejecting; optional


class BatchOp(BaseModel):
    doi: str | None = None
    title: str | None = None
    relevance: str | None = None
    decision: str | None = None
    reject_reason: str | None = None
    clear_user_relevance: bool = False
    delete_if_unrated: bool = False


class BatchUpdate(BaseModel):
    ops: list[BatchOp]


class ChatStreamRequest(BaseModel):
    message: str
    view: dict[str, Any] | None = None
    model: str | None = None
    reasoning_effort: str | None = None


class ChatTruncateRequest(BaseModel):
    from_message_id: int


class ActionConfirmRequest(BaseModel):
    session_id: str
    message_id: int
    action_id: str
    params: dict[str, Any] | None = None


class ActionCancelRequest(BaseModel):
    session_id: str
    message_id: int
    action_id: str


class SteeringNoteRequest(BaseModel):
    note: str


class ClientLogRequest(BaseModel):
    level: str = "error"
    source: str = "frontend"
    message: str
    stack: str = ""
    context: str = ""


_OPENALEX: dict[str, Any] = {"at": 0.0, "data": None}  # see /api/quota/openalex
_APP_UPDATE: dict[str, Any] = {"at": 0.0, "data": None}  # see /api/app-update
_APP_UPDATE_URL = "https://api.github.com/repos/dysprosium231/litloop-paper-agent/releases/latest"
_WRITE_MODE: dict[str, Any] = {}  # zotero-mcp's write mode, cached until the user authorizes


def _parse_version_tuple(version: str) -> tuple[int, ...] | None:
    """Extract numeric version parts (e.g. 'v1.18.3' -> (1, 18, 3))."""
    cleaned = version.strip().lstrip("vV")
    if not cleaned:
        return None
    parts: list[int] = []
    for piece in re.split(r"[.+_-]", cleaned):
        if piece.isdigit():
            parts.append(int(piece))
        else:
            m = re.match(r"^(\d+)", piece)
            if m:
                parts.append(int(m.group(1)))
            break
    return tuple(parts) if parts else None


def _explain(d: dict[str, Any] | None, action: str = "下载", full: bool = False) -> None:
    """Add ``error_text`` (docs/UI_COPY.md section 7) next to a raw ``error``; the raw text stays for the tooltip."""
    from . import error_text
    if d and d.get("error"):
        d["error_text"] = error_text.message(d["error"], action) if full else error_text.reason(d["error"])
        if not full:
            hint = error_text.next_step(d["error"])
            if hint:
                d["error_hint"] = hint
        act = error_text.action_for(d["error"])
        if act:
            d["error_action"] = act


def _fail(status: int, raw: str | None, action: str, fallback: str) -> HTTPException:
    from . import error_text
    return HTTPException(status, error_text.message(raw or fallback, action))


def _record_path(session: str) -> str:
    if not _SESSION_ID.match(session) or session.startswith("."):
        raise HTTPException(400, "无法打开检索会话：会话编号不正确")
    if not db.session_exists(session):
        raise HTTPException(404, "无法打开检索会话：此会话已不存在")
    return session


def _chat_session_id(session_id: str) -> str:
    if session_id == "__draft__":
        return session_id
    return _record_path(session_id)


def _find(items: list[dict[str, Any]], doi: str | None, title: str | None) -> dict[str, Any]:
    key = screening._key({"doi": doi, "title": title})
    for it in items:
        if screening._key(it) == key:
            return it
    raise HTTPException(404, "此检索会话中没有这篇文献")


def create_app(token: str) -> FastAPI:
    app = FastAPI(title="LitLoop app backend", version=__version__, docs_url=None, redoc_url=None)
    # WebView2 158+ treats tauri.localhost -> 127.0.0.1 as a local-network request
    # and sends a Private Network Access preflight first; refusing it (Starlette's
    # default, 400) made every call fail as "无法连接后台" (2026-11-23).
    has_pna = "allow_private_network" in inspect.signature(CORSMiddleware.__init__).parameters
    cors_kwargs: dict[str, Any] = {
        "allow_origins": ORIGINS,
        "allow_methods": ["GET", "POST", "DELETE"],
        "allow_headers": ["X-LitLoop-Token", "Content-Type"],
    }
    if has_pna:
        cors_kwargs["allow_private_network"] = True
    app.add_middleware(CORSMiddleware, **cors_kwargs)

    def _with_cors(request: Request, response: JSONResponse) -> JSONResponse:
        origin = request.headers.get("origin")
        if origin and origin in ORIGINS:
            response.headers["Access-Control-Allow-Origin"] = origin
            response.headers["Vary"] = "Origin"
        if not has_pna and request.headers.get("access-control-request-private-network") == "true":
            response.headers["Access-Control-Allow-Private-Network"] = "true"
        return response

    @app.middleware("http")
    async def require_token(request: Request, call_next):
        if request.method != "OPTIONS" and not secrets.compare_digest(
                request.headers.get("X-LitLoop-Token", ""), token):
            return _with_cors(request, JSONResponse({"detail": "missing or wrong token"}, status_code=401))
        if request.method == "OPTIONS":
            response = await call_next(request)
            if not has_pna and request.headers.get("access-control-request-private-network") == "true":
                response.headers["Access-Control-Allow-Private-Network"] = "true"
            return response

        from ..log import get_logger
        app_log = get_logger()
        t0 = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception as exc:
            ms = int(round((time.perf_counter() - t0) * 1000))
            app_log.exception("[HTTP] Unhandled error on %s %s (%d ms)", request.method, request.url.path, ms)
            return _with_cors(
                request,
                JSONResponse({"detail": f"后台处理请求出错（{type(exc).__name__}），请查看日志"}, status_code=500),
            )
        ms = int(round((time.perf_counter() - t0) * 1000))
        status = response.status_code
        path = request.url.path
        if status >= 500:
            app_log.error("[HTTP] %s %s -> %d (%d ms)", request.method, path, status, ms)
        elif status >= 400:
            app_log.warning("[HTTP] %s %s -> %d (%d ms)", request.method, path, status, ms)
        elif (request.method in ("POST", "DELETE") and path != "/api/client-log") or ms >= 500:
            app_log.info("[HTTP] %s %s -> %d (%d ms)", request.method, path, status, ms)
        if not has_pna and request.headers.get("access-control-request-private-network") == "true":
            response.headers["Access-Control-Allow-Private-Network"] = "true"
        return response

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        return {"version": __version__, "schema": store.SCHEMA}

    @app.post("/api/client-log")
    def client_log(body: ClientLogRequest) -> dict[str, Any]:
        from ..log import get_logger
        app_log = get_logger()
        lvl = str(body.level or "error").strip().lower()
        src = str(body.source or "frontend").strip()[:40] or "frontend"
        msg = str(body.message or "").strip()[:1000]
        ctx = str(body.context or "").strip()[:500]
        stack = str(body.stack or "").strip()[:3000]
        line = f"[UI:{src}] {msg}" + (f" ({ctx})" if ctx else "")
        if stack:
            line += "\n" + "\n".join(f"  {ln}" for ln in stack.splitlines()[:20])
        if lvl in ("warn", "warning"):
            app_log.warning("%s", line)
        elif lvl == "info":
            app_log.info("%s", line)
        else:
            app_log.error("%s", line)
        return {"logged": True}

    @app.get("/api/sessions")
    def sessions(limit: int = 50) -> list[dict[str, Any]]:
        from ..search import deep as deep_search
        out = screening.list_sessions(limit)
        for s in out:
            s["deep"] = (db.load_summary(s["session"]) is not None) or deep_search.has_pool(s["session"])
        return out

    @app.get("/api/sessions/{session}")
    def session(session: str) -> dict[str, Any]:
        from . import agent_runner
        from ..search import deep as deep_search
        _record_path(session)
        record = db.load_screening(session)
        summary = db.load_summary(session)
        if summary is not None:
            # The file keeps the agent's English notes; the app shows today's, in Chinese.
            from .. import quota
            summary["api_warnings"] = quota.warnings("zh")
        return {
            "record": record,
            "summary": summary,
            "resumable": deep_search.has_pool(session),
            "origin": agent_runner.session_origin(session),
        }

    @app.get("/api/sessions/{session}/progress")
    def progress(session: str) -> dict[str, Any] | None:
        _record_path(session)
        p = store.read_progress(session)
        if p and p.get("error"):
            _explain(p, "检索", full=True)
        return p

    @app.get("/api/sessions/{session}/prisma")
    def prisma_view(session: str) -> dict[str, Any]:
        _record_path(session)
        return screening.prisma(session)

    @app.post("/api/sessions/{session}/rating")
    def rate(session: str, body: Rating) -> dict[str, Any]:
        if body.relevance is not None and body.relevance not in screening.RELEVANCE:
            raise HTTPException(400, f"无法评分：评分只能是{'、'.join(screening.RELEVANCE)}")
        _record_path(session)
        res = db.update_item_rating(session, body.doi, body.title, body.relevance)
        if res is None:
            if body.relevance is None:
                raise HTTPException(404, "此检索会话中没有这篇文献")
            from ..search import deep as deep_search
            try:
                paper = deep_search.batch_paper(session, body.doi, body.title)
            except FileNotFoundError:
                paper = None
            if not paper:
                raise HTTPException(404, "此检索会话中没有这篇文献")
            res = db.update_item_rating(
                session, body.doi, body.title, body.relevance, create_paper=paper
            )
            if res is None:
                raise HTTPException(404, "此检索会话中没有这篇文献")
        return res

    @app.post("/api/sessions/{session}/next")
    def next_round(session: str) -> dict[str, Any]:
        """The app's continue button: serve the next batch here, without the agent (it rates the rest when it is back)."""
        import threading
        import time

        from ..search import deep as deep_search
        _record_path(session)
        if not deep_search.has_pool(session):
            raise HTTPException(400, "无法继续检索：这不是深度检索会话。")
        p = store.read_progress(session)
        if p and p.get("state") == "running" and (p.get("pid") or time.time() - float(p.get("updated") or 0) < 300):
            raise HTTPException(409, "正在生成下一批，完成后再继续。")

        def run() -> None:
            try:
                deep_search.next_batch(session, force=True)
            except Exception:  # the progress file says it failed; the app shows that
                pass
        threading.Thread(target=run, name=f"next-{session}", daemon=True).start()
        return {"started": True}

    @app.post("/api/sessions/{session}/resume")
    def resume_session(session: str, body: ResumeSearch | None = None) -> dict[str, Any]:
        """Resume a deep-search session through the configured agent."""
        import inspect

        from . import agent_runner
        _record_path(session)
        try:
            batches = body.batches if body else 0
            force_new = bool(body.force_new) if body else False
            params = inspect.signature(agent_runner.resume).parameters
            if "force_new" in params:
                return agent_runner.resume(session, batches=batches, force_new=force_new)
            return agent_runner.resume(session, batches=batches)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.get("/api/updates")
    def updates_available() -> list[dict[str, Any]]:
        """Components with a newer version (from the weekly check; a due check starts in the background)."""
        from .. import updates
        return updates.available()

    @app.get("/api/history")
    def history() -> list[dict[str, Any]]:
        """Every screening session with its counts and its final picks (the app's history page)."""
        return screening.history()

    @app.get("/api/watches")
    def watches() -> list[dict[str, Any]]:
        """Saved searches, newest check first (定期追踪)."""
        from ..search import watches as saved
        return saved.list_watches()

    @app.post("/api/watches")
    def add_watch(body: Watch) -> dict[str, Any]:
        """Remember this session's last search so later checks report only new papers."""
        from ..search import watches as saved
        record = screening.load(body.session)
        searches = record.get("searches") or []
        last = searches[-1] if searches else {}
        core = list((last.get("params") or {}).get("core_terms") or [])
        query = body.query or str(last.get("query") or "") or str(record.get("question") or "")
        try:
            return saved.add(record.get("question", ""), query,
                             core, record.get("items") or [], body.session)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.delete("/api/watches/{wid}")
    def delete_watch(wid: str) -> dict[str, Any]:
        from ..search import watches as saved
        if not saved.remove(wid):
            raise HTTPException(404, "没有这个追踪")
        return {"removed": True}

    @app.post("/api/watches/check-due")
    def check_due_watches() -> dict[str, Any]:
        """Check the watches that are older than watch_check_days (the app calls this once on open)."""
        from ..search import watches
        return watches.check_due()

    @app.post("/api/watches/{wid}/check")
    def check_watch(wid: str) -> dict[str, Any]:
        """Run the saved search now; only papers this watch has not seen come back."""
        from ..search import watches as saved
        try:
            return saved.check(wid)
        except ValueError as exc:
            raise HTTPException(404, str(exc)) from exc
        except Exception as exc:  # network, quota, source outage
            raise HTTPException(502, f"无法检查追踪：{exc}") from exc

    @app.post("/api/watches/{wid}/merge")
    def merge_watch(wid: str, body: WatchMergeRequest) -> dict[str, Any]:
        """Merge fresh watch hits back into the originating screening session and score them."""
        from ..search import watches as saved
        try:
            return saved.merge_into_session(wid, body.papers, score=body.score)
        except ValueError as exc:
            msg = str(exc)
            raise HTTPException(404 if msg == "没有这个追踪" else 400, msg) from exc
        except Exception as exc:
            raise HTTPException(502, f"无法并入筛选：{exc}") from exc

    @app.post("/api/sessions/{session}/prisma/export")
    def export_prisma(session: str) -> dict[str, Any]:
        """Write the session's flow counts as Markdown under <output_dir>/exports."""
        from ..citations import session_export as export
        _record_path(session)
        return export.prisma_export(session)

    @app.post("/api/sessions/{session}/reproducibility/export")
    def export_reproducibility(session: str) -> dict[str, Any]:
        """Write the session's full reproducibility archive as JSON under <output_dir>/exports."""
        from ..citations import session_export as export
        _record_path(session)
        return export.reproducibility_export(session)

    @app.post("/api/sessions/{session}/blind-review/export")
    def export_blind_review(session: str) -> dict[str, Any]:
        """Write a blind-review JSON packet under <output_dir>/exports for a second reviewer."""
        from ..citations import session_export as export
        _record_path(session)
        return export.blind_review_export(session)

    @app.post("/api/sessions/{session}/dual-review/compare")
    def compare_dual_review(session: str, body: DualReviewCompareRequest) -> dict[str, Any]:
        """Compare current session decisions with a second reviewer's JSON decisions."""
        _record_path(session)
        rows = list(body.decisions or [])
        if body.raw_json.strip():
            try:
                parsed = json.loads(body.raw_json)
            except ValueError as exc:
                raise HTTPException(400, "无法解析对比文件：请提供合法的 JSON 文件") from exc
            if isinstance(parsed, dict):
                parsed = parsed.get("items") if isinstance(parsed.get("items"), list) else parsed.get("decisions")
            if not isinstance(parsed, list):
                raise HTTPException(400, "无法解析对比文件：JSON 中应包含 items 或 decisions 列表")
            rows = [r for r in parsed if isinstance(r, dict)]
        return screening.compare_reviewers(session, rows)

    @app.post("/api/sessions/{session}/export")
    def export_session(session: str, body: Export) -> dict[str, Any]:
        """Write the session's picks (or its 高 papers) as BibTeX / RIS / a DOI list under <output_dir>/exports."""
        from ..citations import session_export as export
        _record_path(session)
        if body.which not in ("selected", "high"):
            raise HTTPException(400, "无法导出：只能导出待下载或评为高的文献")
        try:
            return export.export(session, body.format, body.which)
        except ValueError as exc:
            raise HTTPException(400, "无法导出：没有可导出的文献" if "no papers" in str(exc) else "无法导出：不支持此格式") from exc

    @app.get("/api/translation")
    def translation() -> dict[str, Any]:
        """What the detail pane needs: who translates, which ratings without asking, whether it can."""
        from . import translate
        from ..config import load_config
        config = load_config()
        name = translate.provider(config)
        return {"provider": name, "auto": screening.translate_wanted(config),
                "ready": translate.is_ready(config)}

    @app.post("/api/sessions/{session}/translate")
    def translate_abstract(session: str, body: Translate) -> dict[str, Any]:
        """Translate one paper's abstract with DeepSeek / DeepL and keep it in the record."""
        from . import translate
        _record_path(session)
        item = _find(db.load_screening(session)["items"], body.doi, body.title)
        if item.get("abstract_zh"):
            return {"abstract_zh": item["abstract_zh"]}
        if not item.get("abstract"):
            raise HTTPException(400, "无法翻译：此文献暂无摘要")
        try:
            text = translate.translate_text(item["abstract"])
        except translate.TranslationError as exc:
            raise HTTPException(502, f"无法翻译：{exc}。") from exc
        if not db.set_item_abstract_zh(session, body.doi, body.title, text):
            raise HTTPException(404, "此检索会话中没有这篇文献")
        return {"abstract_zh": text}

    @app.post("/api/sessions/{session}/decision")
    def decide(session: str, body: Decision) -> dict[str, Any]:
        if body.decision not in (None, "selected", "rejected"):
            raise HTTPException(400, "无法保存：只能加入下载或排除")
        _record_path(session)
        res = db.update_item_decision(session, body.doi, body.title, body.decision, body.reason)
        if res is None:
            raise HTTPException(404, "此检索会话中没有这篇文献")
        return res

    @app.post("/api/sessions/{session}/batch")
    def batch_update(session: str, body: BatchUpdate) -> dict[str, Any]:
        _record_path(session)
        if not body.ops:
            raise HTTPException(400, "无法批量保存：未选择文献")
        for op in body.ops:
            if "relevance" in op.model_fields_set and op.relevance is not None and op.relevance not in screening.RELEVANCE:
                raise HTTPException(400, f"无法评分：评分只能是{'、'.join(screening.RELEVANCE)}")
            if "decision" in op.model_fields_set and op.decision not in (None, "selected", "rejected"):
                raise HTTPException(400, "无法保存：只能加入下载或排除")

        record = db.load_screening(session)
        existing_keys = {screening._key(it) for it in record["items"]}
        create_papers: dict[str, dict[str, Any]] = {}
        for op in body.ops:
            if op.delete_if_unrated or op.clear_user_relevance:
                continue
            if "relevance" not in op.model_fields_set or op.relevance is None:
                continue
            key = screening._key({"doi": op.doi, "title": op.title})
            if key in existing_keys or key in create_papers:
                continue
            from ..search import deep as deep_search
            try:
                paper = deep_search.batch_paper(session, op.doi, op.title)
            except FileNotFoundError:
                paper = None
            if paper:
                create_papers[key] = paper

        return db.batch_update_items(session, body.ops, create_papers=create_papers)

    @app.get("/api/glossary")
    def glossary_list() -> dict[str, Any]:
        from ..terms import glossary
        return {"active": glossary.entries("active"), "suggested": glossary.entries("suggested")}

    @app.post("/api/glossary")
    def glossary_add(body: GlossaryEntry) -> dict[str, Any]:
        from ..terms import glossary
        try:
            return glossary.add(body.terms or [], note=body.note or "")
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.get("/api/glossary/builtin")
    def glossary_builtin(q: str = "") -> dict[str, Any]:
        from ..terms import glossary
        return glossary.builtin(q)

    @app.post("/api/glossary/builtin")
    def glossary_builtin_switch(body: BuiltinSwitch) -> dict[str, Any]:
        from ..terms import glossary
        try:
            glossary.set_builtin(body.id, body.enabled)
        except KeyError as exc:
            raise HTTPException(404, str(exc).strip("'\"")) from exc
        return {"id": body.id, "disabled": not body.enabled}

    @app.post("/api/glossary/{entry_id}")
    def glossary_update(entry_id: str, body: GlossaryEntry) -> dict[str, Any]:
        from ..terms import glossary
        try:
            return glossary.update(entry_id, terms=body.terms, note=body.note, confirm=body.confirm)
        except KeyError as exc:
            raise HTTPException(404, str(exc).strip("'\"")) from exc
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.post("/api/glossary/{entry_id}/delete")
    def glossary_delete(entry_id: str) -> dict[str, Any]:
        """POST: the app's CORS allows GET and POST only. A deleted suggestion is not suggested again."""
        from ..terms import glossary
        try:
            glossary.delete(entry_id)
        except KeyError as exc:
            raise HTTPException(404, str(exc).strip("'\"")) from exc
        return {"deleted": entry_id}

    @app.get("/api/quota")
    def quota_summary() -> dict[str, Any]:
        from .. import quota
        return quota.summary()

    @app.get("/api/quota/openalex")
    def quota_openalex(fresh: bool = False) -> dict[str, Any] | None:
        """OpenAlex's own count (costs no credits, but takes seconds): kept two minutes."""
        import time

        from .. import quota
        if fresh or time.time() - _OPENALEX["at"] > 120:
            _OPENALEX.update(at=time.time(), data=quota.openalex_live())
        return _OPENALEX["data"]

    @app.get("/api/settings")
    def settings_list() -> list[dict[str, Any]]:
        from . import settings as app_settings
        return app_settings.settings()

    @app.post("/api/settings")
    def settings_update(body: Setting) -> dict[str, Any]:
        from . import settings as app_settings
        try:
            return app_settings.update(body.key, body.value)
        except KeyError as exc:
            raise HTTPException(400, "无法保存：此项暂不支持在 app 中修改") from exc
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.get("/api/builtin-agent/presets")
    def builtin_agent_presets() -> list[dict[str, Any]]:
        from ..agents import PROVIDER_PRESETS
        return PROVIDER_PRESETS

    @app.post("/api/builtin-agent/preset")
    def builtin_agent_apply_preset(body: BuiltinPreset) -> list[dict[str, Any]]:
        from .. import config
        from ..agents import get_preset
        from . import settings as app_settings

        preset = get_preset(body.preset)
        if preset is None:
            raise HTTPException(400, "不支持这个服务商预设")
        config.update_config("builtin_agent_preset", str(preset["id"]))
        config.update_config("builtin_agent_provider", str(preset["protocol"]))
        config.update_config("builtin_agent_base_url", str(preset.get("base_url") or ""))
        config.update_config("builtin_agent_model", str(preset.get("model") or ""))
        config.update_config("builtin_agent_plan_model", str(preset.get("plan_model") or ""))
        config.update_config("builtin_agent_rate_model", str(preset.get("rate_model") or ""))
        config.update_config("agent_backend", "builtin")
        group = next(g for g in app_settings.settings() if g["id"] == "builtin_agent")
        return group["fields"]

    @app.get("/api/onboarding")
    def onboarding() -> dict[str, Any]:
        from . import settings as app_settings
        return app_settings.onboarding()

    @app.post("/api/onboarding")
    def onboarding_done(body: Onboarding) -> dict[str, Any]:
        """done=true: finished or skipped; done=false: show the guide again (settings page)."""
        from . import settings as app_settings
        return app_settings.finish_onboarding(body.done)

    @app.post("/api/settings/test/{group}")
    def settings_test(group: str) -> dict[str, Any]:
        from . import settings as app_settings
        try:
            return app_settings.run_test(group)
        except KeyError as exc:
            raise HTTPException(404, "无法完成检测：此分组没有检测项") from exc

    @app.post("/api/network/route-benchmark")
    def network_route_benchmark(req: RouteBenchmarkRequest) -> dict[str, Any]:
        from ..institutional import proxy_check
        return proxy_check.probe_academic_routes(
            proxy_url=req.proxy_url,
            is_campus=req.is_campus,
            timeout=max(1.0, min(req.timeout, 15.0)),
        )

    @app.post("/api/network/host-proxy")
    def network_host_proxy_apply(req: HostProxyApplyRequest) -> dict[str, Any]:
        import json
        from .. import config as _config
        cleaned: list[str] = []
        seen: set[str] = set()
        for raw in req.hosts:
            host = str(raw).strip().lower()
            if host and host not in seen:
                seen.add(host)
                cleaned.append(host)
        _config.update_config("host_proxy_hosts", json.dumps(cleaned, ensure_ascii=False))
        proxy_url = req.proxy_url.strip()
        if proxy_url:
            _config.update_config("host_proxy_url", proxy_url)
        cfg = _config.load_config()
        return {
            "ok": True,
            "proxy_url": str(cfg.get("host_proxy_url") or ""),
            "hosts": list(cfg.get("host_proxy_hosts") or []),
        }

    @app.get("/api/harnesses")
    def harnesses_list() -> list[dict[str, Any]]:
        from . import harnesses
        return harnesses.detect_harnesses()

    @app.post("/api/harnesses/{harness_id}/sync")
    def harnesses_sync(harness_id: str) -> dict[str, Any]:
        from . import harnesses
        try:
            return harnesses.sync_harness(harness_id)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.post("/api/harnesses/{harness_id}/select")
    def harnesses_select(harness_id: str) -> list[dict[str, Any]]:
        from . import harnesses
        try:
            return harnesses.select_harness(harness_id)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.get("/api/components/{key}/status")
    def component_status(key: str) -> dict[str, Any]:
        """Return the current install progress and state for an optional component."""
        from .. import optional
        try:
            return optional.install_status(key)
        except KeyError as exc:
            raise HTTPException(404, "没有这个可选组件") from exc

    @app.post("/api/components/{key}/install")
    def component_install(key: str) -> dict[str, Any]:
        """Download an optional component (the layout model) into the data dir."""
        from .. import optional
        if key not in optional.COMPONENTS:
            raise HTTPException(404, "没有这个组件")
        from ..config import load_config
        try:
            return optional.install(key, index_url=str(load_config().get("package_index_url") or ""))
        except KeyError as exc:
            raise HTTPException(404, "没有这个组件") from exc

    @app.get("/api/app-update")
    def app_update(fresh: bool = False) -> dict[str, Any]:
        """Check GitHub Releases for a newer LitLoop version (cached 10 minutes)."""
        now = time.time()
        cached = _APP_UPDATE.get("data")
        if not fresh and cached is not None and now - float(_APP_UPDATE.get("at") or 0.0) <= 600:
            return cached

        base: dict[str, Any] = {
            "current": __version__,
            "latest": "",
            "available": False,
            "url": "",
            "notes": "",
            "error": "",
        }
        import requests
        try:
            resp = requests.get(
                _APP_UPDATE_URL,
                headers={
                    "Accept": "application/vnd.github+json",
                    "User-Agent": f"LitLoop/{__version__}",
                },
                timeout=5,
            )
        except requests.Timeout:
            return {**base, "error": "无法检查更新：请求超时"}
        except requests.RequestException:
            return {**base, "error": "无法检查更新：网络连接失败，请检查网络或代理设置"}

        if resp.status_code != 200:
            return {**base, "error": f"无法检查更新：GitHub 返回 HTTP {resp.status_code}"}

        try:
            payload = resp.json()
        except ValueError:
            return {**base, "error": "无法检查更新：返回数据格式异常"}
        if not isinstance(payload, dict):
            return {**base, "error": "无法检查更新：返回数据格式异常"}

        tag = str(payload.get("tag_name") or "").strip()
        latest = tag.lstrip("vV")
        if not latest:
            return {**base, "error": "无法检查更新：未找到版本号"}

        cur_ver = _parse_version_tuple(__version__)
        lat_ver = _parse_version_tuple(latest)
        if cur_ver is not None and lat_ver is not None:
            available = lat_ver > cur_ver
        else:
            available = latest != __version__

        result = {
            "current": __version__,
            "latest": latest,
            "available": available,
            "url": str(payload.get("html_url") or ""),
            "notes": str(payload.get("body") or ""),
            "error": "",
        }
        _APP_UPDATE.update(at=time.time(), data=result)
        return result

    @app.get("/api/data-dir")
    def data_dir() -> dict[str, Any]:
        """Where the papers, sessions and settings live (not inside the install folder)."""
        from ..config import DATA_DIR
        return {"path": str(DATA_DIR), "exists": DATA_DIR.exists()}

    @app.post("/api/diagnostics")
    def diagnostics_export() -> dict[str, Any]:
        """Write the diagnostic bundle and return where it landed."""
        from ..diagnostics import write_bundle
        try:
            path = write_bundle()
        except OSError as exc:
            raise HTTPException(500, f"无法写诊断包：{exc}") from exc
        return {"path": str(path), "size": path.stat().st_size if path.exists() else 0}

    # Downloads: jobs started from the app (download_jobs), one at a time.
    @app.get("/api/downloads")
    def downloads() -> dict[str, Any]:
        from . import download_jobs
        from ..download import progress as progress_reporter
        from ..config import load_config
        live = progress_reporter.read_state() if download_jobs.running() else None
        # A download the agent or the command line runs: shown on the page too.
        external = None if download_jobs.running() else progress_reporter.running_elsewhere()
        return {"jobs": download_jobs.list_jobs(), "running": download_jobs.running(), "live": live,
                "external": external,
                "pushing": download_jobs.pushing(), "output_dir": str(load_config().get("output_dir") or "")}

    @app.get("/api/downloads/notice")
    def downloads_notice() -> dict[str, Any] | None:
        """Polled on every page for the system notifications (the app's own jobs and the agent's)."""
        from ..download import progress as progress_reporter
        return progress_reporter.latest_notice()

    @app.get("/api/screening/notice")
    def screening_notice() -> dict[str, Any] | None:
        """Polled on every page for the system notifications (a deep-search round finished)."""
        return store.latest_round_notice()

    @app.get("/api/downloads/pending")
    def downloads_pending() -> list[dict[str, Any]]:
        """Papers picked 选中下载 in screening that no job has downloaded yet (or holds in a queue), by session."""
        from . import download_jobs
        got = {k for d in download_jobs.downloaded_dois() | download_jobs.queued_dois()
               for k in download_jobs.same_paper(d)}
        out = []
        for s in db.pending_selected_by_session():  # newest first: a paper picked twice shows in the newest
            papers = []
            for it in s["papers"]:
                keys = download_jobs.same_paper(it["doi"])
                if keys & got:
                    continue
                got |= keys
                papers.append(it)
            if papers:
                out.append({"session": s["session"], "question": s["question"], "papers": papers})
        return out

    @app.get("/api/downloads/{job}")
    def download_job(job: str) -> dict[str, Any]:
        from . import download_jobs
        if not re.fullmatch(r"[\w-]{1,40}", job):
            raise HTTPException(400, "无法打开下载任务：任务编号不正确")
        try:
            d = download_jobs.get(job)
        except FileNotFoundError as exc:
            raise HTTPException(404, "无法打开下载任务：此任务已不存在") from exc
        d["zotero_default"] = download_jobs.default_collection(d["title"])
        _explain(d, full=True)
        _explain(d.get("zotero"), "推送", full=True)
        for it in d.get("items") or []:
            _explain(it)
            _explain(it.get("zotero"), "推送")
        return d

    @app.post("/api/downloads/{job}/queue")
    def download_queue(job: str, body: StartQueue) -> dict[str, Any]:
        """Start one publisher's queue of a long list (download_jobs.start_queue)."""
        from . import download_jobs
        if not re.fullmatch(r"[\w-]{1,40}", job):
            raise HTTPException(400, "无法开始：任务编号不正确")
        try:
            return download_jobs.start_queue(job, body.publisher)
        except FileNotFoundError as exc:
            raise HTTPException(404, "无法开始：此下载任务已不存在") from exc
        except download_jobs.Busy as exc:
            raise HTTPException(409, "已有下载任务在进行，完成后再开始新的下载") from exc
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.post("/api/downloads/{job}/zotero")
    def download_push(job: str, body: ZoteroPush) -> dict[str, Any]:
        from . import download_jobs
        if not re.fullmatch(r"[\w-]{1,40}", job):
            raise HTTPException(400, "无法推送：任务编号不正确")
        try:
            return download_jobs.push_zotero(job, body.collection, body.tags)
        except FileNotFoundError as exc:
            raise HTTPException(404, "无法推送：此下载任务已不存在") from exc
        except download_jobs.Busy as exc:
            raise HTTPException(409, "已有推送任务在进行，完成后再开始新的推送") from exc
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.get("/api/zotero")
    def zotero_overview() -> dict[str, Any]:
        """Is Zotero up, which zotero-mcp is used, what waits to be pushed, what was pushed."""
        import requests

        from . import download_jobs
        from ..config import load_config
        from ..zotero.client import discover_zotero_mcp
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
        from ..config import load_config
        from ..zotero.client import write_mode
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
        from ..config import load_config
        from ..zotero.client import authorize_local_writes
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
        from ..config import load_config
        from ..zotero.client import check_collection
        if not re.fullmatch(r"[A-Z0-9]{8}", body.key):
            raise HTTPException(400, "无法检查附件：分类编号不正确")
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
        from ..config import load_config
        from ..zotero.client import repair_attachment
        if not re.fullmatch(r"[A-Z0-9]{8}", body.item_key) or any(not re.fullmatch(r"[A-Z0-9]{8}", k) for k in body.trash):
            raise HTTPException(400, "无法重新附加：条目编号不正确")
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
        from ..config import load_config
        from ..zotero.client import zotero_collections as read
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

    @app.post("/api/searches/plan")
    def search_plan(body: NewSearch) -> dict[str, Any]:
        from ..agents.runner import plan_search
        from ..config import load_config
        try:
            return plan_search(body.model_dump(), config=load_config())
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        except Exception as exc:
            raise HTTPException(502, f"无法生成检索方案：{exc}") from exc

    @app.post("/api/searches")
    def search_start(body: NewSearch) -> dict[str, Any]:
        from . import agent_runner
        try:
            return agent_runner.start(body.model_dump())
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.get("/api/searches")
    def search_list() -> list[dict[str, Any]]:
        from . import agent_runner
        return agent_runner.list_runs()

    @app.get("/api/searches/{run}")
    def search_get(run: str) -> dict[str, Any]:
        from . import agent_runner
        try:
            d = agent_runner.get(run)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        if not d:
            raise HTTPException(404, "没有这次检索")
        return d

    @app.post("/api/searches/{run}/cancel")
    def search_cancel(run: str) -> dict[str, Any]:
        from . import agent_runner
        return {"cancelled": agent_runner.cancel(run)}

    @app.get("/api/agent/chat/{session_id}")
    def agent_chat_get(session_id: str) -> dict[str, Any]:
        from ..agents import chat_agent
        from ..config import load_config

        sid = _chat_session_id(session_id)
        return {
            "session_id": sid,
            "messages": db.list_agent_messages(sid),
            "steering_notes": db.list_steering_notes(sid, active_only=True),
            "model_info": chat_agent.chat_model_info(load_config()),
        }

    @app.post("/api/agent/chat/{session_id}/stream")
    def agent_chat_stream(session_id: str, body: ChatStreamRequest) -> StreamingResponse:
        from ..agents import chat_agent
        from ..agents.client import REASONING_EFFORTS
        from ..config import load_config

        sid = _chat_session_id(session_id)
        text = body.message.strip()
        if not text:
            raise HTTPException(400, "发送的消息不能为空")
        cfg = load_config()
        if body.model and body.model.strip():
            cfg["builtin_agent_model"] = body.model.strip()
        if body.reasoning_effort and body.reasoning_effort.strip().lower() in REASONING_EFFORTS:
            cfg["builtin_agent_reasoning_effort"] = body.reasoning_effort.strip().lower()

        def event_stream():
            for ev in chat_agent.stream_chat_turn(sid, text, view=body.view, config=cfg):
                yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.post("/api/agent/chat/{session_id}/clear")
    def agent_chat_clear(session_id: str) -> dict[str, Any]:
        sid = _chat_session_id(session_id)
        return {"cleared": db.clear_agent_messages(sid)}

    @app.post("/api/agent/chat/{session_id}/truncate")
    def agent_chat_truncate(session_id: str, body: ChatTruncateRequest) -> dict[str, Any]:
        sid = _chat_session_id(session_id)
        if body.from_message_id <= 0:
            raise HTTPException(400, "消息编号不正确")
        return {"deleted": db.truncate_agent_messages_from(sid, body.from_message_id)}

    @app.post("/api/agent/actions/confirm")
    def agent_action_confirm(body: ActionConfirmRequest) -> dict[str, Any]:
        from ..agents import chat_agent

        sid = _chat_session_id(body.session_id)
        try:
            return chat_agent.execute_action(
                sid,
                body.message_id,
                body.action_id,
                edited_params=body.params,
            )
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from exc
        except ValueError as exc:
            msg = str(exc)
            status = 404 if any(k in msg for k in ("不存在", "未找到", "not found")) else 400
            raise HTTPException(status, msg) from exc

    @app.post("/api/agent/actions/cancel")
    def agent_action_cancel(body: ActionCancelRequest) -> dict[str, Any]:
        _chat_session_id(body.session_id)
        updated = db.update_agent_action(body.message_id, body.action_id, "cancelled")
        if updated is None:
            raise HTTPException(404, "未找到对应的待确认操作")
        return {"action": updated}

    @app.post("/api/agent/steering/{session_id}")
    def agent_steering_add(session_id: str, body: SteeringNoteRequest) -> dict[str, Any]:
        sid = _chat_session_id(session_id)
        note = body.note.strip()
        if not note:
            raise HTTPException(400, "补充筛选要求不能为空")
        return db.add_steering_note(sid, note)

    @app.delete("/api/agent/steering/{note_id}")
    def agent_steering_delete(note_id: int) -> dict[str, Any]:
        if not db.remove_steering_note(note_id):
            raise HTTPException(404, "未找到该补充筛选要求")
        return {"removed": True}

    @app.post("/api/downloads/parse")
    def downloads_parse(body: PastedList) -> dict[str, Any]:
        """DOIs, doi.org links and arXiv ids in pasted text, for a download list.

        ``downloaded``: those whose PDF is still in the save folder; the job skips
        exactly these (2026-10-20: the job history counted too, so the app had to
        say "downloaded before, skipped if the file is still there")."""
        from . import download_jobs
        from ..identifiers import extract_identifiers
        dois, unrecognized = extract_identifiers(body.text[:200_000])
        got = set(download_jobs.local_files())
        return {"papers": [{"doi": d} for d in dois[:2000]], "unrecognized": unrecognized[:100],
                "unrecognized_count": len(unrecognized), "too_many": len(dois) > 2000,
                "downloaded": [d for d in dois if download_jobs.same_paper(d) & got]}

    @app.post("/api/downloads/resolve")
    def downloads_resolve(body: ReferenceLines) -> dict[str, Any]:
        """Pasted lines with no DOI (reference-list entries, titles): look each up
        on Crossref. Only when the user asks (a button), at most 100 lines."""
        from . import download_jobs
        from ..config import load_config
        from ..search.title_to_doi import resolve_references
        lines = [ln.strip() for ln in body.lines if ln.strip()][:100]
        rows = resolve_references(lines, load_config())
        got = set(download_jobs.local_files())
        for r in rows:
            if r.get("doi"):
                r["downloaded"] = bool(download_jobs.same_paper(r["doi"]) & got)
        return {"results": rows}

    @app.post("/api/downloads/skip")
    def download_skip(body: SkipPaper) -> dict[str, Any]:
        """The user will not log in for this paper: give it up, go on with the next."""
        from ..sources import skip_download
        return {"skipped": skip_download(body.doi)}

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
        from ..config import load_config
        target = Path(body.path).resolve()
        allowed = set()
        if load_config().get("output_dir"):
            allowed.add(Path(load_config()["output_dir"]).expanduser().resolve())
            allowed.add((Path(load_config()["output_dir"]).expanduser() / "exports").resolve())
        for j in download_jobs.list_jobs(200):
            d = download_jobs.get(j["job"])
            if d.get("output_dir"):
                allowed.add(Path(d["output_dir"]).resolve())
            allowed |= {Path(i["file"]).resolve() for i in d["items"] if i.get("file")}
        ok_file = target in allowed and target.suffix.lower() == ".pdf" and target.is_file()
        ok_dir = target.is_dir() and (target in allowed or any(target == a.parent for a in allowed if a.suffix))
        if not (ok_file or ok_dir):
            raise HTTPException(403, "无法打开：只能打开 LitLoop 下载的 PDF 和它们的文件夹")
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
    import os
    import platform
    import uvicorn
    from ..config import DATA_DIR
    from ..log import enable_file_logging, get_logger

    ap = argparse.ArgumentParser(description="LitLoop desktop app backend (127.0.0.1 only)")
    ap.add_argument("--token", required=True, help="random token every request must carry")
    ap.add_argument("--port", type=int, default=0, help="0 = pick a free port")
    args = ap.parse_args(argv)
    log_path = enable_file_logging()
    port = args.port or _free_port()
    get_logger().info(
        "[startup] LitLoop %s | pid=%d | port=%d | python=%s | data_dir=%s | log=%s",
        __version__,
        os.getpid(),
        port,
        platform.python_version(),
        DATA_DIR,
        log_path or "(memory-only)",
    )
    print(json.dumps({"litloop_app_server": 1, "port": port}), flush=True)
    uvicorn.run(create_app(args.token), host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":
    main(sys.argv[1:])
