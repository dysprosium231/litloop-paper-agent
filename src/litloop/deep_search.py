"""Deep search (stage 2): find the literature on a topic, not just the top hits.

The agent supplies a question, 4-8 English queries, 1-3 core terms and a few
seed papers. The plugin builds a candidate pool from two complementary
routes and hands it to the agent in ranked batches for screening:

* keyword route: every query's raw candidates (100 each, nothing cut);
* citation route: works that cite any of the seeds' references and mention a
  core term in title/abstract, plus the seeds' own references and citing works.

On the acceptance sets neither route alone found most targets, together they
did (TODO.md, 2026-10-15). The first batch is ranked by

    score = shared references with the seeds (anchored to the user's seeds)
            + 2 x queries the record fully matches

From then on a small model trained on every rating so far ranks the rest
(continuous active learning, see ``_rerank``), and missing abstracts of the
top candidates are fetched before ranking. Papers the agent rates 高 join the
seeds, so later batches follow what the user actually wants (snowballing). A batch with almost nothing relevant, or
the screening cap, ends the search with a stated reason.

Ratings live in the screening session (``litloop_screening``); this module
keeps the pool next to it as ``<session>.pool.json``.
"""

from __future__ import annotations

import json
import math
import random
import re
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from . import screening
from .config import load_config
from .log import get_logger

logger = get_logger()

OPENALEX = "https://api.openalex.org/works"
_FIELDS = ("id,doi,title,publication_year,cited_by_count,referenced_works,"
           "abstract_inverted_index,primary_location,authorships,type")
# Not research papers: never worth a reader's time. Book chapters, theses and
# conference papers stay; the ranking model learns where they belong.
NON_PAPER = {"dataset", "paratext", "erratum", "reference-entry", "book-review", "editorial",
             "peer-review", "grant", "standard", "retraction", "supplementary-materials", "libguides"}
QUERY_WEIGHT = 2
ABSTRACT_CHARS = 1200   # per paper in a batch; a batch of 20 stays under ~30 KB for the agent
PREFILL = 150          # fetch missing abstracts for this many top candidates before ranking
MIN_LABELS = (3, 5)    # positives (高 + seeds), negatives (中/低/不相关) before the model takes over
PSEUDO_NEGATIVES = 100
TITLE_WEIGHT = 0.3     # extra weight of each title word in the ranking model
KNEE_RATIO = 6         # stop when 高 per batch falls below 1/6 of the best batch
WORKERS = 4            # parallel OpenAlex requests (each chunk of 50 ids is independent)


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
        "type": w.get("type") or "",
    }


def _year_filter(year_from: int | None, year_to: int | None) -> list[str]:
    out = []
    if year_from:
        out.append(f"from_publication_date:{year_from}-01-01")
    if year_to:
        out.append(f"to_publication_date:{year_to}-12-31")
    return out


def _parallel(fn, args: list) -> list:
    """``[fn(a) for a in args]`` with WORKERS threads, results in order and concatenated."""
    if len(args) <= 1:
        return [x for a in args for x in fn(a)]
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        return [x for part in ex.map(fn, args) for x in part]


def _chunks(values: list[str], n: int = 50) -> list[list[str]]:
    return [values[i:i + n] for i in range(0, len(values), n)]


def _by_filter(field: str, values: list[str], extra: list[str] | None = None) -> list[dict[str, Any]]:
    """Fetch works whose ``field`` is any of ``values`` (50 per request)."""
    def one(chunk: list[str]) -> list[dict[str, Any]]:
        flt = ",".join([f"{field}:" + "|".join(chunk)] + (extra or []))
        return _get({"filter": flt, "per-page": 50, "select": _FIELDS}).get("results") or []
    return [_record(w) for w in _parallel(one, _chunks(values))]


def _pages(flt: str, max_records: int) -> list[dict[str, Any]]:
    """Up to ``max_records`` works matching ``flt``, most cited first, so a cut drops the least cited."""
    out, cursor = [], "*"
    while cursor and len(out) < max_records:
        d = _get({"filter": flt, "per-page": 200, "cursor": cursor, "select": _FIELDS,
                  "sort": "cited_by_count:desc"})
        out += d.get("results") or []
        cursor = (d.get("meta") or {}).get("next_cursor")
    return [_record(w) for w in out[:max_records]]


def _citing(work_ids: list[str], years: list[str], max_records: int = 400) -> list[dict[str, Any]]:
    return _parallel(lambda chunk: _pages(",".join(["cites:" + "|".join(chunk)] + years), max_records),
                     _chunks(work_ids))


def _topic_search(term: str) -> str:
    """Core term -> OpenAlex search text. Words must all appear; ``a|b`` means either.

    Papers often write only the abbreviation ("Al/SiC") or the British spelling,
    so "Al|aluminum|aluminium SiC" becomes "(Al OR aluminum OR aluminium) AND SiC".
    """
    words = [w for w in term.replace(",", " ").split() if w.strip("|")]
    if not any("|" in w for w in words):
        return " ".join(words)
    parts = [("(" + " OR ".join(a for a in w.split("|") if a) + ")") if "|" in w else w for w in words]
    return " AND ".join(parts)


def _cites_refs_on_topic(ref_ids: list[str], core_terms: list[str], years: list[str],
                         max_records: int = 1000) -> list[dict[str, Any]]:
    """Works citing any of ``ref_ids`` whose title/abstract mentions a core term."""
    jobs = [(term, chunk) for term in (core_terms or [""]) for chunk in _chunks(ref_ids)]

    def flt(term: str, chunk: list[str]) -> str:
        topic = [f"title_and_abstract.search:{_topic_search(term)}"] if term else []
        return ",".join(["cites:" + "|".join(chunk)] + topic + years)

    def one(job: tuple[str, list[str]]) -> list[tuple[bool, list[dict[str, Any]]]]:
        term, chunk = job
        if len(chunk) > 1:  # ask for the count first: fetching 1000 records only to split is slow
            count = (_get({"filter": flt(term, chunk), "per-page": 1, "select": "id"}).get("meta") or {}).get("count", 0)
            if count > max_records:
                return [(True, [])]
        return [(False, _pages(flt(term, chunk), max_records))]

    out: list[dict[str, Any]] = []
    while jobs:  # a query over the cap is split in halves and asked again, all in parallel
        results = _parallel(one, jobs)
        again = []
        for (term, chunk), (split, got) in zip(jobs, results):
            if split:
                half = len(chunk) // 2
                again += [(term, chunk[:half]), (term, chunk[half:])]
            else:
                out += got
        jobs = again
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
    if not k or not rec.get("title") or rec.get("type") in NON_PAPER:
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


def _score(state: dict[str, Any]) -> None:
    """Formula score: anchored coupling + QUERY_WEIGHT x fully matched queries.

    References of the user's own seeds count fully; references that only
    snowballed seeds cite count by how many seeds share them (1/3 each, at most
    1), so a seed set drifting toward a neighbouring topic cannot outvote the
    original intent.
    """
    from .search import _match_tier
    pool = state["pool"]
    seed_refs = set(state["seed_refs"])
    orig_refs = {r for s in _orig_seeds(state) for r in (pool.get(s, {}).get("refs") or [])}
    seed_df = Counter(r for s in state["seeds"] for r in set(pool.get(s, {}).get("refs") or []))
    hits = {k: 0 for k in pool}
    for q in state["queries"]:
        rows = [{"title": r["title"], "abstract": r.get("abstract") or "", "_k": k} for k, r in pool.items()]
        for row in _match_tier(rows, q):
            if row["_tier"] == 0:
                hits[row["_k"]] += 1
    for k, r in pool.items():
        shared = [ref for ref in (r.get("refs") or []) if ref in seed_refs]
        r["coupling"] = len(shared)
        r["anchored"] = round(sum(1.0 if ref in orig_refs else min(1.0, seed_df[ref] / 3) for ref in shared), 2)
        r["query_hits"] = hits[k]
        r["score"] = r["anchored"] + QUERY_WEIGHT * r["query_hits"]
        r["rank"] = r["score"]


def _orig_seeds(state: dict[str, Any]) -> list[str]:
    return state.get("orig_seeds") or state["seeds"][:3]


# --------------------------------------------------------------------------- ranking model
#
# Continuous active learning, the usual way screening tools for systematic
# reviews rank: after every batch, train a small logistic regression on all
# ratings so far (高 and the seeds against 中/低/不相关, plus a random sample of
# unscreened papers standing in as negatives) and rank the rest by it.
# Negative ratings pull the ranking back when snowballing drifts. On the
# SiC/Al set, after the first 90 ratings, the 6 targets still unscreened
# ranked [18, 39, 68, 70, 98, 1196] by the old formula and [9, 19, 30, 38, 52,
# 56] by the model (TODO.md, 2026-10-15).

_WORD = re.compile(r"[a-z][a-z0-9]+")
_STOP = set("the and of in for with by on to a an is are was were from as at this that these be or its "
            "their which we our into than also".split())


def _tokens(rec: dict[str, Any]) -> Counter:
    text = (rec.get("title") or "") + " " + (rec.get("abstract") or "")
    return Counter(w[:7] for w in _WORD.findall(text.lower()) if w not in _STOP)


def _features(state: dict[str, Any]) -> dict[str, dict[str, float]]:
    pool = state["pool"]
    docs = {k: _tokens(r) for k, r in pool.items()}
    df = Counter(w for c in docs.values() for w in c)
    n, nq = len(pool), max(1, len(state["queries"]))
    out = {}
    for k, c in docs.items():
        v = {w: (1 + math.log(m)) * math.log(n / df[w]) for w, m in c.items() if df[w] >= 2}
        norm = math.sqrt(sum(x * x for x in v.values())) or 1.0
        v = {w: x / norm for w, x in v.items()}
        r = pool[k]
        # Title words again, on their own: a title says what the paper is about
        # better than the abstract, and is all there is when the abstract is missing.
        v.update({"t:" + w: TITLE_WEIGHT for w in _tokens({"title": r.get("title")})})
        v["_coupling"] = math.log1p(r.get("anchored", 0)) / 3
        v["_queries"] = r.get("query_hits", 0) / nq
        v["_no_abstract"] = 0.0 if r.get("abstract") else 1.0
        out[k] = v
    return out


def _train(examples: list[tuple[dict[str, float], int]], epochs: int = 60, lr: float = 0.5,
           l2: float = 1e-3) -> Counter:
    w: Counter = Counter()
    for _ in range(epochs):
        for v, y in examples:
            z = sum(w[f] * x for f, x in v.items()) + w["_bias"]
            g = 1 / (1 + math.exp(-max(-30.0, min(30.0, z)))) - y
            for f, x in v.items():
                w[f] -= lr * (g * x + l2 * w[f])
            w["_bias"] -= lr * g
    return w


def _rerank(state: dict[str, Any], rated: dict[str, str]) -> str:
    """Set every record's ``rank``; returns "model" or "formula"."""
    pool, seeds = state["pool"], set(state["seeds"])
    pos = {k for k, r in rated.items() if r == "高" and k in pool} | {k for k in seeds if k in pool}
    neg = [k for k, r in rated.items() if r != "高" and k in pool]
    if len(pos) < MIN_LABELS[0] or len(neg) < MIN_LABELS[1]:
        for r in pool.values():
            r["rank"] = r.get("score", 0)
        return "formula"
    rng = random.Random(len(rated))  # same ratings, same ranking
    unscreened = sorted(k for k in pool if k not in rated and k not in seeds)
    pseudo = rng.sample(unscreened, min(PSEUDO_NEGATIVES, len(unscreened)))
    feats = _features(state)
    examples = [(feats[k], 1) for k in sorted(pos)] + [(feats[k], 0) for k in neg + pseudo]
    rng.shuffle(examples)
    w = _train(examples)
    for k, r in pool.items():
        r["rank"] = round(sum(w[f] * x for f, x in feats[k].items()) + w["_bias"], 4)
    return "model"


def _prefill_abstracts(state: dict[str, Any], rated: dict[str, str]) -> int:
    """Fetch abstracts for the top unscreened candidates that lack one, once each.

    Ranking reads abstracts, so a paper without one used to sink and never
    reach a batch, which is where its abstract would have been fetched.
    """
    from .abstracts import fill_abstracts
    # Pick among the papers that lack one: they rank low for that very reason, so
    # taking the overall top PREFILL would reach almost none of them.
    todo = [r for k in _ranked(state) if k not in rated
            for r in [state["pool"][k]] if not r.get("abstract") and not r.get("abstract_tried") and r.get("doi")]
    todo = todo[:PREFILL]
    if not todo:
        return 0
    fill_abstracts(todo, load_config(), budget_s=30.0)
    for r in todo:
        r["abstract_tried"] = True
    return sum(1 for r in todo if r.get("abstract"))


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
          batch_size: int = 20, cap: int = 200) -> dict[str, Any]:
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
             "orig_seeds": [_key(r) for r in seed_recs],
             "year_from": year_from, "year_to": year_to, "batch_size": batch_size, "cap": cap,
             "must_find": [d.lower() for d in (must_find or [])], "seeds": [], "seed_refs": [],
             "queried_refs": [], "served": [], "rounds": [], "pool": pool}
    _expand(state, [_key(r) for r in seed_recs])
    _score(state)
    state["rounds"].append({"time": time.strftime("%Y-%m-%d %H:%M:%S"), "event": "start",
                            "pool": len(pool), "seconds": round(time.time() - t0, 1)})
    _save(state)
    return _next_batch(state)


def next_batch(session: str) -> dict[str, Any]:
    """Fold the agent's latest ratings back in (高 papers become seeds) and serve the next batch."""
    t0 = time.time()
    state = _load(session)
    rated = _ratings(state)
    new_seeds = [k for k, rel in rated.items() if rel == "高" and k in state["pool"] and k not in state["seeds"]]
    added = 0
    if new_seeds:
        added = _expand(state, new_seeds)
        _score(state)
    t1 = time.time()
    rnd = {"time": time.strftime("%Y-%m-%d %H:%M:%S"), "event": "next", "new_seeds": len(new_seeds),
           "pool_added": added, "expand_seconds": round(t1 - t0, 1)}
    state["rounds"].append(rnd)
    _save(state)
    out = _next_batch(state, rated)
    rnd["rank_seconds"] = round(time.time() - t1, 1)  # abstracts, model, journal ranks
    _save(state)
    return out


def rate(session: str, ratings: list[dict[str, Any]]) -> dict[str, Any]:
    """Save the agent's ratings for the latest batch by number: [{"n": 3, "relevance": "高", "reason": "..."}].

    Title, DOI, year and journal come from the batch, so the agent writes only
    its judgement. Nothing is saved if any number or relevance is invalid.
    """
    state = _load(session)
    batch = state.get("last_batch") or []
    if not batch:
        raise ValueError("no batch to rate yet; call action='start' or 'next' first")
    items, bad = [], []
    for r in ratings or []:
        try:
            n = int(r.get("n"))
        except (TypeError, ValueError):
            n = 0
        rel = str(r.get("relevance") or "").strip()
        if not 1 <= n <= len(batch) or rel not in screening.RELEVANCE:
            bad.append(r)
            continue
        b = batch[n - 1]
        items.append({**{f: b.get(f) for f in ("doi", "title", "year", "journal", "journal_rank", "retracted", "concern")},
                      "relevance": rel, "reason": str(r.get("reason") or rel)})
    if bad:
        raise ValueError(f"nothing saved: invalid entries {bad[:3]}; n is 1-{len(batch)}, "
                         f"relevance one of {'/'.join(screening.RELEVANCE)}")
    saved = screening.save("", items, session=session, mode="deep")
    done = {int(r["n"]) for r in ratings}
    return {"session": session, "saved": len(items), "total": saved["total"], "by_relevance": saved["by_relevance"],
            "not_rated": [i for i in range(1, len(batch) + 1) if i not in done],
            "next_step": "action='next'" if len(done) == len(batch) else "rate the papers in not_rated, then action='next'"}


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
    pool = state["pool"]
    return sorted((k for k in pool if k not in seeds),
                  key=lambda k: (-pool[k].get("rank", pool[k].get("score", 0)), -(pool[k].get("cited_by_count") or 0)))


def _stop(state: dict[str, Any], rated: dict[str, str]) -> dict[str, Any]:
    """Suggest stopping at the cap, or once the yield of 高 has fallen off.

    Knee rule, as in screening for systematic reviews: when the share of 高 in
    the latest batch is below 1/KNEE_RATIO of the best earlier batch, the
    relevant papers have mostly been read. Counting 中 too never fired: the
    neighbouring topics that come up late keep it high.
    """
    if len(rated) >= state["cap"]:
        return {"stop_suggested": True, "stop_reason": f"已筛 {len(rated)} 篇，达到上限 {state['cap']}"}
    batches = [b for b in state["served"] if b]
    last = batches[-1] if batches else []
    judged = [rated[k] for k in last if k in rated]
    if len(batches) < 2 or not judged or len(judged) < len(last) // 2:
        return {"stop_suggested": False, "stop_reason": ""}
    high = sum(1 for r in judged if r == "高")
    rates = [sum(1 for k in b if rated.get(k) == "高") / max(1, sum(1 for k in b if k in rated)) for b in batches[:-1]]
    best = max(rates)
    if best > 0 and high / len(judged) <= best / KNEE_RATIO:
        return {"stop_suggested": True,
                "stop_reason": (f"最近一批 {len(judged)} 篇里只有 {high} 篇高相关，不到此前最好一批"
                                f"（{best:.0%}）的 1/{KNEE_RATIO}，相关文章大多已经读到")}
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
    if not stop["stop_suggested"]:
        if _prefill_abstracts(state, rated):
            _score(state)
        state["ranked_by"] = _rerank(state, rated)
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
    for n, (k, it) in enumerate(zip(picked, items), 1):
        batch.append({
            "n": n, "doi": it.get("doi"), "title": it["title"], "year": it.get("year"), "journal": it.get("journal"),
            "journal_rank": it.get("journal_rank"), "authors": (it.get("authors") or [])[:3],
            "cited_by_count": it.get("cited_by_count"),
            "abstract": clean_abstract(it.get("abstract") or "")[:ABSTRACT_CHARS],
            "abstract_missing": not it.get("abstract"),
            "retracted": it.get("retracted") or None, "concern": it.get("concern") or None,
            "why": {"shared_refs_with_seeds": it.get("coupling", 0), "queries_matched": it.get("query_hits", 0),
                    "found_via": it.get("via", [])[:2]},
        })
    if picked:
        state["served"].append([k for k in picked if k not in served_before])
        # What action="rate" needs to turn "n=3 高" back into a screening item.
        state["last_batch"] = [{"key": k, **{f: it.get(f) for f in ("doi", "title", "year", "journal", "journal_rank",
                                                                     "retracted", "concern")}}
                               for k, it in zip(picked, items)]
        _save(state)
    return {"session": state["session"], "pool": len(state["pool"]), "screened": len(rated),
            "remaining": len(order) - len(picked), "ranked_by": state.get("ranked_by", "formula"), "batch": batch,
            "coverage": _coverage(state, rated, _ranked(state)), **stop,
            "next_step": ("rate every paper: action='rate', session=..., ratings=[{n, relevance, reason}], "
                          "then action='next'") if batch else
                         "stop: show the user the screening session and let them choose"}
