"""Local DOI index, cache validation, and PDF auto-rename helpers.

Extracted on 2026-11-20 from ``sources/__init__.py``; all exported names are
re-exported by ``sources/__init__.py``.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any

from ..identifiers import safe_filename
from ..log import get_logger

log = get_logger()

__all__ = [
    "_EXPLICIT_STRATEGIES",
    "_index_entry",
    "_index_entry_is_fresh",
    "_index_strategy_compatible",
    "_norm_path",
    "_files_claimed_by_other_dois",
    "_pdf_mentions_doi",
    "_same_dir",
    "_update_doi_index",
    "_auto_rename",
]


def _pkg() -> Any:
    return sys.modules[__package__]


#: Strategies that encode an explicit user source preference. For these, a
#: cached file obtained through a different strategy is treated as a miss so
#: the requested channel is actually used; the default strategies reuse cache.
_EXPLICIT_STRATEGIES = ("scihub_only", "legal_only", "scihub_first")


def _index_entry(entry: Any) -> dict[str, Any] | None:
    """Normalize a .doi_index entry to a record dict.

    Legacy ``str -> path`` entries have no provenance and no timestamp; they
    are treated as fresh (ts=None) and upgraded on the next cache hit.
    """
    if isinstance(entry, str):
        return {"file": entry, "source": "", "strategy": "", "ts": None}
    if isinstance(entry, dict):
        ts = entry.get("ts")
        try:
            ts_value = float(ts) if ts is not None else None
        except (TypeError, ValueError):
            ts_value = None
        return {
            "file": str(entry.get("file") or ""),
            "source": str(entry.get("source") or ""),
            "strategy": str(entry.get("strategy") or ""),
            "ts": ts_value,
        }
    return None


def _index_entry_is_fresh(entry: dict[str, Any], config: dict[str, Any]) -> bool:
    """Apply the cache TTL (cache_ttl_hours) to an index entry."""
    try:
        ttl_hours = float(config.get("cache_ttl_hours", 168))
    except (TypeError, ValueError):
        ttl_hours = 168.0
    if ttl_hours <= 0 or entry.get("ts") is None:
        return True
    return (time.time() - entry["ts"]) <= ttl_hours * 3600


def _index_strategy_compatible(entry: dict[str, Any], config: dict[str, Any]) -> bool:
    """Honor an explicitly requested download strategy over cached files."""
    current = str(config.get("download_strategy") or "fastest").strip()
    if current not in _pkg()._EXPLICIT_STRATEGIES:
        return True
    recorded = str(entry.get("strategy") or "").strip()
    return recorded == current


def _norm_path(p: Path | str) -> str:
    return str(Path(p).resolve()).lower()


def _files_claimed_by_other_dois(target_dir: Path, doi: str) -> set[str]:
    """Files the DOI index already assigns to a different DOI."""
    try:
        idx = json.loads((target_dir / ".doi_index.json").read_text(encoding="utf-8"))
    except Exception:
        return set()
    claimed = set()
    for key, raw in idx.items():
        entry = _index_entry(raw)
        if key != doi and entry and entry.get("file"):
            claimed.add(_norm_path(entry["file"]))
    return claimed


def _pdf_mentions_doi(path: Path, doi: str, pages: int = 3) -> bool:
    """True when the DOI appears in the first pages' text (publisher PDFs print it)."""
    from ..download.pdf import doi_printed
    return doi_printed(path, doi, pages)


def _same_dir(a: Path, b: Path) -> bool:
    try:
        return a.resolve() == b.resolve()
    except OSError:
        return str(a) == str(b)


def _update_doi_index(
    target_dir: Path,
    doi: str,
    file_path: Path,
    *,
    source: str = "",
    strategy: str = "",
    config: dict[str, Any] | None = None,
) -> None:
    """Update the DOI→file index for dedup.

    Records carry provenance (``source``/``strategy``) and a timestamp so
    stale or channel-mismatched entries can be invalidated by TTL and by
    explicit strategy changes instead of manual ``rm``. Existing entries keep
    their recorded source/strategy when the caller does not supply new ones
    (e.g. a rename that just moves the file).
    """
    doi_index = target_dir / ".doi_index.json"
    try:
        idx: dict[str, Any] = {}
        if doi_index.exists():
            idx = json.loads(doi_index.read_text(encoding="utf-8"))
        existing = _index_entry(idx.get(doi)) if doi in idx else None
        if existing:
            source = source or existing["source"]
            strategy = strategy or existing["strategy"]
        idx[doi] = {
            "file": str(file_path),
            "source": source or "",
            "strategy": strategy or (config or {}).get("download_strategy", "") or "",
            "ts": time.time(),
        }
        doi_index.write_text(json.dumps(idx, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def _auto_rename(result: dict[str, Any], identifier: str, config: dict[str, Any], doi: str = "", target_dir: Path | None = None) -> None:
    """Auto-rename downloaded PDF based on metadata."""
    from ..download.rename import rename_format
    if rename_format(config) == "off":
        return
    file_path = Path(result.get("file", ""))
    if not file_path.exists():
        return
    if file_path.suffix.lower() != ".pdf":
        # Non-PDF artifacts (e.g. the ElsevierAPI text/plain full text) keep
        # their own extension — rename_pdf would force a .pdf name onto them.
        log.info(f"   Non-PDF artifact ({file_path.suffix or 'no ext'}) — skipping auto-rename")
        return
    # Use cached metadata or fetch from Crossref
    from ..citations.formats import fetch_metadata
    _doi = doi or result.get("doi", identifier)
    metadata = fetch_metadata(_doi, config)
    if metadata:
        new_path = _pkg().rename_pdf(file_path, metadata, doi=_doi, fmt=rename_format(config))
        if new_path and result.get("whole_book") and "整本书" not in new_path.stem:
            # A chapter delivered as its whole book says so in its name.
            tagged = new_path.with_name(f"{new_path.stem}（整本书）{new_path.suffix}")
            try:
                new_path.rename(tagged)
                new_path = tagged
            except OSError:
                pass
        if new_path and result.get("preprint") and "_preprint" not in new_path.stem:
            # A preprint of the paper is not the published PDF: say so in the
            # name, so it is not mistaken for the version of record later.
            tagged = new_path.with_name(f"{new_path.stem}_preprint{new_path.suffix}")
            try:
                new_path.rename(tagged)
                new_path = tagged
            except OSError:
                pass
        if new_path and new_path != file_path:
            result["file"] = str(new_path)
            result["renamed"] = True
            log.info(f"   Renamed: {file_path.name} -> {new_path.name}")
            # Racing leaves one "<doi>_<Source>.pdf" copy per source that also
            # succeeded; the winner is now saved under its final name.
            if _doi:
                removed = 0
                for dup in new_path.parent.glob(f"{safe_filename(_doi)}_*.pdf"):
                    if dup != new_path:
                        try:
                            dup.unlink()
                            removed += 1
                        except OSError:
                            pass
                if removed:
                    log.info(f"   Removed {removed} duplicate source cop{'y' if removed == 1 else 'ies'}")
            # Update DOI→file index for dedup
            if target_dir and _doi:
                _pkg()._update_doi_index(
                    target_dir,
                    _doi,
                    new_path,
                    source=result.get("source") or "",
                    strategy=config.get("download_strategy", ""),
                    config=config,
                )
        else:
            log.info(f"   Kept original name: {file_path.name}")
    else:
        log.info(f"   No metadata for rename, keeping: {file_path.name}")
