"""Chromium profile cookie diagnostics, on a throwaway SQLite file."""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

from litloop.browser import profile_health

OFFSET = profile_health.CHROME_EPOCH_OFFSET_SECONDS


def _chrome_time(seconds_from_now: int) -> int:
    return int((time.time() + seconds_from_now + OFFSET) * 1_000_000)


def _make_profile(root: Path, rows: list[tuple[str, int]], *, with_expires: bool = True) -> Path:
    network = root / "Default" / "Network"
    network.mkdir(parents=True, exist_ok=True)
    db = network / "Cookies"
    conn = sqlite3.connect(db)
    if with_expires:
        conn.execute("create table cookies (host_key text, name text, value text, expires_utc integer)")
        conn.executemany(
            "insert into cookies values (?, 'sid', 'secret-value', ?)",
            [(host, expires) for host, expires in rows],
        )
    else:
        conn.execute("create table cookies (host_key text, name text, value text)")
        conn.executemany("insert into cookies values (?, 'sid', 'secret-value')", [(host,) for host in rows])
    conn.commit()
    conn.close()
    return root


def test_cookie_times_convert_and_zero_means_session():
    assert profile_health.chrome_time_to_iso(0) == "session"
    assert profile_health.chrome_time_to_iso(None) == "session"
    assert profile_health.chrome_time_to_iso(_chrome_time(0)).startswith(str(time.gmtime().tm_year))
    assert profile_health.chrome_time_to_iso("nonsense") == ""


def test_a_missing_network_file_is_reported_not_raised(tmp_path):
    report = profile_health.inspect_browser_profile(tmp_path, ["example.edu"])
    assert report["exists"] is True
    assert report["cookies_db_exists"] is False
    assert report["domains"] == {} and report["error"] == ""


def test_cookies_are_counted_per_host_without_revealing_values(tmp_path):
    profile = _make_profile(
        tmp_path,
        [
            ("id.example.edu", _chrome_time(-3600)),   # already expired
            ("id.example.edu", _chrome_time(86400)),   # still valid
            ("example.edu", _chrome_time(86400)),
            (".other.org", _chrome_time(86400)),       # outside the watched domains
        ],
    )
    report = profile_health.inspect_browser_profile(profile, ["example.edu"])
    entry = report["domains"]["example.edu"]
    assert entry["cookie_count"] == 3
    assert entry["persistent_cookie_count"] == 3
    assert entry["expired_cookie_count"] == 1
    assert entry["latest_expires_at"]
    assert {h["host"] for h in entry["hosts"]} == {"id.example.edu", "example.edu"}
    assert "secret-value" not in str(report)


def test_session_cookies_are_counted_separately(tmp_path):
    profile = _make_profile(tmp_path, [("id.example.edu", 0), ("id.example.edu", _chrome_time(600))])
    entry = profile_health.inspect_browser_profile(profile, ["example.edu"])["domains"]["example.edu"]
    assert entry["session_cookie_count"] == 1
    assert entry["persistent_cookie_count"] == 1


def test_a_table_without_an_expiry_column_still_counts(tmp_path):
    profile = _make_profile(tmp_path, ["id.example.edu", "example.edu"], with_expires=False)
    entry = profile_health.inspect_browser_profile(profile, ["example.edu"])["domains"]["example.edu"]
    assert entry["cookie_count"] == 2
    assert entry["expired_cookie_count"] == 0
    assert entry["latest_expires_at"] == ""


def test_a_broken_database_is_an_error_string(tmp_path):
    network = tmp_path / "Default" / "Network"
    network.mkdir(parents=True)
    (network / "Cookies").write_bytes(b"definitely not sqlite")
    report = profile_health.inspect_browser_profile(tmp_path, ["example.edu"])
    assert report["cookies_db_exists"] is True
    assert "Error" in report["error"] or report["error"]


def test_profile_candidates_are_deduplicated_in_priority_order(tmp_path, monkeypatch):
    monkeypatch.setattr(profile_health, "DATA_DIR", tmp_path / "data")
    configured = tmp_path / "custom"
    dirs = profile_health.candidate_profile_dirs(
        {"chrome_profile_dir": str(configured)}, workspace=tmp_path / "work"
    )
    assert dirs[0] == configured
    assert dirs[1] == tmp_path / "data" / "browser_profiles" / "webvpn"
    assert dirs[2] == tmp_path / "data" / "chrome-profile"
    assert dirs[3] == tmp_path / "work" / ".chrome-sciencedirect"
    assert len(dirs) == 4

    repeated = profile_health.candidate_profile_dirs(
        {"chrome_profile_dir": str(tmp_path / "data" / "chrome-profile")}
    )
    assert len(repeated) == len({str(p) for p in repeated})
