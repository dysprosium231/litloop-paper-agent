"""British and American spellings, so a search finds both.

Papers write "aluminium" or "aluminum", "fibre" or "fiber", "characterised"
or "characterized". The spellings come from VarCon (Kevin Atkinson and
Benjamin Titze, part of SCOWL; see data/VARCON-LICENSE.txt), built into two
tables by scripts/build_spelling_table.py:

* ``to_us`` maps text to the preferred American spelling before words are
  compared (query matching, ranking features);
* ``variants`` gives the spellings of one word worth searching for, since
  OpenAlex matches "aluminum" and "aluminium" separately.

Abbreviations, synonyms and element symbols live in ``glossary``; the user
may switch a word's spellings off there ("spelling:<American word>").
"""

from __future__ import annotations

import gzip
import re
from functools import lru_cache
from importlib.resources import files

_WORD = re.compile(r"[a-z]+")


@lru_cache(maxsize=1)
def _tables() -> tuple[dict[str, str], dict[str, list[str]]]:
    data = files("litloop") / "data"
    with gzip.open(data.joinpath("spelling_us.tsv.gz").open("rb"), "rt", encoding="utf-8") as fh:
        to_us_table = dict(line.rstrip("\n").split("\t", 1) for line in fh if "\t" in line)
    search: dict[str, list[str]] = {}
    with gzip.open(data.joinpath("spelling_search.tsv.gz").open("rb"), "rt", encoding="utf-8") as fh:
        for line in fh:
            word, *others = line.rstrip("\n").split("\t")
            search[word] = others
    return to_us_table, search


_OFF: list = [None, frozenset()]  # (glossary file, its mtime), American words switched off


def _off() -> frozenset[str]:
    """American words whose other spellings the user switched off (the glossary's
    ``disabled``), read again when the file changes."""
    from . import glossary
    p = glossary.path()
    try:
        stamp = (str(p), p.stat().st_mtime_ns)
    except OSError:
        return frozenset()
    if _OFF[0] != stamp:
        _OFF[:] = [stamp, frozenset(k.split(":", 1)[1] for k in glossary.disabled() if k.startswith("spelling:"))]
    return _OFF[1]


def to_us(text: str) -> str:
    """Lower-case ``text`` with British spellings turned American."""
    table = _tables()[0]
    off = _off()

    def us(m: re.Match[str]) -> str:
        w = table.get(m.group(), m.group())
        return m.group() if w in off else w
    return _WORD.sub(us, text.lower())


def variants(word: str) -> list[str]:
    """The spellings of ``word`` worth searching for, the given one first."""
    to_us_table, search = _tables()
    low = word.lower()
    american = to_us_table.get(low, low)
    if american in _off():
        return [word]
    out = [word]
    for w in [american, *search.get(american, [])]:
        if w != low and w not in out:
            out.append(w)
    return out


def spelling_pairs(query: str, limit: int = 50) -> list[tuple[str, list[str]]]:
    """(American spelling, its British spellings) for words starting with ``query``
    in any of their spellings, for looking them up in the app."""
    q = query.strip().lower()
    found = [(us, others) for us, others in _tables()[1].items()
             if others and (us.startswith(q) or any(o.startswith(q) for o in others))]
    found.sort(key=lambda p: (q != p[0] and q not in p[1], len(p[0]), p[0]))  # the word itself, then shortest
    return found[:limit]
