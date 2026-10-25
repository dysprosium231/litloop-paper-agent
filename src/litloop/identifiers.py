"""DOI and arXiv identifier parsing."""

from __future__ import annotations

import re
import urllib.parse

ARXIV_RE = re.compile(r"^(?:arxiv:)?(?P<id>\d{4}\.\d{4,5})(?:v\d+)?$", re.I)
ARXIV_DOI_RE = re.compile(r"10\.48550/arxiv\.(?P<id>\d{4}\.\d{4,5})(?:v\d+)?", re.I)
OLD_ARXIV_RE = re.compile(r"^(?:arxiv:)?(?P<id>[a-z-]+(?:\.[A-Z]{2})?/\d{7})(?:v\d+)?$", re.I)


def canonical_doi(value: str | None) -> str:
    """Canonicalize a DOI: strip, URL-decode, strip prefixes, and lower-case."""
    if not value:
        return ""
    v = urllib.parse.unquote(str(value).strip())
    v = re.sub(r"^https?://(?:dx\.)?doi\.org/", "", v, flags=re.I)
    v = re.sub(r"^doi\.org/", "", v, flags=re.I)
    v = re.sub(r"^doi:\s*", "", v, flags=re.I)
    return v.strip().lower()


def normalize_doi(value: str) -> str:
    value = value.strip()
    value = re.sub(r"^https?://(?:dx\.)?doi\.org/", "", value, flags=re.I)
    value = re.sub(r"^doi:\s*", "", value, flags=re.I)
    return urllib.parse.unquote(value).strip()


def normalize_arxiv_id(value: str) -> str | None:
    raw = value.strip()
    lower = raw.lower()
    if lower.startswith("http://arxiv.org/abs/") or lower.startswith("https://arxiv.org/abs/"):
        raw = raw.rsplit("/", 1)[-1]
    elif lower.startswith("http://arxiv.org/pdf/") or lower.startswith("https://arxiv.org/pdf/"):
        raw = raw.rsplit("/", 1)[-1].removesuffix(".pdf")

    doi_match = ARXIV_DOI_RE.search(raw)
    if doi_match:
        return doi_match.group("id")

    match = ARXIV_RE.match(raw)
    if match:
        return match.group("id")

    old_match = OLD_ARXIV_RE.match(raw)
    if old_match:
        return old_match.group("id")

    return None


def is_arxiv_identifier(value: str) -> bool:
    return normalize_arxiv_id(value) is not None


# Anywhere in a line: a DOI (bare, doi:, doi.org link, or inside a publisher
# URL), an arXiv link, or "arXiv:2401.01234".
_DOI_IN_TEXT = re.compile(r"\b10\.\d{4,9}/[^\s\"'<>]+")
_ARXIV_IN_TEXT = re.compile(r"(?:arxiv\.org/(?:abs|pdf)/|arxiv:\s*)(\d{4}\.\d{4,5})(?:v\d+)?", re.I)
_BARE_ARXIV_LINE = re.compile(r"^\s*(\d{4}\.\d{4,5})(?:v\d+)?\s*$")


def extract_identifiers(text: str) -> tuple[list[str], list[str]]:
    """Papers in pasted text, for a download list: (DOIs, lines with none).

    One line may hold several. DOIs come back canonical (lower case, no
    prefix); arXiv papers as their DataCite DOI 10.48550/arxiv.<id>, which the
    downloads treat as the same paper as the bare id. Duplicates are dropped,
    first occurrence kept. Lines with nothing recognisable come back as they
    were, so the user can see what was not understood.
    """
    found: list[str] = []
    seen: set[str] = set()
    unrecognized: list[str] = []

    def add(doi: str) -> None:
        if doi and doi not in seen:
            seen.add(doi)
            found.append(doi)

    # Copied from PDFs: Unicode hyphens and dashes inside DOIs, non-breaking and
    # zero-width spaces.
    from .terms.text import ascii_dashes
    text = ascii_dashes(text or "")
    text = re.sub("[ ​‌‍﻿]", " ", text)
    for line in text.splitlines():
        if not line.strip():
            continue
        hits = 0
        for m in _DOI_IN_TEXT.finditer(urllib.parse.unquote(line)):
            doi = m.group(0).rstrip(".,;:)]}>")
            doi = re.sub(r"(?i)(\.pdf|/pdf|/full|/abstract|/epdf)$", "", doi)
            arxiv = ARXIV_DOI_RE.fullmatch(doi)
            add(f"10.48550/arxiv.{arxiv.group('id')}" if arxiv else canonical_doi(doi))
            hits += 1
        for m in _ARXIV_IN_TEXT.finditer(line):
            add(f"10.48550/arxiv.{m.group(1)}")
            hits += 1
        bare = _BARE_ARXIV_LINE.match(line)
        if bare and not hits:
            add(f"10.48550/arxiv.{bare.group(1)}")
            hits += 1
        if not hits:
            unrecognized.append(line.strip())
    return found, unrecognized


def validate_doi(doi: str) -> tuple[bool, str]:
    """Check if a DOI resolves via doi.org. Returns (valid, resolved_url or error)."""
    import requests
    from .network import user_agent
    try:
        resp = requests.head(
            f"https://doi.org/{urllib.parse.quote(doi, safe='')}",
            timeout=10,
            allow_redirects=True,
            headers={"User-Agent": user_agent()},
        )
        if resp.status_code == 200:
            return True, resp.url
        elif resp.status_code == 404:
            return False, "DOI not found (404)"
        else:
            return True, f"status={resp.status_code}"
    except requests.Timeout:
        return True, "timeout (assume valid)"
    except Exception as e:
        return True, f"check failed: {e}"


def safe_filename(identifier: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", identifier).strip("_") or "paper"
