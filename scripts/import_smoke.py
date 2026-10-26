"""Import every module a plain `pip install litloop` must be able to run.

The CI job ``base-install`` runs it without any extras installed. Modules that
need an extra (a browser backend, Zotero) import it lazily inside functions,
so they still import here.
"""

from __future__ import annotations

import importlib
import sys

MODULES = [
    "litloop.main", "litloop.server", "litloop.app_server", "litloop.institutional.auth",
    "litloop.sources", "litloop.download_jobs", "litloop.institutional.schools", "litloop.browser.engine",
    "litloop.search.quick", "litloop.search.deep", "litloop.search.scansci_find",
    "litloop.search.abstracts", "litloop.search.title_to_doi", "litloop.search.screening",
    "litloop.download.fetcher", "litloop.download.pipeline", "litloop.download.pdf",
    "litloop.download.rename", "litloop.download.doi_check", "litloop.download.progress",
    "litloop.download.markdown",
    "litloop.terms.text", "litloop.terms.spelling", "litloop.terms.glossary",
    "litloop.citations.formats", "litloop.citations.bibtex_crossref", "litloop.citations.bib_import",
    "litloop.citations.paper_list", "litloop.citations.session_export",
    "litloop.tor.proxy", "litloop.tor.embedded",
    "litloop.zotero.client", "litloop.zotero.bridge",
]

failed = []
for name in MODULES:
    try:
        importlib.import_module(name)
    except Exception as exc:  # report every module, not just the first
        failed.append(f"{name}: {type(exc).__name__}: {exc}")
for line in failed:
    print("FAIL", line)
print(f"{len(MODULES) - len(failed)}/{len(MODULES)} modules import")
sys.exit(1 if failed else 0)
