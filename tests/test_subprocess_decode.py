"""A subprocess whose output fails to decode leaves stdout None; callers must not crash on it."""

import subprocess

from litloop.browser import backend, chrome_copy


def _no_output(*args, **kwargs):
    return subprocess.CompletedProcess(args, 0, stdout=None, stderr=None)


def test_copy_running_with_undecodable_output(monkeypatch):
    monkeypatch.setattr(chrome_copy.subprocess, "run", _no_output)
    assert chrome_copy._copy_running() is False


def test_versions_with_undecodable_output(monkeypatch):
    monkeypatch.setattr(backend.subprocess, "run", _no_output)
    assert backend._probe_windows_versions() == {}
