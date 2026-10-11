"""Paper search via OpenAlex, Semantic Scholar, and Crossref."""

from __future__ import annotations

import logging
import math
import os
import re
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

import requests

from .config import load_config

logger = logging.getLogger(__name__)

_SEARCH_TIMEOUT = 30  # seconds, longer than default because these are public APIs
_USER_AGENT = "scansci-pdf/1.5 (https://github.com/dysprosium231/litloop-paper-agent)"


class _AcademicSession(requests.Session):
    """Adds the OpenAlex API key to api.openalex.org requests only.

    OpenAlex requires an API key on every call (since 2026-02); the same
    session also talks to other APIs, which must never receive the key.
    """

    def request(self, method, url, *args, **kwargs):  # type: ignore[override]
        if str(url).startswith("https://api.openalex.org/"):
            try:
                key = str(load_config().get("openalex_api_key") or "").strip()
            except Exception:
                key = ""
            if key:
                params = dict(kwargs.get("params") or {})
                params.setdefault("api_key", key)
                kwargs["params"] = params
        return super().request(method, url, *args, **kwargs)


def _plain_session() -> requests.Session:
    """A requests session without proxy, for public academic APIs."""
    s = _AcademicSession()
    s.headers.update({"User-Agent": _USER_AGENT, "mailto": "scansci-pdf@example.invalid"})
    return s


def _reconstruct_abstract(inverted_index: dict | None) -> str:
    if not isinstance(inverted_index, dict):
        return ""
    word_positions = []
    for word, positions in inverted_index.items():
        if isinstance(positions, list):
            for pos in positions:
                if isinstance(pos, int):
                    word_positions.append((pos, word))
    word_positions.sort()
    # No cut here: clean_abstract caps very long ones, keeping the conclusion.
    return " ".join(w for _, w in word_positions)


def _search_openalex(
    query: str, limit: int = 10,
    year_from: int | None = None, year_to: int | None = None,
    sort: str | None = None,
) -> list[dict[str, Any]]:
    try:
        session = _plain_session()
        params: dict[str, Any] = {"per_page": limit}
        # OpenAlex rejects keyless requests since 2026-02.
        key = str(load_config().get("openalex_api_key") or "").strip()
        if key:
            params["api_key"] = key
        filters = []
        if sort and query:
            # `search=` also matches full text, so sorted by citations it
            # returned famous off-topic papers; restrict to title + abstract.
            filters.append("title_and_abstract.search:" + query.replace(",", " "))
        elif query:
            params["search"] = query
        if year_from or year_to:
            y_from = year_from or 1900
            y_to = year_to or 2026
            filters.append(f"publication_year:{y_from}-{y_to}")
        if filters:
            params["filter"] = ",".join(filters)
        if sort:
            sort_key = sort if ":" in sort else f"{sort}:desc"
            params["sort"] = sort_key
        resp = session.get(
            "https://api.openalex.org/works",
            params=params,
            timeout=_SEARCH_TIMEOUT,
        )
        if resp.status_code != 200:
            return []
        data = resp.json()
    except Exception:
        return []
    return _parse_openalex_works(data.get("results", []))


def _parse_openalex_works(works: list[dict]) -> list[dict[str, Any]]:
    """Parse OpenAlex works into unified result format."""
    results = []
    for work in works:
        doi_raw = work.get("doi", "") or ""
        doi = doi_raw.replace("https://doi.org/", "") if doi_raw else ""
        if not doi:
            continue
        authors = [
            a.get("author", {}).get("display_name", "")
            for a in (work.get("authorships") or [])[:5]
        ]
        oa = work.get("open_access") or {}
        best_oa = work.get("best_oa_location") or {}
        is_oa = oa.get("is_oa", False)
        oa_url = best_oa.get("pdf_url") or best_oa.get("landing_page_url") or oa.get("oa_url") or ""
        results.append({
            "title": work.get("title", ""),
            "doi": doi,
            "url": work.get("id", ""),
            "authors": authors,
            "year": work.get("publication_year", ""),
            "cited_by_count": work.get("cited_by_count", 0),
            "abstract": _reconstruct_abstract(work.get("abstract_inverted_index")),
            "is_oa": is_oa,
            "oa_url": oa_url,
            "source": "openalex",
            "journal": ((work.get("primary_location") or {}).get("source") or {}).get("display_name") or "",
            "issn": list(((work.get("primary_location") or {}).get("source") or {}).get("issn") or []),
        })
    return results


def _lookup_author_id(name: str) -> tuple[str | None, str, int, int]:
    """Resolve an author name to an OpenAlex author ID.

    Returns (author_id, display_name, works_count, cited_by_count).
    author_id is None if no match found.

    Matching strategy:
    1. Exact case-insensitive name match
    2. If none, pick by highest works_count
    """
    try:
        session = _plain_session()
        resp = session.get(
            "https://api.openalex.org/authors",
            params={"search": name, "per_page": 10},
            timeout=_SEARCH_TIMEOUT,
        )
        if resp.status_code != 200:
            return None, "", 0, 0
        data = resp.json()
    except Exception:
        return None, "", 0, 0

    candidates = data.get("results", [])
    if not candidates:
        return None, "", 0, 0

    name_lower = name.lower().strip()
    # Generate name variants (handle Chinese/Western name order)
    name_parts = name_lower.split()
    name_variants = {name_lower}
    if len(name_parts) == 2:
        # "Fang Jingyun" ↔ "Jingyun Fang"
        name_variants.add(f"{name_parts[1]} {name_parts[0]}")
    elif len(name_parts) >= 3:
        # Try last-name-first ordering for 3+ word names
        name_variants.add(f"{name_parts[-1]} {' '.join(name_parts[:-1])}")

    # First: try exact case-insensitive name match (including variants)
    exact_matches = [
        a for a in candidates
        if (a.get("display_name") or "").lower().strip() in name_variants
    ]
    if exact_matches:
        best = max(
            exact_matches,
            key=lambda a: (a.get("cited_by_count", 0), a.get("works_count", 0)),
        )
    else:
        # Fallback: highest cited_by_count (prefer authoritative consolidated profiles)
        best = max(
            candidates,
            key=lambda a: (a.get("cited_by_count", 0), a.get("works_count", 0)),
        )

    author_id = best.get("id", "").split("/")[-1] if best.get("id") else ""
    display = best.get("display_name", name)
    works = best.get("works_count", 0)
    cited = best.get("cited_by_count", 0)
    return (author_id if author_id else None, display, works, cited)


def _search_openalex_by_author(
    author_id: str,
    limit: int = 10,
    year_from: int | None = None,
    year_to: int | None = None,
    sort: str | None = None,
) -> list[dict[str, Any]]:
    """Search OpenAlex works by author ID."""
    try:
        session = _plain_session()
        filters = [f"authorships.author.id:{author_id}"]
        if year_from or year_to:
            y_from = year_from or 1900
            y_to = year_to or 2026
            filters.append(f"publication_year:{y_from}-{y_to}")

        params: dict[str, Any] = {
            "filter": ",".join(filters),
            "per_page": limit,
        }
        if sort:
            sort_key = sort if ":" in sort else f"{sort}:desc"
            params["sort"] = sort_key

        resp = session.get(
            "https://api.openalex.org/works",
            params=params,
            timeout=_SEARCH_TIMEOUT,
        )
        if resp.status_code != 200:
            return []
        data = resp.json()
    except Exception:
        return []
    return _parse_openalex_works(data.get("results", []))


def search_papers_v2(
    query: str = "",
    limit: int = 10,
    year_from: int | None = None,
    year_to: int | None = None,
    sort: str | None = None,
    *,
    author: str | None = None,
    author_id: str | None = None,
    max_cas_zone: int | None = None,
    max_cas_sub_zone: int | None = None,
    max_jcr_quartile: int | None = None,
    exclude_warning: bool = False,
    exclude_retracted: bool = False,
    stats: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Keyword search via ScanSci Find's discovery engine when available.

    Falls back transparently to the built-in three-source ``search_papers``
    when the scansci-find CLI is missing or the discovery call fails. Author
    search always stays on the built-in OpenAlex path.

    Every result is labelled with its journal's partitions (when
    litloop-journal-rank is installed) and a retraction flag. The journal
    filters (max_cas_zone=2 keeps CAS major-category zones 1-2, max_cas_sub_zone=1
    keeps journals in zone 1 of any of their subcategories, max_jcr_quartile=1 keeps Q1,
    exclude_warning drops warning-listed journals) drop records whose journal
    is unranked as well; ``stats`` receives how many were dropped and why.
    """
    sort = _normalize_sort(sort)
    filtering = bool(max_cas_zone or max_cas_sub_zone or max_jcr_quartile
                     or exclude_warning or exclude_retracted)
    pool = limit * 3 if filtering else limit
    results = _collect(query, pool, limit, year_from, year_to, sort, author, author_id)
    return _finalize(results, limit, max_cas_zone=max_cas_zone, max_cas_sub_zone=max_cas_sub_zone,
                     max_jcr_quartile=max_jcr_quartile,
                     exclude_warning=exclude_warning, exclude_retracted=exclude_retracted, stats=stats)


def _collect(query, pool, limit, year_from, year_to, sort, author, author_id) -> list[dict[str, Any]]:
    if author_id or author:
        return search_papers(
            query, limit=pool, year_from=year_from, year_to=year_to, sort=sort,
            author=author, author_id=author_id,
        )
    if not query:
        return []
    try:
        from .discovery import find_cli_available, search as discovery_search, to_legacy_results
        if not find_cli_available():
            return search_papers(query, limit=pool, year_from=year_from, year_to=year_to, sort=sort)
        with tempfile.TemporaryDirectory(prefix="scansci_find_") as tmp:
            payload = discovery_search(
                query, tmp, depth="quick", limit=max(pool * 2, 30),
                year_from=year_from, year_to=year_to, sources=_quick_sources(),
            )
            results = to_legacy_results(payload.get("candidates") or [], limit=pool * 3)
        logger.info("search: discovery engine returned %d results", len(results))
    except Exception as exc:  # noqa: BLE001 - degrade gracefully to 3-source search
        logger.warning("search: discovery engine failed (%s), falling back to 3-source", exc)
        return search_papers(query, limit=pool, year_from=year_from, year_to=year_to, sort=sort)

    if sort == "cited_by_count":
        # The relevance pool holds only the top ~30 matches; the field's most
        # cited papers are often outside it, so ask OpenAlex for those too.
        seen = {str(r.get("doi") or "").lower() for r in results if r.get("doi")}
        for extra in _search_openalex(query, max(pool * 2, 25), year_from, year_to, "cited_by_count"):
            if extra["doi"].lower() not in seen:
                seen.add(extra["doi"].lower())
                extra["_matched"] = True  # OpenAlex matched it in title/abstract
                results.append(extra)
    results = _dedupe_titles(_drop_off_topic(results, query))
    if sort == "cited_by_count":
        results.sort(key=lambda x: x.get("cited_by_count", 0), reverse=True)
    elif sort == "publication_date":
        results.sort(key=lambda x: x.get("year") or 0, reverse=True)
    return results


def _finalize(results: list[dict[str, Any]], limit: int, *, max_cas_zone=None, max_cas_sub_zone=None,
              max_jcr_quartile=None,
              exclude_warning=False, exclude_retracted=False,
              stats: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    journal_filter = bool(max_cas_zone or max_cas_sub_zone or max_jcr_quartile or exclude_warning)
    # Label only what can be shown, unless a filter needs the whole pool.
    candidates = results if (journal_filter or exclude_retracted) else results[:limit]
    _add_journal_rank(candidates)
    if journal_filter:
        candidates = _filter_by_journal(candidates, max_cas_zone, max_jcr_quartile,
                                        exclude_warning, stats, max_cas_sub_zone)
    _flag_retractions(candidates[: limit * 2] if exclude_retracted else candidates[:limit])
    if exclude_retracted:
        kept = [r for r in candidates if not r.get("retracted")]
        if stats is not None:
            stats["dropped_retracted"] = len(candidates) - len(kept)
        candidates = kept
    shown = candidates[:limit]
    # What the screener reads: clean abstracts, missing ones fetched
    # (Semantic Scholar -> Scopus -> Crossref), short author lists.
    from .abstracts import fill_abstracts, trim_authors
    trim_authors(shown)
    fill_abstracts(shown, load_config())
    return shown


def _dedupe_titles(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One record per paper: a preprint and its published version, or the same
    paper from two sources under different DOIs/casing, share a title. The
    first (best-ranked) wins; a DOI and a journal are borrowed if it lacks them."""
    seen: dict[str, dict[str, Any]] = {}
    out = []
    for r in results:
        key = re.sub(r"[^a-z0-9]+", "", str(r.get("title") or "").lower())
        if len(key) < 20:  # too short to be sure
            out.append(r)
            continue
        first = seen.get(key)
        if first is None:
            seen[key] = r
            out.append(r)
            continue
        for field in ("doi", "journal", "issn", "abstract"):
            if not first.get(field) and r.get(field):
                first[field] = r[field]
        first["cited_by_count"] = max(first.get("cited_by_count") or 0, r.get("cited_by_count") or 0)
    return out


def _filter_by_journal(results, max_cas_zone, max_jcr_quartile, exclude_warning, stats,
                       max_cas_sub_zone=None):
    kept, dropped = [], {"unranked": 0, "below_threshold": 0, "warning": 0}
    for r in results:
        if exclude_warning and r.get("journal_warning"):
            dropped["warning"] += 1
            continue
        if max_cas_zone or max_cas_sub_zone or max_jcr_quartile:
            cas, sub, q = r.get("cas_zone"), r.get("cas_sub_zone"), r.get("jcr_quartile")
            if ((max_cas_zone and cas is None) or (max_cas_sub_zone and sub is None)
                    or (max_jcr_quartile and q is None)):
                dropped["unranked"] += 1
                continue
            if ((max_cas_zone and cas > max_cas_zone) or (max_cas_sub_zone and sub > max_cas_sub_zone)
                    or (max_jcr_quartile and q > max_jcr_quartile)):
                dropped["below_threshold"] += 1
                continue
        kept.append(r)
    if stats is not None:
        stats.update({f"dropped_{k}": v for k, v in dropped.items()})
    return kept


def _add_journal_rank(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Label each result with its journal's partitions when litloop-journal-rank
    is installed (CAS / JCR / XR zones, warning lists, user catalogues)."""
    try:
        from litloop_journal_rank import lookup
    except ImportError:
        return results
    try:
        # Monthly background check for new ShowJCR tables (CAS / JCR / XR).
        from litloop_journal_rank.showjcr_sync import ensure_fresh
        ensure_fresh(proxy=str(load_config().get("host_proxy_url") or "") or None)
    except Exception:  # noqa: BLE001 - older tool version or no network
        pass
    for r in results:
        if not (r.get("journal") or r.get("issn")):
            continue
        try:
            info = lookup(journal=r.get("journal") or "", issn=r.get("issn") or None)
        except Exception:  # noqa: BLE001 - a ranking problem must not break search
            continue
        if not info.get("found"):
            continue
        r["journal_rank"] = info["label"]
        if (info.get("cas") or {}).get("zone"):
            r["cas_zone"] = info["cas"]["zone"]
        sub_zones = [s["zone"] for s in (info.get("cas") or {}).get("subcategories", []) if s.get("zone")]
        if sub_zones:
            r["cas_sub_zone"] = min(sub_zones)  # best subcategory zone
        quartile = (info.get("jcr") or {}).get("best_quartile") or ""
        if quartile[1:].isdigit():
            r["jcr_quartile"] = int(quartile[1:])
        if info.get("warnings") or (info.get("xr") or {}).get("under_review"):
            r["journal_warning"] = True
    return results


def _flag_retractions(results: list[dict[str, Any]]) -> None:
    """Mark retracted papers and expressions of concern.

    Prefers the local Retraction Watch copy kept by litloop-journal-rank
    (`ljr retractions update`): offline, and it also knows expressions of
    concern, corrections and the reasons. Without it, one batched OpenAlex
    request (is_retracted only) for up to 50 DOIs; a failure leaves results
    unflagged. Corrections are recorded in `notices` but not flagged.
    """
    dois = [str(r["doi"]).lower() for r in results if r.get("doi")][:50]
    if not dois:
        return
    try:
        from litloop_journal_rank.retractions import ensure_fresh, lookup_papers
        # Missing or a week old: refresh in the background (GitLab is reached
        # through the per-host proxy when one is configured).
        ensure_fresh(proxy=str(load_config().get("host_proxy_url") or "") or None)
        local = lookup_papers(dois)
    except Exception:  # noqa: BLE001 - tool missing or data unreadable
        local = None
    if local is not None:
        for r in results:
            info = local.get(str(r.get("doi") or "").lower())
            if not info:
                continue
            if info["retracted"]:
                r["retracted"] = True
            if info["concern"]:
                r["concern"] = True
            r["notices"] = [{k: n[k] for k in ("nature", "date", "reasons")} for n in info["notices"]]
        return
    params: dict[str, Any] = {"filter": "doi:" + "|".join(dois), "select": "doi,is_retracted",
                              "per_page": 50}
    key = str(load_config().get("openalex_api_key") or "").strip()
    if key:
        params["api_key"] = key
    try:
        resp = _plain_session().get("https://api.openalex.org/works", params=params,
                                    timeout=_SEARCH_TIMEOUT)
        works = resp.json().get("results", []) if resp.status_code == 200 else []
    except Exception:  # noqa: BLE001
        return
    retracted = {str(w.get("doi") or "").lower().replace("https://doi.org/", "")
                 for w in works if w.get("is_retracted")}
    for r in results:
        if str(r.get("doi") or "").lower() in retracted:
            r["retracted"] = True

def _quick_sources() -> list[str]:
    """Sources for an interactive search.

    OpenAlex indexes what Crossref has, ranks it better and answers in a few
    seconds; Crossref took 7-26 s and mostly added off-topic records, so it is
    used only when OpenAlex has no API key. Semantic Scholar joins when it has
    a key (the keyless pool answers 429 almost every time).
    """
    sources = ["openalex"] if str(load_config().get("openalex_api_key") or "").strip() else ["crossref"]
    if os.environ.get("SEMANTIC_SCHOLAR_API_KEY"):
        sources.append("semantic")
    return sources


_STOPWORDS = frozenset(
    "a an and the of in on for to with by from at as or via its their into using based "
    "study studies effect effects analysis review".split()
)


def _query_terms(query: str) -> list[str]:
    terms = []
    for word in re.findall(r"[a-z0-9]+(?:-[a-z0-9]+)*", query.lower()):
        if word in _STOPWORDS or len(word) < 2:
            continue
        word = word.replace("-", " ")
        # crude singular so "ceramics" matches "ceramic" (a substring test)
        if len(word) > 4 and word.endswith("s") and not word.endswith("ss"):
            word = word[:-1]
        terms.append(word)
    return list(dict.fromkeys(terms))


def _drop_off_topic(results: list[dict[str, Any]], query: str) -> list[dict[str, Any]]:
    """Drop records that share almost none of the query's terms.

    Crossref's ranking returns highly cited papers that match one common word
    ("oxidation", "ceramics"); sorted by citations they took the top slots.
    A record stays when its title (plus abstract, when present) contains at
    least 60% of the query terms. Queries in CJK scripts are left alone.
    """
    if re.search(r"[぀-ヿ㐀-鿿가-힯]", query or ""):
        return results
    terms = _query_terms(query or "")
    if len(terms) < 2:
        return results
    need = max(1, math.ceil(0.6 * len(terms)))
    kept = []
    for r in results:
        # The abstract we hold may be truncated; trust OpenAlex's own
        # title/abstract match for records it returned from that filter.
        if r.pop("_matched", False):
            kept.append(r)
            continue
        text = f"{r.get('title') or ''} {r.get('abstract') or ''}".lower().replace("-", " ")
        if sum(1 for t in terms if t in text) >= need:
            kept.append(r)
    return kept


_SORT_ALIASES = {
    "cited": "cited_by_count", "citations": "cited_by_count", "citation": "cited_by_count",
    "cited_by_count": "cited_by_count", "citation_count": "cited_by_count",
    "date": "publication_date", "year": "publication_date", "newest": "publication_date",
    "recent": "publication_date", "publication_date": "publication_date",
}


def _normalize_sort(sort: str | None) -> str | None:
    """Accept the spellings agents and users actually type; None = relevance."""
    if not sort:
        return None
    return _SORT_ALIASES.get(str(sort).strip().lower(), None)


def search_papers(
    query: str = "",
    limit: int = 10,
    year_from: int | None = None,
    year_to: int | None = None,
    sort: str | None = None,
    *,
    author: str | None = None,
    author_id: str | None = None,
) -> list[dict[str, Any]]:
    """Search papers from OpenAlex + Semantic Scholar + Crossref in parallel.

    When `author` or `author_id` is provided, searches by author instead of keyword.
    - `author`: resolves author name → OpenAlex author ID → works
    - `author_id`: directly searches works by OpenAlex author ID
    """
    # --- Author-based search (fast path) ---
    if author_id or author:
        matched_name = None
        matched_works = 0
        matched_cited = 0
        if author and not author_id:
            resolved, matched_name, matched_works, matched_cited = _lookup_author_id(author)
            if not resolved:
                return []
            author_id = resolved

        if author_id:
            results = _search_openalex_by_author(
                author_id, limit=limit,
                year_from=year_from, year_to=year_to, sort=sort,
            )
            # Attach author match metadata to first result
            if results and (matched_name or author):
                results[0]["_author_match"] = {
                    "name": matched_name or author or "",
                    "id": author_id,
                    "works_count": matched_works,
                    "cited_by_count": matched_cited,
                }
            return results

    # --- Keyword-based search (existing parallel path) ---
    if not query:
        return []

    all_results: list[dict[str, Any]] = []
    per_source = max(5, limit)

    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = {
            pool.submit(_search_openalex, query, per_source, year_from, year_to, sort): "openalex",
            pool.submit(_search_semantic_scholar, query, per_source, year_from, year_to): "semantic_scholar",
            pool.submit(_search_crossref, query, per_source, year_from, year_to): "crossref",
        }
        for future in as_completed(futures, timeout=30):
            try:
                all_results.extend(future.result())
            except Exception:
                pass

    # Deduplicate by DOI (prefer entry with more info)
    seen: dict[str, dict[str, Any]] = {}
    for r in all_results:
        doi = r.get("doi", "").lower()
        if not doi:
            continue
        if doi not in seen:
            seen[doi] = r
        else:
            existing = seen[doi]
            # Merge: keep fields from whichever entry has more data
            if not existing.get("abstract") and r.get("abstract"):
                existing["abstract"] = r["abstract"]
            if not existing.get("is_oa") and r.get("is_oa"):
                existing["is_oa"] = True
                existing["oa_url"] = r.get("oa_url", "")
            if r.get("cited_by_count", 0) > existing.get("cited_by_count", 0):
                existing["cited_by_count"] = r["cited_by_count"]
            existing["source"] = existing.get("source", "") + "+" + r.get("source", "")

    # Sort by relevance or citations
    merged = list(seen.values())
    if sort == "cited_by_count":
        merged.sort(key=lambda x: x.get("cited_by_count", 0), reverse=True)
    elif sort == "publication_date":
        merged.sort(key=lambda x: x.get("year", 0), reverse=True)

    return merged[:limit]


def _search_semantic_scholar(
    query: str, limit: int = 10,
    year_from: int | None = None, year_to: int | None = None,
) -> list[dict[str, Any]]:
    import time
    try:
        session = _plain_session()
        params: dict[str, Any] = {
            "query": query,
            "limit": limit,
            "fields": "title,externalIds,authors,year,citationCount,abstract,isOpenAccess,openAccessPdf",
        }
        year_filter = []
        if year_from:
            year_filter.append(str(year_from))
        if year_to:
            year_filter.append(str(year_to))
        if year_filter:
            params["year"] = "-".join(year_filter) if len(year_filter) == 2 else year_filter[0]

        for attempt in range(3):
            resp = session.get(
                "https://api.semanticscholar.org/graph/v1/paper/search",
                params=params,
                timeout=_SEARCH_TIMEOUT,
            )
            if resp.status_code == 200:
                break
            if resp.status_code == 429:
                time.sleep(2 * (attempt + 1))
                continue
            return []
        else:
            return []
        data = resp.json()
    except Exception:
        return []

    results = []
    for paper in data.get("data", []):
        ext_ids = paper.get("externalIds") or {}
        doi = (ext_ids.get("DOI") or "").strip()
        if not doi:
            continue
        authors = [a.get("name", "") for a in (paper.get("authors") or [])[:5]]
        oa_info = paper.get("openAccessPdf") or {}
        results.append({
            "title": paper.get("title", ""),
            "doi": doi,
            "url": f"https://api.semanticscholar.org/DOI:{doi}",
            "authors": authors,
            "year": paper.get("year", ""),
            "cited_by_count": paper.get("citationCount", 0),
            "abstract": (paper.get("abstract") or "")[:500],
            "is_oa": paper.get("isOpenAccess", False),
            "oa_url": oa_info.get("url", ""),
        })
    return results


def _search_crossref(
    query: str, limit: int = 10,
    year_from: int | None = None, year_to: int | None = None,
) -> list[dict[str, Any]]:
    try:
        session = _plain_session()
        params: dict[str, Any] = {
            "query": query,
            "rows": limit,
            "select": "DOI,title,author,published-print,is-referenced-by-count,abstract,link,container-title",
        }
        filters = []
        if year_from:
            filters.append(f"from-pub-date:{year_from}")
        if year_to:
            filters.append(f"until-pub-date:{year_to}")
        if filters:
            params["filter"] = ",".join(filters)

        resp = session.get(
            "https://api.crossref.org/works",
            params=params,
            timeout=_SEARCH_TIMEOUT,
        )
        if resp.status_code != 200:
            return []
        data = resp.json()
    except Exception:
        return []

    results = []
    for item in data.get("message", {}).get("items", []):
        doi = item.get("DOI", "")
        if not doi:
            continue
        titles = item.get("title", [])
        title = titles[0] if titles else ""
        authors = []
        for a in (item.get("author") or [])[:5]:
            name = " ".join(filter(None, [a.get("given"), a.get("family")]))
            if name:
                authors.append(name)
        # Year from published-print
        pub_date = item.get("published-print", {}).get("date-parts", [[]])
        year = pub_date[0][0] if pub_date and pub_date[0] else ""
        # OA links
        links = item.get("link", [])
        oa_url = ""
        for link in links:
            if link.get("content-type") == "application/pdf":
                oa_url = link.get("URL", "")
                break
        # Abstract (may contain HTML tags)
        abstract = (item.get("abstract") or "")[:500]
        if abstract:
            import re
            abstract = re.sub(r"<[^>]+>", "", abstract)
        results.append({
            "title": title,
            "doi": doi,
            "url": f"https://doi.org/{doi}",
            "authors": authors,
            "year": year,
            "cited_by_count": item.get("is-referenced-by-count", 0),
            "abstract": abstract,
            "is_oa": bool(oa_url),
            "oa_url": oa_url,
        })
    return results


def search_by_title(title: str, config: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """Search OpenAlex by title and return best match with DOI."""
    from difflib import SequenceMatcher
    from .network import _get_session, request_timeout

    if not title or len(title) < 10:
        return None

    if config is None:
        config = load_config()

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
    except Exception:
        return None

    title_lower = title.lower().strip()
    best = None
    best_score = 0.0

    for work in data.get("results", []):
        result_title = (work.get("title") or "").lower().strip()
        if not result_title:
            continue
        score = SequenceMatcher(None, title_lower, result_title).ratio()
        if score > best_score:
            best_score = score
            doi_raw = work.get("doi", "") or ""
            doi = doi_raw.replace("https://doi.org/", "") if doi_raw else ""
            authors = [
                a.get("author", {}).get("display_name", "")
                for a in (work.get("authorships") or [])[:5]
            ]
            best = {
                "title": work.get("title", ""),
                "doi": doi,
                "authors": authors,
                "year": work.get("publication_year", ""),
                "score": round(score, 3),
            }

    if best_score >= 0.75 and best and best.get("doi"):
        return best
    return None
