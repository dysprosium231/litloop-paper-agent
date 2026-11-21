"""Tests for scripts/smoke_app.py (path resolution, port parsing, and backend smoke)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "smoke_app.py"
_SPEC = importlib.util.spec_from_file_location("smoke_app", SCRIPT_PATH)
assert _SPEC and _SPEC.loader
smoke_app = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(smoke_app)


def test_resolve_python_for_exe_layout_variants(tmp_path: Path):
    exe = tmp_path / "litloop-app.exe"
    exe.write_bytes(b"")

    with pytest.raises(FileNotFoundError):
        smoke_app.resolve_python_for_exe(exe)

    res_py = tmp_path / "resources" / "python" / "python.exe"
    res_py.parent.mkdir(parents=True)
    res_py.write_bytes(b"")
    assert smoke_app.resolve_python_for_exe(exe) == res_py

    direct_py = tmp_path / "python" / "python.exe"
    direct_py.parent.mkdir(parents=True)
    direct_py.write_bytes(b"")
    assert smoke_app.resolve_python_for_exe(exe) == direct_py


def test_parse_port_line():
    assert smoke_app.parse_port_line('{"litloop_app_server": 1, "port": 51234}\n') == 51234
    assert smoke_app.parse_port_line("INFO: started\n") is None
    assert smoke_app.parse_port_line('{"litloop_app_server": 1, "port": 0}') is None
    assert smoke_app.parse_port_line("{bad json") is None


def test_smoke_backend_starts_and_passes_health_and_sessions():
    info = smoke_app.smoke_backend(Path(sys.executable), timeout=20.0)
    assert 1 <= info["port"] <= 65535
    assert info["version"]
    assert isinstance(info["schema"], int)
