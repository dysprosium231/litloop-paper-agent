"""Tests for API keys falling back to environment variables in config."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from litloop import config


@pytest.fixture(autouse=True)
def isolated_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "config.json")
    monkeypatch.delenv("OPENALEX_API_KEY", raising=False)
    monkeypatch.delenv("ELSEVIER_API_KEY", raising=False)
    return tmp_path


def test_env_used_when_config_empty(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("OPENALEX_API_KEY", "env-openalex-secret")
    monkeypatch.setenv("ELSEVIER_API_KEY", "env-elsevier-secret")

    cfg = config.load_config()

    assert cfg["openalex_api_key"] == "env-openalex-secret"
    assert cfg["elsevier_api_key"] == "env-elsevier-secret"


def test_config_wins_when_both_set(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("OPENALEX_API_KEY", "env-openalex-secret")
    monkeypatch.setenv("ELSEVIER_API_KEY", "env-elsevier-secret")

    config_data = {
        "openalex_api_key": "cfg-openalex-key",
        "elsevier_api_key": "cfg-elsevier-key",
    }
    (tmp_path / "config.json").write_text(json.dumps(config_data), encoding="utf-8")

    cfg = config.load_config()

    assert cfg["openalex_api_key"] == "cfg-openalex-key"
    assert cfg["elsevier_api_key"] == "cfg-elsevier-key"


def test_empty_string_in_config_falls_back_to_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("OPENALEX_API_KEY", "env-openalex-secret")
    monkeypatch.setenv("ELSEVIER_API_KEY", "env-elsevier-secret")

    config_data = {
        "openalex_api_key": "",
        "elsevier_api_key": "",
    }
    (tmp_path / "config.json").write_text(json.dumps(config_data), encoding="utf-8")

    cfg = config.load_config()

    assert cfg["openalex_api_key"] == "env-openalex-secret"
    assert cfg["elsevier_api_key"] == "env-elsevier-secret"


def test_keys_from_the_environment_are_not_written_to_disk(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("OPENALEX_API_KEY", "env-openalex-secret")
    cfg = config.load_config()
    cfg["connect_timeout"] = 7
    config.save_config(cfg)
    saved = json.loads((tmp_path / "config.json").read_text(encoding="utf-8"))
    assert saved["openalex_api_key"] == "" and saved["connect_timeout"] == 7
    assert config.load_config()["openalex_api_key"] == "env-openalex-secret"
