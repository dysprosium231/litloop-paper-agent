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

from ..terms import text
from ..config import load_config
from ..network import user_agent

logger = logging.getLogger(__name__)

_SEARCH_TIMEOUT = 30  # seconds, longer than default because these are public APIs
_USER_AGENT = user_agent("(https://github.com/dysprosium231/litloop-paper-agent)")


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
            resp = super().request(method, url, *args, **kwargs)
            from .. import quota  # count keyword searches too (10 credits each), not only deep search
            quota.record("openalex", resp)
            return resp
        return super().request(method, url, *args, **kwargs)


def _plain_session() -> requests.Session:
    """A requests session without proxy, for public academic APIs."""
    s = _AcademicSession()
    s.headers.update({"User-Agent": _USER_AGENT, "mailto": "litloop@example.invalid"})
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
    expansions: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Keyword search via ScanSci Find's discovery engine when available.

    Other wordings of the query from the glossary (spelling, the user's terms,
    element symbols) are searched too; ``expansions`` receives what was expanded.

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
    results = _collect(query, pool, limit, year_from, year_to, sort, author, author_id, expansions)
    return _finalize(results, limit, max_cas_zone=max_cas_zone, max_cas_sub_zone=max_cas_sub_zone,
                     max_jcr_quartile=max_jcr_quartile,
                     exclude_warning=exclude_warning, exclude_retracted=exclude_retracted, stats=stats)


def _glossary_variants(query: str) -> tuple[list[str], list[dict[str, Any]]]:
    """Other wordings of ``query`` from the glossary (``glossary.query_variants``:
    the other spelling, the user's terms, element symbols and names)."""
    from ..terms import glossary
    try:
        return glossary.query_variants(query, glossary.in_use())
    except Exception as exc:  # an unreadable glossary must not stop the search
        logger.warning("search: glossary not read (%s)", exc)
        return [], []


def _collect(query, pool, limit, year_from, year_to, sort, author, author_id,
             expansions: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    if author_id or author:
        return search_papers(
            query, limit=pool, year_from=year_from, year_to=year_to, sort=sort,
            author=author, author_id=author_id,
        )
    if not query:
        return []
    variants, used = _glossary_variants(query)
    if expansions is not None:
        expansions += used
    if not variants:
        return _tidy(_collect_one(query, pool, year_from, year_to, sort), query, sort)
    # Each wording is searched on its own; results are taken from each in turn,
    # so papers that only write the other wording also reach the first page.
    with ThreadPoolExecutor(max_workers=len(variants) + 1) as ex:
        runs = list(ex.map(lambda q: _collect_one(q, pool, year_from, year_to, sort), [query, *variants]))
    logger.info("search: glossary wordings %s returned %s results", variants, [len(r) for r in runs])
    return _tidy(_take_in_turns(runs), query, sort, variants)


def _take_in_turns(runs: list[list[dict[str, Any]]]) -> list[dict[str, Any]]:
    """The first result of each run, then the second of each, ...; one record per DOI or title."""
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for i in range(max(map(len, runs), default=0)):
        for run in runs:
            if i < len(run):
                r = run[i]
                key = text.paper_key(r)
                if key not in seen:
                    seen.add(key)
                    out.append(r)
    return out


def _collect_one(query, pool, year_from, year_to, sort) -> list[dict[str, Any]]:
    """One wording of a keyword search, not yet tidied: the discovery engine, or the built-in search."""
    try:
        from .scansci_find import find_cli_available, search as discovery_search, to_legacy_results
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
        logger.warning("search: discovery engine failed (%s), falling back to the built-in search", exc)
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
    return results


def _tidy(results: list[dict[str, Any]], query: str, sort: str | None,
          variants: list[str] | None = None) -> list[dict[str, Any]]:
    """Drop junk titles, rank by how well each matches the query (or one of its
    glossary ``variants``), merge duplicate titles. Both the discovery engine and
    the built-in fallback go through it: the fallback used to skip it (code
    review, 2026-10-18), so users without the engine got unranked,
    undeduplicated results."""
    results = _dedupe_titles(_match_tier(_drop_junk_titles(results), query, variants))
    # Sorting by citations or date happens within a match tier, so a famous
    # but weakly matching paper does not jump ahead of the on-topic ones.
    if sort == "cited_by_count":
        results.sort(key=lambda x: (x.get("_tier", 0), -(x.get("cited_by_count") or 0)))
    elif sort == "publication_date":
        results.sort(key=lambda x: (x.get("_tier", 0), -(x.get("year") or 0)))
    for r in results:
        r.pop("_tier", None)
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
    # A few spares: merging a preprint into its published version, which is
    # only recognisable once both abstracts are in, must not shorten the list.
    shown = candidates[: limit + 3]
    # What the screener reads: clean abstracts, missing ones fetched
    # (Semantic Scholar -> Scopus -> Crossref), short author lists.
    from .abstracts import fill_abstracts, trim_authors
    trim_authors(shown)
    fill_abstracts(shown, load_config())
    shown = _merge_preprints(shown, stats)
    _note_unverified_venues(shown)
    return shown[:limit]


_PREPRINT_DOI = re.compile(
    r"^10\.(2139/ssrn|21203/rs\.|1101/|20944/preprints|48550/arxiv|31219/osf|26434/chemrxiv"
    r"|22541/au|36227/techrxiv|31224/)", re.I)


def _is_preprint(r: dict[str, Any]) -> bool:
    doi = str(r.get("doi") or "")
    return bool(_PREPRINT_DOI.match(doi)) or (not doi and bool(r.get("arxiv_id")))


def _surname(r: dict[str, Any]) -> str:
    authors = r.get("authors") or []
    first = authors[0] if authors else ""
    if isinstance(first, dict):
        first = first.get("family") or first.get("name") or ""
    parts = re.findall(r"[a-z]+", str(first).lower())
    return parts[-1] if parts else ""


def _same_work(a: dict[str, Any], b: dict[str, Any]) -> bool:
    """Same first author and near-identical abstracts (titles often change on publication)."""
    if not _surname(a) or _surname(a) != _surname(b):
        return False
    wa = set(re.findall(r"[a-z0-9]{4,}", str(a.get("abstract") or "").lower()))
    wb = set(re.findall(r"[a-z0-9]{4,}", str(b.get("abstract") or "").lower()))
    if len(wa) < 20 or len(wb) < 20:
        return False
    return len(wa & wb) / len(wa | wb) >= 0.6


def _merge_preprints(results: list[dict[str, Any]], stats: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Fold a preprint into its published version: keep the journal record,
    remember the preprint DOI (useful as an open copy), drop the duplicate."""
    published = [r for r in results if not _is_preprint(r)]
    out, merged = [], 0
    for r in results:
        if _is_preprint(r):
            twin = next((p for p in published if _same_work(r, p)), None)
            if twin is not None:
                twin.setdefault("preprint_doi", r.get("doi") or r.get("arxiv_id"))
                if not twin.get("oa_url") and r.get("oa_url"):
                    twin["preprint_url"] = r["oa_url"]
                merged += 1
                continue
        out.append(r)
    if merged and stats is not None:
        stats["merged_preprints"] = merged
    return out


def _note_unverified_venues(results: list[dict[str, Any]]) -> None:
    """Without a DOI the venue comes from the index alone, and repositories
    are sometimes filed under an unrelated journal; say so."""
    for r in results:
        if r.get("journal") and not r.get("doi"):
            r["venue_unverified"] = True


_ABBREV_ONLY = re.compile(r"(?:[A-Z][A-Za-z]{0,9}\.\s*){2,}")


def _drop_junk_titles(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drop index records whose "title" is a journal abbreviation
    ("Appl. Organomet. Chem.") or just the journal's name."""
    kept = []
    for r in results:
        title = str(r.get("title") or "").strip()
        journal = str(r.get("journal") or "").strip().lower()
        if _ABBREV_ONLY.fullmatch(title) or (journal and title.lower() == journal):
            continue
        kept.append(r)
    return kept


def _dedupe_titles(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One record per paper: a preprint and its published version, or the same
    paper from two sources under different DOIs/casing, share a title. The
    first (best-ranked) wins; a DOI and a journal are borrowed if it lacks them."""
    seen: dict[str, dict[str, Any]] = {}
    out = []
    for r in results:
        key = text.title_key(r.get("title"))
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


_D = re.escape(text.DASHES)
_QUERY_WORD = re.compile(rf"[A-Za-z0-9%]+(?:[{_D}][A-Za-z0-9%]+)*")


def _query_terms(query: str) -> list[str]:
    """Content words of a query, normalised for a substring test.

    Hyphenated concepts stay whole ("polymer derived"); chemical systems and
    terms with digits are split into their parts, since papers write Al-Si-Mg
    as Al-Mg-Si or AlSiMg. Words are made singular and long ones cut to a
    crude stem (``text.singular``, ``text.stem``), so "ceramics" matches
    "ceramic" and "infiltration" matches "infiltrated"."""
    terms = []
    for word in _QUERY_WORD.findall(query or ""):
        if any(ch.isdigit() for ch in word) or text.is_chemical_system(word):
            pieces = [p for p in re.split(rf"[{_D}]", word) if p]
        else:
            pieces = [word]
        for piece in pieces:
            words = text.plain(piece.strip("%")).split()
            if not words or (len(words) == 1 and (words[0] in text.STOPWORDS or len(words[0]) < 2)):
                continue
            words[-1] = text.singular(words[-1])
            terms.append(" ".join(words) if len(words) > 1 else text.stem(words[0]))
    return list(dict.fromkeys(terms))


def _match_tier(results: list[dict[str, Any]], query: str,
                variants: list[str] | None = None) -> list[dict[str, Any]]:
    """Order records by how well they match the query, without dropping any.

    Tier 0: the record names every hyphenated concept of the query and at
    least 60% of its terms. Tier 1: the terms are there but a concept is
    missing ("polymers ... derived" for "polymer-derived"), or there is no
    real abstract and the title alone falls short. Tier 2: an abstract to
    judge from, and fewer than 60% of the terms in it. Records keep their
    relevance order inside a tier, so
    weaker matches only surface when the strong ones run out.

    Nothing is removed: we judge from a title and an often truncated
    abstract, while the index matched the full record, and a deep search
    loses more from a dropped target than from a few extra records to screen.
    Queries in CJK scripts and records OpenAlex matched on title/abstract
    (``_matched``) count as tier 0. With glossary ``variants`` (other wordings
    of the query), a record gets the best tier any wording gives it.
    """
    cjk = re.search(r"[぀-ヿ㐀-鿿가-힯]", query or "")
    specs = []
    for q in [query or "", *(variants or [])]:
        terms = [] if cjk else _query_terms(q)
        need = max(1, math.ceil(0.6 * len(terms))) if len(terms) >= 2 else 0
        specs.append((terms, need, set() if cjk else _query_compounds(q)))
    tiered = []
    for pos, r in enumerate(results):
        tier = 0
        if not r.pop("_matched", False):
            words = f"{r.get('title') or ''} {r.get('abstract') or ''}"
            plain, squashed = text.plain(words), text.squash(words)
            judged_on_title = len(r.get("abstract") or "") < 300
            tier = min(_tier(terms, need, compounds, plain, squashed, judged_on_title, bool(r.get("abstract")))
                       for terms, need, compounds in specs)
        tiered.append((tier, pos, r))
    tiered.sort(key=lambda x: (x[0], x[1]))
    for tier, _, r in tiered:
        r["_tier"] = tier
    return [r for _, _, r in tiered]


def _tier(terms: list[str], need: int, compounds: set[str], plain: str, squashed: str,
          judged_on_title: bool, has_abstract: bool) -> int:
    """One record's tier against one wording of the query (see ``_match_tier``)."""
    if need and sum(1 for t in terms if t in plain) < need:
        # A title alone rarely carries every term: too little to call it off-topic.
        return 1 if judged_on_title else 2
    if compounds and has_abstract and not all(c in squashed for c in compounds):
        return 1
    return 0


def _query_compounds(query: str) -> set[str]:
    """Hyphenated query terms that name one concept ("polymer-derived").

    Chemical systems (Al-Si-Mg, Si-B-C-N) are left out because papers list
    the elements in any order or run them together, and so are terms with
    digits (3D-SiC, Al-12%Si), which are written in too many ways."""
    out = set()
    for c in re.findall(rf"[A-Za-z0-9]+(?:[{_D}][A-Za-z0-9]+)+", query or ""):
        if any(ch.isdigit() for ch in c) or text.is_chemical_system(c):
            continue
        squashed = text.squash(c)
        if len(squashed) >= 4:
            out.add(squashed)
    return out


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

