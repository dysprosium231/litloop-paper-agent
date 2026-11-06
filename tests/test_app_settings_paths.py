r"""Path settings are checked before they are saved (2026-10-19: ":\Users\..." was "saved")."""

from __future__ import annotations

import pytest

from litloop.desktop import settings as app_settings


@pytest.mark.parametrize("bad", [r":\Users\Echo\.litloop\papers", r"Users\Echo\papers", "papers", r"Q:\no-such-drive\papers"])
def test_a_bad_save_folder_is_refused(bad, tmp_path):
    import os
    if bad.startswith("Q:") and os.path.exists("Q:\\"):
        pytest.skip("this machine has a Q: drive")
    with pytest.raises(ValueError):
        app_settings.update("output_dir", bad)


def test_a_full_path_is_saved_even_if_the_folder_is_new(tmp_path):
    target = tmp_path / "new-folder"
    app_settings.update("output_dir", str(target))
    from litloop.config import load_config
    assert load_config()["output_dir"] == str(target)


def test_a_file_is_not_a_save_folder(tmp_path):
    f = tmp_path / "a.txt"
    f.write_text("x", encoding="utf-8")
    with pytest.raises(ValueError):
        app_settings.update("output_dir", str(f))


def test_a_rate_limited_openalex_says_what_to_do():
    """Without a key the budget is shared per IP, so "no connection" would be misleading."""
    why = app_settings._openalex_failure("HTTP 429: rate limit exceeded", False)
    assert "匿名额度" in why and "Key" in why
    assert "学校认证" not in why  # that line is for publishers, not for OpenAlex


def test_a_rate_limited_openalex_with_a_key_only_asks_to_wait():
    why = app_settings._openalex_failure("HTTP 403: forbidden", True)
    assert "限流" in why and "Key" not in why
