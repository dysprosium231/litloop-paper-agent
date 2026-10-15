"""The private Chrome copy follows the installed Chrome."""

from __future__ import annotations

import shutil

import pytest

from litloop import chrome_copy


def _install(root, version, *, alias=False):
    root.mkdir(parents=True, exist_ok=True)
    (root / "chrome.exe").write_bytes(b"exe " + version.encode())
    (root / "chrome_proxy.exe").write_bytes(b"proxy")
    (root / version).mkdir()
    (root / version / "chrome.dll").write_bytes(b"dll " + version.encode())
    if alias:
        (root / "litloop-chrome.exe").write_bytes(b"alias")
    return root


@pytest.fixture
def setup(tmp_path, monkeypatch):
    system = _install(tmp_path / "Program Files" / "Google" / "Chrome" / "Application", "155.0.8059.41")
    (system / "SetupMetrics").mkdir()
    copy = _install(tmp_path / "home" / "chrome-real", "155.0.8059.39", alias=True)
    monkeypatch.setattr(chrome_copy, "system_chrome_dirs", lambda: [system])
    return system, copy


def test_outdated_copy_is_replaced_with_the_system_version(setup):
    system, copy = setup
    out = chrome_copy.refresh_chrome_copy(copy / "chrome.exe", system=(system, (155, 0, 8059, 41)), running=False)
    assert out == "updated"
    assert chrome_copy.chrome_version(copy) == (155, 0, 8059, 41)
    assert not (copy / "155.0.8059.39").exists() and (copy / "chrome.exe").read_bytes() == b"exe 155.0.8059.41"
    assert not (copy / "SetupMetrics").exists()  # installer leftovers are not copied
    assert not copy.with_name("chrome-real.old").exists() and not copy.with_name("chrome-real.new").exists()


def test_current_copy_is_left_alone(setup):
    system, copy = setup
    chrome_copy.refresh_chrome_copy(copy / "chrome.exe", system=(system, (155, 0, 8059, 41)), running=False)
    assert chrome_copy.refresh_chrome_copy(copy / "chrome.exe", system=(system, (155, 0, 8059, 41))) == "current"


def test_running_copy_is_not_touched(setup):
    system, copy = setup
    out = chrome_copy.refresh_chrome_copy(copy / "chrome.exe", system=(system, (155, 0, 8059, 41)), running=True)
    assert out == "skipped-running" and chrome_copy.chrome_version(copy) == (155, 0, 8059, 39)


def test_failed_copy_keeps_the_old_browser(setup, monkeypatch):
    system, copy = setup

    def boom(*a, **kw):
        raise OSError("disk full")

    monkeypatch.setattr(shutil, "copytree", boom)
    out = chrome_copy.refresh_chrome_copy(copy / "chrome.exe", system=(system, (155, 0, 8059, 41)), running=False)
    assert out == "failed"
    assert chrome_copy.chrome_version(copy) == (155, 0, 8059, 39) and (copy / "litloop-chrome.exe").exists()
    assert not copy.with_name("chrome-real.new").exists()


def test_system_chrome_and_other_browsers_are_not_copies(setup, tmp_path):
    system, copy = setup
    assert chrome_copy.is_chrome_copy(copy / "chrome.exe")
    assert not chrome_copy.is_chrome_copy(system / "chrome.exe")
    edge = tmp_path / "edge" / "msedge.exe"
    assert not chrome_copy.is_chrome_copy(edge)
    assert chrome_copy.refresh_chrome_copy(system / "chrome.exe", system=(system, (155, 0, 8059, 41))) == "not-a-copy"
