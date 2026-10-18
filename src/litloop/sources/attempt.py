"""One object per paper being downloaded.

The engine's state used to live in separate process-wide sets and thread-locals
(in flight, settled, skipped, the cancel generation, user-wait heartbeats), each
looked up by DOI and kept in step by hand. A ``DownloadAttempt`` holds what one
paper's download knows about itself; the registry maps the paper's key (its
normalized DOI or arXiv ID) to its attempt while the download runs.

What it records:
- ``gen``: the cancel generation the download started under (a cancel bumps it);
- ``user_wait_at``: the last heartbeat of a source waiting for the user;
- ``steps``: every source tried — route, label, seconds, outcome, error type and
  reason — so the app can say which way was tried and why it failed.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class DownloadAttempt:
    key: str
    gen: int
    started: float = field(default_factory=time.perf_counter)  # browser_backend's clock
    user_wait_at: float = 0.0
    steps: list[dict[str, Any]] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def add_step(self, route: str, label: str, seconds: float, result: dict[str, Any] | None) -> None:
        ok = bool(result and result.get("success"))
        step = {"route": route, "source": label, "seconds": round(seconds, 1), "ok": ok}
        if not ok and result:
            step["error_type"] = str(result.get("error_type") or "")
            step["reason"] = str(result.get("reason") or result.get("error") or "")[:200]
        with self._lock:
            self.steps.append(step)


_LOCK = threading.Lock()
_ACTIVE: dict[str, DownloadAttempt] = {}


def begin(key: str, gen: int) -> DownloadAttempt:
    with _LOCK:
        att = DownloadAttempt(key=key, gen=gen)
        _ACTIVE[key] = att
        return att


def end(key: str) -> DownloadAttempt | None:
    with _LOCK:
        return _ACTIVE.pop(key, None)


def active_keys() -> list[str]:
    with _LOCK:
        return list(_ACTIVE)


def get(key: str | None) -> DownloadAttempt | None:
    if not key:
        return None
    with _LOCK:
        return _ACTIVE.get(key)
