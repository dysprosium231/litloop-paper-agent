"""A check page that cannot run is not waited on."""

from litloop.publishers.strategies import _core as core


def test_dead_end_check_pages():
    assert core._is_challenge_dead_end("<h2>浏览器扩展或网络配置不兼容</h2>")
    assert core._is_challenge_dead_end("Your browser extensions or network settings have blocked the security verification")
    assert not core._is_challenge_dead_end("<title>Just a moment...</title>")  # an ordinary check: may pass
