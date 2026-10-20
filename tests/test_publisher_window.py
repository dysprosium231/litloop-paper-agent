"""Publisher sites open only in their own window, minimized until the user must act (2026-10-20)."""

from __future__ import annotations

import pytest

from litloop import _publisher_strategies_core as core


def test_publisher_site_goes_straight_to_its_window(monkeypatch, tmp_path):
    import litloop.browser_engine as be
    calls = []
    monkeypatch.setattr(be, "is_available", lambda config=None: True)
    monkeypatch.setattr(core, "_visible_browser_download",
                        lambda doi, url, out, config, publisher, wait_for_loading=0: calls.append((url, wait_for_loading)) or {"success": True})
    assert core._browser_download_with_fallback("10.1021/x", "https://doi.org/10.1021/x", tmp_path / "a.pdf", {}, "ACS",
                                                wait_for_loading=10)
    assert calls == [("https://pubs.acs.org/doi/10.1021/x", 10)]  # known landing URL, no doi.org hop
    assert not hasattr(core, "_browser_download")  # the shared-browser first pass is gone


def test_window_error_is_a_failure_with_a_reason(monkeypatch, tmp_path):
    import litloop.browser_engine as be
    monkeypatch.setattr(be, "is_available", lambda config=None: True)

    def boom(*a, **k):
        raise RuntimeError("driver died")
    monkeypatch.setattr(core, "_visible_browser_download", boom)
    assert core._browser_download_with_fallback("10.1/x", "https://x.test/a", tmp_path / "a.pdf", {}, "Wiley") is False
    assert core.get_last_error()[0] == "navigate_failed"


class _Page:
    def __init__(self):
        self.states = []


@pytest.fixture
def window(monkeypatch):
    page = _Page()
    monkeypatch.setattr(core, "_set_window_state", lambda p, s: p.states.append(s) or True)
    core._WINDOW_TLS.page, core._WINDOW_TLS.shown, core._WINDOW_TLS.verify_since = page, False, None
    yield page
    core._WINDOW_TLS.page = None


def test_login_brings_the_window_up_at_once_and_only_once(window):
    core._show_window_for_user("login")
    core._show_window_for_user("login")
    assert window.states == ["normal"]


def test_a_human_check_gets_a_few_seconds_to_pass_by_itself(window, monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(core.time, "time", lambda: now[0])
    core._show_window_for_user("verify")
    now[0] += core._VERIFY_GRACE_S - 1
    core._show_window_for_user("verify")
    assert window.states == []
    now[0] += 2
    core._show_window_for_user("verify")
    assert window.states == ["normal"]


def test_no_window_no_change():
    core._WINDOW_TLS.page = None
    core._show_window_for_user("login")  # nothing to show, nothing raised


@pytest.fixture(scope="module")
def page():
    from litloop import browser_backend as bb
    try:
        b = bb.launch(headless=True)
    except Exception as e:  # no browser on this machine
        pytest.skip(f"no browser: {e}")
    pg = b.new_context().new_page()
    yield pg
    b.close()


def test_publisher_link_rules_skip_supplements(page):
    page.set_content('<a href="https://x.test/doi/pdf/10.1/x_supplement">SI</a>'
                     '<a href="https://x.test/doi/pdf/10.1/x">PDF</a>')
    assert page.evaluate(core._PUBLISHER_PDF_LINK_JS, "10.1/x") == "https://x.test/doi/pdf/10.1/x"


def test_publisher_link_rules_take_wiley_pdfdirect_first(page):
    page.set_content('<a href="https://x.test/doi/pdf/10.1/x">PDF</a><a href="https://x.test/doi/pdfdirect/10.1/x">Direct</a>')
    assert page.evaluate(core._PUBLISHER_PDF_LINK_JS, "10.1/x") == "https://x.test/doi/pdfdirect/10.1/x"
