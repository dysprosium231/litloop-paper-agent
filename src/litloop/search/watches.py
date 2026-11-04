"""Saved searches (定期追踪): run one again and report only papers not seen before.

A watch is a question plus the query that answers it, remembered from a screening
session. Checking one runs the same keyword search, drops every paper already in
that session or in an earlier check, and keeps the rest so the next check starts
from there. Nothing here knows about scheduling; the app decides when to ask.
"""

from __future__ import annotations

import time
import uuid
from pathlib import Path
from typing import Any

from .. import store
from ..identifiers import canonical_doi

MAX_SEEN = 5000    # own value: enough history for years of one topic
NEW_LIMIT = 50     # own value: how many hits one check pulls before dedupe


def path() -> Path:
    from ..config import DATA_DIR
    return Path(DATA_DIR) / "watches.json"


def _data() -> dict[str, Any]:
    try:
        data = store.read_json(path())
    except (OSError, ValueError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    data.setdefault("schema", store.SCHEMA)
    data.setdefault("watches", [])
    return data


def key(paper: dict[str, Any]) -> str:
    """A stable identity for one paper: its DOI, else its title."""
    doi = canonical_doi(str(paper.get("doi") or ""))
    if doi:
        return f"doi:{doi.lower()}"
    return "title:" + " ".join(str(paper.get("title") or "").lower().split())


def list_watches() -> list[dict[str, Any]]:
    """Newest first; the seen list stays in the file (it can be thousands long)."""
    out = [{k: v for k, v in w.items() if k != "seen"} | {"seen_count": len(w.get("seen") or [])}
           for w in _data()["watches"]]
    return sorted(out, key=lambda w: str(w.get("checked") or w.get("created") or ""), reverse=True)


def get(wid: str) -> dict[str, Any] | None:
    return next((w for w in _data()["watches"] if w.get("id") == wid), None)


def add(question: str, query: str, core_terms: list[str] | None = None,
        seen: list[dict[str, Any]] | None = None, session: str = "") -> dict[str, Any]:
    """Remember a search; papers passed in ``seen`` are not reported as new later."""
    query = " ".join(str(query or "").split())
    if not query:
        raise ValueError("无法保存追踪：检索式是空的")
    keys = [key(p) for p in seen or []]
    watch = {"id": uuid.uuid4().hex[:8], "question": " ".join(str(question or "").split()),
             "query": query, "core_terms": list(core_terms or []), "session": session,
             "created": time.strftime("%Y-%m-%d %H:%M"), "checked": "", "last_new": 0,
             "seen": keys[:MAX_SEEN]}
    p = path()
    p.parent.mkdir(parents=True, exist_ok=True)
    if not p.exists():
        store.write_json(p, {"schema": store.SCHEMA, "watches": []}, indent=1)
    with store.update(p, indent=1) as data:
        data.setdefault("watches", [])
        data["watches"].append(watch)
    return {k: v for k, v in watch.items() if k != "seen"} | {"seen_count": len(keys)}


def remove(wid: str) -> bool:
    p = path()
    if not p.exists():
        return False
    with store.update(p, indent=1) as data:
        watches = data.setdefault("watches", [])
        left = [w for w in watches if w.get("id") != wid]
        if len(left) == len(watches):
            return False
        data["watches"] = left
    return True


def check(wid: str, config: dict[str, Any] | None = None) -> dict[str, Any]:
    """Run the saved search once; return only the papers this watch has not seen."""
    from . import quick
    watch = get(wid)
    if not watch:
        raise ValueError("没有这个追踪")
    hits = quick.search_papers_v2(watch["query"], limit=NEW_LIMIT, sort="date") or []
    seen = set(watch.get("seen") or [])
    fresh = [p for p in hits if key(p) not in seen]
    now = time.strftime("%Y-%m-%d %H:%M")
    p = path()
    with store.update(p, indent=1) as data:
        for w in data.setdefault("watches", []):
            if w.get("id") == wid:
                keys = list(w.get("seen") or []) + [key(x) for x in hits]
                w["seen"] = list(dict.fromkeys(keys))[-MAX_SEEN:]
                w["checked"] = now
                w["last_new"] = len(fresh)
    return {"id": wid, "checked": now, "found": len(hits), "new": fresh}


_DUE_LOCK = __import__("threading").Lock()


def check_due(config: dict[str, Any] | None = None) -> dict[str, Any]:
    """Check the watches whose last check is older than `watch_check_days`.

    Called once when the app opens, so a followed topic does not need a click;
    the default (7 days) keeps the OpenAlex credits spent on this small. Returns
    the per-watch outcome and how many new papers came back in total.
    """
    from datetime import datetime, timedelta

    from ..config import load_config
    config = config or load_config()
    days = float(config.get("watch_check_days") or 7)
    if not _DUE_LOCK.acquire(blocking=False):
        return {"checked": [], "new_total": 0, "busy": True}
    try:
        now = datetime.now()
        done: list[dict[str, Any]] = []
        total = 0
        for w in _data()["watches"]:
            checked = str(w.get("checked") or "")
            if checked:
                try:
                    if now - datetime.fromisoformat(checked) < timedelta(days=days):
                        continue
                except ValueError:
                    pass
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
