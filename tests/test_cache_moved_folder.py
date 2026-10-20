"""A paper cached in the old save folder is copied to the new one, not called done
where the user cannot find it (2026-10-19: the save location was changed)."""

from __future__ import annotations

import json

from litloop import sources
from litloop.cache import cache_get, cache_set


def _setup(tmp_path, monkeypatch):
    old, new = tmp_path / "old", tmp_path / "new"
    old.mkdir()
    pdf = old / "Smith2024_Paper_10.1_x.pdf"
    pdf.write_bytes(b"%PDF-1.4 " + b"x" * 20000)
    config = {"cache_dir": str(tmp_path / "cache"), "output_dir": str(new), "cache_ttl_hours": 168}
    monkeypatch.setattr(sources, "load_config", lambda: dict(config))
    cache_set("10.1/x", {"success": True, "doi": "10.1/x", "file": str(pdf), "source": "Wiley"}, config)
    return old, new, pdf, config


def test_cached_paper_from_the_old_folder_is_copied_into_the_new_one(tmp_path, monkeypatch):
    old, new, pdf, config = _setup(tmp_path, monkeypatch)
    r = sources._download_impl("10.1/x", output_dir=new)
    copied = new / pdf.name
    assert r["success"] and r["cached"] and r["file"] == str(copied)
    assert copied.read_bytes() == pdf.read_bytes() and pdf.exists()  # copied, the old one kept
    assert json.loads((new / ".doi_index.json").read_text(encoding="utf-8"))["10.1/x"]["file"] == str(copied)
    assert cache_get("10.1/x", config)["file"] == str(copied)


def test_cached_paper_in_the_same_folder_is_returned_as_is(tmp_path, monkeypatch):
    old, new, pdf, config = _setup(tmp_path, monkeypatch)
    r = sources._download_impl("10.1/x", output_dir=old)
    assert r["success"] and r["file"] == str(pdf)
    assert not (old / ".doi_index.json").exists()
