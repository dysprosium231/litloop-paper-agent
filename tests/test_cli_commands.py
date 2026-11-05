"""The small helpers behind the CLI commands."""

from __future__ import annotations

import types

import pytest
import typer

from litloop.cli import commands


def test_school_type_labels_are_human_names():
    assert commands._school_type_label("webvpn") == "CampusPortal"
    assert commands._school_type_label("atrust") == "CampusConnector"
    assert commands._school_type_label("ezproxy") == "LibraryPortal"
    assert commands._school_type_label("custom") == "custom"


def test_doi_files_skip_comments_and_blank_lines(tmp_path):
    path = tmp_path / "list.txt"
    path.write_text("# header\n10.1/a\n\n  10.2/b  \n#10.3/c\n", encoding="utf-8")
    assert commands._read_dois(path) == ["10.1/a", "10.2/b"]


def test_version_tuples_ignore_suffixes_and_empty_parts():
    assert commands._parse_version_tuple("0.5.9") == (0, 5, 9)
    assert commands._parse_version_tuple("1.2.3b1") == (1, 2, 3)
    assert commands._parse_version_tuple("") == (0,)
    assert commands._parse_version_tuple("2.x") == (2, 0)


def test_a_missing_cloakbrowser_is_reported(monkeypatch):
    monkeypatch.setattr(commands, "_installed_package_version", lambda name: "")
    status, detail = commands._cloakbrowser_version_status()
    assert status == "not_installed" and "pip install" in detail


def test_an_old_cloakbrowser_gets_an_upgrade_hint(monkeypatch):
    monkeypatch.setattr(commands, "_installed_package_version", lambda name: "0.5.1")
    status, detail = commands._cloakbrowser_version_status()
    assert status == "outdated"
    assert commands.CLOAKBROWSER_RECOMMENDED_MIN in detail


def test_a_recent_cloakbrowser_is_fine(monkeypatch):
    monkeypatch.setattr(commands, "_installed_package_version", lambda name: "0.9.0")
    assert commands._cloakbrowser_version_status() == ("ok", "0.9.0")


def _entry(name="示例大学", school_type="webvpn", host="https://vpn.example.edu"):
    return types.SimpleNamespace(name=name, school_type=school_type, host=host)


def test_applying_a_school_clears_the_other_channel(monkeypatch):
    monkeypatch.setattr(commands, "get_school", lambda school: _entry())
    cfg = {"ezproxy_login_url": "https://old"}
    entry = commands._apply_school_config(cfg, "example")
    assert entry.name == "示例大学"
    assert cfg["instsci_school"] == "示例大学"
    assert cfg["instsci_base_url"] == "https://vpn.example.edu"
    assert cfg["ezproxy_login_url"] == ""


def test_applying_an_ezproxy_school_clears_the_webvpn_url(monkeypatch):
    monkeypatch.setattr(commands, "get_school", lambda school: _entry(school_type="ezproxy", host="https://lib.example.edu"))
    cfg = {"instsci_base_url": "https://old"}
    commands._apply_school_config(cfg, "example")
    assert cfg["ezproxy_login_url"] == "https://lib.example.edu"
    assert cfg["instsci_base_url"] == ""


def test_the_access_url_prefers_ezproxy():
    assert commands._access_url({"ezproxy_login_url": "https://lib", "instsci_base_url": "https://vpn"}) == "https://lib"
    assert commands._access_url({"instsci_base_url": "https://vpn"}) == "https://vpn"
    assert commands._access_url({}) == ""


def test_the_configured_institution_is_trimmed():
    assert commands._configured_subscription_institution({"carsi_idp_name": " 某大学 "}) == "某大学"
    assert commands._configured_subscription_institution({"instsci_school": "B 大学"}) == "B 大学"
    assert commands._configured_subscription_institution({}) == ""


def test_an_explicit_institution_wins_without_prompting(monkeypatch):
    monkeypatch.setattr(commands, "save_config", lambda cfg: None)
    assert commands._resolve_subscription_institution({}, "  某大学  ") == "某大学"


def test_a_configured_institution_avoids_the_prompt(monkeypatch):
    monkeypatch.setattr(commands, "save_config", lambda cfg: None)
    assert commands._resolve_subscription_institution({"carsi_idp_name": "某大学"}, "") == "某大学"


def test_a_missing_institution_stops_when_prompting_is_off(monkeypatch):
    with pytest.raises(typer.Exit):
        commands._resolve_subscription_institution({}, "", prompt=False)


def test_path_status_distinguishes_missing_from_present(tmp_path):
    assert commands._path_status("") == ("missing", "")
    assert commands._path_status(str(tmp_path))[0] == "ok"
    assert commands._path_status(str(tmp_path / "nope"))[0] == "missing"
