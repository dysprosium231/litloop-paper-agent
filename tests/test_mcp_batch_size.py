"""A batch's MCP result leaves out each paper's attempts (the app has them)."""

from __future__ import annotations

from litloop import server


def test_batch_results_for_the_agent_drop_attempts():
    raw = [{"doi": "10.1/a", "success": False, "attempts": [{"source": "WebVPN"}] * 10}, {"doi": "10.1/b", "success": True}]
    out = server._without_attempts(raw)
    assert out == [{"doi": "10.1/a", "success": False}, {"doi": "10.1/b", "success": True}]
    assert "attempts" in raw[0], "the original (written to the job file) keeps them"
