"""Screening sessions: criteria, per-paper relevance + reason, the user's picks.

The agent reads search results, rates each paper (高/中/低/不相关) with a
one-line reason, and saves them here; the user then chooses which to keep.
Each session is one JSON file (the record) plus a Markdown table (to read)
under ``<litloop home>/screening``. Saving again into a session merges by
DOI, keeps earlier decisions and appends the search to its history, so a
topic can be searched in several rounds and later continued.
"""

from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any

from . import config as _config
from . import store
from .identifiers import canonical_doi

RELEVANCE = ("高", "中", "低", "不相关")
ABSTRACT_CHARS = 1200  # as much as a deep-search batch shows
_RANK = {r: i for i, r in enumerate(RELEVANCE)}
_EN = {"high": "高", "medium": "中", "low": "低", "irrelevant": "不相关", "none": "不相关"}


def screening_dir() -> Path:
    d = Path(_config.DATA_DIR) / "screening"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _slug(text: str) -> str:
    s = re.sub(r"[^\w一-鿿]+", "-", text.strip().lower()).strip("-")
    return s[:40] or "search"


def _key(item: dict[str, Any]) -> str:
    doi = canonical_doi(item.get("doi"))
    return doi or "title:" + re.sub(r"[^a-z0-9]+", "", str(item.get("title") or "").lower())


def _norm_relevance(value: Any) -> str:
    v = str(value or "").strip()
    v = _EN.get(v.lower(), v)
    if v not in _RANK:
        raise ValueError(f"relevance must be one of {RELEVANCE}, got {value!r}")
    return v


def _path(session: str) -> Path:
    p = screening_dir() / f"{session}.json"
    if not p.exists():
        raise FileNotFoundError(f"no screening session {session!r}")
    return p


def load(session: str) -> dict[str, Any]:
    return store.read_json(_path(session))


def _rating(item: dict[str, Any]) -> str:
    """The rating that counts: the user's, set in the app, overrides the agent's."""
    return item.get("user_relevance") or item["relevance"]


def _write(data: dict[str, Any]) -> dict[str, str]:
    data["updated"] = time.strftime("%Y-%m-%d %H:%M:%S")
    base = screening_dir() / data["id"]
    store.write_json(base.with_suffix(".json"), data, indent=2)
    store.write_text(base.with_suffix(".md"), render_markdown(data))
    return {"json": str(base.with_suffix(".json")), "markdown": str(base.with_suffix(".md"))}


def save(question: str, items: list[dict[str, Any]], criteria: list[str] | str | None = None,
         query: str = "", mode: str = "quick", session: str | None = None,
         search_params: dict[str, Any] | None = None) -> dict[str, Any]:
    """Create a session, or merge ITEMS into SESSION. Returns id, paths, counts."""
    sid = session or time.strftime("%Y%m%d-%H%M%S") + "-" + _slug(question or query)
    with store.lock(screening_dir() / f"{sid}.json"):  # the app may be writing the user's picks
        return _save(sid, bool(session), question, items, criteria, query, mode, search_params)


def _save(sid: str, existing: bool, question: str, items: list[dict[str, Any]], criteria: Any,
          query: str, mode: str, search_params: dict[str, Any] | None) -> dict[str, Any]:
    if existing:
        data = load(sid)
    else:
        data = {"schema": store.SCHEMA, "id": sid, "created": time.strftime("%Y-%m-%d %H:%M:%S"),
                "question": question, "mode": mode, "criteria": [], "searches": [], "items": []}
    if criteria:
        data["criteria"] = [criteria] if isinstance(criteria, str) else list(criteria)
    if query:
        data["searches"].append({"query": query, "time": time.strftime("%Y-%m-%d %H:%M:%S"),
                                 "params": search_params or {}, "results": len(items)})
    by_key = {_key(it): it for it in data["items"]}
    added = 0
    for raw in items:
        item = {k: raw.get(k) for k in ("doi", "title", "year", "journal", "journal_rank", "authors",
                                        "cited_by_count", "abstract", "abstract_missing", "retracted", "concern")
                if raw.get(k) not in (None, "", [])}
        if item.get("abstract"):  # for the app's detail pane; trimmed so a record stays small
            item["abstract"] = str(item["abstract"])[:ABSTRACT_CHARS]
        if isinstance(item.get("authors"), list):
            item["authors"] = item["authors"][:3]
        if raw.get("abstract_zh"):
            item["abstract_zh"] = str(raw["abstract_zh"]).strip()[:ABSTRACT_CHARS]
        item["relevance"] = _norm_relevance(raw.get("relevance"))
        item["reason"] = str(raw.get("reason") or "").strip()
        old = by_key.get(_key(item))
        if old:  # re-rated: keep the user's decision
            old.update({k: v for k, v in item.items()})
        else:
            item["decision"] = None
            data["items"].append(item)
            by_key[_key(item)] = item
            added += 1
    data["items"].sort(key=lambda it: (_RANK[it["relevance"]], -(it.get("cited_by_count") or 0)))
    for n, it in enumerate(data["items"], 1):
        it["n"] = n
    paths = _write(data)
    # Items are re-sorted by relevance, so the numbers the user picks from are
    # these, not the order they were sent in: show the user this list.
    return {"session": data["id"], "added": added, "total": len(data["items"]),
            "by_relevance": {r: sum(1 for it in data["items"] if it["relevance"] == r) for r in RELEVANCE},
            "numbering": [_brief(it) for it in data["items"]],
            **paths}


def translate_wanted(config: dict[str, Any] | None = None) -> list[str]:
    """The ratings whose abstract is translated without asking (config abstract_translation)."""
    from .config import load_config
    value = str((config or load_config()).get("abstract_translation", "高") or "off")
    if value == "all":
        return list(RELEVANCE)
    return [r for r in RELEVANCE if r in value]


def untranslated(session: str, config: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Items the agent should still translate: only when the agent is the translator
    (translation_provider "agent"), rated in translate_wanted, with an abstract."""
    from . import translate as _translate
    if _translate.by_api(config):
        return []
    wanted = translate_wanted(config)
    return [_brief(it) for it in load(session)["items"]
            if _rating(it) in wanted and it.get("abstract") and not it.get("abstract_zh")]


def translate(session: str, items: list[dict[str, Any]]) -> dict[str, Any]:
    """Store the agent's Chinese abstracts: items=[{doi or n, abstract_zh}]. Ratings stay as they are."""
    with store.lock(_path(session)):  # the app may be writing the user's picks
        data = load(session)
        by_doi = {str(it.get("doi") or "").lower(): it for it in data["items"] if it.get("doi")}
        by_n = {it.get("n"): it for it in data["items"]}
        saved, missing = 0, []
        for raw in items or []:
            target = by_doi.get(str(raw.get("doi") or "").lower()) or by_n.get(raw.get("n"))
            text = str(raw.get("abstract_zh") or "").strip()
            if not target or not text:
                missing.append(raw.get("doi") or raw.get("n"))
                continue
            target["abstract_zh"] = text[:ABSTRACT_CHARS]
            saved += 1
        _write(data)
    return {"session": session, "saved": saved, "not_found": missing, "left": untranslated(session)}


def _brief(it: dict[str, Any]) -> dict[str, Any]:
    return {"n": it["n"], "relevance": it["relevance"], "title": str(it.get("title") or "")[:90],
            "doi": it.get("doi")}


def decide(session: str, selected: list[int | str], rejected: list[int | str] | None = None,
           out_file: str | None = None) -> dict[str, Any]:
    """Record the user's whole choice by item number or DOI, replacing any
    earlier decision (a corrected pick must not leave the wrong one behind);
    optionally write a download queue."""
    with store.lock(_path(session)):
        return _decide(session, selected, rejected, out_file)


def _decide(session: str, selected: list[int | str], rejected: list[int | str] | None,
            out_file: str | None) -> dict[str, Any]:
    data = load(session)
    index: dict[str, dict[str, Any]] = {}
    for it in data["items"]:
        index[str(it["n"])] = it
        if it.get("doi"):
            index[str(it["doi"]).lower()] = it
    refs = [(ref, "selected") for ref in selected or []] + [(ref, "rejected") for ref in rejected or []]
    unknown = [ref for ref, _ in refs if str(ref).strip().lower() not in index]
    if unknown:  # a mistyped number must not wipe the earlier choice
        return {"session": session, "error": "unknown item numbers/DOIs; nothing recorded",
                "unknown": unknown, "numbering": [_brief(it) for it in data["items"]]}
    for it in data["items"]:
        it["decision"] = None
    for ref, decision in refs:
        index[str(ref).strip().lower()]["decision"] = decision
    paths = _write(data)
    chosen = [it for it in data["items"] if it.get("decision") == "selected"]
    result: dict[str, Any] = {"session": session, "selected": len(chosen),
                              "identifiers": [it["doi"] for it in chosen if it.get("doi")],
                              "chosen": [_brief(it) for it in chosen],
                              "rejected": [_brief(it) for it in data["items"] if it.get("decision") == "rejected"],
                              "unknown": unknown, **paths}
    if out_file and chosen:
        from .pipeline import QueueEntry, predict_channel, write_queue
        qe = [QueueEntry(identifier=it["doi"], channel=predict_channel(it["doi"]), title=it.get("title", ""))
              for it in chosen if it.get("doi")]
        result["queue_file"] = str(write_queue(qe, out_file))
    return result


def list_sessions(limit: int = 20) -> list[dict[str, Any]]:
    out = []
    # Only the records: a deep search's pool, summary and progress files sit
    # next to them (the pool can be 100+ MB and has no "id").
    records = [p for p in screening_dir().glob("*.json") if p.name.count(".") == 1]
    for p in sorted(records, reverse=True)[:limit]:
        try:
            d = store.read_json(p)
            d["id"]
        except (OSError, ValueError, KeyError, TypeError):
            continue
        out.append({"session": d["id"], "question": d.get("question", ""), "updated": d.get("updated", ""),
                    "items": len(d.get("items", [])),
                    "selected": sum(1 for it in d.get("items", []) if it.get("decision") == "selected")})
    return out


def history(limit: int = 500) -> list[dict[str, Any]]:
    """Every screening session for the app's history: what was asked, how far it got, what was kept.

    "selected" lists the final picks (title, year, journal, DOI), newest session first.
    """
    out = []
    records = [p for p in screening_dir().glob("*.json") if p.name.count(".") == 1]
    for p in sorted(records, reverse=True)[:limit]:
        try:
            d = store.read_json(p)
            sid = d["id"]
        except (OSError, ValueError, KeyError, TypeError):
            continue
        items = d.get("items", [])
        out.append({
            "session": sid, "question": d.get("question", ""), "mode": d.get("mode", "quick"),
            "created": d.get("created", ""), "updated": d.get("updated", ""), "items": len(items),
            "by_relevance": {r: sum(1 for it in items if _rating(it) == r) for r in RELEVANCE},
            "selected": [{k: it.get(k) for k in ("doi", "title", "year", "journal", "journal_rank")}
                         for it in items if it.get("decision") == "selected"],
        })
    return out


_DECISION = {"selected": "✅ 选中", "rejected": "✗ 不要", None: ""}


def render_markdown(data: dict[str, Any]) -> str:
    lines = [f"# 文献筛选：{data.get('question') or ''}", "",
             f"- 会话：`{data['id']}`（{data.get('mode', '')}），更新于 {data.get('updated', '')}"]
    if data.get("criteria"):
        lines += ["- 筛选标准：", *[f"  - {c}" for c in data["criteria"]]]
    if data.get("searches"):
        lines.append("- 检索记录：" + "；".join(f"`{s['query']}`（{s['time']}，{s['results']} 篇）"
                                            for s in data["searches"]))
    lines += ["", "| # | 相关度 | 文章 | 年份 | 期刊 / 分区 | 理由 | 选择 |",
              "|---|---|---|---|---|---|---|"]
    for it in data["items"]:
        title = str(it.get("title") or "").replace("|", "/")
        if it.get("doi"):
            title = f"[{title}](https://doi.org/{it['doi']})"
        flags = ("⚠已撤稿 " if it.get("retracted") else "") + ("⚠关注声明 " if it.get("concern") else "") \
            + ("（仅凭标题）" if it.get("abstract_missing") else "")
        venue = " ".join(x for x in (str(it.get("journal") or ""), str(it.get("journal_rank") or "")) if x)
        lines.append(f"| {it['n']} | {it['relevance']} | {title} | {it.get('year') or ''} | "
                     f"{venue.replace('|', '/')} | {flags}{str(it.get('reason') or '').replace('|', '/')} | "
                     f"{_DECISION.get(it.get('decision'), '')} |")
    return "\n".join(lines) + "\n"
