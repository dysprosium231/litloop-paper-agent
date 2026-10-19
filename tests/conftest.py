import pytest
"""Ensure tests import the repo's own source tree, not an installed copy."""
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
import os


# Tests never touch the user's ~/.litloop: the desktop app reads its progress
# file, download jobs and settings from there while it runs (2026-10-17 a test
# run's "please pass the check" notice showed up in the user's live download).
# Set before litloop is imported: config.DATA_DIR is read once, at import.
import tempfile

os.environ["LITLOOP_DATA_DIR"] = tempfile.mkdtemp(prefix="litloop-tests-")


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
