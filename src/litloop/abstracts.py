"""Abstracts for search results: clean what we have, fetch what is missing.

Fetch order, free sources first (measured on 45 materials-science papers,
29 of them Elsevier):

1. OpenAlex - already in the search results; holds almost no Elsevier abstracts.
2. Semantic Scholar batch endpoint - one keyless request, about 2/3 coverage.
3. Scopus Abstract Retrieval API - with ``elsevier_api_key``; covered all 45
   (all publishers, not only Elsevier). Weekly quota, so only for what is
   still missing; Elsevier DOIs go first since 1-2 rarely have them.
4. Crossref - publisher-deposited abstracts (Wiley, Springer, MDPI...), for
   users without an Elsevier key.

Whatever is still missing is marked ``abstract_missing`` so the screener
knows it judged from the title alone.
"""

from __future__ import annotations

import html
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from concurrent.futures import TimeoutError as FuturesTimeout
from typing import Any

import requests

from . import quota
from .log import get_logger

log = get_logger()

_MAX_CHARS = 4000  # longer abstracts keep head and tail (conclusions sit at the end)
_HEAD, _TAIL = 2600, 1200

_TAG = re.compile(r"<[^>]+>")
_LEADING = re.compile(r"^\s*(?:graphical\s+abstract|abstract|summary|highlights)\s*[:.\-]?\s*", re.I)
_TRAILING = [
    re.compile(r"\s*(?:Copyright\s*)?(?:©|\(c\)|Copyright)\s*\d{4}.*$", re.I | re.S),
    re.compile(r"\s*This article is protected by copyright\..*$", re.I | re.S),
    re.compile(r"\s*All rights reserved\.?\s*$", re.I),
]


def clean_abstract(text: str) -> str:
    """Plain text: no JATS/HTML tags, entities, boilerplate or copyright line."""
    if not text:
        return ""
    t = str(text)
    # Sub/superscripts: Crossref's pretty-printed JATS puts whitespace around
    # them ("A\n <jats:sub>2</jats:sub>\n B ..."). Attach the index to its
    # symbol, and the next element symbol of a formula to the index.
    t = re.sub(r"\s*<(?:jats:)?(?:sub|sup|inf)\b[^>]*>\s*(.*?)\s*</(?:jats:)?(?:sub|sup|inf)>",
               r"\1", t, flags=re.S | re.I)
    t = re.sub(r"(?<=[A-Z][a-z]\d)\s+(?=[A-Z][a-z]?\d)|(?<=[A-Z]\d)\s+(?=[A-Z][a-z]?\d)", "", t)
    t = html.unescape(t)
    t = _TAG.sub(" ", t)
    t = re.sub(r"\s+", " ", t).strip()
    for _ in range(2):
        t = _LEADING.sub("", t)
    for pat in _TRAILING:
        t = pat.sub("", t)
    t = t.strip()
    if len(t) > _MAX_CHARS:
        t = t[:_HEAD].rstrip() + " … " + t[-_TAIL:].lstrip()
    return t


def _session() -> requests.Session:
    s = requests.Session()
    s.headers["User-Agent"] = "LitLoop/1.0 (https://github.com/dysprosium231/litloop-paper-agent)"
    return s


S2_BATCH = 500


def _semantic_scholar(dois: list[str], timeout: float) -> dict[str, str]:
    if not dois:
        return {}
    headers = {}
    if os.environ.get("SEMANTIC_SCHOLAR_API_KEY"):
        headers["x-api-key"] = os.environ["SEMANTIC_SCHOLAR_API_KEY"]
    out: dict[str, str] = {}
    for i in range(0, len(dois), S2_BATCH):  # the batch endpoint takes at most 500 ids
        chunk = dois[i:i + S2_BATCH]
        try:
            with quota.slot("semantic_scholar"):
                r = _session().post("https://api.semanticscholar.org/graph/v1/paper/batch",
                                    params={"fields": "abstract"}, json={"ids": [f"DOI:{d}" for d in chunk]},
                                    headers=headers, timeout=timeout)
            quota.record("semantic_scholar", r)
            if r.status_code != 200:
                log.debug(f"abstracts: Semantic Scholar batch HTTP {r.status_code}")
                break
            rows = r.json()
        except (requests.RequestException, ValueError) as e:
            log.debug(f"abstracts: Semantic Scholar batch failed: {e}")
            break
        if not isinstance(rows, list):
            log.debug("abstracts: Semantic Scholar batch returned no list")
            break
        out.update({d: x["abstract"] for d, x in zip(chunk, rows) if isinstance(x, dict) and x.get("abstract")})
    return out


class _Scopus:
    """Scopus Abstract Retrieval: 9 requests/s and 10,000 per week per key
    (dev.elsevier.com/api_key_settings.html). Parallelism and pacing come from
    ``quota``; a 429 carrying ``X-ELS-Status: QUOTA_EXCEEDED`` means the weekly
    quota is spent, any other 429 is the per-second throttle."""

    def __init__(self, key: str):
        self.key = key
        self.exhausted = False  # weekly quota spent: stop asking this run

    def get(self, doi: str, timeout: float) -> str:
        for attempt in range(3):
            if self.exhausted:
                return ""
            try:
                with quota.slot("scopus"):
                    r = _session().get(f"https://api.elsevier.com/content/abstract/doi/{doi}",
                                       headers={"X-ELS-APIKey": self.key, "Accept": "application/json"},
                                       timeout=timeout)
            except requests.RequestException:
                return ""
            quota.record("scopus", r)
            if r.status_code == 429:
                if "QUOTA_EXCEEDED" in str(r.headers.get("X-ELS-Status") or ""):
                    self.exhausted = True
                    log.warning("abstracts: Scopus weekly quota exhausted")
                    return ""
                time.sleep(1.0 + attempt)  # throttled: slow down and retry
                continue
            if r.status_code != 200:
                return ""
            try:
                core = (r.json().get("abstracts-retrieval-response") or {}).get("coredata") or {}
            except ValueError:
                return ""
            return str(core.get("dc:description") or "")
        return ""


def _crossref(doi: str, timeout: float, email: str = "") -> str:
    """One Crossref record. With a contact email the request joins the polite
    pool (3 parallel requests instead of 1)."""
    provider = "crossref_polite" if email else "crossref"
    try:
        with quota.slot(provider):
            r = _session().get(f"https://api.crossref.org/works/{doi}",
                               params={"mailto": email} if email else None, timeout=timeout)
        quota.record("crossref", r)
        return str(r.json().get("message", {}).get("abstract") or "") if r.status_code == 200 else ""
    except (requests.RequestException, ValueError):
        return ""


ELSEVIER_PREFIX = "10.1016/"


def fill_abstracts(results: list[dict[str, Any]], config: dict[str, Any] | None = None,
                   budget_s: float = 20.0) -> None:
    """Clean every abstract in place; fetch missing ones within BUDGET_S seconds."""
    for r in results:
        r["abstract"] = clean_abstract(r.get("abstract") or "")
        if r["abstract"] and not r.get("abstract_source"):
            r["abstract_source"] = str(r.get("source") or "search")
    missing = {str(r["doi"]).lower(): r for r in results if not r["abstract"] and r.get("doi")}
    if missing and (config or {}).get("search_fill_abstracts", True):
        deadline = time.monotonic() + budget_s
        left = lambda: max(2.0, deadline - time.monotonic())  # noqa: E731
        key = str((config or {}).get("elsevier_api_key") or "").strip()
        email = str((config or {}).get("contact_email") or "").strip()
        scopus = _Scopus(key) if key else None

        def take(doi: str, text: str, source: str) -> None:
            text = clean_abstract(text)
            if text and doi in missing and not missing[doi]["abstract"]:
                missing[doi]["abstract"] = text
                missing[doi]["abstract_source"] = source

        def ask(source: str, doi: str) -> tuple[str, str, str]:
            if missing[doi]["abstract"] or time.monotonic() >= deadline:  # another source was faster
                return source, doi, ""
            if source == "scopus":
                return source, doi, scopus.get(doi, min(15.0, left()))
            return source, doi, _crossref(doi, min(15.0, left()), email)

        # All sources at once, first answer wins: asking them in turn made one
        # slow source (Semantic Scholar without a key often takes 10 s or more)
        # hold up abstracts another source returns in a second.
        # Scopus jobs are queued before Crossref ones, and Elsevier DOIs skip
        # Crossref (Elsevier does not deposit abstracts there): with 150
        # papers to fill, Crossref calls queued alongside used to crowd the
        # Scopus ones out of the budget.
        pool = ThreadPoolExecutor(max_workers=12)
        try:
            jobs = [pool.submit(lambda: ("semantic_scholar", _semantic_scholar(list(missing), min(8.0, left()))))]
            if scopus:
                jobs += [pool.submit(ask, "scopus", doi) for doi in missing]
            jobs += [pool.submit(ask, "crossref", doi) for doi in missing
                     if not (scopus and doi.startswith(ELSEVIER_PREFIX))]
            for job in as_completed(jobs, timeout=max(0.1, deadline - time.monotonic())):
                res = job.result()
                if res[0] == "semantic_scholar":
                    for doi, text in res[1].items():
                        take(doi, text, "semantic_scholar")
                else:
                    take(res[1], res[2], res[0])
                if all(r["abstract"] for r in missing.values()):
                    break
        except FuturesTimeout:
            log.debug("abstracts: budget used up")
        finally:
            pool.shutdown(wait=False, cancel_futures=True)  # do not wait for the slow ones
    for r in results:
        if not r["abstract"]:
            r["abstract_missing"] = True  # judge from the title only


def trim_authors(results: list[dict[str, Any]], keep: int = 3) -> None:
    """First authors only; consortium papers list hundreds."""
    for r in results:
        authors = r.get("authors") or []
        if len(authors) > keep:
            r["author_count"] = len(authors)
            r["authors"] = list(authors[:keep])
