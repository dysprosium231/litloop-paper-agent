"""The glossary: abbreviations and synonyms a search expands by itself.

Papers write "PDC", "polymer-derived ceramic" or "preceramic polymer" for the
same thing. Three sources, used together (``in_use``):

* the user's entries: only someone who knows the field can say these;
* the chemical elements, built in (``elements``: "Al" = "aluminum", from
  PubChem, data/elements.tsv);
* British and American spellings, built in and applied word by word
  (``spelling``, from VarCon).

The user may switch off any built-in element or spelling (``set_builtin``).
Kept in ``<litloop home>/glossary.json`` through ``store`` (shared by the app
and the agent)::

    {"schema": 1,
     "entries": [{"id": "3f9a1c2e", "terms": ["PDC", "polymer-derived ceramic"],
                  "status": "active" | "suggested", "source": "user" | "agent" | "auto",
                  "papers": 4, "note": "", "added": "2026-10-25"}],
     "dismissed": [["pdc", "polycrystalline diamond compact"]],
     "disabled": ["element:La", "spelling:aluminum"]}

How it is used (decided with the user, 2026-10-25, after looking at how
others do it):

* Every entry in use applies to every search; there is no per-topic scope.
  A search is a few concepts joined by AND, so a wrong expansion (PDC as a
  drill-bit cutter searched as "polymer-derived ceramic ... wear") finds next
  to nothing rather than noise. A query that is the abbreviation alone has
  nothing to hold it, so it is not expanded (``query_variants``).
* Each search reports what it expanded, as PubMed's Search Details do.
* Most entries need not be typed: ``discover`` finds "long form (SF)" pairs in
  the abstracts a search has read, with the Schwartz-Hearst algorithm (the
  ``abbreviations`` package), and ``suggest_from`` adds those seen in two or
  more papers as suggestions the user confirms. Long forms are compared after
  ``normalize`` (case, dashes, plurals, British spelling), so "metal matrix
  composite" and "metal-matrix composites" are one meaning; a short form left
  with several meanings (CMC: carboxymethyl cellulose, ceramic matrix
  composite) gets one suggestion per meaning, and the user keeps the right one.
  A suggestion the user deletes is remembered (``dismissed``) and not made again.
* The agent may add entries too; they wait for the user's confirmation.
"""

from __future__ import annotations

import re
import time
import uuid
from collections import Counter
from functools import lru_cache
from pathlib import Path
from typing import Any

from . import store

MAX_VARIANTS = 3      # extra runs of one keyword query (own value)
MIN_PAPERS = 2        # a discovered pair is suggested once this many papers write it (own value)
_STOP = set("a an and as at by for from in into of on or the to with using via under over between".split())


def path() -> Path:
    from .config import DATA_DIR
    return Path(DATA_DIR) / "glossary.json"


def _data() -> dict[str, Any]:
    try:
        data = store.read_json(path())
    except (OSError, ValueError):
        return {"entries": [], "dismissed": [], "disabled": []}
    data.setdefault("entries", [])
    data.setdefault("dismissed", [])
    data.setdefault("disabled", [])
    return data


def entries(status: str | None = None) -> list[dict[str, Any]]:
    """All entries, or those with ``status`` ("active", "suggested")."""
    out = [e for e in _data()["entries"] if isinstance(e, dict) and e.get("terms")]
    return [e for e in out if status is None or e.get("status") == status]


def in_use() -> list[dict[str, Any]]:
    """What a search expands with: the user's entries in use, then the built-in
    elements the user has not switched off."""
    off = disabled()
    return entries("active") + [e for e in elements() if e["id"] not in off]


# --------------------------------------------------------------------------- built in

# Element symbols that are also everyday words (OpenAlex search ignores case): "La", "Ce"
# match French, "Er" German, "Eu" the EU, "In"/"As"/"Be"/"He"/"No" English.
AMBIGUOUS_SYMBOLS = {"as", "at", "be", "ce", "er", "es", "eu", "he", "in", "la", "no"}


@lru_cache(maxsize=1)
def elements() -> list[dict[str, Any]]:
    """The chemical elements as entries. A symbol is matched with its case. One that
    is a single letter (C, I, W) or an everyday word (In, La) neither triggers an
    expansion in a query nor is added to a search: the agent's "In" or "La" core
    term still gets the name. ``match``: terms that trigger an expansion in a
    query; ``add``: terms that may be searched for (both default to all terms)."""
    from importlib.resources import files
    text = (files("litloop") / "data" / "elements.tsv").read_text(encoding="utf-8")
    out = []
    for line in text.splitlines():
        if not line or line.startswith("#"):
            continue
        symbol, name = line.split("\t")
        loose = len(symbol) == 1 or symbol.lower() in AMBIGUOUS_SYMBOLS
        out.append({"id": f"element:{symbol}", "terms": [symbol, name], "status": "active",
                    "source": "builtin", "builtin": "element",
                    "match": [] if loose else [symbol], "add": [name] if loose else [symbol, name]})
    return out


@lru_cache(maxsize=1)
def _symbols() -> frozenset[str]:
    return frozenset(e["terms"][0] for e in elements())


def disabled() -> set[str]:
    """Built-in entries the user switched off: "element:<symbol>", "spelling:<American word>"."""
    return set(_data().get("disabled", []))


def set_builtin(key: str, enabled: bool) -> None:
    """Switch a built-in element or spelling on or off."""
    kind, _, word = key.partition(":")
    if kind == "element" and f"element:{word}" not in {e["id"] for e in elements()}:
        raise KeyError("无法找到这个元素")
    if kind == "spelling":
        from .spelling import spelling_pairs
        if word not in {us for us, _ in spelling_pairs(word, limit=1)}:
            raise KeyError("内置拼写表里没有这个词")
    elif kind != "element":
        raise KeyError("无法找到这一条")
    with _updating() as data:
        off = [k for k in data["disabled"] if k != key]
        data["disabled"] = off if enabled else off + [key]


def builtin(query: str = "", limit: int = 50) -> dict[str, Any]:
    """The built-in entries for the app: elements matching ``query`` (all when it is
    empty), spellings of the words in it, and what is switched off."""
    from .spelling import spelling_pairs, to_us
    off = disabled()
    q = query.strip().lower()
    qs = {q, to_us(q)}  # "aluminium" finds Al
    els = [{"id": e["id"], "terms": e["terms"], "disabled": e["id"] in off} for e in elements()
           if not q or any(s in t.lower() for s in qs for t in e["terms"])]
    els.sort(key=lambda e: not qs & {t.lower() for t in e["terms"]})  # "al": Al before cobalt
    spell = [{"id": f"spelling:{us}", "terms": [us, *others], "disabled": f"spelling:{us}" in off}
             for us, others in spelling_pairs(q, limit)] if q else []
    off_spell = [{"id": k, "terms": [k.split(":", 1)[1]], "disabled": True}
                 for k in sorted(off) if k.startswith("spelling:")]
    return {"elements": els, "spelling": spell, "spelling_disabled": off_spell}


# --------------------------------------------------------------------------- normalizing

_DASHES = re.compile(r"[‐-―−�-]+")


def normalize(term: str) -> str:
    """One spelling for comparing terms: lower case, American spelling, dashes and
    spaces alike, the last word singular ("Metal-matrix composites" ->
    "metal matrix composite")."""
    from .spelling import to_us
    words = to_us(_DASHES.sub(" ", term or "")).split()
    if words:
        last = words[-1]
        if len(last) > 4 and last.endswith("ies"):
            words[-1] = last[:-3] + "y"
        elif len(last) > 3 and last.endswith("s") and not last.endswith(("ss", "us", "is")):
            words[-1] = last[:-1]
    return " ".join(words)


def _clean(terms: list[str]) -> list[str]:
    out: list[str] = []
    for t in terms or []:
        t = " ".join(str(t).split())
        if t and normalize(t) not in {normalize(o) for o in out}:
            out.append(t)
    return out


# --------------------------------------------------------------------------- editing

class _updating:
    """``store.update`` of the glossary file, created when it is not there yet."""

    def __enter__(self) -> dict[str, Any]:
        p = path()
        if not p.exists():
            p.parent.mkdir(parents=True, exist_ok=True)
            store.write_json(p, {"entries": [], "dismissed": []}, indent=1)
        self._cm = store.update(p, indent=1)
        data = self._cm.__enter__()
        data.setdefault("entries", [])
        data.setdefault("dismissed", [])
        data.setdefault("disabled", [])
        return data

    def __exit__(self, *exc: Any) -> bool | None:
        return self._cm.__exit__(*exc)


def _new(terms: list[str], source: str, note: str = "", papers: int | None = None) -> dict[str, Any]:
    e = {"id": uuid.uuid4().hex[:8], "terms": terms, "status": "active" if source == "user" else "suggested",
         "source": source, "note": str(note or "").strip(), "added": time.strftime("%Y-%m-%d")}
    if papers:
        e["papers"] = papers
    return e


def add(terms: list[str], note: str = "", source: str = "user") -> dict[str, Any]:
    """A new entry; one the agent adds waits for the user's confirmation."""
    terms = _clean(terms)
    if len(terms) < 2:
        raise ValueError("无法添加：一条术语至少要有两种写法")
    with _updating() as data:
        same = {normalize(t) for t in terms}
        for e in data["entries"]:
            if {normalize(t) for t in e["terms"]} == same:
                raise ValueError("无法添加：术语表里已有这一条")
        entry = _new(terms, source, note)
        data["entries"].append(entry)
    return entry


def update(entry_id: str, *, terms: list[str] | None = None, note: str | None = None,
           confirm: bool = False) -> dict[str, Any]:
    """Change an entry; ``confirm`` turns a suggestion into an entry in use."""
    with _updating() as data:
        e = _find(data, entry_id)
        if terms is not None:
            terms = _clean(terms)
            if len(terms) < 2:
                raise ValueError("无法保存：一条术语至少要有两种写法")
            e["terms"] = terms
        if note is not None:
            e["note"] = str(note).strip()
        if confirm:
            e["status"] = "active"
        return dict(e)


def delete(entry_id: str) -> None:
    """Remove an entry; a deleted suggestion is not suggested again."""
    with _updating() as data:
        e = _find(data, entry_id)
        data["entries"].remove(e)
        if e.get("status") == "suggested" and len(e["terms"]) == 2:
            pair = [normalize(t) for t in e["terms"]]
            if pair not in data["dismissed"]:
                data["dismissed"].append(pair)


def _find(data: dict[str, Any], entry_id: str) -> dict[str, Any]:
    for e in data["entries"]:
        if e.get("id") == entry_id:
            return e
    raise KeyError("无法找到这条术语：它可能已被删除")


# --------------------------------------------------------------------------- expanding

def _abbreviation(term: str) -> bool:
    """"PDC", "SiC", "EMW" and element symbols: matched with case, so "Al" is not "al" in a sentence."""
    return " " not in term and (sum(ch.isupper() for ch in term) >= 2 or term in _symbols())


@lru_cache(maxsize=2048)
def _pattern(term: str) -> re.Pattern[str]:
    """``term`` as a whole word or phrase, written any way ``normalize`` treats as the
    same: a hyphen or a space ("polymer derived"), British spelling, the last word
    plural ("PDCs", "polymer-derived ceramics")."""
    from .spelling import variants
    if _abbreviation(term):
        return re.compile(rf"(?<![\w-]){re.escape(term)}s?(?![\w-])")
    words = [w for w in re.split(r"[-\s]+", term) if w]
    alts = [variants(w) for w in words]
    last = words[-1] if words else ""
    if len(last) > 2 and last[-1:].lower() == "y" and last[-2:-1].lower() not in "aeiou":
        alts[-1] += [last[:-1] + "ies"]
    body = r"[-\s]+".join("(?:" + "|".join(re.escape(a) for a in alt) + ")" for alt in alts)
    return re.compile(rf"(?<![\w-]){body}(?:s|es)?(?![\w-])", re.I)


def _has_other_concepts(query: str, span: tuple[int, int]) -> bool:
    """Something besides the matched term that keeps an expansion on topic."""
    rest = query[:span[0]] + " " + query[span[1]:]
    return any(len(w) >= 3 and w.lower() not in _STOP for w in re.findall(r"[A-Za-z0-9]+", rest))


def other_spelling(query: str) -> tuple[str, list[tuple[str, str]]]:
    """``query`` with each word in its other spelling (British words American,
    American words in their first British spelling), and the words changed;
    "" when no word has another spelling. Abbreviations (PDC, SiC) stay."""
    from .spelling import to_us, variants
    changed: list[tuple[str, str]] = []

    def swap(m: re.Match[str]) -> str:
        w = m.group(0)
        if not (w.islower() or w.istitle()):
            return w
        low = w.lower()
        us = to_us(low)
        other = us if us != low else next(iter(variants(low)[1:]), low)
        if other == low:
            return w
        changed.append((w, other))
        return other.capitalize() if w.istitle() else other
    out = re.sub(r"[A-Za-z]+", swap, query)
    return (out if changed else ""), changed


def query_variants(query: str, use: list[dict[str, Any]]) -> tuple[list[str], list[dict[str, Any]]]:
    """Other wordings of a keyword query (at most MAX_VARIANTS): first the query in
    its other spelling (OpenAlex matches "aluminum" and "aluminium" separately),
    then one term swapped for another of its entry; and what was expanded or left
    alone ("alone": the query is that term and nothing else)."""
    variants: list[str] = []
    used: list[dict[str, Any]] = []
    spelled, changed = other_spelling(query)
    if spelled:
        variants.append(spelled)
        used += [{"term": w, "with": [o], "query": query, "builtin": "spelling"} for w, o in changed]
    for e in use:
        for t in e.get("match", e["terms"]):
            m = _pattern(t).search(query)
            if not m:
                continue
            others = [o for o in e.get("add", e["terms"]) if o != t and not _pattern(o).search(query)]
            if not others:
                break
            report = {"id": e["id"], "term": m.group(0), "with": others, "query": query}
            if e.get("builtin"):
                report["builtin"] = e["builtin"]
            if not _has_other_concepts(query, m.span()):
                used.append({**report, "alone": True})
                break
            for o in others:
                v = query[:m.start()] + o + query[m.end():]
                if len(variants) < MAX_VARIANTS and v not in variants:
                    variants.append(v)
            used.append(report)
            break
    return variants, used


def alternatives(word: str, use: list[dict[str, Any]]) -> tuple[list[str], list[str]]:
    """The other terms of the entries ``word`` is one of (a word of a deep search's
    core term), and the built-in kinds among those entries ("element")."""
    out: list[str] = []
    kinds: list[str] = []
    for e in use:
        if any(_pattern(t).fullmatch(word) for t in e["terms"]):
            more = [t for t in e.get("add", e["terms"]) if not _pattern(t).fullmatch(word)]
            out += more
            if more and e.get("builtin"):
                kinds.append(e["builtin"])
    return list(dict.fromkeys(out)), list(dict.fromkeys(kinds))


# --------------------------------------------------------------------------- discovering

# Characterization methods nearly every materials abstract spells out: expanding
# them finds nothing new, and suggesting them only gives the user more to click.
_COMMON = set("SEM TEM HRTEM STEM XRD XPS FTIR FT-IR IR NMR TGA DTA DSC TG EDS EDX EDXS BET AFM SAED EIS "
              "UV UV-vis CV GCD EPR ESR XAS XANES EXAFS ICP ICP-OES ICP-MS".split())


def _usable(short: str, long: str) -> bool:
    words = long.split()
    return (2 <= len(short) <= 12 and any(ch.isalpha() for ch in short) and _short_key(short) not in _COMMON
            and 1 <= len(words) <= 8 and not re.search(r"[()\[\]]", long) and words[0].lower() not in _STOP)


def _short_key(short: str) -> str:
    """"PDCs" and "PDC" are one abbreviation."""
    if len(short) > 2 and short.endswith("s") and sum(ch.isupper() for ch in short[:-1]) >= 2:
        return short[:-1]
    return short


def _meaning_key(norm_long: str) -> str:
    """Wordings of one meaning ("scanning electron microscopy" / "microscope"): word stems."""
    return " ".join(w[:7] for w in norm_long.split())


def discover(texts: list[str]) -> dict[str, dict[str, dict[str, Any]]]:
    """"Long form (SF)" pairs in ``texts`` (abstracts): short form -> meaning ->
    {"text": its most common wording, "papers": how many texts write it}."""
    from abbreviations import schwartz_hearst
    seen: dict[str, dict[str, Counter]] = {}
    for text in texts:
        try:
            pairs = schwartz_hearst.extract_abbreviation_definition_pairs(doc_text=text or "")
        except Exception:  # an odd abstract must not stop the rest
            continue
        for short, long in pairs.items():
            long = _DASHES.sub("-", " ".join(long.split()))
            if _usable(short, long):
                key = _meaning_key(normalize(long))
                seen.setdefault(_short_key(short), {}).setdefault(key, Counter())[long.lower()] += 1
    return {s: {m: {"text": c.most_common(1)[0][0], "papers": sum(c.values())} for m, c in longs.items()}
            for s, longs in seen.items()}


def suggest_from(texts: list[str], min_papers: int = MIN_PAPERS) -> list[dict[str, Any]]:
    """Add the pairs ``texts`` write often enough as suggestions; returns the new ones."""
    found = discover(texts)
    if not found:
        return []
    new: list[dict[str, Any]] = []
    with _updating() as data:
        def key(a: str, b: str) -> tuple[str, str]:
            return normalize(_short_key(a)), _meaning_key(normalize(b))

        known = {key(a, b) for e in data["entries"] + elements() for a in e["terms"] for b in e["terms"] if a != b}
        dismissed = {key(*p) for p in data["dismissed"]}
        for short, longs in sorted(found.items()):
            several = sum(1 for i in longs.values() if i["papers"] >= min_papers) > 1
            for meaning, info in sorted(longs.items(), key=lambda kv: -kv[1]["papers"]):
                pair = key(short, info["text"])
                if info["papers"] < min_papers or pair in known or pair in dismissed:
                    continue
                note = (f"在 {info['papers']} 篇摘要中这样写" +
                        ("；这个缩写还有其他全称，留下对的那条" if several else ""))
                e = _new([short, info["text"]], "auto", note, papers=info["papers"])
                data["entries"].append(e)
                new.append(e)
    return new
