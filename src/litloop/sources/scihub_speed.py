"""Before a batch, measure which Sci-Hub mirror and route (proxy or direct) is fastest.

``scihub_route`` decides which routes may be used: ``direct``, ``proxy``
(host_proxy_url only, keeping the school's exit away from Sci-Hub) or ``auto``
(both, the faster wins). For ``auto`` and ``proxy`` the batch first downloads
one known paper through each mirror on each allowed route at once and keeps the
fastest; the batch then asks that mirror first, on that route. Mirrors keep
their order behind it, so a paper the fastest one lacks is still found.

Why: on 2026-10-17 a 1 MB PDF from sci.bban.top took 18.8 s through the proxy
and 6.0 s direct; most of a Sci-Hub round was spent transferring PDFs.
The choice is kept for an hour (``scihub_route.json``) so single downloads use
it too; failures of the chosen mirror drop it, and the next batch measures again.
"""

from __future__ import annotations

import json
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from ..log import get_logger

log = get_logger()

_TTL = 3600.0
_FAILS_TO_DROP = 3
_LOCK = threading.Lock()
_STATE: dict[str, Any] = {}   # {"mirror", "route", "kbps", "at"}
_FAILS = [0]


def mode(config: dict[str, Any]) -> str:
    """direct | proxy | auto (scihub_via_proxy: true is the older "proxy")."""
    m = str(config.get("scihub_route") or "").strip().lower()
    if m in ("direct", "proxy", "auto"):
        return m
    return "proxy" if config.get("scihub_via_proxy") else "direct"


def _file() -> Path:
    from ..config import DATA_DIR
    return DATA_DIR / "scihub_route.json"


def current() -> dict[str, Any] | None:
    """The fastest (mirror, route) measured within the hour, or None."""
    with _LOCK:
        if not _STATE:
            try:
                _STATE.update(json.loads(_file().read_text(encoding="utf-8")))
            except (OSError, ValueError):
                pass
        if _STATE and time.time() - float(_STATE.get("at", 0)) < _TTL:
            return dict(_STATE)
    return None


def route(config: dict[str, Any]) -> str:
    """proxy | direct for Sci-Hub/LibGen requests right now."""
    m = mode(config)
    if m != "auto":
        return m
    cur = current()
    return cur["route"] if cur else "proxy"  # not measured yet: the private route


def preferred_mirror() -> str | None:
    cur = current()
    return cur["mirror"] if cur else None


def note_result(domain: str, ok: bool, absent: bool = False) -> None:
    """Called after each mirror attempt: the chosen mirror failing in a row is dropped."""
    cur = current()
    if not cur or domain != cur.get("mirror"):
        return
    if ok or absent:
        _FAILS[0] = 0
        return
    _FAILS[0] += 1
    if _FAILS[0] >= _FAILS_TO_DROP:
        log.info(f"   [scihub-speed] {domain} failed {_FAILS[0]} times in a row; measuring again next batch")
        forget()


def forget() -> None:
    with _LOCK:
        _STATE.clear()
        _FAILS[0] = 0
        try:
            _file().unlink(missing_ok=True)
        except OSError:
            pass


def _routes(config: dict[str, Any]) -> list[str]:
    from ..network import _proxy_reachable
    m = mode(config)
    url = str(config.get("host_proxy_url") or "").strip()
    proxy_ok = bool(url) and _proxy_reachable(url)
    if m == "direct":
        return ["direct"]
    if m == "proxy":
        return ["proxy"] if proxy_ok else []
    return (["proxy"] if proxy_ok else []) + ["direct"]


def _one(doi: str, mirror: str, rt: str, config: dict[str, Any], tmp: Path) -> tuple[str, str, float, int]:
    """(mirror, route, seconds, bytes) for one test download; seconds is inf on failure."""
    from .scihub import try_scihub_domain
    cfg = {**config, "scihub_route": rt, "scihub_use_browser": False}
    out = tmp / f"{urlparse(mirror).hostname}_{rt}.pdf"
    t = time.time()
    try:
        r = try_scihub_domain(doi, mirror, out, cfg, False, [])
    except Exception:
        r = None
    dt = time.time() - t
    if r and r.get("success") and out.exists():
        return mirror, rt, dt, out.stat().st_size
    return mirror, rt, float("inf"), 0


def _cleanup_later(futs: list[Any], tmp: str) -> None:
    import shutil
    for f in futs:
        try:
            f.result(timeout=120)
        except Exception:
            pass
    shutil.rmtree(tmp, ignore_errors=True)


def measure(config: dict[str, Any], *, force: bool = False) -> dict[str, Any] | None:
    """Pick the fastest mirror and route for this batch (cached for the hour)."""
    if mode(config) == "direct" and not force:
        return None
    if not force and current():
        return current()
    routes = _routes(config)
    mirrors = [str(m).rstrip("/") for m in (config.get("scihub_domains") or [])][: int(config.get("scihub_speedtest_mirrors", 5) or 5)]
    doi = str(config.get("scihub_speedtest_doi") or "10.1016/j.colsurfb.2017.09.051")
    if not routes or not mirrors:
        return None
    budget = float(config.get("scihub_speedtest_seconds", 40) or 40)
    log.info(f"   [scihub-speed] measuring {len(mirrors)} mirrors x {routes} with {doi}")
    results: list[tuple[str, str, float, int]] = []
    # All start together, so the first to finish is the fastest: stop there
    # (waiting out the budget for a dead mirror cost 40 s on 2026-10-17).
    # Losers still writing keep their files open: leave the folder to them.
    tmp = tempfile.mkdtemp(prefix="litloop-speed-")
    ex = ThreadPoolExecutor(max_workers=len(mirrors) * len(routes))
    futs = [ex.submit(_one, doi, m, r, config, Path(tmp)) for m in mirrors for r in routes]
    try:
        for f in as_completed(futs, timeout=budget):
            results.append(f.result())
            if results[-1][2] != float("inf"):
                break
    except Exception:
        pass  # past the budget: nothing finished
    ex.shutdown(wait=False, cancel_futures=True)
    threading.Thread(target=_cleanup_later, args=(futs, tmp), daemon=True).start()
    ok = [r for r in results if r[2] != float("inf")]
    for m, rt, dt, size in sorted(results, key=lambda r: r[2]):
        log.info(f"   [scihub-speed] {m} via {rt}: " + (f"{dt:.1f}s, {size // 1024} KB" if dt != float("inf") else "failed"))
    if not ok:
        return None
    m, rt, dt, size = min(ok, key=lambda r: r[2])
    state = {"mirror": m, "route": rt, "kbps": round(size / 1024 / max(dt, 0.01)), "seconds": round(dt, 1),
             "at": time.time()}
    with _LOCK:
        _STATE.clear()
        _STATE.update(state)
        _FAILS[0] = 0
        try:
            from .. import store
            store.write_json(_file(), state)
        except Exception:
            pass
    log.info(f"   [scihub-speed] using {m} via {rt} ({state['kbps']} KB/s)")
    return state
