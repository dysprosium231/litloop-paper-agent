"""Text normalisation shared by query matching, ranking, the glossary and deduplication.

One set of rules, so that the same two wordings count as the same everywhere
(2026-10-25: there were three stop-word lists, two plural rules, two ways of
stemming, four sets of dashes and four paper keys):

* ``plain`` / ``squash`` / ``ascii_dashes``: case, every kind of dash, American spelling;
* ``singular`` / ``stem``: the plural of a word, and the crude prefix stem used
  for substring tests and word features;
* ``STOPWORDS``: words that carry no topic in a query; ``RANKING_STOPWORDS``,
  the shorter list the ranking model's word features drop;
* ``element_symbols``: the chemical elements (data/elements.tsv, from PubChem);
* ``paper_key`` / ``title_key``: when two records are the same paper.
"""

from __future__ import annotations

import re
from functools import lru_cache
from typing import Any

# Hyphen, non-breaking hyphen, figure dash, en dash, em dash, horizontal bar,
# minus sign, small and full-width hyphen-minus, and the ASCII hyphen-minus.
DASHES = "‐‑‒–—―−﹣－-"
_DASH = re.compile("[" + re.escape(DASHES) + "]")
_DASH_RUN = re.compile("[" + re.escape(DASHES) + "]+")
_SQUASH = re.compile(r"[\s/" + re.escape(DASHES) + "]+")

STOPWORDS = frozenset(
    "a an and the of in on for to with by from at as or via its their into using based under over between "
    "is are was were be this that these which we our than also "
    "study studies effect effects analysis review".split()
)

# The ranking model's word features (deep_search ``_tokens``) drop only these.
# Not STOPWORDS: on SYNERGY, dropping review/study/effect/analysis/using/based
# from the features made Leenaars_2019 and Appenzeller-Herzog_2019 worse, and
# adding just via/under/over/between changed the ranking too (DECISIONS,
# 2026-10-25). Changing this list changes the ranking: run
# scripts/eval_synergy.py before and after.
RANKING_STOPWORDS = frozenset(
    "the and of in for with by on to a an is are was were from as at this that these be or its "
    "their which we our into than also".split()
)


def ascii_dashes(text: str) -> str:
    """Every kind of dash as "-" (DOIs and titles copied from PDFs)."""
    return _DASH.sub("-", text or "")


def plain(text: str) -> str:
    """Lower case, American spelling, every kind of dash read as a space."""
    from .spelling import to_us
    return _DASH_RUN.sub(" ", to_us(text or ""))


def squash(text: str) -> str:
    """Lower case with spaces, dashes and slashes removed: "Polymer–derived" -> "polymerderived"."""
    return _SQUASH.sub("", (text or "").lower())


def singular(word: str) -> str:
    """Crude singular of a lower-case word: "ceramics" -> "ceramic", "properties" -> "property";
    "glass", "virus" and "analysis" stay."""
    if len(word) > 4 and word.endswith("ies"):
        return word[:-3] + "y"
    if len(word) > 3 and word.endswith("s") and not word.endswith(("ss", "us", "is")):
        return word[:-1]
    return word


def stem(word: str) -> str:
    """Crude stem, the first 7 letters: "infiltration" and "infiltrated" -> "infiltr"."""
    return word[:7]


@lru_cache(maxsize=1)
def elements() -> tuple[tuple[str, str], ...]:
    """(symbol, American name) of the 118 elements."""
    from importlib.resources import files
    rows = (files("litloop") / "data" / "elements.tsv").read_text(encoding="utf-8").splitlines()
    return tuple(tuple(line.split("\t")) for line in rows if line and not line.startswith("#"))  # type: ignore[misc]


@lru_cache(maxsize=1)
def element_symbols() -> frozenset[str]:
    return frozenset(s for s, _ in elements())


def is_chemical_system(word: str) -> bool:
    """"Al-Si-Mg", "Si-B-C-N": element symbols joined by dashes (papers list them in any order)."""
    parts = _DASH.split(word)
    return len(parts) > 1 and all(p in element_symbols() for p in parts)


def title_key(title: Any) -> str:
    """A title's letters and digits, lower case: the same title from two sources."""
    return re.sub(r"[^a-z0-9]+", "", str(title or "").lower())


def paper_key(rec: dict[str, Any]) -> str:
    """The key that says two records are one paper: the DOI, or else the title
    ("title:<letters and digits>"). The app's ``paperKey`` (app/src/lib/api.ts) mirrors it."""
    from ..identifiers import canonical_doi
    return canonical_doi(rec.get("doi")) or "title:" + title_key(rec.get("title"))
