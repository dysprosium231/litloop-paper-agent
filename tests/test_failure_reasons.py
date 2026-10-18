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
