"""SQLite persistence layer for LitLoop (sessions, pool items, decisions, watches).

Phase 1 introduces the schema, WAL-mode connections with explicit closing,
versioned migrations via ``PRAGMA user_version`` + ``schema_migrations``,
idempotent legacy JSON import, and watch table operations.
Phase 2 adds screening session & decision operations with automatic on-demand
import of legacy ``<session>.json`` files.
Phase 3 adds deep-search candidate pool & summary operations with automatic
on-demand import of legacy ``<session>.pool.json`` / ``<session>.summary.json`` files.
"""

from __future__ import annotations

import json
import re
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

DB_SCHEMA = 2


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


def _migrate_v2(conn: sqlite3.Connection) -> None:
    statements = [
        """
        CREATE TABLE IF NOT EXISTS agent_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL DEFAULT '',
            actions_json TEXT NOT NULL DEFAULT '[]',
            usage_json TEXT,
            created TEXT NOT NULL DEFAULT ''
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_agent_messages_session ON agent_messages(session_id, id)",
        """
        CREATE TABLE IF NOT EXISTS steering_notes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            note TEXT NOT NULL,
            active INTEGER NOT NULL DEFAULT 1,
            created TEXT NOT NULL DEFAULT ''
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_steering_notes_session ON steering_notes(session_id, active, id)",
    ]
    for stmt in statements:
        conn.execute(stmt)


MIGRATIONS: list[tuple[int, str, Callable[[sqlite3.Connection], None]]] = [
    (1, "initial schema", _migrate_v1),
    (2, "agent messages and steering notes", _migrate_v2),
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


# --------------------------------------------------------------------------- screening sessions & decisions

RELEVANCE = ("高", "中", "低", "不相关")
ABSTRACT_CHARS = 1200
_RANK = {r: i for i, r in enumerate(RELEVANCE)}
_EN_RELEVANCE = {"high": "高", "medium": "中", "low": "低", "irrelevant": "不相关", "none": "不相关"}
_SAFE_SESSION_RE = re.compile(r"^[\w\-. \u4e00-\u9fff]+$")


def _norm_relevance(value: Any) -> str:
    v = str(value or "").strip()
    v = _EN_RELEVANCE.get(v.lower(), v)
    if v not in _RANK:
        raise ValueError(f"relevance must be one of {RELEVANCE}, got {value!r}")
    return v


def _slug(text: str) -> str:
    s = re.sub(r"[^\w\u4e00-\u9fff]+", "-", text.strip().lower()).strip("-")
    return s[:40] or "search"


def _screening_dir(db_file: Path | str | None = None) -> Path:
    if db_file is not None:
        return Path(db_file).parent / "screening"
    from .search.screening import screening_dir
    return screening_dir()


def _decision_brief(it: dict[str, Any]) -> dict[str, Any]:
    return {
        "n": it["n"],
        "relevance": it["relevance"],
        "title": str(it.get("title") or "")[:90],
        "doi": it.get("doi"),
    }


def _decision_row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    item: dict[str, Any] = {}
    if row["n"] is not None:
        item["n"] = int(row["n"])
    if row["doi"]:
        item["doi"] = row["doi"]
    if row["title"]:
        item["title"] = row["title"]
    if row["year"] is not None:
        item["year"] = int(row["year"])
    if row["journal"]:
        item["journal"] = row["journal"]
    if row["journal_rank"]:
        item["journal_rank"] = row["journal_rank"]
    authors = json.loads(row["authors_json"] or "[]")
    if authors:
        item["authors"] = authors
    if row["cited_by_count"] is not None:
        item["cited_by_count"] = int(row["cited_by_count"])
    if row["abstract"]:
        item["abstract"] = row["abstract"]
    if row["abstract_zh"]:
        item["abstract_zh"] = row["abstract_zh"]
    if row["abstract_missing"] is not None:
        item["abstract_missing"] = bool(row["abstract_missing"])
    if row["retracted"] is not None:
        item["retracted"] = bool(row["retracted"])
    if row["concern"] is not None:
        item["concern"] = bool(row["concern"])
    item["relevance"] = row["relevance"]
    item["reason"] = row["reason"]
    if row["user_relevance"] is not None:
        item["user_relevance"] = row["user_relevance"]
    item["decision"] = row["decision"]
    if row["reject_reason"] is not None:
        item["reject_reason"] = row["reject_reason"]
    extra = json.loads(row["data_json"] or "{}")
    if isinstance(extra, dict) and extra:
        for k, v in extra.items():
            if k not in item:
                item[k] = v
    return item


def _load_session_record(conn: sqlite3.Connection, session: str) -> dict[str, Any]:
    srow = conn.execute("SELECT * FROM sessions WHERE id = ?", (session,)).fetchone()
    if srow is None:
        raise FileNotFoundError(f"no screening session {session!r}")
    ver = int(srow["schema_version"] or 1)
    if ver > store.SCHEMA:
        raise store.SchemaTooNew(
            f"{session} was written by a newer LitLoop (schema {ver}, this copy knows {store.SCHEMA}); update LitLoop"
        )
    drows = conn.execute(
        "SELECT * FROM decisions WHERE session_id = ? ORDER BY n ASC, rowid ASC",
        (session,),
    ).fetchall()
    return {
        "schema": ver,
        "id": srow["id"],
        "created": srow["created"],
        "updated": srow["updated"],
        "question": srow["question"],
        "mode": srow["mode"],
        "criteria": json.loads(srow["criteria_json"] or "[]"),
        "searches": json.loads(srow["searches_json"] or "[]"),
        "items": [_decision_row_to_dict(r) for r in drows],
    }


def _legacy_session_file(session: str, sdir: Path) -> Path | None:
    if not session or session.startswith(".") or "/" in session or "\\" in session:
        return None
    if not _SAFE_SESSION_RE.match(session):
        return None
    p = sdir / f"{session}.json"
    return p if p.is_file() else None


def _read_legacy_session(path: Path) -> dict[str, Any] | None:
    sdata = _read_json_safe(path)
    if not isinstance(sdata, dict):
        return None
    ver = _opt_int(sdata.get("schema")) or 1
    if ver > store.SCHEMA:
        raise store.SchemaTooNew(
            f"{path.name} was written by a newer LitLoop (schema {ver}, this copy knows {store.SCHEMA}); update LitLoop"
        )
    return sdata


def _ensure_session_in_conn(
    conn: sqlite3.Connection,
    session: str,
    *,
    sdir: Path,
    in_write_tx: bool,
) -> bool:
    row = conn.execute("SELECT 1 FROM sessions WHERE id = ?", (session,)).fetchone()
    if row is not None:
        return True
    legacy = _legacy_session_file(session, sdir)
    if legacy is None:
        return False
    sdata = _read_legacy_session(legacy)
    if sdata is None:
        return False
    counts = {"sessions": 0, "decisions": 0}
    if not in_write_tx:
        conn.execute("BEGIN IMMEDIATE")
        try:
            _import_session_file(conn, session, sdata, counts)
            if conn.in_transaction:
                conn.execute("COMMIT")
        except BaseException:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
    else:
        _import_session_file(conn, session, sdata, counts)
    return True


def _sync_missing_session_jsons(conn: sqlite3.Connection, *, sdir: Path) -> None:
    if not sdir.is_dir():
        return
    disk_files = [
        p for p in sdir.glob("*.json")
        if p.name.count(".") == 1 and not p.name.startswith(".")
    ]
    if not disk_files:
        return
    existing = {r[0] for r in conn.execute("SELECT id FROM sessions").fetchall()}
    missing = [(p.stem, p) for p in disk_files if p.stem not in existing]
    if not missing:
        return
    counts = {"sessions": 0, "decisions": 0}
    conn.execute("BEGIN IMMEDIATE")
    try:
        for sid, path in missing:
            sdata = _read_legacy_session(path)
            if sdata is not None:
                _import_session_file(conn, sid, sdata, counts)
        if conn.in_transaction:
            conn.execute("COMMIT")
    except BaseException:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise


def _upsert_decision(
    conn: sqlite3.Connection,
    sid: str,
    item: dict[str, Any],
    pkey: str | None = None,
) -> None:
    key = pkey or paper_key(item)
    authors = item.get("authors") if isinstance(item.get("authors"), list) else []
    extra = {k: v for k, v in item.items() if k not in _DECISION_KNOWN_KEYS}
    conn.execute(
        """
        INSERT INTO decisions (
            session_id, paper_key, n, doi, title, year, journal,
            journal_rank, authors_json, cited_by_count, abstract,
            abstract_zh, abstract_missing, retracted, concern,
            relevance, reason, user_relevance, decision, reject_reason,
            data_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(session_id, paper_key) DO UPDATE SET
            n = excluded.n,
            doi = excluded.doi,
            title = excluded.title,
            year = excluded.year,
            journal = excluded.journal,
            journal_rank = excluded.journal_rank,
            authors_json = excluded.authors_json,
            cited_by_count = excluded.cited_by_count,
            abstract = excluded.abstract,
            abstract_zh = excluded.abstract_zh,
            abstract_missing = excluded.abstract_missing,
            retracted = excluded.retracted,
            concern = excluded.concern,
            relevance = excluded.relevance,
            reason = excluded.reason,
            user_relevance = excluded.user_relevance,
            decision = excluded.decision,
            reject_reason = excluded.reject_reason,
            data_json = excluded.data_json
        """,
        (
            sid,
            key,
            _opt_int(item.get("n")),
            str(item["doi"]) if item.get("doi") else None,
            str(item.get("title") or ""),
            _opt_int(item.get("year")),
            str(item.get("journal") or ""),
            str(item.get("journal_rank") or ""),
            json.dumps(authors, ensure_ascii=False),
            _opt_int(item.get("cited_by_count")) or 0,
            str(item.get("abstract") or ""),
            str(item["abstract_zh"]) if item.get("abstract_zh") else None,
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


def session_exists(session: str, *, db_file: Path | str | None = None) -> bool:
    """Return True if SESSION exists in SQLite or can be imported from legacy JSON."""
    sdir = _screening_dir(db_file)
    with connect(db_file) as conn:
        return _ensure_session_in_conn(conn, session, sdir=sdir, in_write_tx=False)


def load_screening(session: str, *, db_file: Path | str | None = None) -> dict[str, Any]:
    """Load one screening session record (auto-importing legacy JSON if present)."""
    sdir = _screening_dir(db_file)
    with connect(db_file) as conn:
        if not _ensure_session_in_conn(conn, session, sdir=sdir, in_write_tx=False):
            raise FileNotFoundError(f"no screening session {session!r}")
        return _load_session_record(conn, session)


def save_screening(
    question: str,
    items: list[dict[str, Any]],
    criteria: list[str] | str | None = None,
    query: str = "",
    mode: str = "quick",
    session: str | None = None,
    search_params: dict[str, Any] | None = None,
    *,
    db_file: Path | str | None = None,
) -> dict[str, Any]:
    """Create or merge a screening session in SQLite and return the full record + summary."""
    sid = session or time.strftime("%Y%m%d-%H%M%S") + "-" + _slug(question or query)
    sdir = _screening_dir(db_file)
    now = _now_iso()
    with transaction(db_file) as conn:
        if session:
            if not _ensure_session_in_conn(conn, sid, sdir=sdir, in_write_tx=True):
                raise FileNotFoundError(f"no screening session {sid!r}")
            data = _load_session_record(conn, sid)
        else:
            data = {
                "schema": store.SCHEMA,
                "id": sid,
                "created": now,
                "updated": now,
                "question": question,
                "mode": mode,
                "criteria": [],
                "searches": [],
                "items": [],
            }
        if criteria:
            data["criteria"] = [criteria] if isinstance(criteria, str) else list(criteria)

        by_key = {paper_key(it): it for it in data["items"]}
        added = 0
        for raw in items:
            item = {
                k: raw.get(k)
                for k in (
                    "doi",
                    "title",
                    "year",
                    "journal",
                    "journal_rank",
                    "authors",
                    "cited_by_count",
                    "abstract",
                    "abstract_missing",
                    "retracted",
                    "concern",
                )
                if raw.get(k) not in (None, "", [])
            }
            if item.get("abstract"):
                item["abstract"] = str(item["abstract"])[:ABSTRACT_CHARS]
            if isinstance(item.get("authors"), list):
                item["authors"] = item["authors"][:3]
            if raw.get("abstract_zh"):
                item["abstract_zh"] = str(raw["abstract_zh"]).strip()[:ABSTRACT_CHARS]
            item["relevance"] = _norm_relevance(raw.get("relevance"))
            item["reason"] = str(raw.get("reason") or "").strip()

            key = paper_key(item)
            old = by_key.get(key)
            if old:
                old.update(item)
            else:
                item["decision"] = None
                data["items"].append(item)
                by_key[key] = item
                added += 1

        data["items"].sort(key=lambda it: (_RANK[it["relevance"]], -(it.get("cited_by_count") or 0)))
        for n, it in enumerate(data["items"], 1):
            it["n"] = n

        if query:
            data["searches"].append(
                {
                    "query": query,
                    "time": now,
                    "params": search_params or {},
                    "results": len(items),
                    "new": added,
                }
            )
        data["updated"] = now

        conn.execute(
            """
            INSERT INTO sessions (
                id, schema_version, created, updated, question, mode,
                criteria_json, searches_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                schema_version = excluded.schema_version,
                updated = excluded.updated,
                question = excluded.question,
                mode = excluded.mode,
                criteria_json = excluded.criteria_json,
                searches_json = excluded.searches_json
            """,
            (
                data["id"],
                int(data.get("schema") or store.SCHEMA),
                data["created"],
                data["updated"],
                data["question"],
                data["mode"],
                json.dumps(data["criteria"], ensure_ascii=False),
                json.dumps(data["searches"], ensure_ascii=False),
            ),
        )
        for it in data["items"]:
            _upsert_decision(conn, data["id"], it)

    return {
        "session": data["id"],
        "added": added,
        "total": len(data["items"]),
        "by_relevance": {r: sum(1 for it in data["items"] if it["relevance"] == r) for r in RELEVANCE},
        "numbering": [_decision_brief(it) for it in data["items"]],
        "record": data,
    }


def list_sessions(limit: int = 20, *, db_file: Path | str | None = None) -> list[dict[str, Any]]:
    """Return screening sessions newest-first with item and selected counts."""
    sdir = _screening_dir(db_file)
    with connect(db_file) as conn:
        _sync_missing_session_jsons(conn, sdir=sdir)
        rows = conn.execute(
            """
            SELECT s.id, s.question, s.updated,
                   COUNT(d.paper_key) AS items,
                   COALESCE(SUM(CASE WHEN d.decision = 'selected' THEN 1 ELSE 0 END), 0) AS selected
            FROM sessions s
            LEFT JOIN decisions d ON d.session_id = s.id
            GROUP BY s.id
            ORDER BY s.id DESC
            LIMIT ?
            """,
            (int(limit),),
        ).fetchall()
    return [
        {
            "session": r["id"],
            "question": r["question"],
            "updated": r["updated"],
            "items": int(r["items"]),
            "selected": int(r["selected"]),
        }
        for r in rows
    ]


def history(limit: int = 500, *, db_file: Path | str | None = None) -> list[dict[str, Any]]:
    """Return screening session history with relevance counts and selected picks, newest-first."""
    sdir = _screening_dir(db_file)
    with connect(db_file) as conn:
        _sync_missing_session_jsons(conn, sdir=sdir)
        srows = conn.execute(
            "SELECT * FROM sessions ORDER BY id DESC LIMIT ?",
            (int(limit),),
        ).fetchall()
        out: list[dict[str, Any]] = []
        for srow in srows:
            sid = srow["id"]
            drows = conn.execute(
                "SELECT * FROM decisions WHERE session_id = ? ORDER BY n ASC, rowid ASC",
                (sid,),
            ).fetchall()
            items = [_decision_row_to_dict(r) for r in drows]
            out.append(
                {
                    "session": sid,
                    "question": srow["question"],
                    "mode": srow["mode"],
                    "created": srow["created"],
                    "updated": srow["updated"],
                    "items": len(items),
                    "by_relevance": {
                        r: sum(1 for it in items if (it.get("user_relevance") or it["relevance"]) == r)
                        for r in RELEVANCE
                    },
                    "selected": [
                        {k: it.get(k) for k in ("doi", "title", "year", "journal", "journal_rank")}
                        for it in items
                        if it.get("decision") == "selected"
                    ],
                }
            )
    return out


def decide_screening(
    session: str,
    selected: list[int | str],
    rejected: list[int | str] | None = None,
    *,
    db_file: Path | str | None = None,
) -> dict[str, Any]:
    """Record selected/rejected picks by item number or DOI, replacing prior decisions."""
    sdir = _screening_dir(db_file)
    now = _now_iso()
    with transaction(db_file) as conn:
        if not _ensure_session_in_conn(conn, session, sdir=sdir, in_write_tx=True):
            raise FileNotFoundError(f"no screening session {session!r}")
        data = _load_session_record(conn, session)
        index: dict[str, dict[str, Any]] = {}
        for it in data["items"]:
            index[str(it["n"])] = it
            if it.get("doi"):
                index[str(it["doi"]).lower()] = it
        refs = [(ref, "selected") for ref in selected or []] + [
            (ref, "rejected") for ref in rejected or []
        ]
        unknown = [ref for ref, _ in refs if str(ref).strip().lower() not in index]
        if unknown:
            return {
                "session": session,
                "error": "unknown item numbers/DOIs; nothing recorded",
                "unknown": unknown,
                "numbering": [_decision_brief(it) for it in data["items"]],
            }

        for it in data["items"]:
            it["decision"] = None
            it.pop("reject_reason", None)
        for ref, decision in refs:
            index[str(ref).strip().lower()]["decision"] = decision

        conn.execute(
            "UPDATE decisions SET decision = NULL, reject_reason = NULL WHERE session_id = ?",
            (session,),
        )
        for ref, decision in refs:
            it = index[str(ref).strip().lower()]
            conn.execute(
                "UPDATE decisions SET decision = ? WHERE session_id = ? AND paper_key = ?",
                (decision, session, paper_key(it)),
            )
        data["updated"] = now
        conn.execute("UPDATE sessions SET updated = ? WHERE id = ?", (now, session))

    chosen = [it for it in data["items"] if it.get("decision") == "selected"]
    return {
        "session": session,
        "selected": len(chosen),
        "identifiers": [it["doi"] for it in chosen if it.get("doi")],
        "chosen": [_decision_brief(it) for it in chosen],
        "rejected": [_decision_brief(it) for it in data["items"] if it.get("decision") == "rejected"],
        "unknown": unknown,
        "record": data,
    }


def translate_screening(
    session: str,
    items: list[dict[str, Any]],
    *,
    db_file: Path | str | None = None,
) -> dict[str, Any]:
    """Store Chinese abstracts in SQLite without altering ratings or decisions."""
    sdir = _screening_dir(db_file)
    now = _now_iso()
    with transaction(db_file) as conn:
        if not _ensure_session_in_conn(conn, session, sdir=sdir, in_write_tx=True):
            raise FileNotFoundError(f"no screening session {session!r}")
        data = _load_session_record(conn, session)
        by_doi = {str(it.get("doi") or "").lower(): it for it in data["items"] if it.get("doi")}
        by_n = {it.get("n"): it for it in data["items"]}
        saved, missing = 0, []
        for raw in items or []:
            target = by_doi.get(str(raw.get("doi") or "").lower()) or by_n.get(raw.get("n"))
            text = str(raw.get("abstract_zh") or "").strip()
            if not target or not text:
                missing.append(raw.get("doi") or raw.get("n"))
                continue
            zh = text[:ABSTRACT_CHARS]
            conn.execute(
                "UPDATE decisions SET abstract_zh = ? WHERE session_id = ? AND paper_key = ?",
                (zh, session, paper_key(target)),
            )
            saved += 1
        conn.execute("UPDATE sessions SET updated = ? WHERE id = ?", (now, session))
    return {"session": session, "saved": saved, "not_found": missing}


def _find_decision_row(
    conn: sqlite3.Connection,
    session: str,
    doi: str | None,
    title: str | None,
) -> sqlite3.Row | None:
    key = paper_key({"doi": doi, "title": title})
    row = conn.execute(
        "SELECT * FROM decisions WHERE session_id = ? AND paper_key = ?",
        (session, key),
    ).fetchone()
    if row is not None:
        return row
    # Fallback scan in case legacy paper_key differed slightly
    for candidate in conn.execute(
        "SELECT * FROM decisions WHERE session_id = ?", (session,)
    ).fetchall():
        if paper_key({"doi": candidate["doi"], "title": candidate["title"]}) == key:
            return candidate
    return None


def update_item_rating(
    session: str,
    doi: str | None,
    title: str | None,
    relevance: str | None,
    *,
    create_paper: dict[str, Any] | None = None,
    db_file: Path | str | None = None,
) -> dict[str, Any] | None:
    """Update or create a user rating for one paper in a session.

    Returns ``None`` if the paper is not in the session and cannot be created.
    """
    if relevance is not None and relevance not in _RANK:
        raise ValueError(f"relevance must be one of {RELEVANCE}, got {relevance!r}")
    sdir = _screening_dir(db_file)
    now = _now_iso()
    with transaction(db_file) as conn:
        if not _ensure_session_in_conn(conn, session, sdir=sdir, in_write_tx=True):
            raise FileNotFoundError(f"no screening session {session!r}")
        row = _find_decision_row(conn, session, doi, title)
        if row is None:
            if relevance is None or not create_paper:
                return None
            max_n_row = conn.execute(
                "SELECT COALESCE(MAX(n), 0) FROM decisions WHERE session_id = ?",
                (session,),
            ).fetchone()
            next_n = int(max_n_row[0] or 0) + 1
            item = {
                k: create_paper.get(k)
                for k in (
                    "doi",
                    "title",
                    "year",
                    "journal",
                    "journal_rank",
                    "authors",
                    "cited_by_count",
                    "abstract",
                    "abstract_missing",
                    "retracted",
                    "concern",
                )
                if create_paper.get(k) not in (None, "", [])
            }
            if item.get("abstract"):
                item["abstract"] = str(item["abstract"])[:ABSTRACT_CHARS]
            if isinstance(item.get("authors"), list):
                item["authors"] = item["authors"][:3]
            item["n"] = next_n
            item["relevance"] = relevance
            item["user_relevance"] = relevance
            item["reason"] = ""
            item["decision"] = None
            _upsert_decision(conn, session, item)
            conn.execute("UPDATE sessions SET updated = ? WHERE id = ?", (now, session))
            created = _find_decision_row(conn, session, item.get("doi"), item.get("title"))
            edited = int(
                conn.execute(
                    "SELECT COUNT(*) FROM decisions WHERE session_id = ? AND user_relevance IS NOT NULL",
                    (session,),
                ).fetchone()[0]
            )
            return {"item": _decision_row_to_dict(created) if created else item, "edited": edited}

        pkey = row["paper_key"]
        if not row["reason"]:
            if relevance is None:
                conn.execute(
                    "DELETE FROM decisions WHERE session_id = ? AND paper_key = ?",
                    (session, pkey),
                )
                conn.execute("UPDATE sessions SET updated = ? WHERE id = ?", (now, session))
                edited = int(
                    conn.execute(
                        "SELECT COUNT(*) FROM decisions WHERE session_id = ? AND user_relevance IS NOT NULL",
                        (session,),
                    ).fetchone()[0]
                )
                return {"item": None, "edited": edited}
            conn.execute(
                "UPDATE decisions SET relevance = ?, user_relevance = ? WHERE session_id = ? AND paper_key = ?",
                (relevance, relevance, session, pkey),
            )
        elif relevance is None or relevance == row["relevance"]:
            conn.execute(
                "UPDATE decisions SET user_relevance = NULL WHERE session_id = ? AND paper_key = ?",
                (session, pkey),
            )
        else:
            conn.execute(
                "UPDATE decisions SET user_relevance = ? WHERE session_id = ? AND paper_key = ?",
                (relevance, session, pkey),
            )
        conn.execute("UPDATE sessions SET updated = ? WHERE id = ?", (now, session))
        updated_row = conn.execute(
            "SELECT * FROM decisions WHERE session_id = ? AND paper_key = ?",
            (session, pkey),
        ).fetchone()
        edited = int(
            conn.execute(
                "SELECT COUNT(*) FROM decisions WHERE session_id = ? AND user_relevance IS NOT NULL",
                (session,),
            ).fetchone()[0]
        )
        return {"item": _decision_row_to_dict(updated_row) if updated_row else None, "edited": edited}


def update_item_decision(
    session: str,
    doi: str | None,
    title: str | None,
    decision: str | None,
    reason: str | None = None,
    *,
    db_file: Path | str | None = None,
) -> dict[str, Any] | None:
    """Update decision and reject_reason for one paper in a session."""
    if decision not in (None, "selected", "rejected"):
        raise ValueError(f"invalid decision {decision!r}")
    sdir = _screening_dir(db_file)
    now = _now_iso()
    with transaction(db_file) as conn:
        if not _ensure_session_in_conn(conn, session, sdir=sdir, in_write_tx=True):
            raise FileNotFoundError(f"no screening session {session!r}")
        row = _find_decision_row(conn, session, doi, title)
        if row is None:
            return None
        clean_reason = " ".join((reason or "").split())[:60]
        reject_reason = clean_reason if decision == "rejected" and clean_reason else None
        conn.execute(
            "UPDATE decisions SET decision = ?, reject_reason = ? WHERE session_id = ? AND paper_key = ?",
            (decision, reject_reason, session, row["paper_key"]),
        )
        conn.execute("UPDATE sessions SET updated = ? WHERE id = ?", (now, session))
        updated_row = conn.execute(
            "SELECT * FROM decisions WHERE session_id = ? AND paper_key = ?",
            (session, row["paper_key"]),
        ).fetchone()
        return {"item": _decision_row_to_dict(updated_row)} if updated_row else None


def set_item_abstract_zh(
    session: str,
    doi: str | None,
    title: str | None,
    abstract_zh: str,
    *,
    db_file: Path | str | None = None,
) -> bool:
    """Persist translated Chinese abstract for a single decision row."""
    sdir = _screening_dir(db_file)
    now = _now_iso()
    with transaction(db_file) as conn:
        if not _ensure_session_in_conn(conn, session, sdir=sdir, in_write_tx=True):
            raise FileNotFoundError(f"no screening session {session!r}")
        row = _find_decision_row(conn, session, doi, title)
        if row is None:
            return False
        conn.execute(
            "UPDATE decisions SET abstract_zh = ? WHERE session_id = ? AND paper_key = ?",
            (str(abstract_zh)[:ABSTRACT_CHARS], session, row["paper_key"]),
        )
        conn.execute("UPDATE sessions SET updated = ? WHERE id = ?", (now, session))
        return True


def batch_update_items(
    session: str,
    ops: list[Any],
    *,
    create_papers: dict[str, dict[str, Any]] | None = None,
    db_file: Path | str | None = None,
) -> dict[str, Any]:
    """Atomically apply batch rating/decision updates (including unrated batch items) in one transaction."""
    sdir = _screening_dir(db_file)
    now = _now_iso()
    papers_by_key = create_papers or {}

    def _field(op: Any, name: str) -> tuple[bool, Any]:
        if isinstance(op, dict):
            return (name in op, op.get(name))
        return (name in getattr(op, "model_fields_set", set()), getattr(op, name, None))

    with transaction(db_file) as conn:
        if not _ensure_session_in_conn(conn, session, sdir=sdir, in_write_tx=True):
            raise FileNotFoundError(f"no screening session {session!r}")

        rows = conn.execute(
            "SELECT * FROM decisions WHERE session_id = ? ORDER BY n ASC, rowid ASC",
            (session,),
        ).fetchall()
        by_key: dict[str, dict[str, Any]] = {}
        max_n = 0
        for r in rows:
            it = _decision_row_to_dict(r)
            by_key[paper_key(it)] = it
            if int(r["n"] or 0) > max_n:
                max_n = int(r["n"] or 0)

        created_keys: set[str] = set()
        for op in ops:
            _, delete_if_unrated = _field(op, "delete_if_unrated")
            _, clear_user_rel = _field(op, "clear_user_relevance")
            has_rel, rel_val = _field(op, "relevance")
            if delete_if_unrated or clear_user_rel or not has_rel or rel_val is None:
                continue
            _, doi = _field(op, "doi")
            _, title = _field(op, "title")
            key = paper_key({"doi": doi, "title": title})
            if key in by_key:
                continue
            paper = papers_by_key.get(key)
            if not paper:
                continue
            max_n += 1
            new_item = {
                k: paper.get(k)
                for k in (
                    "doi",
                    "title",
                    "year",
                    "journal",
                    "journal_rank",
                    "authors",
                    "cited_by_count",
                    "abstract",
                    "abstract_missing",
                    "retracted",
                    "concern",
                )
                if paper.get(k) not in (None, "", [])
            }
            if new_item.get("abstract"):
                new_item["abstract"] = str(new_item["abstract"])[:ABSTRACT_CHARS]
            if isinstance(new_item.get("authors"), list):
                new_item["authors"] = new_item["authors"][:3]
            new_item["n"] = max_n
            new_item["relevance"] = _norm_relevance(rel_val)
            new_item["user_relevance"] = new_item["relevance"]
            new_item["reason"] = ""
            new_item["decision"] = None
            _upsert_decision(conn, session, new_item, pkey=key)
            by_key[key] = new_item
            created_keys.add(key)

        updated = 0
        skipped = 0
        previous: list[dict[str, Any]] = []
        for op in ops:
            _, doi = _field(op, "doi")
            _, title = _field(op, "title")
            _, delete_if_unrated = _field(op, "delete_if_unrated")
            _, clear_user_rel = _field(op, "clear_user_relevance")
            has_rel, rel_val = _field(op, "relevance")
            has_dec, dec_val = _field(op, "decision")
            has_rr, rr_val = _field(op, "reject_reason")

            key = paper_key({"doi": doi, "title": title})
            item = by_key.get(key)
            if item is None:
                skipped += 1
                continue

            existed = key not in created_keys
            prev = {
                "doi": item.get("doi"),
                "title": item.get("title"),
                "existed": existed,
                "relevance": item.get("relevance") if existed else None,
                "user_relevance": item.get("user_relevance") if existed else None,
                "decision": item.get("decision") if existed else None,
                "reject_reason": item.get("reject_reason") if existed else None,
            }
            created_keys.discard(key)

            if delete_if_unrated:
                if not item.get("reason"):
                    conn.execute(
                        "DELETE FROM decisions WHERE session_id = ? AND paper_key = ?",
                        (session, key),
                    )
                    by_key.pop(key, None)
                    updated += 1
                    previous.append(prev)
                    continue
                item.pop("user_relevance", None)
            else:
                if clear_user_rel or has_rel:
                    target_rel = None if clear_user_rel else rel_val
                    if not item.get("reason"):
                        if target_rel is None:
                            conn.execute(
                                "DELETE FROM decisions WHERE session_id = ? AND paper_key = ?",
                                (session, key),
                            )
                            by_key.pop(key, None)
                            updated += 1
                            previous.append(prev)
                            continue
                        item["relevance"] = item["user_relevance"] = target_rel
                    elif target_rel is None or target_rel == item["relevance"]:
                        item.pop("user_relevance", None)
                    else:
                        item["user_relevance"] = target_rel
                if has_dec:
                    item["decision"] = dec_val
                    raw_reason = rr_val if has_rr else None
                    reason = " ".join((raw_reason or "").split())[:60]
                    if dec_val == "rejected" and reason:
                        item["reject_reason"] = reason
                    else:
                        item.pop("reject_reason", None)

            _upsert_decision(conn, session, item, pkey=key)
            updated += 1
            previous.append(prev)

        conn.execute("UPDATE sessions SET updated = ? WHERE id = ?", (now, session))
        edited = int(
            conn.execute(
                "SELECT COUNT(*) FROM decisions WHERE session_id = ? AND user_relevance IS NOT NULL",
                (session,),
            ).fetchone()[0]
        )
    return {"updated": updated, "skipped": skipped, "edited": edited, "previous": previous}


def pending_selected_by_session(*, db_file: Path | str | None = None) -> list[dict[str, Any]]:
    """Return sessions newest-first with their selected papers that carry a DOI."""
    sdir = _screening_dir(db_file)
    with connect(db_file) as conn:
        _sync_missing_session_jsons(conn, sdir=sdir)
        rows = conn.execute(
            """
            SELECT s.id AS session, s.question, d.doi, d.title, d.year, d.journal
            FROM sessions s
            JOIN decisions d ON d.session_id = s.id
            WHERE d.decision = 'selected' AND d.doi IS NOT NULL AND d.doi != ''
            ORDER BY s.id DESC, d.n ASC, d.rowid ASC
            """
        ).fetchall()
    grouped: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for r in rows:
        sid = r["session"]
        if sid not in grouped:
            grouped[sid] = {"session": sid, "question": r["question"], "papers": []}
            order.append(sid)
        grouped[sid]["papers"].append(
            {"doi": r["doi"], "title": r["title"], "year": r["year"], "journal": r["journal"]}
        )
    return [grouped[sid] for sid in order]


# --------------------------------------------------------------------------- deep search pool & summary

_BATCH_FIELDS = (
    "doi",
    "title",
    "year",
    "journal",
    "journal_rank",
    "retracted",
    "concern",
    "authors",
    "cited_by_count",
    "abstract",
    "abstract_missing",
)


def _safe_session_file(session: str, sdir: Path, suffix: str) -> Path | None:
    if not session or session.startswith(".") or "/" in session or "\\" in session:
        return None
    if not _SAFE_SESSION_RE.match(session):
        return None
    p = sdir / f"{session}{suffix}"
    return p if p.is_file() else None


def _ensure_pool_in_conn(
    conn: sqlite3.Connection,
    session: str,
    *,
    sdir: Path,
    in_write_tx: bool,
) -> bool:
    row = conn.execute(
        "SELECT pool_state_json FROM sessions WHERE id = ?", (session,)
    ).fetchone()
    if row is not None and row["pool_state_json"] is not None:
        return True
    has_items = conn.execute(
        "SELECT 1 FROM pool_items WHERE session_id = ? LIMIT 1", (session,)
    ).fetchone()
    if has_items is not None:
        return True

    legacy = _safe_session_file(session, sdir, ".pool.json")
    if legacy is None:
        return False
    pdata = _read_json_safe(legacy)
    if not isinstance(pdata, dict):
        return False

    counts = {"sessions": 0, "pool_items": 0}
    if not in_write_tx:
        conn.execute("BEGIN IMMEDIATE")
        try:
            _import_pool_file(conn, session, pdata, counts)
            if conn.in_transaction:
                conn.execute("COMMIT")
        except BaseException:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
    else:
        _import_pool_file(conn, session, pdata, counts)
    return True


def _ensure_summary_in_conn(
    conn: sqlite3.Connection,
    session: str,
    *,
    sdir: Path,
    in_write_tx: bool,
) -> str | None:
    row = conn.execute(
        "SELECT summary_json FROM sessions WHERE id = ?", (session,)
    ).fetchone()
    if row is not None and row["summary_json"] is not None:
        return str(row["summary_json"])

    legacy = _safe_session_file(session, sdir, ".summary.json")
    if legacy is None:
        return None
    summary = _read_json_safe(legacy)
    if not isinstance(summary, dict):
        return None

    counts = {"sessions": 0}
    if not in_write_tx:
        conn.execute("BEGIN IMMEDIATE")
        try:
            _import_summary_file(conn, session, summary, counts)
            if conn.in_transaction:
                conn.execute("COMMIT")
        except BaseException:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
    else:
        _import_summary_file(conn, session, summary, counts)
    return json.dumps(summary, ensure_ascii=False)


def _pool_row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    raw_data = row["data_json"]
    if raw_data:
        try:
            rec = json.loads(raw_data)
            if isinstance(rec, dict) and rec:
                return rec
        except (ValueError, TypeError):
            pass
    rec: dict[str, Any] = {}
    if row["doi"] is not None:
        rec["doi"] = row["doi"]
    if row["openalex"] is not None:
        rec["openalex"] = row["openalex"]
    rec["title"] = row["title"]
    if row["year"] is not None:
        rec["year"] = int(row["year"])
    if row["journal"]:
        rec["journal"] = row["journal"]
    rec["cited_by_count"] = int(row["cited_by_count"] or 0)
    if row["abstract"]:
        rec["abstract"] = row["abstract"]
    if row["score"] is not None:
        rec["score"] = float(row["score"])
    return rec


def has_pool(session: str, *, db_file: Path | str | None = None) -> bool:
    """Return True if a deep-search candidate pool exists in SQLite or legacy JSON."""
    sdir = _screening_dir(db_file)
    with connect(db_file) as conn:
        return _ensure_pool_in_conn(conn, session, sdir=sdir, in_write_tx=False)


def load_pool_state(session: str, *, db_file: Path | str | None = None) -> dict[str, Any]:
    """Load deep-search pool metadata and candidate items from SQLite (auto-importing legacy JSON)."""
    sdir = _screening_dir(db_file)
    with connect(db_file) as conn:
        if not _ensure_pool_in_conn(conn, session, sdir=sdir, in_write_tx=False):
            raise FileNotFoundError(f"no deep search for session {session!r}")
        srow = conn.execute(
            "SELECT pool_state_json, question FROM sessions WHERE id = ?", (session,)
        ).fetchone()
        meta: dict[str, Any] = {}
        if srow is not None and srow["pool_state_json"]:
            parsed = json.loads(srow["pool_state_json"])
            if isinstance(parsed, dict):
                meta = parsed
        meta.setdefault("session", session)
        if "question" not in meta and srow is not None and srow["question"]:
            meta["question"] = srow["question"]

        rows = conn.execute(
            """
            SELECT paper_key, doi, openalex, title, year, journal,
                   cited_by_count, abstract, score, data_json
            FROM pool_items
            WHERE session_id = ?
            ORDER BY rowid ASC
            """,
            (session,),
        ).fetchall()
        pool = {str(r["paper_key"]): _pool_row_to_dict(r) for r in rows}
    return {**meta, "pool": pool}


def save_pool_state(state: dict[str, Any], *, db_file: Path | str | None = None) -> None:
    """Persist deep-search pool metadata and candidate items in a single transaction."""
    sid = str(state["session"])
    now = _now_iso()
    pool_meta = {k: v for k, v in state.items() if k != "pool"}
    pool = state.get("pool") or {}
    pool_meta_json = json.dumps(pool_meta, ensure_ascii=False)

    with transaction(db_file) as conn:
        conn.execute(
            """
            INSERT INTO sessions (id, question, mode, updated, pool_state_json)
            VALUES (?, ?, 'deep', ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                pool_state_json = excluded.pool_state_json,
                updated = CASE WHEN sessions.updated = '' THEN excluded.updated ELSE sessions.updated END
            """,
            (sid, str(state.get("question") or ""), now, pool_meta_json),
        )

        existing_keys = {
            r[0]
            for r in conn.execute(
                "SELECT paper_key FROM pool_items WHERE session_id = ?", (sid,)
            ).fetchall()
        }
        new_keys = {str(k) for k, v in pool.items() if isinstance(v, dict)}
        stale_keys = existing_keys - new_keys
        if stale_keys:
            conn.executemany(
                "DELETE FROM pool_items WHERE session_id = ? AND paper_key = ?",
                [(sid, k) for k in stale_keys],
            )

        rows = []
        for pkey, rec in pool.items():
            if not isinstance(rec, dict):
                continue
            rows.append(
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
                )
            )
        if rows:
            conn.executemany(
                """
                INSERT OR REPLACE INTO pool_items (
                    session_id, paper_key, doi, openalex, title, year, journal,
                    cited_by_count, abstract, score, data_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )


def load_summary(session: str, *, db_file: Path | str | None = None) -> dict[str, Any] | None:
    """Load deep-search summary dict from SQLite (auto-importing legacy summary JSON)."""
    sdir = _screening_dir(db_file)
    with connect(db_file) as conn:
        raw = _ensure_summary_in_conn(conn, session, sdir=sdir, in_write_tx=False)
    if raw is None:
        return None
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else None
    except (ValueError, TypeError):
        return None


def save_summary(
    session: str,
    summary: dict[str, Any],
    *,
    db_file: Path | str | None = None,
) -> None:
    """Persist deep-search summary JSON into sessions.summary_json."""
    now = _now_iso()
    summary_json = json.dumps(summary, ensure_ascii=False)
    with transaction(db_file) as conn:
        conn.execute(
            """
            INSERT INTO sessions (id, question, mode, updated, summary_json)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                summary_json = excluded.summary_json
            """,
            (
                session,
                str(summary.get("question") or ""),
                str(summary.get("mode") or "deep"),
                str(summary.get("updated") or now),
                summary_json,
            ),
        )


def get_batch_paper(
    session: str,
    doi: str | None,
    title: str | None,
    *,
    db_file: Path | str | None = None,
) -> dict[str, Any] | None:
    """Return one paper from the session's latest batch without loading pool_items."""
    sdir = _screening_dir(db_file)
    with connect(db_file) as conn:
        if not _ensure_pool_in_conn(conn, session, sdir=sdir, in_write_tx=False):
            return None
        row = conn.execute(
            "SELECT pool_state_json FROM sessions WHERE id = ?", (session,)
        ).fetchone()
    if row is None or not row["pool_state_json"]:
        return None
    try:
        meta = json.loads(row["pool_state_json"])
    except (ValueError, TypeError):
        return None
    if not isinstance(meta, dict):
        return None
    target_key = paper_key({"doi": doi, "title": title})
    for b in meta.get("last_batch") or []:
        if isinstance(b, dict) and paper_key({"doi": b.get("doi"), "title": b.get("title")}) == target_key:
            return {f: b.get(f) for f in _BATCH_FIELDS}
    return None


def _row_to_agent_message(row: sqlite3.Row) -> dict[str, Any]:
    try:
        actions = json.loads(row["actions_json"] or "[]")
        if not isinstance(actions, list):
            actions = []
    except (ValueError, TypeError):
        actions = []
    usage: dict[str, Any] | None = None
    if row["usage_json"]:
        try:
            parsed = json.loads(row["usage_json"])
            if isinstance(parsed, dict):
                usage = parsed
        except (ValueError, TypeError):
            usage = None
    return {
        "id": int(row["id"]),
        "session_id": str(row["session_id"]),
        "role": str(row["role"]),
        "content": str(row["content"]),
        "actions": actions,
        "usage": usage,
        "created": str(row["created"]),
    }


def list_agent_messages(
    session_id: str,
    limit: int = 100,
    *,
    db_file: Path | str | None = None,
) -> list[dict[str, Any]]:
    """Return the most recent ``limit`` agent chat messages for ``session_id`` ordered by ``id ASC``."""
    if limit <= 0:
        return []
    with connect(db_file) as conn:
        rows = conn.execute(
            """
            SELECT id, session_id, role, content, actions_json, usage_json, created
            FROM (
                SELECT id, session_id, role, content, actions_json, usage_json, created
                FROM agent_messages
                WHERE session_id = ?
                ORDER BY id DESC
                LIMIT ?
            )
            ORDER BY id ASC
            """,
            (session_id, int(limit)),
        ).fetchall()
    return [_row_to_agent_message(r) for r in rows]


def add_agent_message(
    session_id: str,
    role: str,
    content: str,
    *,
    actions: list[dict[str, Any]] | None = None,
    usage: dict[str, Any] | None = None,
    db_file: Path | str | None = None,
) -> dict[str, Any]:
    """Append an agent chat message for ``session_id`` and return the stored record."""
    created = _now_iso()
    actions_list = list(actions) if actions is not None else []
    actions_json = json.dumps(actions_list, ensure_ascii=False)
    usage_json = json.dumps(usage, ensure_ascii=False) if usage is not None else None
    with transaction(db_file) as conn:
        cur = conn.execute(
            """
            INSERT INTO agent_messages (session_id, role, content, actions_json, usage_json, created)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (session_id, role, content, actions_json, usage_json, created),
        )
        msg_id = int(cur.lastrowid)
    return {
        "id": msg_id,
        "session_id": session_id,
        "role": role,
        "content": content,
        "actions": actions_list,
        "usage": dict(usage) if usage is not None else None,
        "created": created,
    }


def update_agent_action(
    message_id: int,
    action_id: str,
    status: str,
    result: dict[str, Any] | None = None,
    *,
    db_file: Path | str | None = None,
) -> dict[str, Any] | None:
    """Update status (and optional result) of an action card inside ``agent_messages``."""
    with transaction(db_file) as conn:
        row = conn.execute(
            "SELECT actions_json FROM agent_messages WHERE id = ?",
            (int(message_id),),
        ).fetchone()
        if row is None:
            return None
        try:
            actions = json.loads(row["actions_json"] or "[]")
        except (ValueError, TypeError):
            actions = []
        if not isinstance(actions, list):
            return None
        updated: dict[str, Any] | None = None
        for item in actions:
            if isinstance(item, dict) and item.get("id") == action_id:
                item["status"] = status
                if result is not None:
                    item["result"] = result
                updated = dict(item)
                break
        if updated is None:
            return None
        conn.execute(
            "UPDATE agent_messages SET actions_json = ? WHERE id = ?",
            (json.dumps(actions, ensure_ascii=False), int(message_id)),
        )
        return updated


def clear_agent_messages(session_id: str, *, db_file: Path | str | None = None) -> int:
    """Delete all agent chat messages for ``session_id`` and return the deleted row count."""
    with transaction(db_file) as conn:
        cur = conn.execute("DELETE FROM agent_messages WHERE session_id = ?", (session_id,))
        return int(cur.rowcount)


def truncate_agent_messages_from(
    session_id: str,
    from_message_id: int,
    *,
    db_file: Path | str | None = None,
) -> int:
    """Delete ``session_id`` chat messages with ``id >= from_message_id`` and return deleted count."""
    with transaction(db_file) as conn:
        cur = conn.execute(
            "DELETE FROM agent_messages WHERE session_id = ? AND id >= ?",
            (session_id, int(from_message_id)),
        )
        return int(cur.rowcount)


def migrate_agent_session(
    from_session: str,
    to_session: str,
    *,
    db_file: Path | str | None = None,
) -> None:
    """Move ``agent_messages`` and ``steering_notes`` from ``from_session`` to ``to_session``."""
    if from_session == to_session:
        return
    with transaction(db_file) as conn:
        conn.execute(
            "UPDATE agent_messages SET session_id = ? WHERE session_id = ?",
            (to_session, from_session),
        )
        conn.execute(
            "UPDATE steering_notes SET session_id = ? WHERE session_id = ?",
            (to_session, from_session),
        )


def list_steering_notes(
    session_id: str,
    active_only: bool = True,
    *,
    db_file: Path | str | None = None,
) -> list[dict[str, Any]]:
    """Return steering notes for ``session_id`` ordered by ``id ASC``."""
    sql = (
        "SELECT id, session_id, note, active, created FROM steering_notes "
        "WHERE session_id = ?"
    )
    if active_only:
        sql += " AND active = 1"
    sql += " ORDER BY id ASC"
    with connect(db_file) as conn:
        rows = conn.execute(sql, (session_id,)).fetchall()
    return [
        {
            "id": int(r["id"]),
            "session_id": str(r["session_id"]),
            "note": str(r["note"]),
            "active": bool(r["active"]),
            "created": str(r["created"]),
        }
        for r in rows
    ]


def add_steering_note(
    session_id: str,
    note: str,
    *,
    db_file: Path | str | None = None,
) -> dict[str, Any]:
    """Add an active steering note for ``session_id`` and return it."""
    created = _now_iso()
    with transaction(db_file) as conn:
        cur = conn.execute(
            """
            INSERT INTO steering_notes (session_id, note, active, created)
            VALUES (?, ?, 1, ?)
            """,
            (session_id, note, created),
        )
        note_id = int(cur.lastrowid)
    return {
        "id": note_id,
        "session_id": session_id,
        "note": note,
        "active": True,
        "created": created,
    }


def remove_steering_note(note_id: int, *, db_file: Path | str | None = None) -> bool:
    """Deactivate a steering note by ``note_id``, returning ``False`` if not found or already inactive."""
    with transaction(db_file) as conn:
        cur = conn.execute(
            "UPDATE steering_notes SET active = 0 WHERE id = ? AND active = 1",
            (int(note_id),),
        )
        return cur.rowcount > 0



