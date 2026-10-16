"""British and American spellings, so a search finds both.

Papers write "aluminium" or "aluminum", "fibre" or "fiber", "characterised"
or "characterized". The spellings come from VarCon (Kevin Atkinson and
Benjamin Titze, part of SCOWL; see data/VARCON-LICENSE.txt), built into two
tables by scripts/build_spelling_table.py:

* ``to_us`` maps text to the preferred American spelling before words are
  compared (query matching, ranking features);
* ``variants`` gives the spellings of one word worth searching for, since
  OpenAlex matches "aluminum" and "aluminium" separately.

Abbreviations and synonyms (PDC / polymer-derived, EMW / microwave) need
domain knowledge and are left to the agent, which writes them as ``a|b``.
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


def to_us(text: str) -> str:
    """Lower-case ``text`` with British spellings turned American."""
    table = _tables()[0]
    return _WORD.sub(lambda m: table.get(m.group(), m.group()), text.lower())


def variants(word: str) -> list[str]:
    """The spellings of ``word`` worth searching for, the given one first."""
    to_us_table, search = _tables()
    low = word.lower()
    american = to_us_table.get(low, low)
    out = [word]
    for w in [american, *search.get(american, [])]:
        if w != low and w not in out:
            out.append(w)
    return out
