"""Is a DOI registered? Asked once before a paper is downloaded, so a mistyped
or made-up DOI fails at once instead of running every source and school route.

doi.org's handle API knows the DOIs of every registration agency (Crossref,
DataCite, mEDRA, ...): ``/api/handles/<doi>`` answers 200 with
``responseCode`` 1 for a registered DOI and 404 with ``responseCode`` 100 for
one that is not (checked 2026-10-23). Anything else (no network, a timeout, an
odd answer) leaves the question open and the paper is downloaded as before.
"""

from __future__ import annotations

import threading
from urllib.parse import quote

import requests

_KNOWN: dict[str, bool] = {}
_LOCK = threading.Lock()


def registered(doi: str, timeout: float = 6.0) -> bool | None:
    """True or False when doi.org answered, None when it could not tell."""
    key = (doi or "").strip().lower()
    if not key.startswith("10."):
        return None
    with _LOCK:
        if key in _KNOWN:
            return _KNOWN[key]
    try:
        r = requests.get(f"https://doi.org/api/handles/{quote(key, safe='/')}", params={"type": "URL"},
                         timeout=timeout)
        code = r.json().get("responseCode")
    except (requests.RequestException, ValueError, AttributeError):
        return None
    if r.status_code == 200 and code == 1:
        answer = True
    elif r.status_code == 404 and code == 100:
        answer = False
    else:
        return None
    with _LOCK:
        _KNOWN[key] = answer
    return answer
