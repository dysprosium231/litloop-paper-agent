"""First-run guide: shown until finished or skipped, never to someone who already set LitLoop up."""

import pytest

from litloop import app_settings, config


@pytest.fixture
def fresh(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "config.json")
    for env in config.ENV_KEYS.values():
        monkeypatch.delenv(env, raising=False)


def test_a_new_user_gets_the_guide_until_done(fresh):
    o = app_settings.onboarding()
    assert o["done"] is False
    assert [s["group"] for s in o["steps"]] == ["sources", "screening", "downloads", "network", "school", "zotero"]
    assert all(s["group"] in {g["id"] for g in app_settings.GROUPS} for s in o["steps"])
    app_settings.finish_onboarding()
    assert app_settings.onboarding()["done"] is True


@pytest.mark.parametrize("key, value", [("openalex_api_key", "k"), ("elsevier_api_key", "k"), ("email", "me@uni.edu")])
def test_someone_already_set_up_is_left_alone(fresh, key, value):
    config.update_config(key, value)
    assert app_settings.onboarding()["done"] is True
