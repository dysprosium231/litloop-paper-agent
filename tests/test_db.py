"""Tests for the SQLite persistence layer (litloop.db)."""

from __future__ import annotations

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

from litloop import config, db, store


@pytest.fixture(autouse=True)
def tmp_data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    return tmp_path


def test_schema_wal_and_foreign_keys_cascade(tmp_path):
    assert db.db_path() == tmp_path / "litloop.db"
    with db.connect() as conn:
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        fk = conn.execute("PRAGMA foreign_keys").fetchone()[0]
        ver = conn.execute("PRAGMA user_version").fetchone()[0]
        assert str(mode).lower() == "wal"
        assert fk == 1
        assert ver == db.DB_SCHEMA

        tables = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        assert {
            "schema_migrations",
            "sessions",
            "pool_items",
            "decisions",
            "watches",
            "agent_messages",
            "steering_notes",
        } <= tables

        m_rows = conn.execute("SELECT version, note FROM schema_migrations ORDER BY version").fetchall()
        assert [(r["version"], r["note"]) for r in m_rows] == [
            (1, "initial schema"),
            (2, "agent messages and steering notes"),
        ]

    # Verify ON DELETE CASCADE from sessions -> pool_items & decisions
    with db.transaction() as conn:
        conn.execute("INSERT INTO sessions (id, question) VALUES ('s1', 'q1')")
        conn.execute(
            "INSERT INTO pool_items (session_id, paper_key, title) VALUES ('s1', 'doi:10.1/a', 'A')"
        )
        conn.execute(
            "INSERT INTO decisions (session_id, paper_key, title, relevance) VALUES ('s1', 'doi:10.1/a', 'A', '高')"
        )
    with db.transaction() as conn:
        conn.execute("DELETE FROM sessions WHERE id = 's1'")
    with db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM pool_items").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM decisions").fetchone()[0] == 0


def test_schema_too_new_raises_and_closes(tmp_path):
    target = tmp_path / "future.db"
    raw = sqlite3.connect(str(target))
    raw.execute(f"PRAGMA user_version = {db.DB_SCHEMA + 5}")
    raw.close()

    with pytest.raises(store.SchemaTooNew):
        with db.connect(target):
            pass


def test_simulated_v2_migration_hook(tmp_path, monkeypatch):
    base_ver = db.DB_SCHEMA
    next_ver = base_ver + 1
    # Initialize at current schema first
    with db.connect() as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == base_ver

    def _migrate_next(conn: sqlite3.Connection) -> None:
        conn.execute("CREATE TABLE test_next_ext (id TEXT PRIMARY KEY)")

    monkeypatch.setattr(db, "DB_SCHEMA", next_ver)
    monkeypatch.setattr(db, "MIGRATIONS", [*db.MIGRATIONS, (next_ver, "add test_next_ext", _migrate_next)])

    with db.connect() as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == next_ver
        tables = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        assert "test_next_ext" in tables
        rows = conn.execute("SELECT version, note FROM schema_migrations ORDER BY version").fetchall()
        assert rows[-1]["version"] == next_ver
        assert rows[-1]["note"] == "add test_next_ext"


def test_concurrent_multi_connection_read_write(tmp_path):
    # Initialize DB once, then hammer with concurrent writers & readers
    with db.connect():
        pass

    def worker(idx: int) -> None:
        with db.transaction() as conn:
            conn.execute(
                "INSERT INTO watches (id, question, query) VALUES (?, ?, ?)",
                (f"w{idx}", f"Q{idx}", f"query {idx}"),
            )
        with db.connect() as conn:
            row = conn.execute("SELECT query FROM watches WHERE id = ?", (f"w{idx}",)).fetchone()
            assert row is not None and row["query"] == f"query {idx}"

    with ThreadPoolExecutor(max_workers=6) as pool:
        list(pool.map(worker, range(24)))

    assert len(db.list_watches()) == 24


def test_import_legacy_data_lossless_idempotent_and_resilient(tmp_path):
    sdir = tmp_path / "screening"
    sdir.mkdir(parents=True)

    sid = "20261121-100000-aerogel"
    session_file = sdir / f"{sid}.json"
    summary_file = sdir / f"{sid}.summary.json"
    pool_file = sdir / f"{sid}.pool.json"
    watches_file = tmp_path / "watches.json"
    bad_file = sdir / "corrupt-session.json"

    session_file.write_text(
        json.dumps(
            {
                "schema": 1,
                "id": sid,
                "created": "2026-11-21 10:00:00",
                "updated": "2026-11-21 10:05:00",
                "question": "SiC aerogel",
                "mode": "deep",
                "criteria": ["high temp"],
                "searches": [{"query": "SiC aerogel", "results": 2, "new": 2}],
                "items": [
                    {
                        "n": 1,
                        "doi": "10.1000/abc",
                        "title": "Paper A",
                        "year": 2025,
                        "journal": "J. Ceram.",
                        "journal_rank": "Q1",
                        "authors": ["Alice", "Bob"],
                        "cited_by_count": 12,
                        "abstract": "Abstract A",
                        "abstract_zh": "摘要 A",
                        "abstract_missing": False,
                        "retracted": False,
                        "concern": False,
                        "relevance": "高",
                        "reason": "on topic",
                        "user_relevance": "高",
                        "decision": "selected",
                        "custom_extra": "keep_me",
                    },
                    {
                        "n": 2,
                        "doi": "",
                        "title": "Paper B",
                        "relevance": "低",
                        "reason": "off topic",
                        "decision": "rejected",
                        "reject_reason": "主题不相关",
                    },
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    summary_file.write_text(
        json.dumps(
            {
                "schema": 1,
                "session": sid,
                "question": "SiC aerogel",
                "mode": "deep",
                "updated": "2026-11-21 10:05:00",
                "pool": 2,
                "screened": 2,
                "remaining": 0,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    pool_file.write_text(
        json.dumps(
            {
                "schema": 1,
                "session": sid,
                "question": "SiC aerogel",
                "queries": ["SiC aerogel"],
                "core_terms": ["SiC", "aerogel"],
                "seeds": ["doi:10.1000/abc"],
                "pool": {
                    "doi:10.1000/abc": {
                        "doi": "10.1000/abc",
                        "openalex": "W1",
                        "title": "Paper A",
                        "year": 2025,
                        "journal": "J. Ceram.",
                        "cited_by_count": 12,
                        "abstract": "Abstract A",
                        "score": 0.95,
                        "found_via": ["seed"],
                    },
                    "title:paper b": {
                        "doi": None,
                        "openalex": None,
                        "title": "Paper B",
                        "year": 2024,
                        "score": 0.25,
                    },
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    watches_file.write_text(
        json.dumps(
            {
                "schema": 1,
                "watches": [
                    {
                        "id": "w1",
                        "question": "SiC aerogel",
                        "query": "SiC aerogel",
                        "core_terms": ["SiC"],
                        "session": sid,
                        "created": "2026-11-21 10:10",
                        "checked": "",
                        "last_new": 0,
                        "seen": ["doi:10.1000/abc"],
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    # Corrupt JSON should be skipped without crashing
    bad_file.write_text("{not valid json", encoding="utf-8")

    first = db.import_legacy_data(tmp_path)
    assert first == {"sessions": 1, "decisions": 2, "pool_items": 2, "watches": 1}

    # All source JSON files remain untouched
    assert session_file.exists()
    assert summary_file.exists()
    assert pool_file.exists()
    assert watches_file.exists()
    assert bad_file.exists()

    # Second run is completely idempotent
    second = db.import_legacy_data(tmp_path)
    assert second == {"sessions": 0, "decisions": 0, "pool_items": 0, "watches": 0}

    with db.connect() as conn:
        srow = conn.execute("SELECT * FROM sessions WHERE id = ?", (sid,)).fetchone()
        assert srow is not None
        assert srow["question"] == "SiC aerogel"
        assert json.loads(srow["summary_json"])["pool"] == 2
        assert json.loads(srow["pool_state_json"])["core_terms"] == ["SiC", "aerogel"]

        drows = conn.execute(
            "SELECT * FROM decisions WHERE session_id = ? ORDER BY n", (sid,)
        ).fetchall()
        assert len(drows) == 2
        assert drows[0]["decision"] == "selected"
        assert drows[0]["abstract_zh"] == "摘要 A"
        assert json.loads(drows[0]["data_json"]) == {"custom_extra": "keep_me"}
        assert drows[1]["reject_reason"] == "主题不相关"

        prows = conn.execute(
            "SELECT * FROM pool_items WHERE session_id = ? ORDER BY score DESC", (sid,)
        ).fetchall()
        assert len(prows) == 2
        assert prows[0]["paper_key"] == "doi:10.1000/abc"
        assert json.loads(prows[0]["data_json"])["found_via"] == ["seed"]

    w = db.get_watch("w1")
    assert w is not None and w["seen"] == ["doi:10.1000/abc"]


def test_auto_import_legacy_session_json_on_demand(tmp_path):
    sdir = tmp_path / "screening"
    sdir.mkdir(parents=True)
    sid = "20261121-120000-legacy"
    legacy_path = sdir / f"{sid}.json"
    legacy_path.write_text(
        json.dumps(
            {
                "schema": 1,
                "id": sid,
                "created": "2026-11-21 12:00:00",
                "updated": "2026-11-21 12:05:00",
                "question": "legacy topic",
                "mode": "quick",
                "criteria": ["c1"],
                "searches": [{"query": "legacy", "time": "2026-11-21 12:00:00", "params": {}, "results": 2, "new": 2}],
                "items": [
                    {
                        "n": 1,
                        "doi": "10.1/legacy-a",
                        "title": "Legacy A",
                        "year": 2024,
                        "journal": "J. Legacy",
                        "abstract": "Abs A",
                        "relevance": "高",
                        "reason": "good",
                        "decision": "selected",
                    },
                    {
                        "n": 2,
                        "doi": "10.1/legacy-b",
                        "title": "Legacy B",
                        "relevance": "低",
                        "reason": "weak",
                        "decision": None,
                    },
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    assert db.session_exists(sid) is True
    assert legacy_path.exists()  # never deleted

    loaded = db.load_screening(sid)
    assert loaded["id"] == sid
    assert loaded["question"] == "legacy topic"
    assert [it["doi"] for it in loaded["items"]] == ["10.1/legacy-a", "10.1/legacy-b"]

    # Idempotent across repeated calls and list/history queries
    assert db.list_sessions()[0]["session"] == sid
    assert db.history()[0]["selected"][0]["doi"] == "10.1/legacy-a"
    assert legacy_path.exists()


def test_row_level_updates_and_pending_selected_by_session(tmp_path):
    s1 = db.save_screening(
        "Topic 1",
        [
            {"doi": "10.1/p1", "title": "P1", "year": 2024, "journal": "J1", "abstract": "Abs 1", "relevance": "高", "reason": "r1"},
            {"doi": "", "title": "No DOI", "relevance": "中", "reason": "r2"},
        ],
    )["session"]

    # Row-level rating, decision, and abstract_zh updates
    r_rate = db.update_item_rating(s1, "10.1/p1", "P1", "中")
    assert r_rate is not None and r_rate["item"]["user_relevance"] == "中" and r_rate["edited"] == 1

    r_dec = db.update_item_decision(s1, "10.1/p1", "P1", "selected")
    assert r_dec is not None and r_dec["item"]["decision"] == "selected"

    r_rej = db.update_item_decision(s1, None, "No DOI", "rejected", "主题不相关")
    assert r_rej is not None and r_rej["item"]["reject_reason"] == "主题不相关"

    assert db.set_item_abstract_zh(s1, "10.1/p1", "P1", "中文摘要 1") is True

    # Batch update (atomic)
    batch_res = db.batch_update_items(
        s1,
        [
            {"doi": "10.1/p1", "title": "P1", "relevance": "高", "decision": "selected"},
            {"doi": None, "title": "No DOI", "decision": "selected"},
        ],
    )
    assert batch_res["updated"] == 2 and batch_res["edited"] == 0

    loaded = db.load_screening(s1)
    p1 = next(it for it in loaded["items"] if it.get("doi") == "10.1/p1")
    assert p1["abstract_zh"] == "中文摘要 1"
    assert "user_relevance" not in p1  # cleared because relevance matched agent rating

    # Pending selected by session excludes items without DOI and sorts newest-first
    s2 = db.save_screening(
        "Topic 2",
        [{"doi": "10.1/p2", "title": "P2", "year": 2025, "journal": "J2", "relevance": "高", "reason": "r"}],
    )["session"]
    db.update_item_decision(s2, "10.1/p2", "P2", "selected")

    pending = db.pending_selected_by_session()
    assert [p["session"] for p in pending] == sorted([s1, s2], reverse=True)
    by_sid = {p["session"]: p for p in pending}
    assert [x["doi"] for x in by_sid[s1]["papers"]] == ["10.1/p1"]
    assert [x["doi"] for x in by_sid[s2]["papers"]] == ["10.1/p2"]


def test_large_3000_pool_roundtrip_incremental_update_and_batch_paper(tmp_path):
    sid = "20261121-150000-large-pool"
    pool = {
        f"doi:10.1000/p{i}": {
            "doi": f"10.1000/p{i}",
            "openalex": f"W{i}",
            "title": f"Paper {i}",
            "year": 2020 + (i % 6),
            "journal": "J. Test",
            "cited_by_count": i,
            "abstract": f"Abstract {i}",
            "score": round(i * 0.01, 2),
            "found_via": ["keyword"],
        }
        for i in range(3000)
    }
    state = {
        "session": sid,
        "question": "3000 papers",
        "queries": ["q1"],
        "core_terms": ["t1"],
        "seeds": ["doi:10.1000/p0"],
        "served": [["doi:10.1000/p0", "doi:10.1000/p1"]],
        "rounds": [{"event": "start", "pool_added": 3000}],
        "last_batch": [
            {
                "doi": "10.1000/p42",
                "title": "Paper 42",
                "year": 2020,
                "journal": "J. Test",
                "journal_rank": "Q1",
                "retracted": False,
                "concern": False,
                "authors": ["Alice"],
                "cited_by_count": 42,
                "abstract": "Abstract 42",
                "abstract_missing": False,
            }
        ],
        "pool": pool,
    }

    db.save_pool_state(state)
    assert db.has_pool(sid) is True

    loaded = db.load_pool_state(sid)
    assert len(loaded["pool"]) == 3000
    assert loaded["question"] == "3000 papers"
    assert loaded["pool"]["doi:10.1000/p2999"]["openalex"] == "W2999"
    assert loaded["pool"]["doi:10.1000/p2999"]["found_via"] == ["keyword"]

    # Incremental update: remove p0..p9, update p10, add p3000
    for i in range(10):
        pool.pop(f"doi:10.1000/p{i}")
    pool["doi:10.1000/p10"]["score"] = 99.5
    pool["doi:10.1000/p3000"] = {
        "doi": "10.1000/p3000",
        "openalex": "W3000",
        "title": "Paper 3000",
        "score": 88.0,
    }
    db.save_pool_state(state)

    reloaded = db.load_pool_state(sid)
    assert len(reloaded["pool"]) == 2991
    assert "doi:10.1000/p0" not in reloaded["pool"]
    assert reloaded["pool"]["doi:10.1000/p10"]["score"] == 99.5
    assert reloaded["pool"]["doi:10.1000/p3000"]["title"] == "Paper 3000"

    # batch_paper reads from pool_state_json without loading pool_items
    bp = db.get_batch_paper(sid, "10.1000/p42", "Paper 42")
    assert bp is not None and bp["journal_rank"] == "Q1" and bp["authors"] == ["Alice"]
    assert db.get_batch_paper(sid, "10.1000/missing", "Missing") is None

    # summary roundtrip
    db.save_summary(sid, {"session": sid, "pool": 2991, "screened": 1})
    assert db.load_summary(sid) == {"session": sid, "pool": 2991, "screened": 1}


def test_auto_import_legacy_pool_and_summary_on_demand(tmp_path):
    sdir = tmp_path / "screening"
    sdir.mkdir(parents=True)
    sid = "20261121-150500-legacy-deep"
    pool_file = sdir / f"{sid}.pool.json"
    summary_file = sdir / f"{sid}.summary.json"

    pool_file.write_text(
        json.dumps(
            {
                "session": sid,
                "question": "legacy deep",
                "seeds": [],
                "served": [],
                "rounds": [],
                "last_batch": [{"doi": "10.1/l1", "title": "Legacy 1", "year": 2024}],
                "pool": {"doi:10.1/l1": {"doi": "10.1/l1", "title": "Legacy 1", "score": 1.0}},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    summary_file.write_text(
        json.dumps({"session": sid, "question": "legacy deep", "pool": 1}, ensure_ascii=False),
        encoding="utf-8",
    )

    assert db.has_pool(sid) is True
    loaded = db.load_pool_state(sid)
    assert loaded["pool"]["doi:10.1/l1"]["title"] == "Legacy 1"
    assert db.get_batch_paper(sid, "10.1/l1", None)["title"] == "Legacy 1"
    assert db.load_summary(sid)["pool"] == 1
    assert pool_file.exists() and summary_file.exists()


def test_v1_to_v2_migration(tmp_path, monkeypatch):
    v1_db = tmp_path / "v1.db"
    with monkeypatch.context() as mp:
        mp.setattr(db, "DB_SCHEMA", 1)
        mp.setattr(db, "MIGRATIONS", [m for m in db.MIGRATIONS if m[0] == 1])
        with db.connect(v1_db) as conn:
            assert conn.execute("PRAGMA user_version").fetchone()[0] == 1
            tables_v1 = {
                r[0]
                for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
            }
            assert "agent_messages" not in tables_v1
            assert "steering_notes" not in tables_v1

    with db.connect(v1_db) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 2
        tables_v2 = {
            r[0]
            for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
        }
        assert {"agent_messages", "steering_notes"} <= tables_v2
        indexes = {
            r[0]
            for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'index'").fetchall()
        }
        assert {"idx_agent_messages_session", "idx_steering_notes_session"} <= indexes


def test_agent_messages_actions_and_clear(tmp_path):
    # Supports "__draft__" without any row in sessions table
    m1 = db.add_agent_message("__draft__", "user", "hello")
    m2 = db.add_agent_message(
        "__draft__",
        "assistant",
        "sure",
        actions=[{"id": "act-1", "kind": "start_search", "status": "pending", "result": None}],
        usage={"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
    )
    m3 = db.add_agent_message("__draft__", "user", "next")

    all_msgs = db.list_agent_messages("__draft__", limit=100)
    assert [m["id"] for m in all_msgs] == [m1["id"], m2["id"], m3["id"]]
    assert all_msgs[1]["actions"][0]["id"] == "act-1"
    assert all_msgs[1]["usage"] == {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}

    recent = db.list_agent_messages("__draft__", limit=2)
    assert [m["id"] for m in recent] == [m2["id"], m3["id"]]

    updated = db.update_agent_action(m2["id"], "act-1", "confirmed", {"run": "r1"})
    assert updated is not None
    assert updated["status"] == "confirmed"
    assert updated["result"] == {"run": "r1"}
    assert db.update_agent_action(m2["id"], "missing", "confirmed") is None
    assert db.update_agent_action(999999, "act-1", "confirmed") is None

    reloaded = db.list_agent_messages("__draft__")
    assert reloaded[1]["actions"][0]["status"] == "confirmed"
    assert reloaded[1]["actions"][0]["result"] == {"run": "r1"}

    deleted = db.clear_agent_messages("__draft__")
    assert deleted == 3
    assert db.list_agent_messages("__draft__") == []


def test_steering_notes_and_migrate_agent_session(tmp_path):
    db.add_agent_message("__draft__", "user", "draft msg")
    n1 = db.add_steering_note("__draft__", "只要近五年")
    n2 = db.add_steering_note("__draft__", "排除综述")

    active_notes = db.list_steering_notes("__draft__", active_only=True)
    assert [n["id"] for n in active_notes] == [n1["id"], n2["id"]]
    assert all(n["active"] is True for n in active_notes)

    assert db.remove_steering_note(n1["id"]) is True
    assert db.remove_steering_note(n1["id"]) is False
    assert db.remove_steering_note(999999) is False

    active_after = db.list_steering_notes("__draft__", active_only=True)
    assert [n["id"] for n in active_after] == [n2["id"]]
    all_notes = db.list_steering_notes("__draft__", active_only=False)
    assert len(all_notes) == 2
    assert all_notes[0]["active"] is False

    db.migrate_agent_session("__draft__", "sess-real-1")
    assert db.list_agent_messages("__draft__") == []
    assert db.list_steering_notes("__draft__", active_only=False) == []
    assert len(db.list_agent_messages("sess-real-1")) == 1
    assert [n["note"] for n in db.list_steering_notes("sess-real-1")] == ["排除综述"]



