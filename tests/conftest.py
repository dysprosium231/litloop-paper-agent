import pytest
"""Ensure tests import the repo's own source tree, not an installed copy."""
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
import os

os.environ.setdefault("LITLOOP_PROGRESS_BAR", "0")  # pytest 不得弹出 GUI


@pytest.fixture(autouse=True)
def _isolated_api_usage(tmp_path, monkeypatch):
    """Keep request counting (litloop.quota) out of the real ~/.litloop/api_usage.json."""
    from litloop import quota
    monkeypatch.setattr(quota, "_path", lambda: tmp_path / "api_usage.json")


@pytest.fixture(autouse=True)
def _isolated_config(tmp_path, monkeypatch):
    """Tests never read the user's real ~/.litloop/config.json: with it, a test
    could open the school's WebVPN or CARSI in a real browser (2026-10-17 one
    did, for a made-up DOI). A test that needs settings writes them here."""
    from litloop import config
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "litloop-config.json")
