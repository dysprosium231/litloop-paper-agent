"""Tests for lazy package imports in litloop and build_release.prune()."""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load_build_release():
    spec = importlib.util.spec_from_file_location("build_release", ROOT / "scripts" / "build_release.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_desktop_server_import_skips_heavy_submodules():
    code = (
        "import sys, litloop.desktop.server; "
        "assert 'litloop.sources' not in sys.modules, 'litloop.sources imported eagerly'; "
        "assert 'litloop.download.pdf' not in sys.modules, 'litloop.download.pdf imported eagerly'"
    )
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert proc.returncode == 0, f"stderr: {proc.stderr}\nstdout: {proc.stdout}"


def test_lazy_imports_and_public_api():
    code = (
        "import litloop; "
        "from litloop import batch_download, search_papers, load_config, update_config, get_config_safe, __version__; "
        "assert callable(batch_download) and callable(search_papers); "
        "assert callable(load_config) and callable(update_config) and callable(get_config_safe); "
        "assert isinstance(__version__, str) and __version__; "
        "assert litloop.batch_download is batch_download; "
        "assert litloop.search_papers is search_papers; "
        "d = dir(litloop); "
        "assert 'batch_download' in d and 'search_papers' in d; "
        "assert set(litloop.__all__).issubset(set(d))"
    )
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert proc.returncode == 0, f"stderr: {proc.stderr}\nstdout: {proc.stdout}"

    import litloop

    with pytest.raises(AttributeError):
        _ = litloop.non_existent_attribute_for_test


def test_prune_removes_unneeded_files_and_empty_dirs(tmp_path: Path, capsys):
    build_release = _load_build_release()
    site = tmp_path / "site-packages"
    site.mkdir()

    removed_paths = [
        site / "pkg" / "__pycache__" / "mod.cpython-314.pyc",
        site / "pkg" / "mod.pyc",
        site / "pkg" / "mod.pyi",
        site / "pkg" / "include" / "header.h",
        site / "pkg" / "docs.chm",
        site / "pkg" / "native.pdb",
        site / "pkg" / "native.lib",
        site / "pkg" / "native.a",
        site / "pkg" / "ext.c",
        site / "pkg" / "ext.cpp",
        site / "pkg" / "ext.pxd",
        site / "pkg" / "ext.pyx",
        site / "pywin32" / "Pythonwin" / "win.exe",
        site / "pkg" / "Demos" / "demo1.py",
        site / "pkg" / "examples" / "ex1.py",
        site / "pkg" / "tests" / "test_pkg.py",
        site / "pkg" / "test" / "helper.py",
    ]
    kept_paths = [
        site / "pkg" / "__init__.py",
        site / "pkg" / "core.py",
        site / "pkg" / "_speed.pyd",
        site / "pkg" / "libfoo.dll",
    ]

    for p in removed_paths + kept_paths:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"x" * 1024)

    build_release.prune(site)
    captured = capsys.readouterr().out

    for p in removed_paths:
        assert not p.exists(), f"expected pruned: {p}"
    for p in kept_paths:
        assert p.is_file(), f"expected kept: {p}"

    for empty_dir in [
        site / "pkg" / "__pycache__",
        site / "pkg" / "include",
        site / "pywin32" / "Pythonwin",
        site / "pywin32",
        site / "pkg" / "Demos",
        site / "pkg" / "examples",
        site / "pkg" / "tests",
        site / "pkg" / "test",
    ]:
        assert not empty_dir.exists(), f"expected empty dir removed: {empty_dir}"

    assert "4 files" in captured
    assert "MB" in captured
