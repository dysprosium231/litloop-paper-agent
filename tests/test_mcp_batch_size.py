"""A batch's MCP result leaves out each paper's attempts (the app has them)."""

from __future__ import annotations

from litloop import server


def test_batch_results_for_the_agent_drop_attempts():
    raw = [{"doi": "10.1/a", "success": False, "attempts": [{"source": "WebVPN"}] * 10}, {"doi": "10.1/b", "success": True}]
    out = server._without_attempts(raw)
    assert out == [{"doi": "10.1/a", "success": False}, {"doi": "10.1/b", "success": True}]
    assert "attempts" in raw[0], "the original (written to the job file) keeps them"


def test_the_apps_batch_jobs_keep_attempts(monkeypatch, tmp_path):
    """DeepSeek review 2026-10-19: lists over batch_small_max go through the lanes,
    which report only at the end; stripping attempts there left app jobs without reasons."""
    import json
    from litloop.download import pipeline
    failed = {"doi": "10.1016/j.test.2024.000001", "success": False, "reason": "no PDF found",
              "attempts": [{"source": "ElsevierBrowser", "route": "free", "seconds": 30, "ok": False,
                            "error_type": "paywall", "reason": "paywall — requires institutional access"}]}
    monkeypatch.setattr(pipeline, "run_lanes", lambda e, *a, **k: [dict(failed)])
    monkeypatch.setattr(server, "load_config", lambda: {"batch_small_max": 1, "output_dir": str(tmp_path)})
    for_app = json.loads(server._batch_download_impl(["10.1016/j.test.2024.000001", "10.1016/j.test.2024.000002"], output_dir=str(tmp_path), keep_attempts=True))
    for_agent = json.loads(server._batch_download_impl(["10.1016/j.test.2024.000001", "10.1016/j.test.2024.000002"], output_dir=str(tmp_path)))
    assert for_app["results"][0]["attempts"] and "attempts" not in for_agent["results"][0]
