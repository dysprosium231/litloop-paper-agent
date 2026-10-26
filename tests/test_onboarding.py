"""First-run guide: shown until finished or skipped, never to someone who already set LitLoop up."""

import pytest

from litloop import config
from litloop.desktop import settings as app_settings


@pytest.fixture
def fresh(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "config.json")
    for env in config.ENV_KEYS.values():
        monkeypatch.delenv(env, raising=False)


def test_a_new_user_gets_the_guide_until_done(fresh):
    o = app_settings.onboarding()
    assert o["done"] is False
    assert [s["group"] for s in o["steps"]] == ["sources", "screening", "downloads", "network", "school", "components", "zotero"]
    assert all(s["group"] in {g["id"] for g in app_settings.GROUPS} for s in o["steps"])
    app_settings.finish_onboarding()
    assert app_settings.onboarding()["done"] is True


@pytest.mark.parametrize("key, value", [("openalex_api_key", "k"), ("elsevier_api_key", "k"), ("email", "me@uni.edu")])
def test_someone_already_set_up_is_left_alone(fresh, key, value):
    config.update_config(key, value)
    assert app_settings.onboarding()["done"] is True


def test_optional_components_step_says_what_a_missing_piece_costs(monkeypatch):
    """The guide's 可选组件 step: nothing to set, only what is installed."""
    import shutil

    from litloop import deps
    from litloop.desktop import settings as app_settings
    from litloop.browser import backend as browser_backend
    from litloop.tor import embedded as embedded_tor
    assert "components" in [s["group"] for s in app_settings.STEPS]
    monkeypatch.setattr(browser_backend, "is_available", lambda name=None: True)
    monkeypatch.setattr(browser_backend, "browser_info", lambda cfg=None: {"binary": "channel=chrome", "version": ""})
    monkeypatch.setattr(deps, "check_dependency", lambda m: m == "socks")
    monkeypatch.setattr(embedded_tor, "is_tor_installed", lambda cfg: False)
    monkeypatch.setattr(shutil, "which", lambda cmd: None)
    rows = {r["name"]: r for r in app_settings.run_test("components")["results"]}
    assert rows["浏览器组件"]["ok"] is False and "找不到 Chrome" in rows["浏览器组件"]["detail"]
    assert rows["WebVPN 加密"]["ok"] is False and "pycryptodome" in rows["WebVPN 加密"]["detail"]
    assert rows["SOCKS 代理"]["ok"] is True
    assert rows["Tor"]["ok"] is None  # optional: not a failure
    assert rows["Agent 命令"]["ok"] is None and "找不到 claude" in rows["Agent 命令"]["detail"]
