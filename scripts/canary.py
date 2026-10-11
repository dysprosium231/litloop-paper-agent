"""Lightweight live canaries for publisher surfaces and grey-lane backends.

Run in CI (weekly-to-biweekly, .github/workflows/canary.yml) to catch silent
route rot: a failing check usually means a publisher changed their page
structure or started blocking plain HTTP. Exit code 1 alerts the workflow.

2026-09 rot this file now watches for (all three found in one field day):
- MDPI: www.mdpi.com is Akamai-walled; the package routes 10.3390 to the
  open mdpi-res.com CDN — the canary pins a known-good CDN PDF.
- LibGen: the li/bz frontends serve EMPTY 200s to cookieless one-shot
  requests; the lane must warm a session and send a Referer. The canary
  replays that exact wire behavior. The booksdl download CDN 503s most
  requests (soft check, retries).
- Sci-Hub mirrors rotate constantly (soft check — datacenter IPs get
  Turnstile-challenged far more than residential ones, so a challenge in CI
  is not necessarily rot; total unreachability is).
"""

from __future__ import annotations

import sys
import time

import requests

UA = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
    )
}

# (name, url, expected_status, must_be_pdf)
CHECKS = [
    ("arxiv_api", "https://export.arxiv.org/api/query?search_query=all:electron&max_results=1", 200, False),
    ("plos_article_page", "https://journals.plos.org/plosone/article?id=10.1371/journal.pone.0065432", 200, False),
    (
        "copernicus_supplement_pdf",
        "https://nhess.copernicus.org/articles/23/2531/2023/nhess-23-2531-2023-supplement.pdf",
        200,
        True,
    ),
    (
        "nature_article_page",
        "https://www.nature.com/articles/s41586-021-03819-2",
        200,
        False,
    ),
    # MDPI fast lane: the package constructs exactly this URL pattern for
    # 10.3390 DOIs (field-verified hit). If it stops serving, the whole
    # MDPI lane (largest OA publisher) is rotting.
    (
        "mdpi_cdn_pdf",
        "https://mdpi-res.com/d_attachment/toxics/toxics-10-00577/article_deploy/toxics-10-00577.pdf",
        200,
        True,
    ),
]

# Known-in-library DOI that both LibGen (scimag) and Sci-Hub have held for
# years — canaries must not depend on recent additions.
PROBE_DOI = "10.1038/nature14539"


def check_s2_batch(failures: list[str], warnings: list[str]) -> None:
    """S2 batch endpoint: the backbone of batch pretriage (500 DOIs/request).

    S2 rate-limits per IP aggressively (chronic, documented) — after backoff
    retries a 429/400 is a WARNING, not rot. Broken payloads / connection
    errors are rot and fail hard.
    """
    resp = None
    last_exc: Exception | None = None
    try:
        for attempt in range(4):
            try:
                resp = requests.post(
                    "https://api.semanticscholar.org/graph/v1/paper/batch?fields=isOpenAccess",
                    json={"ids": [f"DOI:{PROBE_DOI}"]},
                    headers=UA, timeout=30,
                )
                last_exc = None
            except Exception as exc:  # transient TLS/connect resets
                last_exc = exc
                time.sleep(5)
                continue
            if resp.status_code not in (429, 400):
                break
            time.sleep(10 * (attempt + 1))
    except Exception as exc:
        print(f"[FAIL] s2_batch_api: {exc.__class__.__name__}: {exc}")
        failures.append("s2_batch_api")
        return

    if last_exc is not None:
        print(f"[FAIL] s2_batch_api: {last_exc.__class__.__name__}: {last_exc}")
        failures.append("s2_batch_api")
        return

    if resp is not None and resp.status_code == 200:
        data = resp.json()
        ok = (
            isinstance(data, list) and len(data) == 1
            and isinstance(data[0], dict) and "isOpenAccess" in data[0]
        )
        print(f"[{'PASS' if ok else 'FAIL'}] s2_batch_api: HTTP 200"
              f"{'' if ok else ' | unexpected payload'}")
        if not ok:
            failures.append("s2_batch_api")
        return

    status = resp.status_code if resp is not None else "?"
    print(f"[WARN] s2_batch_api: HTTP {status} after backoff — IP rate-limited "
          "(chronic); investigate only if repeated across runs")
    warnings.append("s2_batch_api")


def check_unpaywall(failures: list[str]) -> None:
    last_exc: Exception | None = None
    resp = None
    for attempt in range(3):
        try:
            resp = requests.get(
                f"https://api.unpaywall.org/v2/{PROBE_DOI}",
                params={"email": "canary@litloop.dev"},
                headers=UA, timeout=30,
            )
            last_exc = None
            break
        except Exception as exc:  # transient TLS/connect resets
            last_exc = exc
            time.sleep(5)
    if last_exc is not None:
        print(f"[FAIL] unpaywall_api: {last_exc.__class__.__name__}: {last_exc}")
        failures.append("unpaywall_api")
        return
    ok = resp.status_code == 200 and "is_oa" in (resp.json() if resp.text else {})
    print(f"[{'PASS' if ok else 'FAIL'}] unpaywall_api: HTTP {resp.status_code}")
    if not ok:
        failures.append("unpaywall_api")


def check_libgen_lane(failures: list[str], warnings: list[str]) -> None:
    """Search page must render a get.php link AFTER session warm-up + Referer.

    The cookieless one-shot request getting a 200-empty page is exactly the
    rot that silently killed the lane — replay the package's wire behavior.
    The booksdl download CDN 503s most requests: soft check with retries.
    """
    mirrors = ["https://libgen.li", "https://libgen.bz"]
    dl_url = None
    used = None
    for mirror in mirrors:
        try:
            s = requests.Session()
            s.headers.update(UA)
            s.get(f"{mirror}/", timeout=20)  # session warm-up (cookies)
            resp = s.get(f"{mirror}/ads.php?doi={PROBE_DOI}",
                         headers={"Referer": f"{mirror}/"}, timeout=30)
            html = resp.text or ""
            if 'href="' in html and "get.php" in html:
                import re
                m = re.search(r'''href=["']([^"']*get\.php[^"']+)["']''', html, re.I)
                if m:
                    dl_url = m.group(1)
                    used = mirror
                    break
            print(f"[warn] libgen_search({mirror}): HTTP {resp.status_code}, "
                  f"{len(html)}B, no get.php link")
        except Exception as exc:
            print(f"[warn] libgen_search({mirror}): {exc.__class__.__name__}")
    if dl_url:
        print(f"[PASS] libgen_search_page: link found via {used}")
    else:
        print("[FAIL] libgen_search_page: no mirror rendered a get.php link "
              "(session gate or endpoint rot)")
        failures.append("libgen_search_page")
        return

    # Soft: the booksdl CDN 503s most requests; one hit in 6 tries = alive.
    import urllib.parse
    dl = urllib.parse.urljoin(f"{used}/", dl_url)
    for attempt in range(6):
        try:
            resp = requests.get(dl, headers=UA, timeout=(20, 60),
                                allow_redirects=True, stream=True)
            first = next(resp.iter_content(chunk_size=8192), b"")
            if resp.status_code == 200 and first.startswith(b"%PDF"):
                print(f"[PASS] libgen_cdn_download: PDF after {attempt + 1} try(ies)")
                return
        except Exception as exc:
            print(f"[warn] libgen_cdn_download try {attempt + 1}: {exc.__class__.__name__}")
        time.sleep(3)
    print("[WARN] libgen_cdn_download: no PDF in 6 tries — CDN degraded "
          "(chronically flaky; investigate if repeated)")
    warnings.append("libgen_cdn_download")


def check_scihub_mirrors(warnings: list[str]) -> None:
    """Soft: mirrors rotate and challenge datacenter IPs; only alert on
    total unreachability, and even that is a warning from CI."""
    mirrors = ["https://sci-hub.vg", "https://sci-hub.al", "https://sci-hub.ren"]
    served = []
    for mirror in mirrors:
        try:
            resp = requests.get(f"{mirror}/{PROBE_DOI}", headers=UA,
                                timeout=20, allow_redirects=True)
            body = (resp.text or "").lower()
            if resp.status_code == 200 and len(body) > 2000 \
                    and "just a moment" not in body:
                served.append(mirror)
        except Exception:
            pass
    if served:
        print(f"[PASS] scihub_mirrors: serving pages — {', '.join(served)}")
    else:
        print("[WARN] scihub_mirrors: none served a page from CI "
              "(challenge/unreachable) — verify from a residential vantage "
              "before treating as rot")
        warnings.append("scihub_mirrors")


def _get_with_429_backoff(session, url, **kwargs):
    """arXiv/S2 429s clear in seconds (documented behavior) — retry twice."""
    for attempt in range(3):
        resp = session.get(url, **kwargs)
        if resp.status_code != 429 or attempt == 2:
            return resp
        time.sleep(8 * (attempt + 1))
    return resp


def main() -> None:
    failures: list[str] = []
    warnings: list[str] = []
    session = requests.Session()
    # honor env proxies so the same script can be verified from a proxied
    # workstation; CI runners have none and go direct
    session.trust_env = True
    for name, url, expected, must_be_pdf in CHECKS:
        try:
            resp = _get_with_429_backoff(session, url, headers=UA, timeout=30,
                                         allow_redirects=True, stream=True)
            ok = resp.status_code == expected
            detail = f"HTTP {resp.status_code}"
            if ok and must_be_pdf:
                first = next(resp.iter_content(chunk_size=8192), b"")
                ok = first.startswith(b"%PDF")
                detail += f" | magic={first[:4]!r}"
            print(f"[{'PASS' if ok else 'FAIL'}] {name}: {detail}")
            if not ok:
                failures.append(name)
        except Exception as exc:
            print(f"[FAIL] {name}: {exc.__class__.__name__}: {exc}")
            failures.append(name)

    check_s2_batch(failures, warnings)
    check_unpaywall(failures)
    check_libgen_lane(failures, warnings)
    check_scihub_mirrors(warnings)

    print(f"\n{len(CHECKS) + 2 - len(failures)}/{len(CHECKS) + 2} hard canaries passed,"
          f" {len(warnings)} soft warning(s)")
    if warnings:
        print("warnings: " + ", ".join(warnings))
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
