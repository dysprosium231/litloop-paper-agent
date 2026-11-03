"""Replay batch selection over a saved deep-search pool, without new credits.

A deep search stores its whole candidate pool in
``~/.litloop/screening/<session>.pool.json`` (refs, abstracts, ``via`` labels).
This script re-runs the shipped ranking on that pool and serves batches under
different selection strategies, reporting the read:found curve against a test
set's judgments — the numbers ``scripts/eval_deep_search.py`` prints, but
offline and repeatable on one pool.

The pool is the one the live run ended with, so the *early* batches here see
papers the live run had not found yet; the curve is an upper bound on the live
one. It is meant to compare strategies against each other, not to reproduce a
recorded run.

  python scripts/replay_batching.py ~/.litloop/screening/<session>.pool.json \
      --judgments tests/acceptance/rare_earth_drug_carriers.judgments.json

Strategies:
  rank   the shipped one: ``deep._ranked``, highest rank first.
  query  one paper per query label (plus one for citation-only papers) in turn,
         best ranked first inside each group: every sub-direction of a broad
         topic gets read early instead of the dominant one filling the batch.
"""

from __future__ import annotations

import argparse
import json
from collections import OrderedDict
from pathlib import Path

QUERY_PREFIX = "query: "
HIGH = "高"


def load(path: str | Path) -> dict:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def relevance(judgments: dict) -> dict[str, str]:
    """DOI (lower) -> relevance label; unjudged papers are not targets."""
    return {doi.lower(): (rec.get("relevance") or "低")
            for doi, rec in (judgments.get("judgments") or {}).items()}


def group(rec: dict) -> str:
    """The query label a paper was found by; '' for citation-only papers."""
    for label in rec.get("via") or []:
        if label.startswith(QUERY_PREFIX):
            return label[len(QUERY_PREFIX):]
    return ""


def pick_rank(order: list[str], batch: int) -> list[str]:
    return order[:batch]


def pick_query(order: list[str], batch: int, pool: dict) -> list[str]:
    """One paper per query label in turn, so no sub-direction is starved."""
    buckets: "OrderedDict[str, list[str]]" = OrderedDict()
    for key in order:
        buckets.setdefault(group(pool[key]), []).append(key)
    # Strongest group first in every pass; a group that runs dry is skipped.
    names = sorted(buckets, key=lambda g: -pool[buckets[g][0]].get("rank", 0))
    out: list[str] = []
    while len(out) < batch and any(buckets[g] for g in names):
        for g in names:
            if buckets[g] and len(out) < batch:
                out.append(buckets[g].pop(0))
    return out


def run(pool: dict, orig_seeds: list[str], queries: list, core_terms: list,
        rel: dict[str, str], strategy: str, batch: int, max_read: int
        ) -> tuple[list[tuple[int, int]], list[tuple[int, str]]]:
    """Serve batches, re-ranking after each one exactly as a live run does.

    Returns the (read, targets found) curve and where each target was read.
    """
    from litloop.search import deep as ds

    seeds = list(orig_seeds)
    state = {
        "session": f"replay-{strategy}",
        "pool": pool,
        "seeds": seeds,
        "orig_seeds": list(seeds),
        "seed_refs": sorted({r for s in seeds for r in (pool.get(s, {}).get("refs") or [])}),
        "queries": queries,
        "core_terms": core_terms,
        "served": [],
    }
    ds._score(state)
    rated: dict[str, str] = {}
    curve: list[tuple[int, int]] = []
    hit_at: dict[str, int] = {}
    read = 0
    while read < max_read:
        if rated:
            ds._rerank(state, rated)
        order = [k for k in ds._ranked(state) if k not in rated]
        if not order:
            break
        picked = (pick_query(order, batch, pool) if strategy == "query"
                  else pick_rank(order, batch))
        for key in picked:
            read += 1
            label = rel.get(key.lower(), "低")
            rated[key] = label
            if label == HIGH:
                hit_at[key] = read
                seeds.append(key)
            if read >= max_read:
                break
        state["seeds"] = seeds
        state["seed_refs"] = sorted({r for s in seeds for r in (pool.get(s, {}).get("refs") or [])})
        ds._score(state)
        curve.append((read, len(hit_at)))
    return curve, sorted((n, k) for k, n in hit_at.items())


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("pool", help="path to a <session>.pool.json")
    ap.add_argument("--judgments", required=True, help="tests/acceptance/<set>.judgments.json")
    ap.add_argument("--batch", type=int, default=30)
    ap.add_argument("--max-read", type=int, default=180)
    ap.add_argument("--strategy", action="append", choices=["rank", "query"],
                    help="repeatable; default both")
    args = ap.parse_args(argv)

    state = load(args.pool)
    pool = state.get("pool") or {}
    if not pool:
        print(f"no pool in {args.pool}")
        return 1
    # Only the seeds the user gave start as read; the rest of ``seeds`` are
    # papers rated 高 during the run.
    orig = state.get("orig_seeds") or (state.get("seeds") or [])[:3]
    rel = relevance(load(args.judgments))
    targets = {d for d, r in rel.items() if r == HIGH}
    in_pool = {k.lower() for k in pool}
    print(f"pool {len(pool)} papers, {len(targets & in_pool)}/{len(targets)} targets in pool "
          f"({len(orig)} seeds, batch {args.batch}, max read {args.max_read})")

    for strategy in (args.strategy or ["rank", "query"]):
        curve, _hits = run(pool, orig, state.get("queries") or [], state.get("core_terms") or [],
                           rel, strategy, args.batch, args.max_read)
        print(f"{strategy:6s} read:found  " + "  ".join(f"{r}:{f}" for r, f in curve))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
