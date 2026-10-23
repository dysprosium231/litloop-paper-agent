"""Import every module a plain `pip install litloop` must be able to run.

The CI job ``base-install`` runs it without any extras installed. Modules that
need an extra (a browser backend, Zotero) import it lazily inside functions,
so they still import here.
"""

from __future__ import annotations

import importlib
import sys

MODULES = [
    "litloop.main", "litloop.server", "litloop.app_server", "litloop.fetcher", "litloop.auth",
    "litloop.sources", "litloop.pipeline", "litloop.download_jobs", "litloop.deep_search",
    "litloop.search", "litloop.screening", "litloop.schools", "litloop.browser_engine",
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
