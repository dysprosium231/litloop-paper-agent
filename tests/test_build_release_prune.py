"""The release runtime leaves out files the app never imports.

Every file in an MSI costs a separate write and an antivirus scan, so stubs,
headers, bundled test suites and byte-code caches are dropped at staging time.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "build_release.py"


def _script():
    spec = importlib.util.spec_from_file_location("build_release", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write(path: Path, text: str = "x") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_prune_drops_stubs_headers_tests_and_caches(tmp_path):
    site = tmp_path / "site-packages"
    keep = [
        _write(site / "numpy" / "__init__.py"),
        _write(site / "numpy" / "core" / "_multiarray_umath.pyd", "binary"),
        _write(site / "litloop" / "cli" / "main.py"),
        _write(site / "mcp" / "server" / "stdio.py"),
    ]
    drop = [
        _write(site / "numpy" / "__init__.pyi"),
        _write(site / "numpy" / "core" / "include" / "numpy" / "arrayobject.h"),
        _write(site / "numpy" / "tests" / "test_core.py"),
        _write(site / "openpyxl" / "worksheet" / "tests" / "test_cell.py"),
        _write(site / "typer" / "__pycache__" / "main.cpython-314.pyc", "binary"),
    ]

    module = _script()
    module.prune(site)

    assert all(path.exists() for path in keep)
    assert not any(path.exists() for path in drop)


def test_prune_keeps_a_module_whose_name_merely_starts_with_test(tmp_path):
    # "testing" is a real package (numpy.testing); only a directory called
    # exactly "test" or "tests" is a bundled test suite.
    site = tmp_path / "site-packages"
    keep = _write(site / "numpy" / "testing" / "__init__.py")
    module = _script()
    module.prune(site)
    assert keep.exists()


def test_prune_is_harmless_on_a_runtime_without_site_packages(tmp_path):
    module = _script()
    module.prune(tmp_path / "nope")
