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


def _model_ready(cfg: dict[str, Any]) -> bool:
    from ..agents import client as aclient
    mcfg = aclient.model_config(cfg, task="rate")
    if not mcfg.get("model"):
        return False
    base = mcfg.get("base_url") or ""
    return bool(mcfg.get("api_key") or "127.0.0.1" in base or "localhost" in base)


def merge_into_session(
    wid: str,
    papers: list[dict[str, Any]],
    *,
    score: bool = True,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Merge fresh watch hits into the originating screening session (or a new one),
    optionally scoring them with the built-in agent first."""
    from . import screening
    from ..agents import scorer

    _ensure_legacy_imported()
    watch = db.get_watch(wid)
    if not watch:
        raise ValueError("没有这个追踪")

    valid_papers = [
        p for p in (papers or [])
        if isinstance(p, dict) and (str(p.get("title") or "").strip() or str(p.get("doi") or "").strip())
    ]
    if not valid_papers:
        raise ValueError("没有可并入的新文献")

    cfg = config if config is not None else config_load(config)
    target_session = str(watch.get("session") or "").strip()
    if target_session and db.session_exists(target_session):
        record = screening.load(target_session)
        question = str(record.get("question") or watch.get("question") or watch["query"])
        criteria = list(record.get("criteria") or [])
        mode = str(record.get("mode") or "quick")
        existing_keys = {screening._key(it) for it in (record.get("items") or [])}
        few_shots = scorer.collect_user_feedback(target_session)
        steering_notes = [
            str(n.get("note") or "").strip()
            for n in db.list_steering_notes(target_session, active_only=True)
            if str(n.get("note") or "").strip()
        ]
    else:
        target_session = ""
        question = str(watch.get("question") or watch["query"])
        criteria = []
        mode = "quick"
        existing_keys = set()
        few_shots = []
        steering_notes = []

    fresh: list[dict[str, Any]] = []
    seen_fresh: set[str] = set()
    for p in valid_papers:
        k = screening._key(p)
        if k in existing_keys or k in seen_fresh:
            continue
        seen_fresh.add(k)
        fresh.append(p)

    if not fresh:
        total_now = len(record.get("items") or []) if target_session else 0
        return {
            "session": target_session,
            "added": 0,
            "total": total_now,
            "scored": False,
            "by_relevance": {},
            "note": "这些文献已在原筛选会话中",
        }

    scored = False
    note = ""
    ratings_by_n: dict[int, dict[str, Any]] = {}
    if score and _model_ready(cfg):
        res = scorer.score_papers(
            question,
            criteria,
            fresh,
            config=cfg,
            few_shots=few_shots or None,
            steering_notes=steering_notes or None,
        )
        ratings_by_n = {
            int(r["n"]): r
            for r in (res.get("ratings") or [])
            if isinstance(r, dict) and r.get("n") is not None
        }
        scored = bool(ratings_by_n)
    elif score:
        note = "未配置内置 Agent 模型，已以「中」并入筛选列表"
    else:
        note = "已直接并入筛选列表"

    merged_items: list[dict[str, Any]] = []
    for idx, src in enumerate(fresh, 1):
        rated = ratings_by_n.get(idx)
        item: dict[str, Any] = {
            "doi": str(src.get("doi") or "").strip() or None,
            "title": str(src.get("title") or "").strip(),
            "relevance": rated["relevance"] if rated else "中",
            "reason": (
                str(rated.get("reason") or "").strip()
                if rated
                else ("定期追踪新文献（待复核）" if scored else "定期追踪新文献（待评分）")
            ),
        }
        for k in (
            "year",
            "journal",
            "journal_rank",
            "authors",
            "cited_by_count",
            "abstract",
            "abstract_missing",
            "retracted",
            "concern",
        ):
            if src.get(k) not in (None, "", []):
                item[k] = src[k]
        merged_items.append(item)

    saved = screening.save(
        question,
        merged_items,
        criteria=criteria or None,
        query=f"[watch] {watch['query']}",
        mode=mode,
        session=target_session or None,
        search_params={"source": "watch", "watch_id": wid, "sources": ["openalex", "crossref"]},
    )
    return {
        "session": saved["session"],
        "added": saved["added"],
        "total": saved["total"],
        "scored": scored,
        "by_relevance": saved["by_relevance"],
        "note": note,
    }


def config_load(cfg: dict[str, Any] | None) -> dict[str, Any]:
    return cfg if cfg is not None else config.load_config()
