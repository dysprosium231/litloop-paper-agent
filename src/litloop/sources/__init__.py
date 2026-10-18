"""Source registry and download orchestration with tiered parallel racing."""

from __future__ import annotations

import hashlib
import inspect
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from ..cache import cache_get, cache_set
from ..config import load_config, DATA_DIR
from ..identifiers import is_arxiv_identifier, normalize_doi, safe_filename
from ..log import get_logger
from ..pdf_utils import fail
from ..rename import rename_pdf, generate_filename as rename_pdf_generate_filename

# Import compiled core functions if available (Cython .pyd/.so)
try:
    from .._core.racing import (
        run_parallel_race as _run_parallel_race_compiled,
    )
    _HAS_COMPILED_CORE = True
except ImportError:
    _HAS_COMPILED_CORE = False

log = get_logger()

from .arxiv import try_arxiv
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

# Institutional bridge — uses instsci PaperFetcher when available
def _try_institutional_bridge(doi: str, output_path: Path, config: dict) -> dict | None:
    """Lazy-loaded instsci institutional bridge."""
    try:
        from ..institutional.instsci_bridge import try_institutional
        return try_institutional(doi, output_path, config)
    except ImportError:
        return None

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



def _any_institutional_path(config: dict[str, Any]) -> bool:
    """Check if any institutional access is configured."""
    return bool(
        (config.get("carsi_enabled") and config.get("carsi_idp_name", "").strip())
        or (config.get("vpnsci_enabled") and (config.get("vpnsci_school") or config.get("vpnsci_base_url")))
        or (config.get("ezproxy_enabled") and config.get("ezproxy_login_url"))
        or config.get("elsevier_api_key")
    )


def _get_browser_semaphore(config: dict[str, Any]) -> threading.Semaphore:
    """Get or create the global browser concurrency semaphore."""
    global _browser_semaphore
    max_workers = config.get("max_browser_workers", 1)
    if _browser_semaphore is None:
        with _browser_semaphore_lock:
            if _browser_semaphore is None:
                _browser_semaphore = threading.Semaphore(max_workers)
    return _browser_semaphore
from .carsi_source import try_carsi

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


def _try_source(
    source_fn: Any, doi: str, output_path: Path, config: dict[str, Any], label: str, use_tor: bool = False
) -> dict[str, Any] | None:
    from .scoring import record_result, classify_error, get_user_advice
    t0 = time.time()
    key = normalize_doi(doi) if not is_arxiv_identifier(doi) else doi
    _SOURCE_TLS.doi = key
    result = None
    try:
        result = _try_source_inner(source_fn, doi, output_path, config, label, use_tor, t0,
                                   record_result, classify_error, get_user_advice)
    finally:
        settled = _SOURCE_TLS.doi in _SETTLED
        _SOURCE_TLS.doi = None
        ok = bool(result and result.get("success"))
        log.info(f"   [timing] {label}: {time.time() - t0:.1f}s, {'PDF' if ok else 'no PDF'}")
        att = _attempt.get(key)
        if att is not None:
            from .._publisher_strategies_core import get_last_error
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
        from .._publisher_strategies_core import _set_error
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
                    from ..pdf_utils import is_suspicious_pdf, suspicious_pdf
                    fp = Path(file_path)
                    if fp.exists() and is_suspicious_pdf(fp, doi):
                        log.info(f"   SUSPICIOUS {label}: PDF appears to be a preview/cover page, not full text")
                        record_result(label, False, latency_ms, "suspicious_pdf")
                        return suspicious_pdf(doi, fp, label)
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
        from ..pdf_utils import is_pdf_file as _is_pdf
        if output_path.exists() and _is_pdf(output_path):
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


def _run_tier(
    tier_sources: list[tuple[Any, str]],
    tier_label: str,
    timeout_sec: int,
    doi: str,
    target_dir: Path,
    output_path: Path,
    config: dict[str, Any],
    use_tor: bool = False,
) -> dict[str, Any] | None:
    if not tier_sources:
        return None
    _SETTLED.discard(normalize_doi(doi) if not is_arxiv_identifier(doi) else doi)  # see _run_tiers_parallel

    if len(tier_sources) == 1:
        fn, label = tier_sources[0]
        log.info(f"   [{tier_label}] Racing 1 sources...")
        src_output = target_dir / f"{safe_filename(doi)}_{label}.pdf"
        try:
            result = _try_source(fn, doi, src_output, config, label, use_tor=use_tor)
            if result and result.get("success"):
                final_path = Path(result.get("file", ""))
                if final_path != output_path and final_path.exists():
                    output_path.parent.mkdir(parents=True, exist_ok=True)
                    if output_path.exists():
                        output_path.unlink()
                    final_path.rename(output_path)
                    result["file"] = str(output_path)
                log.info(f"   OK {label}")
                return result
            else:
                log.info(f"   FAIL {label}")
                if src_output.exists():
                    try:
                        src_output.unlink(missing_ok=True)
                    except OSError:
                        pass
        except Exception:
            log.info(f"   FAIL {label}")
        return None

    log.info(f"   [{tier_label}] Racing {len(tier_sources)} sources...")
    with ThreadPoolExecutor(max_workers=len(tier_sources)) as pool:
        futures = {}
        for fn, label in tier_sources:
            src_output = target_dir / f"{safe_filename(doi)}_{label}.pdf"
            futures[pool.submit(_try_source, fn, doi, src_output, config, label, use_tor)] = (label, src_output)
        try:
            for future in as_completed(futures, timeout=timeout_sec):
                label, src_output = futures[future]
                try:
                    result = future.result(timeout=1)
                except Exception:
                    result = None
                if result and result.get("success"):
                    final_path = Path(result.get("file", ""))
                    if final_path != output_path and final_path.exists():
                        output_path.parent.mkdir(parents=True, exist_ok=True)
                        if output_path.exists():
                            output_path.unlink()
                        final_path.rename(output_path)
                        result["file"] = str(output_path)
                    for _, other_path in futures.values():
                        if other_path != output_path and other_path.exists():
                            try:
                                other_path.unlink(missing_ok=True)
                            except OSError:
                                pass
                    log.info(f"   OK {label}")
                    return result
                else:
                    log.info(f"   FAIL {label}")
                    if src_output.exists():
                        try:
                            src_output.unlink(missing_ok=True)
                        except OSError:
                            pass
        except TimeoutError:
            log.info(f"   [{tier_label}] Timeout after {timeout_sec}s")
    return None


def _build_free_sources(doi: str, config: dict[str, Any]) -> list[tuple[Any, str]]:
    """Build Phase 1 sources: all free/OA/grey sources, sorted by adaptive score.
    
    Respects config['download_strategy']:
      - fastest:     all sources, EMA-sorted (default)
      - scihub_first: all sources, but grey sources sorted first
      - scihub_only:  only SciBban + Sci-Hub + LibGen
      - oa_first:     all sources, but OA sources sorted before grey
      - legal_only:   exclude grey sources (SciBban, Sci-Hub, LibGen)
    """
    from .scoring import sort_sources

    strategy = config.get("download_strategy", "fastest")

    publisher_fast = get_publisher_fast_sources(doi)
    _fast_names = {label for _, label in publisher_fast}

    extra_fast = []
    for fn, label in [
        (try_unpaywall, "Unpaywall"),
        (try_openalex_oa, "OpenAlexOA"),
        (try_semanticscholar, "SemanticScholar"),
        (try_openaire, "OpenAIRE"),
    ]:
        if label not in _fast_names:
            extra_fast.append((fn, label))

    from .elsevier_api import fulltext_enabled
    if not fulltext_enabled(config):  # off in settings, or the key has no full-text entitlement
        publisher_fast = [(fn, label) for fn, label in publisher_fast if label != "ElsevierAPI"]
    legal_sources = publisher_fast + extra_fast
    # Every publisher's own site is tried here, in phase 1, from this exit:
    # those without a browser lane of their own (PNAS, Optica, World Scientific,
    # unlisted ones) get the generic one. Open-access hosts need none.
    if not any("Browser" in label for label in _fast_names):
        from .publishers import get_publisher
        if get_publisher(doi) not in _OPEN_HOSTS:
            from .publishers import _FN_MAP
            legal_sources.append((_FN_MAP["GenericBrowser"], "GenericBrowser"))
    # Publishers whose site is not worth phase 1 (publisher_site_skip_prefixes):
    # the school routes reach the same content faster.
    skip = tuple(str(p).strip().lower().rstrip("/") + "/" for p in config.get("publisher_site_skip_prefixes") or [] if p)
    if skip and doi.lower().startswith(skip):
        legal_sources = [(fn, label) for fn, label in legal_sources if "Browser" not in label]
        log.info("   Publisher site skipped in phase 1 (publisher_site_skip_prefixes)")
    legal_sources += [(try_doaj, "DOAJ"), (try_crossref_page_scrape, "CrossrefPage")]
    legal_sources += [(try_europepmc, "EuropePMC"), (try_core, "CORE")]
    if not config.get("_batch"):
        legal_sources.append((try_pmc, "PMC"))

    if config.get("openalex_api_key"):
        legal_sources.append((try_openalex_content_api, "OpenAlexContent"))

    # Grey / shadow-library sources (Sci-Hub, SciBban, LibGen) are all gated
    # by scihub_enabled. The fallback mirrors config.DEFAULT_CONFIG (True):
    # grey sources are a core, default-on capability and a partial config
    # dict must not silently disable them. Opt-out is explicit
    # (scihub_enabled=false / strategy=legal_only).
    grey_sources: list[tuple[Any, str]] = []
    if config.get("scihub_enabled", True):
        from .scihub import recently_absent, too_new
        new = too_new(doi, config)  # published in or after scihub_skip_from_year
        if not new:
            grey_sources.append((try_scibban, "SciBban"))  # a Sci-Hub mirror
        grey_sources.append((try_libgen, "LibGen"))
        if not new and not recently_absent(doi):  # Sci-Hub said within the hour it does not have it
            grey_sources.append((try_scihub, "Sci-Hub"))

    if strategy == "scihub_only":
        # Only Sci-Hub (not SciBban, not LibGen)
        return sort_sources([(try_scihub, "Sci-Hub")]) if config.get("scihub_enabled", True) else []
    elif strategy == "grey_only":
        return sort_sources(grey_sources)
    elif strategy == "legal_only":
        return sort_sources(legal_sources)
    elif strategy == "scihub_first":
        # Grey sources first, then legal
        return sort_sources(grey_sources) + sort_sources(legal_sources)
    elif strategy == "oa_first":
        # Legal sources first, then grey
        return sort_sources(legal_sources) + grey_sources
    else:
        # fastest: all sources EMA-sorted
        return sort_sources(legal_sources + grey_sources)


# Publishers whose papers are open: no site visit from phase 1 is needed.
_OPEN_HOSTS = frozenset({"MDPI", "arXiv", "bioRxiv", "PLOS", "Frontiers", "BMC", "Beilstein", "EMBO",
                         "Research Square", "chemRxiv", "Preprints.org"})


def _webvpn_http(doi: str, output_path: Path, config: dict[str, Any]) -> dict[str, Any] | None:
    """WebVPN without a browser: the saved session over HTTP (and a campus
    connector's SOCKS5 when one is configured). Asks the user nothing."""
    from . import instsci
    if instsci._is_campus_connector_mode(config):
        result = instsci._try_instsci_socks5(doi, output_path, config)
        if result:
            return result
    if not instsci.instsci_cookie_path(config).exists():
        return None
    return instsci._try_instsci_http(doi, output_path, config)


def _webvpn_browser(doi: str, output_path: Path, config: dict[str, Any]) -> dict[str, Any] | None:
    from . import instsci
    return instsci._try_instsci_browser(doi, output_path, config)


# The school's routes, tried one after another (institutional_order): the ones
# that ask the user nothing first, then those that may need a login, so at most
# one window waits for the user at a time. Each step: (enabled?, function, label);
# the label decides whether it takes the browser slot (_BROWSER_SOURCE_LABELS).
_INSTITUTIONAL_STEPS: dict[str, tuple[Any, Any, str]] = {
    "webvpn_http": (lambda c: bool(c.get("vpnsci_enabled")), _webvpn_http, "WebVPN-HTTP"),
    "webvpn_browser": (lambda c: bool(c.get("vpnsci_enabled")), _webvpn_browser, "WebVPN"),
    "carsi": (lambda c: bool(c.get("carsi_enabled") and str(c.get("carsi_idp_name") or "").strip()),
              lambda d, o, c: try_carsi(d, o, c), "CARSI"),
    "ezproxy": (lambda c: bool(c.get("ezproxy_enabled")), lambda d, o, c: try_ezproxy(d, o, c), "EZProxy"),
}
DEFAULT_INSTITUTIONAL_ORDER = ["webvpn_http", "webvpn_browser", "carsi", "ezproxy"]


def _build_institutional_sources(doi: str, config: dict[str, Any], *, use_vpnsci: bool = False) -> list[tuple[Any, str]]:
    """Phase 2: the school's routes that are switched on, in institutional_order.

    WebVPN follows its setting (use_vpnsci is kept for callers; a single
    download used to leave WebVPN out unless asked). The publisher's own site
    is not here: phase 1 opened it from this same exit.
    """
    order = config.get("institutional_order") or DEFAULT_INSTITUTIONAL_ORDER
    out = []
    for name in order:
        step = _INSTITUTIONAL_STEPS.get(str(name))
        if step and step[0](config):
            out.append((step[1], step[2]))
    return out


def _run_institutional_sequence(steps: list[tuple[Any, str]], doi: str, target_dir: Path, output_path: Path,
                                config: dict[str, Any], failures: list[dict[str, str]] | None = None,
                                ) -> dict[str, Any] | None:
    """Try the school's routes one at a time; the first PDF wins."""
    from .. import progress_reporter
    log.info(f"   School routes in turn: {', '.join(label for _, label in steps)}")
    t_seq = time.time()
    for fn, label in steps:
        if _cancelled():
            return None
        progress_reporter.item_stage(doi, f"正在走学校通道：{_STEP_TEXT.get(label, label)}")
        src_output = target_dir / f"{safe_filename(doi)}_{label}.pdf"
        done = threading.Event()
        threading.Thread(target=_step_watchdog, args=(doi, label, done, config), daemon=True).start()
        try:
            result = _try_source(fn, doi, src_output, config, label)
        finally:
            done.set()
        if result and result.get("success"):
            final_path = Path(result.get("file", ""))
            if final_path != output_path and final_path.exists():
                if output_path.exists():
                    output_path.unlink()
                final_path.rename(output_path)
                result["file"] = str(output_path)
            log.info(f"   OK {label}")
            log.info(f"   [timing] school routes: {time.time() - t_seq:.1f}s")
            return result
        if failures is not None:
            _record_source_failure(failures, label, result or {"success": False, "error": "no PDF"})
        if src_output.exists():
            src_output.unlink(missing_ok=True)
    log.info(f"   [timing] school routes: {time.time() - t_seq:.1f}s, none had it")
    return None


def _step_watchdog(doi: str, label: str, done: threading.Event, config: dict[str, Any]) -> None:
    """End a school route that has gone quiet: after institutional_step_seconds
    without a result (a login the user is doing keeps it alive), kill the
    browsers it opened, so its blocked browser calls fail and the next route
    runs. 2026-10-17: a WebVPN browser step hung over 10 minutes on a page
    that never finished loading, with no timeout of its own firing."""
    from ..browser_backend import close_browsers_since
    limit = float(config.get("institutional_step_seconds", 240) or 240)
    since = time.perf_counter()  # close_browsers_since's clock
    deadline = time.time() + limit
    while not done.wait(5):
        if _user_waiting_for(doi):
            deadline = max(deadline, time.time() + 60)  # the user is at it: give them time
            continue
        if time.time() >= deadline:
            n = close_browsers_since(since, owner=doi)
            log.info(f"   [timing] {label}: nothing after {limit:.0f}s; closed {n} browser(s), next route")
            return


_STEP_TEXT = {"WebVPN-HTTP": "WebVPN 直接下载", "WebVPN": "WebVPN 浏览器", "CARSI": "CARSI", "EZProxy": "EZProxy"}


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
    # Delegate to the compiled racing engine only for the flat race: the
    # hedged cascade needs staggered submission, which the compiled engine
    # does not model.
    race_mode = str(config.get("race_mode", "hedge")).lower()
    if _HAS_COMPILED_CORE and race_mode == "full":
        all_sources = []
        for tier_sources, tier_label, tier_timeout in tiers:
            for fn, label in tier_sources:
                all_sources.append((fn, label, tier_label, tier_timeout))

        def _try_source_recorded(source_fn, doi_, out_path, config_, label_,
                                 use_tor=False):
            # Same recording contract as the pure-Python race below: the
            # compiled engine only manages threading, it still calls back
            # into _try_source for every lane (with use_tor as a keyword —
            # see racing.pyx).
            result = _try_source(source_fn, doi_, out_path, config_, label_,
                                 use_tor=use_tor)
            _record_source_failure(failures, label_, result)
            return result

        return _run_parallel_race_compiled(
            all_sources, doi, target_dir, output_path, config,
            use_tor, overall_timeout, _try_source_recorded, safe_filename, log,
        )
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
                from ..browser_engine import shutdown_shared_browser
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
            with result_lock:
                if shared_result["result"] is None:
                    shared_result["result"] = (result, label, src_output)
                    cancel_event.set()
                    success_event.set()
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
    # flat all-at-once race (compiled engine eligible again).
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
            log.info("   All sources failed")
            return None

        # Final scan: check if any source wrote a valid PDF file despite timeout
        from ..pdf_utils import is_pdf_file, is_suspicious_pdf, suspicious_pdf
        for label, src_output in futures.values():
            if src_output.exists() and is_pdf_file(src_output):
                if is_suspicious_pdf(src_output, doi):
                    log.info(f"   SUSPICIOUS {label} (file scan): skipping suspicious PDF")
                    continue
                output_path.parent.mkdir(parents=True, exist_ok=True)
                if output_path.exists():
                    output_path.unlink()
                src_output.rename(output_path)
                log.info(f"   OK {label} (file scan)")
                return {"success": True, "identifier": doi, "doi": doi,
                        "file": str(output_path), "source": label}

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


#: Strategies that encode an explicit user source preference. For these, a
#: cached file obtained through a different strategy is treated as a miss so
#: the requested channel is actually used; the default strategies reuse cache.
_EXPLICIT_STRATEGIES = ("scihub_only", "legal_only", "scihub_first")


def _index_entry(entry: Any) -> dict[str, Any] | None:
    """Normalize a .doi_index entry to a record dict.

    Legacy ``str -> path`` entries have no provenance and no timestamp; they
    are treated as fresh (ts=None) and upgraded on the next cache hit.
    """
    if isinstance(entry, str):
        return {"file": entry, "source": "", "strategy": "", "ts": None}
    if isinstance(entry, dict):
        ts = entry.get("ts")
        try:
            ts_value = float(ts) if ts is not None else None
        except (TypeError, ValueError):
            ts_value = None
        return {
            "file": str(entry.get("file") or ""),
            "source": str(entry.get("source") or ""),
            "strategy": str(entry.get("strategy") or ""),
            "ts": ts_value,
        }
    return None


def _index_entry_is_fresh(entry: dict[str, Any], config: dict[str, Any]) -> bool:
    """Apply the cache TTL (cache_ttl_hours) to an index entry."""
    try:
        ttl_hours = float(config.get("cache_ttl_hours", 168))
    except (TypeError, ValueError):
        ttl_hours = 168.0
    if ttl_hours <= 0 or entry.get("ts") is None:
        return True
    return (time.time() - entry["ts"]) <= ttl_hours * 3600


def _index_strategy_compatible(entry: dict[str, Any], config: dict[str, Any]) -> bool:
    """Honor an explicitly requested download strategy over cached files."""
    current = str(config.get("download_strategy") or "fastest").strip()
    if current not in _EXPLICIT_STRATEGIES:
        return True
    recorded = str(entry.get("strategy") or "").strip()
    return recorded == current


def _norm_path(p: Path | str) -> str:
    return str(Path(p).resolve()).lower()


def _files_claimed_by_other_dois(target_dir: Path, doi: str) -> set[str]:
    """Files the DOI index already assigns to a different DOI."""
    try:
        idx = json.loads((target_dir / ".doi_index.json").read_text(encoding="utf-8"))
    except Exception:
        return set()
    claimed = set()
    for key, raw in idx.items():
        entry = _index_entry(raw)
        if key != doi and entry and entry.get("file"):
            claimed.add(_norm_path(entry["file"]))
    return claimed


def _pdf_mentions_doi(path: Path, doi: str, pages: int = 3) -> bool:
    """True when the DOI appears in the first pages' text (publisher PDFs print it)."""
    try:
        import pymupdf
        with pymupdf.open(path) as pdf:
            text = "".join(pdf[i].get_text() for i in range(min(pages, pdf.page_count)))
    except Exception:
        return False
    flat = "".join(text.lower().split())
    return doi.lower() in flat


def _update_doi_index(
    target_dir: Path,
    doi: str,
    file_path: Path,
    *,
    source: str = "",
    strategy: str = "",
    config: dict[str, Any] | None = None,
) -> None:
    """Update the DOI→file index for dedup.

    Records carry provenance (``source``/``strategy``) and a timestamp so
    stale or channel-mismatched entries can be invalidated by TTL and by
    explicit strategy changes instead of manual ``rm``. Existing entries keep
    their recorded source/strategy when the caller does not supply new ones
    (e.g. a rename that just moves the file).
    """
    doi_index = target_dir / ".doi_index.json"
    try:
        idx: dict[str, Any] = {}
        if doi_index.exists():
            idx = json.loads(doi_index.read_text(encoding="utf-8"))
        existing = _index_entry(idx.get(doi)) if doi in idx else None
        if existing:
            source = source or existing["source"]
            strategy = strategy or existing["strategy"]
        idx[doi] = {
            "file": str(file_path),
            "source": source or "",
            "strategy": strategy or (config or {}).get("download_strategy", "") or "",
            "ts": time.time(),
        }
        doi_index.write_text(json.dumps(idx, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def _auto_rename(result: dict[str, Any], identifier: str, config: dict[str, Any], doi: str = "", target_dir: Path | None = None) -> None:
    """Auto-rename downloaded PDF based on metadata."""
    from ..rename import rename_format
    if rename_format(config) == "off":
        return
    file_path = Path(result.get("file", ""))
    if not file_path.exists():
        return
    if file_path.suffix.lower() != ".pdf":
        # Non-PDF artifacts (e.g. the ElsevierAPI text/plain full text) keep
        # their own extension — rename_pdf would force a .pdf name onto them.
        log.info(f"   Non-PDF artifact ({file_path.suffix or 'no ext'}) — skipping auto-rename")
        return
    # Use cached metadata or fetch from Crossref
    from ..citation import fetch_metadata
    _doi = doi or result.get("doi", identifier)
    metadata = fetch_metadata(_doi, config)
    if metadata:
        new_path = rename_pdf(file_path, metadata, doi=_doi, fmt=rename_format(config))
        if new_path and result.get("whole_book") and "整本书" not in new_path.stem:
            # A chapter delivered as its whole book says so in its name.
            tagged = new_path.with_name(f"{new_path.stem}（整本书）{new_path.suffix}")
            try:
                new_path.rename(tagged)
                new_path = tagged
            except OSError:
                pass
        if new_path and new_path != file_path:
            result["file"] = str(new_path)
            result["renamed"] = True
            log.info(f"   Renamed: {file_path.name} -> {new_path.name}")
            # Racing leaves one "<doi>_<Source>.pdf" copy per source that also
            # succeeded; the winner is now saved under its final name.
            if _doi:
                removed = 0
                for dup in new_path.parent.glob(f"{safe_filename(_doi)}_*.pdf"):
                    if dup != new_path:
                        try:
                            dup.unlink()
                            removed += 1
                        except OSError:
                            pass
                if removed:
                    log.info(f"   Removed {removed} duplicate source cop{'y' if removed == 1 else 'ies'}")
            # Update DOI→file index for dedup
            if target_dir and _doi:
                _update_doi_index(
                    target_dir,
                    _doi,
                    new_path,
                    source=result.get("source") or "",
                    strategy=config.get("download_strategy", ""),
                    config=config,
                )
        else:
            log.info(f"   Kept original name: {file_path.name}")
    else:
        log.info(f"   No metadata for rename, keeping: {file_path.name}")


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
    "verify": "请在弹出的浏览器窗口中完成人机验证（出现勾选框时点击它）",
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
            from .. import progress_reporter as pr
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
        from .. import progress_reporter as _pr
        _pr.clear_attention(f"wait:{key}")
        from ..browser_backend import close_browsers_since
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
        from ..browser_backend import close_browsers_since
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
        from ..browser_backend import current_owner
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
) -> dict[str, Any]:
    key = identifier.strip()
    if not is_arxiv_identifier(key):
        key = normalize_doi(key)
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
            from .._publisher_strategies_core import clear_no_access
            clear_no_access(key)
        return result
    finally:
        _DL_TLS.key = prev_key
        try:  # whatever this paper waited on the user for is over now
            from .. import progress_reporter as _pr
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
            from ..browser_backend import close_browsers_since
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
        if cached_file.exists():
            cached["cached"] = True
            if bibtex:
                from ..bibtex import fetch_bibtex
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

    from ..citation import fetch_metadata
    metadata = fetch_metadata(doi, config)
    if metadata:
        from ..rename import rename_format
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
                from ..bibtex import fetch_bibtex
                result["bibtex"] = fetch_bibtex(identifier, config)
            return result
        return fail(identifier, "arXiv PDF not available")

    doi = normalize_doi(identifier)

    # Phase 1: Free sources (OA + grey) — parallel race
    t_phase = time.time()
    p_phase = time.perf_counter()  # close_browsers_since's clock
    free_sources = _build_free_sources(doi, config)
    log.info(f"   [timing] phase 1 sources built: {time.time() - t_phase:.1f}s")
    if free_sources:
        result = _run_tiers_parallel(
            [(free_sources, "Free", 15)], doi, target_dir, output_path, config, use_tor, 15,
            failures=race_failures,
        )
        log.info(f"   [timing] phase 1 (free sources and publisher site): {time.time() - t_phase:.1f}s, "
                 f"{'PDF' if result else 'no PDF'}")
        if result:
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
                from ..bibtex import fetch_bibtex
                result["bibtex"] = fetch_bibtex(doi, config)
            return result

    # Phase 2: Institutional access — only when Phase 1 failed
    # Skip institutional fallback for grey_only/scihub_only strategy
    from .._publisher_strategies_core import no_access
    if _institutional and no_access(doi) and not config.get("school_routes_after_publisher_login", False):
        log.info("   Logged into the publisher as the school and still no full text: skipping the school routes")
        return fail(identifier, "the school does not subscribe: logged into the publisher with the school's identity, still no full text",
                    error_type="not_entitled", action="no_subscription")
    if _institutional and config.get("download_strategy") not in ("scihub_only", "grey_only") and not _cancelled():
        # Phase 1's lanes that are still at work (waived) keep their browsers and
        # the browser slot: on 2026-10-18 a stuck publisher-site lane held it
        # for 16 minutes and the WebVPN step queued 697 s behind it. Phase 1 is
        # over, so close what it opened before the school routes need a browser.
        try:
            from ..browser_backend import close_browsers_since
            n = close_browsers_since(p_phase, owner=getattr(_DL_TLS, "key", None) or doi)
            if n:
                log.info(f"   Closed {n} browser(s) phase 1 left open")
        except Exception as exc:
            log.info(f"   Phase 1 browser cleanup skipped: {exc}")
        # Session self-healing: validate and refresh institutional sessions
        # before racing them (WebVPN only; CARSI self-heals inside login()).
        t_heal = time.time()
        try:
            from ..session_heal import ensure_institutional_sessions
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
            if result:
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
                    from ..bibtex import fetch_bibtex
                    result["bibtex"] = fetch_bibtex(doi, config)
                return result

    # Late capture: wait briefly for browser downloads that complete after race timeout,
    # then scan for any PDFs that were saved to disk by racing threads.
    import time as _time
    _time.sleep(2)  # grace period for browser threads to finish writing
    for p in target_dir.glob(f"{safe_filename(identifier)}*.pdf"):
        if p.stat().st_size > 5000:
            result = {
                "success": True, "identifier": identifier, "doi": doi,
                "file": str(p), "source": "late_capture",
            }
            cache_set(identifier, result, config)
            if rename:
                _auto_rename(result, identifier, config, doi=doi, target_dir=target_dir)
            if bibtex:
                from ..bibtex import fetch_bibtex
                result["bibtex"] = fetch_bibtex(identifier, config)
            return result

    # What this paper's own browser strategies reported (never another paper's).
    att = _attempt.get(getattr(_DL_TLS, "key", None))
    error_type, error_action = att.last_error if att is not None else ("", "")
    guidance = _build_failure_guidance(doi, config, error_type)

    hint: dict[str, Any] = {"manual_url": f"https://sci-hub.ru/{doi}", "guidance": guidance}

    reason = "no PDF found"
    if error_type in ("paywall", "cloudflare_blocked"):
        from ..error_text import raw_for
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


def _build_failure_guidance(doi: str, config: dict[str, Any], error_type: str = "") -> list[str]:
    """Build actionable guidance when all download sources fail.

    error_type: what this paper's browser strategies reported (its DownloadAttempt).
    """
    import os
    tips = []

    if error_type == "paywall":
        tips.append("此论文需要机构订阅才能下载")
        tips.append(f"→ 运行 litloop_login(identifier=\"{doi}\") 打开浏览器登录")
        tips.append("→ 在浏览器中点击 'Access through your institution' 选择你的机构")
        tips.append("→ 登录后关闭浏览器，cookies 自动保存")
        tips.append("→ 重新运行下载命令即可")
    elif error_type == "cloudflare_blocked":
        tips.append("Cloudflare 防护阻止了访问")
        tips.append("→ 安装 CloakBrowser (pip install cloakbrowser) 绕过反爬")
        tips.append("→ 或配置代理: litloop config_set network_proxy \"socks5://127.0.0.1:1080\"")
    elif error_type == "browser_unavailable":
        tips.append("CloakBrowser 不可用，无法使用浏览器下载策略")
        tips.append("→ 运行: pip install cloakbrowser")

    # Check litloop proxy config
    cfg_proxy = config.get("network_proxy", "")
    env_proxy = os.environ.get("LITLOOP_PROXY", "")

    if cfg_proxy or env_proxy:
        active = env_proxy or cfg_proxy
        tips.append(f"当前代理: {active} — 如果 Sci-Hub 仍不通，尝试更换代理地址")
    else:
        # Check if system has proxy that litloop ignores
        sys_proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY") or ""
        if sys_proxy:
            tips.append(f"检测到系统代理 {sys_proxy}，但 litloop 未使用")
            tips.append(f"→ 运行: litloop config_set network_proxy \"{sys_proxy}\"")
        else:
            tips.append("未配置代理 — 如果网络受限，运行: litloop config_set network_proxy \"socks5://127.0.0.1:1080\"")

    # Check Tor
    try:
        from ..tor import check_tor_circuit
        if not check_tor_circuit(config):
            tips.append("Tor 未运行 → 运行: litloop tor_start（匿名访问 Sci-Hub/LibGen）")
    except Exception:
        pass

    # Check WebVPN
    if not config.get("vpnsci_enabled"):
        tips.append("有高校账号？运行: litloop config_set vpnsci_enabled true")

    # Manual fallback
    tips.append(f"手动下载: https://sci-hub.ru/{doi}")

    # Network diagnostic
    tips.append("运行网络诊断: litloop network_diagnose")

    return tips


def _get_progress_file(batch_id: str) -> Path:
    """Get path to batch progress file."""
    return DATA_DIR / "batch_progress" / f"{batch_id}.jsonl"


_progress_lock = threading.Lock()


def _save_progress(batch_id: str, identifier: str, result: dict[str, Any]) -> None:
    """Append a single result to the progress file (thread-safe)."""
    progress_file = _get_progress_file(batch_id)
    progress_file.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "identifier": identifier,
        "success": result.get("success", False),
        "source": result.get("source", "none"),
        "file": result.get("file", ""),
        "doi": result.get("doi", ""),
    }
    line = json.dumps(entry, ensure_ascii=False) + "\n"
    with _progress_lock:
        with progress_file.open("a", encoding="utf-8") as f:
            f.write(line)


def _load_progress(batch_id: str) -> dict[str, dict[str, Any]]:
    """Load completed results from progress file. Returns {identifier: result}."""
    progress_file = _get_progress_file(batch_id)
    completed: dict[str, dict[str, Any]] = {}
    if not progress_file.exists():
        return completed
    try:
        with progress_file.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                entry = json.loads(line)
                ident = entry.get("identifier", "")
                if ident and entry.get("success"):
                    completed[ident] = entry
    except Exception:
        pass
    return completed


def _clear_progress(batch_id: str) -> None:
    """Remove progress file after successful completion."""
    progress_file = _get_progress_file(batch_id)
    if progress_file.exists():
        try:
            progress_file.unlink()
        except OSError:
            pass


def _publisher_result_to_standard(
    result: Any, doi: str, output_dir: Path, publisher_name: str
) -> dict[str, Any]:
    """Convert a PublisherBatchDownloader DownloadResult to the standard result format."""
    if result.ok and result.pdf_path:
        src = Path(result.pdf_path)
        dst = output_dir / f"{safe_filename(doi)}.pdf"
        try:
            if src != dst:
                output_dir.mkdir(parents=True, exist_ok=True)
                if dst.exists():
                    dst.unlink()
                src.rename(dst)
        except OSError:
            dst = src
        return {
            "success": True, "doi": doi, "identifier": doi,
            "file": str(dst), "source": f"institutional:browser:{publisher_name}",
            "full_text_length": result.text_length,
        }
    return fail(doi, result.reason or "institutional download failed")


def _batch_institutional_phase(
    failed_dois: list[str],
    output_dir: Path,
    config: dict[str, Any],
    batch_id: str,
    results_map: dict[str, dict[str, Any]],
) -> None:
    """Phase 2 for batch downloads: group DOIs by publisher, batch-download per group.

    Modifies results_map in-place with successful results.
    """
    try:
        from ..publisher_profiles import infer_publisher_profile
        from ..publisher_batch import DownloadResult, PaperRecord, PublisherBatchDownloader
    except ImportError:
        log.info("   [Batch] publisher_batch not available, skipping institutional phase")
        return

    try:
        from ..browser_backend import launch_persistent_context  # noqa: F401
        from ..browser_backend import is_available as _browser_backend_available
        if not _browser_backend_available():
            log.info("   [Batch] no browser backend available, skipping institutional phase")
            return
    except ImportError:
        log.info("   [Batch] no browser backend available, skipping institutional phase")
        return

    # Group DOIs by publisher profile
    grouped: dict[str, list[str]] = {}  # profile_name -> [doi, ...]
    ungrouped: list[str] = []
    for doi in failed_dois:
        profile = infer_publisher_profile(doi)
        if profile:
            grouped.setdefault(profile.name, []).append(doi)
        else:
            ungrouped.append(doi)

    if not grouped and not ungrouped:
        return

    institution_query = config.get("vpnsci_school", "")
    login_timeout = config.get("browser_login_timeout", 300)

    # Process each publisher group
    for profile_name, dois in grouped.items():
        profile = infer_publisher_profile(dois[0])
        if not profile:
            ungrouped.extend(dois)
            continue

        log.info(f"   [Batch] Phase 2: {len(dois)} DOIs via {profile_name} (one login)")

        records = [PaperRecord(doi=doi) for doi in dois]
        run_dir = output_dir / ".publisher_runs" / profile_name
        run_dir.mkdir(parents=True, exist_ok=True)

        downloader = PublisherBatchDownloader(
            config=config,
            profile=profile,
            institution_query=institution_query,
            login_timeout_sec=login_timeout,
            pdf_timeout_sec=60,
            post_login_hold_sec=config.get("post_login_hold", 0),
            post_run_hold_sec=0,
        )

        try:
            summary = downloader.run_records(records, run_dir, retry_failed=True)
        except Exception as e:
            log.info(f"   [Batch] {profile_name} batch failed: {e}")
            ungrouped.extend(dois)
            continue

        # Map DOIs to results from the manifest
        manifest_path = run_dir / "complete" / "manifest.json"
        if manifest_path.exists():
            try:
                import json
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                doi_to_manifest = {entry.get("doi", ""): entry for entry in manifest}
                for doi in dois:
                    entry = doi_to_manifest.get(doi, {})
                    if entry.get("status") == "success":
                        pdf_path = entry.get("pdf_path", "")
                        if pdf_path and Path(pdf_path).exists():
                            result = _publisher_result_to_standard(
                                type("R", (), {"ok": True, "pdf_path": pdf_path, "text_length": 0})(),
                                doi, output_dir, profile_name,
                            )
                            # Phase 1 renames inside download(); phase-2 files
                            # were left as <doi>.pdf.
                            _auto_rename(result, doi, config, doi=doi, target_dir=output_dir)
                        else:
                            result = fail(doi, "PDF not found after batch download")
                    else:
                        result = fail(doi, entry.get("reason", "institutional download failed"))
                    results_map[doi] = result
                    _save_progress(batch_id, doi, result)
            except Exception as e:
                log.info(f"   [Batch] Failed to parse manifest for {profile_name}: {e}")
                ungrouped.extend(dois)
        else:
            log.info(f"   [Batch] No manifest found for {profile_name}")
            ungrouped.extend(dois)

    # Fallback: ungrouped DOIs get per-DOI institutional access
    for doi in ungrouped:
        if doi in results_map and results_map[doi].get("success"):
            continue
        # Check if a PDF already exists on disk (from prior run, doi_index, or partial download)
        safe_name = safe_filename(doi)
        existing = None
        for p in output_dir.glob(f"{safe_name}*.pdf"):
            if p.stat().st_size > 5000:
                existing = p
                break
        if existing:
            result = {
                "success": True, "doi": doi, "identifier": doi,
                "file": str(existing), "source": "local_cache",
            }
            results_map[doi] = result
            _save_progress(batch_id, doi, result)
            continue
        log.info(f"   [Batch] Phase 2 fallback: {doi}")
        result = download(doi, output_dir, scihub_enabled=config.get("scihub_enabled", True), use_vpnsci=True, _institutional=True, _batch=True)
        results_map[doi] = result
        _save_progress(batch_id, doi, result)


def _write_download_results(results: list[dict[str, Any]], output_dir: str | Path | None, **extra: Any) -> None:
    """Persist batch outcomes as download_results.json for scansci-find reconcile."""
    try:
        out = Path(output_dir) if output_dir else Path(load_config().get("output_dir", "."))
        out.mkdir(parents=True, exist_ok=True)
        report: dict[str, Any] = {
            "service": "litloop",
            "total": len(results),
            "succeeded": sum(1 for r in results if r and r.get("success")),
            "entries": [
                {
                    "identifier": r.get("identifier") or r.get("doi") or "",
                    "doi": r.get("doi") or "",
                    "success": bool(r.get("success")),
                    "source": r.get("source") or "",
                    "file": r.get("file") or "",
                    "cached": bool(r.get("cached")),
                    "preprint_fallback": r.get("preprint_fallback") or "",
                    "error_type": r.get("error_type") or "",
                    "reason": r.get("reason") or r.get("error") or "",
                }
                for r in results
                if r
            ],
        }
        report.update(extra)
        target = out / "download_results.json"
        tmp = out / "download_results.json.tmp"
        tmp.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(target)
    except Exception:
        pass


def _apply_preprint_fallbacks(
    fallbacks: dict[str, list[str]],
    identifiers: list[str],
    final_map: dict[str, dict[str, Any]],
    batch_id: str,
    *,
    output_dir: str | Path | None,
    scihub_enabled: bool | None,
    use_tor: bool,
    use_vpnsci: bool,
) -> None:
    """Retry failed DOIs with their matched preprint arXiv IDs, in place.

    Updates ``final_map`` (and the batch progress file) when a fallback
    download succeeds so downstream result aggregation picks it up.
    """
    for ident in [i for i in identifiers if not (final_map.get(i) or {}).get("success")]:
        for fallback_id in fallbacks.get(ident, []):
            log.info(f"   [Fallback] {ident} -> preprint {fallback_id}")
            fb_result = download(
                fallback_id, output_dir,
                scihub_enabled=scihub_enabled, use_tor=use_tor, use_vpnsci=False, _institutional=False, _batch=True,
            )
            if fb_result and fb_result.get("success"):
                fb_result["identifier"] = ident
                fb_result["doi"] = ident
                fb_result["preprint_fallback"] = fallback_id
                _save_progress(batch_id, ident, fb_result)
                final_map[ident] = fb_result
                break


def institutional_download(
    identifier: str,
    output_dir: str | Path | None = None,
    *,
    use_vpnsci: bool = False,
    heal: bool = True,
) -> dict[str, Any] | None:
    """Phase 2 of a single download on its own: the school's routes (WebVPN,
    CARSI, EZProxy, one after another), after refreshing their sessions.

    Batches run it, one paper at a time, for what the faster lanes missed: a
    single download always had this phase, a batch had none (its grey lane
    turns it off so ten workers do not open ten login windows). heal=False
    skips the session refresh for the papers after the first.
    """
    config = load_config()
    if config.get("download_strategy") in ("scihub_only", "grey_only"):
        return None
    target_dir = Path(output_dir) if output_dir else Path(config["output_dir"])
    target_dir.mkdir(parents=True, exist_ok=True)
    identifier = identifier.strip()
    doi = normalize_doi(identifier) if not is_arxiv_identifier(identifier) else identifier
    output_path = target_dir / f"{safe_filename(identifier)}.pdf"
    if heal:
        try:
            from ..session_heal import ensure_institutional_sessions
            for channel, state in ensure_institutional_sessions(
                    config, use_vpnsci=use_vpnsci or bool(config.get("vpnsci_enabled"))).items():
                log.info(f"   [session-heal] {channel}: {state.get('status')}")
        except Exception as exc:
            log.info(f"   [session-heal] skipped: {exc}")
    inst_sources = _build_institutional_sources(doi, config, use_vpnsci=use_vpnsci)
    if not inst_sources:
        return None
    log.info(f"   [school route] {doi}")
    result = _run_institutional_sequence(inst_sources, doi, target_dir, output_path, config)
    if not result:
        return None
    _update_doi_index(target_dir, doi, Path(result.get("file", "")), source=result.get("source") or "",
                      strategy=config.get("download_strategy", ""), config=config)
    _auto_rename(result, identifier, config, doi=doi, target_dir=target_dir)
    cache_set(identifier, result, config)
    return result


def batch_download(
    identifiers: list[str],
    output_dir: str | Path | None = None,
    *,
    scihub_enabled: bool | None = None,
    use_tor: bool = False,
    use_vpnsci: bool = False,
    progress_callback: Any = None,
    batch_id: str | None = None,
    resume: bool = True,
    fallbacks: dict[str, list[str]] | None = None,
) -> dict[str, Any]:
    config = load_config()
    workers = config.get("batch_workers", 5)
    batch_t0 = time.perf_counter()

    # Duplicate detection via DOI normalization
    seen_dois: set[str] = set()
    unique_identifiers: list[str] = []
    skipped_duplicates = 0
    for ident in identifiers:
        normalized = normalize_doi(ident.strip()) if not is_arxiv_identifier(ident) else ident.strip()
        if normalized.lower() in seen_dois:
            skipped_duplicates += 1
            continue
        seen_dois.add(normalized.lower())
        unique_identifiers.append(ident)

    if skipped_duplicates > 0:
        log.info(f"Skipped {skipped_duplicates} duplicate identifiers")

    # Auto-generate batch_id from identifiers if not provided
    if not batch_id:
        import hashlib as _hashlib
        batch_id = _hashlib.md5("|".join(sorted(unique_identifiers)).encode()).hexdigest()[:12]

    # Load previous progress for resume
    completed_map: dict[str, dict[str, Any]] = {}
    if resume:
        completed_map = _load_progress(batch_id)
        if completed_map:
            log.info(f"Resuming batch {batch_id}: {len(completed_map)} already completed")

    # Filter out already-completed identifiers
    pending_identifiers = [i for i in unique_identifiers if i not in completed_map]
    skipped_completed = len(unique_identifiers) - len(pending_identifiers)

    if not pending_identifiers:
        log.info("All items already completed")
        all_results = [completed_map[i] for i in unique_identifiers]
        succeeded = sum(1 for r in all_results if r.get("success"))
        summary = {
            "total": len(identifiers),
            "unique": len(unique_identifiers),
            "skipped_duplicates": skipped_duplicates,
            "skipped_completed": skipped_completed,
            "succeeded": succeeded,
            "failed": len(unique_identifiers) - succeeded,
            "results": all_results,
            "failed_dois": [i for i in unique_identifiers if not completed_map.get(i, {}).get("success")],
            "batch_id": batch_id,
        }
        _write_download_results(all_results, output_dir)
        return summary

    # Pre-validate DOIs concurrently
    from ..identifiers import validate_doi
    valid_identifiers: list[str] = []
    invalid_results: list[dict[str, Any]] = []
    arxiv_ids = [i for i in pending_identifiers if is_arxiv_identifier(i)]
    doi_ids = [i for i in pending_identifiers if not is_arxiv_identifier(i)]

    if doi_ids:
        log.info(f"Batch {batch_id}: validating {len(doi_ids)} DOIs...")
        with ThreadPoolExecutor(max_workers=min(10, len(doi_ids))) as pool:
            futures = {pool.submit(validate_doi, normalize_doi(i)): i for i in doi_ids}
            for future in as_completed(futures, timeout=60):
                ident = futures[future]
                try:
                    valid, msg = future.result()
                except Exception:
                    valid, msg = True, "validation error"
                if valid:
                    valid_identifiers.append(ident)
                else:
                    log.info(f"   SKIP {ident}: {msg}")
                    r = fail(ident, f"Invalid DOI: {msg}")
                    invalid_results.append(r)
                    _save_progress(batch_id, ident, r)

    valid_identifiers.extend(arxiv_ids)
    pending_identifiers = valid_identifiers

    if invalid_results:
        log.info(f"Batch {batch_id}: {len(invalid_results)} invalid DOIs skipped")

    if not pending_identifiers:
        log.info("No valid identifiers to download")
        all_results = [completed_map.get(i) or fail(i, "invalid") for i in unique_identifiers]
        succeeded = sum(1 for r in all_results if r and r.get("success"))
        return {
            "total": len(identifiers),
            "unique": len(unique_identifiers),
            "skipped_duplicates": skipped_duplicates,
            "skipped_completed": skipped_completed,
            "skipped_invalid": len(invalid_results),
            "succeeded": succeeded,
            "failed": len(unique_identifiers) - succeeded,
            "results": all_results,
            "batch_id": batch_id,
        }

    log.info(f"Batch {batch_id}: downloading {len(pending_identifiers)} items ({skipped_completed} skipped, {len(invalid_results)} invalid)")

    delay_lock = threading.Lock()
    last_download_time = [0.0]
    delay_between = float(config.get("batch_stagger_seconds", 0.3))
    total = len(pending_identifiers)
    completed_count = [0]
    num_invalid = len(invalid_results)

    batch_gen = _CANCEL_GEN[0]

    def _staggered_download(ident: str) -> dict[str, Any]:
        if _CANCEL_GEN[0] != batch_gen:
            return fail(ident, "cancelled by user", error_type="cancelled", action="none")
        with delay_lock:
            elapsed = time.time() - last_download_time[0]
            if elapsed < delay_between:
                time.sleep(delay_between - elapsed)
            last_download_time[0] = time.time()
        return download(ident, output_dir, scihub_enabled=scihub_enabled, use_tor=use_tor, use_vpnsci=use_vpnsci, _institutional=False, _batch=True)

    results: list[dict[str, Any] | None] = [None] * total
    total_budget = float(config.get("batch_total_timeout", 600))
    pool = ThreadPoolExecutor(max_workers=workers)
    future_to_idx = {pool.submit(_staggered_download, ident): i for i, ident in enumerate(pending_identifiers)}
    try:
        for future in as_completed(future_to_idx, timeout=total_budget):
            idx = future_to_idx[future]
            try:
                result = future.result()
            except Exception:
                result = fail(pending_identifiers[idx], "download exception")
            results[idx] = result

            # Save progress immediately
            _save_progress(batch_id, pending_identifiers[idx], result)

            completed_count[0] += 1
            if progress_callback:
                try:
                    progress_callback(
                        completed_count[0] + skipped_completed + num_invalid,
                        len(unique_identifiers),
                        pending_identifiers[idx],
                        result,
                    )
                except Exception:
                    pass
    except TimeoutError:
        log.info(f"Batch {batch_id}: timeout after {int(total_budget)}s")
    finally:
        # Waive stragglers so the batch report always lands within budget;
        # per-source I/O deadlines bound the lifetime of abandoned workers.
        alive = [f for f in future_to_idx if not f.done()]
        if alive:
            log.info(f"Batch {batch_id}: {len(alive)} source(s) still running (waived)")
        pool.shutdown(wait=False, cancel_futures=True)

    for i, r in enumerate(results):
        if r is None:
            results[i] = fail(pending_identifiers[i], "timeout or incomplete")

    # Phase 2: Publisher-grouped institutional download for failed DOIs
    phase1_results = dict(zip(pending_identifiers, results))
    failed_phase1 = [doi for doi, r in phase1_results.items() if r and not r.get("success")]
    if failed_phase1 and _CANCEL_GEN[0] != batch_gen:
        log.info(f"Batch {batch_id}: cancelled — skipping Phase 2")
        failed_phase1 = []
    if failed_phase1:
        log.info(f"Batch {batch_id}: Phase 1 failed for {len(failed_phase1)} DOIs, starting Phase 2...")
        _batch_institutional_phase(failed_phase1, Path(config["output_dir"]) if not output_dir else Path(output_dir), config, batch_id, phase1_results)
        # Update results list with Phase 2 outcomes from progress file
        phase2_progress = _load_progress(batch_id)
        for i, ident in enumerate(pending_identifiers):
            if ident in phase2_progress:
                results[i] = phase2_progress[ident]

    # Reload progress to include newly-saved invalid results
    final_map = _load_progress(batch_id)

    # Preprint fallback: DOIs that failed every phase retry with the arXiv
    # preprint IDs recorded in the ScanSci Find download queue (fallbacks map).
    if fallbacks:
        _apply_preprint_fallbacks(
            fallbacks, pending_identifiers, final_map, batch_id,
            output_dir=output_dir, scihub_enabled=scihub_enabled, use_tor=use_tor, use_vpnsci=use_vpnsci,
        )

    # Build a lookup from pending_identifiers → download results
    pending_results = dict(zip(pending_identifiers, results))

    # Merge completed_map with new results (dict lookup, no fragile index counter)
    all_results = []
    for ident in unique_identifiers:
        if ident in final_map:
            all_results.append(final_map[ident])
        elif ident in completed_map:
            all_results.append(completed_map[ident])
        elif ident in pending_results:
            all_results.append(pending_results[ident])
        else:
            all_results.append(fail(ident, "missing result"))

    succeeded = sum(1 for r in all_results if r and r.get("success"))
    failed_dois = [r["identifier"] for r in all_results if r and not r.get("success")]

    # Clean up progress file if all succeeded
    if not failed_dois:
        _clear_progress(batch_id)

    summary = {
        "total": len(identifiers),
        "unique": len(unique_identifiers),
        "skipped_duplicates": skipped_duplicates,
        "skipped_completed": skipped_completed,
        "skipped_invalid": len(invalid_results),
        "succeeded": succeeded,
        "failed": len(failed_dois),
        "results": all_results,
        "failed_dois": failed_dois,
        "batch_id": batch_id,
    }
    _close_batch_browsers(batch_t0, unique_identifiers)
    _write_download_results(all_results, output_dir)
    return summary


def _close_batch_browsers(t0: float, identifiers: list[str]) -> None:
    """End-of-batch cleanup: waived downloads stop launching, their windows close.

    Per-paper cleanup in download() is skipped while other papers of the batch
    are still in flight, so windows of waived stragglers would otherwise stay
    open after the batch reports. Only closes when every download still in
    flight belongs to this batch, never a concurrent unrelated download.
    """
    keys = {normalize_doi(i) if not is_arxiv_identifier(i) else i for i in identifiers}
    with _INFLIGHT_LOCK:
        _SETTLED.update(keys)
        foreign = set(_attempt.active_keys()) - keys
    if foreign:
        log.info(f"Batch cleanup skipped: {len(foreign)} unrelated download(s) in flight")
        return
    try:
        from ..browser_backend import close_browsers_since
        n = close_browsers_since(t0)
        if n:
            log.info(f"Batch cleanup: closed {n} browser(s) left open by this batch")
    except Exception as exc:
        log.info(f"Batch browser cleanup skipped: {exc}")

try:
    from .. import browser_backend as _bb
    _bb.LAUNCH_GUARD = _source_may_continue
    # A browser belongs to the paper the launching thread works for.
    _bb.OWNER = lambda: getattr(_SOURCE_TLS, "doi", None) or getattr(_DL_TLS, "key", None)
except Exception:
    pass
