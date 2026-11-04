"""The tools the built-in agent may call, and the schemas the model sees.

They are thin wrappers over the functions the MCP server exposes, so the app's
own agent finds the same behaviour (same screening files, same sessions) as an
external Agent driving LitLoop through MCP.
"""

from __future__ import annotations

from typing import Any, Callable

from ..search import deep, screening
from ..search.quick import search_papers_v2


def _brief(paper: dict[str, Any]) -> dict[str, Any]:
    out = {}
    for key in ("doi", "title", "year", "journal", "cited_by_count", "is_oa", "retracted"):
        if paper.get(key) not in (None, "", []):
            out[key] = paper[key]
    return out


def search(query: str, limit: int = 10, sort: str | None = None,
           year_from: int | None = None, year_to: int | None = None) -> dict[str, Any]:
    """Quick search. Returns the papers plus how many were filtered out."""
    stats: dict[str, Any] = {}
    expansions: list[dict[str, Any]] = []
    papers = search_papers_v2(query, limit=min(int(limit or 10), 50), sort=sort,
                              year_from=year_from, year_to=year_to,
                              stats=stats, expansions=expansions)
    return {"query": query, "count": len(papers), "papers": [_brief(p) for p in papers],
            "filtered": {k: v for k, v in stats.items() if v} or None,
            "glossary": expansions or None}


def deep_search_start(question: str, queries: list[str], seeds: list[str],
                      core_terms: list[str] | None = None, criteria: list[str] | None = None,
                      must_find: list[str] | None = None, batch_size: int = 20) -> dict[str, Any]:
    """Build the candidate pool and return the first batch to screen."""
    return deep.start(question, queries, seeds, core_terms or [], criteria=criteria,
                      must_find=must_find, batch_size=batch_size)


def deep_search_next(session: str, force: bool = False) -> dict[str, Any]:
    """Fold the latest ratings in and serve the next batch."""
    return deep.next_batch(session, force=force)


def deep_search_status(session: str) -> dict[str, Any]:
    """What this session has read and found so far, and whether stopping is suggested."""
    return deep.status(session)


def screening_save(session: str, ratings: list[dict[str, Any]]) -> dict[str, Any]:
    """Record ratings ([{"doi", "relevance", "reason"}], relevance one of 高/中/低/不相关)."""
    items = []
    for item in ratings:
        doi = str(item.get("doi") or "").strip()
        relevance = str(item.get("relevance") or "").strip()
        if not doi or relevance not in ("高", "中", "低", "不相关"):
            raise ValueError(f"评分不对：{item!r}（要 doi 和 高/中/低/不相关）")
        items.append({"doi": doi, "relevance": relevance, "reason": str(item.get("reason") or "")})
    data = screening.load(session)
    return screening.save(data.get("question", ""), items, session=session)


TOOLS: list[dict[str, Any]] = [
    {"name": "search", "description": "快速检索文献，返回 DOI、标题、年份、期刊、被引数。",
     "parameters": {"type": "object", "properties": {
         "query": {"type": "string", "description": "检索式，可用 | 分隔同义词"},
         "limit": {"type": "integer", "description": "最多返回几篇，默认 10"},
         "sort": {"type": "string", "enum": ["cited", "date"], "description": "按被引或时间排序"},
         "year_from": {"type": "integer"}, "year_to": {"type": "integer"}},
         "required": ["query"]}},
    {"name": "deep_search_start", "description": "建候选池并给出第一批待筛文献；需要 4–8 条英文检索词和 1–3 篇种子。",
     "parameters": {"type": "object", "properties": {
         "question": {"type": "string", "description": "用户的研究问题"},
         "queries": {"type": "array", "items": {"type": "string"}},
         "seeds": {"type": "array", "items": {"type": "string"}, "description": "种子 DOI"},
         "core_terms": {"type": "array", "items": {"type": "string"}},
         "criteria": {"type": "array", "items": {"type": "string"}},
         "must_find": {"type": "array", "items": {"type": "string"}},
         "batch_size": {"type": "integer", "description": "每批几篇，默认 20"}},
         "required": ["question", "queries", "seeds"]}},
    {"name": "deep_search_next", "description": "提交这一批评分后取下一批。",
     "parameters": {"type": "object", "properties": {
         "session": {"type": "string"}, "force": {"type": "boolean"}},
         "required": ["session"]}},
    {"name": "deep_search_status", "description": "看这个会话读到第几篇、找到多少目标、是否建议停止。",
     "parameters": {"type": "object", "properties": {"session": {"type": "string"}},
                    "required": ["session"]}},
    {"name": "screening_save", "description": "保存一批评分：每篇给 doi、相关度（高/中/低/不相关）和一句理由。",
     "parameters": {"type": "object", "properties": {
         "session": {"type": "string"},
         "ratings": {"type": "array", "items": {"type": "object", "properties": {
             "doi": {"type": "string"}, "relevance": {"type": "string"},
             "reason": {"type": "string"}}, "required": ["doi", "relevance"]}}},
         "required": ["session", "ratings"]}},
]

FUNCTIONS: dict[str, Callable[..., Any]] = {
    "search": search,
    "deep_search_start": deep_search_start,
    "deep_search_next": deep_search_next,
    "deep_search_status": deep_search_status,
    "screening_save": screening_save,
}
