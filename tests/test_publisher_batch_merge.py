"""Fixes that lived only in institutional/publisher_batch.py, now in the one module."""

from __future__ import annotations

from types import SimpleNamespace

from litloop import publisher_batch as pb
from litloop.publisher_profiles import get_publisher_profile


def _downloader(profile="wiley"):
    d = pb.PublisherBatchDownloader.__new__(pb.PublisherBatchDownloader)  # no browser, no config
    d.profile = get_publisher_profile(profile)
    d.institution_query = "Beihang"
    return d


def test_any_schools_sign_in_page_is_the_users_to_fill():
    d = _downloader()
    for url in ("https://sso.buaa.edu.cn/login", "https://idp.tsinghua.edu.cn/idp", "https://cas.example.edu/x"):
        assert d._is_human_login_page(SimpleNamespace(url=url)), url
    for url in ("https://ds.carsi.edu.cn/", "https://login.openathens.net/", "https://onlinelibrary.wiley.com/"):
        assert not d._is_human_login_page(SimpleNamespace(url=url)), url


def test_back_on_the_article_after_the_school_sign_in_counts_as_not_entitled(monkeypatch):
    d = _downloader("wiley")
    d.login_timeout_sec = 30
    result = pb.DownloadResult(doi="10.1002/x", status="pending",
                               article_url="https://onlinelibrary.wiley.com/doi/10.1002/x")
    pages = iter(["https://sso.buaa.edu.cn/login", "https://onlinelibrary.wiley.com/doi/10.1002/x"])
    page = SimpleNamespace(url="https://onlinelibrary.wiley.com/doi/10.1002/x")
    beats = []
    for name in ("_dismiss_cookie_banners", "_click_sso_entry", "_click_openathens_entry", "_select_institution",
                 "_click_optional_continue"):
        monkeypatch.setattr(d, name, lambda *a, **k: False)
    monkeypatch.setattr(pb.time, "sleep", lambda s: None)
    monkeypatch.setattr(pb, "_user_waiting", lambda: beats.append(1))
    monkeypatch.setattr(d, "_title", lambda p: "")
    monkeypatch.setattr(d, "_is_challenge_page", lambda p: False)

    def human(p):
        p.url = next(pages, p.url)
        return "buaa" in p.url

    monkeypatch.setattr(d, "_is_human_login_page", human)
    monkeypatch.setattr(d, "_return_to_record_article_if_needed", lambda p, r, doi, after_idp=False: after_idp)
    monkeypatch.setattr(d, "_looks_logged_out", lambda p: True)
    assert d._complete_login_from_current_page(page, result) is False
    assert any(e["state"] == "not_entitled_after_login" for e in result.events)
    assert beats, "the login wait reports that the user is busy"


def test_optica_profile_is_in_the_one_registry():
    from litloop.publisher_profiles import infer_publisher_profile
    assert infer_publisher_profile("10.1364/ol.47.013297").name == "Optica"
