"""Source registry and download orchestration with tiered parallel racing."""

from __future__ import annotations

import inspect
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from ..cache import cache_get, cache_set
from ..config import load_config, DATA_DIR
from ..identifiers import is_arxiv_identifier, normalize_doi, safe_filename
from ..log import get_logger
from ..download.pdf import fail
from ..download.rename import rename_pdf, generate_filename as rename_pdf_generate_filename

log = get_logger()

from .arxiv import try_arxiv
from .carsi import try_carsi
from .core_api import try_core
from .crossref import try_crossref_page_scrape
from .europepmc import try_europepmc, try_pmc
from .libgen import try_libgen
from .oa_discovery import try_doaj
from .openalex import try_openalex_oa, try_openalex_content_api
from . import attempt as _attempt
from .publishers import get_publisher_fast_sources
from .scibban import try_scibban
from .scihub import try_scihub
from .semantic_scholar import try_semanticscholar
from .openaire import try_openaire
from .unpaywall import try_unpaywall
from .vpnsci import try_vpnsci
from .ezproxy import try_ezproxy

# Global semaphore to limit concurrent browser-based sources across all DOI
# Prevents batch_workers × browser_sources_per_DOI explosion of Chrome windows
_browser_semaphore: threading.Semaphore | None = None
_browser_semaphore_lock = threading.Lock()

# Source labels that require launching a browser (CloakBrowser)
_BROWSER_SOURCE_LABELS = frozenset({
    "ElsevierBrowser", "WileyBrowser", "IEEEBrowser", "ACSBrowser",
    "RSCBrowser", "AIPBrowser", "SpringerBrowser", "APSBrowser",
    "TandFBrowser", "IOPBrowser", "OxfordBrowser", "ACMBrowser",
    "NatureBrowser", "ScienceBrowser", "SAGEBrowser", "ASCEBrowser",
    "RoyalSocietyBrowser", "CopernicusDirect",
    "GenericBrowser", "WebVPN", "CARSI", "EZProxy",
    # Sci-Hub launches CloakBrowser internally (browser-first pass +
    # Cloudflare/ALTCHA challenge solving), so it must participate in the
    # global browser-source concurrency budget.
    "Sci-Hub",
})

# ponytail: negative cache — (source, publisher) -> expiry. A challenge/block
# on one paper skips that source for the whole publisher prefix for 15 min,
# saving the doomed timeout on every later paper. Broaden the key or raise
# the TTL if false-skip reports come in.
_NEG_TTL = 900.0
_NEG_CACHE: dict[tuple[str, str], float] = {}
_NEG_LOCK = threading.Lock()
_NEG_KEYWORDS = ("cloudflare", "challenge", "captcha", "blocked", "forbidden", "403", "429")


def _publisher_of(doi: str) -> str:
    return doi.split("/")[0].lower() if doi.lower().startswith("10.") else "*"


def _neg_record(label: str, doi: str, result: dict[str, Any]) -> None:
    reason = (str(result.get("error_type", "")) + " " + str(result.get("error", ""))).lower()
    if not any(k in reason for k in _NEG_KEYWORDS):
        return  # one-off failures (404, paywall) must not poison the source
    with _NEG_LOCK:
        _NEG_CACHE[(label, _publisher_of(doi))] = time.time() + _NEG_TTL


def _neg_blocked(label: str, doi: str) -> bool:
    now = time.time()
    with _NEG_LOCK:
        expired = [k for k, exp in _NEG_CACHE.items() if exp <= now]
        for k in expired:
            _NEG_CACHE.pop(k, None)
        pub = _publisher_of(doi)
        return (label, pub) in _NEG_CACHE or (label, "*") in _NEG_CACHE


def _get_browser_semaphore(config: dict[str, Any]) -> threading.Semaphore:
    """Get or create the global browser concurrency semaphore."""
    global _browser_semaphore
    max_workers = config.get("max_browser_workers", 1)
    if _browser_semaphore is None:
        with _browser_semaphore_lock:
            if _browser_semaphore is None:
                _browser_semaphore = threading.Semaphore(max_workers)
    return _browser_semaphore

__all__ = ["download", "batch_download"]

_cleanup_done = False


def _cleanup_stale_files(target_dir: Path) -> None:
    """Remove orphaned .part files and racing temp files from previous runs."""
    global _cleanup_done
    if _cleanup_done:
        return
    _cleanup_done = True
    if not target_dir.exists():
        return
    count = 0
    for f in target_dir.iterdir():
        if not f.is_file():
            continue
        name = f.name
        # Skip hidden files (like .doi_index.json)
        if name.startswith("."):
            continue
        # Always clean up .part files
        if name.endswith(".part"):
            try:
                f.unlink()
                count += 1
            except OSError:
                pass
            continue
        # Clean up racing temp files: DOI-based identifier + source label suffix
        # e.g. 10_1038_nature12373_Unpaywall.pdf, 10_1016_test_scihub_st.pdf
        # Must start with 10_ (DOI prefix) and have at least 4 segments (10_NNNN_suffix_label)
        if name.endswith(".pdf") and name.startswith("10_"):
            stem = name[:-4]  # remove .pdf
            # Split from the right to get the last segment as the source label
            parts = stem.rsplit("_", 1)
            if len(parts) == 2:
                base, label = parts
                # Verify: base must look like a DOI (10_NNNN_... with at least 4 segments)
                # and label must be a source name (letters, not just a number)
                base_parts = base.split("_")
                if (len(base_parts) >= 3
                        and base_parts[0] == "10"
                        and base_parts[1].isdigit()
                        and len(base_parts[1]) >= 3
                        and any(c.isalpha() for c in label)):
                    try:
                        f.unlink()
                        count += 1
                    except OSError:
                        pass
    if count > 0:
        log.info(f"Cleaned up {count} stale temp files")


def _is_wrong_paper(path: Path, doi: str, config: dict[str, Any], label: str) -> bool:
    """True when the PDF a source produced is a different paper (download.pdf.paper_check)."""
    from ..download.pdf import paper_check
    t = time.time()
    verdict = paper_check(path, doi, config)
    log.info(f"   [timing] {label} paper check: {(time.time() - t) * 1000:.0f}ms ({verdict})")
    if verdict == "wrong":
        log.info(f"   WRONG PAPER {label}: neither the DOI nor the title is on its first pages")
    return verdict == "wrong"


def _try_source(
    source_fn: Any, doi: str, output_path: Path, config: dict[str, Any], label: str, use_tor: bool = False
) -> dict[str, Any] | None:
    from .scoring import record_result, classify_error, get_user_advice
    t0 = time.time()
    key = normalize_doi(doi) if not is_arxiv_identifier(doi) else doi
    _SOURCE_TLS.doi = key
    # The download this source works for, taken now: by the time a slow source
    # ends, a retry of the same paper may have registered a new attempt, which
    # must not get this one's steps or errors (DeepSeek review 2026-10-19).
    att = _attempt.get(key)
    result = None
    try:
        result = _try_source_inner(source_fn, doi, output_path, config, label, use_tor, t0,
                                   record_result, classify_error, get_user_advice)
    finally:
        settled = _SOURCE_TLS.doi in _SETTLED or (att is not None and _attempt.get(key) is not att)
        _SOURCE_TLS.doi = None
        ok = bool(result and result.get("success"))
        log.info(f"   [timing] {label}: {time.time() - t0:.1f}s, {'PDF' if ok else 'no PDF'}")
        if att is not None:
            from ..publishers.strategies._core import get_last_error
            att.add_step("school" if label in _STEP_TEXT else "free", label, time.time() - t0, result,
                         error=get_last_error())  # this thread's: _try_source_inner cleared it first
    if settled:
        # Late finisher of an already-returned download: its copy is a duplicate.
        if output_path.exists():
            try:
                output_path.unlink()
            except OSError:
                pass
        return None
    return result


def _try_source_inner(source_fn: Any, doi: str, output_path: Path, config: dict[str, Any], label: str,
                      use_tor: bool, t0: float, record_result: Any, classify_error: Any,
                      get_user_advice: Any) -> dict[str, Any] | None:

    # Limit concurrency for browser-based sources
    is_browser = label in _BROWSER_SOURCE_LABELS
    if label == "Sci-Hub" and not config.get("scihub_use_browser", True):
        is_browser = False  # pure-HTTP Sci-Hub must not queue behind the browser lanes
    sem = _get_browser_semaphore(config) if is_browser else None
    if sem:
        t_wait = time.time()
        sem.acquire()
        if time.time() - t_wait > 1:
            log.info(f"   [timing] {label}: waited {time.time() - t_wait:.1f}s for the browser")
    try:
        # Each source starts with no browser error of its own: a fresh racing
        # thread otherwise read the latest error any lane reported, and the
        # Elsevier browser skipped its ScienceDirect attempt (0.0 s, no log)
        # because the previous paper had ended on a paywall (2026-10-17).
        from ..publishers.strategies._core import _set_error
        _set_error("", "")
        sig = inspect.signature(source_fn)
        if "use_tor" in sig.parameters:
            result = source_fn(doi, output_path, config, use_tor=use_tor)
        else:
            result = source_fn(doi, output_path, config)
        latency_ms = (time.time() - t0) * 1000
        if result:
            result["doi"] = doi
            result["identifier"] = doi
            if result.get("success"):
                # Check for suspicious PDF (1-page or very small — likely not full text)
                file_path = result.get("file", "")
                if file_path:
                    from ..download.pdf import is_suspicious_pdf, suspicious_pdf
                    fp = Path(file_path)
                    if fp.exists() and is_suspicious_pdf(fp, doi):
                        log.info(f"   SUSPICIOUS {label}: PDF appears to be a preview/cover page, not full text")
                        record_result(label, False, latency_ms, "suspicious_pdf")
                        return suspicious_pdf(doi, fp, label)
                    if fp.exists() and _is_wrong_paper(fp, doi, config, label):
                        record_result(label, False, latency_ms, "wrong_paper")
                        from ..download.pdf import wrong_paper
                        return wrong_paper(doi, fp, label)
                    if fp.exists():
                        from ..download.pdf import preprint_mark
                        mark = preprint_mark(fp, doi)
                        if mark:
                            # Still this paper, but not the version of record:
                            # the race keeps the copy as a fallback and looks
                            # for the published PDF (2026-11-03).
                            result["preprint"] = mark
                            log.info(f"   PREPRINT {label}: {mark} copy, not the published version")
                record_result(label, True, latency_ms)
            else:
                error_type = classify_error(result.get("status_code", 0))
                record_result(label, False, latency_ms, error_type)
        return result
    except SourceWaived:
        log.info(f"   {label}: stopped, the paper is already downloaded")
        return None
    except Exception as e:
        latency_ms = (time.time() - t0) * 1000
        error_type = classify_error(exception=e)
        # Check if a valid PDF was actually written to disk despite the exception
        # (common with Sci-Hub: browser download succeeds but post-download logic raises)
        from ..download.pdf import is_pdf_file as _is_pdf
        if output_path.exists() and _is_pdf(output_path) and not _is_wrong_paper(output_path, doi, config, label):
            log.info(f"   OK {label} (recovered after {error_type})")
            record_result(label, True, latency_ms)
            return {"success": True, "identifier": doi, "doi": doi,
                    "file": str(output_path), "source": label}
        record_result(label, False, latency_ms, error_type)
        advice = get_user_advice(error_type, label)
        log.info(f"   FAIL {label}: {error_type} — {advice}")
        return None
    finally:
        if sem:
            sem.release()


def _is_browser_lane(label: str) -> bool:
    """A lane that opens a browser window (Sci-Hub mostly solves its gate over HTTP)."""
    return label != "Sci-Hub" and label in _BROWSER_SOURCE_LABELS


def _wait_any(events: tuple[threading.Event, threading.Event], timeout: float) -> bool:
    """Wait for any event to be set, polling at a fine grain (no wait-any API).

    Used by the racing engine to react to either the first success or the
    completion of every submitted source, whichever comes first.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if any(e.is_set() for e in events):
            return True
        if _cancelled():
            return False
        time.sleep(0.05)
    return any(e.is_set() for e in events)


def _promote_publisher_browser(launchable: list[tuple[Any, str]], doi: str) -> None:
    """Launch the paper's own publisher browser lane second, not after every cheap lane.

    The hedged cascade adds one lane per hedge delay; for a paywalled paper the
    publisher's browser lane (the one that can actually get it) used to start
    only after ~5 open-access lanes, 7-8 s in. Second place keeps the best
    cheap lane first, so open-access papers still win in a second or two.
    """
    try:
        from .publishers import get_publisher
        own = f"{get_publisher(doi)}Browser"
    except Exception:
        return
    for i, (_fn, label) in enumerate(launchable):
        if label == own and i > 1:
            launchable.insert(1, launchable.pop(i))
            return


def _run_tiers_parallel(
    tiers: list[tuple[list[tuple[Any, str]], str, int]],
    doi: str,
    target_dir: Path,
    output_path: Path,
    config: dict[str, Any],
    use_tor: bool,
    overall_timeout: int,
    failures: list[dict[str, str]] | None = None,
) -> dict[str, Any] | None:
    """Race all tiers in parallel. First successful tier wins.

    Uses a shared result dict so that any source thread can publish its
    success immediately, even if it's running inside a nested parallel
    call (like Sci-Hub domain racing). `failures` (optional) collects why
    each losing lane failed — pool workers append to it.
    """
    if failures is None:
        failures = []
    if _cancelled():
        return None
    # A new race for this DOI is live again: callers such as batch_download race
    # without going through download(), so clear any earlier "settled" mark here
    # or every result of this race would be discarded as a late finisher.
    _SETTLED.discard(normalize_doi(doi) if not is_arxiv_identifier(doi) else doi)
    if not tiers:
        return None

    # Flatten all sources across tiers with their labels
    all_sources: list[tuple[Any, str, str, int]] = []  # (fn, label, tier_label, timeout)
    for tier_sources, tier_label, tier_timeout in tiers:
        for fn, label in tier_sources:
            all_sources.append((fn, label, tier_label, tier_timeout))

    if not all_sources:
        return None

    # If only one source, run directly
    if len(all_sources) == 1:
        fn, label, tier_label, timeout = all_sources[0]
        src_output = target_dir / f"{safe_filename(doi)}_{label}.pdf"
        result = _try_source(fn, doi, src_output, config, label, use_tor=use_tor)
        if result and result.get("success"):
            final_path = Path(result.get("file", ""))
            if final_path != output_path and final_path.exists():
                output_path.parent.mkdir(parents=True, exist_ok=True)
                if output_path.exists():
                    output_path.unlink()
                final_path.rename(output_path)
                result["file"] = str(output_path)
            return result
        _record_source_failure(failures, label, result)
        return None

    # Shared result: any thread can publish success here, signaled via Event
    result_lock = threading.Lock()
    success_event = threading.Event()
    cancel_event = threading.Event()
    all_done_event = threading.Event()   # set when every submitted future returned
    done_counter: list[int] = [0]
    done_counter_lock = threading.Lock()
    submission_done = threading.Event()  # staggered scheduler finished launching lanes
    lanes_idle = threading.Event()       # every launched-so-far lane has answered
    shared_result: dict[str, Any] = {"result": None}
    # A preprint copy of the right paper: kept, but a version of record wins
    # (2026-10-26: the open-access route served the Research Square preprint
    # for a Scientific Reports DOI and ended the race before the publisher).
    preprint_result: dict[str, Any] = {"result": None}
    race_over = threading.Event()  # set when this race returns; waived lanes stop launching

    def _try_and_publish(fn, label, src_output):
        # Skip if another source already succeeded
        if cancel_event.is_set():
            return None
        _SOURCE_TLS.race = race_over
        try:
            result = _try_source(fn, doi, src_output, config, label, use_tor=use_tor)
        finally:
            _SOURCE_TLS.race = None
            # This pool is created per download (issue #57): once its worker
            # threads die, their thread-local shared browsers are unreachable
            # to close_shared_browser() and the atexit reaper only fires at
            # process exit — a long-lived MCP server accumulates Chromium
            # processes. Each worker closes its own browser before exiting.
            try:
                from ..browser.engine import shutdown_shared_browser
                shutdown_shared_browser()
            except Exception:
                pass
        if result and not result.get("success"):
            # ponytail: (source, publisher) negative cache — a Cloudflare block
            # on one paper must not burn every later paper's timeout; raise TTL
            # or key by full DOI if false-skip reports come in.
            _neg_record(label, doi, result)
            _record_source_failure(failures, label, result)
        if result and result.get("success"):
            if result.get("preprint"):
                # Not the version of record: hold it and keep the other lanes
                # going. It is used only if none of them delivers one.
                with result_lock:
                    if preprint_result["result"] is None:
                        preprint_result["result"] = (result, label, src_output)
                log.info(f"   PREPRINT {label} ({result['preprint']}); still looking for the published version")
                return result
            with result_lock:
                if shared_result["result"] is None:
                    shared_result["result"] = (result, label, src_output)
                    cancel_event.set()
                    success_event.set()
        return result

    def _take_preprint() -> dict[str, Any] | None:
        """The kept preprint copy, moved into place, when nothing better came back."""
        item = preprint_result["result"]
        if item is None:
            return None
        result, label, src_output = item
        final_path = Path(result.get("file", ""))
        if final_path != output_path and final_path.exists():
            output_path.parent.mkdir(parents=True, exist_ok=True)
            if output_path.exists():
                output_path.unlink()
            final_path.rename(output_path)
            result["file"] = str(output_path)
        log.info(f"   OK {label} (preprint only: {result.get('preprint')})")
        return result

    def _on_future_done(fut) -> None:
        with done_counter_lock:
            done_counter[0] += 1
            if done_counter[0] >= len(futures):
                # Mid-scheduling this wakes the scheduler so a fast-failing
                # lane widens immediately instead of idling out the hedge
                # delay; after submission_done it means the race is over.
                lanes_idle.set()
                if submission_done.is_set():
                    all_done_event.set()

    # Hedged cascade (default): lanes arrive score-ordered by the caller.
    # Launch the best first and only widen when it fails to answer within
    # hedge_delay_seconds — cuts request volume and anti-bot heat vs a flat
    # race while keeping tail latency close. race_mode=full restores the
    # flat all-at-once race.
    race_mode = str(config.get("race_mode", "hedge")).lower()
    hedge_delay = max(0.0, float(config.get("hedge_delay_seconds", 1.5)))
    if race_mode == "full":
        hedge_delay = 0.0
    mode_label = "hedged cascade" if hedge_delay > 0 else "flat race"

    launchable: list[tuple[Any, str]] = []
    for fn, label, _tier_label, _tier_timeout in all_sources:
        if _neg_blocked(label, doi):
            log.info(f"   SKIP {label} (negative cache: recently failed for this publisher)")
            continue
        launchable.append((fn, label))
    _promote_publisher_browser(launchable, doi)

    log.info(f"   Racing {len(all_sources)} sources across {len(tiers)} tiers ({mode_label})...")
    deadline = time.time() + overall_timeout + 5
    pool = ThreadPoolExecutor(max_workers=max(1, len(launchable)))
    futures = {}
    try:
        for i, (fn, label) in enumerate(launchable):
            lanes_idle.clear()
            src_output = target_dir / f"{safe_filename(doi)}_{label}.pdf"
            fut = pool.submit(_try_and_publish, fn, label, src_output)
            futures[fut] = (label, src_output)
            fut.add_done_callback(_on_future_done)
            if hedge_delay <= 0 or i == len(launchable) - 1 or success_event.is_set():
                continue
            # Widen only when needed: another lane succeeded, everything
            # launched so far already answered (fast failure), the hedge
            # delay elapsed, or the overall deadline arrived.
            now = time.time()
            if now >= deadline:
                break
            _wait_any((success_event, lanes_idle),
                      min(hedge_delay, deadline - now))
            if success_event.is_set() or time.time() >= deadline:
                break
        submission_done.set()
        with done_counter_lock:
            if done_counter[0] >= len(futures):
                all_done_event.set()

        # Wait for first success, all sources done, or the overall timeout.
        # all_done lets a full fast-failure return immediately instead of
        # burning the whole timeout + grace period.
        if not success_event.is_set() and not all_done_event.is_set():
            _wait_any((success_event, all_done_event),
                      max(0.0, deadline - time.time()))

        if shared_result["result"] is not None:
            result, label, src_output = shared_result["result"]
            final_path = Path(result.get("file", ""))
            if final_path != output_path and final_path.exists():
                output_path.parent.mkdir(parents=True, exist_ok=True)
                if output_path.exists():
                    output_path.unlink()
                final_path.rename(output_path)
                result["file"] = str(output_path)
            log.info(f"   OK {label}")
            return result
        if all_done_event.is_set():
            preprint = _take_preprint()
            if preprint is not None:
                return preprint
            log.info("   All sources failed")
            return None

        # Timeout reached — late results. A browser lane still at work (opening
        # the page, reaching a login) gets up to race_grace_browser_seconds;
        # once none is, only race_grace_seconds. A lane that waits for the user
        # says so (note_user_waiting) and is waited for below.
        grace = float(config.get("race_grace_seconds", 15))
        grace_browser = float(config.get("race_grace_browser_seconds", 180))
        log.info(f"   Racing timed out after {overall_timeout + 5}s, waiting for late results...")
        t_grace = time.time()
        quick_until = time.time() + grace
        stop_at = time.time() + max(grace, grace_browser)
        while not success_event.is_set() and not all_done_event.is_set() and not _cancelled():
            now = time.time()
            if now >= stop_at or _user_waiting_for(doi):
                break
            browser_at_work = any(not f.done() and _is_browser_lane(lbl) for f, (lbl, _) in futures.items())
            if not browser_at_work and now >= quick_until:
                break
            _wait_any((success_event, all_done_event), 1.0)
        # A lane is waiting for the user's login: keep waiting while its
        # heartbeat is fresh instead of abandoning a login in progress.
        if _user_waiting_for(doi):
            log.info("   A source is waiting for your login — waiting for it...")
            stop_at = time.time() + _USER_WAIT_MAX_S
            while (not success_event.is_set() and not all_done_event.is_set()
                   and not _cancelled() and time.time() < stop_at
                   and _user_waiting_for(doi)):
                _wait_any((success_event, all_done_event), 3.0)
        if shared_result["result"] is not None:
            result, label, src_output = shared_result["result"]
            final_path = Path(result.get("file", ""))
            if final_path != output_path and final_path.exists():
                output_path.parent.mkdir(parents=True, exist_ok=True)
                if output_path.exists():
                    output_path.unlink()
                final_path.rename(output_path)
                result["file"] = str(output_path)
            log.info(f"   OK {label} (late, after {time.time() - t_grace:.1f}s)")
            return result
        if all_done_event.is_set():
            preprint = _take_preprint()
            if preprint is not None:
                return preprint
            log.info("   All sources failed")
            return None

        # Final scan: check if any source wrote a valid PDF file despite timeout
        from ..download.pdf import is_pdf_file, is_suspicious_pdf, suspicious_pdf
        for label, src_output in futures.values():
            if src_output.exists() and is_pdf_file(src_output):
                if is_suspicious_pdf(src_output, doi):
                    log.info(f"   SUSPICIOUS {label} (file scan): skipping suspicious PDF")
                    continue
                if _is_wrong_paper(src_output, doi, config, f"{label} (file scan)"):
                    continue
                output_path.parent.mkdir(parents=True, exist_ok=True)
                if output_path.exists():
                    output_path.unlink()
                src_output.rename(output_path)
                log.info(f"   OK {label} (file scan)")
                return {"success": True, "identifier": doi, "doi": doi,
                        "file": str(output_path), "source": label}

        preprint = _take_preprint()
        if preprint is not None:
            return preprint
        log.info(f"   All sources failed")
    finally:
        # Never block termination on sources that refuse to return: cancel
        # pending futures and waive stragglers. Their own I/O deadlines
        # (connect/read timeouts, stream deadline in pdf_utils) bound how long
        # an abandoned worker thread can outlive the race.
        race_over.set()
        alive = [f for f in futures if not f.done()]
        if alive:
            log.info(f"   Racing finished with {len(alive)} source(s) still running (waived)")
        pool.shutdown(wait=False, cancel_futures=True)
        # Cleanup temp files
        for _, other_path in futures.values():
            if other_path != output_path and other_path.exists():
                try:
                    other_path.unlink(missing_ok=True)
                except OSError:
                    pass

    return None


# Single-flight guard: at most one download() per identifier per process.
# Duplicate submissions (e.g. an MCP client retrying after its own 30s client
# timeout while the server is still racing sources) used to start a second,
# parallel browser task for the same paper — the "infinite popup" experience.
_INFLIGHT_LOCK = threading.Lock()
# The papers in flight are the DownloadAttempts registered in sources.attempt.

class _RecentSet:
    """A set that keeps only the latest ``limit`` keys (insertion order).

    Late finishers only need to know a paper ended while they still run, so a
    long-running server need not remember every paper it ever downloaded.
    """

    def __init__(self, limit: int = 2000) -> None:
        self._keys: dict[str, None] = {}
        self._limit = limit
        self._lock = threading.Lock()

    def add(self, key: str) -> None:
        with self._lock:
            self._keys.pop(key, None)
            self._keys[key] = None
            while len(self._keys) > self._limit:
                self._keys.pop(next(iter(self._keys)))

    def update(self, keys: Any) -> None:
        for k in keys:
            self.add(k)

    def discard(self, key: str) -> None:
        with self._lock:
            self._keys.pop(key, None)

    def clear(self) -> None:
        with self._lock:
            self._keys.clear()

    def __contains__(self, key: object) -> bool:
        return key in self._keys

    def __len__(self) -> int:
        return len(self._keys)


# DOIs whose download() has returned. Racing returns on the first success while
# other sources keep running ("waived"); those late finishers check this set to
# drop their duplicate files and to stop launching browsers.
_SETTLED = _RecentSet()
_SOURCE_TLS = threading.local()

# Heartbeats from sources that are waiting for the user to log in, by DOI.
# The race must not give up (and open the next phase's window) while the user
# is in the middle of a login.
# Heartbeats stop the moment the login succeeds, but the source still has to
# reopen the article and capture the PDF: keep waiting this long after the
# last heartbeat so the race doesn't open the next phase's window meanwhile.
_USER_WAIT_FRESH_S = 120.0
_USER_WAIT_MAX_S = 600.0


_WAIT_TEXT = {
    "login": "请在弹出的浏览器窗口中登录学校账号",
    "verify": "请在弹出的浏览器窗口中完成人机验证，出现勾选框时勾选它",
}


class SourceWaived(RuntimeError):
    """Raised in a source's wait-for-the-user loop once its paper is settled."""


def note_user_waiting(what: str = "") -> None:
    """Called by a source on every poll while it waits for the user (a login, a
    human check). Keeps the race from closing that window, and tells the app:
    a notice on top of the job page and a line under the paper.

    Raises ``SourceWaived`` when the paper is already done (another source got
    it): the waiting window must not hold the browser or ask for a login."""
    if not _source_may_continue():
        raise SourceWaived("download already finished; stop waiting for the user")
    doi = getattr(_SOURCE_TLS, "doi", None)
    if doi:
        att = _attempt.get(doi)
        if att is not None:
            att.user_wait_at = time.time()
        try:
            from ..download import progress as pr
            text = _WAIT_TEXT.get(what, "请在弹出的浏览器窗口中完成登录或人机验证")
            pr.set_attention(f"wait:{doi}", f"{text}（{doi}）", current=doi, phase="等待你的操作")
            pr.item_stage(doi, "等待你在浏览器中操作")
        except Exception:
            pass


def _user_waiting_for(doi: str) -> bool:
    key = normalize_doi(doi) if not is_arxiv_identifier(doi) else doi
    att = _attempt.get(key)
    return att is not None and time.time() - att.user_wait_at < _USER_WAIT_FRESH_S


# Cooperative cancellation. cancel_downloads() bumps the generation; every
# download() remembers the generation it started under (thread-local), so it
# knows it was cancelled without killing threads. Downloads started afterwards
# are unaffected.
_CANCEL_GEN = [0]
_DL_TLS = threading.local()
# Papers the user chose to skip while one of their windows waited for them
# (the app's 跳过这篇): that download stops as if cancelled; the others go on.
_SKIPPED: set[str] = set()


def _cancelled() -> bool:
    """This thread's paper was cancelled (its job's generation moved on) or skipped."""
    key = getattr(_DL_TLS, "key", None)
    att = _attempt.get(key)
    gen = att.gen if att is not None else getattr(_DL_TLS, "gen", None)
    if gen is not None and gen != _CANCEL_GEN[0]:
        return True
    return key in _SKIPPED


def skip_download(identifier: str) -> bool:
    """Give up on one paper: in flight, its windows stop waiting for the user and
    close and its race returns; not started yet, it is skipped when its turn
    comes (download_jobs clears the requests when a job starts)."""
    key = identifier.strip()
    if not is_arxiv_identifier(key):
        key = normalize_doi(key)
    with _INFLIGHT_LOCK:
        _SKIPPED.add(key)
        if _attempt.get(key) is None:
            log.info(f"   Will skip when its turn comes: {key}")
            return True
        _SETTLED.add(key)  # its sources stop waiting, launch nothing, drop late results
    try:
        from ..download import progress as _pr
        _pr.clear_attention(f"wait:{key}")
        from ..browser.backend import close_browsers_since
        close_browsers_since(0.0, owner=key)  # its own windows only
    except Exception as exc:
        log.info(f"   Skip: browser cleanup skipped: {exc}")
    log.info(f"   Skipped by the user: {key}")
    return True


def cancel_downloads() -> dict[str, Any]:
    """Cancel every download in flight: stop waiting, drop late results, close windows."""
    with _INFLIGHT_LOCK:
        _CANCEL_GEN[0] += 1
        keys = sorted(_attempt.active_keys())
        _SETTLED.update(keys)  # sources still running drop results, launch nothing
    closed = 0
    try:
        from ..browser.backend import close_browsers_since
        closed = close_browsers_since(0.0)
    except Exception as exc:
        log.info(f"   Cancel: browser cleanup skipped: {exc}")
    log.info(f"   Cancelled {len(keys)} download(s); closed {closed} browser(s)")
    return {"cancelled": keys, "browsers_closed": closed}


def _source_may_continue() -> bool:
    """LAUNCH_GUARD hook: False once this thread's race or download is over."""
    race = getattr(_SOURCE_TLS, "race", None)
    if race is not None and race.is_set():
        return False  # waived lane: its race already returned
    doi = getattr(_SOURCE_TLS, "doi", None)
    if not doi:  # a pool thread working for a source (Sci-Hub's mirror race)
        from ..browser.backend import current_owner
        doi = current_owner()
    return not (doi and doi in _SETTLED)


# Per-download record of WHY each racing source failed. A channel being
# unreachable is usually temporary or a missing user decision (e.g. Unpaywall
# needs the user's email) — surfaced in the final failure result so the
# user/agent can choose: retry, fix config, or switch channel. Never skip a
# channel silently. The list is created per download() and threaded through
# _run_tiers_parallel (pool workers append to it; list.append is atomic).
def _record_source_failure(failures: list[dict[str, str]], label: str,
                           result: dict[str, Any] | None) -> None:
    """Collect structured failure reasons (label, reason, error_type, action)."""
    if not result or result.get("success"):
        return
    try:
        failures.append({
            "source": label,
            "reason": str(result.get("reason") or result.get("error") or "")[:200],
            "error_type": str(result.get("error_type", "")),
            "action": str(result.get("action", "")),
        })
    except Exception:
        pass


def download(
    identifier: str,
    output_dir: str | Path | None = None,
    *,
    scihub_enabled: bool | None = None,
    use_tor: bool = False,
    use_vpnsci: bool = False,
    bibtex: bool = False,
    rename: bool = True,
    _institutional: bool = True,
    strategy: str | None = None,
    _batch: bool = False,
    _scihub_http_only: bool = False,
    _school_only: bool = False,
) -> dict[str, Any]:
    key = identifier.strip()
    if not is_arxiv_identifier(key):
        key = normalize_doi(key)
        from ..download.doi_check import registered
        if registered(key) is False:  # a typo: no source can have it
            return fail(key, "DOI is not registered at doi.org", error_type="doi_not_found",
                        action="check the DOI for typos")
    gen = getattr(_DL_TLS, "gen", None)
    with _INFLIGHT_LOCK:
        if _attempt.get(key) is not None:
            log.info(f"   Single-flight: download already in progress for {key}")
            return fail(
                key,
                reason="download already in progress for this identifier",
                error_type="in_progress",
                action="wait for the in-flight attempt to finish (it is still "
                       "racing sources); retry in a minute only if it failed",
            )
        if key in _SKIPPED:  # the user skipped it while it was queued
            _SKIPPED.discard(key)  # used up: a later retry of the paper runs
            return fail(key, "skipped by user", error_type="skipped", action="none")
        _attempt.begin(key, gen if gen is not None else _CANCEL_GEN[0])
        _SETTLED.discard(key)
    t0 = time.perf_counter()  # same clock as browser_backend._remember
    prev_key = getattr(_DL_TLS, "key", None)
    _DL_TLS.key = key
    prev_gen = getattr(_DL_TLS, "gen", None)
    if prev_gen is None:  # nested calls (batch phase 2) keep the batch's generation
        _DL_TLS.gen = _CANCEL_GEN[0]
    try:
        result = _download_impl(
            identifier,
            output_dir,
            scihub_enabled=scihub_enabled,
            use_tor=use_tor,
            use_vpnsci=use_vpnsci,
            bibtex=bibtex,
            rename=rename,
            _institutional=_institutional,
            strategy=strategy,
            _batch=_batch,
            _scihub_http_only=_scihub_http_only,
            _school_only=_school_only,
        )
        att = _attempt.get(key)
        steps = list(att.steps) if att is not None else []
        if key in _SKIPPED and not result.get("success"):
            _SKIPPED.discard(key)  # used up: a later retry of the paper runs
            return {**fail(key, "skipped by user", error_type="skipped", action="none"), "attempts": steps}
        if _cancelled() and not result.get("success"):
            return {**fail(key, "cancelled by user", error_type="cancelled", action="none"), "attempts": steps}
        if steps and isinstance(result, dict):
            result.setdefault("attempts", steps)  # which ways were tried, how long, why they failed
        if result.get("success"):
            from ..publishers.strategies._core import clear_no_access
            clear_no_access(key)
            _SKIPPED.discard(key)  # a skip that came too late must not skip the next try
        return result
    finally:
        _DL_TLS.key = prev_key
        try:  # whatever this paper waited on the user for is over now
            from ..download import progress as _pr
            _pr.clear_attention(f"wait:{normalize_doi(key) if not is_arxiv_identifier(key) else key}")
        except Exception:
            pass
        if prev_gen is None:
            _DL_TLS.gen = None
        with _INFLIGHT_LOCK:
            _attempt.end(key)
            _SETTLED.add(key)
        # Racing returns on the first success but waived sources keep their
        # browser windows open. Close what this download launched (its own
        # browsers only, so a concurrent download keeps its windows).
        try:
            from ..browser.backend import close_browsers_since
            n = close_browsers_since(t0, owner=key)
            if n:
                log.info(f"   Closed {n} browser(s) left open by this download")
        except Exception as exc:
            log.info(f"   Browser cleanup skipped: {exc}")


def _download_impl(
    identifier: str,
    output_dir: str | Path | None = None,
    *,
    scihub_enabled: bool | None = None,
    use_tor: bool = False,
    use_vpnsci: bool = False,
    bibtex: bool = False,
    rename: bool = True,
    _institutional: bool = True,
    strategy: str | None = None,
    _batch: bool = False,
    _scihub_http_only: bool = False,
    _school_only: bool = False,
) -> dict[str, Any]:
    race_failures: list[dict[str, str]] = []
    config = load_config()
    if _scihub_http_only:
        # Sci-Hub's gate solved over HTTP only: no browser, so the paper does
        # not queue for the one browser slot (a batch's first round).
        config["scihub_use_browser"] = False
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    if scihub_enabled is not None:
        config["scihub_enabled"] = scihub_enabled

    if strategy is not None:
        config["download_strategy"] = strategy

    # PMC allows automated retrieval only through its Cloud / OAI-PMH /
    # E-utilities / BioC services (https://pmc.ncbi.nlm.nih.gov/tools/developers/),
    # so its PDF pages are used for single downloads only, never in a batch.
    config["_batch"] = _batch

    target_dir = Path(output_dir) if output_dir else Path(config["output_dir"])
    target_dir.mkdir(parents=True, exist_ok=True)

    _cleanup_stale_files(target_dir)

    identifier = identifier.strip()
    output_path = target_dir / f"{safe_filename(identifier)}.pdf"

    cached = cache_get(identifier, config)
    if cached and cached.get("success"):
        cached_file = Path(cached.get("file", ""))
        cached_doi = str(cached.get("doi") or "")
        # A result cached before the paper check existed may be a different paper
        # (a JSTOR essay came back from the cache for a Ceramics International DOI, 2026-10-28).
        if cached_file.is_file() and cached_doi and _is_wrong_paper(cached_file, cached_doi, config, "cache"):
            log.info("   The cached file is a different paper; downloading again")
            cached_file = Path("")
        if cached_file.is_file() and not _same_dir(cached_file.parent, target_dir):
            # Got before into another folder (the save location has changed since,
            # 2026-10-19): the user expects it here. Copy it, no new download.
            try:
                import shutil
                copied = target_dir / cached_file.name
                if not copied.exists():
                    shutil.copy2(cached_file, copied)
                log.info(f"   Cached file is in another folder; copied {cached_file.name} here")
                cached = {**cached, "file": str(copied)}
                cached_file = copied
                if cached.get("doi"):
                    _update_doi_index(target_dir, str(cached["doi"]), copied,
                                      source=str(cached.get("source") or ""), config=config)
                cache_set(identifier, cached, config)
            except OSError as exc:
                log.info(f"   Cannot copy the cached file here ({exc}); downloading again")
                cached_file = Path("")
        if cached_file.is_file():
            cached["cached"] = True
            if bibtex:
                from ..citations.bibtex_crossref import fetch_bibtex
                cached["bibtex"] = fetch_bibtex(identifier, config)
            return cached

    # Scan output dir for existing file with same rename pattern
    doi = normalize_doi(identifier) if not is_arxiv_identifier(identifier) else identifier

    # Validate DOI before attempting download
    if not is_arxiv_identifier(identifier):
        from ..identifiers import validate_doi
        valid, msg = validate_doi(doi)
        if not valid:
            log.info(f"   DOI validation failed: {msg}")
            return {"success": False, "identifier": identifier, "doi": doi, "error": f"Invalid DOI: {msg}"}

    # Scan output dir for any PDF already downloaded for this DOI.
    # Uses a DOI→file index to catch renamed files reliably.
    doi_index = target_dir / ".doi_index.json"
    if doi_index.exists():
        try:
            idx = json.loads(doi_index.read_text(encoding="utf-8"))
            raw_entry = idx.get(doi)
            if raw_entry:
                entry = _index_entry(raw_entry)
                candidate = Path(entry["file"]) if entry and entry.get("file") else None
                if entry and candidate and _index_entry_is_fresh(entry, config) and _index_strategy_compatible(entry, config):
                    if candidate.exists():
                        log.info(f"   Found existing file (index): {candidate.name}")
                        result = {
                            "success": True, "identifier": identifier,
                            "doi": doi, "file": str(candidate),
                            "source": "local_cache", "cached": True,
                        }
                        # Upgrade legacy entries to records and refresh provenance.
                        idx[doi] = {
                            "file": str(candidate),
                            "source": entry["source"] or "local_cache",
                            "strategy": entry["strategy"] or config.get("download_strategy", ""),
                            "ts": time.time(),
                        }
                        doi_index.write_text(json.dumps(idx, indent=2, ensure_ascii=False), encoding="utf-8")
                        cache_set(identifier, result, config)
                        return result
                # Stale (file deleted), TTL-expired, or strategy-mismatched entry —
                # drop it so the download is retried instead of serving stale state.
                del idx[doi]
                doi_index.write_text(json.dumps(idx, indent=2, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass

    from ..citations.formats import fetch_metadata
    metadata = fetch_metadata(doi, config)
    if metadata:
        from ..download.rename import rename_format
        expected_name = rename_pdf_generate_filename(metadata, doi=doi, fmt=rename_format(config))
        if expected_name:
            # Generated names can collide across papers, so a name match
            # alone is not proof: skip files the index gives to
            # another DOI and require this DOI inside the PDF.
            claimed = _files_claimed_by_other_dois(target_dir, doi)
            for suffix in ("", "_2", "_3", "_4", "_5"):
                candidate = target_dir / f"{expected_name}{suffix}.pdf"
                if (candidate.exists() and _norm_path(candidate) not in claimed
                        and _pdf_mentions_doi(candidate, doi)):
                    log.info(f"   Found existing file: {candidate.name}")
                    result = {
                        "success": True, "identifier": identifier,
                        "doi": doi, "file": str(candidate),
                        "source": "local_cache", "cached": True,
                    }
                    cache_set(identifier, result, config)
                    return result

    log.info(f"LitLoop - {identifier}")
    if _cancelled():  # skipped or cancelled while the metadata lookup ran
        return fail(identifier, "skipped by user" if getattr(_DL_TLS, "key", None) in _SKIPPED else "cancelled by user")

    if is_arxiv_identifier(identifier):
        log.info("   [L0] arXiv direct")
        result = try_arxiv(identifier, output_path, config)
        if result:
            _update_doi_index(
                target_dir,
                identifier,
                Path(result.get("file", "")),
                source=result.get("source") or "arXiv",
                strategy=config.get("download_strategy", ""),
                config=config,
            )
            if rename:
                _auto_rename(result, identifier, config, doi=identifier, target_dir=target_dir)
            cache_set(identifier, result, config)
            if bibtex:
                from ..citations.bibtex_crossref import fetch_bibtex
                result["bibtex"] = fetch_bibtex(identifier, config)
            return result
        return fail(identifier, "arXiv PDF not available")

    doi = normalize_doi(identifier)

    # A preprint is a file for this DOI but not the version of record: keep it
    # aside and let the later phases look for the published PDF (2026-11-03).
    preprint_only: dict[str, Any] = {"result": None}

    def _stash_preprint(res: dict[str, Any]) -> bool:
        if not (res and res.get("preprint")):
            return False
        path = Path(res.get("file", ""))
        marked = path.with_name(f"{path.stem}_preprint{path.suffix}")
        if path.exists() and marked != path:
            try:
                path.replace(marked)
                res["file"] = str(marked)
            except OSError:
                pass
        preprint_only["result"] = res
        log.info(f"   {res['preprint']} preprint of this paper; still looking for the published version")
        return True

    def _use_preprint() -> dict[str, Any] | None:
        res = preprint_only["result"]
        if res is None:
            return None
        if rename:
            _auto_rename(res, identifier, config, doi=doi, target_dir=target_dir)
        if bibtex:
            from ..citations.bibtex_crossref import fetch_bibtex
            res["bibtex"] = fetch_bibtex(doi, config)
        log.info(f"   No version of record available; keeping the {res.get('preprint')} preprint")
        return res

    # Phase 1: Free sources (OA + grey) — parallel race
    t_phase = time.time()
    p_phase = time.perf_counter()  # close_browsers_since's clock
    # _school_only: a batch's second pass; its first pass already tried these.
    free_sources = [] if _school_only else _build_free_sources(doi, config)
    log.info(f"   [timing] phase 1 sources built: {time.time() - t_phase:.1f}s")
    if free_sources:
        result = _run_tiers_parallel(
            [(free_sources, "Free", 15)], doi, target_dir, output_path, config, use_tor, 15,
            failures=race_failures,
        )
        log.info(f"   [timing] phase 1 (free sources and publisher site): {time.time() - t_phase:.1f}s, "
                 f"{'PDF' if result else 'no PDF'}")
        if result and _stash_preprint(result):
            pass
        elif result:
            _update_doi_index(
                target_dir,
                doi,
                Path(result.get("file", "")),
                source=result.get("source") or "",
                strategy=config.get("download_strategy", ""),
                config=config,
            )
            if rename:
                _auto_rename(result, identifier, config, doi=doi, target_dir=target_dir)
            cache_set(identifier, result, config)
            if bibtex:
                from ..citations.bibtex_crossref import fetch_bibtex
                result["bibtex"] = fetch_bibtex(doi, config)
            return result

    # Phase 2: Institutional access — only when Phase 1 failed
    # Skip institutional fallback for grey_only/scihub_only strategy
    from ..publishers.strategies._core import no_access
    if _institutional and no_access(doi) and not config.get("school_routes_after_publisher_login", False):
        log.info("   Logged into the publisher as the school and still no full text: skipping the school routes")
        preprint = _use_preprint()
        if preprint is not None:
            return preprint
        return fail(identifier, "the school does not subscribe: logged into the publisher with the school's identity, still no full text",
                    error_type="not_entitled", action="no_subscription")
    if _institutional and config.get("download_strategy") not in ("scihub_only", "grey_only") and not _cancelled():
        # Phase 1's lanes that are still at work (waived) keep their browsers and
        # the browser slot: on 2026-10-18 a stuck publisher-site lane held it
        # for 16 minutes and the WebVPN step queued 697 s behind it. Phase 1 is
        # over, so close what it opened before the school routes need a browser.
        try:
            from ..browser.backend import close_browsers_since
            n = close_browsers_since(p_phase, owner=getattr(_DL_TLS, "key", None) or doi)
            if n:
                log.info(f"   Closed {n} browser(s) phase 1 left open")
        except Exception as exc:
            log.info(f"   Phase 1 browser cleanup skipped: {exc}")
        # Session self-healing: validate and refresh institutional sessions
        # before racing them (WebVPN only; CARSI self-heals inside login()).
        t_heal = time.time()
        try:
            from ..institutional.session_heal import ensure_institutional_sessions
            heal_report = ensure_institutional_sessions(
                config, use_vpnsci=use_vpnsci or bool(config.get("vpnsci_enabled")))
            for channel, state in heal_report.items():
                log.info(f"   [session-heal] {channel}: {state.get('status')}")
        except Exception as exc:
            log.info(f"   [session-heal] skipped: {exc}")
        log.info(f"   [timing] session check: {time.time() - t_heal:.1f}s")
        if _cancelled():  # skipped or cancelled while the session check waited for a login
            return fail(identifier, "skipped by user" if getattr(_DL_TLS, "key", None) in _SKIPPED else "cancelled by user")
        inst_sources = _build_institutional_sources(doi, config, use_vpnsci=use_vpnsci)
        if inst_sources:
            log.info("   Phase 1 failed, trying institutional access...")
            result = _run_institutional_sequence(inst_sources, doi, target_dir, output_path, config,
                                                 failures=race_failures)
            if result and _stash_preprint(result):
                pass
            elif result:
                _update_doi_index(
                    target_dir,
                    doi,
                    Path(result.get("file", "")),
                    source=result.get("source") or "",
                    strategy=config.get("download_strategy", ""),
                    config=config,
                )
                if rename:
                    _auto_rename(result, identifier, config, doi=doi, target_dir=target_dir)
                cache_set(identifier, result, config)
                if bibtex:
                    from ..citations.bibtex_crossref import fetch_bibtex
                    result["bibtex"] = fetch_bibtex(doi, config)
                return result

    # Late capture: wait briefly for browser downloads that complete after race timeout,
    # then scan for any PDFs that were saved to disk by racing threads.
    import time as _time
    _time.sleep(2)  # grace period for browser threads to finish writing
    from ..download.pdf import is_pdf_file
    held = Path(preprint_only["result"]["file"]) if preprint_only["result"] else None
    for p in target_dir.glob(f"{safe_filename(identifier)}*.pdf"):
        if held is not None and p == held:
            continue  # the kept preprint is not a late capture of the published PDF
        # Only a whole PDF of this very paper: a racing thread may have left a
        # different paper under this name (Sci-Hub mapped a Ceramics
        # International DOI to a JSTOR essay; it was taken here, 2026-10-17).
        if p.stat().st_size > 5000 and is_pdf_file(p) and not _is_wrong_paper(p, doi, config, "late capture"):
            result = {
                "success": True, "identifier": identifier, "doi": doi,
                "file": str(p), "source": "late_capture",
            }
            cache_set(identifier, result, config)
            if rename:
                _auto_rename(result, identifier, config, doi=doi, target_dir=target_dir)
            if bibtex:
                from ..citations.bibtex_crossref import fetch_bibtex
                result["bibtex"] = fetch_bibtex(identifier, config)
            return result

    # Nothing delivered the published PDF: a preprint of this paper is better
    # than reporting failure, and its file name says what it is.
    preprint = _use_preprint()
    if preprint is not None:
        return preprint

    # What this paper's own browser strategies reported (never another paper's).
    att = _attempt.get(getattr(_DL_TLS, "key", None))
    error_type, error_action = att.last_error if att is not None else ("", "")
    guidance = _build_failure_guidance(doi, config, error_type)

    hint: dict[str, Any] = {"manual_url": f"https://sci-hub.ru/{doi}", "guidance": guidance}

    reason = "no PDF found"
    if error_type in ("paywall", "cloudflare_blocked"):
        from ..desktop.error_text import raw_for
        reason = raw_for(error_type)

    # Add agent hint for paywall - clear instruction on what to do next
    if error_type == "paywall" or error_action == "login_required":
        result = fail(
            identifier,
            reason,
            hint,
            error_type=error_type or "paywall",
            action=error_action or "login_required",
        )
        result["agent_hint"] = (
            f"此论文需要机构登录才能下载。请运行 litloop_login(identifier=\"{doi}\") "
            "打开浏览器让用户登录机构账号，登录后关闭浏览器，然后重试下载。"
        )
    else:
        result = fail(
            identifier,
            reason,
            hint,
            error_type=error_type or "not_found",
            action=error_action or "",
        )
    result["source"] = "none"

    # Surface WHY each channel failed so the user can decide (channels are
    # often only temporarily unreachable; some need a user decision, e.g.
    # Unpaywall requires the user's real email).
    source_failures = race_failures
    if source_failures:
        result["source_failures"] = source_failures
        config_needs = [f for f in source_failures if f.get("error_type") == "config_needed"]
        if config_needs and "agent_hint" not in result:
            channels = ", ".join(sorted({f["source"] for f in config_needs}))
            result["agent_hint"] = (
                f"配置缺失（{channels}）：Unpaywall 等渠道需要用户真实邮箱。"
                '请询问用户的邮箱地址，调用 litloop_config(key="email", value="<用户邮箱>") '
                "后重试下载。"
            )
    return result


# Re-export symbols split into internal modules so callers, tests, and
# monkeypatches against ``litloop.sources`` keep working unchanged.
from ._index import (
    _EXPLICIT_STRATEGIES,
    _auto_rename,
    _files_claimed_by_other_dois,
    _index_entry,
    _index_entry_is_fresh,
    _index_strategy_compatible,
    _norm_path,
    _pdf_mentions_doi,
    _same_dir,
    _update_doi_index,
)
from ._routes import (
    DEFAULT_INSTITUTIONAL_ORDER,
    _INSTITUTIONAL_STEPS,
    _OPEN_HOSTS,
    _STEP_TEXT,
    _build_failure_guidance,
    _build_free_sources,
    _build_institutional_sources,
    _run_institutional_sequence,
    _step_watchdog,
    _webvpn_browser,
    _webvpn_http,
)
from ._batch import (
    _apply_preprint_fallbacks,
    _batch_institutional_phase,
    _clear_progress,
    _close_batch_browsers,
    _get_progress_file,
    _load_progress,
    _progress_lock,
    _save_progress,
    _school_routes_each,
    _write_download_results,
    batch_download,
    institutional_download,
)


try:
    from ..browser import backend as _bb
    _bb.LAUNCH_GUARD = _source_may_continue
    # A browser belongs to the paper the launching thread works for.
    _bb.OWNER = lambda: getattr(_SOURCE_TLS, "doi", None) or getattr(_DL_TLS, "key", None)
except Exception:
    pass
