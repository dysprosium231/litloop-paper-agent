"""CloakBrowser compat shims: cache directory plumbing and the Windows arch patch."""

from __future__ import annotations

import types

import pytest

from litloop.browser import cloakbrowser_compat as compat


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    monkeypatch.delenv("CLOAKBROWSER_CACHE_DIR", raising=False)
    monkeypatch.delenv("LITLOOP_CLOAKBROWSER_CACHE_DIR", raising=False)


def test_an_existing_cache_dir_wins_and_is_left_alone(monkeypatch, tmp_path):
    monkeypatch.setenv("CLOAKBROWSER_CACHE_DIR", str(tmp_path / "keep"))
    target = compat.configure_builtin_cloakbrowser(tmp_path / "ignored")
    assert target == tmp_path / "keep"
    assert not (tmp_path / "keep").exists()


def test_the_explicit_cache_dir_is_created_and_exported(monkeypatch, tmp_path):
    target = compat.configure_builtin_cloakbrowser(tmp_path / "cache")
    assert target.is_dir()
    assert target == (tmp_path / "cache").resolve()
    assert __import__("os").environ["CLOAKBROWSER_CACHE_DIR"] == str(target)


def test_the_litloop_env_var_is_the_fallback(monkeypatch, tmp_path):
    monkeypatch.setenv("LITLOOP_CLOAKBROWSER_CACHE_DIR", str(tmp_path / "from-env"))
    target = compat.configure_builtin_cloakbrowser()
    assert target == (tmp_path / "from-env").resolve()
    assert target.is_dir()


def test_creating_the_directory_can_be_skipped(tmp_path):
    target = compat.configure_builtin_cloakbrowser(tmp_path / "later", create_dir=False)
    assert target == (tmp_path / "later").resolve()
    assert not target.exists()


def test_the_platform_patch_leaves_other_systems_alone(monkeypatch):
    monkeypatch.setattr(compat.platform, "system", lambda: "Linux")
    monkeypatch.setattr(compat.platform, "machine", lambda: "")
    assert compat.ensure_cloakbrowser_platform_compatible(types.SimpleNamespace(SUPPORTED_PLATFORMS={})) is False


def test_a_known_machine_needs_no_patch(monkeypatch):
    monkeypatch.setattr(compat.platform, "system", lambda: "Windows")
    monkeypatch.setattr(compat.platform, "machine", lambda: "AMD64")
    config = types.SimpleNamespace(SUPPORTED_PLATFORMS={})
    assert compat.ensure_cloakbrowser_platform_compatible(config) is False


def test_an_empty_machine_on_64_bit_windows_gets_a_patch(monkeypatch):
    monkeypatch.setattr(compat.platform, "system", lambda: "Windows")
    monkeypatch.setattr(compat.platform, "machine", lambda: "")
    monkeypatch.setenv("ProgramFiles(x86)", r"C:\Program Files (x86)")
    config = types.SimpleNamespace(SUPPORTED_PLATFORMS={("Windows", "AMD64"): "windows-x64"})
    assert compat.ensure_cloakbrowser_platform_compatible(config) is True
    assert config.SUPPORTED_PLATFORMS[("Windows", "")] == "windows-x64"
    # Already patched: the second call reports no change.
    assert compat.ensure_cloakbrowser_platform_compatible(config) is False


def test_an_empty_machine_on_32_bit_windows_stays_unsupported(monkeypatch):
    monkeypatch.setattr(compat.platform, "system", lambda: "Windows")
    monkeypatch.setattr(compat.platform, "machine", lambda: "")
    for name in ("ProgramFiles(x86)", "PROCESSOR_ARCHITEW6432"):
        monkeypatch.delenv(name, raising=False)
    config = types.SimpleNamespace(SUPPORTED_PLATFORMS={})
    assert compat.ensure_cloakbrowser_platform_compatible(config) is False
    assert config.SUPPORTED_PLATFORMS == {}


def test_a_module_without_the_lookup_table_is_ignored(monkeypatch):
    monkeypatch.setattr(compat.platform, "system", lambda: "Windows")
    monkeypatch.setattr(compat.platform, "machine", lambda: "")
    monkeypatch.setenv("ProgramFiles(x86)", r"C:\Program Files (x86)")
    assert compat.ensure_cloakbrowser_platform_compatible(types.SimpleNamespace()) is False
