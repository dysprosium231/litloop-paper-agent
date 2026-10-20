r"""Path settings are checked before they are saved (2026-10-19: ":\Users\..." was "saved")."""

from __future__ import annotations

import pytest

from litloop import app_settings


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
