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
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from . import screening, store, translate
from .config import load_config
from .identifiers import canonical_doi
from .log import get_logger

logger = get_logger()
_run = threading.local()  # the round being worked on in this thread, for progress reports


def _phase(name: str, total: int | None = None) -> None:
    p = getattr(_run, "progress", None)
    if p:
        p.phase(name, total)


def _step(done: int, total: int | None = None) -> None:
    p = getattr(_run, "progress", None)
    if p:
        p.step(done, total)

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
# OpenAlex budget guards (LitLoop's own choices, not official limits; the daily budget
# itself is in docs/API_LIMITS.md). A reference cited by thousands of on-topic papers
# (common in biomedicine) used to cost 50 credits for its 1000 most cited citers;
# one 2026-10-16 round spent 2184 credits this way.
SINGLE_REF_CAP = 200   # one reference over the cap: keep only its 200 most cited citers (one page)
ROUND_CREDITS = 800    # stop asking for more citers once a round has spent this many credits
RESERVE_CREDITS = 1000 # and leave this much of the daily budget for everything else


# --------------------------------------------------------------------------- OpenAlex

def _boolean_heavy(params: dict[str, Any]) -> bool:
    """OpenAlex allows only 5 requests/s for searches with more than five boolean operators."""
    text = " ".join(str(v) for v in params.values())
    return len(re.findall(r"(?:OR|AND|NOT)", text)) > 5


def _get(params: dict[str, Any], *, tries: int = 6) -> dict[str, Any]:
    """One OpenAlex request, paced and counted by ``quota``. A 429 is retried with
    growing waits; a spent daily budget (remaining 0) is reported plainly."""
    from . import quota
    from .search import _plain_session
    key = str(load_config().get("openalex_api_key") or "").strip()
    if key:
        params = {**params, "api_key": key}
    lane = ("openalex_boolean" if _boolean_heavy(params) else "openalex") + ("" if key else "_nokey")
    last: Exception | None = None
    for attempt in range(tries):
        wait = min(30.0, 1.5 * 2 ** attempt)
        try:
            with quota.slot(lane):
                r = _plain_session().get(OPENALEX, params=params, timeout=60)
            quota.record("openalex", r)
            remaining = r.headers.get("X-RateLimit-Remaining")
            if r.status_code == 200:
                return r.json()
            if r.status_code == 429 and remaining == "0":
                hours = round(int(r.headers.get("X-RateLimit-Reset") or 0) / 3600, 1)
                raise RuntimeError(f"OpenAlex daily budget used up ({r.headers.get('X-RateLimit-Limit')} "
                                   f"credits); it resets in about {hours} h (midnight UTC)")
            if r.status_code == 429:
                wait = max(wait, float(r.headers.get("Retry-After") or 0))
            last = RuntimeError(f"OpenAlex HTTP {r.status_code}")
        except RuntimeError:
            raise
        except Exception as exc:  # network hiccup: retry
            last = exc
        time.sleep(wait)
    raise RuntimeError(f"OpenAlex request failed: {last}")


def _short(wid: str) -> str:
    return str(wid or "").rsplit("/", 1)[-1]


def _record(w: dict[str, Any]) -> dict[str, Any]:
    from .search import _reconstruct_abstract
    src = ((w.get("primary_location") or {}).get("source") or {})
    return {
        "openalex": _short(w.get("id")),
        "doi": canonical_doi(w.get("doi")) or None,
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


def _pages(flt: str, max_records: int, *, split_over: bool = False) -> list[dict[str, Any]] | None:
    """Up to ``max_records`` works matching ``flt``, most cited first, so a cut drops the least cited.

    With ``split_over``, return None as soon as the first page shows more than
    ``max_records`` matches, so the caller can split the query instead; the
    first page already carries the count, so no separate count request is
    needed (each topic-search request costs 10 OpenAlex credits).
    """
    # Cursor paging past the first page needs an API key; without one, page numbers
    # (allowed up to 10,000 results) do the same job.
    keyed = bool(str(load_config().get("openalex_api_key") or "").strip())
    out, cursor, page = [], "*", 1
    while len(out) < max_records:
        params = {"filter": flt, "per-page": 200, "select": _FIELDS, "sort": "cited_by_count:desc"}
        params.update({"cursor": cursor} if keyed else {"page": page})
        d = _get(params)
        meta = d.get("meta") or {}
        if split_over and page == 1 and (meta.get("count") or 0) > max_records:
            return None
        rows = d.get("results") or []
        out += rows
        page += 1
        cursor = meta.get("next_cursor")
        if not rows or (keyed and not cursor) or len(out) >= (meta.get("count") or 0):
            break
    return [_record(w) for w in out[:max_records]]


def _citing(work_ids: list[str], years: list[str], max_records: int = 400) -> list[dict[str, Any]]:
    return _parallel(lambda chunk: _pages(",".join(["cites:" + "|".join(chunk)] + years), max_records),
                     _chunks(work_ids))


def _topic_search(term: str) -> str:
    """Core term -> OpenAlex search text. Words must all appear; ``a|b`` means either.

    Papers often write only the abbreviation ("Al/SiC"), so the agent writes
    "Al|aluminum SiC"; British spellings are added here, giving
    "(Al OR aluminum OR aluminium) AND SiC".
    """
    from .spelling import variants
    groups = []
    for w in term.replace(",", " ").split():
        alts = [v for a in w.split("|") if a for v in variants(a)]
        if alts:
            groups.append(list(dict.fromkeys(alts)))
    if all(len(g) == 1 for g in groups):
        return " ".join(g[0] for g in groups)
    return " AND ".join(("(" + " OR ".join(g) + ")") if len(g) > 1 else g[0] for g in groups)


def _budget_ok(credits0: int) -> bool:
    """True while this round may still spend OpenAlex credits (see ROUND_CREDITS, RESERVE_CREDITS)."""
    from . import quota
    if quota.spent("openalex") - credits0 >= ROUND_CREDITS:
        return False
    remaining = (quota.summary().get("openalex") or {}).get("remaining")
    return remaining is None or remaining > RESERVE_CREDITS


def _cites_refs_on_topic(ref_ids: list[str], core_terms: list[str], years: list[str],
                         max_records: int = 1000, credits0: int | None = None,
                         ) -> tuple[list[dict[str, Any]], set[str]]:
    """Works citing any of ``ref_ids`` whose title/abstract mentions every core term.

    Each core term is one concept the paper must mention (``a|b`` = either);
    the terms are joined into one search, so ["lanthanide|europium",
    "drug|doxorubicin"] means rare earth AND drug. (Until 2026-10-16 each term
    was a separate search and a paper matching any one was kept, which let
    27,000 drug-delivery papers without rare earths into one pool.)
    ``ref_ids`` come most important first. Returns the works and the references
    actually asked about: once the round's credit budget is spent, the rest
    are left for a later round.
    """
    from . import quota
    credits0 = quota.spent("openalex") if credits0 is None else credits0
    term = " ".join(t.strip() for t in core_terms or [] if t.strip())
    jobs = [(term, chunk) for chunk in _chunks(ref_ids)]

    def flt(term: str, chunk: list[str]) -> str:
        topic = [f"title_and_abstract.search:{_topic_search(term)}"] if term else []
        return ",".join(["cites:" + "|".join(chunk)] + topic + years)

    def one(job: tuple[str, list[str]]) -> list[tuple[bool, list[dict[str, Any]]]]:
        term, chunk = job
        single = len(chunk) == 1
        got = _pages(flt(term, chunk), SINGLE_REF_CAP if single else max_records, split_over=not single)
        return [(True, [])] if got is None else [(False, got)]

    out: list[dict[str, Any]] = []
    asked: set[str] = set()
    _step(0, len(ref_ids))
    while jobs:  # a query over the cap is split in halves; a few jobs at a time, budget permitting
        if not _budget_ok(credits0):
            break
        wave, jobs = jobs[:WORKERS * 2], jobs[WORKERS * 2:]
        again = []
        for (term, chunk), (split, got) in zip(wave, _parallel(one, wave)):
            if split:
                half = len(chunk) // 2
                again += [(term, chunk[:half]), (term, chunk[half:])]
            else:
                out += got
                asked.update(chunk)
        _step(len(asked))
        jobs = again + jobs  # finish the halves of important references first
    return out, asked


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
    doi = rec.get("doi") or ""
    if doi.startswith("10.1002/ange."):  # Angewandte's German edition: same article as 10.1002/anie.*
        rec = {**rec, "doi": "10.1002/anie." + doi[len("10.1002/ange."):]}
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
    from .spelling import to_us
    text = (rec.get("title") or "") + " " + (rec.get("abstract") or "")
    return Counter(w[:7] for w in _WORD.findall(to_us(text)) if w not in _STOP)


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
    _phase("abstracts", len(todo))
    # Scopus answers about 3 papers a second at its allowed pace: budget for that.
    fill_abstracts(todo, load_config(), budget_s=min(90.0, max(30.0, len(todo) / 3)), progress=_step)
    for r in todo:
        if r.get("abstract") or r.pop("abstract_decided", False):
            r["abstract_tried"] = True  # undecided ones (timeout, throttled) are asked again next round
    return sum(1 for r in todo if r.get("abstract"))


def _pool_path(session: str):
    return screening.screening_dir() / f"{session}.pool.json"


def _load(session: str) -> dict[str, Any]:
    p = _pool_path(session)
    if not p.exists():
        raise FileNotFoundError(f"no deep search for session {session!r}")
    return store.read_json(p)


def _save(state: dict[str, Any]) -> None:
    store.write_json(_pool_path(state["session"]), state)  # whole file at once: a crash keeps the old one


def _expand(state: dict[str, Any], new_seed_keys: list[str]) -> int:
    """Add citation-route candidates for new seeds; returns how many records were added."""
    pool = state["pool"]
    years = _year_filter(state.get("year_from"), state.get("year_to"))
    from . import quota
    credits0 = quota.spent("openalex")
    seeds = [pool[k] for k in new_seed_keys if k in pool]
    # References shared by more seeds (the user's own seeds count double) are asked first,
    # so a spent budget drops the least informative ones; they stay for a later round.
    orig = set(_orig_seeds(state))
    weight = Counter(r for k in set(state["seeds"]) | set(new_seed_keys) if k in pool
                     for r in set(pool[k].get("refs") or []) for _ in range(2 if k in orig else 1))
    all_seeds = seeds + [pool[k] for k in state["seeds"] if k in pool]
    new_refs = sorted({r for s in all_seeds for r in (s.get("refs") or [])} - set(state["queried_refs"]),
                      key=lambda r: (-weight[r], r))
    before = len(pool)
    asked: set[str] = set()
    _phase("expand", len(new_refs))
    if new_refs:
        for rec in _by_filter("openalex", new_refs, years):
            _add(pool, rec, "seed_reference")
        got, asked = _cites_refs_on_topic(new_refs, state["core_terms"], years, credits0=credits0)
        for rec in got:
            _add(pool, rec, "cites_seed_references")
    seed_ids = [s["openalex"] for s in seeds if s.get("openalex")]
    if seed_ids and _budget_ok(credits0):
        for rec in _citing(seed_ids, years):
            _add(pool, rec, "cites_seed")
    deferred = len(new_refs) - len(asked)
    state["deferred_refs"] = deferred
    if deferred:
        logger.info("deep search: OpenAlex budget for this round used; %d references left for later", deferred)
    state["queried_refs"] = sorted(set(state["queried_refs"]) | asked)
    state["seed_refs"] = sorted(set(state["seed_refs"]) | {r for s in seeds for r in (s.get("refs") or [])})
    for k in new_seed_keys:
        if k not in state["seeds"]:
            state["seeds"].append(k)
    return len(pool) - before


# --------------------------------------------------------------------------- public

# Element symbols that are also everyday words (OpenAlex search ignores case): "La", "Ce"
# match French, "Er" German, "Eu" the EU, "In"/"As"/"Be"/"He"/"No" English.
AMBIGUOUS_SYMBOLS = {"as", "at", "be", "ce", "er", "es", "eu", "he", "in", "la", "no"}


def _core_term_notes(core_terms: list[str]) -> list[str]:
    """Warnings about core terms that would make the citation route far too broad."""
    alts = {a.lower() for term in core_terms for w in term.replace(",", " ").split() for a in w.split("|") if a}
    loose = sorted(alts & AMBIGUOUS_SYMBOLS)
    if not loose:
        return []
    return ["core_terms contain element symbols that are also common words (" + ", ".join(loose)
            + "; search ignores case): write the element's full name instead"]


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
    _run.progress = store.Progress(session, "start")
    try:
        return _start(session, t0, question, queries, seeds, core_terms, must_find, year_from, year_to,
                      batch_size, cap)
    except BaseException:
        _run.progress.finish("failed")
        raise
    finally:
        _run.progress = None


def _start(session: str, t0: float, question: str, queries: list[str], seeds: list[str],
           core_terms: list[str], must_find: list[str] | None, year_from: int | None, year_to: int | None,
           batch_size: int, cap: int) -> dict[str, Any]:
    _phase("keywords", len(queries))
    years = _year_filter(year_from, year_to)
    pool: dict[str, dict] = {}
    seed_recs = _by_filter("doi", [canonical_doi(s) for s in seeds])
    for rec in seed_recs:
        _add(pool, rec, "seed")
    for q, rows in _keyword_candidates(queries, year_from, year_to).items():
        for r in rows:
            _add(pool, {"openalex": None, "doi": canonical_doi(r.get("doi")) or None,
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
             "must_find": [canonical_doi(d) for d in (must_find or [])], "seeds": [], "seed_refs": [],
             "queried_refs": [], "served": [], "rounds": [], "pool": pool}
    _expand(state, [_key(r) for r in seed_recs])
    _score(state)
    state["rounds"].append({"time": time.strftime("%Y-%m-%d %H:%M:%S"), "event": "start",
                            "pool": len(pool), "seconds": round(time.time() - t0, 1)})
    _save(state)
    out = _next_batch(state)
    notes = _core_term_notes(core_terms)
    if notes:
        out["core_term_notes"] = notes
    _run.progress.finish()
    return out


def next_batch(session: str) -> dict[str, Any]:
    """Fold the agent's latest ratings back in (高 papers become seeds) and serve the next batch."""
    _run.progress = store.Progress(session, "next")
    try:
        out = _next_round(session)
    except BaseException:
        _run.progress.finish("failed")
        raise
    finally:
        p, _run.progress = _run.progress, None
    p.finish()
    return out


def _next_round(session: str) -> dict[str, Any]:
    from . import quota
    t0 = time.time()
    credits0 = quota.spent("openalex")
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
    rnd["openalex_credits"] = quota.spent("openalex") - credits0
    rnd["api_budget"] = {k: {f: v.get(f) for f in ("remaining", "limit", "resets_at")}
                         for k, v in quota.summary().items()}
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
        items.append({**{f: b.get(f) for f in _BATCH_FIELDS},
                      "relevance": rel, "reason": str(r.get("reason") or rel), "abstract_zh": r.get("abstract_zh")})
    if bad:
        raise ValueError(f"nothing saved: invalid entries {bad[:3]}; n is 1-{len(batch)}, "
                         f"relevance one of {'/'.join(screening.RELEVANCE)}")
    saved = screening.save("", items, session=session, mode="deep")
    done = {int(r["n"]) for r in ratings}
    out = {"session": session, "saved": len(items), "total": saved["total"], "by_relevance": saved["by_relevance"],
           "not_rated": [i for i in range(1, len(batch) + 1) if i not in done],
           "next_step": "action='next'" if len(done) == len(batch) else "rate the papers in not_rated, then action='next'"}
    left = screening.untranslated(session)
    if left:  # the app shows these abstracts in Chinese
        out["untranslated"] = left
        out["next_step"] = ("translate their abstracts into Chinese: litloop_screening(action='translate', session=..., "
                            "items=[{doi, abstract_zh}]); then " + out["next_step"])
    return out


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
            out[key] = screening._rating(it)  # the user's own rating (from the app) wins
    return out


def _ranked(state: dict[str, Any]) -> list[str]:
    seeds = set(state["seeds"])
    pool = state["pool"]
    return sorted((k for k in pool if k not in seeds),
                  key=lambda k: (-pool[k].get("rank", pool[k].get("score", 0)), -(pool[k].get("cited_by_count") or 0)))


def _remaining_estimate(state: dict[str, Any], rated: dict[str, str]) -> dict[str, Any] | None:
    """Roughly how many 高 papers are still unread, from the yield curve.

    The model's scores are not calibrated probabilities (it is trained against a
    random sample of unread papers), so the estimate comes from what the ratings
    show instead: fit a geometric decay to the share of 高 per batch (least
    squares on log share, recent batches weighted more) and sum the batches
    still ahead, as far as the pool goes. Needs at least three rated batches;
    a flat or rising curve has no finite tail and gives no total.
    """
    batches = [b for b in state["served"] if b]
    rates, sizes = [], []
    for b in batches:
        judged = [k for k in b if k in rated]
        if len(judged) < max(3, len(b) // 2):
            continue
        rates.append(sum(1 for k in judged if rated[k] == "高") / len(judged))
        sizes.append(len(judged))
    if len(rates) < 3:
        return None
    size = state.get("batch_size") or len(batches[-1])
    xs = list(range(len(rates)))
    ys = [math.log(r + 0.02) for r in rates]  # 0.02: a batch without 高 is not log(0)
    ws = [1 + i for i in xs]
    sw = sum(ws)
    mx = sum(w * x for w, x in zip(ws, xs)) / sw
    my = sum(w * y for w, y in zip(ws, ys)) / sw
    var = sum(w * (x - mx) ** 2 for w, x in zip(ws, xs))
    slope = sum(w * (x - mx) * (y - my) for w, x, y in zip(ws, xs, ys)) / var if var else 0.0
    q = math.exp(slope)
    last = max(0.0, math.exp(my + slope * (xs[-1] - mx)) - 0.02)
    seeds = set(state.get("seeds") or [])
    unread = sum(1 for k in state.get("pool") or {} if k not in rated and k not in seeds)
    out = {"next_batch": round(last * q * size, 1), "batches": len(rates), "decay": round(q, 2)}
    if q < 0.95 and unread:
        ahead = min(unread // max(1, size), 50)
        out["remaining"] = round(sum(last * q ** j * size for j in range(1, ahead + 1)), 1)
    return out


def _stop(state: dict[str, Any], rated: dict[str, str]) -> dict[str, Any]:
    """Suggest stopping at the cap, or once the yield of 高 has fallen off.

    Knee rule, as in screening for systematic reviews: when the share of 高 in
    the latest batch is below 1/KNEE_RATIO of the best earlier batch, the
    relevant papers have mostly been read. Counting 中 too never fired: the
    neighbouring topics that come up late keep it high.
    """
    est = _remaining_estimate(state, rated)
    if len(rated) >= state["cap"]:
        return _with_estimate({"stop_suggested": True, "stop_reason": f"已筛 {len(rated)} 篇，达到上限 {state['cap']}"}, est)
    batches = [b for b in state["served"] if b]
    last = batches[-1] if batches else []
    judged = [rated[k] for k in last if k in rated]
    if len(batches) < 2 or not judged or len(judged) < len(last) // 2:
        return _with_estimate({"stop_suggested": False, "stop_reason": ""}, est)
    high = sum(1 for r in judged if r == "高")
    rates = [sum(1 for k in b if rated.get(k) == "高") / max(1, sum(1 for k in b if k in rated)) for b in batches[:-1]]
    best = max(rates)
    if best > 0 and high / len(judged) <= best / KNEE_RATIO:
        return _with_estimate({"stop_suggested": True,
                               "stop_reason": (f"最近一批 {len(judged)} 篇里只有 {high} 篇高相关，不到此前最好一批"
                                               f"（{best:.0%}）的 1/{KNEE_RATIO}，相关文章大多已经读到")}, est)
    useful = sum(1 for r in judged if r in ("高", "中"))
    if useful <= max(1, len(judged) // 15):
        return _with_estimate({"stop_suggested": True,
                               "stop_reason": f"最近一批 {len(judged)} 篇里只有 {useful} 篇高或中相关，新增已经很少"}, est)
    return _with_estimate({"stop_suggested": False, "stop_reason": ""}, est)


def _with_estimate(stop: dict[str, Any], est: dict[str, Any] | None) -> dict[str, Any]:
    """Attach the estimate; when stopping is suggested, also say it in the reason."""
    if est is None:
        return stop
    stop["remaining_estimate"] = est
    if stop["stop_suggested"] and "remaining" in est:
        stop["stop_reason"] += (f"。按每批高相关比例的下降趋势粗略外推，继续读下去约还能找到 "
                                f"{est['remaining']:.0f} 篇高相关（下一批约 {est['next_batch']:.0f} 篇）")
    return stop


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


# What a rated paper keeps in the screening record, so the app can show it
# (abstract and authors included: the app never reads the pool).
_BATCH_FIELDS = ("doi", "title", "year", "journal", "journal_rank", "retracted", "concern",
                 "authors", "cited_by_count", "abstract", "abstract_missing")


def _write_summary(state: dict[str, Any], rated: dict[str, str], stop: dict[str, Any],
                   coverage: list[dict[str, Any]], notes: list[str], remaining: int,
                   batch: list[dict[str, Any]]) -> None:
    """A small overview of the session for the app, which never reads the (huge) pool."""
    batches = []
    for i, keys in enumerate(b for b in state["served"] if b):
        counts = {r: sum(1 for k in keys if rated.get(k) == r) for r in screening.RELEVANCE}
        batches.append({"batch": i + 1, "size": len(keys), "rated": sum(counts.values()), **counts})
    rounds = [{f: r.get(f) for f in ("time", "event", "new_seeds", "pool_added", "expand_seconds",
                                     "rank_seconds", "openalex_credits", "seconds") if r.get(f) is not None}
              for r in state["rounds"]]
    summary = {"session": state["session"], "question": state["question"], "mode": "deep",
               "updated": time.strftime("%Y-%m-%d %H:%M:%S"), "pool": len(state["pool"]),
               "screened": len(rated), "remaining": remaining, "ranked_by": state.get("ranked_by", "formula"),
               "seeds": len(state["seeds"]), "batches": batches, "rounds": rounds,
               "stop_suggested": stop.get("stop_suggested", False), "stop_reason": stop.get("stop_reason", ""),
               "remaining_estimate": stop.get("remaining_estimate"),
               "coverage": coverage, "api_warnings": notes, "core_terms": state.get("core_terms", []),
               "queries": state.get("queries", []), "current_batch": batch}
    try:
        store.write_json(store.summary_path(state["session"]), summary)
    except OSError as exc:
        logger.warning("deep search: could not write the session summary (%s)", exc)


def _next_batch(state: dict[str, Any], rated: dict[str, str] | None = None) -> dict[str, Any]:
    from .abstracts import clean_abstract, fill_abstracts
    from .search import _add_journal_rank, _flag_retractions
    rated = rated if rated is not None else _ratings(state)
    stop = _stop(state, rated)
    if not stop["stop_suggested"]:
        if _prefill_abstracts(state, rated):
            _score(state)
        _phase("rank")
        state["ranked_by"] = _rerank(state, rated)
    served_before = {k for b in state["served"] for k in b}
    order = [k for k in _ranked(state) if k not in rated]
    fresh = [k for k in order if k not in served_before]
    # Unrated papers from earlier batches come back first: the agent may have skipped some.
    pending = [k for k in order if k in served_before]
    picked = (pending + fresh)[: state["batch_size"]] if not stop["stop_suggested"] else []
    items = [dict(state["pool"][k]) for k in picked]
    if items:
        _phase("batch", len(items))
        fill_abstracts([it for it in items if not it.get("abstract")], load_config(), progress=_step)
        for k, it in zip(picked, items):  # keep what was found, so ranking can use it too
            if it.get("abstract") and not state["pool"][k].get("abstract"):
                state["pool"][k]["abstract"] = it["abstract"]
                state["pool"][k]["abstract_source"] = it.get("abstract_source")
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
        state["last_batch"] = [{"key": k, **{f: b.get(f) for f in _BATCH_FIELDS}} for k, b in zip(picked, batch)]
        _save(state)
    from . import quota
    notes = quota.warnings()
    if not str(load_config().get("openalex_api_key") or "").strip():
        notes.insert(0, "no OpenAlex API key: the free daily budget without one is about 1,000 credits, "
                        "which one deep search can use up; add a free key (openalex_api_key)")
    if state.get("deferred_refs"):
        notes.append(f"openalex: this round's credit budget was used; {state['deferred_refs']} seed references "
                     "were not followed yet and are tried again in later rounds")
    coverage = _coverage(state, rated, _ranked(state))
    _write_summary(state, rated, stop, coverage, notes, remaining=len(order) - len(picked), batch=batch)
    return {"session": state["session"], "pool": len(state["pool"]), "screened": len(rated),
            "api_warnings": notes,
            "remaining": len(order) - len(picked), "ranked_by": state.get("ranked_by", "formula"), "batch": batch,
            "coverage": coverage, **stop,
            "next_step": ("rate every paper: action='rate', session=..., ratings=[{n, relevance, reason}"
                          + (f", abstract_zh (Chinese abstract) for {'/'.join(screening.translate_wanted())}"
                             if screening.translate_wanted() and not translate.by_api() else "")
                          + "], then action='next'") if batch else
                         "stop: show the user the screening session and let them choose"}
