"""Tests for rename formats, configuration, and app settings."""

from __future__ import annotations

import pytest
from pathlib import Path

from litloop import app_settings, config
from litloop.rename import (
    DEFAULT_FORMAT,
    FORMATS,
    _full_title,
    generate_filename,
    rename_format,
    rename_pdf,
)

SAMPLE_META = {
    "author": [{"family": "Jiang"}],
    "year": 2024,
    "title": ["Lightweight, thermally insulating SiBCN aerogels: a study?"],
    "DOI": "10.1016/j.cej.2024.157656",
}


def test_formats_and_default():
    expected_keys = {
        "author_year_title_doi",
        "author_year_title",
        "zotero",
        "year_author_title",
        "title",
        "doi",
        "off",
    }
    assert set(FORMATS.keys()) == expected_keys
    assert DEFAULT_FORMAT == "author_year_title_doi"
    for k, v in FORMATS.items():
        assert isinstance(v, str) and len(v) > 0


def test_rename_format_resolution():
    assert rename_format({"auto_rename": False}) == "off"
    assert rename_format({"auto_rename": False, "rename_format": "zotero"}) == "off"
    assert rename_format({}) == DEFAULT_FORMAT
    assert rename_format({"rename_format": ""}) == DEFAULT_FORMAT
    assert rename_format({"rename_format": "unknown_format"}) == DEFAULT_FORMAT
    assert rename_format({"rename_format": "zotero"}) == "zotero"
    assert rename_format({"rename_format": "doi"}) == "doi"
    assert rename_format({"rename_format": "off"}) == "off"
    assert rename_format({"auto_rename": True, "rename_format": "author_year_title"}) == "author_year_title"


def test_sample_author_year_title_doi_keeps_legacy_name():
    expected = "Jiang2024_Lightweight_Thermally_Insulating_SiBCN_10.1016_j.cej.2024.157656"
    assert generate_filename(SAMPLE_META, fmt="author_year_title_doi") == expected
    assert generate_filename(SAMPLE_META) == expected


def test_sample_author_year_title_has_no_doi():
    name = generate_filename(SAMPLE_META, fmt="author_year_title")
    assert name == "Jiang2024_Lightweight_Thermally_Insulating_SiBCN"
    assert "10.1016" not in name


def test_sample_zotero_format():
    name = generate_filename(SAMPLE_META, fmt="zotero")
    assert name == "Jiang - 2024 - Lightweight, thermally insulating SiBCN aerogels a study"


def test_sample_year_author_title():
    name = generate_filename(SAMPLE_META, fmt="year_author_title")
    assert name == "2024_Jiang_Lightweight_Thermally_Insulating_SiBCN"


def test_sample_title_format():
    name = generate_filename(SAMPLE_META, fmt="title")
    assert name == "Lightweight, thermally insulating SiBCN aerogels a study"


def test_sample_doi_format():
    name = generate_filename(SAMPLE_META, fmt="doi")
    assert name == "10.1016_j.cej.2024.157656"


def test_zotero_removes_windows_forbidden_and_control_chars():
    meta = {
        "author": [{"family": "Jiang"}],
        "year": 2024,
        "title": "A <tag> B : C \" D / E \\ F | G ? H * I \x00 J \x1f K",
    }
    name = generate_filename(meta, fmt="zotero")
    assert name == "Jiang - 2024 - A B C D E F G H I J K"
    for forbidden in '<>:"/\\|?*\x00\x1f':
        assert forbidden not in name


def test_zotero_preserves_non_ascii_author_and_title():
    meta_cn = {"author": [{"family": "李"}], "year": 2024, "title": "超轻 SiBCN 气凝胶"}
    assert generate_filename(meta_cn, fmt="zotero") == "李 - 2024 - 超轻 SiBCN 气凝胶"

    meta_de = {"author": [{"family": "Müller"}], "year": 2024, "title": "Über Porosität"}
    assert generate_filename(meta_de, fmt="zotero") == "Müller - 2024 - Über Porosität"


def test_title_truncates_at_word_boundary_under_120_chars():
    long_title = "Lightweight porous ceramic aerogels with high mechanical strength and thermal stability " * 3
    meta = {"title": long_title, "author": [{"family": "Jiang"}], "year": 2024}

    full_title = _full_title(meta)
    assert len(full_title) <= 120
    assert not full_title.endswith(" ")
    # Ensure it didn't cut off in the middle of a word
    words = full_title.split()
    assert words[-1] in ("Lightweight", "porous", "ceramic", "aerogels", "with", "high",
                         "mechanical", "strength", "and", "thermal", "stability")

    title_name = generate_filename(meta, fmt="title")
    assert len(title_name) <= 120
    assert title_name == full_title

    zotero_name = generate_filename(meta, fmt="zotero")
    assert zotero_name == f"Jiang - 2024 - {full_title}"


def test_doi_is_safe_filename():
    meta = {"doi": "10.1016/j.cej.2024.157656"}
    assert generate_filename(meta, fmt="doi") == "10.1016_j.cej.2024.157656"


def test_formats_fallback_to_doi_when_metadata_missing():
    meta = {"doi": "10.1016/j.cej.2024.157656"}
    safe_doi = "10.1016_j.cej.2024.157656"

    assert generate_filename(meta, fmt="doi") == safe_doi
    assert generate_filename(meta, fmt="title") == safe_doi
    assert generate_filename(meta, fmt="zotero") == safe_doi
    assert generate_filename(meta, fmt="author_year_title") == safe_doi
    assert generate_filename(meta, fmt="year_author_title") == safe_doi
    assert generate_filename(meta, fmt="off") == safe_doi

    # author_year_title_doi intentionally keeps legacy behavior of returning empty string when parts is empty
    assert generate_filename(meta, fmt="author_year_title_doi") == ""

    # When DOI is also missing, all return empty string
    empty_meta = {}
    assert generate_filename(empty_meta, fmt="zotero") == ""
    assert generate_filename(empty_meta, fmt="author_year_title") == ""
    assert generate_filename(empty_meta, fmt="doi") == ""


def test_rename_pdf_with_fmt_off_returns_none_and_leaves_file(tmp_path):
    pdf = tmp_path / "sample.pdf"
    content = b"%PDF-1.4 dummy paper content"
    pdf.write_bytes(content)

    res = rename_pdf(pdf, SAMPLE_META, fmt="off")
    assert res is None
    assert pdf.exists()
    assert pdf.read_bytes() == content


def test_rename_pdf_with_valid_fmt_renames_file(tmp_path):
    pdf = tmp_path / "sample.pdf"
    content = b"%PDF-1.4 dummy paper content"
    pdf.write_bytes(content)

    target = rename_pdf(pdf, SAMPLE_META, fmt="zotero")
    assert target is not None
    assert target.exists()
    assert target.name == "Jiang - 2024 - Lightweight, thermally insulating SiBCN aerogels a study.pdf"
    assert not pdf.exists()
    assert target.read_bytes() == content


def test_rename_pdf_nonexistent_file_returns_none(tmp_path):
    assert rename_pdf(tmp_path / "not_found.pdf", SAMPLE_META) is None


@pytest.fixture
def config_home(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "config.json")
    for env in config.ENV_KEYS.values():
        monkeypatch.delenv(env, raising=False)
    return tmp_path


def test_app_settings_shows_rename_format_select(config_home):
    all_fields = {f["key"]: f for g in app_settings.settings() for f in g["fields"]}
    assert "rename_format" in all_fields
    field = all_fields["rename_format"]
    assert field["type"] == "select"
    assert field["value"] == "author_year_title_doi"
    assert field["options"] == [[k, v] for k, v in FORMATS.items()]


def test_app_settings_reflects_rename_format_when_auto_rename_false(config_home):
    config.update_config("auto_rename", "false")
    all_fields = {f["key"]: f for g in app_settings.settings() for f in g["fields"]}
    assert all_fields["rename_format"]["value"] == "off"


def test_app_settings_update_rename_format_saves_and_enables_auto_rename(config_home):
    config.update_config("auto_rename", "false")
    assert config.load_config()["auto_rename"] is False

    updated = app_settings.update("rename_format", "zotero")
    assert updated["value"] == "zotero"

    cfg = config.load_config()
    assert cfg["rename_format"] == "zotero"
    assert cfg["auto_rename"] is True


def test_app_settings_update_invalid_rename_format_raises_value_error(config_home):
    with pytest.raises(ValueError, match="rename_format must be one of"):
        app_settings.update("rename_format", "invalid_format")
