"""New-version reminder: checked weekly in the background, shown as one line in results."""

import json
import time

import pytest

from litloop import __version__, config, server, store, updates


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(updates, "COMPONENTS", [
        ("LitLoop", lambda cfg: __version__, lambda: "99.0.0", "运行 python scripts/deploy.py 更新"),
        ("easyseek", lambda cfg: "0.4.0", lambda: "0.4.0", "运行 python scripts/deploy.py 更新"),
        ("zotero-mcp", lambda cfg: "0.14.1", lambda: (_ for _ in ()).throw(OSError("offline")), "pip"),
    ])
    return tmp_path


def test_check_compares_versions_and_skips_failures(home):
    data = updates.check({})
    by = {c["name"]: c for c in data["components"]}
    assert by["LitLoop"]["update"] is True and by["easyseek"]["update"] is False
    assert "zotero-mcp" not in by  # offline: tried again next time
    assert store.read_json(home / "update_check.json")["components"] == data["components"]


def test_notice_reads_the_cache_and_does_not_wait(home, monkeypatch):
    updates.check({})
    started = []
    monkeypatch.setattr(updates, "_refresh_in_background", lambda cfg: started.append(1))
    note = updates.notice({"update_check_days": 7})
    assert note.startswith("有新版本：LitLoop 99.0.0") and "deploy.py" in note and "easyseek" not in note
    assert started == []  # checked just now: not due


def test_a_stale_check_starts_in_the_background(home, monkeypatch):
    store.write_json(home / "update_check.json", {"checked": time.time() - 8 * 86400, "components": []})
    started = []
    monkeypatch.setattr(updates, "_refresh_in_background", lambda cfg: started.append(1))
    assert updates.notice({"update_check_days": 7}) is None and started == [1]


def test_off_means_off(home, monkeypatch):
    updates.check({})
    monkeypatch.setattr(updates, "_refresh_in_background", lambda cfg: pytest.fail("no check when off"))
    assert updates.notice({"update_check_days": 0}) is None


def test_litloop_already_updated_is_not_reported(home, monkeypatch):
    store.write_json(home / "update_check.json", {"checked": time.time(), "components": [
        {"name": "LitLoop", "installed": "1.0.0", "latest": "1.0.1", "update": True, "how": "x"}]})
    assert updates.available({"update_check_days": 7}) == []  # the running version is newer


def test_tool_results_carry_the_notice(monkeypatch):
    monkeypatch.setattr(updates, "notice", lambda cfg=None: "有新版本：X")
    assert json.loads(server._with_update_notice('{"results": []}'))["update_notice"] == "有新版本：X"
    assert server._with_update_notice("not json") == "not json"
    monkeypatch.setattr(updates, "notice", lambda cfg=None: None)
    assert "update_notice" not in json.loads(server._with_update_notice('{"results": []}'))
