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

from .. import config as _config
from .. import db
from .. import store

RELEVANCE = db.RELEVANCE
ABSTRACT_CHARS = db.ABSTRACT_CHARS
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
    from ..terms.text import paper_key
    return paper_key(item)


def _norm_relevance(value: Any) -> str:
    v = str(value or "").strip()
    v = _EN.get(v.lower(), v)
    if v not in _RANK:
        raise ValueError(f"relevance must be one of {RELEVANCE}, got {value!r}")
    return v


def load(session: str) -> dict[str, Any]:
    return db.load_screening(session)


def _rating(item: dict[str, Any]) -> str:
    """The rating that counts: the user's, set in the app, overrides the agent's."""
    return item.get("user_relevance") or item["relevance"]


def _write_markdown(data: dict[str, Any]) -> dict[str, str]:
    md_path = screening_dir() / f"{data['id']}.md"
    store.write_text(md_path, render_markdown(data))
    return {"markdown": str(md_path)}


def save(question: str, items: list[dict[str, Any]], criteria: list[str] | str | None = None,
         query: str = "", mode: str = "quick", session: str | None = None,
         search_params: dict[str, Any] | None = None) -> dict[str, Any]:
    """Create a session, or merge ITEMS into SESSION. Returns id, paths, counts."""
    res = db.save_screening(
        question,
        items,
        criteria=criteria,
        query=query,
        mode=mode,
        session=session,
        search_params=search_params,
    )
    record = res.pop("record")
    paths = _write_markdown(record)
    return {**res, **paths}


def translate_wanted(config: dict[str, Any] | None = None) -> list[str]:
    """The ratings whose abstract is translated without asking (config abstract_translation)."""
    from ..config import load_config
    value = str((config or load_config()).get("abstract_translation", "高") or "off")
    if value == "all":
        return list(RELEVANCE)
    return [r for r in RELEVANCE if r in value]


def untranslated(session: str, config: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Items the agent should still translate: only when the agent is the translator
    (translation_provider "agent"), rated in translate_wanted, with an abstract."""
    from ..desktop import translate as _translate
    if _translate.by_api(config):
        return []
    wanted = translate_wanted(config)
    return [_brief(it) for it in load(session)["items"]
            if _rating(it) in wanted and it.get("abstract") and not it.get("abstract_zh")]


def translate(session: str, items: list[dict[str, Any]]) -> dict[str, Any]:
    """Store the agent's Chinese abstracts: items=[{doi or n, abstract_zh}]. Ratings stay as they are."""
    res = db.translate_screening(session, items)
    return {**res, "left": untranslated(session)}


def _brief(it: dict[str, Any]) -> dict[str, Any]:
    return {"n": it["n"], "relevance": it["relevance"], "title": str(it.get("title") or "")[:90],
            "doi": it.get("doi")}


def decide(session: str, selected: list[int | str], rejected: list[int | str] | None = None,
           out_file: str | None = None) -> dict[str, Any]:
    """Record the user's whole choice by item number or DOI, replacing any
    earlier decision (a corrected pick must not leave the wrong one behind);
    optionally write a download queue."""
    res = db.decide_screening(session, selected, rejected)
    if "error" in res:
        return res
    record = res.pop("record")
    paths = _write_markdown(record)
    result: dict[str, Any] = {**res, **paths}
    chosen = [it for it in record["items"] if it.get("decision") == "selected"]
    if out_file and chosen:
        from ..download.pipeline import QueueEntry, predict_channel, write_queue
        qe = [QueueEntry(identifier=it["doi"], channel=predict_channel(it["doi"]), title=it.get("title", ""))
              for it in chosen if it.get("doi")]
        result["queue_file"] = str(write_queue(qe, out_file))
    return result


def list_sessions(limit: int = 20) -> list[dict[str, Any]]:
    return db.list_sessions(limit)


def prisma(session: str) -> dict[str, Any]:
    """The review's flow counts from what this session recorded (PRISMA-style).

    Records identified are what the searches returned; duplicates are those that
    were already in the session; screening is every paper the user was shown.
    Exclusions are counted separately for the user's "不要" and for the low
    ratings the agent gave without the user deciding, with the reasons attached.
    """
    data = load(session)
    items = data.get("items", [])
    searches = [{"query": s.get("query", ""), "time": s.get("time", ""),
                 "results": int(s.get("results") or 0), "new": int(s.get("new") or 0)}
                for s in data.get("searches", [])]
    selected = [it for it in items if it.get("decision") == "selected"]
    rejected = [it for it in items if it.get("decision") == "rejected"]
    low = [it for it in items if it.get("decision") is None and _rating(it) in ("低", "不相关")]
    reasons: dict[str, int] = {}
    for it in rejected + low:
        # The user's own reason outranks the agent's when they gave one.
        text = " ".join(str(it.get("reject_reason") or it.get("reason") or "").split())[:120]
        if text:
            reasons[text] = reasons.get(text, 0) + 1
    return {
        "session": data.get("id", session), "question": data.get("question", ""),
        "mode": data.get("mode", "quick"), "created": data.get("created", ""),
        "updated": data.get("updated", ""), "searches": searches,
        "identified": sum(s["results"] for s in searches),
        "duplicates": sum(max(0, s["results"] - s["new"]) for s in searches) if searches else 0,
        "screened": len(items),
        "by_relevance": {r: sum(1 for it in items if _rating(it) == r) for r in RELEVANCE},
        "included": len(selected),
        "excluded": len(rejected),
        "low_without_decision": len(low),
        "reasons": [{"reason": k, "count": v} for k, v in sorted(reasons.items(), key=lambda kv: -kv[1])[:10]],
    }


def history(limit: int = 500) -> list[dict[str, Any]]:
    """Every screening session for the app's history: what was asked, how far it got, what was kept.

    "selected" lists the final picks (title, year, journal, DOI), newest session first.
    """
    return db.history(limit)


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


def prisma_markdown(session: str) -> str:
    """The flow counts as a Markdown table, for a manuscript's record."""
    d = prisma(session)
    lines = [f"# 检索流程：{d['question'] or '（没有记录检索问题）'}", "",
             "| 阶段 | 篇数 |", "| --- | --- |",
             f"| 检索命中 | {d['identified']} |", f"| 去重后 | {d['screened']} |",
             f"| 已筛 | {d['screened']} |", f"| 纳入 | {d['included']} |",
             f"| 排除 | {d['excluded']} |", f"| 低相关未定 | {d['low_without_decision']} |", ""]
    if d["searches"]:
        lines += ["## 检索", "", "| 时间 | 检索词 | 返回 | 新增 |", "| --- | --- | --- | --- |"]
        lines += [f"| {s['time']} | {s['query']} | {s['results']} | {s['new']} |" for s in d["searches"]]
        lines.append("")
    if d["reasons"]:
        lines += ["## 排除理由", ""] + [f"- {r['reason']}（{r['count']} 篇）" for r in d["reasons"]] + [""]
    return "\n".join(lines)
