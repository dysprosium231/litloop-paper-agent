"""How well the deep search's stop rule and remaining estimate held up.

For every saved deep search, take each point where the rule was asked (the
``stop_checks`` the session logged, or, for sessions from before that log
existed, a replay over its served batches) and compare what it said with how
many 高 papers were still found after that point.

"Found after" only counts what was read later, so for a session that stopped
early it is a lower bound; a rating the user changed afterwards can also skew
it. A replay ranks nothing again and uses the pool as it was saved at the end,
which is larger than at the time, so its estimates run high: use replayed
points for when the rule fires, logged ones for the estimate.

Nothing here changes a session; the rule is not tuned until there are a dozen
or so stop points (TODO.md, review item 13).

    python scripts/stop_calibration.py            # every session
    python scripts/stop_calibration.py --since 20261017 --all-points
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from litloop import deep_search as ds  # noqa: E402
from litloop import screening, store  # noqa: E402


def replay(state: dict, rated: dict[str, str]) -> list[dict]:
    """What the rule would have said after each served batch."""
    out, seen = [], set()
    batches = [b for b in state.get("served") or [] if b]
    for i in range(1, len(batches) + 1):
        seen.update(batches[i - 1])
        part = {k: r for k, r in rated.items() if k in seen}
        stop = ds._stop({**state, "served": batches[:i]}, part)
        est = stop.get("remaining_estimate") or {}
        out.append({"screened": len(part), "high": sum(1 for r in part.values() if r == "高"),
                    "rule": ds._stop_rule(stop), "estimate": est.get("remaining"), "next_batch": est.get("next_batch")})
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--since", default="", help="only sessions whose name starts at or after this, e.g. 20261017")
    ap.add_argument("--all-points", action="store_true", help="list every point, not only suggested stops")
    args = ap.parse_args()
    stops = []
    for p in sorted(screening.screening_dir().glob("*.pool.json")):
        session = p.name[: -len(".pool.json")]
        if session < args.since:
            continue
        state = store.read_json(p)
        rated = ds._ratings(state)
        final = sum(1 for r in rated.values() if r == "高")
        checks = state.get("stop_checks") or replay(state, rated)
        source = "logged" if state.get("stop_checks") else "replayed (estimates run high)"
        rows = [c | {"after": max(0, final - c["high"]), "source": source} for c in checks]
        shown = rows if args.all_points else [r for r in rows if r["rule"]]
        if not shown:
            continue
        print(f"\n{session[:60]}  ({source}; {len(rated)} rated, {final} 高)")
        for r in shown:
            est = "-" if r["estimate"] is None else f"{r['estimate']:.0f}"
            print(f"  at {r['screened']:>4}: rule={r['rule'] or '-':<10} estimate={est:>4}  found after={r['after']}")
        stops += [r for r in rows if r["rule"] and r["rule"] != "cap"]
    print(f"\nsuggested stops (knee / few useful): {len(stops)}")
    if stops:
        late = [r for r in stops if r["after"] > 0]
        print(f"  still found 高 afterwards: {len(late)} of {len(stops)}; "
              f"average found after: {sum(r['after'] for r in stops) / len(stops):.1f}")
        with_est = [r for r in stops if r["estimate"] is not None and r["source"] == "logged"]
        if with_est:
            bias = sum(r["estimate"] - r["after"] for r in with_est) / len(with_est)
            print(f"  estimate minus found after, average over {len(with_est)}: {bias:+.1f} "
                  "(found after is a lower bound when the session stopped there)")
        elif any(r["estimate"] is not None for r in stops):
            print("  estimates: only replayed points so far, which run high; not compared")


if __name__ == "__main__":
    main()
