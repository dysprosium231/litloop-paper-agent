"""Tests for the components the installer leaves out (the layout model)."""

from __future__ import annotations

import subprocess
import sys

import pytest

from litloop import optional


@pytest.fixture(autouse=True)
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("LITLOOP_DATA_DIR", str(tmp_path))
    return tmp_path


def test_packages_dir_follows_the_data_dir(data_dir):
    assert optional.packages_dir() == data_dir / "packages"


def test_ensure_on_path_puts_the_packages_dir_first(data_dir, monkeypatch):
    (data_dir / "packages").mkdir()
    path = list(sys.path)
    monkeypatch.setattr(optional.sys, "path", path)
    optional.ensure_on_path()
    assert optional.sys.path[0] == str(data_dir / "packages")
    optional.ensure_on_path()  # idempotent
    assert optional.sys.path.count(str(data_dir / "packages")) == 1


def test_a_missing_folder_is_not_added(tmp_path, monkeypatch):
    monkeypatch.setenv("LITLOOP_DATA_DIR", str(tmp_path / "nothing-here"))
    before = list(sys.path)
    monkeypatch.setattr(optional.sys, "path", list(before))
    optional.ensure_on_path()
    assert optional.sys.path == before


def test_unknown_component_is_rejected():
    with pytest.raises(KeyError):
        optional.install("nope")


def test_without_uv_it_says_what_is_missing(monkeypatch):
    monkeypatch.setattr(optional, "uv_command", lambda: None)
    out = optional.install("layout")
    assert out["ok"] is False and "uv" in out["log"]


def test_install_runs_uv_into_the_packages_dir(data_dir, monkeypatch):
    monkeypatch.setattr(optional, "uv_command", lambda: "C:/tools/uv.exe")
    monkeypatch.setattr(optional, "installed", lambda key: True)  # pretend uv delivered it
    seen: dict[str, object] = {}

    def fake_run(cmd, **kwargs):
        seen["cmd"] = cmd
        seen["env"] = kwargs.get("env") or {}
        return subprocess.CompletedProcess(cmd, 0, "installed", "")

    monkeypatch.setattr(optional.subprocess, "run", fake_run)
    out = optional.install("layout", index_url="https://mirror.example/simple")
    cmd = seen["cmd"]
    assert cmd[:5] == ["C:/tools/uv.exe", "pip", "install", "--python", sys.executable]
    assert "--target" in cmd and str(data_dir / "packages") in cmd
    assert "onnxruntime" in cmd and "pymupdf-layout" in cmd
    assert (seen["env"])["UV_INDEX_URL"] == "https://mirror.example/simple"
    assert out["log"] == "installed"
    assert out["ok"] is True


def test_a_failing_uv_reports_its_output(data_dir, monkeypatch):
    monkeypatch.setattr(optional, "uv_command", lambda: "C:/tools/uv.exe")
    monkeypatch.setattr(optional.subprocess, "run", lambda cmd, **kw: subprocess.CompletedProcess(
        cmd, 1, "", "error: no such package"))
    out = optional.install("layout")
    assert out["ok"] is False and "no such package" in out["log"]
