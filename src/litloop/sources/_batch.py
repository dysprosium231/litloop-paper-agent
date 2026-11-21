"""Batch download orchestration, JSONL resume state, preprint fallbacks, and standalone institutional download.

Extracted from ``litloop.sources.__init__`` on 2026-11-20; ``litloop.sources``
re-exports all symbols defined here so callers, tests, and monkeypatches remain unchanged.
"""

from __future__ import annotations

import json
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from ..download.pdf import fail
from ..identifiers import is_arxiv_identifier, normalize_doi, safe_filename
from ..log import get_logger

log = get_logger()


def _pkg() -> Any:
    return sys.modules[__package__]


def _get_progress_file(batch_id: str) -> Path:
    """Get path to batch progress file."""
    return _pkg().DATA_DIR / "batch_progress" / f"{batch_id}.jsonl"


_progress_lock = threading.Lock()


def _save_progress(batch_id: str, identifier: str, result: dict[str, Any]) -> None:
    """Append a single result to the progress file (thread-safe)."""
    progress_file = _pkg()._get_progress_file(batch_id)
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
    progress_file = _pkg()._get_progress_file(batch_id)
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
    progress_file = _pkg()._get_progress_file(batch_id)
    if progress_file.exists():
        try:
            progress_file.unlink()
        except OSError:
            pass


def _batch_institutional_phase(
    failed_dois: list[str],
    output_dir: Path,
    config: dict[str, Any],
    batch_id: str,
    results_map: dict[str, dict[str, Any]],
    batch_gen: int | None = None,
) -> None:
    """Phase 2 of a batch: the school routes (WebVPN, CARSI), one paper at a time.

    Through the same engine as single downloads (2026-10-20: the per-publisher
    PublisherBatchDownloader is gone), so session self-healing, failure reasons,
    skip and cancel work here too. Phase 1 already tried the free sources and
    the publisher's own site, so only the school routes run. Modifies
    results_map in place.
    """
    prev_gen = getattr(_pkg()._DL_TLS, "gen", None)
    if batch_gen is not None:
        _pkg()._DL_TLS.gen = batch_gen  # the downloads below belong to the batch: its cancel stops them
    try:
        _pkg()._school_routes_each(failed_dois, output_dir, config, batch_id, results_map, batch_gen)
    finally:
        _pkg()._DL_TLS.gen = prev_gen


def _school_routes_each(failed_dois, output_dir, config, batch_id, results_map, batch_gen) -> None:
    for doi in failed_dois:
        if doi in results_map and results_map[doi].get("success"):
            continue
        if batch_gen is not None and _pkg()._CANCEL_GEN[0] != batch_gen:
            log.info(f"Batch {batch_id}: cancelled — stopping phase 2")
            break
        # A PDF already on disk (an earlier run, a partial download)
        existing = next((p for p in output_dir.glob(f"{safe_filename(doi)}*.pdf") if p.stat().st_size > 5000), None)
        if existing:
            result = {"success": True, "doi": doi, "identifier": doi, "file": str(existing), "source": "local_cache"}
        else:
            log.info(f"   [Batch] Phase 2 (school routes): {doi}")
            result = _pkg().download(
                doi,
                output_dir,
                scihub_enabled=config.get("scihub_enabled", False),
                use_vpnsci=True,
                _institutional=True,
                _batch=True,
                _school_only=True,
            )
        results_map[doi] = result
        _pkg()._save_progress(batch_id, doi, result)


def _write_download_results(results: list[dict[str, Any]], output_dir: str | Path | None, **extra: Any) -> None:
    """Persist batch outcomes as download_results.json for scansci-find reconcile."""
    try:
        out = Path(output_dir) if output_dir else Path(_pkg().load_config().get("output_dir", "."))
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
            fb_result = _pkg().download(
                fallback_id,
                output_dir,
                scihub_enabled=scihub_enabled,
                use_tor=use_tor,
                use_vpnsci=False,
                _institutional=False,
                _batch=True,
            )
            if fb_result and fb_result.get("success"):
                fb_result["identifier"] = ident
                fb_result["doi"] = ident
                fb_result["preprint_fallback"] = fallback_id
                _pkg()._save_progress(batch_id, ident, fb_result)
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
    config = _pkg().load_config()
    if config.get("download_strategy") in ("scihub_only", "grey_only"):
        return None
    target_dir = Path(output_dir) if output_dir else Path(config["output_dir"])
    target_dir.mkdir(parents=True, exist_ok=True)
    identifier = identifier.strip()
    doi = normalize_doi(identifier) if not is_arxiv_identifier(identifier) else identifier
    output_path = target_dir / f"{safe_filename(identifier)}.pdf"
    if heal:
        try:
            from ..institutional.session_heal import ensure_institutional_sessions
            for channel, state in ensure_institutional_sessions(
                    config, use_vpnsci=use_vpnsci or bool(config.get("vpnsci_enabled"))).items():
                log.info(f"   [session-heal] {channel}: {state.get('status')}")
        except Exception as exc:
            log.info(f"   [session-heal] skipped: {exc}")
    inst_sources = _pkg()._build_institutional_sources(doi, config, use_vpnsci=use_vpnsci)
    if not inst_sources:
        return None
    log.info(f"   [school route] {doi}")
    result = _pkg()._run_institutional_sequence(inst_sources, doi, target_dir, output_path, config)
    if not result:
        return None
    from ..publishers.strategies._core import clear_no_access
    clear_no_access(doi)  # the school routes got it: "not subscribed" was wrong
    _pkg()._update_doi_index(target_dir, doi, Path(result.get("file", "")), source=result.get("source") or "",
                             strategy=config.get("download_strategy", ""), config=config)
    _pkg()._auto_rename(result, identifier, config, doi=doi, target_dir=target_dir)
    _pkg().cache_set(identifier, result, config)
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
    config = _pkg().load_config()
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
        completed_map = _pkg()._load_progress(batch_id)
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
        _pkg()._write_download_results(all_results, output_dir)
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
                    _pkg()._save_progress(batch_id, ident, r)

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

    batch_gen = _pkg()._CANCEL_GEN[0]

    def _staggered_download(ident: str) -> dict[str, Any]:
        if _pkg()._CANCEL_GEN[0] != batch_gen:
            return fail(ident, "cancelled by user", error_type="cancelled", action="none")
        with delay_lock:
            elapsed = time.time() - last_download_time[0]
            if elapsed < delay_between:
                time.sleep(delay_between - elapsed)
            last_download_time[0] = time.time()
        return _pkg().download(
            ident,
            output_dir,
            scihub_enabled=scihub_enabled,
            use_tor=use_tor,
            use_vpnsci=use_vpnsci,
            _institutional=False,
            _batch=True,
        )

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
            _pkg()._save_progress(batch_id, pending_identifiers[idx], result)

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
    if failed_phase1 and _pkg()._CANCEL_GEN[0] != batch_gen:
        log.info(f"Batch {batch_id}: cancelled — skipping Phase 2")
        failed_phase1 = []
    if failed_phase1:
        log.info(f"Batch {batch_id}: Phase 1 failed for {len(failed_phase1)} DOIs, starting Phase 2...")
        _pkg()._batch_institutional_phase(
            failed_phase1,
            Path(config["output_dir"]) if not output_dir else Path(output_dir),
            config,
            batch_id,
            phase1_results,
            batch_gen,
        )
        # Update results list with Phase 2 outcomes from progress file
        phase2_progress = _pkg()._load_progress(batch_id)
        for i, ident in enumerate(pending_identifiers):
            if ident in phase2_progress:
                results[i] = phase2_progress[ident]

    # Reload progress to include newly-saved invalid results
    final_map = _pkg()._load_progress(batch_id)

    # Preprint fallback: DOIs that failed every phase retry with the arXiv
    # preprint IDs recorded in the ScanSci Find download queue (fallbacks map).
    if fallbacks:
        _pkg()._apply_preprint_fallbacks(
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
        _pkg()._clear_progress(batch_id)

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
    _pkg()._close_batch_browsers(batch_t0, unique_identifiers)
    _pkg()._write_download_results(all_results, output_dir)
    return summary


def _close_batch_browsers(t0: float, identifiers: list[str]) -> None:
    """End-of-batch cleanup: waived downloads stop launching, their windows close.

    Per-paper cleanup in download() is skipped while other papers of the batch
    are still in flight, so windows of waived stragglers would otherwise stay
    open after the batch reports. Only closes when every download still in
    flight belongs to this batch, never a concurrent unrelated download.
    """
    keys = {normalize_doi(i) if not is_arxiv_identifier(i) else i for i in identifiers}
    pkg = _pkg()
    with pkg._INFLIGHT_LOCK:
        pkg._SETTLED.update(keys)
        foreign = set(pkg._attempt.active_keys()) - keys
    if foreign:
        log.info(f"Batch cleanup skipped: {len(foreign)} unrelated download(s) in flight")
        return
    try:
        from ..browser.backend import close_browsers_since
        n = close_browsers_since(t0)
        if n:
            log.info(f"Batch cleanup: closed {n} browser(s) left open by this batch")
    except Exception as exc:
        log.info(f"Batch browser cleanup skipped: {exc}")
