"""scripts/eval_synergy.py drives the deep search's own ranking offline; it must
keep working when deep_search's internals change."""

from __future__ import annotations

import importlib.util
import json
import random
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _script():
    spec = importlib.util.spec_from_file_location("eval_synergy", ROOT / "scripts" / "eval_synergy.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _fake(n: int = 200, included: int = 12) -> list[dict]:
    rng = random.Random(3)
    on, off = ["sleep", "histamine", "microdialysis", "amino", "brain"], ["heart", "diet", "bone", "lung", "virus", "skin"]
    recs = []
    for i in range(n):
        inc = i < included
        words = rng.sample(on if inc else off, 4)
        refs = [f"R{rng.randint(0, 10)}" if inc else f"R{rng.randint(8, 300)}" for _ in range(5)]
        recs.append({"id": f"W{i}", "doi": None, "title": " ".join(words), "abstract": " ".join(words * 3),
                     "refs": refs, "cited_by": i % 7, "year": 2015, "label": int(inc)})
    rng.shuffle(recs)
    return recs


def test_a_simulated_search_reads_every_target_and_reports_its_metrics():
    mod = _script()
    run = mod.simulate("Fake", _fake(), n_seeds=2, batch=20, rng=random.Random(0), rep=0)
    assert len(run["targets"]) == 10  # 12 included minus the 2 seeds
    assert run["targets"] <= set(run["order"])
    assert len(run["order"]) == len(set(run["order"]))  # nothing served twice
    m = mod._metrics(run)
    assert 0 < m["read@95"] <= len(run["order"])
    assert -0.05 <= m["wss@95"] < 1
    assert m["recall@100"] == 1.0  # an easy set: the ranking finds them all early


def test_run_reads_the_exported_jsonl(tmp_path, capsys):
    mod = _script()
    (tmp_path / "Fake.jsonl").write_text("\n".join(json.dumps(r) for r in _fake()), encoding="utf-8")
    mod.run(["Fake"], tmp_path, n_seeds=2, repeats=2, batch=20)
    out = capsys.readouterr().out
    assert "200 records, 12 included" in out and "wss@95" in out
