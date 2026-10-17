"""Papers from scihub_skip_from_year on are not asked of Sci-Hub."""

from __future__ import annotations

from litloop.sources import _build_free_sources, scihub


def _labels(doi, config):
    return [label for _, label in _build_free_sources(doi, config)]


def test_year_from_doi_and_from_the_list():
    assert scihub.publication_year("10.1016/j.ceramint.2026.06.274") == 2026
    assert scihub.publication_year("10.26599/jac.2025.9221068") == 2025
    assert scihub.publication_year("10.1111/jace.20586") is None
    scihub.note_years({"10.1111/JACE.20586": "2025-03"})
    assert scihub.publication_year("10.1111/jace.20586") == 2025


def test_too_new_follows_the_setting():
    cfg = {"scihub_skip_from_year": "2024"}
    assert scihub.too_new("10.1016/j.ceramint.2026.06.274", cfg)
    assert not scihub.too_new("10.1016/j.jcis.2012.08.004", cfg)
    assert not scihub.too_new("10.1021/ja8058843", cfg)  # no known year: still asked
    assert not scihub.too_new("10.1016/j.ceramint.2026.06.274", {"scihub_skip_from_year": 0})


def test_free_sources_leave_out_sci_hub_for_new_papers():
    cfg = {"scihub_enabled": True, "scihub_skip_from_year": 2024}
    new = _labels("10.1016/j.ceramint.2026.06.274", cfg)
    old = _labels("10.1016/j.jcis.2012.08.004", cfg)
    assert "Sci-Hub" not in new and "SciBban" not in new
    assert "Sci-Hub" in old


def test_try_scihub_returns_at_once_for_new_papers(tmp_path, monkeypatch):
    monkeypatch.setattr(scihub, "_try_scihub_impl", lambda *a, **k: (_ for _ in ()).throw(AssertionError("asked")))
    assert scihub.try_scihub("10.1016/j.ceramint.2026.06.274", tmp_path / "x.pdf",
                             {"scihub_skip_from_year": 2024}) is None
