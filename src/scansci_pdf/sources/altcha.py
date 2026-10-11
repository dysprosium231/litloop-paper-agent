"""ALTCHA proof-of-work solved over plain HTTP (no browser).

ALTCHA (used by sci-hub.ru) is a proof-of-work gate, not a human test: the
page's widget fetches a challenge (algorithm, salt, target hash, maxnumber) and
the client searches for the number n with hash(salt + n) == target, then posts
it back. Browsers do this in JS; doing the same in Python skips the browser
window entirely (~1-3 s). Ported from the user's academic-paper-downloader
skill (scripts/scihub_solver.py).
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import time
from typing import Any

import requests

from ..log import get_logger

log = get_logger()

_CHALLENGE_URL_RE = re.compile(r'challengeurl\s*=\s*["\']([^"\']+)["\']', re.I)
# sci-hub.ru posts the answer to its own solution URL, whose id differs from
# the challenge id: read it from the page script, don't derive it.
_SOLUTION_URL_RE = re.compile(r"""fetch\(\s*['"]([^'"]*/solution/[^'"]*)['"]""", re.I)
_HASHES = {"SHA-256": hashlib.sha256, "SHA-1": hashlib.sha1, "SHA-512": hashlib.sha512}


def is_altcha_page(html: str) -> bool:
    low = html.lower()
    return "altcha" in low and bool(_CHALLENGE_URL_RE.search(html))


def solve(challenge: dict[str, Any], max_seconds: float = 20.0) -> str | None:
    """Return the base64 ALTCHA payload for a challenge, or None."""
    algorithm = str(challenge.get("algorithm") or "SHA-256").upper()
    target = str(challenge.get("challenge") or "")
    salt = str(challenge.get("salt") or "")
    hasher = _HASHES.get(algorithm)
    if not target or not salt or hasher is None:
        return None
    max_number = int(challenge.get("maxNumber") or challenge.get("maxnumber") or 1_000_000)
    deadline = time.monotonic() + max_seconds
    prefix = salt.encode("utf-8")
    for n in range(max_number + 1):
        if hasher(prefix + str(n).encode()).hexdigest() == target:
            payload = {"algorithm": algorithm, "challenge": target, "number": n,
                       "salt": salt, "signature": challenge.get("signature", "")}
            return base64.b64encode(json.dumps(payload).encode("utf-8")).decode("ascii")
        if n % 50_000 == 0 and time.monotonic() > deadline:
            return None
    return None


def pass_gate(session: requests.Session, html: str, page_url: str,
              timeout: tuple[int, int] = (10, 25)) -> str | None:
    """Solve the ALTCHA gate on ``page_url`` and return the page HTML behind it."""
    m = _CHALLENGE_URL_RE.search(html)
    if not m:
        return None
    origin = "{0.scheme}://{0.netloc}".format(requests.utils.urlparse(page_url))
    challenge_url = requests.compat.urljoin(page_url, m.group(1))
    try:
        t0 = time.monotonic()
        challenge = session.get(challenge_url, headers={"Referer": page_url}, timeout=timeout).json()
        payload = solve(challenge)
        if not payload:
            log.info("   [altcha-http] no solution within the time budget")
            return None
        sm = _SOLUTION_URL_RE.search(html)
        solution_url = (requests.compat.urljoin(page_url, sm.group(1)) if sm
                        else challenge_url.replace("/challenge/", "/solution/"))
        answer = session.post(
            solution_url,
            json={"captcha": payload},
            headers={"Origin": origin, "Referer": page_url},
            timeout=timeout,
        ).json()
        if answer.get("success") is not True:
            log.info(f"   [altcha-http] solution rejected: {str(answer)[:80]}")
            return None
        log.info(f"   [altcha-http] solved in {time.monotonic() - t0:.1f}s")
        return session.get(page_url, headers={"Referer": page_url}, timeout=timeout).text
    except Exception as exc:
        log.info(f"   [altcha-http] failed: {type(exc).__name__}: {exc}")
        return None
