"""Title-to-DOI resolver via OpenAlex search with similarity matching."""

from __future__ import annotations

import logging
import time
from difflib import SequenceMatcher
from typing import Any

from ..citations.paper_list import PaperEntry

log = logging.getLogger(__name__)

TITLE_SIMILARITY_THRESHOLD = 0.75


def _words(text: str) -> list[str]:
    import re
    return [w for w in re.findall(r"[a-z0-9]+", text.lower()) if len(w) > 2]


def resolve_reference(line: str, config: dict[str, Any], timeout: float = 15.0) -> dict[str, Any] | None:
    """The paper a reference line (or a bare title) names, by Crossref's
    ``query.bibliographic`` (built for matching whole citations; free, and in
    the polite pool with the contact email). Accepted only when nearly all of
    the found title's words are in the line, so a near miss never becomes a
    download. Returns {"doi", "title", "year"} or None."""
    import requests

    from .. import quota
    line = " ".join((line or "").split())
    if len(line) < 15:
        return None
    email = str(config.get("email") or "").strip()
    provider = "crossref_polite" if email else "crossref"
    params = {"query.bibliographic": line[:600], "rows": 3, "select": "DOI,title,issued"}
    if email:
        params["mailto"] = email
    try:
        with quota.slot(provider):
            r = requests.get("https://api.crossref.org/works", params=params, timeout=timeout)
        quota.record("crossref", r)
        if r.status_code != 200:
            return None
        items = r.json().get("message", {}).get("items", [])
    except (requests.RequestException, ValueError):
        return None
    have = set(_words(line))
    for it in items:
        title = ((it.get("title") or [""])[0] or "").strip()
        words = _words(title)
        if len(words) < 3 or not it.get("DOI"):
            continue
        if sum(1 for w in words if w in have) / len(words) >= 0.85:
            parts = (it.get("issued") or {}).get("date-parts") or [[None]]
            return {"doi": str(it["DOI"]).lower(), "title": title, "year": parts[0][0] if parts and parts[0] else None}
    return None


def resolve_references(lines: list[str], config: dict[str, Any], budget_s: float = 60.0) -> list[dict[str, Any]]:
    """resolve_reference for each line, three at a time (the polite pool's
    limit), within ``budget_s``; [{"line", "doi", "title", "year"} | {"line"}]."""
    from concurrent.futures import ThreadPoolExecutor, wait
    ex = ThreadPoolExecutor(max_workers=3)
    futs = [ex.submit(resolve_reference, ln, config) for ln in lines]
    wait(futs, timeout=budget_s)
    ex.shutdown(wait=False, cancel_futures=True)
    out = []
    for ln, f in zip(lines, futs):
        hit = f.result() if f.done() and not f.cancelled() and f.exception() is None else None
        out.append({"line": ln, **hit} if hit else {"line": ln})
    return out


def resolve_title_to_doi(title: str, config: dict[str, Any]) -> str | None:
    """Search OpenAlex by title and return best-matching DOI."""
    from ..network import _get_session, request_timeout
    from .quick import _reconstruct_abstract

    if not title or len(title) < 10:
        return None

    try:
        session = _get_session(config)
        resp = session.get(
            "https://api.openalex.org/works",
            params={"search": title, "per_page": 5},
            timeout=request_timeout(config),
        )
        if resp.status_code != 200:
            return None
        data = resp.json()
    except Exception as exc:
        log.debug("Title search failed for '%s': %s", title[:50], exc)
        return None

    best_doi = None
    best_score = 0.0
    title_lower = title.lower().strip()

    for work in data.get("results", []):
        result_title = (work.get("title") or "").lower().strip()
        if not result_title:
            continue
        score = SequenceMatcher(None, title_lower, result_title).ratio()
        if score > best_score:
            best_score = score
            doi_raw = work.get("doi", "") or ""
            best_doi = doi_raw.replace("https://doi.org/", "") if doi_raw else None

    if best_score >= TITLE_SIMILARITY_THRESHOLD and best_doi:
        log.info("Resolved title '%s...' → %s (score=%.2f)", title[:40], best_doi, best_score)
        return best_doi

    log.debug("No match for '%s...' (best score=%.2f)", title[:40], best_score)
    return None


def batch_resolve(
    entries: list[PaperEntry],
    config: dict[str, Any],
    delay: float = 0.3,
) -> dict[str, Any]:
    """Batch resolve: fix DOI format + search by title for missing DOIs.

    Returns dict with 'entries' (resolved list) and 'stats'.
    """
    total = len(entries)
    fixed_unicode = 0
    resolved_by_title = 0
    already_has_doi = 0
    unresolvable = 0

    for i, entry in enumerate(entries):
        if entry.doi:
            already_has_doi += 1
            continue

        if not entry.title:
            unresolvable += 1
            continue

        # Search by title
        doi = resolve_title_to_doi(entry.title, config)
        if doi:
            entry.doi = doi
            resolved_by_title += 1
        else:
            unresolvable += 1

        # Rate limit for OpenAlex API
        if delay > 0 and i < total - 1:
            time.sleep(delay)

    return {
        "entries": entries,
        "stats": {
            "total": total,
            "already_has_doi": already_has_doi,
            "resolved_by_title": resolved_by_title,
            "unresolvable": unresolvable,
        },
    }
