"""Screening sessions: criteria, per-paper relevance + reason, the user's picks.

The agent reads search results, rates each paper (高/中/低/不相关) with a
one-line reason, and saves them here; the user then chooses which to keep.
Each session is one JSON file (the record) plus a Markdown table (to read)
under ``<litloop home>/screening``. Saving again into a session merges by
DOI, keeps earlier decisions and appends the search to its history, so a
topic can be searched in several rounds and later continued.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

from . import config as _config
from .identifiers import canonical_doi

RELEVANCE = ("高", "中", "低", "不相关")
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
    return json.loads(_path(session).read_text(encoding="utf-8"))


def _write(data: dict[str, Any]) -> dict[str, str]:
    data["updated"] = time.strftime("%Y-%m-%d %H:%M:%S")
    base = screening_dir() / data["id"]
    base.with_suffix(".json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    base.with_suffix(".md").write_text(render_markdown(data), encoding="utf-8")
    return {"json": str(base.with_suffix(".json")), "markdown": str(base.with_suffix(".md"))}


def save(question: str, items: list[dict[str, Any]], criteria: list[str] | str | None = None,
         query: str = "", mode: str = "quick", session: str | None = None,
         search_params: dict[str, Any] | None = None) -> dict[str, Any]:
    """Create a session, or merge ITEMS into SESSION. Returns id, paths, counts."""
    if session:
        data = load(session)
    else:
        sid = time.strftime("%Y%m%d-%H%M%S") + "-" + _slug(question or query)
        data = {"id": sid, "created": time.strftime("%Y-%m-%d %H:%M:%S"), "question": question,
                "mode": mode, "criteria": [], "searches": [], "items": []}
    if criteria:
        data["criteria"] = [criteria] if isinstance(criteria, str) else list(criteria)
    if query:
        data["searches"].append({"query": query, "time": time.strftime("%Y-%m-%d %H:%M:%S"),
                                 "params": search_params or {}, "results": len(items)})
    by_key = {_key(it): it for it in data["items"]}
    added = 0
    for raw in items:
        item = {k: raw.get(k) for k in ("doi", "title", "year", "journal", "journal_rank",
                                        "cited_by_count", "abstract_missing", "retracted", "concern")
                if raw.get(k) not in (None, "", [])}
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


def _brief(it: dict[str, Any]) -> dict[str, Any]:
    return {"n": it["n"], "relevance": it["relevance"], "title": str(it.get("title") or "")[:90],
            "doi": it.get("doi")}


def decide(session: str, selected: list[int | str], rejected: list[int | str] | None = None,
           out_file: str | None = None) -> dict[str, Any]:
    """Record the user's whole choice by item number or DOI, replacing any
    earlier decision (a corrected pick must not leave the wrong one behind);
    optionally write a download queue."""
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
    for p in sorted(screening_dir().glob("*.json"), reverse=True)[:limit]:
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        out.append({"session": d["id"], "question": d.get("question", ""), "updated": d.get("updated", ""),
                    "items": len(d.get("items", [])),
                    "selected": sum(1 for it in d.get("items", []) if it.get("decision") == "selected")})
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
