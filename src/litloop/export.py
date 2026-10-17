"""Export a screening session's papers: BibTeX, RIS or a DOI list, from the record alone (no network).

Which papers: "selected" (the user's final picks, the default) or "high" (rated 高,
the user's rating first). Files go to <output_dir>/exports/.
"""

from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any

from . import screening
from .config import load_config

FORMATS = {"bibtex": "bib", "ris": "ris", "doi": "txt"}


def papers(session: str, which: str = "selected") -> list[dict[str, Any]]:
    items = screening.load(session)["items"]
    if which == "high":
        return [it for it in items if screening._rating(it) == "高"]
    return [it for it in items if it.get("decision") == "selected"]


def _key(it: dict[str, Any], used: set[str]) -> str:
    first = (it.get("authors") or ["anon"])[0]
    surname = re.sub(r"[^A-Za-z]", "", first.split()[-1] if first.split() else "anon") or "anon"
    word = next((w for w in re.findall(r"[A-Za-z]{4,}", it.get("title") or "")), "paper")
    base = f"{surname}{it.get('year') or ''}{word}".lower()
    key, n = base, 1
    while key in used:
        n += 1
        key = f"{base}{n}"
    used.add(key)
    return key


def _bib_escape(s: str) -> str:
    return str(s).replace("{", "\\{").replace("}", "\\}")


def bibtex(items: list[dict[str, Any]]) -> str:
    used: set[str] = set()
    out = []
    for it in items:
        fields = {"title": it.get("title"), "author": " and ".join(it.get("authors") or []) or None,
                  "journal": it.get("journal"), "year": it.get("year"), "doi": it.get("doi")}
        body = ",\n".join(f"  {k} = {{{_bib_escape(v)}}}" for k, v in fields.items() if v)
        out.append(f"@article{{{_key(it, used)},\n{body}\n}}")
    return "\n\n".join(out) + "\n"


def ris(items: list[dict[str, Any]]) -> str:
    out = []
    for it in items:
        lines = ["TY  - JOUR", f"TI  - {it.get('title') or ''}"]
        lines += [f"AU  - {a}" for a in it.get("authors") or []]
        for tag, key in (("JO", "journal"), ("PY", "year"), ("DO", "doi")):
            if it.get(key):
                lines.append(f"{tag}  - {it[key]}")
        lines.append("ER  - ")
        out.append("\n".join(lines))
    return "\n\n".join(out) + "\n"


def doi_list(items: list[dict[str, Any]]) -> str:
    return "\n".join(it["doi"] for it in items if it.get("doi")) + "\n"


def export(session: str, fmt: str = "bibtex", which: str = "selected") -> dict[str, Any]:
    if fmt not in FORMATS:
        raise ValueError(f"format must be one of {sorted(FORMATS)}")
    items = papers(session, which)
    if not items:
        raise ValueError("no papers to export")
    text = {"bibtex": bibtex, "ris": ris, "doi": doi_list}[fmt](items)
    folder = Path(str(load_config().get("output_dir") or "")).expanduser() / "exports"
    folder.mkdir(parents=True, exist_ok=True)
    name = f"{session[:15]}-{which}-{time.strftime('%H%M%S')}.{FORMATS[fmt]}"
    path = folder / name
    path.write_text(text, encoding="utf-8", newline="\n")
    return {"file": str(path), "count": len(items)}
