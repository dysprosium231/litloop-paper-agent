"""The setup diagnostic never touches the network or the real system in these tests."""

from __future__ import annotations

import contextlib

import pytest

from litloop.cli import doctor


@pytest.fixture
def no_tor(monkeypatch):
    """No Tor binary anywhere and nothing listening on the usual ports."""
    monkeypatch.setattr("litloop.tor.embedded._tor_binary", lambda config: None)
    monkeypatch.setattr("litloop.config.load_config", lambda: {})
    monkeypatch.setattr(doctor.shutil, "which", lambda name: None)

    def refuse(*args, **kwargs):
        raise ConnectionRefusedError("nobody home")

    monkeypatch.setattr(doctor.socket, "create_connection", refuse)


def test_the_system_report_names_os_arch_and_python():
    info = doctor.detect_system()
    assert info["os"] and info["arch"] and info["python"]
    assert "." in info["python"]


def test_an_embedded_tor_binary_counts_as_installed(monkeypatch, no_tor):
    monkeypatch.setattr("litloop.tor.embedded._tor_binary", lambda config: "/tmp/tor/tor.exe")
    result = doctor.check_tor()
    assert result["installed"] is True and result["embedded"] is True
    assert result["path"] == "/tmp/tor/tor.exe"
    assert result["running"] is False


def test_a_tor_binary_on_the_path_is_found(monkeypatch, no_tor):
    monkeypatch.setattr(doctor.shutil, "which", lambda name: "/usr/bin/tor" if name == "tor" else None)
    result = doctor.check_tor()
    assert result["installed"] is True and result["embedded"] is False
    assert result["path"] == "/usr/bin/tor"


def test_a_listening_port_means_tor_is_running(monkeypatch, no_tor):
    def connect(address, timeout=None):
        if address[1] == 9150:
            return contextlib.nullcontext()
        raise ConnectionRefusedError("closed")

    monkeypatch.setattr(doctor.socket, "create_connection", connect)
    result = doctor.check_tor()
    assert result["running"] is True and result["port"] == 9150


def test_the_browser_check_survives_an_import_error(monkeypatch):
    def boom(config):
        raise RuntimeError("引擎坏了")

    monkeypatch.setattr("litloop.browser.engine.is_available", boom)
    monkeypatch.setattr("litloop.config.load_config", lambda: {})
    assert doctor.check_browser() == {"running": False}


def test_install_commands_follow_the_platform(no_tor):
    assert "tor" in doctor.get_install_commands({"os": "Windows"}, {"running": False})
    linux = doctor.get_install_commands({"os": "Linux"}, {"running": False})["tor"]
    assert linux["method"] == "embedded"
    assert linux["alternatives"][0]["method"] == "apt"
    mac = doctor.get_install_commands({"os": "Darwin"}, {"running": False})["tor"]
    assert mac["alternatives"][0]["method"] == "homebrew"
    assert doctor.get_install_commands({"os": "Linux"}, {"running": True}) == {}


def test_readiness_needs_both_tor_and_a_browser(monkeypatch):
    monkeypatch.setattr(doctor, "detect_system", lambda: {"os": "Linux", "os_version": "", "arch": "x86_64", "python": "3.13.0"})
    monkeypatch.setattr(doctor, "check_tor", lambda: {"installed": True, "running": True, "port": 1080})
    monkeypatch.setattr(doctor, "check_browser", lambda: {"running": True})
    ready = doctor.setup_check()
    assert ready["readiness"] == "ready" and ready["issues"] == []

    monkeypatch.setattr(doctor, "check_browser", lambda: {"running": False})
    assert doctor.setup_check()["readiness"] == "partial"

    monkeypatch.setattr(doctor, "check_tor", lambda: {"installed": False, "running": False, "port": 1080})
    limited = doctor.setup_check()
    assert limited["readiness"] == "limited" and len(limited["issues"]) == 2
