"""Compiled core modules for IP protection.

These .pyx files are compiled to .pyd/.so via Cython at build time.
Pure Python fallbacks exist in the original modules.

Usage in other modules:
    try:
        from .._core.racing import run_parallel_race
    except ImportError:
        # Pure Python fallback is used inline
"""

# Re-export all compiled functions for convenience.
# Individual modules are imported directly by source files via try/except,
# so this __init__.py serves as documentation and a unified entry point.

__all__ = [
    # racing.pyx
    "run_parallel_race",
    "build_tiers",
    "batch_download",
    # scihub_core.pyx
    "domain_score",
    "filter_cooldown_domains",
    "rank_domains",
    "record_domain_result",
    "select_domains_for_attempt",
    # instsci_core.pyx
    "convert_url",
    "construct_publisher_pdf_url",
    "find_pdf_link_in_html",
    "encrypt_data",
    "decrypt_data",
]

