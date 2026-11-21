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
        assert {"schema_migrations", "sessions", "pool_items", "decisions", "watches"} <= tables

        m_rows = conn.execute("SELECT version, note FROM schema_migrations ORDER BY version").fetchall()
        assert [(r["version"], r["note"]) for r in m_rows] == [(1, "initial schema")]

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
    # Initialize at v1 first
    with db.connect() as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 1

    def _migrate_v2(conn: sqlite3.Connection) -> None:
        conn.execute("CREATE TABLE test_v2_ext (id TEXT PRIMARY KEY)")

    monkeypatch.setattr(db, "DB_SCHEMA", 2)
    monkeypatch.setattr(db, "MIGRATIONS", [*db.MIGRATIONS, (2, "add test_v2_ext", _migrate_v2)])

    with db.connect() as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 2
        tables = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        assert "test_v2_ext" in tables
        rows = conn.execute("SELECT version, note FROM schema_migrations ORDER BY version").fetchall()
        assert [(r["version"], r["note"]) for r in rows] == [
            (1, "initial schema"),
            (2, "add test_v2_ext"),
        ]


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
