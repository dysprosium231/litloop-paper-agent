"""PDF auto-rename based on metadata, in the format the user picked (``rename_format``)."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from pathlib import Path
from typing import Any

from .identifiers import safe_filename
from .log import get_logger

log = get_logger()


# rename_format values -> what the settings page shows. "off" keeps the name the source gave.
FORMATS = {
    "author_year_title_doi": "作者年份_标题_DOI",
    "author_year_title": "作者年份_标题",
    "zotero": "作者 - 年份 - 完整标题",
    "year_author_title": "年份_作者_标题",
    "title": "完整标题",
    "doi": "DOI",
    "off": "保持原名",
}
DEFAULT_FORMAT = "author_year_title_doi"


def rename_format(config: dict[str, Any]) -> str:
    """The configured format; the older ``auto_rename: false`` means off."""
    if config.get("auto_rename") is False:
        return "off"
    fmt = config.get("rename_format") or DEFAULT_FORMAT
    return fmt if fmt in FORMATS else DEFAULT_FORMAT


_ILLEGAL = re.compile('[' + re.escape('<>:"/' + chr(92) + '|?*') + chr(0) + '-' + chr(31) + ']')


def _full_title(metadata: dict[str, Any], max_len: int = 120) -> str:
    """The whole title, kept in its own script, with characters Windows forbids removed."""
    titles = metadata.get("title", metadata.get("titles", []))
    title = titles[0] if isinstance(titles, list) and titles else titles
    if not isinstance(title, str):
        return ""
    title = re.sub(r"<[^>]+>", "", title)
    title = " ".join(_ILLEGAL.sub(" ", title).split()).strip(" .")
    if len(title) > max_len:
        title = title[:max_len].rsplit(" ", 1)[0].rstrip(" .,;")
    return title


def generate_filename(metadata: dict[str, Any], doi: str | None = None,
                      max_len: int = 60, max_words: int = 4, fmt: str = DEFAULT_FORMAT) -> str:
    """Generate a clean filename from paper metadata in format ``fmt`` (see FORMATS).

    Format: LastNameYear_First_Title_Words_<DOI>.pdf. The readable part is
    capped at ``max_words`` words / ``max_len`` chars; the DOI (from ``doi``
    or the metadata) makes the name unique to the paper.
    Example: Jiang2024_Lightweight_Thermally_Insulating_SiBCN_10.1016_j.cej.2024.157656.pdf
    """
    # Extract year
    year = ""
    for key in ("year", "published-print", "published-online", "created"):
        val = metadata.get(key)
        if isinstance(val, str) and val.isdigit():
            year = val
            break
        elif isinstance(val, dict):
            parts = val.get("date-parts", [[]])
            if parts and parts[0]:
                year = str(parts[0][0])
                break
        elif isinstance(val, (int, float)):
            year = str(int(val))
            break

    # Extract first author last name
    authors = metadata.get("author", metadata.get("authors", []))
    first_author = ""
    if authors and isinstance(authors, list):
        a = authors[0]
        if isinstance(a, dict):
            first_author = a.get("family", a.get("given", a.get("name", "")))
        elif isinstance(a, str):
            # "Last, First" or "First Last"
            parts = a.split(",")
            first_author = parts[0].strip().split()[-1] if parts else a

    # Extract the first meaningful words of the title
    titles = metadata.get("title", metadata.get("titles", []))
    title_words: list[str] = []
    if titles:
        title = titles[0] if isinstance(titles, list) else titles
        if isinstance(title, str):
            title = re.sub(r"<[^>]+>", "", _ascii(title))
            stop_words = {"a", "an", "the", "on", "in", "of", "for", "to", "and", "with", "from",
                          "by", "via", "at", "as", "its", "their", "into", "using"}
            for w in re.findall(r"[A-Za-z0-9]+", title):
                if w.lower() in stop_words or (len(w) < 3 and not any(c.isdigit() for c in w)):
                    continue
                title_words.append(w if any(c.isupper() for c in w[1:]) else w.capitalize())
                if len(title_words) == max_words:
                    break

    # Clean components
    first_author = re.sub(r"[^A-Za-z]", "", _ascii(first_author))[:20]
    year = re.sub(r"[^0-9]", "", year)[:4]
    title_word = "_".join(title_words)

    # Build filename: LastNameYear_TitleWord
    parts = []
    if first_author:
        parts.append(first_author)
    if year:
        parts.append(year)
    if title_word:
        parts.append(title_word)

    if not parts and fmt not in ("doi", "title"):
        doi = doi or metadata.get("DOI") or metadata.get("doi")
        return safe_filename(str(doi).strip().lower()) if doi and fmt != "author_year_title_doi" else ""

    doi = doi or metadata.get("DOI") or metadata.get("doi")
    doi_part = safe_filename(str(doi).strip().lower()) if doi else ""
    if fmt == "doi":
        return doi_part
    if fmt == "title":
        return _full_title(metadata) or doi_part
    if fmt == "zotero":
        # Zotero's own pattern keeps names in their script (李, Müller).
        raw = authors[0] if authors and isinstance(authors, list) else ""
        raw = (raw.get("family") or raw.get("name") or "") if isinstance(raw, dict) else str(raw).split(",")[0]
        author = " ".join(_ILLEGAL.sub(" ", raw).split())[:30] or first_author
        named = " - ".join(x for x in (author, year, _full_title(metadata)) if x)
        return named or doi_part
    if fmt == "year_author_title":
        name = "_".join(x for x in (year, first_author, title_word) if x)
        if len(name) > max_len:
            name = name[:max_len].rsplit("_", 1)[0]
        return name or doi_part

    # Join first two parts directly (AuthorYear), then underscore with title
    if len(parts) >= 3:
        name = f"{parts[0]}{parts[1]}_{parts[2]}"
    elif len(parts) == 2:
        name = f"{parts[0]}{parts[1]}"
    else:
        name = parts[0]
    # Truncate at a word boundary if too long
    if len(name) > max_len:
        name = name[:max_len].rsplit("_", 1)[0] if "_" in name[:max_len] else name[:max_len]
    if fmt == "author_year_title_doi" and doi_part:
        name = f"{name}_{doi_part}"
    return name


def _ascii(text: str) -> str:
    """Fold accents so names stay readable (Müller -> Muller)."""
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")


def _md5(path: Path) -> str:
    h = hashlib.md5()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def rename_pdf(
    file_path: Path,
    metadata: dict[str, Any],
    target_dir: Path | None = None,
    doi: str | None = None,
    fmt: str = DEFAULT_FORMAT,
) -> Path | None:
    """Rename a downloaded PDF based on metadata. Returns new path or None if failed."""
    if not file_path.exists() or fmt == "off":
        return None

    new_name = generate_filename(metadata, doi=doi, fmt=fmt)
    if not new_name:
        return None

    new_name = f"{new_name}.pdf"
    target = (target_dir or file_path.parent) / new_name

    # Never overwrite. Identical content (md5) is reused; anything else
    # (another copy of the same DOI, or a legacy name) gets a numeric suffix.
    if target.exists() and target != file_path:
        try:
            if _md5(target) == _md5(file_path):
                file_path.unlink(missing_ok=True)
                return target
        except OSError:
            pass
        stem, counter = target.stem, 2
        while target.exists() and target != file_path:
            target = target.parent / f"{stem}_{counter}.pdf"
            counter += 1

    try:
        if target != file_path:
            file_path.rename(target)
            log.info(f"Renamed: {file_path.name} -> {target.name}")
        return target
    except OSError as e:
        log.warning(f"Rename failed: {e}")
        return None
