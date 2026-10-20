"""Open-access round: a refused OA link gets one short browser try, one paper at a time
(2026-10-20: the round once took 110 s; each worker thread started its own Chrome)."""

from __future__ import annotations

import threading
import time

from litloop import pipeline
from litloop.pipeline import QueueEntry


def test_browser_fallback_is_short_and_one_at_a_time(tmp_path, monkeypatch):
    import litloop.browser_engine as be
    active, peak, timeouts = [0], [0], []
    lock = threading.Lock()

    def fake_browser(url, path, config, *, timeout=60.0):
        with lock:
            active[0] += 1
            peak[0] = max(peak[0], active[0])
        timeouts.append(timeout)
        time.sleep(0.05)
        with lock:
            active[0] -= 1
        return False

    monkeypatch.setattr(pipeline, "_download_url", lambda *a, **k: None)  # every OA link refused
    monkeypatch.setattr(be, "download_pdf_via_browser", fake_browser)
    entries = [QueueEntry(identifier=f"10.1002/x{i}", oa_url=f"https://w.test/doi/pdfdirect/10.1002/x{i}")
               for i in range(4)]
    out = pipeline._run_fast_lane(entries, tmp_path, {"oa_browser_fallback_timeout": 7}, workers=4)
    assert [r["success"] for r in out] == [False] * 4
    assert peak[0] == 1
    assert timeouts == [7.0] * 4
