"""SQLite persistence layer for LitLoop (sessions, pool items, decisions, watches).

Phase 1 introduces the schema, WAL-mode connections with explicit closing,
versioned migrations via ``PRAGMA user_version`` + ``schema_migrations``,
idempotent legacy JSON import, and watch table operations.
"""

from __future__ import annotations

import json
import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Iterator

from . import config, store
from .log import get_logger
from .terms.text import paper_key

logger = get_logger()

DB_SCHEMA = 1


def db_path() -> Path:
    """Return the SQLite database path under ``config.DATA_DIR``, creating parents."""
    p = Path(config.DATA_DIR) / "litloop.db"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _resolve_db_file(data_dir: Path | str | None) -> Path | None:
    if data_dir is None:
        return None
    return Path(data_dir) / "litloop.db"


def _now_iso() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _migrate_v1(conn: sqlite3.Connection) -> None:
    statements = [
        """
        CREATE TABLE IF NOT EXISTS sessions (
            id TEXT PRIMARY KEY,
            schema_version INTEGER NOT NULL DEFAULT 1,
            created TEXT NOT NULL DEFAULT '',
            updated TEXT NOT NULL DEFAULT '',
            question TEXT NOT NULL DEFAULT '',
            mode TEXT NOT NULL DEFAULT 'quick',
            criteria_json TEXT NOT NULL DEFAULT '[]',
            searches_json TEXT NOT NULL DEFAULT '[]',
            summary_json TEXT,
            pool_state_json TEXT
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS pool_items (
            session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
            paper_key TEXT NOT NULL,
            doi TEXT,
            openalex TEXT,
            title TEXT NOT NULL DEFAULT '',
            year INTEGER,
            journal TEXT NOT NULL DEFAULT '',
            cited_by_count INTEGER NOT NULL DEFAULT 0,
            abstract TEXT NOT NULL DEFAULT '',
            score REAL,
            data_json TEXT NOT NULL DEFAULT '{}',
            PRIMARY KEY (session_id, paper_key)
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_pool_items_session_score ON pool_items(session_id, score DESC)",
        "CREATE INDEX IF NOT EXISTS idx_pool_items_doi ON pool_items(doi)",
        """
        CREATE TABLE IF NOT EXISTS decisions (
            session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
            paper_key TEXT NOT NULL,
            n INTEGER,
            doi TEXT,
            title TEXT NOT NULL DEFAULT '',
            year INTEGER,
            journal TEXT NOT NULL DEFAULT '',
            journal_rank TEXT NOT NULL DEFAULT '',
            authors_json TEXT NOT NULL DEFAULT '[]',
            cited_by_count INTEGER NOT NULL DEFAULT 0,
            abstract TEXT NOT NULL DEFAULT '',
            abstract_zh TEXT,
            abstract_missing INTEGER,
            retracted INTEGER,
            concern INTEGER,
            relevance TEXT NOT NULL DEFAULT '',
            reason TEXT NOT NULL DEFAULT '',
            user_relevance TEXT,
            decision TEXT,
            reject_reason TEXT,
            data_json TEXT NOT NULL DEFAULT '{}',
            PRIMARY KEY (session_id, paper_key)
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_decisions_session_n ON decisions(session_id, n)",
        "CREATE INDEX IF NOT EXISTS idx_decisions_doi ON decisions(doi)",
        """
        CREATE TABLE IF NOT EXISTS watches (
            id TEXT PRIMARY KEY,
            question TEXT NOT NULL DEFAULT '',
            query TEXT NOT NULL,
            core_terms_json TEXT NOT NULL DEFAULT '[]',
            session TEXT NOT NULL DEFAULT '',
            created TEXT NOT NULL DEFAULT '',
            checked TEXT NOT NULL DEFAULT '',
            last_new INTEGER NOT NULL DEFAULT 0,
            seen_json TEXT NOT NULL DEFAULT '[]'
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_watches_checked ON watches(checked)",
    ]
    for stmt in statements:
        conn.execute(stmt)


MIGRATIONS: list[tuple[int, str, Callable[[sqlite3.Connection], None]]] = [
    (1, "initial schema", _migrate_v1),
]


def _apply_pragmas(conn: sqlite3.Connection) -> None:
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 30000")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.row_factory = sqlite3.Row


def _run_migrations(conn: sqlite3.Connection) -> None:
    ver = int(conn.execute("PRAGMA user_version").fetchone()[0])
    if ver > DB_SCHEMA:
        conn.close()
        raise store.SchemaTooNew(
            f"litloop.db was written by a newer LitLoop (schema {ver}, "
            f"this copy knows {DB_SCHEMA}); update LitLoop"
        )
    if ver >= DB_SCHEMA:
        return

    conn.execute("BEGIN IMMEDIATE")
    try:
        ver = int(conn.execute("PRAGMA user_version").fetchone()[0])
        if ver > DB_SCHEMA:
            raise store.SchemaTooNew(
                f"litloop.db was written by a newer LitLoop (schema {ver}, "
                f"this copy knows {DB_SCHEMA}); update LitLoop"
            )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version INTEGER PRIMARY KEY,
                applied_at TEXT NOT NULL,
                note TEXT NOT NULL
            )
            """
        )
        for m_ver, m_note, m_fn in sorted(MIGRATIONS, key=lambda item: item[0]):
            if ver < m_ver <= DB_SCHEMA:
                m_fn(conn)
                conn.execute(
                    "INSERT OR IGNORE INTO schema_migrations (version, applied_at, note) "
                    "VALUES (?, ?, ?)",
                    (m_ver, _now_iso(), m_note),
                )
                conn.execute(f"PRAGMA user_version = {int(m_ver)}")
                ver = m_ver
        if conn.in_transaction:
            conn.execute("COMMIT")
    except BaseException:
        if conn.in_transaction:
            try:
                conn.execute("ROLLBACK")
            except sqlite3.Error:
                pass
        conn.close()
        raise


def _open_connection(db_file: Path | str | None = None) -> sqlite3.Connection:
    target = Path(db_file) if db_file is not None else db_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(target), timeout=30.0, isolation_level=None)
    try:
        _apply_pragmas(conn)
        _run_migrations(conn)
    except BaseException:
        try:
            conn.close()
        except sqlite3.Error:
            pass
        raise
    return conn


@contextmanager
def connect(db_file: Path | str | None = None) -> Iterator[sqlite3.Connection]:
    """Open a WAL-mode SQLite connection and always close it on exit."""
    conn = _open_connection(db_file)
    try:
        yield conn
    finally:
        conn.close()


@contextmanager
def transaction(db_file: Path | str | None = None) -> Iterator[sqlite3.Connection]:
    """Open a connection wrapped in an IMMEDIATE transaction, closed on exit."""
    with connect(db_file) as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            yield conn
        except BaseException:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        else:
            if conn.in_transaction:
                conn.execute("COMMIT")


# --------------------------------------------------------------------------- watches


def _watch_row_to_dict(row: sqlite3.Row, *, include_seen: bool) -> dict[str, Any]:
    seen = json.loads(row["seen_json"] or "[]")
    core_terms = json.loads(row["core_terms_json"] or "[]")
    out: dict[str, Any] = {
        "id": row["id"],
        "question": row["question"],
        "query": row["query"],
        "core_terms": core_terms,
        "session": row["session"],
        "created": row["created"],
        "checked": row["checked"],
        "last_new": row["last_new"],
    }
    if include_seen:
        out["seen"] = seen
    else:
        out["seen_count"] = len(seen)
    return out


def list_watches(db_file: Path | str | None = None) -> list[dict[str, Any]]:
    """Return watches newest-first without the full ``seen`` list."""
    with connect(db_file) as conn:
        rows = conn.execute("SELECT * FROM watches").fetchall()
    items = [_watch_row_to_dict(r, include_seen=False) for r in rows]
    return sorted(items, key=lambda w: str(w.get("checked") or w.get("created") or ""), reverse=True)


def get_watch(wid: str, db_file: Path | str | None = None) -> dict[str, Any] | None:
    """Return the full watch dict (including ``seen``), or ``None`` if absent."""
    with connect(db_file) as conn:
        row = conn.execute("SELECT * FROM watches WHERE id = ?", (wid,)).fetchone()
    return _watch_row_to_dict(row, include_seen=True) if row is not None else None


def add_watch(
    wid: str,
    question: str,
    query: str,
    core_terms: list[str] | None = None,
    seen_keys: list[str] | None = None,
    session: str = "",
    created: str | None = None,
    checked: str = "",
    last_new: int = 0,
    db_file: Path | str | None = None,
) -> dict[str, Any]:
    """Insert a watch row and return its summary dict."""
    created_str = created if created is not None else time.strftime("%Y-%m-%d %H:%M")
    core_list = list(core_terms or [])
    seen_list = list(seen_keys or [])
    with transaction(db_file) as conn:
        conn.execute(
            """
            INSERT INTO watches (
                id, question, query, core_terms_json, session,
                created, checked, last_new, seen_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                wid,
                question,
                query,
                json.dumps(core_list, ensure_ascii=False),
                session,
                created_str,
                checked,
                int(last_new),
                json.dumps(seen_list, ensure_ascii=False),
            ),
        )
    return {
        "id": wid,
        "question": question,
        "query": query,
        "core_terms": core_list,
        "session": session,
        "created": created_str,
        "checked": checked,
        "last_new": int(last_new),
        "seen_count": len(seen_list),
    }


def remove_watch(wid: str, db_file: Path | str | None = None) -> bool:
    """Delete a watch by id; return ``True`` if a row was removed."""
    with transaction(db_file) as conn:
        cur = conn.execute("DELETE FROM watches WHERE id = ?", (wid,))
        return cur.rowcount > 0


def update_watch_check(
    wid: str,
    *,
    seen_keys: list[str],
    checked: str,
    last_new: int,
    db_file: Path | str | None = None,
) -> bool:
    """Update ``seen_json``, ``checked`` and ``last_new`` after a check run."""
    with transaction(db_file) as conn:
        cur = conn.execute(
            """
            UPDATE watches
            SET seen_json = ?, checked = ?, last_new = ?
            WHERE id = ?
            """,
            (json.dumps(list(seen_keys), ensure_ascii=False), checked, int(last_new), wid),
        )
        return cur.rowcount > 0


def due_watches(
    days: float,
    *,
    now: datetime | None = None,
    db_file: Path | str | None = None,
) -> list[dict[str, Any]]:
    """Return watches never checked or checked at least ``days`` ago."""
    ref = now or datetime.now()
    threshold = timedelta(days=float(days))
    with connect(db_file) as conn:
        rows = conn.execute("SELECT * FROM watches").fetchall()
    due: list[dict[str, Any]] = []
    for row in rows:
        checked = str(row["checked"] or "")
        if checked:
            try:
                if ref - datetime.fromisoformat(checked) < threshold:
                    continue
            except ValueError:
                pass
        due.append(_watch_row_to_dict(row, include_seen=True))
    return due


# --------------------------------------------------------------------------- legacy JSON import


def _read_json_safe(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError) as exc:
        logger.warning("db: skipping unreadable legacy JSON %s (%s)", path, exc)
        return None


def import_legacy_watches(
    data_dir: Path | str | None = None,
    legacy_path: Path | str | None = None,
) -> int:
    """Idempotently import ``watches.json`` into SQLite without deleting the file."""
    base = Path(data_dir) if data_dir is not None else Path(config.DATA_DIR)
    src = Path(legacy_path) if legacy_path is not None else base / "watches.json"
    if not src.exists():
        return 0
    data = _read_json_safe(src)
    if not isinstance(data, dict):
        return 0
    raw_watches = data.get("watches")
    if not isinstance(raw_watches, list):
        return 0

    db_file = _resolve_db_file(data_dir)
    inserted = 0
    with transaction(db_file) as conn:
        for w in raw_watches:
            if not isinstance(w, dict):
                continue
            wid = str(w.get("id") or "").strip()
            query = str(w.get("query") or "").strip()
            if not wid or not query:
                continue
            core_terms = w.get("core_terms") if isinstance(w.get("core_terms"), list) else []
            seen = w.get("seen") if isinstance(w.get("seen"), list) else []
            cur = conn.execute(
                """
                INSERT OR IGNORE INTO watches (
                    id, question, query, core_terms_json, session,
                    created, checked, last_new, seen_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    wid,
                    str(w.get("question") or ""),
                    query,
                    json.dumps(core_terms, ensure_ascii=False),
                    str(w.get("session") or ""),
                    str(w.get("created") or ""),
                    str(w.get("checked") or ""),
                    int(w.get("last_new") or 0),
                    json.dumps(seen, ensure_ascii=False),
                ),
            )
            if cur.rowcount > 0:
                inserted += 1
    return inserted


_DECISION_KNOWN_KEYS = frozenset(
    {
        "n",
        "doi",
        "title",
        "year",
        "journal",
        "journal_rank",
        "authors",
        "cited_by_count",
        "abstract",
        "abstract_zh",
        "abstract_missing",
        "retracted",
        "concern",
        "relevance",
        "reason",
        "user_relevance",
        "decision",
        "reject_reason",
    }
)


def _opt_bool_int(value: Any) -> int | None:
    if value is None:
        return None
    return 1 if bool(value) else 0


def _opt_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _opt_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _collect_session_ids(sdir: Path) -> list[str]:
    sids: set[str] = set()
    for p in sdir.iterdir():
        if not p.is_file():
            continue
        name = p.name
        if name.startswith(".") or not name.endswith(".json"):
            continue
        if name.endswith(".progress.json"):
            continue
        if name.endswith(".summary.json"):
            sids.add(name[: -len(".summary.json")])
        elif name.endswith(".pool.json"):
            sids.add(name[: -len(".pool.json")])
        else:
            sids.add(name[: -len(".json")])
    return sorted(s for s in sids if s)


def _import_session_file(conn: sqlite3.Connection, sid: str, sdata: dict[str, Any], counts: dict[str, int]) -> None:
    criteria = sdata.get("criteria") if isinstance(sdata.get("criteria"), list) else []
    searches = sdata.get("searches") if isinstance(sdata.get("searches"), list) else []
    cur = conn.execute(
        """
        INSERT OR IGNORE INTO sessions (
            id, schema_version, created, updated, question, mode,
            criteria_json, searches_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            sid,
            _opt_int(sdata.get("schema")) or 1,
            str(sdata.get("created") or ""),
            str(sdata.get("updated") or ""),
            str(sdata.get("question") or ""),
            str(sdata.get("mode") or "quick"),
            json.dumps(criteria, ensure_ascii=False),
            json.dumps(searches, ensure_ascii=False),
        ),
    )
    if cur.rowcount > 0:
        counts["sessions"] += 1

    items = sdata.get("items")
    if not isinstance(items, list):
        return
    for item in items:
        if not isinstance(item, dict):
            continue
        pkey = paper_key(item)
        authors = item.get("authors") if isinstance(item.get("authors"), list) else []
        extra = {k: v for k, v in item.items() if k not in _DECISION_KNOWN_KEYS}
        dcur = conn.execute(
            """
            INSERT OR IGNORE INTO decisions (
                session_id, paper_key, n, doi, title, year, journal,
                journal_rank, authors_json, cited_by_count, abstract,
                abstract_zh, abstract_missing, retracted, concern,
                relevance, reason, user_relevance, decision, reject_reason,
                data_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                sid,
                pkey,
                _opt_int(item.get("n")),
                str(item["doi"]) if item.get("doi") is not None else None,
                str(item.get("title") or ""),
                _opt_int(item.get("year")),
                str(item.get("journal") or ""),
                str(item.get("journal_rank") or ""),
                json.dumps(authors, ensure_ascii=False),
                _opt_int(item.get("cited_by_count")) or 0,
                str(item.get("abstract") or ""),
                str(item["abstract_zh"]) if item.get("abstract_zh") is not None else None,
                _opt_bool_int(item.get("abstract_missing")),
                _opt_bool_int(item.get("retracted")),
                _opt_bool_int(item.get("concern")),
                str(item.get("relevance") or ""),
                str(item.get("reason") or ""),
                str(item["user_relevance"]) if item.get("user_relevance") is not None else None,
                str(item["decision"]) if item.get("decision") is not None else None,
                str(item["reject_reason"]) if item.get("reject_reason") is not None else None,
                json.dumps(extra, ensure_ascii=False),
            ),
        )
        if dcur.rowcount > 0:
            counts["decisions"] += 1


def _import_summary_file(conn: sqlite3.Connection, sid: str, summary: dict[str, Any], counts: dict[str, int]) -> None:
    cur = conn.execute(
        "INSERT OR IGNORE INTO sessions (id, question, mode, updated) VALUES (?, ?, ?, ?)",
        (
            sid,
            str(summary.get("question") or ""),
            str(summary.get("mode") or "deep"),
            str(summary.get("updated") or ""),
        ),
    )
    if cur.rowcount > 0:
        counts["sessions"] += 1
    conn.execute(
        "UPDATE sessions SET summary_json = ? WHERE id = ? AND summary_json IS NULL",
        (json.dumps(summary, ensure_ascii=False), sid),
    )


def _import_pool_file(conn: sqlite3.Connection, sid: str, pdata: dict[str, Any], counts: dict[str, int]) -> None:
    cur = conn.execute(
        "INSERT OR IGNORE INTO sessions (id, question, mode) VALUES (?, ?, ?)",
        (sid, str(pdata.get("question") or ""), "deep"),
    )
    if cur.rowcount > 0:
        counts["sessions"] += 1

    pool_meta = {k: v for k, v in pdata.items() if k != "pool"}
    conn.execute(
        "UPDATE sessions SET pool_state_json = ? WHERE id = ? AND pool_state_json IS NULL",
        (json.dumps(pool_meta, ensure_ascii=False), sid),
    )

    pool = pdata.get("pool")
    if not isinstance(pool, dict):
        return
    for pkey, rec in pool.items():
        if not isinstance(rec, dict):
            continue
        pcur = conn.execute(
            """
            INSERT OR IGNORE INTO pool_items (
                session_id, paper_key, doi, openalex, title, year, journal,
                cited_by_count, abstract, score, data_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                sid,
                str(pkey),
                str(rec["doi"]) if rec.get("doi") is not None else None,
                str(rec["openalex"]) if rec.get("openalex") is not None else None,
                str(rec.get("title") or ""),
                _opt_int(rec.get("year")),
                str(rec.get("journal") or ""),
                _opt_int(rec.get("cited_by_count")) or 0,
                str(rec.get("abstract") or ""),
                _opt_float(rec.get("score")),
                json.dumps(rec, ensure_ascii=False),
            ),
        )
        if pcur.rowcount > 0:
            counts["pool_items"] += 1


def import_legacy_data(data_dir: Path | str | None = None) -> dict[str, int]:
    """Idempotently import legacy JSON files (watches + screening sessions/summaries/pools).

    Never deletes any source JSON file and skips malformed JSON without crashing.
    """
    base = Path(data_dir) if data_dir is not None else Path(config.DATA_DIR)
    counts: dict[str, int] = {
        "sessions": 0,
        "decisions": 0,
        "pool_items": 0,
        "watches": import_legacy_watches(data_dir=data_dir),
    }
    sdir = base / "screening"
    if not sdir.is_dir():
        return counts

    db_file = _resolve_db_file(data_dir)
    sids = _collect_session_ids(sdir)
    with transaction(db_file) as conn:
        for sid in sids:
            main_file = sdir / f"{sid}.json"
            if main_file.is_file():
                sdata = _read_json_safe(main_file)
                if isinstance(sdata, dict):
                    _import_session_file(conn, sid, sdata, counts)

            summary_file = sdir / f"{sid}.summary.json"
            if summary_file.is_file():
                summary = _read_json_safe(summary_file)
                if isinstance(summary, dict):
                    _import_summary_file(conn, sid, summary, counts)

            pool_file = sdir / f"{sid}.pool.json"
            if pool_file.is_file():
                pdata = _read_json_safe(pool_file)
                if isinstance(pdata, dict):
                    _import_pool_file(conn, sid, pdata, counts)

    return counts
