"""egress remembers a publisher's refusal; a check page that cannot run is not waited on."""

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
