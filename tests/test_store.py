"""Shared files between the agent and the desktop app (litloop.store)."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from litloop import screening, store

SRC = str(Path(__file__).resolve().parents[1] / "src")


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setattr(screening._config, "DATA_DIR", tmp_path)
    return tmp_path


def test_write_is_whole_and_carries_the_schema(tmp_path):
    f = tmp_path / "x.json"
    store.write_json(f, {"a": 1})
    assert json.loads(f.read_text(encoding="utf-8")) == {"a": 1, "schema": store.SCHEMA}
    assert [p.name for p in tmp_path.iterdir()] == ["x.json"]  # no temporary file left behind


def test_a_newer_schema_asks_for_an_update(tmp_path):
    f = tmp_path / "x.json"
    f.write_text(json.dumps({"schema": store.SCHEMA + 1}), encoding="utf-8")
    with pytest.raises(store.SchemaTooNew):
        store.read_json(f)


def test_two_processes_updating_one_file_lose_nothing(tmp_path):
    f = tmp_path / "counter.json"
    store.write_json(f, {"n": 0})
    code = ("import sys; sys.path.insert(0, sys.argv[2]); from pathlib import Path; from litloop import store\n"
            "for _ in range(40):\n"
            "    with store.update(Path(sys.argv[1])) as d:\n"
            "        d['n'] += 1\n")
    procs = [subprocess.Popen([sys.executable, "-c", code, str(f), SRC]) for _ in range(2)]
    assert all(p.wait(timeout=120) == 0 for p in procs)
    assert store.read_json(f)["n"] == 80


def test_session_list_skips_pool_summary_and_progress_files(home):
    sid = screening.save("q", [{"doi": "10.1/a", "title": "A", "relevance": "高", "reason": "r"}])["session"]
    d = screening.screening_dir()
    (d / f"{sid}.pool.json").write_text(json.dumps({"session": sid, "pool": {}}), encoding="utf-8")
    store.write_json(store.summary_path(sid), {"session": sid})
    store.write_json(store.progress_path(sid), {"session": sid})
    assert [s["session"] for s in screening.list_sessions()] == [sid]


def test_the_users_rating_from_the_app_overrides_the_agents(home):
    sid = screening.save("q", [{"doi": "10.1/a", "title": "A", "relevance": "低", "reason": "r"}])["session"]
    with store.update(screening.screening_dir() / f"{sid}.json") as data:  # what the app does
        data["items"][0]["user_relevance"] = "高"
    item = screening.load(sid)["items"][0]
    assert screening._rating(item) == "高"
    # the agent rating the paper again must not erase the user's rating
    screening.save("q", [{"doi": "10.1/a", "title": "A", "relevance": "中", "reason": "again"}], session=sid)
    item = screening.load(sid)["items"][0]
    assert item["relevance"] == "中" and screening._rating(item) == "高"


def test_progress_records_each_phase_and_ends_idle(home):
    p = store.Progress("s1", "next")
    p.phase("expand", 10)
    p.step(10)
    p.phase("rank")
    p.finish()
    got = store.read_progress("s1")
    assert got["state"] == "idle" and got["phase"] is None
    assert [ph["name"] for ph in got["phases"]] == ["expand", "rank"]
