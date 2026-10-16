"""Build LitLoop's British/American spelling table from VarCon.

VarCon (Kevin Atkinson, Benjamin Titze; part of SCOWL, wordlist.aspell.net)
lists the American, British, Canadian and Australian spellings of each word.
This script keeps the single-word clusters that have an American spelling and
writes two tab-separated tables, gzip-compressed, to src/litloop/data/:

    spelling_us.tsv.gz      any spelling -> preferred American spelling
    spelling_search.tsv.gz  American spelling -> British spellings worth searching

Usage:
    python scripts/build_spelling_table.py path/to/varcon.txt

varcon.txt: https://github.com/en-wl/wordlist/blob/master/varcon/varcon.txt
Its copyright and permission notice must ship next to the tables
(src/litloop/data/VARCON-LICENSE.txt).
"""

from __future__ import annotations

import gzip
import re
import sys
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "src" / "litloop" / "data"
WORD = re.compile(r"^[a-z]+$")
# Clusters where VarCon's general-English choice is wrong for scientific
# writing: "aerogel" is the usual spelling everywhere, VarCon lists "airgel".
SKIP = {"airgel", "airgels"}


def parse_line(line: str) -> list[tuple[str, str]]:
    """'A Cv: color / Av B C: colour | <N>' -> [('A Cv', 'color'), ('Av B C', 'colour')]."""
    body = line.split("|", 1)[0].strip()
    out = []
    for part in body.split(" / "):
        if ": " not in part:
            continue
        tags, word = part.split(": ", 1)
        out.append((tags.strip(), word.strip()))
    return out


def main(varcon: Path) -> None:
    clusters = []
    for line in varcon.read_text(encoding="latin-1").splitlines():  # VarCon ships as Latin-1
        if not line or line.startswith("#"):
            continue
        forms = parse_line(line)
        american = next((w for tags, w in forms if "A" in tags.split()), None)  # plain "A": preferred American
        if american and WORD.match(american) and american not in SKIP:
            clusters.append((american, forms))
    # A word that is the preferred American spelling anywhere is never rewritten:
    # "disc" and "disk" are both American in some sense, so neither maps to the other.
    americans = {a for a, _ in clusters}
    to_us: dict[str, str] = {}
    search: dict[str, list[str]] = {}
    for american, forms in clusters:
        for tags, word in forms:
            if not WORD.match(word) or word == american or word in americans:
                continue
            marks = tags.split()
            if any(m.endswith("x") for m in marks):  # improper variants
                continue
            to_us.setdefault(word, american)
            # Search only for the spellings a British text would actually use.
            if any(m in ("B", "Z") for m in marks):
                search.setdefault(american, [])
                if word not in search[american]:
                    search[american].append(word)
    OUT.mkdir(parents=True, exist_ok=True)
    with gzip.open(OUT / "spelling_us.tsv.gz", "wt", encoding="utf-8") as fh:
        for word in sorted(to_us):
            fh.write(f"{word}\t{to_us[word]}\n")
    with gzip.open(OUT / "spelling_search.tsv.gz", "wt", encoding="utf-8") as fh:
        for word in sorted(search):
            fh.write(word + "\t" + "\t".join(search[word]) + "\n")
    print(f"{len(to_us)} spellings -> American, {len(search)} American words with British forms")


if __name__ == "__main__":
    main(Path(sys.argv[1]))
