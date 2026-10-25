"""Build LitLoop's table of chemical element symbols and names from PubChem.

PubChem's periodic table (NCBI, https://pubchem.ncbi.nlm.nih.gov/periodic-table/)
gives each element's symbol and its American name ("Aluminum", "Cesium",
"Sulfur"); British spellings (aluminium, caesium, sulphur) come from the
spelling table, so they are not repeated here. Writes
src/litloop/data/elements.tsv: one "symbol<TAB>name" line per element.

Usage:
    python scripts/build_element_table.py path/to/elements.csv

elements.csv: https://pubchem.ncbi.nlm.nih.gov/rest/pug/periodictable/CSV
(download it with curl or a browser).
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "src" / "litloop" / "data" / "elements.tsv"


def main(src: str) -> None:
    with open(src, encoding="utf-8", newline="") as fh:
        rows = [(r["Symbol"].strip(), r["Name"].strip().lower()) for r in csv.DictReader(fh)]
    if len(rows) != 118 or len({s for s, _ in rows}) != 118:
        sys.exit(f"expected 118 distinct elements, got {len(rows)}")
    lines = ["# symbol\tname (American), from PubChem's periodic table; built by scripts/build_element_table.py"]
    lines += [f"{s}\t{n}" for s, n in rows]
    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    print(f"wrote {len(rows)} elements to {OUT}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    main(sys.argv[1])
