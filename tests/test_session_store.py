"""CookieStore: the WebVPN / CARSI session cookies kept between runs (auth.py)."""

from __future__ import annotations

import json

import requests

from litloop.session_store import CookieStore

NOW = 1_800_000_000.0


def test_save_keeps_session_and_unexpired_cookies_only(tmp_path):
    store = CookieStore(tmp_path / "sub" / "cookies.json")
    kept = store.save([
        {"name": "session", "value": "a", "domain": "webvpn.test"},             # session cookie: no expiry
        {"name": "fresh", "value": "b", "expires": NOW + 60},
        {"name": "stale", "value": "c", "expires": NOW - 60},
        {"name": "minus", "value": "d", "expires": -1},                        # -1 means a session cookie
        {"name": None, "value": "e"},
        {"name": "novalue"},
        "not a cookie",
    ], now=NOW)
    assert [c["name"] for c in kept] == ["session", "fresh", "minus"]
    assert json.loads(store.path.read_text(encoding="utf-8")) == kept
    assert kept[2]["expires"] == 0


def test_load_drops_cookies_that_expired_since(tmp_path):
    store = CookieStore(tmp_path / "cookies.json")
    store.save([{"name": "a", "value": "1", "expires": NOW + 10}, {"name": "b", "value": "2"}], now=NOW)
    assert [c["name"] for c in store.load(now=NOW + 20)] == ["b"]


def test_load_survives_missing_broken_and_wrong_shape_files(tmp_path):
    assert CookieStore(tmp_path / "none.json").load() == []
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    assert CookieStore(bad).load() == []
    bad.write_text('{"name": "a"}', encoding="utf-8")
    assert CookieStore(bad).load() == []


def test_load_into_a_requests_session(tmp_path):
    store = CookieStore(tmp_path / "cookies.json")
    store.save([{"name": "wengine_vpn_ticket", "value": "t", "domain": "webvpn.test", "path": "/"}], now=NOW)
    s = requests.Session()
    assert store.load_into(s, now=NOW)
    assert s.cookies.get("wengine_vpn_ticket", domain="webvpn.test") == "t"
    assert not CookieStore(tmp_path / "empty.json").load_into(requests.Session())


def test_cookie_files_are_readable_by_the_user_alone(tmp_path, monkeypatch):
    """On macOS and Linux a saved login session gets mode 0600; Windows is left alone."""
    from litloop import session_store
    modes = []
    monkeypatch.setattr(session_store.os, "chmod", lambda p, m: modes.append(m))
    monkeypatch.setattr(session_store, "_WINDOWS", False)
    session_store.CookieStore(tmp_path / "c.json").save([{"name": "a", "value": "1", "domain": ".x.org"}])
    assert modes == [0o600]
    monkeypatch.setattr(session_store, "_WINDOWS", True)
    session_store.keep_private(tmp_path / "c.json")
    assert modes == [0o600]
