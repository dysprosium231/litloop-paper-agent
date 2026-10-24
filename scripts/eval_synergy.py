"""Offline evaluation of the deep search's ranking and stop rule on SYNERGY.

SYNERGY (https://doi.org/10.34894/HE6NAQ, CC0) holds the screening of 26
systematic reviews: every record the authors screened, as an OpenAlex work
(title, abstract, references), and whether it was finally included. It cannot
test building the pool (the records are fixed) nor materials topics; it does
test how fast the ranking brings the included papers up and where the stop
rule fires (TODO, 2026-10-23).

Two steps, both in LitLoop's environment:

1. export::

       python scripts/eval_synergy.py export Leenaars_2019 Wassenaar_2017

   downloads each review's files from DataverseNL (``labels.csv`` and
   ``works_*.zip``, 15-20 MB for a review of a few thousand records) into
   ``<litloop home>/eval/synergy/raw/<name>/``, unless they are there, and
   writes ``<litloop home>/eval/synergy/<name>.jsonl``, one record per line:
   id, doi, title, abstract, refs, cited_by, year, label. (Not through the
   ``synergy-dataset`` package: building a dataset with it asks the network
   first, and its download hung here, 2026-10-24.)

2. run (LitLoop's environment; no network, no API credits)::

       python scripts/eval_synergy.py run Leenaars_2019 [--seeds 2] [--repeats 5] [--batch 30]

   simulates a deep search: the pool is the review's records, a few included
   papers picked at random are the seeds, each batch is rated from the labels
   (included = 高, the rest = 低) and LitLoop's own ranking and stop rule decide
   what comes next (``deep_search._score``, ``_rerank``, ``_ranked``,
   ``_stop``). Papers rated 高 become seeds, as in a real search. Reports
   recall after N papers read, WSS@95 (the share of reading saved at 95%
   recall, minus 5%), and where the stop rule fired with the recall there.
   No query: SYNERGY has none, so the formula ranking of the first batch is
   coupling with the seeds alone.
"""

from __future__ import annotations

import argparse
import json
import random
import statistics
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

RECALL_AT = (100, 200, 500, 1000)


def _data_dir(arg: str | None) -> Path:
    if arg:
        return Path(arg).expanduser()
    from litloop.config import DATA_DIR
    return Path(DATA_DIR) / "eval" / "synergy"


def _short(oid: str | None) -> str:
    return (oid or "").rsplit("/", 1)[-1]


DATAVERSE = "https://dataverse.nl/api"
DOI = "doi:10.34894/HE6NAQ"


def _fetch(name: str, raw: Path) -> None:
    """The review's labels and works, from DataverseNL's public file API."""
    import requests

    files = requests.get(f"{DATAVERSE}/datasets/:persistentId/versions/:latest/files",
                         params={"persistentId": DOI}, timeout=60).json()["data"]
    mine = [f for f in files if f.get("directoryLabel", "").endswith("/" + name)]
    if not mine:
        raise SystemExit(f"{name}: no such review in SYNERGY (names as in its README, e.g. Leenaars_2019)")
    raw.mkdir(parents=True, exist_ok=True)
    for f in mine:
        fn = f["dataFile"]["filename"]
        if fn != "labels.csv" and not fn.startswith("works_"):
            continue
        # Through curl (on Windows since 10): the files redirect to objectstore.surf.nl,
        # where Python's downloads hung here while curl took seconds (2026-10-24).
        subprocess.run(["curl", "-sS", "-L", "--fail", "--max-time", "900", "-o", str(raw / fn),
                        f"{DATAVERSE}/access/datafile/{f['dataFile']['id']}"], check=True)


def _abstract(inverted: dict | None) -> str:
    """OpenAlex keeps an abstract as word -> positions."""
    if not inverted:
        return ""
    pos = {i: w for w, idx in inverted.items() for i in idx}
    return " ".join(pos[i] for i in sorted(pos))


def export(names: list[str], out: Path) -> None:
    import csv
    import zipfile

    out.mkdir(parents=True, exist_ok=True)
    for name in names:
        raw = out / "raw" / name
        if not (raw / "labels.csv").exists() or not list(raw.glob("works_*.zip")):
            _fetch(name, raw)
        with (raw / "labels.csv").open(encoding="utf-8") as fh:
            labels = {_short(row["openalex_id"]): int(row["label_included"]) for row in csv.DictReader(fh)}
        n = inc = 0
        with (out / f"{name}.jsonl").open("w", encoding="utf-8") as fh:
            for zp in sorted(raw.glob("works_*.zip")):
                with zipfile.ZipFile(zp) as z:
                    for member in z.namelist():
                        for work in json.loads(z.read(member)):
                            wid = _short(work.get("id"))
                            if wid not in labels:
                                continue
                            rec = {"id": wid, "doi": (work.get("doi") or "").replace("https://doi.org/", "") or None,
                                   "title": work.get("title") or work.get("display_name") or "",
                                   "abstract": _abstract(work.get("abstract_inverted_index")),
                                   "refs": [_short(r) for r in work.get("referenced_works") or []],
                                   "cited_by": work.get("cited_by_count") or 0, "year": work.get("publication_year"),
                                   "label": labels[wid]}
                            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
                            n, inc = n + 1, inc + rec["label"]
        print(f"{name}: {n} records, {inc} included -> {out / (name + '.jsonl')}")


def _load(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def simulate(name: str, recs: list[dict], n_seeds: int, batch: int, rng: random.Random, rep: int,
             max_read: int = 1000, patiences: tuple[int, ...] = (1, 2, 3)) -> dict:
    """One simulated deep search of at most ``max_read`` papers; returns the reading
    order and the first stop. (A real one reads about 200: reading on retrains the
    model on every rating each batch, which takes long in pure Python.)"""
    from litloop import deep_search as ds

    pool = {r["id"]: {"title": r["title"], "abstract": r["abstract"], "refs": r["refs"],
                      "cited_by_count": r["cited_by"], "doi": r["doi"]} for r in recs if r.get("title")}
    included = sorted(r["id"] for r in recs if r["label"] and r["id"] in pool)
    seeds = rng.sample(included, min(n_seeds, len(included)))
    state = {"session": f"synergy-{name}-{rep}", "pool": pool, "seeds": list(seeds), "orig_seeds": list(seeds),
             "seed_refs": sorted({x for s in seeds for x in pool[s]["refs"]}), "queries": [], "served": [],
             "batch_size": batch, "cap": len(pool) + 1}
    labels = {r["id"]: r["label"] for r in recs}
    rated: dict[str, str] = {}
    order: list[str] = []
    # Where the stop rule fires as LitLoop has it (patience 1) and if it had to
    # say so for 2, 3 batches in a row: one simulation gives them all.
    stops: dict[int, tuple[int, str]] = {}
    streak = 0
    ds._score(state)
    while len(order) < min(max_read, len(pool) - len(seeds)):
        ds._rerank(state, rated)
        nxt = [k for k in ds._ranked(state) if k not in rated][:batch]
        if not nxt:
            break
        for k in nxt:
            rated[k] = "高" if labels.get(k) else "低"
        order += nxt
        state["served"].append(nxt)
        new_seeds = [k for k in nxt if rated[k] == "高"]
        if new_seeds:  # 高 papers become seeds; the pool is fixed, so nothing is fetched
            state["seeds"] += new_seeds
            state["seed_refs"] = sorted(set(state["seed_refs"]) | {x for k in new_seeds for x in pool[k]["refs"]})
            ds._score(state)
        stop = ds._stop(state, rated)
        streak = streak + 1 if stop["stop_suggested"] else 0
        for pt in patiences:
            if pt not in stops and streak >= pt:
                stops[pt] = (len(order), ds._stop_rule(stop))
        # Every target read: the rest of the order changes nothing that is
        # reported, once every stop variant has spoken.
        if all(k in rated for k in included if k not in seeds) and len(stops) == len(patiences):
            break
    targets = [k for k in included if k not in seeds]
    return {"order": order, "targets": set(targets), "stops": stops,
            "pool": len(pool) - len(seeds), "max_read": max_read}


def _metrics(run: dict) -> dict:
    found, curve = 0, []
    for k in run["order"]:
        found += k in run["targets"]
        curve.append(found)
    total = len(run["targets"]) or 1
    need = -(-95 * total // 100)  # papers to find for 95% recall
    read95 = next((i + 1 for i, f in enumerate(curve) if f >= need), None)
    out = {f"recall@{n}": curve[min(n, len(curve)) - 1] / total
           for n in RECALL_AT if curve and n <= run.get("max_read", n)}
    if read95:  # 95% reached within the papers read
        out["wss@95"] = (run["pool"] - read95) / run["pool"] - 0.05
        out["read@95"] = read95
    for pt, (at, rule) in run["stops"].items():
        out[f"stop{pt}"] = at
        out[f"stop{pt}_rule"] = rule
        out[f"recall_at_stop{pt}"] = curve[at - 1] / total
    return out


def run(names: list[str], data: Path, n_seeds: int, repeats: int, batch: int, max_read: int = 1000,
        patiences: tuple[int, ...] = (1, 2, 3)) -> None:
    """Simulate each review ``repeats`` times; print each run, then a summary of all reviews."""
    import time
    summary = []
    for name in names:
        recs = _load(data / f"{name}.jsonl")
        n_inc = sum(r["label"] for r in recs)
        print(f"\n{name}: {len(recs)} records, {n_inc} included; {n_seeds} seeds, batch {batch}, "
              f"at most {max_read} read, {repeats} runs", flush=True)
        rows = []
        for rep in range(repeats):
            t0 = time.time()
            m = _metrics(simulate(name, recs, n_seeds, batch, random.Random(rep), rep, max_read, patiences))
            rows.append(m)
            stops = ", ".join(f"{pt}: {m[f'stop{pt}']} ({m[f'recall_at_stop{pt}']:.2f})" if f"stop{pt}" in m
                              else f"{pt}: -" for pt in patiences)
            print(f"  run {rep + 1}: read@95 {m.get('read@95', '-')}, stops [{stops}], {time.time() - t0:.0f}s",
                  flush=True)
        summary.append((name, len(recs), n_inc, rows))

    def mean(rows: list[dict], k: str, fmt: str) -> str:
        vals = [r[k] for r in rows if k in r]
        if not vals:
            return "-"
        s = format(statistics.mean(vals), fmt)
        return s if len(vals) == len(rows) else f"{s} ({len(vals)}/{len(rows)})"

    print("\nSummary (means over runs; (k/n): only k of n runs got there)")
    head = ["review", "records", "incl", "read@95", "wss@95", "recall@200"] + \
        [f"stop x{pt} / recall" for pt in patiences]
    print(" | ".join(head))
    for name, n, inc, rows in summary:
        cells = [name, str(n), str(inc), mean(rows, "read@95", ".0f"), mean(rows, "wss@95", ".2f"),
                 mean(rows, "recall@200", ".2f")]
        cells += [f"{mean(rows, f'stop{pt}', '.0f')} / {mean(rows, f'recall_at_stop{pt}', '.2f')}" for pt in patiences]
        print(" | ".join(cells))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("export", help="download reviews from DataverseNL and write them as JSONL")
    e.add_argument("names", nargs="+")
    e.add_argument("--out", help="folder for the JSONL files (default <litloop home>/eval/synergy)")
    r = sub.add_parser("run", help="simulate deep searches on exported reviews")
    r.add_argument("names", nargs="+")
    r.add_argument("--data", help="folder with the JSONL files (default <litloop home>/eval/synergy)")
    r.add_argument("--seeds", type=int, default=2, help="included papers given as seeds (default 2)")
    r.add_argument("--repeats", type=int, default=5, help="runs with different seeds (default 5)")
    r.add_argument("--batch", type=int, default=30, help="papers per batch (default 30)")
    r.add_argument("--max", type=int, default=1000, help="papers read at most per run (default 1000)")
    r.add_argument("--patience", type=int, nargs="+", default=[1, 2, 3],
                   help="report where the stop rule fires if it has to say so this many batches in a row "
                        "(default 1 2 3; 1 is the rule as LitLoop has it)")
    r.add_argument("--knee", type=float, help="try another knee ratio for the stop rule (deep_search.KNEE_RATIO)")
    a = ap.parse_args()
    if a.cmd == "export":
        export(a.names, _data_dir(a.out))
    else:
        if a.knee:
            from litloop import deep_search
            deep_search.KNEE_RATIO = a.knee
            print(f"stop rule knee ratio set to 1/{a.knee:g} for this run")
        run(a.names, _data_dir(a.data), a.seeds, a.repeats, a.batch, a.max, tuple(a.patience))
    return 0


if __name__ == "__main__":
    sys.exit(main())
