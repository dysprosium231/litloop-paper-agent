"""Tests for login page detection and user waiting heartbeat notifications."""

from types import SimpleNamespace
import pytest

import litloop.sources as sources
from litloop.publishers.strategies._core import _on_login_page, _user_waiting


class FakePage:
    def __init__(self, title="Some article", url="https://www.sciencedirect.com/science/article/pii/X", raises=False):
        self._title = title
        self.url = url
        self.raises = raises

    def title(self):
        if self.raises:
            raise RuntimeError("title query failed")
        return self._title


def test_on_login_page():
    # Fake page whose title() returns "统一身份认证登录" -> True
    login_by_title = FakePage(title="统一身份认证登录", url="https://example.com")
    assert _on_login_page(login_by_title) is True

    # url "https://sso.buaa.edu.cn/login" -> True
    login_by_sso_url = FakePage(title="Some article", url="https://sso.buaa.edu.cn/login")
    assert _on_login_page(login_by_sso_url) is True

    # url "https://ds.carsi.edu.cn/x" -> True
    login_by_carsi_url = FakePage(title="Some article", url="https://ds.carsi.edu.cn/x")
    assert _on_login_page(login_by_carsi_url) is True

    # A plain article (title "Some article", url "https://www.sciencedirect.com/science/article/pii/X") -> False
    article_page = FakePage(title="Some article", url="https://www.sciencedirect.com/science/article/pii/X")
    assert _on_login_page(article_page) is False

    # A fake context object with attribute pages=[article_page, login_page] -> True
    login_page = FakePage(title="统一身份认证登录", url="https://sso.buaa.edu.cn/login")
    fake_context = SimpleNamespace(pages=[article_page, login_page])
    assert _on_login_page(fake_context) is True

    # A page whose title() raises -> skipped (False if no other page)
    failing_page = FakePage(raises=True, url="https://example.com")
    assert _on_login_page(failing_page) is False


def test_user_waiting_calls_note_user_waiting(monkeypatch):
    calls = []
    monkeypatch.setattr(sources, "note_user_waiting", lambda what: calls.append(what))
    monkeypatch.setattr(sources, "_source_may_continue", lambda: True)

    article_page = FakePage(title="Some article", url="https://www.sciencedirect.com/science/article/pii/X")
    login_page = FakePage(title="统一身份认证登录", url="https://sso.buaa.edu.cn/login")

    # _user_waiting("login", where=article_page) does NOT call litloop.sources.note_user_waiting
    _user_waiting("login", where=article_page)
    assert calls == []

    # while where=login_page does call it once with "login"
    _user_waiting("login", where=login_page)
    assert calls == ["login"]


def test_user_waiting_when_paper_settled(monkeypatch):
    # When the paper is settled: monkeypatch litloop.sources._source_may_continue to return False
    # and assert _user_waiting("login", where=article_page) raises litloop.sources.SourceWaived
    monkeypatch.setattr(sources, "_source_may_continue", lambda: False)

    article_page = FakePage(title="Some article", url="https://www.sciencedirect.com/science/article/pii/X")

    with pytest.raises(sources.SourceWaived):
        _user_waiting("login", where=article_page)
