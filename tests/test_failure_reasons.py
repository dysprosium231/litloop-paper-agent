"""A failed paper says which ways were tried and the most telling reason."""

from __future__ import annotations

from litloop import error_text


def _r(**kw):
    return {"success": False, **kw}


def test_known_own_reason_wins():
    r = _r(error="skipped by user", error_type="skipped",
           attempts=[{"source": "WebVPN", "route": "school", "seconds": 240, "ok": False,
                      "error_type": "timeout", "reason": "timed out"}])
    assert error_text.pick_failure(r) == "skipped by user"


def test_most_telling_attempt_wins():
    r = _r(error="All sources failed", attempts=[
        {"source": "WebVPN", "route": "school", "seconds": 240, "ok": False, "error_type": "timeout", "reason": "timed out"},
        {"source": "SpringerBrowser", "route": "free", "seconds": 170, "ok": False, "error_type": "not_entitled",
         "reason": "NOT_ENTITLED"},
    ])
    assert error_text.pick_failure(r) == "NOT_ENTITLED"
    assert "学校未订阅" in error_text.reason(error_text.pick_failure(r))


def test_success_has_no_failure():
    assert error_text.pick_failure({"success": True}) is None


def test_steps_for_app_drops_quick_api_misses():
    steps = error_text.steps_for_app([
        {"source": "Crossref", "route": "free", "seconds": 0.2, "ok": False},
        {"source": "ElsevierBrowser", "route": "free", "seconds": 38.0, "ok": False, "reason": "NOT_ENTITLED"},
        {"source": "WebVPN", "route": "school", "seconds": 0.4, "ok": False},
        {"source": "CARSI", "route": "school", "seconds": 26.7, "ok": True},
    ])
    assert [s["source"] for s in steps] == ["ElsevierBrowser", "WebVPN", "CARSI"]
    assert steps[0]["why"] and steps[2]["why"] is None
    assert error_text.steps_for_app([{"source": "Crossref", "route": "free", "seconds": 0.1, "ok": False}]) is None


import pytest  # noqa: E402

# Raw reasons the download engine really writes (fail(...) calls, the final
# default, browser error types) -> what the app must show. None of them may come
# out as the generic "unexpected error".
@pytest.mark.parametrize("raw, shown", [
    ("no PDF found", "所有来源都没有找到 PDF"),
    ("paywall — requires institutional access", "需要订阅才能下载"),
    ("Cloudflare anti-bot protection blocked access", "人机验证"),
    ("Cloudflare challenge inside WebVPN", "人机验证"),
    ("no PDF link on the publisher page", "没有 PDF 链接"),
    ("school login not completed", "学校登录没有完成"),
    ("could not open the publisher page", "打不开出版商网页"),
    ("browser not available", "浏览器无法启动"),
    ("download already in progress for this identifier", "另一个任务"),
    ("PDF not found after batch download", "所有来源都没有找到 PDF"),
    ("arXiv PDF not available", "arXiv"),
    ("Invalid DOI: bad prefix", "无法识别的 DOI"),
    ("the school does not subscribe: logged into the publisher with the school's identity, still no full text", "学校没有订阅"),
])
def test_real_failure_strings_have_a_reason(raw, shown):
    assert shown in error_text.reason(raw)


@pytest.mark.parametrize("etype", ["not_entitled", "login_abandoned", "cloudflare_blocked", "navigate_failed",
                                   "paywall", "no_pdf_found", "browser_unavailable"])
def test_every_browser_error_type_has_a_reason(etype):
    assert error_text._match(error_text.raw_for(etype)) is not None


def test_unknown_reason_uses_the_generic_sentence():
    # docs/UI_COPY.md 7: unmatched reasons get the generic sentence, the raw text the tooltip.
    assert error_text.reason("something new went wrong") == "出现意外错误"


def test_a_browser_step_without_a_result_keeps_its_reason():
    from litloop.sources.attempt import DownloadAttempt
    att = DownloadAttempt(key="10.1/x", gen=0)
    att.add_step("free", "ElsevierBrowser", 30.0, None, error=("paywall", "login_required"))
    att.add_step("free", "Crossref", 0.2, None)
    assert att.steps[0]["error_type"] == "paywall" and "paywall" in att.steps[0]["reason"]
    assert "reason" not in att.steps[1]
    assert att.last_error == ("paywall", "login_required")
    r = {"success": False, "error": "no PDF found", "attempts": att.steps}
    assert error_text.reason(error_text.pick_failure(r)) == "需要订阅才能下载"


@pytest.mark.parametrize("raw, shown", [
    ("Unpaywall needs the user's real email address - only a placeholder is configured", "邮箱"),
    ("Unpaywall rejected the configured email address (HTTP 422)", "邮箱"),
    ("download exception: KeyError", "下载过程出错"),
    ("missing result", "下载过程出错"),
    ("invalid", "下载过程出错"),
])
def test_config_and_internal_failures_are_not_called_paywalls(raw, shown):
    assert shown in error_text.reason(raw)


def test_the_most_telling_lane_wins_not_the_last():
    from litloop.sources.attempt import DownloadAttempt
    att = DownloadAttempt(key="10.1/x", gen=0)
    att.add_step("free", "ElsevierBrowser", 30.0, None, error=("paywall", "login_required"))
    att.add_step("free", "SSRNBrowser", 5.0, None, error=("navigate_failed", "site_unreachable"))
    assert att.last_error == ("navigate_failed", "site_unreachable")  # outranks a paywall
    att.add_step("free", "SpringerBrowser", 60.0, None, error=("not_entitled", "no_subscription"))
    att.add_step("free", "WileyBrowser", 2.0, None, error=("paywall", "login_required"))
    assert att.last_error == ("not_entitled", "no_subscription")
    # The result's own reason gives way to a better-ranked attempt.
    r = {"success": False, "error": "paywall — requires institutional access", "error_type": "paywall",
         "attempts": att.steps}
    assert "学校没有订阅" in error_text.reason(error_text.pick_failure(r))
