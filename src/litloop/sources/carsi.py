"""Standalone CARSI download source — decoupled from WebVPN.

CARSI can now be used independently: set carsi_enabled=True and carsi_idp_name,
and it will be tried in the download tier system without requiring instsci_enabled.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from ..log import get_logger

log = get_logger()


def try_carsi(doi: str, output_path: Path, config: dict[str, Any]) -> dict[str, Any] | None:
    """Try downloading via CARSI federated auth without WebVPN dependency.

    Returns a result dict on success, None on failure.
    """
    if not config.get("carsi_enabled", False):
        return None

    idp_name = config.get("carsi_idp_name", "").strip()
    if not idp_name:
        return None

    try:
        from ..institutional.carsi_login import CARSIClient, detect_publisher
        from .instsci import _resolve_doi_url

        resolved_url = _resolve_doi_url(doi)
        if not resolved_url:
            resolved_url = f"https://doi.org/{doi}"

        publisher = detect_publisher(resolved_url)
        if not publisher:
            return None

        log.info(f"   [CARSI] Trying {publisher} via {idp_name} for {doi}")
        client = CARSIClient(config)

        # If resolved URL is on a mirror/proxy domain, rebuild using primary domain
        from urllib.parse import urlparse
        cfg = client._publisher_configs.get(publisher)
        # Elsevier DOIs resolve to linkinghub.elsevier.com/retrieve/pii/<PII>; that path does
        # not exist on sciencedirect.com, so map it to the real article page.
        _lh = re.search(r"linkinghub\.elsevier\.com/retrieve/pii/([A-Za-z0-9]+)", resolved_url)
        if _lh:
            resolved_url = f"https://www.sciencedirect.com/science/article/pii/{_lh.group(1)}"
        if cfg:
            resolved_host = urlparse(resolved_url).hostname or ""
            primary_domain = cfg.domains[0]
            # Only a host outside every known domain is a mirror: nature.com is
            # one of Springer's own domains, and moving a Nature article onto
            # link.springer.com gives "Page Unavailable".
            if resolved_host and not any(d in resolved_host for d in cfg.domains):
                # Reconstruct URL using primary domain + same path
                from urllib.parse import urlunparse
                parsed = urlparse(resolved_url)
                resolved_url = urlunparse(parsed._replace(
                    scheme="https", netloc=primary_domain))
                log.info(f"   [CARSI] Redirected to primary domain: {resolved_url[:80]}")

        # Try browser download (CloakBrowser first, Selenium fallback)
        result = client.download_via_browser(doi, resolved_url, output_path)
        if result:
            return result
    except ImportError:
        return None
    except Exception as e:
        log.info(f"   [CARSI] {e}")
    return None
