"""A publisher refused this exit: its site is skipped for the school route; a dead-end check is not waited on."""

from pathlib import Path

from litloop import _publisher_strategies_core as core
from litloop import egress


def test_refusal_is_remembered_for_an_hour(monkeypatch):
    egress.reset()
    assert not egress.refused()
    egress.mark_refused()
    assert egress.refused()
    monkeypatch.setattr(egress.time, "time", lambda: egress._REFUSED_AT + egress.TTL_SECONDS + 1)
    assert not egress.refused()
    egress.reset()


def test_dead_end_check_pages():
    assert core._is_challenge_dead_end("<h2>浏览器扩展或网络配置不兼容</h2>")
    assert core._is_challenge_dead_end("Your browser extensions or network settings have blocked the security verification")
    assert not core._is_challenge_dead_end("<title>Just a moment...</title>")  # an ordinary check: may pass


def test_publisher_site_skipped_when_the_exit_is_refused(monkeypatch, tmp_path: Path):
    from litloop import browser_engine
    egress.mark_refused()
    monkeypatch.setattr(browser_engine, "is_available", lambda cfg: True)
    opened = []
    monkeypatch.setattr(browser_engine, "create_tab", lambda *a, **k: opened.append(1) or None)
    monkeypatch.setattr(core, "_has_publisher_cookies", lambda cfg: False)
    try:
        r = core._browser_download("10.1111/x.1", "https://onlinelibrary.wiley.com/doi/10.1111/x.1",
                                   tmp_path / "x.pdf", {"vpnsci_enabled": True}, "Wiley")
    finally:
        egress.reset()
    assert r is False and not opened
