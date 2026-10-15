"""Deep search (stage 2): find the literature on a topic, not just the top hits.

The agent supplies a question, 4-8 English queries, 1-3 core terms and a few
seed papers. The plugin builds a candidate pool from two complementary
routes and hands it to the agent in ranked batches for screening:

* keyword route: every query's raw candidates (100 each, nothing cut);
* citation route: works that cite any of the seeds' references and mention a
  core term in title/abstract, plus the seeds' own references and citing works.

On the acceptance sets neither route alone found most targets, together they
did (TODO.md, 2026-10-15). Candidates are ranked by

    score = shared references with the seeds + 2 x queries the record fully matches

Papers the agent rates 高 join the seeds, so later batches follow what the
user actually wants (snowballing). A batch with almost nothing relevant, or
the screening cap, ends the search with a stated reason.

Ratings live in the screening session (``litloop_screening``); this module
keeps the pool next to it as ``<session>.pool.json``.
"""

from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from . import screening
from .config import load_config
from .log import get_logger

logger = get_logger()

OPENALEX = "https://api.openalex.org/works"
_FIELDS = ("id,doi,title,publication_year,cited_by_count,referenced_works,"
           "abstract_inverted_index,primary_location,authorships")
QUERY_WEIGHT = 2
ABSTRACT_CHARS = 1500


# --------------------------------------------------------------------------- OpenAlex

def _get(params: dict[str, Any], *, tries: int = 3) -> dict[str, Any]:
    from .search import _plain_session
    key = str(load_config().get("openalex_api_key") or "").strip()
    if key:
        params = {**params, "api_key": key}
    last: Exception | None = None
    for attempt in range(tries):
        try:
            r = _plain_session().get(OPENALEX, params=params, timeout=60)
            if r.status_code == 200:
                return r.json()
            last = RuntimeError(f"OpenAlex HTTP {r.status_code}")
        except Exception as exc:  # network hiccup: retry
            last = exc
        time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"OpenAlex request failed: {last}")


def _short(wid: str) -> str:
    return str(wid or "").rsplit("/", 1)[-1]


def _record(w: dict[str, Any]) -> dict[str, Any]:
    from .search import _reconstruct_abstract
    src = ((w.get("primary_location") or {}).get("source") or {})
    return {
        "openalex": _short(w.get("id")),
        "doi": (str(w.get("doi") or "").replace("https://doi.org/", "").lower() or None),
        "title": w.get("title") or "",
        "year": w.get("publication_year"),
        "journal": src.get("display_name") or "",
        "issn": list(src.get("issn") or []),
        "authors": [((a.get("author") or {}).get("display_name") or "") for a in (w.get("authorships") or [])[:3]],
        "cited_by_count": w.get("cited_by_count") or 0,
        "abstract": _reconstruct_abstract(w.get("abstract_inverted_index")),
        "refs": [_short(r) for r in (w.get("referenced_works") or [])],
    }


def _year_filter(year_from: int | None, year_to: int | None) -> list[str]:
    out = []
    if year_from:
        out.append(f"from_publication_date:{year_from}-01-01")
    if year_to:
        out.append(f"to_publication_date:{year_to}-12-31")
    return out


def _by_filter(field: str, values: list[str], extra: list[str] | None = None) -> list[dict[str, Any]]:
    """Fetch works whose ``field`` is any of ``values`` (50 per request)."""
    out = []
    for i in range(0, len(values), 50):
        flt = ",".join([f"{field}:" + "|".join(values[i:i + 50])] + (extra or []))
        out += _get({"filter": flt, "per-page": 50, "select": _FIELDS}).get("results") or []
    return [_record(w) for w in out]


def _pages(flt: str, max_records: int) -> list[dict[str, Any]]:
    out, cursor = [], "*"
    while cursor and len(out) < max_records:
        d = _get({"filter": flt, "per-page": 200, "cursor": cursor, "select": _FIELDS})
        out += d.get("results") or []
        cursor = (d.get("meta") or {}).get("next_cursor")
    return [_record(w) for w in out[:max_records]]


def _citing(work_ids: list[str], years: list[str], max_records: int = 400) -> list[dict[str, Any]]:
    out = []
    for i in range(0, len(work_ids), 50):
        out += _pages(",".join(["cites:" + "|".join(work_ids[i:i + 50])] + years), max_records)
    return out


def _cites_refs_on_topic(ref_ids: list[str], core_terms: list[str], years: list[str],
                         max_records: int = 1000) -> list[dict[str, Any]]:
    """Works citing any of ``ref_ids`` whose title/abstract mentions a core term."""
    out = []
    for term in core_terms or [""]:
        topic = [f"title_and_abstract.search:{term.replace(',', ' ')}"] if term else []
        for i in range(0, len(ref_ids), 50):
            out += _pages(",".join(["cites:" + "|".join(ref_ids[i:i + 50])] + topic + years), max_records)
    return out


# --------------------------------------------------------------------------- pool

def _keyword_candidates(queries: list[str], year_from: int | None, year_to: int | None) -> dict[str, list[dict]]:
    from .search import _collect

    def one(q: str) -> list[dict[str, Any]]:
        try:
            return _collect(q, 100, 100, year_from, year_to, None, None, None)
        except Exception as exc:
            logger.warning("deep search: query %r failed (%s)", q, exc)
            return []

    with ThreadPoolExecutor(max_workers=4) as ex:
        return dict(zip(queries, ex.map(one, queries)))


def _key(rec: dict[str, Any]) -> str:
    return rec.get("doi") or ("oa:" + rec["openalex"] if rec.get("openalex") else "")


def _add(pool: dict[str, dict], rec: dict[str, Any], via: str) -> None:
    k = _key(rec)
    if not k or not rec.get("title"):
        return
    have = pool.get(k)
    if have is None:
        pool[k] = {**rec, "via": [via]}
        return
    for field in ("abstract", "journal", "openalex", "refs", "year", "issn", "authors"):
        if not have.get(field) and rec.get(field):
            have[field] = rec[field]
    if via != "metadata" and via not in have["via"]:  # a metadata lookup is not a way of finding it
        have["via"].append(via)


def _score(pool: dict[str, dict], seed_refs: set[str], queries: list[str]) -> None:
    from .search import _match_tier
    hits = {k: 0 for k in pool}
    for q in queries:
        rows = [{"title": r["title"], "abstract": r.get("abstract") or "", "_k": k} for k, r in pool.items()]
        for row in _match_tier(rows, q):
            if row["_tier"] == 0:
                hits[row["_k"]] += 1
    for k, r in pool.items():
        r["coupling"] = sum(1 for ref in (r.get("refs") or []) if ref in seed_refs)
        r["query_hits"] = hits[k]
        r["score"] = r["coupling"] + QUERY_WEIGHT * r["query_hits"]


def _pool_path(session: str):
    return screening.screening_dir() / f"{session}.pool.json"


def _load(session: str) -> dict[str, Any]:
    p = _pool_path(session)
    if not p.exists():
        raise FileNotFoundError(f"no deep search for session {session!r}")
    return json.loads(p.read_text(encoding="utf-8"))


def _save(state: dict[str, Any]) -> None:
    _pool_path(state["session"]).write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")


def _expand(state: dict[str, Any], new_seed_keys: list[str]) -> int:
    """Add citation-route candidates for new seeds; returns how many records were added."""
    pool = state["pool"]
    years = _year_filter(state.get("year_from"), state.get("year_to"))
    seeds = [pool[k] for k in new_seed_keys if k in pool]
    new_refs = sorted({r for s in seeds for r in (s.get("refs") or [])} - set(state["queried_refs"]))
    before = len(pool)
    if new_refs:
        for rec in _by_filter("openalex", new_refs, years):
            _add(pool, rec, "seed_reference")
        for rec in _cites_refs_on_topic(new_refs, state["core_terms"], years):
            _add(pool, rec, "cites_seed_references")
    seed_ids = [s["openalex"] for s in seeds if s.get("openalex")]
    if seed_ids:
        for rec in _citing(seed_ids, years):
            _add(pool, rec, "cites_seed")
    state["queried_refs"] = sorted(set(state["queried_refs"]) | set(new_refs))
    state["seed_refs"] = sorted(set(state["seed_refs"]) | {r for s in seeds for r in (s.get("refs") or [])})
    for k in new_seed_keys:
        if k not in state["seeds"]:
            state["seeds"].append(k)
    return len(pool) - before


# --------------------------------------------------------------------------- public

def start(question: str, queries: list[str], seeds: list[str], core_terms: list[str], *,
          criteria: list[str] | None = None, must_find: list[str] | None = None,
          year_from: int | None = None, year_to: int | None = None,
          batch_size: int = 30, cap: int = 200) -> dict[str, Any]:
    """Build the pool and return the first batch to screen."""
    if not queries:
        raise ValueError("give 4-8 English queries")
    if not seeds:
        raise ValueError("give 1-3 seed DOIs (papers the user already knows are on topic)")
    t0 = time.time()
    session = screening.save(question, [], criteria=criteria, query=" | ".join(queries), mode="deep",
                             search_params={"core_terms": core_terms, "seeds": seeds,
                                            "year_from": year_from, "year_to": year_to})["session"]
    years = _year_filter(year_from, year_to)
    pool: dict[str, dict] = {}
    seed_recs = _by_filter("doi", [s.lower() for s in seeds])
    for rec in seed_recs:
        _add(pool, rec, "seed")
    for q, rows in _keyword_candidates(queries, year_from, year_to).items():
        for r in rows:
            _add(pool, {"openalex": None, "doi": str(r.get("doi") or "").lower() or None,
                        "title": r.get("title") or "", "year": r.get("year"), "journal": r.get("journal") or "",
                        "issn": r.get("issn") or [], "authors": r.get("authors") or [],
                        "cited_by_count": r.get("cited_by_count") or 0, "abstract": r.get("abstract") or "",
                        "refs": None}, f"query: {q}")
    # Keyword hits come without reference lists; fetch them so coupling can count.
    missing = [k for k, r in pool.items() if r.get("refs") is None and r.get("doi")]
    for rec in _by_filter("doi", missing):
        _add(pool, rec, "metadata")
    state = {"session": session, "question": question, "queries": queries, "core_terms": core_terms,
             "year_from": year_from, "year_to": year_to, "batch_size": batch_size, "cap": cap,
             "must_find": [d.lower() for d in (must_find or [])], "seeds": [], "seed_refs": [],
             "queried_refs": [], "served": [], "rounds": [], "pool": pool}
    _expand(state, [_key(r) for r in seed_recs])
    _score(pool, set(state["seed_refs"]), queries)
    state["rounds"].append({"time": time.strftime("%Y-%m-%d %H:%M:%S"), "event": "start",
                            "pool": len(pool), "seconds": round(time.time() - t0, 1)})
    _save(state)
    return _next_batch(state)


def next_batch(session: str) -> dict[str, Any]:
    """Fold the agent's latest ratings back in (高 papers become seeds) and serve the next batch."""
    state = _load(session)
    rated = _ratings(state)
    new_seeds = [k for k, rel in rated.items() if rel == "高" and k in state["pool"] and k not in state["seeds"]]
    added = 0
    if new_seeds:
        added = _expand(state, new_seeds)
        _score(state["pool"], set(state["seed_refs"]), state["queries"])
    state["rounds"].append({"time": time.strftime("%Y-%m-%d %H:%M:%S"), "event": "next",
                            "new_seeds": len(new_seeds), "pool_added": added})
    _save(state)
    return _next_batch(state, rated)


def status(session: str) -> dict[str, Any]:
    state = _load(session)
    rated = _ratings(state)
    order = _ranked(state)
    return {"session": session, "pool": len(state["pool"]), "screened": len(rated),
            "by_relevance": {r: sum(1 for v in rated.values() if v == r) for r in screening.RELEVANCE},
            "seeds": len(state["seeds"]), "coverage": _coverage(state, rated, order), **_stop(state, rated)}


# --------------------------------------------------------------------------- helpers

def _ratings(state: dict[str, Any]) -> dict[str, str]:
    """Ratings keyed like the pool. Papers without a DOI are matched back by title, the way screening keys them."""
    by_title = {screening._key({"title": r.get("title")}): k for k, r in state["pool"].items() if not r.get("doi")}
    out = {}
    for it in screening.load(state["session"])["items"]:
        key = screening._key(it)
        key = by_title.get(key) if key.startswith("title:") else key
        if key:
            out[key] = it["relevance"]
    return out


def _ranked(state: dict[str, Any]) -> list[str]:
    seeds = set(state["seeds"])
    return sorted((k for k in state["pool"] if k not in seeds),
                  key=lambda k: (-state["pool"][k].get("score", 0), -(state["pool"][k].get("cited_by_count") or 0)))


def _stop(state: dict[str, Any], rated: dict[str, str]) -> dict[str, Any]:
    if len(rated) >= state["cap"]:
        return {"stop_suggested": True, "stop_reason": f"已筛 {len(rated)} 篇，达到上限 {state['cap']}"}
    last = state["served"][-1] if state["served"] else []
    judged = [rated[k] for k in last if k in rated]
    if len(state["served"]) >= 2 and judged and len(judged) >= len(last) // 2:
        useful = sum(1 for r in judged if r in ("高", "中"))
        if useful <= max(1, len(judged) // 15):
            return {"stop_suggested": True,
                    "stop_reason": f"最近一批 {len(judged)} 篇里只有 {useful} 篇高或中相关，新增已经很少"}
    return {"stop_suggested": False, "stop_reason": ""}


def _coverage(state: dict[str, Any], rated: dict[str, str], order: list[str]) -> list[dict[str, Any]]:
    pos = {k: i for i, k in enumerate(order, 1)}
    out = []
    for doi in state.get("must_find") or []:
        if doi in rated:
            out.append({"doi": doi, "status": "screened", "relevance": rated[doi]})
        elif doi in state["seeds"]:
            out.append({"doi": doi, "status": "seed"})
        elif doi in pos:
            out.append({"doi": doi, "status": "in_pool", "rank": pos[doi]})
        else:
            out.append({"doi": doi, "status": "not_found"})
    return out


def _next_batch(state: dict[str, Any], rated: dict[str, str] | None = None) -> dict[str, Any]:
    from .abstracts import clean_abstract, fill_abstracts
    from .search import _add_journal_rank, _flag_retractions
    rated = rated if rated is not None else _ratings(state)
    stop = _stop(state, rated)
    served_before = {k for b in state["served"] for k in b}
    order = [k for k in _ranked(state) if k not in rated]
    fresh = [k for k in order if k not in served_before]
    # Unrated papers from earlier batches come back first: the agent may have skipped some.
    pending = [k for k in order if k in served_before]
    picked = (pending + fresh)[: state["batch_size"]] if not stop["stop_suggested"] else []
    items = [dict(state["pool"][k]) for k in picked]
    if items:
        fill_abstracts([it for it in items if not it.get("abstract")], load_config())
        _add_journal_rank(items)
        _flag_retractions(items)
    batch = []
    for k, it in zip(picked, items):
        batch.append({
            "doi": it.get("doi"), "title": it["title"], "year": it.get("year"), "journal": it.get("journal"),
            "journal_rank": it.get("journal_rank"), "authors": it.get("authors"),
            "cited_by_count": it.get("cited_by_count"),
            "abstract": clean_abstract(it.get("abstract") or "")[:ABSTRACT_CHARS],
            "abstract_missing": not it.get("abstract"),
            "retracted": it.get("retracted") or None, "concern": it.get("concern") or None,
            "why": {"shared_refs_with_seeds": it.get("coupling", 0), "queries_matched": it.get("query_hits", 0),
                    "found_via": it.get("via", [])[:4]},
        })
    if picked:
        state["served"].append([k for k in picked if k not in served_before])
        _save(state)
    return {"session": state["session"], "pool": len(state["pool"]), "screened": len(rated),
            "remaining": len(order) - len(picked), "batch": batch,
            "coverage": _coverage(state, rated, _ranked(state)), **stop,
            "next_step": ("rate every paper in batch, save with litloop_screening(action='save', "
                          "session=..., mode='deep'), then call action='next'") if batch else
                         "stop: show the user the screening session and let them choose"}
