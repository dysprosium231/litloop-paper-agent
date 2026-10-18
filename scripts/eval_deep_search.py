"""Replay a deep search on an acceptance set and report how fast it finds the targets.

Starts a fresh deep search with the question, queries, core terms and seeds
stored in tests/acceptance/<set>.json, rates every batch from the stored
judgments (tests/acceptance/<set>.judgments.json) and prints, after each
batch, how many papers were read and how many targets found, plus the stop
suggestion and the OpenAlex credits spent.

Papers that were never judged are counted separately. By default they are
rated 低 so the run can go on (earlier runs ranked them below what was read,
so most are off topic); with --stop-on-unjudged the run stops and lists them
so someone can judge them and add them to the judgments file.

Usage:
    python scripts/eval_deep_search.py sic_al_infiltration [--max 150] [--batch 30]
    python scripts/eval_deep_search.py pdc_aerogel_review --stop-on-unjudged

It makes live OpenAlex / Scopus requests (a run of 150 papers costs a few
thousand OpenAlex credits; see litloop_diagnostics(check="quota")).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from litloop import deep_search, quota  # noqa: E402
from litloop.identifiers import canonical_doi  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("set", help="acceptance set name, e.g. sic_al_infiltration")
    ap.add_argument("--max", type=int, default=150, help="papers to read before stopping (default 150)")
    ap.add_argument("--batch", type=int, default=30, help="batch size (default 30, as in the recorded runs)")
    ap.add_argument("--stop-on-unjudged", action="store_true", help="stop and list papers with no judgment")
    ap.add_argument("--past-stop", action="store_true",
                    help="keep reading after a suggested stop, up to --max (data for scripts/stop_calibration.py)")
    args = ap.parse_args()

    acc = json.loads((ROOT / "tests/acceptance" / f"{args.set}.json").read_text(encoding="utf-8"))
    judged = json.loads((ROOT / "tests/acceptance" / f"{args.set}.judgments.json").read_text(encoding="utf-8"))["judgments"]
    params = acc["deep_search"]
    seeds = params.get("seeds") or [s["doi"] if isinstance(s, dict) else s for s in acc["seeds"]]
    targets = {canonical_doi(t["doi"] if isinstance(t, dict) else t) for t in acc["targets"]}

    t0, credits0 = time.time(), quota.spent("openalex")
    out = deep_search.start(params["question"], params["queries"], seeds, params["core_terms"],
                            must_find=sorted(targets), year_from=params.get("year_from"),
                            year_to=params.get("year_to"), batch_size=args.batch, cap=max(args.max, 200))
    session, read, found, unjudged, curve = out["session"], 0, set(), [], []
    print(f"session {session}  pool {out['pool']}  start {time.time() - t0:.0f}s")
    while out.get("batch"):
        ratings, missing = [], []
        for b in out["batch"]:
            j = judged.get(canonical_doi(b.get("doi")))
            if j is None:
                missing.append(b)
            ratings.append({"n": b["n"], "relevance": j["relevance"] if j else "低",
                            "reason": j["reason"] if j else "unjudged (eval default)"})
        if missing and args.stop_on_unjudged:
            print(f"\n{len(missing)} papers have no judgment; judge them and add to the judgments file:")
            for b in missing:
                print(f"  {b.get('doi')} | {b.get('year')} | {b['title'][:120]}")
            break
        unjudged += [b.get("doi") for b in missing]
        deep_search.rate(session, ratings)
        read += len(out["batch"])
        found |= {canonical_doi(b.get("doi")) for b in out["batch"]} & targets
        curve.append((read, len(found)))
        print(f"read {read:4d}  targets {len(found):3d}/{len(targets)}  unjudged in batch {len(missing):2d}  "
              f"pool {out['pool']}  stop={out.get('stop_suggested')}")
        if read >= args.max:
            break
        out = deep_search.next_batch(session, force=args.past_stop)
        if out.get("stop_suggested"):
            print(f"stop suggested at {read}: {out.get('stop_reason')}")
            if not args.past_stop or not out.get("batch"):
                break
    missed = sorted(targets - found)
    status = {c["doi"]: c for c in deep_search.status(session)["coverage"]}
    print("\nread:found " + " ".join(f"{r}:{f}" for r, f in curve))
    print(f"targets found {len(found)}/{len(targets)}; unjudged papers rated 低: {len(unjudged)}")
    for doi in missed:
        c = status.get(doi, {})
        print(f"  missed {doi}: {c.get('status')}" + (f" rank {c['rank']}" if c.get("rank") else ""))
    print(f"OpenAlex credits {quota.spent('openalex') - credits0}; time {time.time() - t0:.0f}s; session {session}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
