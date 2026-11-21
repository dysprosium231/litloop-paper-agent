"""Saved searches (定期追踪): run one again and report only papers not seen before.

A watch is a question plus the query that answers it, remembered from a screening
session. Checking one runs the same keyword search, drops every paper already in
that session or in an earlier check, and keeps the rest so the next check starts
from there. Nothing here knows about scheduling; the app decides when to ask.
"""

from __future__ import annotations

import threading
import time
import uuid
from pathlib import Path
from typing import Any

from .. import config, db
from ..identifiers import canonical_doi

MAX_SEEN = 5000    # own value: enough history for years of one topic
NEW_LIMIT = 50     # own value: how many hits one check pulls before dedupe


def path() -> Path:
    """Legacy JSON path, kept for backward compatibility and one-time import."""
    return Path(config.DATA_DIR) / "watches.json"


def _ensure_legacy_imported() -> None:
    legacy = path()
    if legacy.exists():
        db.import_legacy_watches(legacy_path=legacy)


def key(paper: dict[str, Any]) -> str:
    """A stable identity for one paper: its DOI, else its title."""
    doi = canonical_doi(str(paper.get("doi") or ""))
    if doi:
        return f"doi:{doi.lower()}"
    return "title:" + " ".join(str(paper.get("title") or "").lower().split())


def list_watches() -> list[dict[str, Any]]:
    """Newest first; the seen list stays in the database (it can be thousands long)."""
    _ensure_legacy_imported()
    return db.list_watches()


def get(wid: str) -> dict[str, Any] | None:
    _ensure_legacy_imported()
    return db.get_watch(wid)


def add(question: str, query: str, core_terms: list[str] | None = None,
        seen: list[dict[str, Any]] | None = None, session: str = "") -> dict[str, Any]:
    """Remember a search; papers passed in ``seen`` are not reported as new later."""
    _ensure_legacy_imported()
    query = " ".join(str(query or "").split())
    if not query:
        raise ValueError("无法保存追踪：检索式是空的")
    keys = [key(p) for p in seen or []][:MAX_SEEN]
    return db.add_watch(
        wid=uuid.uuid4().hex[:8],
        question=" ".join(str(question or "").split()),
        query=query,
        core_terms=list(core_terms or []),
        seen_keys=keys,
        session=session,
    )


def remove(wid: str) -> bool:
    _ensure_legacy_imported()
    return db.remove_watch(wid)


def check(wid: str, config: dict[str, Any] | None = None) -> dict[str, Any]:
    """Run the saved search once; return only the papers this watch has not seen."""
    from . import quick
    _ensure_legacy_imported()
    watch = db.get_watch(wid)
    if not watch:
        raise ValueError("没有这个追踪")
    hits = quick.search_papers_v2(watch["query"], limit=NEW_LIMIT, sort="date", use_cache=False) or []
    seen = set(watch.get("seen") or [])
    fresh = [p for p in hits if key(p) not in seen]
    now = time.strftime("%Y-%m-%d %H:%M")
    keys = list(watch.get("seen") or []) + [key(x) for x in hits]
    merged_seen = list(dict.fromkeys(keys))[-MAX_SEEN:]
    db.update_watch_check(wid, seen_keys=merged_seen, checked=now, last_new=len(fresh))
    return {"id": wid, "checked": now, "found": len(hits), "new": fresh}


_DUE_LOCK = threading.Lock()


def check_due(config: dict[str, Any] | None = None) -> dict[str, Any]:
    """Check the watches whose last check is older than `watch_check_days`.

    Called once when the app opens, so a followed topic does not need a click;
    the default (7 days) keeps the OpenAlex credits spent on this small. Returns
    the per-watch outcome and how many new papers came back in total.
    """
    from ..config import load_config
    config = config or load_config()
    days = float(config.get("watch_check_days") or 7)
    if not _DUE_LOCK.acquire(blocking=False):
        return {"checked": [], "new_total": 0, "busy": True}
    try:
        _ensure_legacy_imported()
        done: list[dict[str, Any]] = []
        total = 0
        for w in db.due_watches(days):
            try:
                r = check(str(w["id"]), config)
            except Exception as exc:  # one broken search must not stop the rest
                done.append({"id": w["id"], "question": w.get("question") or w.get("query"), "error": str(exc)})
                continue
            done.append({"id": r["id"], "question": w.get("question") or w.get("query"),
                         "found": r["found"], "new": len(r["new"])})
            total += len(r["new"])
        return {"checked": done, "new_total": total}
    finally:
        _DUE_LOCK.release()
