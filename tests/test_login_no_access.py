"""Logged into the publisher as the school and still no full text: no school routes."""

from __future__ import annotations

import litloop.sources as src
from litloop import _publisher_strategies_core as core
from litloop.error_text import reason


def _setup(monkeypatch, tmp_path, called):
    monkeypatch.setattr(src, "_build_free_sources", lambda doi, config: [(lambda *a, **k: None, "Crossref")])
    monkeypatch.setattr(src, "_run_tiers_parallel", lambda *a, **k: None)
    monkeypatch.setattr(src, "_run_institutional_sequence", lambda *a, **k: called.append(1))
    monkeypatch.setattr(src, "_build_institutional_sources", lambda *a, **k: [(None, "WebVPN")])
    import litloop.identifiers as ids
    monkeypatch.setattr(ids, "validate_doi", lambda doi: (True, ""))  # no network
    import litloop.session_heal as sh
    monkeypatch.setattr(sh, "ensure_institutional_sessions", lambda *a, **k: {})


def test_school_routes_skipped(monkeypatch, tmp_path):
    called = []
    _setup(monkeypatch, tmp_path, called)
    base = src.load_config()
    monkeypatch.setattr(src, "load_config", lambda: {**base, "school_routes_after_publisher_login": False})
    core.LOGIN_NO_ACCESS.add("10.1007/978-981-96-2944-2_32")
    r = src.download("10.1007/978-981-96-2944-2_32", str(tmp_path), _institutional=True)
    assert not called
    assert r.get("error_type") == "not_entitled"
    assert "学校没有订阅" in (reason(r.get("reason")) or "")


def test_setting_keeps_school_routes(monkeypatch, tmp_path):
    called = []
    _setup(monkeypatch, tmp_path, called)
    base = src.load_config()
    monkeypatch.setattr(src, "load_config", lambda: {**base, "school_routes_after_publisher_login": True})
    core.LOGIN_NO_ACCESS.add("10.1007/x-2")
    src.download("10.1007/x-2", str(tmp_path), _institutional=True)
    assert called
