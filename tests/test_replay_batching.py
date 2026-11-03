"""scripts/replay_batching.py re-runs the shipped ranking over a saved pool;
it must keep working when the deep search's internals change."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _script():
    spec = importlib.util.spec_from_file_location("replay_batching", ROOT / "scripts" / "replay_batching.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _rec(title: str, refs=(), via=(), rank: float = 0.0) -> dict:
    return {"title": title, "abstract": title, "refs": list(refs), "via": list(via), "rank": rank}


def _pool(n: int = 6) -> dict:
    pool = {"10.1/seed": _rec("zirconium aerogel seed", refs=["R0"], rank=9.0)}
    for i in range(n):
        pool[f"10.1/p{i}"] = _rec(f"zirconium aerogel paper {i}", refs=[f"R{i % 3}"], rank=float(n - i))
    return pool


def test_group_takes_the_first_query_label_and_ignores_citation_only():
    mod = _script()
    rec = {"via": ["citation: 10.1/x", "query: Eu-doped ACP", "query: other"]}
    assert mod.group(rec) == "Eu-doped ACP"
    assert mod.group({"via": ["citation: 10.1/x"]}) == ""


def test_pick_query_serves_one_paper_per_query_in_turn():
    mod = _script()
    pool = {
        "a1": _rec("a", via=["query: A"], rank=9), "a2": _rec("a", via=["query: A"], rank=8),
        "a3": _rec("a", via=["query: A"], rank=7),
        "b1": _rec("b", via=["query: B"], rank=6), "b2": _rec("b", via=["query: B"], rank=5),
        "c1": _rec("c", via=["citation: x"], rank=4),
    }
    picked = mod.pick_query(["a1", "a2", "a3", "b1", "b2", "c1"], 5, pool)
    # One per group per pass, strongest group first; c1 fills the last slot.
    assert picked == ["a1", "b1", "c1", "a2", "b2"]


def test_run_reads_its_batches_once_and_reports_targets():
    mod = _script()
    pool = _pool()
    rel = {"10.1/p2": "高", "10.1/p5": "高"}
    curve, hits = mod.run(pool, ["10.1/seed"], ["zirconium aerogel"], ["zirconium"],
                          rel, "rank", batch=2, max_read=4)
    assert curve[-1][0] == 4
    assert [f for _, f in curve] == sorted(f for _, f in curve)  # found never goes down
    # Two batches of two: p2 is the only target inside max_read.
    assert [k for _, k in hits] == ["10.1/p2"] and hits[0][0] <= 4


def test_main_prints_a_curve_for_a_pool_file(tmp_path, capsys):
    mod = _script()
    state = {"pool": _pool(), "orig_seeds": ["10.1/seed"],
             "seeds": ["10.1/seed"], "queries": ["zirconium aerogel"], "core_terms": ["zirconium"]}
    (tmp_path / "s.pool.json").write_text(json.dumps(state), encoding="utf-8")
    (tmp_path / "j.json").write_text(json.dumps(
        {"judgments": {"10.1/p1": {"relevance": "高"}}}), encoding="utf-8")
    assert mod.main([str(tmp_path / "s.pool.json"), "--judgments", str(tmp_path / "j.json"),
                     "--batch", "2", "--max-read", "4"]) == 0
    out = capsys.readouterr().out
    assert "7 papers" in out and "1/1 targets in pool" in out and "read:found" in out
