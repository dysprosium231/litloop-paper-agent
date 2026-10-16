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


class Decision(BaseModel):
    doi: str | None = None
    title: str | None = None
    decision: str | None = None  # "selected", "rejected" or None


def _record_path(session: str):
    if not _SESSION_ID.match(session) or session.startswith("."):
        raise HTTPException(400, "bad session id")
    p = screening.screening_dir() / f"{session}.json"
    if not p.exists():
        raise HTTPException(404, f"no session {session}")
    return p


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
        with store.update(_record_path(session), indent=2) as data:
            item = _find(data["items"], body.doi, body.title)
            if body.relevance is None or body.relevance == item["relevance"]:
                item.pop("user_relevance", None)
            else:
                item["user_relevance"] = body.relevance
            edited = sum(1 for it in data["items"] if it.get("user_relevance"))
        return {"item": item, "edited": edited}

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

    return app


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="LitLoop desktop app backend (127.0.0.1 only)")
    ap.add_argument("--token", required=True, help="random token every request must carry")
    ap.add_argument("--port", type=int, default=0, help="0 = pick a free port")
    args = ap.parse_args(argv)
    import uvicorn
    port = args.port or _free_port()
    print(json.dumps({"litloop_app_server": 1, "port": port}), flush=True)
    uvicorn.run(create_app(args.token), host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":
    main(sys.argv[1:])
